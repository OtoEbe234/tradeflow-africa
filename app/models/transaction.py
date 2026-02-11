"""
Transaction model — represents a cross-border payment request.

Each transaction starts in PENDING status, enters the matching pool,
and progresses through MATCHED -> SETTLING -> COMPLETED or FAILED.
"""

import uuid
import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, Numeric, DateTime, ForeignKey, Enum as SAEnum, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TransactionDirection(str, enum.Enum):
    NGN_TO_CNY = "ngn_to_cny"
    CNY_TO_NGN = "cny_to_ngn"


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    IN_POOL = "in_pool"
    MATCHED = "matched"
    SETTLING = "settling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    trader_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("traders.id"), nullable=False
    )
    direction: Mapped[TransactionDirection] = mapped_column(
        SAEnum(TransactionDirection), nullable=False
    )
    source_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False
    )
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False
    )
    target_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    locked_rate: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=6), nullable=False
    )
    fee_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), default=Decimal("0.00")
    )
    status: Mapped[TransactionStatus] = mapped_column(
        SAEnum(TransactionStatus), default=TransactionStatus.PENDING
    )
    reference: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False
    )
    beneficiary_name: Mapped[str | None] = mapped_column(String(255))
    beneficiary_account: Mapped[str | None] = mapped_column(String(50))
    beneficiary_bank: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    trader = relationship("Trader", back_populates="transactions")

    def __repr__(self) -> str:
        return f"<Transaction {self.reference} {self.source_amount} {self.source_currency}>"
