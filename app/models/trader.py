"""
Trader model — represents a registered trader on the platform.

Per the TradeFlow PRD/FRD v2.0 data model:
- Phone-based registration with TF-XXXXX IDs
- Tiered KYC with calculated monthly limits
- Fernet-encrypted BVN/NIN at rest
- PIN-based transaction authorization (bcrypt)
"""

import enum
import random
import string
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import bcrypt as _bcrypt
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Enum as SAEnum,
    event,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.database import Base

# ---------------------------------------------------------------------------
# Fernet cipher — lazily initialised from settings
# ---------------------------------------------------------------------------

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(settings.FERNET_KEY.encode())
    return _fernet


def configure_fernet(key: str | bytes) -> None:
    """Override the Fernet key at runtime (used in tests)."""
    global _fernet
    if isinstance(key, str):
        key = key.encode()
    _fernet = Fernet(key)


# ---------------------------------------------------------------------------
# Monthly‑limit tiers (USD equivalent)
# ---------------------------------------------------------------------------

TIER_LIMITS: dict[int, Decimal] = {
    1: Decimal("5000"),
    2: Decimal("50000"),
    3: Decimal("500000"),
}


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TraderStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class Trader(Base):
    __tablename__ = "traders"
    __table_args__ = (
        CheckConstraint("kyc_tier >= 1 AND kyc_tier <= 3", name="ck_traders_kyc_tier"),
    )

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Identity
    phone: Mapped[str] = mapped_column(
        String(15), unique=True, index=True, nullable=False
    )
    tradeflow_id: Mapped[str] = mapped_column(
        String(10), unique=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(200))

    # KYC documents — stored Fernet‑encrypted
    bvn: Mapped[str | None] = mapped_column(String(256))
    nin: Mapped[str | None] = mapped_column(String(256))
    cac_number: Mapped[str | None] = mapped_column(String(20))

    # Tier & limits
    kyc_tier: Mapped[int] = mapped_column(Integer, default=1)
    monthly_limit: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), default=TIER_LIMITS[1]
    )
    monthly_used: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), default=Decimal("0")
    )

    # Auth
    pin_hash: Mapped[str | None] = mapped_column(String(256))

    # Status
    status: Mapped[TraderStatus] = mapped_column(
        SAEnum(TraderStatus, name="traderstatus"),
        default=TraderStatus.PENDING,
    )

    # Referral (self-referential FK)
    referred_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("traders.id"), nullable=True
    )

    # Timestamps (timezone-aware)
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
    transactions = relationship("Transaction", back_populates="trader")
    referrals = relationship("Trader", backref="referrer", remote_side=[id])

    # ------------------------------------------------------------------
    # TradeFlow ID generation
    # ------------------------------------------------------------------

    @staticmethod
    def generate_tradeflow_id() -> str:
        """Generate a unique TF-XXXXX identifier (5 uppercase alphanumeric chars)."""
        chars = string.ascii_uppercase + string.digits
        suffix = "".join(random.choices(chars, k=5))
        return f"TF-{suffix}"

    # ------------------------------------------------------------------
    # Encryption helpers
    # ------------------------------------------------------------------

    @staticmethod
    def encrypt_value(plaintext: str) -> str:
        """Encrypt a string value using Fernet. Returns base64-encoded ciphertext."""
        return _get_fernet().encrypt(plaintext.encode()).decode()

    @staticmethod
    def decrypt_value(ciphertext: str) -> str:
        """Decrypt a Fernet-encrypted value. Raises ValueError on failure."""
        try:
            return _get_fernet().decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            raise ValueError("Failed to decrypt value — invalid key or corrupted data")

    def set_bvn(self, plaintext_bvn: str) -> None:
        """Encrypt and store a BVN."""
        self.bvn = self.encrypt_value(plaintext_bvn)

    def get_bvn(self) -> str | None:
        """Return the decrypted BVN, or None if not set."""
        if self.bvn is None:
            return None
        return self.decrypt_value(self.bvn)

    def set_nin(self, plaintext_nin: str) -> None:
        """Encrypt and store a NIN."""
        self.nin = self.encrypt_value(plaintext_nin)

    def get_nin(self) -> str | None:
        """Return the decrypted NIN, or None if not set."""
        if self.nin is None:
            return None
        return self.decrypt_value(self.nin)

    # ------------------------------------------------------------------
    # PIN helpers (using bcrypt directly)
    # ------------------------------------------------------------------

    def set_pin(self, plain_pin: str) -> None:
        """Hash and store a PIN using bcrypt."""
        hashed = _bcrypt.hashpw(plain_pin.encode(), _bcrypt.gensalt())
        self.pin_hash = hashed.decode()

    def verify_pin(self, plain_pin: str) -> bool:
        """Verify a PIN against the stored hash."""
        if self.pin_hash is None:
            return False
        return _bcrypt.checkpw(plain_pin.encode(), self.pin_hash.encode())

    # ------------------------------------------------------------------
    # Limit helpers
    # ------------------------------------------------------------------

    def exceeds_monthly_limit(self, amount: Decimal) -> bool:
        """Return True if adding *amount* would exceed the monthly limit."""
        return (self.monthly_used + amount) > self.monthly_limit

    def sync_monthly_limit(self) -> None:
        """Recalculate monthly_limit from kyc_tier."""
        self.monthly_limit = TIER_LIMITS.get(self.kyc_tier, TIER_LIMITS[1])

    # ------------------------------------------------------------------
    # Repr (safe — no sensitive fields)
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<Trader {self.tradeflow_id} "
            f"name={self.full_name!r} "
            f"status={self.status.value if self.status else 'N/A'}>"
        )


# ---------------------------------------------------------------------------
# Auto-set Python-side defaults on construction
# ---------------------------------------------------------------------------


@event.listens_for(Trader, "init")
def _set_defaults(target, args, kwargs):
    if "id" not in kwargs:
        target.id = uuid.uuid4()
    if "tradeflow_id" not in kwargs:
        target.tradeflow_id = Trader.generate_tradeflow_id()
    if "kyc_tier" not in kwargs:
        target.kyc_tier = 1
    if "monthly_limit" not in kwargs:
        tier = kwargs.get("kyc_tier", 1)
        target.monthly_limit = TIER_LIMITS.get(tier, TIER_LIMITS[1])
    if "monthly_used" not in kwargs:
        target.monthly_used = Decimal("0")
    if "status" not in kwargs:
        target.status = TraderStatus.PENDING
    if "created_at" not in kwargs:
        target.created_at = datetime.now(timezone.utc)
    if "updated_at" not in kwargs:
        target.updated_at = datetime.now(timezone.utc)
