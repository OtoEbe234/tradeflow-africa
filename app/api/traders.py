"""
Trader profile and KYC endpoints.

Handles trader profile management and identity verification
through BVN/NIN checks required for regulatory compliance.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.trader import TraderRead, TraderUpdate, KYCSubmit, KYCStatus

router = APIRouter()


@router.get("/me", response_model=TraderRead)
async def get_profile(db: AsyncSession = Depends(get_db)):
    """Get the authenticated trader's profile."""
    # TODO: Extract trader_id from JWT
    # TODO: Fetch trader from database
    raise HTTPException(status_code=501, detail="Not implemented")


@router.patch("/me", response_model=TraderRead)
async def update_profile(payload: TraderUpdate, db: AsyncSession = Depends(get_db)):
    """Update trader profile fields (business name, address, etc.)."""
    # TODO: Validate and apply partial update
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/me/kyc", response_model=KYCStatus, status_code=status.HTTP_202_ACCEPTED)
async def submit_kyc(payload: KYCSubmit, db: AsyncSession = Depends(get_db)):
    """Submit KYC documents (BVN or NIN) for verification."""
    # TODO: Validate document type and number format
    # TODO: Dispatch async verification via KYC service
    # TODO: Return pending status
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/me/kyc", response_model=KYCStatus)
async def get_kyc_status(db: AsyncSession = Depends(get_db)):
    """Check the current KYC verification status."""
    # TODO: Return latest KYC verification record
    raise HTTPException(status_code=501, detail="Not implemented")
