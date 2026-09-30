"""admin accounts with TOTP, and a registration identity for users

Revision ID: e8f4a1c9b720
Revises: d5b2f8a1c430
Create Date: 2026-09-30

Two changes, one migration: admin authentication, and the identity columns the
registration flow (P-2) will populate.

**Admin accounts are a separate table, not a role on `users`.** The full
reasoning is in `app/models/__init__.py`; the migration-relevant part is that
`users.phone_e164` is NOT NULL and UNIQUE, so an admin without a phone number
cannot be a row there. A `role = 'ADMIN'` row would need a fake or nullable
phone, and nullable-means-role-dependent is the shape that eventually produces
an admin with no credential at all.

**Every new `users` column is nullable, and that is deliberate.** The table
predates username/email/password: existing rows were created by phone-OTP and
have none of them. A NOT NULL would fail the migration on any non-empty
database, and the grandfathering path (a returning user claims credentials on
next login) is what fills them in. `account_status` is the one exception — it
is backfilled to ACTIVE for existing rows, because those accounts are already
in use and marking them UNVERIFIED would lock out every current user. New rows
default to UNVERIFIED.

The backfill is why `account_status` is added with a server_default and then
the default dropped: SQLite-free Postgres would accept `NOT NULL DEFAULT` in one
step, but leaving the server default in place would let a future INSERT that
omits the column silently create an ACTIVE account — the wrong failure.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8f4a1c9b720"
down_revision: Union[str, Sequence[str], None] = "d5b2f8a1c430"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. Admin accounts
    # ------------------------------------------------------------------ #
    op.create_table(
        "admin_accounts",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(32), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("full_name", sa.String(120), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        # NULL until first-login enrolment completes — this is what makes the
        # login flow a state machine rather than a single call.
        sa.Column("totp_secret", sa.String(64), nullable=True),
        sa.Column("totp_enrolled_at", sa.DateTime(timezone=True), nullable=True),
        # Replay guard: the last accepted 30s step.
        sa.Column("totp_last_counter", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # Uniqueness is what stops two admins sharing a login identifier. Named
    # explicitly (rather than left to the index-name generator) so a later
    # migration can reference or drop them predictably.
    op.create_index("uq_admin_accounts_username", "admin_accounts", ["username"], unique=True)
    op.create_index("uq_admin_accounts_email", "admin_accounts", ["email"], unique=True)

    op.create_table(
        "admin_recovery_codes",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "admin_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("admin_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False),
        # `used_at` rather than a delete: "this code was used" is the finding
        # you want when investigating an account takeover.
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_admin_recovery_codes_admin_id", "admin_recovery_codes", ["admin_id"])
    op.create_index("ix_admin_recovery_codes_code_hash", "admin_recovery_codes", ["code_hash"])
    op.create_unique_constraint(
        "uq_admin_recovery_code", "admin_recovery_codes", ["admin_id", "code_hash"]
    )

    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        # Nullable: a failed login for a non-existent username must still be
        # recorded, or credential stuffing leaves no trace.
        sa.Column("admin_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("username_attempted", sa.String(64), nullable=True),
        sa.Column("event", sa.String(48), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("detail", sa.String(255), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_admin_audit_log_admin_id", "admin_audit_log", ["admin_id"])
    op.create_index("ix_admin_audit_log_event", "admin_audit_log", ["event"])
    op.create_index("ix_admin_audit_log_created_at", "admin_audit_log", ["created_at"])

    # ------------------------------------------------------------------ #
    # 2. Registration identity on users (P-2 foundation)
    # ------------------------------------------------------------------ #
    # All nullable: existing rows have none of these, and the grandfathering
    # path fills them in when the user next logs in.
    op.add_column("users", sa.Column("username", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("given_name", sa.String(60), nullable=True))
    op.add_column("users", sa.Column("family_name", sa.String(60), nullable=True))
    op.add_column("users", sa.Column("gender", sa.String(12), nullable=True))
    op.add_column("users", sa.Column("avatar_key", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(254), nullable=True))
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    op.add_column(
        "users", sa.Column("phone_reverify_due_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))

    # Partial unique indexes. A plain UNIQUE on a nullable column permits any
    # number of NULLs in Postgres, which is what we want, but it also means a
    # UNIQUE index would be scanned for NULL-lookups; a partial index on
    # `IS NOT NULL` is both smaller and exactly the constraint intended.
    op.create_index(
        "uq_users_username",
        "users",
        ["username"],
        unique=True,
        postgresql_where=sa.text("username IS NOT NULL"),
    )
    op.create_index(
        "uq_users_email",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )

    # account_status: backfill existing rows as ACTIVE (they are already in
    # use — marking them UNVERIFIED would lock every current user out), then
    # drop the server default so a future INSERT that omits it cannot silently
    # produce an ACTIVE account.
    op.add_column(
        "users",
        sa.Column("account_status", sa.String(16), nullable=False, server_default="ACTIVE"),
    )
    op.alter_column("users", "account_status", server_default=None)

    # Grandfathered accounts are given a re-verification deadline rather than
    # left NULL. A NULL is treated as due by the guard (fail-closed), so this
    # is informational — but it makes the queue visible instead of implicit.
    op.execute(
        "UPDATE users SET phone_reverify_due_at = now() + interval '30 days' "
        "WHERE phone_reverify_due_at IS NULL"
    )


def downgrade() -> None:
    op.drop_index("uq_users_email", table_name="users")
    op.drop_index("uq_users_username", table_name="users")
    op.drop_column("users", "account_status")
    for col in (
        "locked_until",
        "failed_login_count",
        "phone_reverify_due_at",
        "password_hash",
        "email_verified_at",
        "email",
        "avatar_key",
        "gender",
        "family_name",
        "given_name",
        "username",
    ):
        op.drop_column("users", col)

    op.drop_index("ix_admin_audit_log_created_at", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_event", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_admin_id", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")

    op.drop_constraint("uq_admin_recovery_code", "admin_recovery_codes", type_="unique")
    op.drop_index("ix_admin_recovery_codes_code_hash", table_name="admin_recovery_codes")
    op.drop_index("ix_admin_recovery_codes_admin_id", table_name="admin_recovery_codes")
    op.drop_table("admin_recovery_codes")

    op.drop_index("uq_admin_accounts_email", table_name="admin_accounts")
    op.drop_index("uq_admin_accounts_username", table_name="admin_accounts")
    op.drop_table("admin_accounts")
