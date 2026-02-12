"""
Integration test — full payment-to-pool flow with real PostgreSQL and Redis.

Runs the FULL lifecycle:
  1. Register a trader  (DB insert)
  2. Create a transaction  (DB insert, status=INITIATED)
  3. Simulate payment  (INITIATED → FUNDED, MatchingPool row, Redis sorted-set + hash)
  4. Verify transaction status in DB
  5. Verify MatchingPool record in DB
  6. Verify Redis sorted set has the entry
  7. Verify Redis hash has correct details

Prerequisites:
  docker compose -f docker-compose.test.yml up -d
"""

import json
import os
import socket
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


_PG_UP = _port_open(os.environ.get("PGHOST", "localhost"), int(os.environ.get("PGPORT", "5433")))
_REDIS_UP = _port_open(os.environ.get("REDIS_HOST", "localhost"), int(os.environ.get("REDIS_PORT", "6380")))

pytestmark = pytest.mark.skipif(
    not (_PG_UP and _REDIS_UP),
    reason="Integration tests require PostgreSQL and Redis. Start with: docker compose -f docker-compose.test.yml up -d",
)

from app.matching_engine.config import POOL_KEY_BUY, POOL_KEY_SELL
from app.matching_engine.pool_manager import _entry_hash_key
from app.models.matching_pool import MatchingPool
from app.models.trader import Trader, TraderStatus
from app.models.transaction import Transaction, TransactionDirection, TransactionStatus
from app.services import auth_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_trader(pin: str = "1234", **overrides) -> Trader:
    """Build an active Trader with PIN, ready for transactions."""
    defaults = dict(
        phone="+2348012345678",
        full_name="Adebayo Ogunlesi",
        business_name="Lagos Trading Co.",
        status=TraderStatus.ACTIVE,
    )
    defaults.update(overrides)
    trader = Trader(**defaults)
    trader.set_pin(pin)
    return trader


def _create_transaction(trader_id, **overrides) -> Transaction:
    """Build an INITIATED transaction for the given trader."""
    defaults = dict(
        trader_id=trader_id,
        direction=TransactionDirection.NGN_TO_CNY,
        source_amount=Decimal("1000000"),
        target_amount=Decimal("4677.42"),
        exchange_rate=Decimal("213.7931"),
        fee_amount=Decimal("20000"),
        fee_percentage=Decimal("2.00"),
        supplier_name="Shenzhen Electronics Co.",
        supplier_bank="Bank of China",
        status=TransactionStatus.INITIATED,
    )
    defaults.update(overrides)
    txn = Transaction(**defaults)
    txn.set_supplier_account("621082100123456789")
    return txn


# ---------------------------------------------------------------------------
# Full-flow test
# ---------------------------------------------------------------------------


