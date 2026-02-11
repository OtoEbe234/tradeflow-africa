"""
Matching pool model — tracks transactions currently in the matching pool.

Mirrors the Redis pool state in the database for audit and recovery.
Each entry represents a transaction waiting to be matched with a counterparty.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, Numeric, DateTime, ForeignKey, Boolean, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.transaction import TransactionDirection


class MatchingPool(Base):
    __tablename__ = "matching_pool"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), unique=True, nullable=False
    )
    trader_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("traders.id"), nullable=False
    )
    direction: Mapped[TransactionDirection] = mapped_column(
        SAEnum(TransactionDirection), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    priority_score: Mapped[Decimal] = mapped_column(
        Numeric(precision=8, scale=4), default=Decimal("0.0")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    entered_pool_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return f"<PoolEntry {self.transaction_id} {self.amount} {self.currency}>"
