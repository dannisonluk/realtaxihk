"""refund requests: partial-withdrawal flag (P2-2)

Revision ID: a7b3c1d2e4f6
Revises: e6f7c3d9e5a9
Create Date: 2026-10-08

Drivers can now claim a *partial* refund — withdraw part of the balance while
staying on the platform. The payout amount was already stored on the row; what
the decision flow needs is an explicit marker of whether the claim was partial,
because the two kinds end differently:

* full refund (is_partial = false) — approval pays out and **terminates** the
  driver (existing behaviour, unchanged);
* partial refund (is_partial = true) — approval pays out the requested amount,
  releases the rest of the hold, and returns the driver to ACTIVE.

The flag is written at request time, when the semantics are unambiguous; it is
never inferred at decision time from balances, because admin grants or fees
landing in between would make that read unreliable.

Backfill: every existing row is a full refund, so the server default `false`
is exactly right and no data migration is needed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b3c1d2e4f6"
down_revision: str | Sequence[str] | None = "e6f7c3d9e5a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "refund_requests",
        sa.Column(
            "is_partial",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("refund_requests", "is_partial")
