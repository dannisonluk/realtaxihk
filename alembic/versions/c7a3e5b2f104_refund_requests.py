"""refund requests: driver-initiated deposit refund with admin decision

Revision ID: c7a3e5b2f104
Revises: b4f1c2a7d901
Create Date: 2026-09-29

Business gap closure — refund flow:
- `refund_requests` records a driver asking to withdraw their remaining deposit.
  Money is only *held* at request time (balance_hkd -> held_hkd); it actually
  leaves the platform only on admin approval, which writes the REFUND ledger
  entry. A rejection releases the hold.
- `uq_refund_pending_per_driver` is a partial UNIQUE index: a driver may have at
  most one PENDING request, so a double-submit cannot double-hold funds.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c7a3e5b2f104"
down_revision: Union[str, Sequence[str], None] = "b4f1c2a7d901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "refund_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("driver_profile_id", sa.UUID(), nullable=False),
        sa.Column("amount_hkd", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "APPROVED",
                "REJECTED",
                name="refund_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["driver_profile_id"], ["driver_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_refund_requests_driver_profile_id"),
        "refund_requests",
        ["driver_profile_id"],
        unique=False,
    )
    op.create_index(op.f("ix_refund_requests_status"), "refund_requests", ["status"], unique=False)
    # At most one open request per driver.
    op.create_index(
        "uq_refund_pending_per_driver",
        "refund_requests",
        ["driver_profile_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("uq_refund_pending_per_driver", table_name="refund_requests")
    op.drop_index(op.f("ix_refund_requests_status"), table_name="refund_requests")
    op.drop_index(op.f("ix_refund_requests_driver_profile_id"), table_name="refund_requests")
    op.drop_table("refund_requests")
