"""
FX Rate Engine — rate fetching, caching, fee tiers, and circuit breaker.

Supports NGN/CNY cross-rate calculation from NGN/USD and CNY/USD base rates.
Uses exchangerate-api.com (free tier) or mock data for development/testing.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fee tiers: (min_volume_usd, fee_percent, tier_name)
# Ordered highest-first so the first match wins.
FEE_TIERS = [
    (Decimal("500000"), Decimal("0.75"), "platinum"),
    (Decimal("200000"), Decimal("1.00"), "gold"),
    (Decimal("50000"),  Decimal("1.50"), "silver"),
    (Decimal("0"),      Decimal("2.00"), "standard"),
]

MIN_FEE_NGN = Decimal("5000")

# Typical Nigerian bank spread for savings comparison
BANK_SPREAD_PERCENT = Decimal("5.0")

# Circuit breaker
CIRCUIT_BREAKER_THRESHOLD = Decimal("3.0")   # percent movement
CIRCUIT_BREAKER_WINDOW = 3600                # 1 hour in seconds
CIRCUIT_BREAKER_COOLDOWN = 900               # 15 minutes pause

# Redis keys
RATE_CACHE_KEY = "fx_rates:USD"
RATE_HISTORY_KEY = "rate_history:NGN_CNY"
CIRCUIT_BREAKER_KEY = "circuit_breaker:rates"
QUOTE_KEY_PREFIX = "quote:"

# Mock rates (deterministic for testing)
MOCK_NGN_PER_USD = Decimal("1550.00")
MOCK_CNY_PER_USD = Decimal("7.25")


# ---------------------------------------------------------------------------
# Rate provider protocol
# ---------------------------------------------------------------------------


class RateProvider(Protocol):
    async def fetch_rates(self) -> dict[str, Decimal]:
        """Fetch {NGN: rate, CNY: rate} per 1 USD."""
        ...


class MockRateProvider:
    """Deterministic rates for dev/testing."""

    async def fetch_rates(self) -> dict[str, Decimal]:
        return {
            "NGN": MOCK_NGN_PER_USD,
            "CNY": MOCK_CNY_PER_USD,
        }


class ExchangeRateAPIProvider:
    """Fetch live rates from exchangerate-api.com (free tier)."""

    API_URL = "https://open.er-api.com/v6/latest/USD"

    async def fetch_rates(self) -> dict[str, Decimal]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(self.API_URL)
            resp.raise_for_status()
            data = resp.json()

        if data.get("result") != "success":
            raise RuntimeError(f"Rate API error: {data}")

        rates = data["rates"]
        return {
            "NGN": Decimal(str(rates["NGN"])),
            "CNY": Decimal(str(rates["CNY"])),
        }


# Module-level provider override (for tests)
_provider: RateProvider | None = None


def get_rate_provider() -> RateProvider:
    """Return the configured rate provider."""
    global _provider
    if _provider is not None:
        return _provider
    if settings.FX_RATE_MOCK:
        return MockRateProvider()
    return ExchangeRateAPIProvider()


def set_rate_provider(provider: RateProvider | None) -> None:
    """Override the rate provider (for testing)."""
    global _provider
    _provider = provider


# ---------------------------------------------------------------------------
# Circuit breaker exception
# ---------------------------------------------------------------------------


class CircuitBreakerOpenError(Exception):
    """Raised when rate quotes are paused due to circuit breaker."""
    pass


# ---------------------------------------------------------------------------
# RateService
# ---------------------------------------------------------------------------


class RateService:
    """FX rate engine with caching, fee tiers, and circuit breaker."""

    def __init__(self, redis):
        self.redis = redis

    # --- Rate fetching with cache ---

    async def get_rates(self) -> dict:
        """
        Get current FX rates (from cache or fresh fetch).

        Returns dict with ngn_per_usd, cny_per_usd, ngn_per_cny,
        timestamp, source — all as strings for JSON serialization.
        """
        cached = await self.redis.get(RATE_CACHE_KEY)
        if cached is not None:
            return json.loads(cached)

        # Fetch fresh rates
        provider = get_rate_provider()
        raw = await provider.fetch_rates()

        ngn_per_usd = raw["NGN"]
        cny_per_usd = raw["CNY"]
        ngn_per_cny = (ngn_per_usd / cny_per_usd).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

        now = datetime.now(timezone.utc)
        source = "mock" if settings.FX_RATE_MOCK else "exchangerate-api"

        rates = {
            "ngn_per_usd": str(ngn_per_usd),
            "cny_per_usd": str(cny_per_usd),
            "ngn_per_cny": str(ngn_per_cny),
            "timestamp": now.isoformat(),
            "source": source,
        }

        # Cache in Redis
        await self.redis.setex(
            RATE_CACHE_KEY,
            settings.FX_RATE_CACHE_TTL_SECONDS,
            json.dumps(rates),
        )

        # Update rate history for circuit breaker
        await self._record_rate(ngn_per_cny, now)

        return rates

    # --- Fee tier calculation ---

    @staticmethod
    def get_fee_tier(monthly_volume_usd: Decimal) -> tuple[str, Decimal]:
        """
        Determine fee tier based on monthly USD volume.

        Returns (tier_name, fee_percentage).

        Tiers:
            $0-$50K     -> 2.00% (standard)
            $50K-$200K  -> 1.50% (silver)
            $200K-$500K -> 1.00% (gold)
            $500K+      -> 0.75% (platinum)
        """
        for threshold, pct, name in FEE_TIERS:
            if monthly_volume_usd >= threshold:
                return name, pct
        return "standard", Decimal("2.00")

    # --- Quote generation ---

    async def generate_quote(
        self,
        source_currency: str,
        target_currency: str,
        source_amount: Decimal,
        monthly_volume_usd: Decimal = Decimal("0"),
    ) -> dict:
        """
        Generate a rate quote with fee breakdown.

        Fee is charged on top of source_amount (total_cost = source + fee).
        Target amount is calculated at mid-market rate.
        Quote is stored in Redis with a 60-second TTL.
        """
        # Check circuit breaker
        if await self.is_circuit_breaker_open():
            raise CircuitBreakerOpenError(
                "Rate quotes paused due to unusual market movement. "
                "Try again shortly."
            )

        rates = await self.get_rates()
        ngn_per_cny = Decimal(rates["ngn_per_cny"])

        # Determine fee tier
        tier_name, fee_pct = self.get_fee_tier(monthly_volume_usd)

        source = source_currency.upper()
        target = target_currency.upper()

        if source == "NGN" and target == "CNY":
            mid_market_rate = ngn_per_cny  # NGN per 1 CNY

            # Fee in NGN
            fee_amount = (source_amount * fee_pct / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if fee_amount < MIN_FEE_NGN:
                fee_amount = MIN_FEE_NGN

            total_cost = source_amount + fee_amount
            target_amount = (source_amount / mid_market_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            tradeflow_rate = (total_cost / target_amount).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            ) if target_amount > 0 else Decimal("0")

        elif source == "CNY" and target == "NGN":
            mid_market_rate = ngn_per_cny

            # Fee in CNY — convert min fee to CNY
            min_fee_cny = (MIN_FEE_NGN / ngn_per_cny).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            fee_amount = (source_amount * fee_pct / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if fee_amount < min_fee_cny:
                fee_amount = min_fee_cny

            total_cost = source_amount + fee_amount
            target_amount = (source_amount * mid_market_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            tradeflow_rate = (target_amount / total_cost).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            ) if total_cost > 0 else Decimal("0")

        else:
            raise ValueError(f"Unsupported currency pair: {source}/{target}")

        # Savings vs bank (bank charges ~5% spread)
        bank_fee = (source_amount * BANK_SPREAD_PERCENT / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        savings_vs_bank = (bank_fee - fee_amount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if savings_vs_bank < 0:
            savings_vs_bank = Decimal("0")

        # Generate quote ID and expiry
        quote_id = f"QT-{uuid.uuid4().hex[:12].upper()}"
        now = datetime.now(timezone.utc)
        quote_valid_until = now + timedelta(seconds=settings.FX_QUOTE_TTL_SECONDS)

        quote = {
            "quote_id": quote_id,
            "source_currency": source,
            "target_currency": target,
            "source_amount": str(source_amount),
            "target_amount": str(target_amount),
            "mid_market_rate": str(mid_market_rate),
            "tradeflow_rate": str(tradeflow_rate),
            "fee_tier": tier_name,
            "fee_percentage": str(fee_pct),
            "fee_amount": str(fee_amount),
            "total_cost": str(total_cost),
            "savings_vs_bank": str(savings_vs_bank),
            "quote_valid_until": quote_valid_until.isoformat(),
        }

        # Store quote in Redis with TTL
        await self.redis.setex(
            f"{QUOTE_KEY_PREFIX}{quote_id}",
            settings.FX_QUOTE_TTL_SECONDS,
            json.dumps(quote),
        )

        return quote

    # --- Circuit breaker ---

    async def _record_rate(self, rate: Decimal, timestamp: datetime) -> None:
        """Add rate to history sorted set and check circuit breaker."""
        ts = timestamp.timestamp()
        await self.redis.zadd(RATE_HISTORY_KEY, {str(rate): ts})

        # Trim entries older than the window
        cutoff = ts - CIRCUIT_BREAKER_WINDOW
        await self.redis.zremrangebyscore(RATE_HISTORY_KEY, "-inf", cutoff)

        # Check for excessive movement
        await self._check_circuit_breaker()

    async def _check_circuit_breaker(self) -> None:
        """If rate moved >3% within the window, trip the circuit breaker."""
        entries = await self.redis.zrange(RATE_HISTORY_KEY, 0, -1)
        if len(entries) < 2:
            return

        rates = [Decimal(r) for r in entries]
        min_rate = min(rates)
        max_rate = max(rates)

        if min_rate <= 0:
            return

        movement = (max_rate - min_rate) / min_rate * Decimal("100")

        if movement > CIRCUIT_BREAKER_THRESHOLD:
            logger.warning(
                "Circuit breaker tripped! Rate movement: %.2f%% "
                "(min=%.4f, max=%.4f) in last hour",
                movement, min_rate, max_rate,
            )
            await self.redis.setex(
                CIRCUIT_BREAKER_KEY,
                CIRCUIT_BREAKER_COOLDOWN,
                json.dumps({
                    "reason": f"Rate moved {movement:.2f}% in 1 hour",
                    "min_rate": str(min_rate),
                    "max_rate": str(max_rate),
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                }),
            )

    async def is_circuit_breaker_open(self) -> bool:
        """Check if the circuit breaker is currently tripped."""
        return await self.redis.get(CIRCUIT_BREAKER_KEY) is not None
