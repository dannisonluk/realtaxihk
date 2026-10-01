"""admin RBAC, and structured audit payloads

Two additive columns, no new tables:

* `admin_accounts.role` — the coarse role that backends every `require_role`
  check. Stored as `VARCHAR(16)` with a server default of `'SUPPORT'`.

* `admin_audit_log.payload` — nullable `JSONB` for events whose interesting
  content is a set of changed values rather than a sentence. `detail` stays
  `VARCHAR(255)` for the human-readable summary.

The backfill is the part worth reading. **Existing admin accounts are set to
`SUPER_ADMIN`, not the column default.** A role hierarchy with nobody at the
top is a deadlock: `SUPER_ADMIN` is the only role allowed to grant roles, so
if every existing account landed on `SUPPORT` the system would come up with no
one able to assign anything, and the only way back would be hand-editing the
database — which is precisely the operational failure that makes role rollouts
get abandoned halfway.

So the migration grants the top role to everyone present, and that is
deliberately a *starting* state, not the intended end state. **The next step
after deploying this is to lower every account except one**, using
`PATCH /api/v1/admin/accounts/{id}/role`. Until that is done the role column
is present but not enforcing anything meaningful, because every account
outranks every threshold. Do not stop after the migrate.

Revision ID: d7f3b21a6e08
Revises: c3e9a7b41d52
Create Date: 2026-10-02

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7f3b21a6e08"
down_revision: Union[str, Sequence[str], None] = "c3e9a7b41d52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "admin_accounts",
        sa.Column(
            "role",
            sa.String(length=16),
            nullable=False,
            server_default="SUPPORT",
        ),
    )
    op.create_index("ix_admin_accounts_role", "admin_accounts", ["role"])

    # Backfill BEFORE anything reads the column. See the module docstring for
    # why this is SUPER_ADMIN: a hierarchy with an empty top rank cannot be
    # repaired from inside the application.
    op.execute("UPDATE admin_accounts SET role = 'SUPER_ADMIN'")

    op.add_column(
        "admin_audit_log",
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_audit_log", "payload")
    op.drop_index("ix_admin_accounts_role", table_name="admin_accounts")
    op.drop_column("admin_accounts", "role")
