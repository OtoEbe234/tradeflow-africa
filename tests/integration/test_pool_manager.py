"""
Integration tests for PoolManager — real Redis (docker-compose.test.yml).

Tests: add, remove, snapshot ordering, update_entry_amount,
       lock acquisition, concurrent lock attempt, lock expiry,
       get_pool_stats with volumes.

Prerequisites:
  docker compose -f docker-compose.test.yml up -d
"""

import asyncio
import os
import socket
from decimal import Decimal

import pytest
import pytest_asyncio

from app.matching_engine.pool_manager import (
    PoolManager,
    _entry_hash_key,
    _pool_key,
    LOCK_TIMEOUT_SECONDS,
)
from app.matching_engine.config import POOL_KEY_BUY, POOL_KEY_SELL, POOL_LOCK_KEY


# ── Skip if Redis not available ────────────────────────────────────────────

def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


_REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
_REDIS_PORT = int(os.environ.get("REDIS_PORT", "6380"))
_REDIS_UP = _port_open(_REDIS_HOST, _REDIS_PORT)

pytestmark = pytest.mark.skipif(
    not _REDIS_UP,
    reason=(
        f"Pool manager tests require Redis ({_REDIS_HOST}:{_REDIS_PORT}). "
        "Start with: docker compose -f docker-compose.test.yml up -d"
    ),
)


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def redis_client():
    """Provide a real Redis client, flushed after each test."""
    if not _REDIS_UP:
        pytest.skip("Redis not available")
    import redis.asyncio as aioredis

    url = os.environ.get("REDIS_URL", f"redis://{_REDIS_HOST}:{_REDIS_PORT}")
    r = aioredis.from_url(url, decode_responses=True)
    yield r
    await r.flushdb()
    await r.aclose()


@pytest.fixture
def pm(redis_client) -> PoolManager:
    """A PoolManager wired to the test Redis instance."""
    return PoolManager(redis_client=redis_client)


# ── Helpers ────────────────────────────────────────────────────────────────

def _entry_data(
    *,
    ref: str = "TXN-ABC123",
    amount: str = "1000000",
    direction: str = "ngn_to_cny",
    currency: str = "NGN",
    trader_id: str = "trader-1",
) -> dict:
    return {
        "reference": ref,
        "source_amount": amount,
        "target_amount": "4677.42",
        "direction": direction,
        "currency": currency,
        "trader_id": trader_id,
        "entered_pool_at": "2026-01-01T00:00:00+00:00",
        "expires_at": "2026-01-02T00:00:00+00:00",
    }


# ── Add / Remove ──────────────────────────────────────────────────────────


class TestAddToPool:
    """Adding entries creates both sorted-set member and detail hash."""

    @pytest.mark.asyncio
    async def test_add_creates_sorted_set_member(self, pm, redis_client):
        await pm.add_to_pool(
            pool_entry_id="pe-1",
            transaction_id="txn-1",
            direction="ngn_to_cny",
            data=_entry_data(),
            score=50.0,
        )

        members = await redis_client.zrange(POOL_KEY_BUY, 0, -1, withscores=True)
        assert len(members) == 1
        assert members[0] == ("pe-1", 50.0)

    @pytest.mark.asyncio
    async def test_add_creates_detail_hash(self, pm, redis_client):
        await pm.add_to_pool(
            pool_entry_id="pe-1",
            transaction_id="txn-1",
            direction="ngn_to_cny",
            data=_entry_data(),
            score=50.0,
        )

        h = await redis_client.hgetall(_entry_hash_key("pe-1"))
        assert h["id"] == "pe-1"
        assert h["transaction_id"] == "txn-1"
        assert h["reference"] == "TXN-ABC123"
        assert h["source_amount"] == "1000000"
        assert h["direction"] == "ngn_to_cny"

    @pytest.mark.asyncio
    async def test_add_to_sell_pool(self, pm, redis_client):
        await pm.add_to_pool(
            pool_entry_id="pe-sell",
            transaction_id="txn-sell",
            direction="cny_to_ngn",
            data=_entry_data(direction="cny_to_ngn", currency="CNY"),
            score=30.0,
        )

        buy = await redis_client.zcard(POOL_KEY_BUY)
        sell = await redis_client.zcard(POOL_KEY_SELL)
        assert buy == 0
        assert sell == 1

    @pytest.mark.asyncio
    async def test_add_is_atomic_pipeline(self, pm, redis_client):
        """Both ZADD and HSET happen in one pipeline execution."""
        await pm.add_to_pool(
            pool_entry_id="pe-atom",
            transaction_id="txn-atom",
            direction="ngn_to_cny",
            data=_entry_data(),
            score=42.0,
        )
        # If both wrote atomically, both exist
        assert await redis_client.zscore(POOL_KEY_BUY, "pe-atom") == 42.0
        assert await redis_client.hget(_entry_hash_key("pe-atom"), "id") == "pe-atom"


