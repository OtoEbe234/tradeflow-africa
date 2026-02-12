"""
Matching pool model — tracks transactions currently in the matching pool.

Mirrors the Redis sorted-set pool state in the database for audit and
disaster recovery. Each entry represents a funded transaction waiting
to be matched with a counterparty.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Enum as SAEnum,
    event,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.transaction import TransactionDirection


class MatchingPool(Base):
    __tablename__ = "matching_pool"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"),
        unique=True, nullable=False,
    )
    trader_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("traders.id"), nullable=False,
    )
    direction: Mapped[TransactionDirection] = mapped_column(
        SAEnum(TransactionDirection, name="transactiondirection",
               create_type=False),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    priority_score: Mapped[Decimal] = mapped_column(
        Numeric(precision=8, scale=4), default=Decimal("0"),
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    entered_pool_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    # Relationships
    transaction = relationship("Transaction", backref="pool_entry")
    trader = relationship("Trader")

    def __repr__(self) -> str:
        return (
            f"<PoolEntry txn={self.transaction_id} "
            f"{self.amount} {self.currency} "
            f"active={self.is_active}>"
        )


@event.listens_for(MatchingPool, "init")
def _set_pool_defaults(target, args, kwargs):
    if "id" not in kwargs:
        target.id = uuid.uuid4()
    if "priority_score" not in kwargs:
        target.priority_score = Decimal("0")
    if "is_active" not in kwargs:
        target.is_active = True
    if "entered_pool_at" not in kwargs:
        target.entered_pool_at = datetime.now(timezone.utc)
