"""
Comprehensive tests for WhatsApp conversation flows.

Covers helpers, registration, payment, status, and menu flows
with mocked database sessions, Redis, message senders, and services.
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.trader import Trader, TraderStatus
from app.models.transaction import Transaction, TransactionStatus
from app.services.kyc_service import BVNResult


# ── Fixtures ─────────────────────────────────────────────────────────────


PHONE = "+2348012345678"


def _make_trader(**overrides) -> Trader:
    defaults = {
        "phone": PHONE,
        "full_name": "Adebayo Ogunlesi",
        "status": TraderStatus.ACTIVE,
    }
    defaults.update(overrides)
    trader = Trader(**defaults)
    trader.set_pin("5791")
    return trader


def _make_transaction(**overrides) -> Transaction:
    defaults = {
        "trader_id": uuid.uuid4(),
        "direction": "ngn_to_cny",
        "source_amount": Decimal("5000000"),
        "target_amount": Decimal("23364.49"),
        "exchange_rate": Decimal("213.9310"),
        "fee_amount": Decimal("100000"),
        "fee_percentage": Decimal("2.00"),
        "supplier_name": "Guangzhou Supplies",
        "supplier_bank": "Bank of China",
        "status": TransactionStatus.INITIATED,
    }
    defaults.update(overrides)
    return Transaction(**defaults)


def _mock_session_with_trader(trader):
    """Create a mock async_session that returns the given trader on execute."""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=trader)
    session.execute = AsyncMock(return_value=mock_result)
    session.add = MagicMock()
    session.commit = AsyncMock()

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=cm), session


def _mock_session_no_trader():
    """Create a mock async_session that returns None on execute."""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=mock_result)
    session.add = MagicMock()
    session.commit = AsyncMock()

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=cm), session


def _mock_session_with_txn_list(txns):
    """Session mock where first execute returns a trader, second returns txns."""
    session = AsyncMock()
    trader = _make_trader()

    trader_result = MagicMock()
    trader_result.scalar_one_or_none = MagicMock(return_value=trader)

    txn_scalars = MagicMock()
    txn_scalars.all = MagicMock(return_value=txns)
    txn_result = MagicMock()
    txn_result.scalars = MagicMock(return_value=txn_scalars)

    session.execute = AsyncMock(side_effect=[trader_result, txn_result])
    session.add = MagicMock()
    session.commit = AsyncMock()

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=cm), session


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock()
    r.setex = AsyncMock()
    r.delete = AsyncMock()
    return r


# ═════════════════════════════════════════════════════════════════════════
# HELPERS TESTS
# ═════════════════════════════════════════════════════════════════════════


class TestHelpers:

    def test_is_weak_pin_sequential(self):
        from app.whatsapp.flows.helpers import is_weak_pin
        assert is_weak_pin("1234") is True
        assert is_weak_pin("4321") is True
        assert is_weak_pin("0123") is True

    def test_is_weak_pin_all_same(self):
        from app.whatsapp.flows.helpers import is_weak_pin
        assert is_weak_pin("0000") is True
        assert is_weak_pin("1111") is True
        assert is_weak_pin("9999") is True

    def test_is_weak_pin_strong(self):
        from app.whatsapp.flows.helpers import is_weak_pin
        assert is_weak_pin("5791") is False
        assert is_weak_pin("8023") is False

    def test_validate_bvn_format(self):
        from app.whatsapp.flows.helpers import validate_bvn_format
        assert validate_bvn_format("12345678901") is True
        assert validate_bvn_format("1234567890") is False  # 10 digits
        assert validate_bvn_format("123456789012") is False  # 12 digits
        assert validate_bvn_format("abcdefghijk") is False

    def test_validate_account_number(self):
        from app.whatsapp.flows.helpers import validate_account_number
        assert validate_account_number("1234567890") is True  # 10 digits
        assert validate_account_number("12345678901234567890") is True  # 20 digits
        assert validate_account_number("123456789") is False  # 9 digits
        assert validate_account_number("abc") is False

    def test_validate_pin_format(self):
        from app.whatsapp.flows.helpers import validate_pin_format
        assert validate_pin_format("1234") is True
        assert validate_pin_format("0000") is True
        assert validate_pin_format("123") is False
        assert validate_pin_format("12345") is False
        assert validate_pin_format("abcd") is False

    def test_format_direction(self):
        from app.whatsapp.flows.helpers import format_direction
        assert "NGN" in format_direction("ngn_to_cny")
        assert "CNY" in format_direction("cny_to_ngn")
        assert format_direction("unknown") == "unknown"

    def test_format_status(self):
        from app.whatsapp.flows.helpers import format_status
        assert "Initiated" in format_status("initiated")
        assert "Completed" in format_status("completed")
        assert format_status("unknown_status") == "unknown_status"


# ═════════════════════════════════════════════════════════════════════════
# REGISTRATION FLOW TESTS
# ═════════════════════════════════════════════════════════════════════════


class TestRegistrationFlow:

    @pytest.mark.asyncio
    async def test_start_new_user(self):
        """New user sees welcome and language selection."""
        mock_sess, _ = _mock_session_no_trader()
        with (
            patch("app.whatsapp.flows.registration.get_trader_by_phone", new_callable=AsyncMock, return_value=None),
            patch("app.whatsapp.flows.registration.send_welcome", new_callable=AsyncMock) as mock_welcome,
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock) as mock_text,
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "", {"step": "start", "data": {}})

        mock_welcome.assert_called_once()
        assert result["step"] == "language"
        assert "language" in mock_text.call_args[0][1].lower() or "English" in mock_text.call_args[0][1]

    @pytest.mark.asyncio
    async def test_start_existing_user(self):
        """Existing user is told they already have an account."""
        trader = _make_trader()
        with (
            patch("app.whatsapp.flows.registration.get_trader_by_phone", new_callable=AsyncMock, return_value=trader),
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock) as mock_text,
            patch("app.whatsapp.flows.registration.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "", {"step": "start", "data": {}})

        assert result["flow"] == "menu"
        assert "already" in mock_text.call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_language_english(self):
        """Selecting '1' sets English language."""
        with (
            patch("app.whatsapp.flows.registration.set_user_lang", new_callable=AsyncMock) as mock_lang,
            patch("app.whatsapp.flows.registration.send_button", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "1", {"step": "language", "data": {}})

        mock_lang.assert_called_once_with(PHONE, "en")
        assert result["step"] == "phone_confirm"
        assert result["data"]["lang"] == "en"

    @pytest.mark.asyncio
    async def test_language_pidgin(self):
        """Selecting '2' sets Pidgin."""
        with (
            patch("app.whatsapp.flows.registration.set_user_lang", new_callable=AsyncMock) as mock_lang,
            patch("app.whatsapp.flows.registration.send_button", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "2", {"step": "language", "data": {}})

        mock_lang.assert_called_once_with(PHONE, "pcm")
        assert result["data"]["lang"] == "pcm"

    @pytest.mark.asyncio
    async def test_language_invalid(self):
        """Invalid language choice stays on language step."""
        with (
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "3", {"step": "language", "data": {}})

        assert result["step"] == "language"

    @pytest.mark.asyncio
    async def test_phone_confirm_yes(self):
        """Confirming phone moves to BVN input."""
        with (
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "yes", {"step": "phone_confirm", "data": {"lang": "en"}})

        assert result["step"] == "bvn_input"

    @pytest.mark.asyncio
    async def test_phone_confirm_no(self):
        """Declining phone cancels registration."""
        with (
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.flows.registration.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "no", {"step": "phone_confirm", "data": {"lang": "en"}})

        assert result["flow"] == "menu"

    @pytest.mark.asyncio
    async def test_phone_confirm_button_yes(self):
        """confirm_yes button moves to BVN input."""
        with (
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_interactive
            result = await handle_interactive(PHONE, "confirm_yes", {"step": "phone_confirm", "data": {"lang": "en"}})

        assert result["step"] == "bvn_input"

    @pytest.mark.asyncio
    async def test_phone_confirm_button_no(self):
        """confirm_no button cancels registration."""
        with (
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.flows.registration.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_interactive
            result = await handle_interactive(PHONE, "confirm_no", {"step": "phone_confirm", "data": {"lang": "en"}})

        assert result["flow"] == "menu"

    @pytest.mark.asyncio
    async def test_bvn_invalid_format(self):
        """Invalid BVN format stays on bvn_input."""
        with (
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "123", {"step": "bvn_input", "data": {"lang": "en"}})

        assert result["step"] == "bvn_input"

    @pytest.mark.asyncio
    async def test_bvn_verify_fail(self):
        """BVN verification failure stays on bvn_input."""
        failed_result = BVNResult(verified=False, full_name="", date_of_birth="", phone_number="", phone_match=False)
        mock_provider = AsyncMock()
        mock_provider.verify_bvn = AsyncMock(return_value=failed_result)
        with (
            patch("app.whatsapp.flows.registration.get_bvn_provider", return_value=mock_provider),
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "00000000000", {"step": "bvn_input", "data": {"lang": "en"}})

        assert result["step"] == "bvn_input"

    @pytest.mark.asyncio
    async def test_bvn_valid(self):
        """Valid BVN moves to pin_set."""
        ok_result = BVNResult(
            verified=True, full_name="Adebayo Ogunlesi",
            date_of_birth="1985-03-15", phone_number=PHONE, phone_match=True,
        )
        mock_provider = AsyncMock()
        mock_provider.verify_bvn = AsyncMock(return_value=ok_result)
        with (
            patch("app.whatsapp.flows.registration.get_bvn_provider", return_value=mock_provider),
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "12345678901", {"step": "bvn_input", "data": {"lang": "en"}})

        assert result["step"] == "pin_set"
        assert result["data"]["full_name"] == "Adebayo Ogunlesi"

    @pytest.mark.asyncio
    async def test_pin_weak_rejected(self):
        """Weak PINs are rejected."""
        with (
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock) as mock_text,
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "1234", {"step": "pin_set", "data": {"lang": "en"}})

        assert result["step"] == "pin_set"
        assert "easy" in mock_text.call_args[0][1].lower() or "weak" in mock_text.call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_pin_valid_moves_to_confirm(self):
        """Valid PIN moves to pin_confirm."""
        with (
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "5791", {"step": "pin_set", "data": {"lang": "en"}})

        assert result["step"] == "pin_confirm"
        assert result["data"]["pin"] == "5791"

    @pytest.mark.asyncio
    async def test_pin_mismatch(self):
        """PIN mismatch goes back to pin_set."""
        with (
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "9999", {"step": "pin_confirm", "data": {"pin": "5791", "lang": "en"}})

        assert result["step"] == "pin_set"

    @pytest.mark.asyncio
    async def test_pin_confirm_success(self):
        """Matching PIN creates trader and goes to menu."""
        mock_sess, session = _mock_session_no_trader()
        with (
            patch("app.whatsapp.flows.registration.async_session", mock_sess),
            patch("app.whatsapp.flows.registration.send_text", new_callable=AsyncMock) as mock_text,
            patch("app.whatsapp.flows.registration.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.registration import handle_text
            result = await handle_text(PHONE, "5791", {
                "step": "pin_confirm",
                "data": {"pin": "5791", "full_name": "Adebayo Ogunlesi", "bvn": "12345678901", "lang": "en"},
            })

        assert result["flow"] == "menu"
        session.add.assert_called_once()
        session.commit.assert_called_once()
        assert "successfully" in mock_text.call_args[0][1].lower()


# ═════════════════════════════════════════════════════════════════════════
# PAYMENT FLOW TESTS
# ═════════════════════════════════════════════════════════════════════════


class TestPaymentFlow:

    @pytest.mark.asyncio
    async def test_start_unregistered(self):
        """Unregistered user is rejected from payment."""
        with (
            patch("app.whatsapp.flows.payment.get_trader_by_phone", new_callable=AsyncMock, return_value=None),
            patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.flows.payment.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "", {"step": "start", "data": {}})

        assert result["flow"] == "menu"

    @pytest.mark.asyncio
    async def test_start_registered_no_direction(self):
        """Registered user without preset direction gets direction prompt."""
        trader = _make_trader()
        with (
            patch("app.whatsapp.flows.payment.get_trader_by_phone", new_callable=AsyncMock, return_value=trader),
            patch("app.whatsapp.flows.payment.send_button", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "", {"step": "start", "data": {}})

        assert result["step"] == "direction"

    @pytest.mark.asyncio
    async def test_start_with_preset_direction(self):
        """Preset direction skips to amount_input."""
        trader = _make_trader()
        with (
            patch("app.whatsapp.flows.payment.get_trader_by_phone", new_callable=AsyncMock, return_value=trader),
            patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "", {"step": "start", "data": {"direction": "ngn_to_cny"}})

        assert result["step"] == "amount_input"

    @pytest.mark.asyncio
    async def test_direction_text(self):
        """Direction '1' sets ngn_to_cny."""
        with (
            patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "1", {"step": "direction", "data": {}})

        assert result["data"]["direction"] == "ngn_to_cny"
        assert result["step"] == "amount_input"

    @pytest.mark.asyncio
    async def test_direction_button(self):
        """dir_ngn_cny button sets ngn_to_cny."""
        with (
            patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_interactive
            result = await handle_interactive(PHONE, "dir_ngn_cny", {"step": "direction", "data": {}})

        assert result["data"]["direction"] == "ngn_to_cny"

    @pytest.mark.asyncio
    async def test_amount_invalid(self):
        """Invalid amount stays on amount_input."""
        with (
            patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "xyz", {"step": "amount_input", "data": {"direction": "ngn_to_cny"}})

        assert result["step"] == "amount_input"

    @pytest.mark.asyncio
    async def test_amount_below_minimum(self):
        """Amount below minimum stays on amount_input."""
        with (
            patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock) as mock_text,
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "5000", {"step": "amount_input", "data": {"direction": "ngn_to_cny"}})

        assert result["step"] == "amount_input"
        assert "Minimum" in mock_text.call_args[0][1] or "minimum" in mock_text.call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_amount_valid_generates_quote(self):
        """Valid amount generates quote and moves to rate_display."""
        mock_quote = {
            "quote_id": "QT-TEST123",
            "source_currency": "NGN",
            "target_currency": "CNY",
            "source_amount": "5000000",
            "target_amount": "23364.49",
            "mid_market_rate": "213.9310",
            "fee_amount": "100000",
            "fee_percentage": "2.00",
            "total_cost": "5100000",
        }
        mock_svc = AsyncMock()
        mock_svc.generate_quote = AsyncMock(return_value=mock_quote)
        with (
            patch("app.whatsapp.flows.payment.RateService", return_value=mock_svc),
            patch("app.whatsapp.flows.payment.get_user_lang", new_callable=AsyncMock, return_value="en"),
            patch("app.whatsapp.flows.payment.send_rate_quote", new_callable=AsyncMock),
            patch("app.whatsapp.flows.payment.send_button", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "5m", {"step": "amount_input", "data": {"direction": "ngn_to_cny"}})

        assert result["step"] == "rate_display"
        assert result["data"]["quote"] == mock_quote

    @pytest.mark.asyncio
    async def test_circuit_breaker(self):
        """Circuit breaker error cancels to menu."""
        from app.services.rate_service import CircuitBreakerOpenError
        mock_svc = AsyncMock()
        mock_svc.generate_quote = AsyncMock(side_effect=CircuitBreakerOpenError("paused"))
        with (
            patch("app.whatsapp.flows.payment.RateService", return_value=mock_svc),
            patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.flows.payment.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "5m", {"step": "amount_input", "data": {"direction": "ngn_to_cny"}})

        assert result["flow"] == "menu"

    @pytest.mark.asyncio
    async def test_rate_accept(self):
        """Accepting rate moves to supplier_name."""
        with (
            patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "proceed", {"step": "rate_display", "data": {"quote": {}}})

        assert result["step"] == "supplier_name"

    @pytest.mark.asyncio
    async def test_rate_decline(self):
        """Declining rate cancels to menu."""
        with (
            patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.flows.payment.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "cancel", {"step": "rate_display", "data": {"quote": {}}})

        assert result["flow"] == "menu"

    @pytest.mark.asyncio
    async def test_supplier_fields(self):
        """Supplier name → bank → account flows correctly."""
        with patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock):
            from app.whatsapp.flows.payment import handle_text

            # Name
            r1 = await handle_text(PHONE, "Guangzhou Supplies", {"step": "supplier_name", "data": {"quote": {}}})
            assert r1["step"] == "supplier_bank"
            assert r1["data"]["supplier_name"] == "Guangzhou Supplies"

            # Bank
            r2 = await handle_text(PHONE, "Bank of China", {"step": "supplier_bank", "data": r1["data"]})
            assert r2["step"] == "supplier_account"
            assert r2["data"]["supplier_bank"] == "Bank of China"

    @pytest.mark.asyncio
    async def test_account_validation(self):
        """Invalid account number stays on supplier_account."""
        with patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "abc", {"step": "supplier_account", "data": {"quote": {}}})

        assert result["step"] == "supplier_account"

    @pytest.mark.asyncio
    async def test_invoice_skip(self):
        """Typing 'skip' moves to summary."""
        with (
            patch("app.whatsapp.flows.payment.get_user_lang", new_callable=AsyncMock, return_value="en"),
            patch("app.whatsapp.flows.payment.send_payment_summary", new_callable=AsyncMock),
            patch("app.whatsapp.flows.payment.send_button", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_text
            data = {
                "direction": "ngn_to_cny",
                "supplier_name": "Test",
                "supplier_bank": "BoC",
                "supplier_account": "1234567890",
                "quote": {
                    "source_amount": "5000000", "source_currency": "NGN",
                    "target_currency": "CNY", "mid_market_rate": "213.93",
                },
            }
            result = await handle_text(PHONE, "skip", {"step": "invoice_upload", "data": data})

        assert result["step"] == "summary_confirm"

    @pytest.mark.asyncio
    async def test_invoice_upload_with_media(self):
        """Media upload moves to summary."""
        with (
            patch("app.whatsapp.flows.payment.get_user_lang", new_callable=AsyncMock, return_value="en"),
            patch("app.whatsapp.flows.payment.send_payment_summary", new_callable=AsyncMock),
            patch("app.whatsapp.flows.payment.send_button", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_text
            data = {
                "direction": "ngn_to_cny",
                "supplier_name": "Test",
                "supplier_bank": "BoC",
                "supplier_account": "1234567890",
                "quote": {
                    "source_amount": "5000000", "source_currency": "NGN",
                    "target_currency": "CNY", "mid_market_rate": "213.93",
                },
                "last_media": {"type": "image", "media_id": "img-123"},
            }
            result = await handle_text(PHONE, "uploaded", {"step": "invoice_upload", "data": data})

        assert result["step"] == "summary_confirm"
        assert "invoice_media" in result["data"]

    @pytest.mark.asyncio
    async def test_summary_confirm(self):
        """Confirming summary moves to PIN entry."""
        with patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "confirm", {"step": "summary_confirm", "data": {"quote": {}}})

        assert result["step"] == "pin_entry"

    @pytest.mark.asyncio
    async def test_summary_cancel(self):
        """Cancelling summary returns to menu."""
        with (
            patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.flows.payment.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "cancel", {"step": "summary_confirm", "data": {"quote": {}}})

        assert result["flow"] == "menu"

    @pytest.mark.asyncio
    async def test_pin_valid_creates_transaction(self):
        """Correct PIN creates transaction and shows deposit instructions."""
        trader = _make_trader()
        mock_sess, session = _mock_session_no_trader()

        quote = {
            "source_amount": "5000000", "target_amount": "23364.49",
            "mid_market_rate": "213.9310", "fee_amount": "100000",
            "fee_percentage": "2.00", "total_cost": "5100000",
            "source_currency": "NGN", "target_currency": "CNY",
        }

        with (
            patch("app.whatsapp.flows.payment.get_trader_by_phone", new_callable=AsyncMock, return_value=trader),
            patch("app.whatsapp.flows.payment.async_session", mock_sess),
            patch("app.whatsapp.flows.payment.get_user_lang", new_callable=AsyncMock, return_value="en"),
            patch("app.whatsapp.flows.payment.send_deposit_instructions", new_callable=AsyncMock) as mock_deposit,
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "5791", {
                "step": "pin_entry",
                "data": {"direction": "ngn_to_cny", "quote": quote, "supplier_name": "Test", "supplier_bank": "BoC", "supplier_account": "1234567890"},
            })

        assert result["flow"] == "menu"
        session.add.assert_called_once()
        session.commit.assert_called_once()
        mock_deposit.assert_called_once()

    @pytest.mark.asyncio
    async def test_pin_invalid_increments_attempts(self):
        """Wrong PIN increments attempt counter."""
        trader = _make_trader()
        with (
            patch("app.whatsapp.flows.payment.get_trader_by_phone", new_callable=AsyncMock, return_value=trader),
            patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock) as mock_text,
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "0000", {
                "step": "pin_entry",
                "data": {"quote": {}, "pin_attempts": 0},
            })

        assert result["step"] == "pin_entry"
        assert result["data"]["pin_attempts"] == 1
        assert "remaining" in mock_text.call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_pin_max_attempts(self):
        """3 failed PIN attempts cancels flow."""
        trader = _make_trader()
        with (
            patch("app.whatsapp.flows.payment.get_trader_by_phone", new_callable=AsyncMock, return_value=trader),
            patch("app.whatsapp.flows.payment.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.flows.payment.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.payment import handle_text
            result = await handle_text(PHONE, "0000", {
                "step": "pin_entry",
                "data": {"quote": {}, "pin_attempts": 2},
            })

        assert result["flow"] == "menu"


# ═════════════════════════════════════════════════════════════════════════
# STATUS FLOW TESTS
# ═════════════════════════════════════════════════════════════════════════


class TestStatusFlow:

    @pytest.mark.asyncio
    async def test_start_unregistered(self):
        """Unregistered user is told to register."""
        with (
            patch("app.whatsapp.flows.status.get_trader_by_phone", new_callable=AsyncMock, return_value=None),
            patch("app.whatsapp.flows.status.send_text", new_callable=AsyncMock),
            patch("app.whatsapp.flows.status.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.status import handle_text
            result = await handle_text(PHONE, "", {"step": "start", "data": {}})

        assert result["flow"] == "menu"

    @pytest.mark.asyncio
    async def test_no_transactions(self):
        """Registered user with no txns is told."""
        trader = _make_trader()
        with (
            patch("app.whatsapp.flows.status.get_trader_by_phone", new_callable=AsyncMock, return_value=trader),
            patch("app.whatsapp.flows.status.get_trader_transactions", new_callable=AsyncMock, return_value=[]),
            patch("app.whatsapp.flows.status.send_text", new_callable=AsyncMock) as mock_text,
            patch("app.whatsapp.flows.status.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.status import handle_text
            result = await handle_text(PHONE, "", {"step": "start", "data": {}})

        assert result["flow"] == "menu"
        assert "no transactions" in mock_text.call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_shows_transaction_list(self):
        """Shows numbered list of recent transactions."""
        txn = _make_transaction()
        with (
            patch("app.whatsapp.flows.status.get_trader_by_phone", new_callable=AsyncMock, return_value=_make_trader()),
            patch("app.whatsapp.flows.status.get_trader_transactions", new_callable=AsyncMock, return_value=[txn]),
            patch("app.whatsapp.flows.status.send_text", new_callable=AsyncMock) as mock_text,
        ):
            from app.whatsapp.flows.status import handle_text
            result = await handle_text(PHONE, "", {"step": "start", "data": {}})

        assert result["step"] == "select_transaction"
        assert len(result["data"]["refs"]) == 1
        assert txn.reference in mock_text.call_args[0][1]

    @pytest.mark.asyncio
    async def test_select_by_number(self):
        """Selecting by number shows transaction detail."""
        txn = _make_transaction()
        mock_sess, _ = _mock_session_with_trader(None)  # won't be used directly

        # Mock for _show_transaction's async_session
        show_session = AsyncMock()
        show_result = MagicMock()
        show_result.scalar_one_or_none = MagicMock(return_value=txn)
        show_session.execute = AsyncMock(return_value=show_result)
        show_cm = AsyncMock()
        show_cm.__aenter__ = AsyncMock(return_value=show_session)
        show_cm.__aexit__ = AsyncMock(return_value=None)
        mock_show_sess = MagicMock(return_value=show_cm)

        with (
            patch("app.whatsapp.flows.status.async_session", mock_show_sess),
            patch("app.whatsapp.flows.status.get_user_lang", new_callable=AsyncMock, return_value="en"),
            patch("app.whatsapp.flows.status.send_status_update", new_callable=AsyncMock) as mock_status,
            patch("app.whatsapp.flows.status.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.status import handle_text
            result = await handle_text(PHONE, "1", {
                "step": "select_transaction",
                "data": {"refs": [txn.reference]},
            })

        assert result["flow"] == "menu"
        mock_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_select_by_reference(self):
        """Selecting by TXN reference shows transaction detail."""
        txn = _make_transaction()

        show_session = AsyncMock()
        show_result = MagicMock()
        show_result.scalar_one_or_none = MagicMock(return_value=txn)
        show_session.execute = AsyncMock(return_value=show_result)
        show_cm = AsyncMock()
        show_cm.__aenter__ = AsyncMock(return_value=show_session)
        show_cm.__aexit__ = AsyncMock(return_value=None)
        mock_show_sess = MagicMock(return_value=show_cm)

        with (
            patch("app.whatsapp.flows.status.async_session", mock_show_sess),
            patch("app.whatsapp.flows.status.get_user_lang", new_callable=AsyncMock, return_value="en"),
            patch("app.whatsapp.flows.status.send_status_update", new_callable=AsyncMock) as mock_status,
            patch("app.whatsapp.flows.status.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.status import handle_text
            result = await handle_text(PHONE, txn.reference, {
                "step": "select_transaction",
                "data": {"refs": [txn.reference]},
            })

        assert result["flow"] == "menu"
        mock_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_selection(self):
        """Invalid selection stays on select_transaction."""
        with (
            patch("app.whatsapp.flows.status.send_text", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.status import handle_text
            result = await handle_text(PHONE, "abc", {
                "step": "select_transaction",
                "data": {"refs": ["TXN-TEST1234"]},
            })

        assert result["step"] == "select_transaction"


# ═════════════════════════════════════════════════════════════════════════
# MENU FLOW TESTS
# ═════════════════════════════════════════════════════════════════════════


class TestMenuFlow:

    @pytest.mark.asyncio
    async def test_hi_shows_menu(self):
        """Greeting shows the menu."""
        with (
            patch("app.whatsapp.flows.menu.send_menu", new_callable=AsyncMock) as mock_menu,
        ):
            from app.whatsapp.flows.menu import handle_text
            result = await handle_text(PHONE, "hi", {"step": "start", "data": {}})

        mock_menu.assert_called_once()
        assert result["step"] == "awaiting_selection"

    @pytest.mark.asyncio
    async def test_pay_registered(self):
        """Registered user is routed to payment flow."""
        trader = _make_trader()
        with (
            patch("app.whatsapp.flows.menu.get_trader_by_phone", new_callable=AsyncMock, return_value=trader),
        ):
            from app.whatsapp.flows.menu import handle_text
            result = await handle_text(PHONE, "pay", {"step": "start", "data": {}})

        assert result["flow"] == "payment"
        assert result["data"]["direction"] == "ngn_to_cny"

    @pytest.mark.asyncio
    async def test_pay_unregistered(self):
        """Unregistered user is told to register."""
        with (
            patch("app.whatsapp.flows.menu.get_trader_by_phone", new_callable=AsyncMock, return_value=None),
            patch("app.whatsapp.flows.menu.send_text", new_callable=AsyncMock) as mock_text,
            patch("app.whatsapp.flows.menu.send_menu", new_callable=AsyncMock),
        ):
            from app.whatsapp.flows.menu import handle_text
            result = await handle_text(PHONE, "pay", {"step": "start", "data": {}})

        assert result["flow"] == "menu"
        assert "register" in mock_text.call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_rate_display(self):
        """Rate option fetches and shows rates."""
        mock_rates = {"ngn_per_cny": "213.9310"}
        mock_svc = AsyncMock()
        mock_svc.get_rates = AsyncMock(return_value=mock_rates)
        with (
            patch("app.whatsapp.flows.menu.get_user_lang", new_callable=AsyncMock, return_value="en"),
            patch("app.whatsapp.flows.menu.RateService", return_value=mock_svc),
            patch("app.whatsapp.flows.menu.send_text", new_callable=AsyncMock) as mock_text,
        ):
            from app.whatsapp.flows.menu import handle_text
            result = await handle_text(PHONE, "rate", {"step": "start", "data": {}})

        assert result["flow"] == "menu"
        assert "213.9310" in mock_text.call_args[0][1]

    @pytest.mark.asyncio
    async def test_status_route(self):
        """Status option routes to status flow."""
        from app.whatsapp.flows.menu import handle_text
        result = await handle_text(PHONE, "status", {"step": "start", "data": {}})

        assert result["flow"] == "status"

    @pytest.mark.asyncio
    async def test_register_route(self):
        """Register option routes to registration flow."""
        from app.whatsapp.flows.menu import handle_text
        result = await handle_text(PHONE, "register", {"step": "start", "data": {}})

        assert result["flow"] == "registration"

    @pytest.mark.asyncio
    async def test_interactive_action_rate(self):
        """action_rate button shows rates."""
        mock_rates = {"ngn_per_cny": "213.9310"}
        mock_svc = AsyncMock()
        mock_svc.get_rates = AsyncMock(return_value=mock_rates)
        with (
            patch("app.whatsapp.flows.menu.get_user_lang", new_callable=AsyncMock, return_value="en"),
            patch("app.whatsapp.flows.menu.RateService", return_value=mock_svc),
            patch("app.whatsapp.flows.menu.send_text", new_callable=AsyncMock) as mock_text,
        ):
            from app.whatsapp.flows.menu import handle_interactive
            result = await handle_interactive(PHONE, "action_rate", {"step": "start", "data": {}})

        assert "213.9310" in mock_text.call_args[0][1]

    @pytest.mark.asyncio
    async def test_unknown_input_shows_menu(self):
        """Unknown input shows the menu."""
        with (
            patch("app.whatsapp.flows.menu.send_menu", new_callable=AsyncMock) as mock_menu,
        ):
            from app.whatsapp.flows.menu import handle_text
            result = await handle_text(PHONE, "random gibberish", {"step": "start", "data": {}})

        mock_menu.assert_called_once()
        assert result["flow"] == "menu"
