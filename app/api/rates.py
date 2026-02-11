"""
FX rate quote endpoints.

Provides current and historical exchange rates for the NGN/CNY pair.
Rates are cached in Redis with a configurable TTL.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.redis_client import get_redis

router = APIRouter()


@router.get("/ngn-cny")
async def get_ngn_cny_rate(redis=Depends(get_redis)):
    """
    Get the current NGN to CNY exchange rate.

    Returns the mid-market rate, buy rate, and sell rate
    with a timestamp of when the rate was last fetched.
    """
    # TODO: Check Redis cache for current rate
    # TODO: If stale, fetch from rate provider and update cache
    # TODO: Return rate with spread applied
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/ngn-cny/history")
async def get_rate_history(days: int = 7):
    """Get historical NGN/CNY rates for the specified number of days."""
    # TODO: Fetch from database or rate provider
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/quote")
async def get_quote(amount: float, direction: str = "ngn_to_cny"):
    """
    Get a rate quote for a specific amount.

    Includes the locked rate, fees, and estimated settlement amount.
    The quote is valid for a limited time window.
    """
    # TODO: Fetch current rate
    # TODO: Calculate fees based on amount tier
    # TODO: Return quote with expiry timestamp
    raise HTTPException(status_code=501, detail="Not implemented")
