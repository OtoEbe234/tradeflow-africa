"""
Transaction CRUD endpoints.

Handles creation, listing, and status tracking of cross-border
payment transactions between Nigerian and Chinese traders.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.transaction import (
    TransactionCreate,
    TransactionRead,
    TransactionList,
    TransactionStatusUpdate,
)

router = APIRouter()


@router.post("/", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    payload: TransactionCreate, db: AsyncSession = Depends(get_db)
):
    """
    Create a new cross-border payment transaction.

    Validates the trader's KYC status, locks the current FX rate,
    and places the transaction into the matching pool.
    """
    # TODO: Verify trader KYC is approved
    # TODO: Fetch and lock current FX rate
    # TODO: Create transaction record
    # TODO: Add to matching pool in Redis
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/", response_model=TransactionList)
async def list_transactions(
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List transactions for the authenticated trader with pagination."""
    # TODO: Filter by trader_id from JWT and optional status
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/{transaction_id}", response_model=TransactionRead)
async def get_transaction(transaction_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get details of a specific transaction."""
    # TODO: Fetch transaction, verify ownership
    raise HTTPException(status_code=501, detail="Not implemented")


@router.patch("/{transaction_id}/cancel", response_model=TransactionRead)
async def cancel_transaction(transaction_id: UUID, db: AsyncSession = Depends(get_db)):
    """Cancel a pending transaction (only if not yet matched)."""
    # TODO: Verify transaction is still in PENDING status
    # TODO: Remove from matching pool
    # TODO: Update status to CANCELLED
    raise HTTPException(status_code=501, detail="Not implemented")
