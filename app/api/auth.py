"""
Authentication endpoints — registration, login, and OTP verification.

Supports phone-number-based auth with OTP delivery via SMS and WhatsApp.
JWT tokens use RS256 for stateless verification across services.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.trader import TraderCreate, TraderRead, TokenResponse, OTPRequest, OTPVerify

router = APIRouter()


@router.post("/register", response_model=TraderRead, status_code=status.HTTP_201_CREATED)
async def register(payload: TraderCreate, db: AsyncSession = Depends(get_db)):
    """Register a new trader account. Sends OTP for phone verification."""
    # TODO: Check if phone number already exists
    # TODO: Create trader record with unverified status
    # TODO: Generate and send OTP via SMS/WhatsApp
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/otp/request", status_code=status.HTTP_200_OK)
async def request_otp(payload: OTPRequest, db: AsyncSession = Depends(get_db)):
    """Request a new OTP for login or verification."""
    # TODO: Validate phone number exists
    # TODO: Generate OTP, store in Redis with TTL
    # TODO: Send via SMS and WhatsApp
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/otp/verify", response_model=TokenResponse)
async def verify_otp(payload: OTPVerify, db: AsyncSession = Depends(get_db)):
    """Verify OTP and return JWT access + refresh tokens."""
    # TODO: Validate OTP from Redis
    # TODO: Mark phone as verified if first login
    # TODO: Generate RS256 JWT token pair
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/token/refresh", response_model=TokenResponse)
async def refresh_token():
    """Exchange a valid refresh token for a new access token."""
    # TODO: Validate refresh token signature and expiry
    # TODO: Issue new token pair
    raise HTTPException(status_code=501, detail="Not implemented")
