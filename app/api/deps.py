"""
Reusable FastAPI dependencies for authentication and authorization.

Dependencies:
  - get_current_trader  — extracts trader from JWT (401 if invalid)
  - require_active      — alias that also rejects suspended/blocked
  - require_tier        — factory that enforces a minimum KYC tier (403)
  - require_pin         — validates PIN from a request body field
"""

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_token, verify_pin as _verify_pin
from app.database import get_db
from app.models.trader import Trader, TraderStatus


# ---------------------------------------------------------------------------
# Core: extract trader from JWT
# ---------------------------------------------------------------------------


async def get_current_trader(
    authorization: str = Header(..., description="Bearer <access_token>"),
    db: AsyncSession = Depends(get_db),
) -> Trader:
    """
    Parse the ``Authorization: Bearer <token>`` header, verify the JWT,
    look up the Trader in the database, and return it.

    Raises 401 if the token is missing, malformed, expired, or the trader
    is not found / deactivated.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header",
        )

    token = authorization[len("Bearer "):]
    payload = verify_token(token, expected_type="access")
    trader_id = payload.get("sub")

    result = await db.execute(select(Trader).where(Trader.id == trader_id))
    trader = result.scalar_one_or_none()

    if trader is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Trader not found",
        )

    if trader.status in (TraderStatus.SUSPENDED, TraderStatus.BLOCKED):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is deactivated",
        )

    return trader


# Convenience alias
require_auth = get_current_trader


# ---------------------------------------------------------------------------
# Tier guard
# ---------------------------------------------------------------------------


def require_tier(min_tier: int):
    """
    Factory that returns a dependency which enforces ``trader.kyc_tier >= min_tier``.

    Usage::

        @router.post("/large-transfer")
        async def large_transfer(
            trader: Trader = Depends(require_tier(2)),
        ):
            ...
    """

    async def _check(trader: Trader = Depends(get_current_trader)) -> Trader:
        if trader.kyc_tier < min_tier:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires KYC tier {min_tier} or higher. "
                       f"Your current tier is {trader.kyc_tier}.",
            )
        return trader

    return _check


# ---------------------------------------------------------------------------
# PIN verification
# ---------------------------------------------------------------------------


async def require_pin(
    pin: str,
    trader: Trader = Depends(get_current_trader),
) -> Trader:
    """
    Verify the transaction PIN supplied in the request body.

    Intended to be called explicitly from route handlers, not as a
    ``Depends()`` because the PIN comes from the request body (not a header).

    Usage in a route handler::

        trader = await require_pin(payload.pin, trader)
    """
    if trader.pin_hash is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PIN has not been set. Complete registration first.",
        )

    if not _verify_pin(pin, trader.pin_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid PIN",
        )

    return trader
