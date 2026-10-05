"""harden deposit and refund FKs to RESTRICT

Revision ID: f1c2d3e4a5b6
Revises: 8f2a1c5d3b40
Create Date: 2026-10-05

The ledger is append-only and money records must not silently vanish when a
driver profile (or, by cascade, a user) is deleted. `driver_deposits` holds a
paid balance and `refund_requests` can hold an approved payout decision;
both used `ON DELETE CASCADE`, so deleting a profile could erase them even
though `ledger_entries.driver_profile_id` is RESTRICT.

There is no product delete-user path today, only fixture/script cleanup, so
RESTRICT changes no live workflow; it makes the money invariant true at the
database instead of by convention.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f1c2d3e4a5b6"
down_revision: str | Sequence[str] | None = "8f2a1c5d3b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "driver_deposits_driver_profile_id_fkey",
        "driver_deposits",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "driver_deposits_driver_profile_id_fkey",
        "driver_deposits",
        "driver_profiles",
        ["driver_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "refund_requests_driver_profile_id_fkey",
        "refund_requests",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "refund_requests_driver_profile_id_fkey",
        "refund_requests",
        "driver_profiles",
        ["driver_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "driver_deposits_driver_profile_id_fkey",
        "driver_deposits",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "driver_deposits_driver_profile_id_fkey",
        "driver_deposits",
        "driver_profiles",
        ["driver_profile_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "refund_requests_driver_profile_id_fkey",
        "refund_requests",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "refund_requests_driver_profile_id_fkey",
        "refund_requests",
        "driver_profiles",
        ["driver_profile_id"],
        ["id"],
        ondelete="CASCADE",
    )
