"""
Pydantic schemas for trader registration, profile, KYC, and auth responses.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TraderCreate(BaseModel):
    """Schema for new trader registration."""
    phone: str = Field(..., pattern=r"^\+?[0-9]{10,15}$", examples=["+2348012345678"])
    business_name: str = Field(..., min_length=2, max_length=255)
    trader_type: str = Field(..., examples=["nigerian_importer"])


class TraderUpdate(BaseModel):
    """Schema for partial profile updates."""
    business_name: str | None = None
    email: str | None = None


class TraderRead(BaseModel):
    """Schema returned when reading trader data."""
    id: UUID
    phone: str
    phone_verified: bool
    business_name: str | None
    trader_type: str
    kyc_level: str
    email: str | None
    is_active: bool
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
