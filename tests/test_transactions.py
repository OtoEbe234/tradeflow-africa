"""Tests for transaction endpoints — create, get, list, cancel."""

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.trader import TraderStatus
from app.models.transaction import Transaction, TransactionDirection, TransactionStatus
from app.services import auth_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rate_redis(mock_redis):
    """Extend mock_redis with sorted set methods needed by RateService."""
    mock_redis.zadd = AsyncMock()
    mock_redis.zremrangebyscore = AsyncMock()
    mock_redis.zrange = AsyncMock(return_value=[])
    return mock_redis


@pytest.fixture
def trader_with_pin(make_trader):
    """Active trader with PIN set and default tier-1 limits."""
    trader = make_trader(status=TraderStatus.ACTIVE)
    trader.set_pin("1234")
    return trader


@pytest.fixture
def auth_headers(trader_with_pin):
    """JWT Authorization header for the test trader."""
    token = auth_service.create_access_token(
        str(trader_with_pin.id), trader_with_pin.phone,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def valid_create_payload():
    """Valid NGN→CNY transaction creation payload."""
    return {
        "source_currency": "NGN",
        "target_currency": "CNY",
        "source_amount": 1000000,
        "supplier_name": "Shenzhen Electronics Co.",
        "supplier_bank": "Bank of China",
        "supplier_account": "621082100123456789",
        "pin": "1234",
    }


def _setup_trader_lookup(mock_db, trader):
    """Configure mock_db.execute to return *trader* for every call."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    mock_db.execute = AsyncMock(return_value=mock_result)


# ---------------------------------------------------------------------------
# POST /api/v1/transactions/ — Create transaction
# ---------------------------------------------------------------------------


class TestCreateTransaction:

    @pytest.mark.asyncio
    async def test_create_success_ngn_to_cny(
        self, client, mock_db, rate_redis, trader_with_pin,
        auth_headers, valid_create_payload,
    ):
        """Standard NGN→CNY creation returns 201 with deposit instructions."""
        _setup_trader_lookup(mock_db, trader_with_pin)

        resp = await client.post(
            "/api/v1/transactions/", json=valid_create_payload, headers=auth_headers,
        )
        assert resp.status_code == 201

        data = resp.json()
        assert data["direction"] == "ngn_to_cny"
        assert data["source_currency"] == "NGN"
        assert data["target_currency"] == "CNY"
        assert data["status"] == "initiated"
        assert data["reference"].startswith("TXN-")
        assert Decimal(data["source_amount"]) == Decimal("1000000")
        assert Decimal(data["target_amount"]) > 0
        assert Decimal(data["exchange_rate"]) > 0
        assert Decimal(data["fee_amount"]) > 0

        # Deposit instructions present
        instr = data["deposit_instructions"]
        assert instr is not None
        assert instr["bank_name"] == "Providus Bank"
        assert instr["currency"] == "NGN"
        assert instr["reference"].startswith("TXN-")
        assert instr["expires_at"] is not None
        # Total deposit = source + fee
        assert float(instr["amount"]) > float(data["source_amount"])

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called()

    @pytest.mark.asyncio
    async def test_create_cny_to_ngn(
        self, client, mock_db, rate_redis, trader_with_pin, auth_headers,
    ):
        """CNY→NGN direction succeeds."""
        _setup_trader_lookup(mock_db, trader_with_pin)

        payload = {
            "source_currency": "CNY",
            "target_currency": "NGN",
            "source_amount": 10000,
            "supplier_name": "Nigerian Imports Ltd",
            "supplier_bank": "First Bank",
            "supplier_account": "1234567890",
            "pin": "1234",
        }

        resp = await client.post(
            "/api/v1/transactions/", json=payload, headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["direction"] == "cny_to_ngn"
        assert data["source_currency"] == "CNY"
        assert data["target_currency"] == "NGN"

    # --- Fee calculation -------------------------------------------------

    @pytest.mark.asyncio
    async def test_standard_fee_calculation(
        self, client, mock_db, rate_redis, trader_with_pin,
        auth_headers, valid_create_payload,
    ):
        """Standard tier: 1,000,000 NGN × 2 % = 20,000 NGN fee."""
        _setup_trader_lookup(mock_db, trader_with_pin)

        resp = await client.post(
            "/api/v1/transactions/", json=valid_create_payload, headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert Decimal(data["fee_percentage"]) == Decimal("2.00")
        assert Decimal(data["fee_amount"]) == Decimal("20000.00")

    @pytest.mark.asyncio
    async def test_minimum_fee_applied(
        self, client, mock_db, rate_redis, trader_with_pin, auth_headers,
    ):
        """100,000 NGN × 2 % = 2,000 < min 5,000 → fee is 5,000."""
        _setup_trader_lookup(mock_db, trader_with_pin)

        payload = {
            "source_currency": "NGN",
            "target_currency": "CNY",
            "source_amount": 100000,
            "supplier_name": "Test",
            "supplier_bank": "Test Bank",
            "supplier_account": "1234567890",
            "pin": "1234",
        }

        resp = await client.post(
            "/api/v1/transactions/", json=payload, headers=auth_headers,
        )
        assert resp.status_code == 201
        assert Decimal(resp.json()["fee_amount"]) == Decimal("5000")

    # --- Quote locking ---------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_with_valid_quote(
        self, client, mock_db, rate_redis, trader_with_pin,
        auth_headers, valid_create_payload,
    ):
        """Using a cached quote applies the quoted rate/fee values."""
        _setup_trader_lookup(mock_db, trader_with_pin)

        quote_data = json.dumps({
            "mid_market_rate": "213.7931",
            "target_amount": "4677.42",
            "fee_percentage": "2.00",
            "fee_amount": "20000.00",
        })

        async def _get_by_key(key):
            if key == "quote:QT-TEST123":
                return quote_data
            return None

        rate_redis.get = _get_by_key

        valid_create_payload["quote_id"] = "QT-TEST123"
        resp = await client.post(
            "/api/v1/transactions/", json=valid_create_payload, headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert Decimal(data["exchange_rate"]) == Decimal("213.7931")
        assert Decimal(data["target_amount"]) == Decimal("4677.42")

    @pytest.mark.asyncio
    async def test_create_expired_quote_400(
        self, client, mock_db, rate_redis, trader_with_pin,
        auth_headers, valid_create_payload,
    ):
        """Expired / invalid quote_id returns 400."""
        _setup_trader_lookup(mock_db, trader_with_pin)

        valid_create_payload["quote_id"] = "QT-EXPIRED999"
        resp = await client.post(
            "/api/v1/transactions/", json=valid_create_payload, headers=auth_headers,
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"].lower()
        assert "expired" in detail or "invalid" in detail

    # --- PIN verification ------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_wrong_pin_401(
        self, client, mock_db, rate_redis, trader_with_pin,
        auth_headers, valid_create_payload,
    ):
        """Wrong PIN returns 401."""
        _setup_trader_lookup(mock_db, trader_with_pin)

        valid_create_payload["pin"] = "9999"
        resp = await client.post(
            "/api/v1/transactions/", json=valid_create_payload, headers=auth_headers,
        )
        assert resp.status_code == 401
        assert "Invalid PIN" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_no_pin_set_400(
        self, client, mock_db, rate_redis, make_trader,
    ):
        """Trader without PIN set gets 400."""
        trader = make_trader(status=TraderStatus.ACTIVE)
        # pin_hash is None
        _setup_trader_lookup(mock_db, trader)

        token = auth_service.create_access_token(str(trader.id), trader.phone)
        headers = {"Authorization": f"Bearer {token}"}

        payload = {
            "source_currency": "NGN",
            "target_currency": "CNY",
            "source_amount": 1000000,
            "supplier_name": "Test",
            "supplier_bank": "Test Bank",
            "supplier_account": "1234567890",
            "pin": "1234",
        }

        resp = await client.post(
            "/api/v1/transactions/", json=payload, headers=headers,
        )
        assert resp.status_code == 400
        assert "PIN has not been set" in resp.json()["detail"]

    # --- Validation errors -----------------------------------------------

    @pytest.mark.asyncio
    async def test_create_below_minimum_ngn_400(
        self, client, mock_db, rate_redis, trader_with_pin, auth_headers,
    ):
        """NGN amount below 10,000 returns 400."""
        _setup_trader_lookup(mock_db, trader_with_pin)

        payload = {
            "source_currency": "NGN",
            "target_currency": "CNY",
            "source_amount": 5000,
            "supplier_name": "Test",
            "supplier_bank": "Test Bank",
            "supplier_account": "1234567890",
            "pin": "1234",
        }

        resp = await client.post(
            "/api/v1/transactions/", json=payload, headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "Minimum" in resp.json()["detail"]
        assert "NGN" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_below_minimum_cny_400(
        self, client, mock_db, rate_redis, trader_with_pin, auth_headers,
    ):
        """CNY amount below 100 returns 400."""
        _setup_trader_lookup(mock_db, trader_with_pin)

        payload = {
            "source_currency": "CNY",
            "target_currency": "NGN",
            "source_amount": 50,
            "supplier_name": "Test",
            "supplier_bank": "Test Bank",
            "supplier_account": "1234567890",
            "pin": "1234",
        }

        resp = await client.post(
            "/api/v1/transactions/", json=payload, headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "Minimum" in resp.json()["detail"]
        assert "CNY" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_unsupported_pair_400(
        self, client, mock_db, rate_redis, trader_with_pin, auth_headers,
    ):
        """Same-currency pair (NGN/NGN) returns 400."""
        _setup_trader_lookup(mock_db, trader_with_pin)

        payload = {
            "source_currency": "NGN",
            "target_currency": "NGN",
            "source_amount": 1000000,
            "supplier_name": "Test",
            "supplier_bank": "Test Bank",
            "supplier_account": "1234567890",
            "pin": "1234",
        }

        resp = await client.post(
            "/api/v1/transactions/", json=payload, headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "supported" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_create_invalid_supplier_account_short_422(
        self, client, mock_db, rate_redis, trader_with_pin, auth_headers,
    ):
        """Supplier account with < 10 digits fails schema validation (422)."""
        _setup_trader_lookup(mock_db, trader_with_pin)

        payload = {
            "source_currency": "NGN",
            "target_currency": "CNY",
            "source_amount": 1000000,
            "supplier_name": "Test",
            "supplier_bank": "Test Bank",
            "supplier_account": "12345",
            "pin": "1234",
        }

        resp = await client.post(
            "/api/v1/transactions/", json=payload, headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_invalid_supplier_account_alpha_422(
        self, client, mock_db, rate_redis, trader_with_pin, auth_headers,
    ):
        """Supplier account with letters fails schema validation (422)."""
        _setup_trader_lookup(mock_db, trader_with_pin)

        payload = {
            "source_currency": "NGN",
            "target_currency": "CNY",
            "source_amount": 1000000,
            "supplier_name": "Test",
            "supplier_bank": "Test Bank",
            "supplier_account": "ABCDEFGHIJ",
            "pin": "1234",
        }

        resp = await client.post(
            "/api/v1/transactions/", json=payload, headers=auth_headers,
        )
        assert resp.status_code == 422

    # --- Monthly limit ---------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_exceeds_monthly_limit_400(
        self, client, mock_db, rate_redis, trader_with_pin, auth_headers,
    ):
        """Transaction that would exceed monthly limit returns 400."""
        _setup_trader_lookup(mock_db, trader_with_pin)

        # Tier 1 limit = $5,000. Nearly maxed out already.
        trader_with_pin.monthly_used = Decimal("4999")
        trader_with_pin.monthly_limit = Decimal("5000")

        payload = {
            "source_currency": "NGN",
            "target_currency": "CNY",
            "source_amount": 1000000,  # ≈ $645 at 1550 NGN/USD
            "supplier_name": "Test",
            "supplier_bank": "Test Bank",
            "supplier_account": "1234567890",
            "pin": "1234",
        }

        resp = await client.post(
            "/api/v1/transactions/", json=payload, headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "monthly limit" in resp.json()["detail"].lower()

    # --- Auth ------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_unauthenticated_422(
        self, client, mock_db, rate_redis, valid_create_payload,
    ):
        """Missing Authorization header returns 422."""
        resp = await client.post(
            "/api/v1/transactions/", json=valid_create_payload,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_updates_monthly_usage(
        self, client, mock_db, rate_redis, trader_with_pin,
        auth_headers, valid_create_payload,
    ):
        """After creation, trader.monthly_used is increased."""
        _setup_trader_lookup(mock_db, trader_with_pin)
        assert trader_with_pin.monthly_used == Decimal("0")

        resp = await client.post(
            "/api/v1/transactions/", json=valid_create_payload, headers=auth_headers,
        )
        assert resp.status_code == 201
        assert trader_with_pin.monthly_used > Decimal("0")


# ---------------------------------------------------------------------------
# GET /api/v1/transactions/{id} — Get transaction
# ---------------------------------------------------------------------------


class TestGetTransaction:

    def _make_txn(self, trader_id, **overrides):
        """Helper to build a Transaction ORM object."""
        defaults = dict(
            trader_id=trader_id,
            direction=TransactionDirection.NGN_TO_CNY,
            source_amount=Decimal("1000000"),
            target_amount=Decimal("4677.42"),
            exchange_rate=Decimal("213.7931"),
            fee_amount=Decimal("20000"),
            fee_percentage=Decimal("2.00"),
            supplier_name="Test Supplier",
            supplier_bank="Test Bank",
            status=TransactionStatus.INITIATED,
        )
        defaults.update(overrides)
        return Transaction(**defaults)

    @pytest.mark.asyncio
    async def test_get_own_transaction_200(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """Owner can retrieve their transaction."""
        txn = self._make_txn(trader_with_pin.id)

        trader_result = MagicMock()
        trader_result.scalar_one_or_none = MagicMock(return_value=trader_with_pin)
        txn_result = MagicMock()
        txn_result.scalar_one_or_none = MagicMock(return_value=txn)

        mock_db.execute = AsyncMock(side_effect=[trader_result, txn_result])

        resp = await client.get(
            f"/api/v1/transactions/{txn.id}", headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(txn.id)
        assert data["source_currency"] == "NGN"
        assert data["target_currency"] == "CNY"
        assert data["status"] == "initiated"

    @pytest.mark.asyncio
    async def test_get_not_found_404(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """Non-existent transaction returns 404."""
        trader_result = MagicMock()
        trader_result.scalar_one_or_none = MagicMock(return_value=trader_with_pin)
        txn_result = MagicMock()
        txn_result.scalar_one_or_none = MagicMock(return_value=None)

        mock_db.execute = AsyncMock(side_effect=[trader_result, txn_result])

        fake_id = str(uuid.uuid4())
        resp = await client.get(
            f"/api/v1/transactions/{fake_id}", headers=auth_headers,
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_other_traders_transaction_403(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """Accessing another trader's transaction returns 403."""
        other_id = uuid.uuid4()
        txn = self._make_txn(other_id)

        trader_result = MagicMock()
        trader_result.scalar_one_or_none = MagicMock(return_value=trader_with_pin)
        txn_result = MagicMock()
        txn_result.scalar_one_or_none = MagicMock(return_value=txn)

        mock_db.execute = AsyncMock(side_effect=[trader_result, txn_result])

        resp = await client.get(
            f"/api/v1/transactions/{txn.id}", headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_get_unauthenticated_422(self, client, mock_db):
        """Missing auth returns 422."""
        fake_id = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/transactions/{fake_id}")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/transactions/ — List transactions
# ---------------------------------------------------------------------------


class TestListTransactions:

    def _make_txns(self, trader_id, count=3):
        """Create a list of Transaction instances for listing tests."""
        txns = []
        for i in range(count):
            txns.append(
                Transaction(
                    trader_id=trader_id,
                    direction=TransactionDirection.NGN_TO_CNY,
                    source_amount=Decimal(str((i + 1) * 1000000)),
                    target_amount=Decimal(str((i + 1) * 4677)),
                    exchange_rate=Decimal("213.7931"),
                    fee_amount=Decimal("20000"),
                    fee_percentage=Decimal("2.00"),
                    status=TransactionStatus.INITIATED,
                )
            )
        return txns

    def _setup_list_mocks(self, mock_db, trader, total, items):
        """Wire up the three db.execute calls for list endpoint."""
        trader_result = MagicMock()
        trader_result.scalar_one_or_none = MagicMock(return_value=trader)

        count_result = MagicMock()
        count_result.scalar_one = MagicMock(return_value=total)

        items_result = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all = MagicMock(return_value=items)
        items_result.scalars = MagicMock(return_value=scalars_mock)

        mock_db.execute = AsyncMock(
            side_effect=[trader_result, count_result, items_result],
        )

    @pytest.mark.asyncio
    async def test_list_paginated_200(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """Returns paginated transaction list."""
        txns = self._make_txns(trader_with_pin.id, count=3)
        self._setup_list_mocks(mock_db, trader_with_pin, total=3, items=txns)

        resp = await client.get("/api/v1/transactions/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["page"] == 1
        assert data["per_page"] == 20
        assert len(data["items"]) == 3

    @pytest.mark.asyncio
    async def test_list_empty_200(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """No transactions returns empty list."""
        self._setup_list_mocks(mock_db, trader_with_pin, total=0, items=[])

        resp = await client.get("/api/v1/transactions/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert len(data["items"]) == 0

    @pytest.mark.asyncio
    async def test_list_with_status_filter(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """Status filter param is accepted and returns 200."""
        self._setup_list_mocks(mock_db, trader_with_pin, total=0, items=[])

        resp = await client.get(
            "/api/v1/transactions/",
            params={"status": "initiated"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_with_date_filters(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """Date range filters are accepted."""
        self._setup_list_mocks(mock_db, trader_with_pin, total=0, items=[])

        resp = await client.get(
            "/api/v1/transactions/",
            params={
                "date_from": "2025-01-01T00:00:00",
                "date_to": "2025-12-31T23:59:59",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_custom_pagination(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """Custom page / per_page values are reflected in response."""
        self._setup_list_mocks(mock_db, trader_with_pin, total=50, items=[])

        resp = await client.get(
            "/api/v1/transactions/",
            params={"page": 3, "per_page": 10},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 3
        assert data["per_page"] == 10
        assert data["total"] == 50

    @pytest.mark.asyncio
    async def test_list_unauthenticated_422(self, client, mock_db):
        """Missing auth returns 422."""
        resp = await client.get("/api/v1/transactions/")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/transactions/{id}/cancel — Cancel transaction
# ---------------------------------------------------------------------------


class TestCancelTransaction:

    def _make_txn(self, trader_id, status=TransactionStatus.INITIATED):
        """Helper to create a Transaction with given status."""
        txn = Transaction(
            trader_id=trader_id,
            direction=TransactionDirection.NGN_TO_CNY,
            source_amount=Decimal("1000000"),
            target_amount=Decimal("4677.42"),
            exchange_rate=Decimal("213.7931"),
            fee_amount=Decimal("20000"),
            fee_percentage=Decimal("2.00"),
            supplier_name="Test Supplier",
            supplier_bank="Test Bank",
            status=TransactionStatus.INITIATED,
        )
        # Walk to desired status if not INITIATED
        if status == TransactionStatus.FUNDED:
            txn.transition_to(TransactionStatus.FUNDED)
        elif status == TransactionStatus.COMPLETED:
            txn.transition_to(TransactionStatus.FUNDED)
            txn.transition_to(TransactionStatus.MATCHING)
            txn.transition_to(TransactionStatus.MATCHED)
            txn.transition_to(TransactionStatus.PENDING_SETTLEMENT)
            txn.transition_to(TransactionStatus.SETTLING)
            txn.transition_to(TransactionStatus.COMPLETED)
        return txn

    def _setup_cancel_mocks(self, mock_db, trader, txn):
        """Wire trader + transaction lookup for cancel endpoint."""
        trader_result = MagicMock()
        trader_result.scalar_one_or_none = MagicMock(return_value=trader)
        txn_result = MagicMock()
        txn_result.scalar_one_or_none = MagicMock(return_value=txn)
        mock_db.execute = AsyncMock(side_effect=[trader_result, txn_result])

    @pytest.mark.asyncio
    async def test_cancel_initiated_200(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """INITIATED transaction can be cancelled."""
        txn = self._make_txn(trader_with_pin.id)
        self._setup_cancel_mocks(mock_db, trader_with_pin, txn)

        resp = await client.post(
            f"/api/v1/transactions/{txn.id}/cancel",
            json={"pin": "1234"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_funded_409(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """FUNDED transaction cannot be cancelled via this endpoint (409)."""
        txn = self._make_txn(trader_with_pin.id, status=TransactionStatus.FUNDED)
        self._setup_cancel_mocks(mock_db, trader_with_pin, txn)

        resp = await client.post(
            f"/api/v1/transactions/{txn.id}/cancel",
            json={"pin": "1234"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "Cannot cancel" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_cancel_completed_409(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """COMPLETED transaction cannot be cancelled (409)."""
        txn = self._make_txn(trader_with_pin.id, status=TransactionStatus.COMPLETED)
        self._setup_cancel_mocks(mock_db, trader_with_pin, txn)

        resp = await client.post(
            f"/api/v1/transactions/{txn.id}/cancel",
            json={"pin": "1234"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "Cannot cancel" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_cancel_wrong_pin_401(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """Wrong PIN returns 401."""
        txn = self._make_txn(trader_with_pin.id)
        self._setup_cancel_mocks(mock_db, trader_with_pin, txn)

        resp = await client.post(
            f"/api/v1/transactions/{txn.id}/cancel",
            json={"pin": "9999"},
            headers=auth_headers,
        )
        assert resp.status_code == 401
        assert "Invalid PIN" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_cancel_not_found_404(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """Cancel non-existent transaction returns 404."""
        trader_result = MagicMock()
        trader_result.scalar_one_or_none = MagicMock(return_value=trader_with_pin)
        txn_result = MagicMock()
        txn_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(side_effect=[trader_result, txn_result])

        fake_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/transactions/{fake_id}/cancel",
            json={"pin": "1234"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_other_traders_txn_403(
        self, client, mock_db, trader_with_pin, auth_headers,
    ):
        """Cannot cancel another trader's transaction (403)."""
        other_id = uuid.uuid4()
        txn = self._make_txn(other_id)
        self._setup_cancel_mocks(mock_db, trader_with_pin, txn)

        resp = await client.post(
            f"/api/v1/transactions/{txn.id}/cancel",
            json={"pin": "1234"},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_cancel_unauthenticated_422(self, client, mock_db):
        """Missing auth returns 422."""
        fake_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/transactions/{fake_id}/cancel",
            json={"pin": "1234"},
        )
        assert resp.status_code == 422
