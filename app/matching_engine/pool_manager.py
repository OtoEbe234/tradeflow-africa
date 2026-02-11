"""
Redis pool operations for the matching engine.

Manages the sorted sets in Redis that hold pending transactions
awaiting matching, ordered by priority score.
"""

import json
from decimal import Decimal

from app.redis_client import redis
from app.matching_engine.config import POOL_KEY_BUY, POOL_KEY_SELL


class PoolManager:
    """Manages the Redis-backed transaction matching pools."""

    async def add_to_pool(self, transaction_id: str, direction: str, data: dict, score: float):
        """Add a transaction to the appropriate matching pool sorted set."""
        key = POOL_KEY_BUY if direction == "ngn_to_cny" else POOL_KEY_SELL
        await redis.zadd(key, {json.dumps({**data, "id": transaction_id}): score})

    async def remove_from_pool(self, transaction_id: str, direction: str):
        """Remove a transaction from the matching pool (after match or cancellation)."""
        key = POOL_KEY_BUY if direction == "ngn_to_cny" else POOL_KEY_SELL
        # Scan members to find and remove by transaction_id
        members = await redis.zrange(key, 0, -1)
        for member in members:
            entry = json.loads(member)
            if entry.get("id") == transaction_id:
                await redis.zrem(key, member)
                return True
        return False

    async def get_buy_pool(self) -> list[dict]:
        """Get all entries in the buy pool (NGN->CNY), ordered by priority."""
        members = await redis.zrevrange(POOL_KEY_BUY, 0, -1, withscores=True)
        return [
            {**json.loads(member), "_score": score}
            for member, score in members
        ]

    async def get_sell_pool(self) -> list[dict]:
        """Get all entries in the sell pool (CNY->NGN), ordered by priority."""
        members = await redis.zrevrange(POOL_KEY_SELL, 0, -1, withscores=True)
        return [
            {**json.loads(member), "_score": score}
            for member, score in members
        ]

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
