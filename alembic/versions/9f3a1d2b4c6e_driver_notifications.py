"""Driver in-app notification inbox.

Revision ID: 9f3a1d2b4c6e
Revises: b8d1f2a3c4e5
Create Date: 2026-10-07

Premium/fixed-fare orders create rows here so drivers see a badge after a
disconnect as well as while online. External push is intentionally not part of
this migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9f3a1d2b4c6e"
down_revision: str | Sequence[str] | None = "b8d1f2a3c4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "driver_notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("driver_profile_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("headline_zh", sa.String(length=120), nullable=False),
        sa.Column("headline_en", sa.String(length=120), nullable=False),
        sa.Column("body_zh", sa.String(length=500), nullable=False),
        sa.Column("body_en", sa.String(length=500), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["driver_profile_id"],
            ["driver_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_driver_notifications_profile_created",
        "driver_notifications",
        ["driver_profile_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_driver_notifications_order_id",
        "driver_notifications",
        ["order_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_driver_notifications_order_id",
        table_name="driver_notifications",
    )
    op.drop_index(
        "ix_driver_notifications_profile_created",
        table_name="driver_notifications",
    )
    op.drop_table("driver_notifications")
