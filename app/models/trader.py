"""
Trader model — represents a registered trader on the platform.

Traders can be Nigerian exporters/importers or Chinese suppliers.
KYC verification is required before transactions are allowed.
"""

import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

import enum


class TraderType(str, enum.Enum):
    NIGERIAN_IMPORTER = "nigerian_importer"
    NIGERIAN_EXPORTER = "nigerian_exporter"
    CHINESE_SUPPLIER = "chinese_supplier"


class KYCLevel(str, enum.Enum):
    NONE = "none"
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class Trader(Base):
    __tablename__ = "traders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    business_name: Mapped[str | None] = mapped_column(String(255))
    trader_type: Mapped[TraderType] = mapped_column(
        SAEnum(TraderType), nullable=False
    )
    kyc_level: Mapped[KYCLevel] = mapped_column(
        SAEnum(KYCLevel), default=KYCLevel.NONE
    )
    bvn: Mapped[str | None] = mapped_column(String(11))
    nin: Mapped[str | None] = mapped_column(String(11))
    email: Mapped[str | None] = mapped_column(String(255))
    whatsapp_id: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    transactions = relationship("Transaction", back_populates="trader")

    def __repr__(self) -> str:
        return f"<Trader {self.business_name} ({self.phone})>"
