"""create transactions table

Revision ID: 003
Revises: 002
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    transactiondirection = sa.Enum("ngn_to_cny", "cny_to_ngn", name="transactiondirection")
    transactiondirection.create(op.get_bind(), checkfirst=True)

    transactionstatus = sa.Enum(
        "initiated", "funded", "matching", "matched", "partial_matched",
        "pending_settlement", "settling", "completed", "failed",
        "refunded", "cancelled", "expired",
        name="transactionstatus",
    )
    transactionstatus.create(op.get_bind(), checkfirst=True)

    settlementmethod = sa.Enum(
        "matched", "partial_matched", "cips_settled",
        name="settlementmethod",
    )
    settlementmethod.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "transactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("reference", sa.String(16), unique=True, index=True, nullable=False),
        sa.Column(
            "trader_id", UUID(as_uuid=True),
            sa.ForeignKey("traders.id"), nullable=False,
        ),
        sa.Column("direction", transactiondirection, nullable=False),
        sa.Column("source_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("target_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("exchange_rate", sa.Numeric(12, 6), nullable=True),
        sa.Column("fee_amount", sa.Numeric(18, 2), server_default="0", nullable=False),
        sa.Column("fee_percentage", sa.Numeric(5, 4), server_default="0", nullable=False),
        sa.Column("supplier_name", sa.String(200), nullable=True),
        sa.Column("supplier_bank", sa.String(100), nullable=True),
        sa.Column("supplier_account", sa.String(256), nullable=True),
        sa.Column("invoice_url", sa.String(500), nullable=True),
        sa.Column("status", transactionstatus, server_default="initiated", nullable=False),
        sa.Column(
            "match_id", UUID(as_uuid=True),
            sa.ForeignKey("matches.id"), nullable=True,
        ),
        sa.Column("settlement_method", settlementmethod, nullable=True),
        sa.Column("funded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.CheckConstraint("source_amount > 0", name="ck_transactions_source_positive"),
    )


def downgrade() -> None:
    op.drop_table("transactions")
    sa.Enum(name="settlementmethod").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="transactionstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="transactiondirection").drop(op.get_bind(), checkfirst=True)
