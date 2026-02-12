"""
Authentication endpoints — registration, OTP verification, and token management.

Registration flow:
  1. POST /register   — validate phone, send OTP
  2. POST /verify-otp — verify OTP, unlock next step (BVN verification)
  3. POST /verify-bvn — verify BVN, create trader account (Tier 1)
  4. POST /set-pin    — set PIN, activate account, return JWT tokens

Login flow:
  5. POST /otp/request — request OTP for existing trader
  6. POST /otp/verify  — verify OTP, receive JWT token pair

Token management:
  7. POST /token/refresh — exchange refresh token for new pair
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.trader import Trader, TraderStatus
from app.redis_client import get_redis
from app.core.security import verify_pin
from app.schemas.trader import (
    LoginRequest,
    LoginResponse,
    OTPRequest,
    OTPVerify,
    RefreshTokenRequest,
    RefreshTokenResponse,
    RegisterRequest,
    RegisterResponse,
    SetPinRequest,
    SetPinResponse,
    TokenResponse,
    TraderRead,
    VerifyBVNRequest,
    VerifyBVNResponse,
    VerifyOTPRequest,
    VerifyOTPResponse,
)
from app.services import auth_service
from app.services.kyc_service import get_bvn_provider

logger = logging.getLogger(__name__)

router = APIRouter()

# Nigerian phone: +234 followed by exactly 10 digits
_NIGERIAN_PHONE_RE = re.compile(r"^\+234[0-9]{10}$")


# ---------------------------------------------------------------------------
# Registration flow
# ---------------------------------------------------------------------------


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_200_OK)
async def register(
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """
    Start registration — validate Nigerian phone and send OTP.

    1. Validate phone format (+234 + 10 digits)
    2. Check phone not already registered
    3. Rate-limit OTP requests (max 3 per phone per hour)
    4. Generate 6-digit OTP and store in Redis (5-min expiry)
    5. Log OTP to console (SMS integration placeholder)
    6. Return success response
    """
    # 1. Format validation (also enforced by Pydantic, but belt-and-suspenders)
    if not _NIGERIAN_PHONE_RE.match(payload.phone):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid phone format. Expected +234XXXXXXXXXX",
        )

    # 2. Duplicate check
    result = await db.execute(select(Trader).where(Trader.phone == payload.phone))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Phone number already registered",
        )

    # 3. Rate limiting
    within_limit = await auth_service.check_otp_rate_limit(payload.phone, redis)
    if not within_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Try again later.",
        )

    # 4. Generate and store OTP
    otp = await auth_service.generate_otp(payload.phone, redis)

    # 5. Send OTP (log for development, SMS integration later)
    logger.info("OTP for %s: %s", payload.phone, otp)

    # 6. Success
    return RegisterResponse(success=True, message="OTP sent")


@router.post("/verify-otp", response_model=VerifyOTPResponse, status_code=status.HTTP_200_OK)
async def verify_otp_registration(
    payload: VerifyOTPRequest,
    redis=Depends(get_redis),
):
    """
    Verify OTP during registration.

    - Max 3 attempts per phone; on 3rd failure, lock for 30 minutes.
    - On success, clear attempt counter and return next step.
    """
    # Check lockout
    if await auth_service.check_otp_locked(payload.phone, redis):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again in 30 minutes.",
        )

    # Verify OTP
    valid = await auth_service.verify_otp(payload.phone, payload.otp, redis)
    if not valid:
        attempts = await auth_service.record_failed_attempt(payload.phone, redis)
        remaining = auth_service.OTP_MAX_ATTEMPTS - attempts
        if remaining <= 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed attempts. Try again in 30 minutes.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired OTP",
        )

    # OTP valid — clear attempt counter
    await auth_service.clear_attempts(payload.phone, redis)

    return VerifyOTPResponse(success=True, next_step="bvn_verification")


# ---------------------------------------------------------------------------
# BVN verification + account creation
# ---------------------------------------------------------------------------

# PINs that are too easy to guess
_SEQUENTIAL_PINS = {"0123", "1234", "2345", "3456", "4567", "5678", "6789",
                    "9876", "8765", "7654", "6543", "5432", "4321", "3210"}


def _is_weak_pin(pin: str) -> bool:
    """Return True if the PIN is sequential or all-same digits."""
    if len(set(pin)) == 1:          # 1111, 0000, 9999 ...
        return True
    if pin in _SEQUENTIAL_PINS:     # 1234, 4321 ...
        return True
    return False


@router.post("/verify-bvn", response_model=VerifyBVNResponse, status_code=status.HTTP_201_CREATED)
async def verify_bvn(
    payload: VerifyBVNRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Verify BVN and create a Tier-1 trader account.

    1. Validate BVN format (11 digits)
    2. Call VerifyMe (or mock) to verify BVN
    3. Confirm the phone number matches the BVN record
    4. Check phone not already registered
    5. Create trader record in pending status
    6. Return trader_id, tradeflow_id, name
    """
    # 1. Call BVN provider
    provider = get_bvn_provider()
    result = await provider.verify_bvn(payload.bvn, payload.phone)

    if not result.verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="BVN verification failed. Please check your BVN and try again.",
        )

    # 2. Phone match
    if not result.phone_match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number does not match BVN records.",
        )

    # 3. Duplicate check
    existing = await db.execute(select(Trader).where(Trader.phone == payload.phone))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Phone number already registered",
        )

    # 4. Create trader (Tier 1, pending status — activated after PIN setup)
    trader = Trader(
        phone=payload.phone,
        full_name=result.full_name,
        kyc_tier=1,
        status=TraderStatus.PENDING,
    )
    trader.set_bvn(payload.bvn)
    db.add(trader)
    await db.flush()

    logger.info("Trader created: %s (%s)", trader.tradeflow_id, trader.full_name)

    return VerifyBVNResponse(
        success=True,
        trader_id=trader.id,
        tradeflow_id=trader.tradeflow_id,
        name=result.full_name,
    )