class TestRemoveFromPool:
    """Removing entries deletes both sorted-set member and hash."""

    @pytest.mark.asyncio
    async def test_remove_deletes_sorted_set_member(self, pm, redis_client):
        await pm.add_to_pool("pe-1", "txn-1", "ngn_to_cny", _entry_data(), 50.0)
        await pm.remove_from_pool("pe-1", "ngn_to_cny")

        members = await redis_client.zrange(POOL_KEY_BUY, 0, -1)
        assert len(members) == 0

    @pytest.mark.asyncio
    async def test_remove_deletes_detail_hash(self, pm, redis_client):
        await pm.add_to_pool("pe-1", "txn-1", "ngn_to_cny", _entry_data(), 50.0)
        await pm.remove_from_pool("pe-1", "ngn_to_cny")

        exists = await redis_client.exists(_entry_hash_key("pe-1"))
        assert exists == 0

    @pytest.mark.asyncio
    async def test_remove_nonexistent_is_noop(self, pm, redis_client):
        """Removing an entry that doesn't exist should not raise."""
        await pm.remove_from_pool("pe-ghost", "ngn_to_cny")
        assert await redis_client.zcard(POOL_KEY_BUY) == 0


# ── Snapshot Ordering ─────────────────────────────────────────────────────


class TestGetPoolSnapshot:
    """Snapshot returns entries sorted by descending priority score."""

    @pytest.mark.asyncio
    async def test_snapshot_returns_highest_priority_first(self, pm):
        await pm.add_to_pool("pe-low", "txn-1", "ngn_to_cny",
                             _entry_data(ref="LOW"), 10.0)
        await pm.add_to_pool("pe-mid", "txn-2", "ngn_to_cny",
                             _entry_data(ref="MID"), 50.0)
        await pm.add_to_pool("pe-high", "txn-3", "ngn_to_cny",
                             _entry_data(ref="HIGH"), 90.0)

        snapshot = await pm.get_pool_snapshot("ngn_to_cny")
        assert len(snapshot) == 3
        assert snapshot[0]["id"] == "pe-high"
        assert snapshot[1]["id"] == "pe-mid"
        assert snapshot[2]["id"] == "pe-low"

    @pytest.mark.asyncio
    async def test_snapshot_includes_score(self, pm):
        await pm.add_to_pool("pe-1", "txn-1", "ngn_to_cny",
                             _entry_data(), 72.5)

        snapshot = await pm.get_pool_snapshot("ngn_to_cny")
        assert snapshot[0]["_score"] == 72.5

    @pytest.mark.asyncio
    async def test_snapshot_empty_pool(self, pm):
        snapshot = await pm.get_pool_snapshot("ngn_to_cny")
        assert snapshot == []

    @pytest.mark.asyncio
    async def test_snapshot_directions_are_independent(self, pm):
        await pm.add_to_pool("pe-buy", "txn-1", "ngn_to_cny",
                             _entry_data(), 50.0)
        await pm.add_to_pool("pe-sell", "txn-2", "cny_to_ngn",
                             _entry_data(direction="cny_to_ngn"), 60.0)

        buy_snap = await pm.get_pool_snapshot("ngn_to_cny")
        sell_snap = await pm.get_pool_snapshot("cny_to_ngn")
        assert len(buy_snap) == 1
        assert buy_snap[0]["id"] == "pe-buy"
        assert len(sell_snap) == 1
        assert sell_snap[0]["id"] == "pe-sell"

    @pytest.mark.asyncio
    async def test_snapshot_uses_pipeline_for_hashes(self, pm):
        """Add 5 entries and verify snapshot retrieves all of them."""
        for i in range(5):
            await pm.add_to_pool(
                f"pe-{i}", f"txn-{i}", "ngn_to_cny",
                _entry_data(ref=f"REF-{i}", amount=str(1000 * (i + 1))),
                float(i * 10),
            )

        snapshot = await pm.get_pool_snapshot("ngn_to_cny")
        assert len(snapshot) == 5
        # Highest score first
        assert snapshot[0]["id"] == "pe-4"
        assert snapshot[4]["id"] == "pe-0"

    @pytest.mark.asyncio
    async def test_backward_compat_get_buy_pool(self, pm):
        await pm.add_to_pool("pe-1", "txn-1", "ngn_to_cny",
                             _entry_data(), 50.0)
        result = await pm.get_buy_pool()
        assert len(result) == 1
        assert result[0]["id"] == "pe-1"

    @pytest.mark.asyncio
    async def test_backward_compat_get_sell_pool(self, pm):
        await pm.add_to_pool("pe-1", "txn-1", "cny_to_ngn",
                             _entry_data(direction="cny_to_ngn"), 50.0)
        result = await pm.get_sell_pool()
        assert len(result) == 1
        assert result[0]["id"] == "pe-1"


