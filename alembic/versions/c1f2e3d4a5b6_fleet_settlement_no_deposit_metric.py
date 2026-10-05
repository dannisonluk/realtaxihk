"""fleet settlement no-deposit metric

Revision ID: c1f2e3d4a5b6
Revises: 5e1a9c7d4b02
Create Date: 2026-10-05 14:20:00.000000

Separates members skipped from the weekly fee because they have no fulfilled
deposit from members actually charged and then skipped (already charged in the
same period). Previously both were collapsed into `skipped`, so an operator
reviewing a fleet run could not tell "no money moved, no deposit on file" from
"money had already moved for this period".

The new column is a defensive report metric only; it is not used by any
charging query.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1f2e3d4a5b6"
down_revision: str | Sequence[str] | None = "5e1a9c7d4b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fleet_settlement_runs",
        sa.Column(
            "skipped_no_deposit_account",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("fleet_settlement_runs", "skipped_no_deposit_account")