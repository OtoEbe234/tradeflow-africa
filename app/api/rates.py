"""
FX rate quote endpoints.

Provides current exchange rates and authenticated rate quotes for
the NGN/CNY pair. Rates are cached in Redis with configurable TTL.
"""

import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_trader
from app.models.trader import Trader
from app.redis_client import get_redis
from app.schemas.rate import RateData, RateQuoteResponse
from app.services.rate_service import CircuitBreakerOpenError, RateService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/quote", response_model=RateQuoteResponse)
async def get_quote(
    source: str = Query(
        ..., description="Source currency (NGN or CNY)", examples=["NGN"],
    ),
    target: str = Query(
        ..., description="Target currency (CNY or NGN)", examples=["CNY"],
    ),
    amount: Decimal = Query(
        ..., gt=0, description="Amount in source currency", examples=[50000000],
    ),
    trader: Trader = Depends(get_current_trader),
    redis=Depends(get_redis),
):
    """
    Get a rate quote for a specific amount.

    Returns the mid-market rate, TradeFlow rate (with spread), fee breakdown,
    and savings compared to typical bank rates. Quote is valid for 60 seconds
    and stored in Redis with key ``quote:{quote_id}``.

    Requires authentication — the trader's monthly volume determines the fee tier.
    """
    source = source.upper()
    target = target.upper()

    if (source, target) not in (("NGN", "CNY"), ("CNY", "NGN")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only NGN/CNY and CNY/NGN pairs are supported.",
        )

    svc = RateService(redis)

    # Convert trader's monthly_used (NGN) to USD for fee tier
    rates = await svc.get_rates()
    ngn_per_usd = Decimal(rates["ngn_per_usd"])
    monthly_volume_usd = (
        (trader.monthly_used / ngn_per_usd).quantize(Decimal("0.01"))
        if ngn_per_usd > 0
        else Decimal("0")
    )

    try:
        quote = await svc.generate_quote(source, target, amount, monthly_volume_usd)
    except CircuitBreakerOpenError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return RateQuoteResponse(**quote)


@router.get("/current", response_model=RateData)
async def get_current_rates(redis=Depends(get_redis)):
    """
    Get the current NGN/CNY exchange rate.

    Returns the mid-market cross-rate with rate source and timestamp.
    No authentication required — public endpoint.
    """
    svc = RateService(redis)

    if await svc.is_circuit_breaker_open():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate service temporarily paused due to unusual market movement.",
        )

    rates = await svc.get_rates()
    return RateData(
        ngn_per_usd=Decimal(rates["ngn_per_usd"]),
        cny_per_usd=Decimal(rates["cny_per_usd"]),
        ngn_per_cny=Decimal(rates["ngn_per_cny"]),
        timestamp=rates["timestamp"],
        source=rates["source"],
    )
