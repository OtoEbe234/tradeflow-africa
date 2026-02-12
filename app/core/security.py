"""
Core security module — JWT token management and PIN hashing.

Provides the canonical token creation/verification logic used by
both auth endpoints and FastAPI dependencies. Supports RS256 with
HS256 fallback when RSA key files are missing.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt as _bcrypt
import jwt
from fastapi import HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------

_private_key: str | bytes | None = None
_public_key: str | bytes | None = None
_algorithm: str = settings.JWT_ALGORITHM


def _load_keys() -> None:
    """Load RSA keys from disk. Falls back to HS256 with SECRET_KEY."""
    global _private_key, _public_key, _algorithm

    private_path = Path(settings.JWT_PRIVATE_KEY_PATH)
    public_path = Path(settings.JWT_PUBLIC_KEY_PATH)

    if private_path.exists() and public_path.exists():
        _private_key = private_path.read_bytes()
        _public_key = public_path.read_bytes()
        _algorithm = "RS256"
        logger.info("Loaded RSA keys for JWT signing (RS256).")
    else:
        _private_key = settings.SECRET_KEY
        _public_key = settings.SECRET_KEY
        _algorithm = "HS256"
        logger.warning(
            "RSA key files not found. Falling back to HS256. "
            "Run 'python scripts/generate_keys.py' to generate keys.",
        )


_load_keys()


def configure_keys(
    *, private_key: str | bytes, public_key: str | bytes, algorithm: str = "RS256"
) -> None:
    """Override keys at runtime (used in tests)."""
    global _private_key, _public_key, _algorithm
    _private_key = private_key
    _public_key = public_key
    _algorithm = algorithm


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------


def create_access_token(trader_id: str, phone: str) -> str:
    """Create a short-lived access JWT."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(trader_id),
        "phone": phone,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, _private_key, algorithm=_algorithm)


def create_refresh_token(trader_id: str, phone: str) -> str:
    """Create a long-lived refresh JWT."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(trader_id),
        "phone": phone,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, _private_key, algorithm=_algorithm)


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def decode_token(token: str) -> dict:
    """
    Decode and return the JWT payload.

    Raises HTTP 401 on expiry or any other invalid-token error.
    """
    try:
        return jwt.decode(token, _public_key, algorithms=[_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


def verify_token(token: str, expected_type: str) -> dict:
    """Decode a JWT and validate its ``type`` claim."""
    payload = decode_token(token)
    if payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Expected {expected_type} token",
        )
    return payload


# ---------------------------------------------------------------------------
# PIN helpers
# ---------------------------------------------------------------------------


def hash_pin(plain_pin: str) -> str:
    """Hash a PIN using bcrypt with 12 rounds."""
    hashed = _bcrypt.hashpw(plain_pin.encode(), _bcrypt.gensalt(rounds=12))
    return hashed.decode()


def verify_pin(plain_pin: str, pin_hash: str) -> bool:
    """Verify a plain PIN against its bcrypt hash."""
    return _bcrypt.checkpw(plain_pin.encode(), pin_hash.encode())
