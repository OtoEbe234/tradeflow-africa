"""
Pydantic schemas for trader registration, profile, KYC, and auth responses.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Registration (phone‑only first step)
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    """Schema for starting registration — phone only."""
    phone: str = Field(
        ...,
        pattern=r"^\+234[0-9]{10}$",
        examples=["+2348012345678"],
        description="Nigerian phone number in +234XXXXXXXXXX format",
    )


class RegisterResponse(BaseModel):
    """Response after successful OTP dispatch."""
    success: bool = True
    message: str = "OTP sent"


class VerifyOTPRequest(BaseModel):
    """Schema for verifying an OTP during registration."""
    phone: str = Field(..., pattern=r"^\+234[0-9]{10}$")
    otp: str = Field(..., min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class VerifyOTPResponse(BaseModel):
    """Response after successful OTP verification."""
    success: bool = True
    next_step: str = "bvn_verification"


# ---------------------------------------------------------------------------
# Trader CRUD
# ---------------------------------------------------------------------------


class TraderCreate(BaseModel):
    """Schema for creating a full trader profile (after phone verification)."""
    phone: str = Field(..., pattern=r"^\+234[0-9]{10}$", examples=["+2348012345678"])
    full_name: str = Field(..., min_length=2, max_length=100)
    business_name: str | None = Field(None, max_length=200)
    pin: str = Field(..., min_length=4, max_length=6, pattern=r"^[0-9]{4,6}$")
    referred_by: UUID | None = None


class TraderUpdate(BaseModel):
    """Schema for partial profile updates."""
    full_name: str | None = Field(None, min_length=2, max_length=100)
    business_name: str | None = Field(None, max_length=200)


class TraderRead(BaseModel):
    """Schema returned when reading trader data."""
    id: UUID
    phone: str
    tradeflow_id: str
    full_name: str
    business_name: str | None
    kyc_tier: int
    monthly_limit: Decimal
    monthly_used: Decimal
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class OTPRequest(BaseModel):
    """Schema for requesting an OTP."""
    phone: str = Field(..., pattern=r"^\+?[0-9]{10,15}$")


class OTPVerify(BaseModel):
    """Schema for verifying an OTP."""
    phone: str = Field(..., pattern=r"^\+?[0-9]{10,15}$")
    otp: str = Field(..., min_length=6, max_length=6)


class TokenResponse(BaseModel):
    """JWT token pair response."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class LoginRequest(BaseModel):
    """Schema for PIN-based login."""
    phone: str = Field(..., pattern=r"^\+234[0-9]{10}$")
    pin: str = Field(..., min_length=4, max_length=4, pattern=r"^[0-9]{4}$")


class LoginResponse(BaseModel):
    """Response after successful PIN login."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    trader: TraderRead


class RefreshTokenRequest(BaseModel):
    """Schema for refreshing an access token."""
    refresh_token: str


class RefreshTokenResponse(BaseModel):
    """Response containing only a new access token."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# ---------------------------------------------------------------------------
# BVN verification
# ---------------------------------------------------------------------------


class VerifyBVNRequest(BaseModel):
    """Schema for BVN verification during registration."""
    phone: str = Field(..., pattern=r"^\+234[0-9]{10}$")
    bvn: str = Field(
        ...,
        min_length=11,
        max_length=11,
        pattern=r"^[0-9]{11}$",
        description="11-digit Bank Verification Number",
    )


class VerifyBVNResponse(BaseModel):
    """Response after successful BVN verification + trader creation."""
    success: bool = True
    trader_id: UUID
    tradeflow_id: str
    name: str


# ---------------------------------------------------------------------------
# PIN setup
# ---------------------------------------------------------------------------


class SetPinRequest(BaseModel):
    """Schema for setting a transaction PIN."""
    trader_id: UUID
    pin: str = Field(
        ...,
        min_length=4,
        max_length=4,
        pattern=r"^[0-9]{4}$",
        description="4-digit numeric PIN",
    )


class SetPinResponse(BaseModel):
    """Response after PIN setup + account activation."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


# ---------------------------------------------------------------------------
# KYC (general)
# ---------------------------------------------------------------------------


class KYCSubmit(BaseModel):
    """Schema for submitting KYC documents."""
    document_type: str = Field(..., examples=["bvn", "nin"])
    document_number: str = Field(..., min_length=11, max_length=11)


class KYCStatus(BaseModel):
    """Schema for KYC verification status."""
    status: str = Field(..., examples=["pending", "verified", "rejected"])
    document_type: str | None = None
    submitted_at: datetime | None = None
    verified_at: datetime | None = None
    rejection_reason: str | None = None
