"""
FX rate service — fetches and caches NGN/CNY exchange rates.

Rates are cached in Redis with a configurable TTL to minimize
API calls while keeping quotes reasonably fresh.
"""

import json
from decimal import Decimal
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.redis_client import redis


class RateService:
    """Fetches, caches, and serves NGN/CNY exchange rates."""

    CACHE_KEY = "fx:ngn_cny"
    SPREAD_BPS = 50  # 0.5% spread applied to mid-market rate

    async def get_rate(self) -> dict:
        """
        Get the current NGN/CNY rate, serving from cache if available.

        Returns dict with mid, buy, sell rates and timestamp.
        """
        cached = await redis.get(self.CACHE_KEY)
        if cached:
            return json.loads(cached)

        rate_data = await self._fetch_from_provider()
        await redis.setex(
            self.CACHE_KEY,
            settings.FX_RATE_CACHE_TTL_SECONDS,
            json.dumps(rate_data),
        )
        return rate_data

    async def _fetch_from_provider(self) -> dict:
        """Fetch the latest rate from the configured FX rate provider."""
        # TODO: Call exchangerate-api or similar provider
        # TODO: Calculate NGN/CNY cross rate if not directly available
        mid_rate = Decimal("0.0046")  # Placeholder: 1 NGN ≈ 0.0046 CNY
        spread = mid_rate * self.SPREAD_BPS / Decimal("10000")

        return {
            "pair": "NGN/CNY",
            "mid": str(mid_rate),
            "buy": str(mid_rate - spread),
            "sell": str(mid_rate + spread),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    async def get_quote(self, amount: Decimal, direction: str) -> dict:
        """
        Generate a rate quote for a specific amount and direction.

        Applies tiered fees based on transaction size.
        """
        rate_data = await self.get_rate()
        # TODO: Apply tiered fee schedule
        # TODO: Calculate target amount
        # TODO: Set quote expiry (e.g., 5 minutes)
        return {
            "source_amount": str(amount),
            "direction": direction,
            "rate": rate_data,
            "fee_percent": "1.0",
            "quote_expires_at": None,
        }


rate_service = RateService()
