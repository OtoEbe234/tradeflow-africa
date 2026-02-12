"""
Development endpoint — simulate payment for testing.

Only available when APP_ENV == "development".
"""

import logging
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.transaction import Transaction, TransactionStatus
from app.services.payment_service import payment_service
from app.api.webhooks import _process_payment

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Schemas ---


class SimulatePaymentRequest(BaseModel):
    transaction_id: UUID
    amount: float


class SimulatePaymentResponse(BaseModel):
    result: dict
    webhook_payload: dict


# --- Endpoint ---


@router.post("/simulate-payment", response_model=SimulatePaymentResponse)
async def simulate_payment(
    payload: SimulatePaymentRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Simulate a Providus payment for a transaction (dev only).

    Builds a mock webhook payload and processes it directly
    without HTTP round-trip.
    """
    if settings.APP_ENV != "development":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Simulate payment is only available in development mode",
        )

    # Look up transaction
    result = await db.execute(
        select(Transaction).where(Transaction.id == payload.transaction_id)
    )
    txn = result.scalar_one_or_none()

    if txn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found",
        )

    if txn.status != TransactionStatus.INITIATED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Transaction is already in '{txn.status.value}' status",
        )

    # Build mock webhook payload
    account_number = f"TF{txn.reference[4:]}"
    webhook_payload = payment_service.simulate_webhook_payload(
        account_number=account_number,
        amount=payload.amount,
        reference=txn.reference,
    )

    # Process payment directly
    process_result = await _process_payment(
        paid_amount=Decimal(str(payload.amount)),
        account_number=account_number,
        session_id=webhook_payload["sessionId"],
        db=db,
    )

    return SimulatePaymentResponse(
        result=process_result,
        webhook_payload=webhook_payload,
    )
