"""
Redis connection setup using redis-py async client.

Configured for Upstash Redis with optional TLS support.
Provides a shared redis instance for caching and pub/sub.
"""

import redis.asyncio as aioredis

from app.config import settings

redis = aioredis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    ssl=settings.REDIS_SSL,
)


async def get_redis() -> aioredis.Redis:
    """FastAPI dependency that provides the Redis client."""
    return redis
