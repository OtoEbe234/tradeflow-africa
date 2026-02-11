"""
Admin dashboard endpoints.

Provides platform-wide analytics, trader management,
and operational controls for TradeFlow administrators.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter()


@router.get("/dashboard")
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    """
    Get admin dashboard summary.

    Returns key metrics: active traders, pending transactions,
    total volume (NGN & CNY), matching rate, and pending KYC reviews.
    """
    # TODO: Aggregate metrics from database
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/traders")
async def list_traders(
    page: int = 1,
    page_size: int = 20,
    kyc_status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List all traders with optional KYC status filter."""
    # TODO: Paginated trader query with filters
    raise HTTPException(status_code=501, detail="Not implemented")


@router.patch("/traders/{trader_id}/kyc/approve")
async def approve_kyc(trader_id: str, db: AsyncSession = Depends(get_db)):
    """Manually approve a trader's KYC verification."""
    # TODO: Update KYC status, notify trader
    raise HTTPException(status_code=501, detail="Not implemented")


@router.patch("/traders/{trader_id}/kyc/reject")
async def reject_kyc(trader_id: str, reason: str = "", db: AsyncSession = Depends(get_db)):
    """Reject a trader's KYC verification with a reason."""
    # TODO: Update KYC status with reason, notify trader
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/transactions")
async def list_all_transactions(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """List all transactions across the platform (admin view)."""
    # TODO: Paginated query with full details
    raise HTTPException(status_code=501, detail="Not implemented")
