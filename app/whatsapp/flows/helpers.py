"""
Shared helpers for WhatsApp conversation flows.

Provides database lookups, language persistence, input validators,
and display formatters used across registration, payment, status,
and menu flows.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.trader import Trader
from app.models.transaction import Transaction
from app.redis_client import redis

# ---------------------------------------------------------------------------
# Language persistence (Redis, no expiry)
# ---------------------------------------------------------------------------

LANG_KEY_PREFIX = "wa:lang:"


async def get_user_lang(phone: str) -> str:
    """Read the user's language preference from Redis, default ``'en'``."""
    val = await redis.get(f"{LANG_KEY_PREFIX}{phone}")
    return val if val else "en"


async def set_user_lang(phone: str, lang: str) -> None:
    """Persist language preference to Redis (no expiry)."""
    await redis.set(f"{LANG_KEY_PREFIX}{phone}", lang)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


async def get_trader_by_phone(phone: str) -> Trader | None:
    """Look up a trader by phone number. Returns ``None`` if not found."""
    async with async_session() as session:
        result = await session.execute(
            select(Trader).where(Trader.phone == phone)
        )
        return result.scalar_one_or_none()


async def get_trader_transactions(phone: str, limit: int = 5) -> list[Transaction]:
    """Fetch a trader's most recent transactions, ordered newest-first."""
    async with async_session() as session:
        result = await session.execute(
            select(Trader).where(Trader.phone == phone)
        )
        trader = result.scalar_one_or_none()
        if trader is None:
            return []

        txn_result = await session.execute(
            select(Transaction)
            .where(Transaction.trader_id == trader.id)
            .order_by(Transaction.created_at.desc())
            .limit(limit)
        )
        return list(txn_result.scalars().all())


# ---------------------------------------------------------------------------
# Input validators
# ---------------------------------------------------------------------------

_SEQUENTIAL_PINS = {
    "0123", "1234", "2345", "3456", "4567", "5678", "6789",
    "9876", "8765", "7654", "6543", "5432", "4321", "3210",
}


def is_weak_pin(pin: str) -> bool:
    """Return True if the PIN is sequential or all-same digits."""
    if len(set(pin)) == 1:
        return True
    if pin in _SEQUENTIAL_PINS:
        return True
    return False


def validate_bvn_format(text: str) -> bool:
    """Return True if *text* looks like a valid 11-digit BVN."""
    return bool(re.match(r"^\d{11}$", text.strip()))


def validate_account_number(text: str) -> bool:
    """Return True if *text* is a 10–20 digit account number."""
    return bool(re.match(r"^\d{10,20}$", text.strip()))


def validate_pin_format(text: str) -> bool:
    """Return True if *text* is exactly 4 digits."""
    return bool(re.match(r"^\d{4}$", text.strip()))


# ---------------------------------------------------------------------------
# Display formatters
# ---------------------------------------------------------------------------


def format_direction(direction: str) -> str:
    """Human-readable direction label."""
    return {
        "ngn_to_cny": "NGN → CNY (Pay Chinese Supplier)",
        "cny_to_ngn": "CNY → NGN (Receive from Nigerian Buyer)",
    }.get(direction, direction)


def format_status(status_value: str) -> str:
    """Human-readable transaction status."""
    return {
        "initiated": "Initiated — awaiting deposit",
        "funded": "Funded — entering matching pool",
        "matching": "Matching — looking for counterparty",
        "matched": "Matched — pending settlement",
        "partial_matched": "Partially Matched",
        "pending_settlement": "Pending Settlement",
        "settling": "Settling — funds in transit",
        "completed": "Completed ✓",
        "failed": "Failed",
        "refunded": "Refunded",
        "cancelled": "Cancelled",
        "expired": "Expired",
    }.get(status_value, status_value)
