"""
Match record model — records successful matches between transactions.

When the matching engine pairs a buy-side and sell-side transaction,
a Match record is created to track the settlement lifecycle.
"""

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    Enum as SAEnum,
    event,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MatchType(str, enum.Enum):
    EXACT = "exact"
    MULTI = "multi"
    PARTIAL = "partial"


class MatchStatus(str, enum.Enum):
    PENDING_SETTLEMENT = "pending_settlement"
    SETTLING = "settling"
    SETTLED = "settled"
    FAILED = "failed"


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    cycle_id: Mapped[str] = mapped_column(String(50), nullable=False)

    buy_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False,
    )
    sell_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False,
    )

    match_type: Mapped[MatchType] = mapped_column(
        SAEnum(MatchType, name="matchtype"), nullable=False,
    )
    matched_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False,
    )
    matched_rate: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=6), nullable=False,
    )

    status: Mapped[MatchStatus] = mapped_column(
        SAEnum(MatchStatus, name="matchstatus"),
        default=MatchStatus.PENDING_SETTLEMENT,
    )
    settlement_reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)

    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    buy_transaction = relationship(
        "Transaction", foreign_keys=[buy_transaction_id],
    )
    sell_transaction = relationship(
        "Transaction", foreign_keys=[sell_transaction_id],
    )

    def __repr__(self) -> str:
        return (
            f"<Match {self.cycle_id} "
            f"{self.matched_amount} "
            f"({self.match_type.value if self.match_type else 'N/A'})>"
        )


@event.listens_for(Match, "init")
def _set_match_defaults(target, args, kwargs):
    if "id" not in kwargs:
        target.id = uuid.uuid4()
    if "status" not in kwargs:
        target.status = MatchStatus.PENDING_SETTLEMENT
    if "matched_at" not in kwargs:
        target.matched_at = datetime.now(timezone.utc)
