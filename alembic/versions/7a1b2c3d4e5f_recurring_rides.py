"""recurring rides template table

Revision ID: 7a1b2c3d4e5f
Revises: f1c2d3e4a5b6
Create Date: 2026-10-05

Adds the passenger recurring-ride template table. The background minter creates
one `orders` row per due template and advances `next_run_at` in the same
transaction, so a crash cannot double-mint. Status/frequency use string
columns with CHECK constraints, matching the model's `SAEnum(native_enum=False)`
convention elsewhere in this repository.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7a1b2c3d4e5f"
down_revision = "f1c2d3e4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recurring_rides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "passenger_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column(
            "frequency",
            sa.String(length=16),
            nullable=False,
            server_default="WEEKLY",
        ),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("scheduled_time", sa.String(length=5), nullable=False),
        sa.Column("template_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "source_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "last_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "next_run_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "weekday BETWEEN 1 AND 7", name="ck_recurring_rides_weekday"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'PAUSED', 'CANCELLED')",
            name="ck_recurring_rides_status",
        ),
        sa.CheckConstraint(
            "frequency IN ('WEEKLY')",
            name="ck_recurring_rides_frequency",
        ),
    )
    op.create_index(
        "ix_recurring_rides_next_run_at",
        "recurring_rides",
        ["next_run_at"],
    )
    op.create_index(
        "ix_recurring_rides_passenger_id",
        "recurring_rides",
        ["passenger_id"],
    )
    op.create_index(
        "ix_recurring_rides_status_next_run",
        "recurring_rides",
        ["status", "next_run_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_recurring_rides_status_next_run", table_name="recurring_rides")
    op.drop_index("ix_recurring_rides_passenger_id", table_name="recurring_rides")
    op.drop_index("ix_recurring_rides_next_run_at", table_name="recurring_rides")
    op.drop_table("recurring_rides")