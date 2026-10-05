"""order receipt request

Revision ID: 5e1a9c7d4b02
Revises: 042a7bc3e54c
Create Date: 2026-10-05 14:20:00.000000

Adds the passenger-facing receipt request to `orders`:

- `receipt_requested`      — the passenger asked for a receipt (default false).
- `receipt_requested_at`   — when they asked, for the audit trail.
- `receipt_snapshot_json`  — the frozen receipt document, written once at
  request time and never recomputed.

Why a frozen snapshot rather than a render-on-read:

The platform is an information intermediary (Cap. 374D). A receipt is a record
of what an order *was priced at and settled for*, so re-rendering it from live
rows would let a later tariff change, a driver attribute edit or a ledger
adjustment silently rewrite a document the passenger already holds. The three
money fields (`receipt_total_hkd`, and the fixed-fare split) therefore live in
the snapshot, not in the response builder.

All three columns are nullable/defaulted, so existing orders and every existing
fixture and client decode unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e1a9c7d4b02"
down_revision: str | Sequence[str] | None = "042a7bc3e54c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "orders",
        sa.Column(
            "receipt_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "orders",
        sa.Column("receipt_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    # JSONB, matching every other frozen snapshot on this table (`fare_json`,
    # `requirements_json`, ...). No server default: NULL means "not requested",
    # which is different from "requested and the document is empty".
    op.add_column(
        "orders",
        sa.Column("receipt_snapshot_json", sa.dialects.postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("orders", "receipt_snapshot_json")
    op.drop_column("orders", "receipt_requested_at")
    op.drop_column("orders", "receipt_requested")
