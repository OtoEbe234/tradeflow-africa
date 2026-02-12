"""Tests for payment collection system — webhooks, dev simulate, and expiry."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.trader import TraderStatus
from app.models.transaction import Transaction, TransactionDirection, TransactionStatus
from app.services import auth_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pool_redis(mock_redis):
    """Extend mock_redis with sorted set methods needed by pool_manager."""
    mock_redis.zadd = AsyncMock()
    mock_redis.zrange = AsyncMock(return_value=[])
    mock_redis.zrem = AsyncMock()
    mock_redis.zrevrange = AsyncMock(return_value=[])
    mock_redis.zcard = AsyncMock(return_value=0)
    return mock_redis


@pytest.fixture
def trader_with_pin(make_trader):
    """Active trader with PIN set and default tier-1 limits."""
    trader = make_trader(status=TraderStatus.ACTIVE)
    trader.set_pin("1234")
    return trader


def _make_initiated_txn(trader_id, **overrides):
    """Create an INITIATED Transaction ORM object with sensible defaults."""
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


def _build_webhook_payload(txn, amount=None):
    """Build a Providus-format webhook dict for a transaction."""
    account_number = f"TF{txn.reference[4:]}"
    if amount is None:
        amount = float(txn.source_amount + txn.fee_amount)
    return {
        "sessionId": f"SIM-{txn.reference}-12345",
        "accountNumber": account_number,
        "transactionAmount": str(amount),
        "tranRemarks": f"Payment for {txn.reference}",
        "settledAmount": str(amount),
        "currency": "NGN",
        "initiationTranRef": txn.reference,
    }


def _setup_webhook_mocks(mock_db, trader, txn, completed_count=0):
    """Wire up the DB mocks for webhook processing.

    Sequence of db.execute calls in _process_payment:
    1. SELECT transaction by reference
    2. SELECT trader (for funded path)
    3. SELECT count completed (for priority calculation)
    """
    txn_result = MagicMock()
    txn_result.scalar_one_or_none = MagicMock(return_value=txn)

    trader_result = MagicMock()
    trader_result.scalar_one_or_none = MagicMock(return_value=trader)

    count_result = MagicMock()
    count_result.scalar_one = MagicMock(return_value=completed_count)

    mock_db.execute = AsyncMock(
        side_effect=[txn_result, trader_result, count_result],
    )


# ---------------------------------------------------------------------------
# TestWebhookSuccessfulFunding
# ---------------------------------------------------------------------------


class TestWebhookSuccessfulFunding:
    """Exact and within-tolerance payments → FUNDED."""

    @pytest.mark.asyncio
    @patch("app.api.webhooks.pool_manager", new_callable=AsyncMock)
    @patch("app.api.webhooks.send_status_update")
    @patch("app.services.payment_service.payment_service.verify_webhook_signature", return_value=True)
    async def test_exact_payment_funds_transaction(
        self, mock_verify, mock_notify, mock_pool, client, mock_db, pool_redis, trader_with_pin,
    ):
        """Exact payment amount transitions to FUNDED (200)."""
        txn = _make_initiated_txn(trader_with_pin.id)
        _setup_webhook_mocks(mock_db, trader_with_pin, txn)

        payload = _build_webhook_payload(txn)
        resp = await client.post("/api/v1/webhooks/providus", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["transaction_status"] == "funded"
        assert data["classification"] == "exact"
        assert txn.status == TransactionStatus.FUNDED
        assert txn.funded_at is not None
        mock_pool.add_to_pool.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.api.webhooks.pool_manager", new_callable=AsyncMock)
    @patch("app.api.webhooks.send_status_update")
    @patch("app.services.payment_service.payment_service.verify_webhook_signature", return_value=True)
    async def test_within_tolerance_funds_transaction(
        self, mock_verify, mock_notify, mock_pool, client, mock_db, pool_redis, trader_with_pin,
    ):
        """Payment within +/- NGN 100 tolerance → FUNDED."""
        txn = _make_initiated_txn(trader_with_pin.id)
        _setup_webhook_mocks(mock_db, trader_with_pin, txn)

        expected = float(txn.source_amount + txn.fee_amount)
        payload = _build_webhook_payload(txn, amount=expected - 50)
        resp = await client.post("/api/v1/webhooks/providus", json=payload)

        assert resp.status_code == 200
        assert resp.json()["classification"] == "exact"
        assert txn.status == TransactionStatus.FUNDED


# ---------------------------------------------------------------------------
# TestWebhookUnderpayment
# ---------------------------------------------------------------------------


class TestWebhookUnderpayment:
    """Underpayment handling: adjusted (95-99%) or held (< 95%)."""

    @pytest.mark.asyncio
    @patch("app.api.webhooks.pool_manager", new_callable=AsyncMock)
    @patch("app.api.webhooks.send_status_update")
    @patch("app.services.payment_service.payment_service.verify_webhook_signature", return_value=True)
    async def test_underpayment_95_to_99_accepted_adjusted(
        self, mock_verify, mock_notify, mock_pool, client, mock_db, pool_redis, trader_with_pin,
    ):
        """Payment at 97% of expected → accepted and amounts adjusted."""
        txn = _make_initiated_txn(trader_with_pin.id)
        original_source = txn.source_amount
        _setup_webhook_mocks(mock_db, trader_with_pin, txn)

        expected = float(txn.source_amount + txn.fee_amount)
        paid = expected * 0.97  # 97%
        payload = _build_webhook_payload(txn, amount=paid)
        resp = await client.post("/api/v1/webhooks/providus", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["classification"] == "adjusted"
        assert data["transaction_status"] == "funded"
        assert txn.status == TransactionStatus.FUNDED
        # Source amount should be reduced proportionally
        assert txn.source_amount < original_source

    @pytest.mark.asyncio
    @patch("app.api.webhooks.send_status_update")
    @patch("app.services.payment_service.payment_service.verify_webhook_signature", return_value=True)
    async def test_underpayment_below_95_held(
        self, mock_verify, mock_notify, client, mock_db, pool_redis, trader_with_pin,
    ):
        """Payment at 80% → held, notification sent."""
        txn = _make_initiated_txn(trader_with_pin.id)
        # For the held path, db.execute is called: 1. txn lookup, 2. trader lookup (for notification)
        txn_result = MagicMock()
        txn_result.scalar_one_or_none = MagicMock(return_value=txn)
        trader_result = MagicMock()
        trader_result.scalar_one_or_none = MagicMock(return_value=trader_with_pin)
        mock_db.execute = AsyncMock(side_effect=[txn_result, trader_result])

        expected = float(txn.source_amount + txn.fee_amount)
        paid = expected * 0.80
        payload = _build_webhook_payload(txn, amount=paid)
        resp = await client.post("/api/v1/webhooks/providus", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "held"
        # Transaction should still be INITIATED (not transitioned)
        assert txn.status == TransactionStatus.INITIATED
        # Notification sent about underpayment
        mock_notify.delay.assert_called_once()


# ---------------------------------------------------------------------------
# TestWebhookOverpayment
# ---------------------------------------------------------------------------


class TestWebhookOverpayment:
    """Overpayment → accepted as-is, no adjustment."""

    @pytest.mark.asyncio
    @patch("app.api.webhooks.pool_manager", new_callable=AsyncMock)
    @patch("app.api.webhooks.send_status_update")
    @patch("app.services.payment_service.payment_service.verify_webhook_signature", return_value=True)
    async def test_overpayment_accepted(
        self, mock_verify, mock_notify, mock_pool, client, mock_db, pool_redis, trader_with_pin,
    ):
        """Overpayment → accepted, source_amount unchanged."""
        txn = _make_initiated_txn(trader_with_pin.id)
        original_source = txn.source_amount
        original_fee = txn.fee_amount
        _setup_webhook_mocks(mock_db, trader_with_pin, txn)

        expected = float(txn.source_amount + txn.fee_amount)
        paid = expected * 1.10  # 10% overpayment
        payload = _build_webhook_payload(txn, amount=paid)
        resp = await client.post("/api/v1/webhooks/providus", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["classification"] == "overpayment"
        assert data["transaction_status"] == "funded"
        assert txn.status == TransactionStatus.FUNDED
        # Source amount and fee not adjusted for overpayment
        assert txn.source_amount == original_source
        assert txn.fee_amount == original_fee


# ---------------------------------------------------------------------------
# TestWebhookDuplicatePayment
# ---------------------------------------------------------------------------


class TestWebhookDuplicatePayment:
    """Already-funded transaction returns duplicate."""

    @pytest.mark.asyncio
    @patch("app.services.payment_service.payment_service.verify_webhook_signature", return_value=True)
    async def test_already_funded_returns_duplicate(
        self, mock_verify, client, mock_db, pool_redis, trader_with_pin,
    ):
        """Already FUNDED → returns 'duplicate'."""
        txn = _make_initiated_txn(trader_with_pin.id)
        txn.transition_to(TransactionStatus.FUNDED)

        txn_result = MagicMock()
        txn_result.scalar_one_or_none = MagicMock(return_value=txn)
        mock_db.execute = AsyncMock(return_value=txn_result)

        payload = _build_webhook_payload(txn)
        resp = await client.post("/api/v1/webhooks/providus", json=payload)

        assert resp.status_code == 200
        assert resp.json()["status"] == "duplicate"


# ---------------------------------------------------------------------------
# TestWebhookValidation
# ---------------------------------------------------------------------------


class TestWebhookValidation:
    """Webhook input validation tests."""

    @pytest.mark.asyncio
    @patch("app.services.payment_service.payment_service.verify_webhook_signature", return_value=False)
    async def test_invalid_signature_401(
        self, mock_verify, client, mock_db, pool_redis,
    ):
        """Invalid HMAC signature → 401."""
        payload = {
            "sessionId": "SIM-TEST",
            "accountNumber": "TFABCD1234",
            "transactionAmount": "1000000",
        }
        resp = await client.post("/api/v1/webhooks/providus", json=payload)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    @patch("app.services.payment_service.payment_service.verify_webhook_signature", return_value=True)
    async def test_missing_fields_400(
        self, mock_verify, client, mock_db, pool_redis,
    ):
        """Missing required fields → 400."""
        payload = {"accountNumber": "TFABCD1234"}  # missing transactionAmount, sessionId
        resp = await client.post("/api/v1/webhooks/providus", json=payload)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @patch("app.services.payment_service.payment_service.verify_webhook_signature", return_value=True)
    async def test_bad_account_format_400(
        self, mock_verify, client, mock_db, pool_redis,
    ):
        """Account number not starting with TF → 400."""
        payload = {
            "sessionId": "SIM-TEST",
            "accountNumber": "XXBADFORMAT",
            "transactionAmount": "1000000",
        }
        resp = await client.post("/api/v1/webhooks/providus", json=payload)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @patch("app.services.payment_service.payment_service.verify_webhook_signature", return_value=True)
    async def test_transaction_not_found_404(
        self, mock_verify, client, mock_db, pool_redis,
    ):
        """Account number with no matching transaction → 404."""
        txn_result = MagicMock()
        txn_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=txn_result)

        payload = {
            "sessionId": "SIM-TEST",
            "accountNumber": "TFNOTFOUND",
            "transactionAmount": "1000000",
        }
        resp = await client.post("/api/v1/webhooks/providus", json=payload)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TestTransactionExpiry
# ---------------------------------------------------------------------------


class TestTransactionExpiry:
    """Tests for the expire_stale_transactions Celery task."""

    @pytest.mark.asyncio
    @patch("app.tasks.notification_tasks.send_status_update")
    async def test_stale_transaction_expired(self, mock_notify):
        """Transaction older than PAYMENT_EXPIRY_HOURS is expired."""
        from app.tasks.payment_tasks import _expire_stale_transactions_async

        trader = MagicMock()
        trader.phone = "+2348012345678"

        txn = _make_initiated_txn(uuid.uuid4())
        txn.created_at = datetime.now(timezone.utc) - timedelta(hours=3)

        # Mock async session
        mock_session = AsyncMock()
        txn_result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[txn])
        txn_result.scalars = MagicMock(return_value=scalars)

        trader_result = MagicMock()
        trader_result.scalar_one_or_none = MagicMock(return_value=trader)

        mock_session.execute = AsyncMock(side_effect=[txn_result, trader_result])
        mock_session.commit = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("app.database.async_session", mock_session_factory):
            result = await _expire_stale_transactions_async()

        assert result["expired_count"] == 1
        assert txn.reference in result["expired_references"]
        assert txn.status == TransactionStatus.EXPIRED
        mock_notify.delay.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.tasks.notification_tasks.send_status_update")
    async def test_fresh_transaction_untouched(self, mock_notify):
        """Transaction created recently is NOT expired."""
        from app.tasks.payment_tasks import _expire_stale_transactions_async

        # No stale transactions
        mock_session = AsyncMock()
        txn_result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[])
        txn_result.scalars = MagicMock(return_value=scalars)
        mock_session.execute = AsyncMock(return_value=txn_result)
        mock_session.commit = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("app.database.async_session", mock_session_factory):
            result = await _expire_stale_transactions_async()

        assert result["expired_count"] == 0
        mock_notify.delay.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.tasks.notification_tasks.send_status_update")
    async def test_expiry_sends_notification(self, mock_notify):
        """Expired transaction triggers notification to trader."""
        from app.tasks.payment_tasks import _expire_stale_transactions_async

        trader = MagicMock()
        trader.phone = "+2348099999999"

        txn = _make_initiated_txn(uuid.uuid4())
        txn.created_at = datetime.now(timezone.utc) - timedelta(hours=5)

        mock_session = AsyncMock()
        txn_result = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[txn])
        txn_result.scalars = MagicMock(return_value=scalars)

        trader_result = MagicMock()
        trader_result.scalar_one_or_none = MagicMock(return_value=trader)

        mock_session.execute = AsyncMock(side_effect=[txn_result, trader_result])
        mock_session.commit = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("app.database.async_session", mock_session_factory):
            result = await _expire_stale_transactions_async()

        mock_notify.delay.assert_called_once_with(
            "+2348099999999", txn.reference, "expired",
        )


# ---------------------------------------------------------------------------
# TestDevSimulatePayment
# ---------------------------------------------------------------------------


class TestDevSimulatePayment:
    """Tests for the dev simulate-payment endpoint."""

    @pytest.mark.asyncio
    @patch("app.api.webhooks.pool_manager", new_callable=AsyncMock)
    @patch("app.api.webhooks.send_status_update")
    @patch("app.api.dev.settings")
    async def test_simulate_success_funds_transaction(
        self, mock_settings, mock_notify, mock_pool, client, mock_db, pool_redis, trader_with_pin,
    ):
        """Successful simulation → FUNDED."""
        mock_settings.APP_ENV = "development"

        txn = _make_initiated_txn(trader_with_pin.id)
        exact_amount = float(txn.source_amount + txn.fee_amount)

        # First call: dev.py looks up txn by id
        dev_txn_result = MagicMock()
        dev_txn_result.scalar_one_or_none = MagicMock(return_value=txn)
        # Then _process_payment calls: txn by reference, trader, count
        webhook_txn_result = MagicMock()
        webhook_txn_result.scalar_one_or_none = MagicMock(return_value=txn)
        trader_result = MagicMock()
        trader_result.scalar_one_or_none = MagicMock(return_value=trader_with_pin)
        count_result = MagicMock()
        count_result.scalar_one = MagicMock(return_value=0)

        mock_db.execute = AsyncMock(
            side_effect=[dev_txn_result, webhook_txn_result, trader_result, count_result],
        )

        resp = await client.post(
            "/api/v1/dev/simulate-payment",
            json={"transaction_id": str(txn.id), "amount": exact_amount},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["result"]["status"] == "success"
        assert data["result"]["transaction_status"] == "funded"
        assert "webhook_payload" in data
        assert txn.status == TransactionStatus.FUNDED

    @pytest.mark.asyncio
    @patch("app.api.dev.settings")
    async def test_simulate_production_mode_403(
        self, mock_settings, client, mock_db, pool_redis,
    ):
        """Production mode → 403."""
        mock_settings.APP_ENV = "production"

        resp = await client.post(
            "/api/v1/dev/simulate-payment",
            json={"transaction_id": str(uuid.uuid4()), "amount": 1000000},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    @patch("app.api.dev.settings")
    async def test_simulate_not_found_404(
        self, mock_settings, client, mock_db, pool_redis,
    ):
        """Non-existent transaction → 404."""
        mock_settings.APP_ENV = "development"

        txn_result = MagicMock()
        txn_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=txn_result)

        resp = await client.post(
            "/api/v1/dev/simulate-payment",
            json={"transaction_id": str(uuid.uuid4()), "amount": 1000000},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("app.api.dev.settings")
    async def test_simulate_already_funded_409(
        self, mock_settings, client, mock_db, pool_redis, trader_with_pin,
    ):
        """Already funded → 409."""
        mock_settings.APP_ENV = "development"

        txn = _make_initiated_txn(trader_with_pin.id)
        txn.transition_to(TransactionStatus.FUNDED)

        txn_result = MagicMock()
        txn_result.scalar_one_or_none = MagicMock(return_value=txn)
        mock_db.execute = AsyncMock(return_value=txn_result)

        resp = await client.post(
            "/api/v1/dev/simulate-payment",
            json={"transaction_id": str(txn.id), "amount": 1000000},
        )
        assert resp.status_code == 409
