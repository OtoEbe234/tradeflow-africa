"""
Redis pool operations for the matching engine.

Manages the sorted sets and hashes in Redis that hold pending transactions
awaiting matching, ordered by priority score.

Data layout:
  Sorted set  — ``pool:{direction}``       score=priority  member=pool_entry_id
  Hash        — ``pool_entry:{id}``         field→value details of the entry
  Lock        — ``pool:lock``               distributed lock (5-min auto-expiry)

All multi-key writes use Redis pipelines for atomicity.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from app.matching_engine.config import POOL_KEY_BUY, POOL_KEY_SELL, POOL_LOCK_KEY

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Redis key helpers ──────────────────────────────────────────────────────────

POOL_ENTRY_PREFIX = "pool_entry"
LOCK_TIMEOUT_SECONDS = 300  # 5-minute auto-expiry


def _pool_key(direction: str) -> str:
    """Return the sorted-set key for a given trade direction."""
    return POOL_KEY_BUY if direction == "ngn_to_cny" else POOL_KEY_SELL


def _entry_hash_key(pool_entry_id: str) -> str:
    """Return the hash key for a pool entry's detail record."""
    return f"{POOL_ENTRY_PREFIX}:{pool_entry_id}"


# PoolManager ────────────────────────────────────────────────────────────────


class PoolManager:
    """
    Manages the Redis-backed transaction matching pools.

    Accepts a ``redis`` client on construction so callers (and tests)
    can inject their own connection.  Falls back to the module-level
    singleton from ``app.redis_client`` when no client is supplied.
    """

    def __init__(self, redis_client: "aioredis.Redis | None" = None):
        self._redis = redis_client

    @property
    def redis(self) -> "aioredis.Redis":
        if self._redis is not None:
            return self._redis
        # Lazy import to avoid circular deps at module load time
        from app.redis_client import redis as _default
        return _default

    # ── add / remove ────────────────────────────────────────────────────

    async def add_to_pool(
        self,
        pool_entry_id: str,
        transaction_id: str,
        direction: str,
        data: dict,
        score: float,
    ) -> None:
        """
        Add a transaction to the matching pool (pipeline — atomic).

        1. ZADD pool:{direction}  {score}  {pool_entry_id}
        2. HSET  pool_entry:{id}  field=value …
        """
        key = _pool_key(direction)
        hash_key = _entry_hash_key(pool_entry_id)

        mapping = {
            "id": pool_entry_id,
            "transaction_id": transaction_id,
            **{k: str(v) for k, v in data.items()},
        }

        pipe = self.redis.pipeline(transaction=True)
        pipe.zadd(key, {pool_entry_id: score})
        pipe.hset(hash_key, mapping=mapping)
        await pipe.execute()

    async def remove_from_pool(self, pool_entry_id: str, direction: str) -> None:
        """Remove a pool entry by its ID (pipeline — atomic)."""
        key = _pool_key(direction)
        hash_key = _entry_hash_key(pool_entry_id)

        pipe = self.redis.pipeline(transaction=True)
        pipe.zrem(key, pool_entry_id)
        pipe.delete(hash_key)
        await pipe.execute()

    # ── query ───────────────────────────────────────────────────────────

    async def get_entry(self, pool_entry_id: str) -> dict | None:
        """Retrieve details for a single pool entry from its hash."""
        data = await self.redis.hgetall(_entry_hash_key(pool_entry_id))
        return data if data else None

    async def get_pool_snapshot(self, direction: str) -> list[dict]:
        """
        Return all ACTIVE entries for *direction*, highest priority first.

        Uses ZREVRANGE to read the sorted set, then a pipeline of
        HGETALL calls to bulk-fetch every entry's detail hash.
        """
        key = _pool_key(direction)
        members = await self.redis.zrevrange(key, 0, -1, withscores=True)
        if not members:
            return []

        # Pipeline: fetch all hashes in one round-trip
        pipe = self.redis.pipeline(transaction=False)
        for member_id, _score in members:
            pipe.hgetall(_entry_hash_key(member_id))
        results = await pipe.execute()

        entries = []
        for (member_id, score), hash_data in zip(members, results):
            if hash_data:
                entries.append({**hash_data, "_score": score})
        return entries

    # ── update ──────────────────────────────────────────────────────────

    async def update_entry_amount(
        self,
        pool_entry_id: str,
        new_amount: Decimal | float | str,
    ) -> None:
        """
        Update the available amount on a pool entry after a partial match.

        Only mutates the hash — the sorted-set score (priority) is unchanged.
        """
        hash_key = _entry_hash_key(pool_entry_id)
        await self.redis.hset(hash_key, "source_amount", str(new_amount))

    # ── distributed lock ────────────────────────────────────────────────

    async def acquire_lock(self) -> "aioredis.lock.Lock | None":
        """
        Acquire a distributed lock on the matching pool.

        Uses the redis-py ``Lock`` implementation which is based on the
        Redlock single-instance algorithm (SET NX PX + Lua-based release).
        Auto-expires after ``LOCK_TIMEOUT_SECONDS`` (5 min).

        Returns the Lock object on success, or ``None`` if the lock is
        already held by another process.
        """
        lock = self.redis.lock(
            POOL_LOCK_KEY,
            timeout=LOCK_TIMEOUT_SECONDS,
            blocking=False,
        )
        acquired = await lock.acquire()
        if acquired:
            return lock
        return None

    async def release_lock(self, lock: "aioredis.lock.Lock") -> None:
        """Release a previously acquired distributed lock."""
        try:
            await lock.release()
        except Exception:
            # Lock may have already expired — log but don't raise
            logger.warning("Lock release failed (may have auto-expired)")

    # ── stats ───────────────────────────────────────────────────────────

    async def get_pool_stats(self) -> dict:
        """
        Return summary statistics for both pools.

        Uses a pipeline for a single round-trip.  Volume is the sum
        of ``source_amount`` across all entries in each pool.
        """
        pipe = self.redis.pipeline(transaction=False)
        pipe.zcard(POOL_KEY_BUY)
        pipe.zcard(POOL_KEY_SELL)
        pipe.zrange(POOL_KEY_BUY, 0, -1)
        pipe.zrange(POOL_KEY_SELL, 0, -1)
        buy_count, sell_count, buy_ids, sell_ids = await pipe.execute()

        # Fetch source_amount from each entry's hash via pipeline
        async def _sum_volume(entry_ids: list[str]) -> Decimal:
            if not entry_ids:
                return Decimal("0")
            vol_pipe = self.redis.pipeline(transaction=False)
            for eid in entry_ids:
                vol_pipe.hget(_entry_hash_key(eid), "source_amount")
            amounts = await vol_pipe.execute()
            total = Decimal("0")
            for amt in amounts:
                if amt:
                    total += Decimal(amt)
            return total

        buy_volume = await _sum_volume(buy_ids)
        sell_volume = await _sum_volume(sell_ids)

        return {
            "ngn_to_cny_count": buy_count,
            "ngn_to_cny_volume": str(buy_volume),
            "cny_to_ngn_count": sell_count,
            "cny_to_ngn_volume": str(sell_volume),
        }

    # ── backward-compat aliases ─────────────────────────────────────────

    async def get_buy_pool(self) -> list[dict]:
        """Alias: ``get_pool_snapshot("ngn_to_cny")``."""
        return await self.get_pool_snapshot("ngn_to_cny")

    async def get_sell_pool(self) -> list[dict]:
        """Alias: ``get_pool_snapshot("cny_to_ngn")``."""
        return await self.get_pool_snapshot("cny_to_ngn")


# Module-level singleton (uses the default redis client)
pool_manager = PoolManager()
