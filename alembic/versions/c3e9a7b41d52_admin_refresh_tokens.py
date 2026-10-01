"""admin refresh tokens — the console's session life (SEV-1)

One table, purely additive: `admin_refresh_tokens`.

Rationale for a table of its own rather than a row in `refresh_tokens`:
`refresh_tokens.user_id` is a `ForeignKey("users.id")`, and an admin id lives in
a different UUID space, so an admin token stored there would violate the FK. The
alternatives were a nullable FK whose meaning depends on a second column, or
dropping the FK and its `ondelete="RESTRICT"` guarantee; both trade a real
constraint for a saved table.

`csrf_hash` is the second half of the double-submit CSRF defence that makes the
`HttpOnly` cookie safe to attach automatically. It is stored (hashed) beside the
refresh token so the pair is issued and revoked together.

Revision ID: c3e9a7b41d52
Revises: a4b8e2f6c150
Create Date: 2026-10-02

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3e9a7b41d52"
down_revision: Union[str, Sequence[str], None] = "a4b8e2f6c150"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_refresh_tokens",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "admin_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("admin_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # sha256 hex of the raw token. Unique via a UNIQUE **index** rather than
        # a UNIQUE constraint plus a plain index: the model declares
        # `unique=True, index=True`, which SQLAlchemy renders as one unique
        # index, and a second non-unique index on the same column is a
        # redundant structure that also makes `alembic check` report drift.
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_admin_refresh_tokens_admin_id", "admin_refresh_tokens", ["admin_id"]
    )
    op.create_index(
        "ix_admin_refresh_tokens_token_hash",
        "admin_refresh_tokens",
        ["token_hash"],
        unique=True,
    )
    # The PDPO purge job deletes by age, so this one is read on every run.
    op.create_index(
        "ix_admin_refresh_tokens_expires_at", "admin_refresh_tokens", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_admin_refresh_tokens_expires_at", table_name="admin_refresh_tokens")
    op.drop_index("ix_admin_refresh_tokens_token_hash", table_name="admin_refresh_tokens")
    op.drop_index("ix_admin_refresh_tokens_admin_id", table_name="admin_refresh_tokens")
    op.drop_table("admin_refresh_tokens")
