"""
Authentication service — OTP generation, rate limiting, and token helpers.

JWT token creation/verification is delegated to ``app.core.security``.
This module re-exports those functions for backward compatibility and
adds OTP-specific logic (Redis storage, rate limiting, attempt tracking).
"""

import logging
import random
import string

from app.config import settings

# Re-export JWT functions from core.security so existing callers work unchanged
from app.core.security import (  # noqa: F401
    configure_keys,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_token,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OTP generation / verification
# ---------------------------------------------------------------------------


async def generate_otp(phone: str, redis) -> str:
    """Generate a random OTP, store in Redis with TTL, and return it."""
    otp = "".join(random.choices(string.digits, k=settings.OTP_LENGTH))
    key = f"otp:{phone}"
    await redis.setex(key, settings.OTP_EXPIRE_SECONDS, otp)
    return otp


async def verify_otp(phone: str, otp: str, redis) -> bool:
    """Verify OTP against Redis. Deletes the key on success."""
    key = f"otp:{phone}"
    stored = await redis.get(key)
    if stored is None or stored != otp:
        return False
    await redis.delete(key)
    return True


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


async def check_otp_rate_limit(phone: str, redis) -> bool:
    """
    Enforce max 3 OTP requests per hour per phone.

    Returns True if within limit, False if exceeded.
    """
    key = f"otp_limit:{phone}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 3600)
    return count <= 3


# ---------------------------------------------------------------------------
# OTP attempt tracking
# ---------------------------------------------------------------------------

OTP_MAX_ATTEMPTS = 3
OTP_LOCKOUT_SECONDS = 30 * 60  # 30 minutes


async def check_otp_locked(phone: str, redis) -> bool:
    """Return True if the phone is locked out after too many failed attempts."""
    lock_key = f"otp_lock:{phone}"
    locked = await redis.get(lock_key)
    return locked is not None


async def record_failed_attempt(phone: str, redis) -> int:
    """
    Increment the failed-attempt counter.

    If it reaches OTP_MAX_ATTEMPTS, set a 30-minute lockout.
    Returns the current attempt count.
    """
    key = f"otp_attempts:{phone}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, OTP_LOCKOUT_SECONDS)
    if count >= OTP_MAX_ATTEMPTS:
        lock_key = f"otp_lock:{phone}"
        await redis.setex(lock_key, OTP_LOCKOUT_SECONDS, "1")
    return count


async def clear_attempts(phone: str, redis) -> None:
    """Clear failed-attempt counter and lock on successful verification."""
    await redis.delete(f"otp_attempts:{phone}")
    await redis.delete(f"otp_lock:{phone}")