# ── Update Entry Amount ──────────────────────────────────────────────────


class TestUpdateEntryAmount:
    """Partial match updates the hash amount but not the score."""

    @pytest.mark.asyncio
    async def test_update_changes_hash_amount(self, pm, redis_client):
        await pm.add_to_pool("pe-1", "txn-1", "ngn_to_cny",
                             _entry_data(amount="1000000"), 50.0)

        await pm.update_entry_amount("pe-1", Decimal("600000"))

        h = await redis_client.hget(_entry_hash_key("pe-1"), "source_amount")
        assert h == "600000"

    @pytest.mark.asyncio
    async def test_update_preserves_sorted_set_score(self, pm, redis_client):
        await pm.add_to_pool("pe-1", "txn-1", "ngn_to_cny",
                             _entry_data(amount="1000000"), 50.0)

        await pm.update_entry_amount("pe-1", Decimal("600000"))

        score = await redis_client.zscore(POOL_KEY_BUY, "pe-1")
        assert score == 50.0

    @pytest.mark.asyncio
    async def test_update_preserves_other_hash_fields(self, pm, redis_client):
        await pm.add_to_pool("pe-1", "txn-1", "ngn_to_cny",
                             _entry_data(amount="1000000"), 50.0)

        await pm.update_entry_amount("pe-1", "750000")

        h = await redis_client.hgetall(_entry_hash_key("pe-1"))
        assert h["source_amount"] == "750000"
        assert h["transaction_id"] == "txn-1"
        assert h["reference"] == "TXN-ABC123"
        assert h["direction"] == "ngn_to_cny"

    @pytest.mark.asyncio
    async def test_update_reflects_in_snapshot(self, pm):
        await pm.add_to_pool("pe-1", "txn-1", "ngn_to_cny",
                             _entry_data(amount="1000000"), 50.0)

        await pm.update_entry_amount("pe-1", "400000")

        snapshot = await pm.get_pool_snapshot("ngn_to_cny")
        assert snapshot[0]["source_amount"] == "400000"


# ── get_entry ────────────────────────────────────────────────────────────


class TestGetEntry:
    """Retrieve a single entry's detail hash."""

    @pytest.mark.asyncio
    async def test_get_existing_entry(self, pm):
        await pm.add_to_pool("pe-1", "txn-1", "ngn_to_cny",
                             _entry_data(), 50.0)
        entry = await pm.get_entry("pe-1")
        assert entry is not None
        assert entry["id"] == "pe-1"
        assert entry["transaction_id"] == "txn-1"

    @pytest.mark.asyncio
    async def test_get_nonexistent_entry(self, pm):
        entry = await pm.get_entry("pe-ghost")
        assert entry is None


# ── Distributed Lock ─────────────────────────────────────────────────────


