"""Add missing FK index for orders.dropoff_landmark_id.

Revision ID: e6f7c3d9e5a9
Revises: e5f6b2c9d4a8
Create Date: 2026-10-07 00:10:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "e6f7c3d9e5a9"
down_revision = "e5f6b2c9d4a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_orders_dropoff_landmark_id",
        "orders",
        ["dropoff_landmark_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_orders_dropoff_landmark_id", table_name="orders")
