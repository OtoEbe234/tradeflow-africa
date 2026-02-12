"""
Transaction model — represents a cross-border payment request.

Per the TradeFlow PRD/FRD v2.0:
- TXN-XXXXXXXX reference format
- 12-state lifecycle with validated transitions
- Encrypted supplier account details
- Fee tiers calculated at initiation
"""

import enum
import random
import string
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
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

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TransactionDirection(str, enum.Enum):
    NGN_TO_CNY = "ngn_to_cny"
    CNY_TO_NGN = "cny_to_ngn"


class TransactionStatus(str, enum.Enum):
    INITIATED = "initiated"
    FUNDED = "funded"
    MATCHING = "matching"
    MATCHED = "matched"
    PARTIAL_MATCHED = "partial_matched"
    PENDING_SETTLEMENT = "pending_settlement"
    SETTLING = "settling"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class SettlementMethod(str, enum.Enum):
    MATCHED = "matched"
    PARTIAL_MATCHED = "partial_matched"
    CIPS_SETTLED = "cips_settled"


# ---------------------------------------------------------------------------
# Status transition map
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[TransactionStatus, set[TransactionStatus]] = {
    TransactionStatus.INITIATED: {
        TransactionStatus.FUNDED,
        TransactionStatus.CANCELLED,
        TransactionStatus.EXPIRED,
    },
    TransactionStatus.FUNDED: {
        TransactionStatus.MATCHING,
        TransactionStatus.CANCELLED,
        TransactionStatus.EXPIRED,
    },
    TransactionStatus.MATCHING: {
        TransactionStatus.MATCHED,
        TransactionStatus.PARTIAL_MATCHED,
        TransactionStatus.EXPIRED,
    },
    TransactionStatus.MATCHED: {
        TransactionStatus.PENDING_SETTLEMENT,
    },
    TransactionStatus.PARTIAL_MATCHED: {
        TransactionStatus.PENDING_SETTLEMENT,
        TransactionStatus.MATCHING,
    },
    TransactionStatus.PENDING_SETTLEMENT: {
        TransactionStatus.SETTLING,
        TransactionStatus.FAILED,
    },
    TransactionStatus.SETTLING: {
        TransactionStatus.COMPLETED,
        TransactionStatus.FAILED,
    },
    TransactionStatus.COMPLETED: set(),
    TransactionStatus.FAILED: {
        TransactionStatus.REFUNDED,
    },
    TransactionStatus.REFUNDED: set(),
    TransactionStatus.CANCELLED: set(),
    TransactionStatus.EXPIRED: {
        TransactionStatus.REFUNDED,
    },
}


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("source_amount > 0", name="ck_transactions_source_positive"),
    )

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )

    # Reference
    reference: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, nullable=False,
    )

    # Owner
    trader_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("traders.id"), nullable=False,
    )

    # Direction
    direction: Mapped[TransactionDirection] = mapped_column(
        SAEnum(TransactionDirection, name="transactiondirection"), nullable=False,
    )

    # Amounts
    source_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False,
    )
    target_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=True,
    )
    exchange_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=12, scale=6), nullable=True,
    )

    # Fees
    fee_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), default=Decimal("0"),
    )
    fee_percentage: Mapped[Decimal] = mapped_column(
        Numeric(precision=5, scale=4), default=Decimal("0"),
    )

    # Supplier / beneficiary info
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    supplier_bank: Mapped[str | None] = mapped_column(String(100))
    supplier_account: Mapped[str | None] = mapped_column(String(256))  # encrypted
    invoice_url: Mapped[str | None] = mapped_column(String(500))

    # Status
    status: Mapped[TransactionStatus] = mapped_column(
        SAEnum(TransactionStatus, name="transactionstatus"),
        default=TransactionStatus.INITIATED,
    )

    # Match / settlement
    match_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matches.id"), nullable=True,
    )
    settlement_method: Mapped[SettlementMethod | None] = mapped_column(
        SAEnum(SettlementMethod, name="settlementmethod"), nullable=True,
    )

    # Lifecycle timestamps
    funded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    trader = relationship("Trader", back_populates="transactions")

    # ------------------------------------------------------------------
    # Reference generation
    # ------------------------------------------------------------------

    @staticmethod
    def generate_reference() -> str:
        """Generate a TXN-XXXXXXXX reference (8 uppercase alphanumeric chars)."""
        chars = string.ascii_uppercase + string.digits
        suffix = "".join(random.choices(chars, k=8))
        return f"TXN-{suffix}"

    # ------------------------------------------------------------------
    # Encrypted supplier account helpers
    # ------------------------------------------------------------------

    def set_supplier_account(self, plaintext: str) -> None:
        """Encrypt and store the supplier account number."""
        from app.models.trader import Trader
        self.supplier_account = Trader.encrypt_value(plaintext)

    def get_supplier_account(self) -> str | None:
        """Decrypt and return the supplier account number."""
        if self.supplier_account is None:
            return None
        from app.models.trader import Trader
        return Trader.decrypt_value(self.supplier_account)

    # ------------------------------------------------------------------
    # Status transition validation
    # ------------------------------------------------------------------

    @staticmethod
    def is_valid_transition(from_status: TransactionStatus, to_status: TransactionStatus) -> bool:
        """Check whether a status transition is allowed."""
        allowed = VALID_TRANSITIONS.get(from_status, set())
        return to_status in allowed

    def transition_to(self, new_status: TransactionStatus) -> None:
        """
        Transition to *new_status* if the move is valid.

        Raises ValueError if the transition is not allowed.
        Also auto-sets lifecycle timestamps where applicable.
        """
        if not self.is_valid_transition(self.status, new_status):
            raise ValueError(
                f"Invalid transition: {self.status.value} -> {new_status.value}"
            )
        self.status = new_status

        now = datetime.now(timezone.utc)
        if new_status == TransactionStatus.FUNDED:
            self.funded_at = now
        elif new_status in (TransactionStatus.MATCHED, TransactionStatus.PARTIAL_MATCHED):
            self.matched_at = now
        elif new_status == TransactionStatus.COMPLETED:
            self.settled_at = now

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<Transaction {self.reference} "
            f"{self.source_amount} "
            f"status={self.status.value if self.status else 'N/A'}>"
        )


# ---------------------------------------------------------------------------
# Auto-set Python-side defaults on construction
# ---------------------------------------------------------------------------


@event.listens_for(Transaction, "init")
def _set_transaction_defaults(target, args, kwargs):
    if "id" not in kwargs:
        target.id = uuid.uuid4()
    if "reference" not in kwargs:
        target.reference = Transaction.generate_reference()
    if "status" not in kwargs:
        target.status = TransactionStatus.INITIATED
    if "fee_amount" not in kwargs:
        target.fee_amount = Decimal("0")
    if "fee_percentage" not in kwargs:
        target.fee_percentage = Decimal("0")
    if "created_at" not in kwargs:
        target.created_at = datetime.now(timezone.utc)
    if "updated_at" not in kwargs:
        target.updated_at = datetime.now(timezone.utc)