class TestDistributedLock:
    """Redis distributed lock with 5-minute auto-expiry."""

    @pytest.mark.asyncio
    async def test_acquire_lock_success(self, pm):
        lock = await pm.acquire_lock()
        assert lock is not None
        await pm.release_lock(lock)

    @pytest.mark.asyncio
    async def test_concurrent_lock_attempt_fails(self, pm):
        """Second acquire returns None while first lock is held."""
        lock1 = await pm.acquire_lock()
        assert lock1 is not None

        lock2 = await pm.acquire_lock()
        assert lock2 is None

        await pm.release_lock(lock1)

    @pytest.mark.asyncio
    async def test_lock_can_be_reacquired_after_release(self, pm):
        lock1 = await pm.acquire_lock()
        assert lock1 is not None
        await pm.release_lock(lock1)

        lock2 = await pm.acquire_lock()
        assert lock2 is not None
        await pm.release_lock(lock2)

    @pytest.mark.asyncio
    async def test_lock_auto_expires(self, pm, redis_client):
        """Lock with short timeout auto-expires, allowing re-acquisition."""
        # Create a separate PoolManager that uses a very short lock timeout
        # We'll manually acquire with a short TTL
        short_lock = redis_client.lock(
            POOL_LOCK_KEY,
            timeout=1,  # 1-second expiry
            blocking=False,
        )
        acquired = await short_lock.acquire()
        assert acquired is True

        # Immediately, lock is held — cannot acquire via pm
        pm_lock = await pm.acquire_lock()
        assert pm_lock is None

        # Wait for expiry
        await asyncio.sleep(1.1)

        # Now we can acquire
        pm_lock = await pm.acquire_lock()
        assert pm_lock is not None
        await pm.release_lock(pm_lock)

    @pytest.mark.asyncio
    async def test_release_expired_lock_does_not_raise(self, pm, redis_client):
        """Releasing an already-expired lock logs warning but doesn't crash."""
        short_lock = redis_client.lock(
            POOL_LOCK_KEY,
            timeout=1,
            blocking=False,
        )
        await short_lock.acquire()
        await asyncio.sleep(1.1)
        # Lock has expired — release should not raise
        await pm.release_lock(short_lock)

    @pytest.mark.asyncio
    async def test_lock_key_set_in_redis(self, pm, redis_client):
        lock = await pm.acquire_lock()
        assert lock is not None

        # The lock key should exist in Redis
        exists = await redis_client.exists(POOL_LOCK_KEY)
        assert exists == 1

        await pm.release_lock(lock)

        exists_after = await redis_client.exists(POOL_LOCK_KEY)
        assert exists_after == 0


# ── Pool Stats ───────────────────────────────────────────────────────────


class TestGetPoolStats:
    """Pool statistics with counts and volumes."""

    @pytest.mark.asyncio
    async def test_empty_pools(self, pm):
        stats = await pm.get_pool_stats()
        assert stats == {
            "ngn_to_cny_count": 0,
            "ngn_to_cny_volume": "0",
            "cny_to_ngn_count": 0,
            "cny_to_ngn_volume": "0",
        }

    @pytest.mark.asyncio
    async def test_counts(self, pm):
        await pm.add_to_pool("pe-1", "txn-1", "ngn_to_cny",
                             _entry_data(amount="1000000"), 50.0)
        await pm.add_to_pool("pe-2", "txn-2", "ngn_to_cny",
                             _entry_data(amount="2000000"), 60.0)
        await pm.add_to_pool("pe-3", "txn-3", "cny_to_ngn",
                             _entry_data(direction="cny_to_ngn", amount="50000"), 30.0)

        stats = await pm.get_pool_stats()
        assert stats["ngn_to_cny_count"] == 2
        assert stats["cny_to_ngn_count"] == 1

    @pytest.mark.asyncio
    async def test_volumes(self, pm):
        await pm.add_to_pool("pe-1", "txn-1", "ngn_to_cny",
                             _entry_data(amount="1000000"), 50.0)
        await pm.add_to_pool("pe-2", "txn-2", "ngn_to_cny",
                             _entry_data(amount="2500000"), 60.0)
        await pm.add_to_pool("pe-3", "txn-3", "cny_to_ngn",
                             _entry_data(direction="cny_to_ngn", amount="75000"), 30.0)

        stats = await pm.get_pool_stats()
        assert Decimal(stats["ngn_to_cny_volume"]) == Decimal("3500000")
        assert Decimal(stats["cny_to_ngn_volume"]) == Decimal("75000")

    @pytest.mark.asyncio
    async def test_stats_after_remove(self, pm):
        await pm.add_to_pool("pe-1", "txn-1", "ngn_to_cny",
                             _entry_data(amount="1000000"), 50.0)
        await pm.add_to_pool("pe-2", "txn-2", "ngn_to_cny",
                             _entry_data(amount="2000000"), 60.0)
        await pm.remove_from_pool("pe-1", "ngn_to_cny")

        stats = await pm.get_pool_stats()
        assert stats["ngn_to_cny_count"] == 1
        assert Decimal(stats["ngn_to_cny_volume"]) == Decimal("2000000")

    @pytest.mark.asyncio
    async def test_stats_after_amount_update(self, pm):
        await pm.add_to_pool("pe-1", "txn-1", "ngn_to_cny",
                             _entry_data(amount="1000000"), 50.0)
        await pm.update_entry_amount("pe-1", "400000")

        stats = await pm.get_pool_stats()
        assert stats["ngn_to_cny_count"] == 1
        assert Decimal(stats["ngn_to_cny_volume"]) == Decimal("400000")
