"""create matching_pool table

Revision ID: 004
Revises: 003
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # transactiondirection enum already exists from migration 003
    op.create_table(
        "matching_pool",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "transaction_id", UUID(as_uuid=True),
            sa.ForeignKey("transactions.id"), unique=True, nullable=False,
        ),
        sa.Column(
            "trader_id", UUID(as_uuid=True),
            sa.ForeignKey("traders.id"), nullable=False,
        ),
        sa.Column(
            "direction",
            sa.Enum("ngn_to_cny", "cny_to_ngn",
                    name="transactiondirection", create_type=False),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("priority_score", sa.Numeric(8, 4), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "entered_pool_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("matching_pool")
