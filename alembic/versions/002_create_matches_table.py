"""create matches table

Revision ID: 002
Revises: 001
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    matchtype = sa.Enum("exact", "multi", "partial", name="matchtype")
    matchtype.create(op.get_bind(), checkfirst=True)

    matchstatus = sa.Enum(
        "pending_settlement", "settling", "settled", "failed",
        name="matchstatus",
    )
    matchstatus.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "matches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("cycle_id", sa.String(50), nullable=False),
        sa.Column(
            "buy_transaction_id", UUID(as_uuid=True),
            sa.ForeignKey("transactions.id"), nullable=False,
        ),
        sa.Column(
            "sell_transaction_id", UUID(as_uuid=True),
            sa.ForeignKey("transactions.id"), nullable=False,
        ),
        sa.Column("match_type", matchtype, nullable=False),
        sa.Column("matched_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("matched_rate", sa.Numeric(12, 6), nullable=False),
        sa.Column("status", matchstatus, server_default="pending_settlement", nullable=False),
        sa.Column("settlement_reference", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "matched_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("matches")
    sa.Enum(name="matchstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="matchtype").drop(op.get_bind(), checkfirst=True)
