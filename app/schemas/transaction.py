"""
Pydantic schemas for transaction creation, listing, and status updates.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class TransactionCreate(BaseModel):
    """Schema for creating a new transaction."""
    direction: str = Field(..., examples=["ngn_to_cny"])
    source_amount: Decimal = Field(..., gt=0, examples=[5000000.00])
    source_currency: str = Field(..., min_length=3, max_length=3, examples=["NGN"])
    beneficiary_name: str = Field(..., max_length=255)
    beneficiary_account: str = Field(..., max_length=50)
    beneficiary_bank: str = Field(..., max_length=100)
    notes: str | None = None


class TransactionRead(BaseModel):
    """Schema returned when reading transaction data."""
    id: UUID
    trader_id: UUID
    direction: str
    source_amount: Decimal
    source_currency: str
    target_amount: Decimal
    target_currency: str
    locked_rate: Decimal
    fee_amount: Decimal
    status: str
    reference: str
    beneficiary_name: str | None
    beneficiary_account: str | None
    beneficiary_bank: str | None
    notes: str | None
    matched_at: datetime | None
    settled_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TransactionList(BaseModel):
    """Paginated list of transactions."""
    items: list[TransactionRead]
    total: int
    page: int
    page_size: int


class TransactionStatusUpdate(BaseModel):
    """Schema for transaction status webhook callbacks."""
    transaction_id: UUID
    status: str
    reference: str | None = None
    metadata: dict | None = None
