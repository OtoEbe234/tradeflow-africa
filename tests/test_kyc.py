"""Tests for BVN verification and PIN setup endpoints."""

import uuid
from unittest.mock import MagicMock

import pytest

from app.models.trader import TraderStatus
from app.services import auth_service
from app.services.kyc_service import (
    BVNResult,
    MockBVNProvider,
    set_bvn_provider,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _use_mock_bvn_provider():
    """Ensure all tests in this module use the mock BVN provider."""
    set_bvn_provider(MockBVNProvider())
    yield
    set_bvn_provider(MockBVNProvider())


# ---------------------------------------------------------------------------
# POST /verify-bvn — BVN verification + account creation
# ---------------------------------------------------------------------------


class TestVerifyBVN:
    """Tests for BVN verification endpoint."""

    @pytest.mark.asyncio
    async def test_success(self, client, mock_db):
        """Valid BVN with matching phone creates trader, returns 201."""
        response = await client.post(
            "/api/v1/auth/verify-bvn",
            json={"phone": "+2348012345678", "bvn": "12345678901"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["success"] is True
        assert data["name"] == "Adebayo Ogunlesi"
        assert data["tradeflow_id"].startswith("TF-")
        assert data["trader_id"] is not None

        # Trader should have been added to the DB session
        mock_db.add.assert_called_once()
        mock_db.flush.assert_called()

    @pytest.mark.asyncio
    async def test_invalid_bvn_format_short(self, client):
        """BVN shorter than 11 digits returns 422."""
        response = await client.post(
            "/api/v1/auth/verify-bvn",
            json={"phone": "+2348012345678", "bvn": "1234567890"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_bvn_format_letters(self, client):
        """BVN containing letters returns 422."""
        response = await client.post(
            "/api/v1/auth/verify-bvn",
            json={"phone": "+2348012345678", "bvn": "1234567890A"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_bvn_not_found(self, client, mock_db):
        """BVN not in the provider's database returns 400."""
        response = await client.post(
            "/api/v1/auth/verify-bvn",
            json={"phone": "+2348012345678", "bvn": "00000000000"},
        )
        assert response.status_code == 400
        assert "verification failed" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_phone_mismatch(self, client, mock_db):
        """BVN exists but phone doesn't match returns 400."""
        # BVN "99999999999" maps to phone "+2340000000000" — never matches
        response = await client.post(
            "/api/v1/auth/verify-bvn",
            json={"phone": "+2348012345678", "bvn": "99999999999"},
        )
        assert response.status_code == 400
        assert "does not match" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_duplicate_phone(self, client, mock_db, make_trader):
        """Phone already registered returns 409."""
        existing = make_trader(phone="+2348012345678")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=existing)
        mock_db.execute.return_value = mock_result

        response = await client.post(
            "/api/v1/auth/verify-bvn",
            json={"phone": "+2348012345678", "bvn": "12345678901"},
        )
        assert response.status_code == 409
        assert "already registered" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_invalid_phone_format(self, client):
        """Non-Nigerian phone format returns 422."""
        response = await client.post(
            "/api/v1/auth/verify-bvn",
            json={"phone": "+1234567890", "bvn": "12345678901"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_second_mock_bvn(self, client, mock_db):
        """Second test BVN also works correctly."""
        response = await client.post(
            "/api/v1/auth/verify-bvn",
            json={"phone": "+2348098765432", "bvn": "12345678902"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Chioma Nwosu"


# ---------------------------------------------------------------------------
# POST /set-pin — PIN setup + account activation
# ---------------------------------------------------------------------------


class TestSetPin:
    """Tests for PIN setup endpoint."""

    @pytest.mark.asyncio
    async def test_success(self, client, mock_db, make_trader):
        """Valid PIN activates account and returns tokens."""
        trader = make_trader(status=TraderStatus.PENDING)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=trader)
        mock_db.execute.return_value = mock_result

        response = await client.post(
            "/api/v1/auth/set-pin",
            json={"trader_id": str(trader.id), "pin": "5739"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0

        # Account should now be ACTIVE
        assert trader.status == TraderStatus.ACTIVE
        # PIN should be hashed
        assert trader.pin_hash is not None
        assert trader.verify_pin("5739")

    @pytest.mark.asyncio
    async def test_sequential_pin_rejected(self, client, mock_db, make_trader):
        """Sequential PIN (1234) is rejected as weak."""
        trader = make_trader(status=TraderStatus.PENDING)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=trader)
        mock_db.execute.return_value = mock_result

        response = await client.post(
            "/api/v1/auth/set-pin",
            json={"trader_id": str(trader.id), "pin": "1234"},
        )
        assert response.status_code == 400
        assert "too weak" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_reverse_sequential_pin_rejected(self, client, mock_db, make_trader):
        """Reverse-sequential PIN (4321) is also rejected."""
        trader = make_trader(status=TraderStatus.PENDING)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=trader)
        mock_db.execute.return_value = mock_result

        response = await client.post(
            "/api/v1/auth/set-pin",
            json={"trader_id": str(trader.id), "pin": "4321"},
        )
        assert response.status_code == 400
        assert "too weak" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_repeated_pin_rejected(self, client, mock_db, make_trader):
        """Repeated digits PIN (1111) is rejected as weak."""
        trader = make_trader(status=TraderStatus.PENDING)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=trader)
        mock_db.execute.return_value = mock_result

        response = await client.post(
            "/api/v1/auth/set-pin",
            json={"trader_id": str(trader.id), "pin": "1111"},
        )
        assert response.status_code == 400
        assert "too weak" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_trader_not_found(self, client, mock_db):
        """Unknown trader_id returns 404."""
        response = await client.post(
            "/api/v1/auth/set-pin",
            json={"trader_id": str(uuid.uuid4()), "pin": "5739"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_already_active_rejected(self, client, mock_db, make_trader):
        """Cannot set PIN on an already-active account."""
        trader = make_trader(status=TraderStatus.ACTIVE)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=trader)
        mock_db.execute.return_value = mock_result

        response = await client.post(
            "/api/v1/auth/set-pin",
            json={"trader_id": str(trader.id), "pin": "5739"},
        )
        assert response.status_code == 400
        assert "already activated" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_pin_too_short(self, client):
        """PIN shorter than 4 digits returns 422."""
        response = await client.post(
            "/api/v1/auth/set-pin",
            json={"trader_id": str(uuid.uuid4()), "pin": "12"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_pin_with_letters(self, client):
        """PIN with non-numeric characters returns 422."""
        response = await client.post(
            "/api/v1/auth/set-pin",
            json={"trader_id": str(uuid.uuid4()), "pin": "12ab"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_pin_hashed_with_bcrypt(self, client, mock_db, make_trader):
        """PIN is stored as bcrypt hash, not plaintext."""
        trader = make_trader(status=TraderStatus.PENDING)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=trader)
        mock_db.execute.return_value = mock_result

        await client.post(
            "/api/v1/auth/set-pin",
            json={"trader_id": str(trader.id), "pin": "8642"},
        )

        # Hash should NOT be the plaintext
        assert trader.pin_hash != "8642"
        # But verification should work
        assert trader.verify_pin("8642") is True
        assert trader.verify_pin("0000") is False


# ---------------------------------------------------------------------------
# KYC service unit tests (MockBVNProvider)
# ---------------------------------------------------------------------------


class TestMockBVNProvider:
    """Unit tests for the MockBVNProvider directly."""

    @pytest.mark.asyncio
    async def test_known_bvn_returns_verified(self):
        """Known BVN returns verified=True with name and DOB."""
        provider = MockBVNProvider()
        result = await provider.verify_bvn("12345678901", "+2348012345678")
        assert result.verified is True
        assert result.full_name == "Adebayo Ogunlesi"
        assert result.date_of_birth == "1985-03-15"
        assert result.phone_match is True

    @pytest.mark.asyncio
    async def test_known_bvn_wrong_phone(self):
        """Known BVN with wrong phone returns phone_match=False."""
        provider = MockBVNProvider()
        result = await provider.verify_bvn("12345678901", "+2349999999999")
        assert result.verified is True
        assert result.phone_match is False

    @pytest.mark.asyncio
    async def test_unknown_bvn(self):
        """Unknown BVN returns verified=False."""
        provider = MockBVNProvider()
        result = await provider.verify_bvn("00000000000", "+2348012345678")
        assert result.verified is False
        assert result.full_name == ""

    @pytest.mark.asyncio
    async def test_mismatch_bvn(self):
        """Test BVN 99999999999 always has mismatched phone."""
        provider = MockBVNProvider()
        result = await provider.verify_bvn("99999999999", "+2348012345678")
        assert result.verified is True
        assert result.phone_match is False

    @pytest.mark.asyncio
    async def test_all_mock_bvns_resolve(self):
        """All entries in the mock DB return verified=True."""
        from app.services.kyc_service import _MOCK_BVN_DB
        provider = MockBVNProvider()
        for bvn, record in _MOCK_BVN_DB.items():
            result = await provider.verify_bvn(bvn, record["phone_number"])
            assert result.verified is True
            assert result.full_name == record["full_name"]
            assert result.phone_match is True


# ---------------------------------------------------------------------------
# PIN validation unit tests
# ---------------------------------------------------------------------------


class TestPinValidation:
    """Unit tests for the _is_weak_pin helper."""

    def test_sequential_pins(self):
        from app.api.auth import _is_weak_pin
        assert _is_weak_pin("1234") is True
        assert _is_weak_pin("4321") is True
        assert _is_weak_pin("0123") is True
        assert _is_weak_pin("6789") is True
        assert _is_weak_pin("9876") is True

    def test_repeated_pins(self):
        from app.api.auth import _is_weak_pin
        assert _is_weak_pin("1111") is True
        assert _is_weak_pin("0000") is True
        assert _is_weak_pin("9999") is True

    def test_strong_pins(self):
        from app.api.auth import _is_weak_pin
        assert _is_weak_pin("5739") is False
        assert _is_weak_pin("8642") is False
        assert _is_weak_pin("2580") is False
        assert _is_weak_pin("3917") is False
        assert _is_weak_pin("7205") is False
