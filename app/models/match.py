"""
Match record model — records successful matches between transactions.

When the matching engine pairs a buy-side and sell-side transaction,
a Match record is created to track the settlement lifecycle.
"""

import uuid
import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, Numeric, DateTime, ForeignKey, Enum as SAEnum, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MatchType(str, enum.Enum):
    EXACT = "exact"
    MULTI = "multi"
    PARTIAL = "partial"


class SettlementMethod(str, enum.Enum):
    P2P = "p2p"
    CIPS = "cips"


class MatchStatus(str, enum.Enum):
    PENDING_SETTLEMENT = "pending_settlement"
    SETTLING = "settling"
    SETTLED = "settled"
    FAILED = "failed"


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cycle_id: Mapped[str] = mapped_column(String(50), nullable=False)
    buy_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False
    )
    sell_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False
    )
    match_type: Mapped[MatchType] = mapped_column(
        SAEnum(MatchType), nullable=False
    )
    matched_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False
    )
    matched_rate: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=6), nullable=False
    )
    settlement_method: Mapped[SettlementMethod] = mapped_column(
        SAEnum(SettlementMethod), default=SettlementMethod.P2P
    )
    status: Mapped[MatchStatus] = mapped_column(
        SAEnum(MatchStatus), default=MatchStatus.PENDING_SETTLEMENT
    )
    settlement_reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    matched_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime)

    def __repr__(self) -> str:
        return f"<Match {self.cycle_id} {self.matched_amount} ({self.match_type.value})>"
