"""prod hardening: refresh_tokens table + ledger reference idempotency index

Revision ID: b4f1c2a7d901
Revises: 9307e944a592
Create Date: 2026-09-29

Part of the MVP->production hardening wave (docs/PRODUCTION_READINESS.md):
- P1-5 refresh tokens (rotating, hashed at rest);
- P1-7 idempotency: partial UNIQUE index on ledger_entries.reference —
  a retried admin grant with the same reference can never double-credit.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b4f1c2a7d901"
down_revision: Union[str, Sequence[str], None] = "9307e944a592"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])

    # Idempotency guard: one ledger entry per non-null reference (e.g. grant key).
    op.create_index(
        "uq_ledger_reference",
        "ledger_entries",
        ["reference"],
        unique=True,
        postgresql_where=sa.text("reference IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_ledger_reference", table_name="ledger_entries")
    op.drop_index("ix_refresh_tokens_expires_at", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