class TestPoolEntryFullFlow:
    """End-to-end: trader → transaction → payment → pool entry."""

    @pytest.mark.asyncio
    @patch("app.api.webhooks.send_status_update")
    async def test_payment_creates_pool_entry_in_db_and_redis(
        self,
        mock_notify,
        db_session,
        real_redis,
        integration_client,
    ):
        """
        Full flow: register trader, create transaction, simulate payment,
        verify the matching pool record in both PostgreSQL and Redis.
        """
        # ── 1. Create trader in DB ──────────────────────────────────────
        trader = _create_trader()
        db_session.add(trader)
        await db_session.flush()

        # ── 2. Create INITIATED transaction in DB ───────────────────────
        txn = _create_transaction(trader.id)
        db_session.add(txn)
        await db_session.flush()

        assert txn.status == TransactionStatus.INITIATED
        assert txn.funded_at is None

        # ── 3. Simulate payment via the dev endpoint ────────────────────
        token = auth_service.create_access_token(str(trader.id), trader.phone)
        exact_amount = float(txn.source_amount + txn.fee_amount)  # 1_020_000

        # Patch pool_manager to use the real_redis instead of the
        # module-level redis client
        with patch("app.matching_engine.pool_manager.redis", real_redis):
            resp = await integration_client.post(
                "/api/v1/dev/simulate-payment",
                json={
                    "transaction_id": str(txn.id),
                    "amount": exact_amount,
                },
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["result"]["status"] == "success"
        assert data["result"]["transaction_status"] == "funded"
        assert data["result"]["classification"] == "exact"
        pool_entry_id = data["result"]["pool_entry_id"]

        # ── 4. Verify transaction status in DB ──────────────────────────
        result = await db_session.execute(
            select(Transaction).where(Transaction.id == txn.id)
        )
        db_txn = result.scalar_one()
        assert db_txn.status == TransactionStatus.FUNDED
        assert db_txn.funded_at is not None

        # ── 5. Verify MatchingPool record in DB ─────────────────────────
        result = await db_session.execute(
            select(MatchingPool).where(MatchingPool.transaction_id == txn.id)
        )
        pool_row = result.scalar_one()

        assert pool_row is not None
        assert str(pool_row.id) == pool_entry_id
        assert pool_row.trader_id == trader.id
        assert pool_row.direction == TransactionDirection.NGN_TO_CNY
        assert pool_row.amount == txn.source_amount
        assert pool_row.currency == "NGN"
        assert pool_row.is_active is True
        assert pool_row.priority_score > 0
        assert pool_row.entered_pool_at is not None
        assert pool_row.expires_at > pool_row.entered_pool_at

        # ── 6. Verify Redis sorted set ──────────────────────────────────
        # The entry should be in pool:ngn_to_cny with the pool_entry_id as member
        members = await real_redis.zrange(POOL_KEY_BUY, 0, -1, withscores=True)
        assert len(members) == 1
        member_id, score = members[0]
        assert member_id == pool_entry_id
        assert score > 0

        # ── 7. Verify Redis hash ────────────────────────────────────────
        hash_key = _entry_hash_key(pool_entry_id)
        entry_data = await real_redis.hgetall(hash_key)

        assert entry_data is not None
        assert entry_data["id"] == pool_entry_id
        assert entry_data["transaction_id"] == str(txn.id)
        assert entry_data["reference"] == txn.reference
        assert entry_data["source_amount"] == str(txn.source_amount)
        assert entry_data["direction"] == "ngn_to_cny"
        assert entry_data["currency"] == "NGN"
        assert entry_data["trader_id"] == str(trader.id)
        assert "entered_pool_at" in entry_data
        assert "expires_at" in entry_data

        # Verify expiry is ~24h after entry
        entered = datetime.fromisoformat(entry_data["entered_pool_at"])
        expires = datetime.fromisoformat(entry_data["expires_at"])
        delta_hours = (expires - entered).total_seconds() / 3600
        assert 23.9 < delta_hours < 24.1

        # ── 8. Verify notification was sent ─────────────────────────────
        mock_notify.delay.assert_called_once_with(
            trader.phone, txn.reference, "funded",
        )

    @pytest.mark.asyncio
    @patch("app.api.webhooks.send_status_update")
    async def test_cny_to_ngn_direction_uses_sell_pool(
        self,
        mock_notify,
        db_session,
        real_redis,
        integration_client,
    ):
        """CNY→NGN transactions land in the sell pool (pool:cny_to_ngn)."""
        trader = _create_trader(phone="+2348099887766")
        db_session.add(trader)
        await db_session.flush()

        txn = _create_transaction(
            trader.id,
            direction=TransactionDirection.CNY_TO_NGN,
            source_amount=Decimal("50000"),
            target_amount=Decimal("10685000"),
            fee_amount=Decimal("1000"),
        )
        db_session.add(txn)
        await db_session.flush()

        exact_amount = float(txn.source_amount + txn.fee_amount)

        with patch("app.matching_engine.pool_manager.redis", real_redis):
            resp = await integration_client.post(
                "/api/v1/dev/simulate-payment",
                json={"transaction_id": str(txn.id), "amount": exact_amount},
            )

        assert resp.status_code == 200
        pool_entry_id = resp.json()["result"]["pool_entry_id"]

        # Should be in the SELL pool, not BUY
        buy_members = await real_redis.zrange(POOL_KEY_BUY, 0, -1)
        sell_members = await real_redis.zrange(POOL_KEY_SELL, 0, -1)
        assert len(buy_members) == 0
        assert len(sell_members) == 1
        assert sell_members[0] == pool_entry_id

        # Redis hash should show CNY currency
        entry_data = await real_redis.hgetall(_entry_hash_key(pool_entry_id))
        assert entry_data["currency"] == "CNY"
        assert entry_data["direction"] == "cny_to_ngn"

    @pytest.mark.asyncio
    @patch("app.api.webhooks.send_status_update")
    async def test_duplicate_payment_does_not_create_second_pool_entry(
        self,
        mock_notify,
        db_session,
        real_redis,
        integration_client,
    ):
        """Paying twice for the same transaction should return 'duplicate'."""
        trader = _create_trader(phone="+2348055566677")
        db_session.add(trader)
        await db_session.flush()

        txn = _create_transaction(trader.id)
        db_session.add(txn)
        await db_session.flush()

        exact_amount = float(txn.source_amount + txn.fee_amount)

        with patch("app.matching_engine.pool_manager.redis", real_redis):
            # First payment — should succeed
            resp1 = await integration_client.post(
                "/api/v1/dev/simulate-payment",
                json={"transaction_id": str(txn.id), "amount": exact_amount},
            )
            assert resp1.status_code == 200
            assert resp1.json()["result"]["status"] == "success"

            # Second payment — should return duplicate
            # Need to reset the mock_db since dev.py also checks status
            resp2 = await integration_client.post(
                "/api/v1/dev/simulate-payment",
                json={"transaction_id": str(txn.id), "amount": exact_amount},
            )
            assert resp2.status_code == 409  # dev.py catches non-INITIATED

        # Only one pool entry in Redis
        members = await real_redis.zrange(POOL_KEY_BUY, 0, -1)
        assert len(members) == 1

        # Only one pool entry in DB
        result = await db_session.execute(
            select(MatchingPool).where(MatchingPool.transaction_id == txn.id)
        )
        pool_rows = list(result.scalars().all())
        assert len(pool_rows) == 1

    @pytest.mark.asyncio
    @patch("app.api.webhooks.send_status_update")
    async def test_adjusted_payment_updates_pool_amount(
        self,
        mock_notify,
        db_session,
        real_redis,
        integration_client,
    ):
        """A 97% payment should create pool entry with adjusted amount."""
        trader = _create_trader(phone="+2348011122233")
        db_session.add(trader)
        await db_session.flush()

        txn = _create_transaction(trader.id)
        db_session.add(txn)
        await db_session.flush()

        original_source = txn.source_amount
        expected = float(txn.source_amount + txn.fee_amount)
        paid = expected * 0.97  # 97% underpayment

        with patch("app.matching_engine.pool_manager.redis", real_redis):
            resp = await integration_client.post(
                "/api/v1/dev/simulate-payment",
                json={"transaction_id": str(txn.id), "amount": paid},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["result"]["classification"] == "adjusted"
        pool_entry_id = data["result"]["pool_entry_id"]

        # DB pool entry should have the adjusted amount
        result = await db_session.execute(
            select(MatchingPool).where(MatchingPool.id == pool_entry_id)
        )
        pool_row = result.scalar_one()
        assert pool_row.amount < original_source

        # Redis hash should have the adjusted amount
        entry_data = await real_redis.hgetall(_entry_hash_key(pool_entry_id))
        assert Decimal(entry_data["source_amount"]) < original_source

    @pytest.mark.asyncio
    @patch("app.api.webhooks.send_status_update")
    async def test_webhook_endpoint_creates_pool_entry(
        self,
        mock_notify,
        db_session,
        real_redis,
        integration_client,
    ):
        """Test the actual webhook endpoint (not dev simulate) also creates pool entries."""
        trader = _create_trader(phone="+2348077788899")
        db_session.add(trader)
        await db_session.flush()

        txn = _create_transaction(trader.id)
        db_session.add(txn)
        await db_session.flush()

        exact_amount = float(txn.source_amount + txn.fee_amount)
        account_number = f"TF{txn.reference[4:]}"

        webhook_payload = {
            "sessionId": f"PROV-{txn.reference}-99999",
            "accountNumber": account_number,
            "transactionAmount": str(exact_amount),
            "tranRemarks": f"Payment for {txn.reference}",
            "settledAmount": str(exact_amount),
            "currency": "NGN",
        }

        with (
            patch("app.matching_engine.pool_manager.redis", real_redis),
            patch(
                "app.services.payment_service.payment_service.verify_webhook_signature",
                return_value=True,
            ),
        ):
            resp = await integration_client.post(
                "/api/v1/webhooks/providus",
                json=webhook_payload,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["transaction_status"] == "funded"
        pool_entry_id = data["pool_entry_id"]

        # Verify DB
        result = await db_session.execute(
            select(MatchingPool).where(MatchingPool.transaction_id == txn.id)
        )
        pool_row = result.scalar_one()
        assert str(pool_row.id) == pool_entry_id

        # Verify Redis
        members = await real_redis.zrange(POOL_KEY_BUY, 0, -1)
        assert pool_entry_id in members

        entry_data = await real_redis.hgetall(_entry_hash_key(pool_entry_id))
        assert entry_data["transaction_id"] == str(txn.id)
