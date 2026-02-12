"""
Pydantic schemas for transaction creation, listing, and management.
"""

import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TransactionCreateRequest(BaseModel):
    """Schema for creating a new cross-border payment transaction."""
    source_currency: str = Field(..., pattern=r"^(NGN|CNY)$", examples=["NGN"])
    target_currency: str = Field(..., pattern=r"^(NGN|CNY)$", examples=["CNY"])
    source_amount: Decimal = Field(..., gt=0, examples=[50000000])
    supplier_name: str = Field(
        ..., min_length=1, max_length=200,
        examples=["Shenzhen Electronics Co."],
    )
    supplier_bank: str = Field(
        ..., min_length=1, max_length=100,
        examples=["Bank of China"],
    )
    supplier_account: str = Field(..., examples=["621082100123456789"])
    quote_id: str | None = Field(None, examples=["QT-ABC123DEF456"])
    pin: str = Field(..., min_length=4, max_length=4, pattern=r"^\d{4}$")

    @field_validator("supplier_account")
    @classmethod
    def validate_supplier_account(cls, v: str) -> str:
        if not re.match(r"^\d{10,20}$", v):
            raise ValueError("Supplier account must be 10-20 digits")
        return v


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class DepositInstructions(BaseModel):
    """Mock virtual account deposit instructions."""
    bank_name: str
    account_number: str
    account_name: str
    amount: Decimal
    currency: str
    reference: str
    expires_at: datetime


class TransactionResponse(BaseModel):
    """Full transaction response with optional deposit instructions."""
    id: UUID
    reference: str
    trader_id: UUID
    direction: str
    source_currency: str
    target_currency: str
    source_amount: Decimal
    target_amount: Decimal | None
    exchange_rate: Decimal | None
    fee_amount: Decimal
    fee_percentage: Decimal
    supplier_name: str | None
    supplier_bank: str | None
    status: str
    funded_at: datetime | None
    matched_at: datetime | None
    settled_at: datetime | None
    created_at: datetime
    deposit_instructions: DepositInstructions | None = None


class TransactionListResponse(BaseModel):
    """Paginated transaction list."""
    items: list[TransactionResponse]
    total: int
    page: int
    per_page: int


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


class CancelRequest(BaseModel):
    """Schema for cancelling a transaction (requires PIN)."""
    pin: str = Field(..., min_length=4, max_length=4, pattern=r"^\d{4}$")


# ---------------------------------------------------------------------------
# Webhook (kept for future use)
# ---------------------------------------------------------------------------


class TransactionStatusUpdate(BaseModel):
    """Schema for transaction status webhook callbacks."""
    transaction_id: UUID
    status: str
    reference: str | None = None
    metadata: dict | None = None
