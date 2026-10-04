"""a claimed-not-verified phone, and reviewer-account expiry

Three changes to `users`, all of them consequences of separating *signing in*
from *proving a phone number*:

1. `phone_e164` loses its plain UNIQUE. Registration still requires a number,
   but it no longer has to be the registrant's own, so several accounts may
   claim the same one. Keeping UNIQUE would make the first claimer the
   permanent owner and refuse registration to the real owner — a
   denial-of-registration vector, and a cheap one, because a phone number is
   public information.
2. `uq_users_phone_e164_verified` — a **partial** unique index that puts the
   guarantee back where it actually matters: at most one account may *verify* a
   given number. `ix_users_phone_e164` is recreated as a plain (non-unique)
   index so the by-number lookup the secondary OTP login performs is still
   indexed rather than a sequential scan.
3. `users.reviewer_expires_at` — the expiry of a restricted reviewer account
   (`scripts/ops/create_reviewer_account.py`). NULL means "not a reviewer".

The pair (2)+(3) is why this is a migration and not a model-only change: the
partial index is the thing that makes phone binding race-safe, and it cannot be
expressed as a table constraint.

Downgrade note: re-creating the plain UNIQUE on `phone_e164` will FAIL if any
two rows already claim the same number. That is honest rather than a bug — the
data this migration permits genuinely cannot satisfy the older schema — but it
means a downgrade on a live database needs a de-duplication step first.

Revision ID: b7d4e1c9a3f2
Revises: 2e276a320b35
Create Date: 2026-10-04

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d4e1c9a3f2"
down_revision: Union[str, Sequence[str], None] = "2e276a320b35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Dropped and recreated under the same name rather than altered in place:
    # Postgres has no `ALTER INDEX ... DROP UNIQUE`, so the only way to relax
    # uniqueness is to rebuild. The name is kept because every existing
    # by-number query and the model both expect `ix_users_phone_e164`.
    op.drop_index("ix_users_phone_e164", table_name="users")
    op.create_index("ix_users_phone_e164", "users", ["phone_e164"], unique=False)

    # The authority on "exactly one verified owner per number". `postgresql_where`
    # is what makes it partial; a plain unique index here would reject the second
    # *unverified* claimant, which is the bug this migration exists to remove.
    op.create_index(
        "uq_users_phone_e164_verified",
        "users",
        ["phone_e164"],
        unique=True,
        postgresql_where=sa.text("phone_verified_at IS NOT NULL"),
    )

    op.add_column(
        "users",
        sa.Column("reviewer_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "reviewer_expires_at")
    op.drop_index("uq_users_phone_e164_verified", table_name="users")
    op.drop_index("ix_users_phone_e164", table_name="users")
    # Restores the old uniqueness. Fails loudly on duplicate claims — see the
    # module docstring; de-duplicate first.
    op.create_index("ix_users_phone_e164", "users", ["phone_e164"], unique=True)
