"""DECISION-3: explicit manual release after arrears are topped up.

Revision ID: e5f6b2c9d4a8
Revises: e2f6b1c9d4a7
Create Date: 2026-10-07 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e5f6b2c9d4a8"
down_revision = "e2f6b1c9d4a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "driver_deposits",
        sa.Column(
            "acceptance_unlocked_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
    )
    # Rows already in arrears must stay locked until an operator explicitly
    # releases them. A server-default of now() is correct for every other row:
    # it means a driver who has never defaulted is not suddenly locked.
    op.execute(
        "UPDATE driver_deposits SET acceptance_unlocked_at = NULL WHERE balance_hkd + held_hkd < 0"
    )


def downgrade() -> None:
    op.drop_column("driver_deposits", "acceptance_unlocked_at")