@router.post("/set-pin", response_model=SetPinResponse, status_code=status.HTTP_200_OK)
async def set_pin(
    payload: SetPinRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Set the transaction PIN and activate the trader account.

    1. Validate PIN: 4 digits, no sequential (1234), no repeated (1111)
    2. Hash with bcrypt (rounds=12)
    3. Activate account
    4. Generate JWT access + refresh tokens
    5. Return token pair
    """
    # 1. PIN strength validation
    if _is_weak_pin(payload.pin):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PIN is too weak. Avoid sequential (1234) or repeated (1111) digits.",
        )

    # 2. Find trader
    result = await db.execute(select(Trader).where(Trader.id == payload.trader_id))
    trader = result.scalar_one_or_none()
    if trader is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trader not found",
        )

    if trader.status != TraderStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account already activated",
        )

    # 3. Hash and store PIN, activate
    trader.set_pin(payload.pin)
    trader.status = TraderStatus.ACTIVE
    await db.flush()

    logger.info("Trader %s activated", trader.tradeflow_id)

    # 4. Generate tokens
    access_token = auth_service.create_access_token(str(trader.id), trader.phone)
    refresh_token = auth_service.create_refresh_token(str(trader.id), trader.phone)

    return SetPinResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


# ---------------------------------------------------------------------------
# PIN-based login
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse, status_code=status.HTTP_200_OK)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate with phone + PIN and receive JWT tokens.

    1. Look up trader by phone
    2. Verify account is active
    3. Verify PIN against stored bcrypt hash
    4. Return access + refresh tokens with trader profile
    """
    # 1. Find trader
    result = await db.execute(select(Trader).where(Trader.phone == payload.phone))
    trader = result.scalar_one_or_none()
    if trader is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone or PIN",
        )

    # 2. Check account is active
    if trader.status != TraderStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is not active",
        )

    # 3. Verify PIN
    if trader.pin_hash is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="PIN has not been set. Complete registration first.",
        )

    if not verify_pin(payload.pin, trader.pin_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone or PIN",
        )

    # 4. Generate tokens
    access_token = auth_service.create_access_token(str(trader.id), trader.phone)
    refresh_token = auth_service.create_refresh_token(str(trader.id), trader.phone)

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        trader=TraderRead.model_validate(trader),
    )


# ---------------------------------------------------------------------------
# Refresh (access token only)
# ---------------------------------------------------------------------------


@router.post("/refresh", response_model=RefreshTokenResponse, status_code=status.HTTP_200_OK)
async def refresh_access_token(
    payload: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Exchange a valid refresh token for a new access token.

    Unlike /token/refresh, this returns only a new access token (not a
    full pair), which is the standard pattern for token rotation.
    """
    # Validate refresh token
    token_payload = auth_service.verify_token(payload.refresh_token, expected_type="refresh")
    trader_id = token_payload.get("sub")

    # Find trader
    result = await db.execute(select(Trader).where(Trader.id == trader_id))
    trader = result.scalar_one_or_none()
    if trader is None or trader.status in (TraderStatus.SUSPENDED, TraderStatus.BLOCKED):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    # Issue new access token only
    access_token = auth_service.create_access_token(str(trader.id), trader.phone)

    return RefreshTokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


# ---------------------------------------------------------------------------
# OTP-based login flow
# ---------------------------------------------------------------------------


@router.post("/otp/request", status_code=status.HTTP_200_OK)
async def request_otp(
    payload: OTPRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """Request a new OTP for login."""
    # Rate limit check
    within_limit = await auth_service.check_otp_rate_limit(payload.phone, redis)
    if not within_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Try again later.",
        )

    # Validate phone number exists
    result = await db.execute(select(Trader).where(Trader.phone == payload.phone))
    trader = result.scalar_one_or_none()
    if trader is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Phone number not found",
        )

    # Generate OTP and log
    otp = await auth_service.generate_otp(payload.phone, redis)
    logger.info("OTP for %s: %s", payload.phone, otp)

    return {"message": "OTP sent", "expires_in": settings.OTP_EXPIRE_SECONDS}


@router.post("/otp/verify", response_model=TokenResponse)
async def verify_otp_login(
    payload: OTPVerify,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """Verify OTP and return JWT access + refresh tokens."""
    # Find trader
    result = await db.execute(select(Trader).where(Trader.phone == payload.phone))
    trader = result.scalar_one_or_none()
    if trader is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone or OTP",
        )

    # Verify OTP from Redis
    valid = await auth_service.verify_otp(payload.phone, payload.otp, redis)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired OTP",
        )

    # Activate trader on first successful OTP verification
    if trader.status.value == "pending":
        trader.status = "active"
        await db.flush()

    # Generate token pair
    access_token = auth_service.create_access_token(str(trader.id), trader.phone)
    refresh_token = auth_service.create_refresh_token(str(trader.id), trader.phone)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


@router.post("/token/refresh", response_model=TokenResponse)
async def refresh_token(
    payload: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a valid refresh token for a new access token."""
    # Validate refresh token
    token_payload = auth_service.verify_token(payload.refresh_token, expected_type="refresh")
    trader_id = token_payload.get("sub")

    # Find trader
    result = await db.execute(select(Trader).where(Trader.id == trader_id))
    trader = result.scalar_one_or_none()
    if trader is None or trader.status.value not in ("active", "pending"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    # Issue new token pair
    access_token = auth_service.create_access_token(str(trader.id), trader.phone)
    new_refresh_token = auth_service.create_refresh_token(str(trader.id), trader.phone)

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
