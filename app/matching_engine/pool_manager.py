"""
Redis pool operations for the matching engine.

Manages the sorted sets and hashes in Redis that hold pending transactions
awaiting matching, ordered by priority score.

Data layout:
  Sorted set  — ``matching_pool:{direction}``  score=priority  member=pool_entry_id
  Hash        — ``pool_entry:{id}``            field→value details of the entry
"""

import json
from decimal import Decimal

from app.redis_client import redis
from app.matching_engine.config import POOL_KEY_BUY, POOL_KEY_SELL

# Redis key helpers
POOL_ENTRY_PREFIX = "pool_entry"


def _pool_key(direction: str) -> str:
    return POOL_KEY_BUY if direction == "ngn_to_cny" else POOL_KEY_SELL


def _entry_hash_key(pool_entry_id: str) -> str:
    return f"{POOL_ENTRY_PREFIX}:{pool_entry_id}"


class PoolManager:
    """Manages the Redis-backed transaction matching pools."""

    async def add_to_pool(
        self,
        pool_entry_id: str,
        transaction_id: str,
        direction: str,
        data: dict,
        score: float,
    ):
        """
        Add a transaction to the matching pool.

        1. ZADD matching_pool:{direction} {score} {pool_entry_id}
        2. HSET pool_entry:{pool_entry_id}  field=value ...
        """
        key = _pool_key(direction)
        await redis.zadd(key, {pool_entry_id: score})
        await redis.hset(_entry_hash_key(pool_entry_id), mapping={
            "id": pool_entry_id,
            "transaction_id": transaction_id,
            **{k: str(v) for k, v in data.items()},
        })

    async def remove_from_pool(self, pool_entry_id: str, direction: str):
        """Remove a pool entry by its ID (after match or cancellation)."""
        key = _pool_key(direction)
        await redis.zrem(key, pool_entry_id)
        await redis.delete(_entry_hash_key(pool_entry_id))

    async def get_entry(self, pool_entry_id: str) -> dict | None:
        """Retrieve details for a single pool entry from its hash."""
        data = await redis.hgetall(_entry_hash_key(pool_entry_id))
        return data if data else None

    async def get_buy_pool(self) -> list[dict]:
        """Get all entries in the buy pool (NGN->CNY), ordered by priority."""
        members = await redis.zrevrange(POOL_KEY_BUY, 0, -1, withscores=True)
        entries = []
        for member, score in members:
            data = await redis.hgetall(_entry_hash_key(member))
            if data:
                entries.append({**data, "_score": score})
        return entries

    async def get_sell_pool(self) -> list[dict]:
        """Get all entries in the sell pool (CNY->NGN), ordered by priority."""
        members = await redis.zrevrange(POOL_KEY_SELL, 0, -1, withscores=True)
        entries = []
        for member, score in members:
            data = await redis.hgetall(_entry_hash_key(member))
            if data:
                entries.append({**data, "_score": score})
        return entries

    async def get_pool_stats(self) -> dict:
        """Get summary statistics for both pools."""
        buy_count = await redis.zcard(POOL_KEY_BUY)
        sell_count = await redis.zcard(POOL_KEY_SELL)
        return {
            "buy_count": buy_count,
            "sell_count": sell_count,
            "total": buy_count + sell_count,
        }


pool_manager = PoolManager()
