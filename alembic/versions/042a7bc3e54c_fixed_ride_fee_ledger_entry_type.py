"""fixed ride fee ledger entry type

Revision ID: 042a7bc3e54c
Revises: 7a1b2c3d4e5f
Create Date: 2026-10-05 13:15:52.409933

Adds `FIXED_RIDE_FEE` to the `ledger_entries.entry_type` CHECK constraint.
The enum member is implemented as a string column plus a database CHECK
constraint (the repo-wide convention for `SAEnum(native_enum=False)`), so the
revision drops the existing constraint and recreates it with the new member.
Downgrade restores the original allowed set.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "042a7bc3e54c"
down_revision: str | Sequence[str] | None = "7a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEDGER_ENTRY_TYPES_BEFORE = (
    "DEPOSIT_TOPUP",
    "WEEKLY_FEE_DEDUCTION",
    "PENALTY_DEDUCTION",
    "REFUND",
    "ADJUSTMENT",
)
_LEDGER_ENTRY_TYPES_AFTER = (*_LEDGER_ENTRY_TYPES_BEFORE, "FIXED_RIDE_FEE")


def _entry_type_check(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"entry_type IN ({joined})"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        "ck_ledger_entries_entry_type",
        "ledger_entries",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ledger_entries_entry_type",
        "ledger_entries",
        _entry_type_check(_LEDGER_ENTRY_TYPES_AFTER),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_ledger_entries_entry_type",
        "ledger_entries",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ledger_entries_entry_type",
        "ledger_entries",
        _entry_type_check(_LEDGER_ENTRY_TYPES_BEFORE),
    )
