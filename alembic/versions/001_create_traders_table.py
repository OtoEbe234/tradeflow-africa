"""create traders table

Revision ID: 001
Revises:
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the TraderStatus enum type
    traderstatus = sa.Enum(
        "pending", "active", "suspended", "blocked",
        name="traderstatus",
    )
    traderstatus.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "traders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("phone", sa.String(15), unique=True, index=True, nullable=False),
        sa.Column("tradeflow_id", sa.String(10), unique=True, nullable=False),
        sa.Column("full_name", sa.String(100), nullable=False),
        sa.Column("business_name", sa.String(200), nullable=True),
        sa.Column("bvn", sa.String(256), nullable=True),
        sa.Column("nin", sa.String(256), nullable=True),
        sa.Column("cac_number", sa.String(20), nullable=True),
        sa.Column("kyc_tier", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "monthly_limit",
            sa.Numeric(precision=18, scale=2),
            server_default="5000",
            nullable=False,
        ),
        sa.Column(
            "monthly_used",
            sa.Numeric(precision=18, scale=2),
            server_default="0",
            nullable=False,
        ),
        sa.Column("pin_hash", sa.String(256), nullable=True),
        sa.Column(
            "status",
            traderstatus,
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "referred_by",
            UUID(as_uuid=True),
            sa.ForeignKey("traders.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kyc_tier >= 1 AND kyc_tier <= 3",
            name="ck_traders_kyc_tier",
        ),
    )


def downgrade() -> None:
    op.drop_table("traders")

    # Drop the enum type
    sa.Enum(name="traderstatus").drop(op.get_bind(), checkfirst=True)
