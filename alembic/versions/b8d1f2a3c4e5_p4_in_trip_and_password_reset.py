"""P4 in-trip lifecycle, and the password-reset token

Three things, all additive or a widening of an existing CHECK — no row is
rewritten and no backfill is needed:

* **`orders` gains the in-trip columns** (`docs/IN_TRIP_REDESIGN.md` §3.1). The
  arrival proof gets *two* timestamps on purpose: `arrival_claimed_at` is "the
  driver says so" and `arrival_confirmed_at` is "the passenger agreed". Only the
  second locks the cancel right, so they cannot be one column.
* **`order_events`** — the per-order timeline (§3.4), distinct from
  `admin_audit_log`: that answers "which operator did what", this answers "what
  happened to this trip".
* **`password_reset_tokens`** — the forgot-password flow, shaped like
  `email_verification_tokens` (SHA-256 digest, single-use, expiring).

The two CHECK widenings are the repo-wide `SAEnum(native_enum=False)` pattern:
adding a member is a `drop_constraint` + `create_check_constraint`, never an
`ALTER TYPE`. `042a7bc3e54c` is the precedent.

Revision ID: b8d1f2a3c4e5
Revises: c1f2e3d4a5b6
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b8d1f2a3c4e5"
down_revision: str | Sequence[str] | None = "c1f2e3d4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ORDER_STATUS_BEFORE = (
    "CREATED",
    "BROADCASTING",
    "ACCEPTED",
    "DRIVER_ARRIVED",
    "IN_TRIP",
    "COMPLETED",
    "CANCELLED",
)
_ORDER_STATUS_AFTER = (
    "CREATED",
    "BROADCASTING",
    "ACCEPTED",
    "PENDING_ARRIVAL_CONFIRM",
    "DRIVER_ARRIVED",
    "IN_TRIP",
    "DESTINATION_CHANGED",
    "INTERRUPTED",
    "COMPLETED",
    "CANCELLED",
)

_LEDGER_ENTRY_TYPES_BEFORE = (
    "DEPOSIT_TOPUP",
    "WEEKLY_FEE_DEDUCTION",
    "PENALTY_DEDUCTION",
    "REFUND",
    "ADJUSTMENT",
    "FIXED_RIDE_FEE",
)
_LEDGER_ENTRY_TYPES_AFTER = (
    *_LEDGER_ENTRY_TYPES_BEFORE,
    "PLATFORM_TRIP_FEE",
    "CANCELLATION_PENALTY",
    "DISPUTE_ADJUSTMENT",
)

_INTERRUPTION_REASONS = (
    "ACCIDENT",
    "CONFLICT",
    "PASSENGER_MISCONDUCT",
    "PASSENGER_SICK",
    "DRIVER_MISCONDUCT",
    "VEHICLE_BREAKDOWN",
    "UNSAFE_ROUTE",
    "FARE_DISPUTE",
    "OTHER",
)

_ORDER_EVENT_TYPES = (
    "STATE_CHANGED",
    "ARRIVAL_CLAIMED",
    "ARRIVAL_CONFIRMED",
    "DEST_CHANGED",
    "FEE_CHARGED",
    "PENALTY_CHARGED",
    "INTERRUPTED",
    "DISPUTE_OPENED",
)


def _in_check(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    """Upgrade schema."""
    # --- orders: P4 columns ------------------------------------------------ #
    op.add_column(
        "orders", sa.Column("arrival_claimed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("orders", sa.Column("arrival_gps_distance_m", sa.Numeric(7, 1), nullable=True))
    op.add_column(
        "orders", sa.Column("arrival_confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "orders",
        sa.Column("arrival_pin_attempts", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column("orders", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "orders", sa.Column("platform_fee_charged_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("orders", sa.Column("original_dropoff_address", sa.Text(), nullable=True))
    op.add_column(
        "orders",
        sa.Column(
            "original_dropoff_location",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "orders", sa.Column("destination_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "orders",
        sa.Column(
            "destination_change_count", sa.SmallInteger(), nullable=False, server_default="0"
        ),
    )
    op.add_column("orders", sa.Column("interruption_reason", sa.String(length=32), nullable=True))
    op.add_column("orders", sa.Column("interrupted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("interrupted_by_kind", sa.String(length=16), nullable=True))

    # --- widen the order-status CHECK ------------------------------------- #
    op.drop_constraint("ck_orders_status", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_status", "orders", _in_check("status", _ORDER_STATUS_AFTER)
    )
    # The model derives the column width from the longest enum member
    # (`PENDING_ARRIVAL_CONFIRM`, 23); the old column was sized for
    # `DRIVER_ARRIVED` (14). Without this the migrated schema and the models
    # disagree on width, which `test_migration_schema_parity` reports as a type
    # change. `native_enum=False` means the type stays VARCHAR either way.
    op.alter_column(
        "orders",
        "status",
        existing_type=sa.String(length=14),
        type_=sa.String(length=23),
        existing_nullable=False,
    )

    # New closed sets carried as CHECK constraints, per the repo convention.
    op.create_check_constraint(
        "ck_orders_interruption_reason",
        "orders",
        f"interruption_reason IS NULL OR {_in_check('interruption_reason', _INTERRUPTION_REASONS)}",
    )
    op.create_check_constraint(
        "ck_orders_interrupted_by_kind",
        "orders",
        "interrupted_by_kind IS NULL OR interrupted_by_kind IN ('PASSENGER', 'DRIVER')",
    )

    # --- widen the ledger entry-type CHECK -------------------------------- #
    op.drop_constraint("ck_ledger_entries_entry_type", "ledger_entries", type_="check")
    op.create_check_constraint(
        "ck_ledger_entries_entry_type",
        "ledger_entries",
        _in_check("entry_type", _LEDGER_ENTRY_TYPES_AFTER),
    )

    # --- order_events ------------------------------------------------------ #
    op.create_table(
        "order_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=True),
        sa.Column("actor_kind", sa.String(length=16), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_in_check("event", _ORDER_EVENT_TYPES), name="ck_order_events_event"),
    )
    op.create_index("ix_order_events_order_id", "order_events", ["order_id"])
    op.create_index("ix_order_events_created_at", "order_events", ["created_at"])
    op.create_index("ix_order_events_order_time", "order_events", ["order_id", "created_at"])

    # --- password_reset_tokens -------------------------------------------- #
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_ip", sa.String(length=45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    op.create_index(
        "ix_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"], unique=True
    )
    op.create_index("ix_password_reset_tokens_expires_at", "password_reset_tokens", ["expires_at"])
    op.create_index("ix_password_reset_tokens_created_at", "password_reset_tokens", ["created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_password_reset_tokens_created_at", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_expires_at", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_token_hash", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")

    op.drop_index("ix_order_events_order_time", table_name="order_events")
    op.drop_index("ix_order_events_created_at", table_name="order_events")
    op.drop_index("ix_order_events_order_id", table_name="order_events")
    op.drop_table("order_events")

    op.drop_constraint("ck_ledger_entries_entry_type", "ledger_entries", type_="check")
    op.create_check_constraint(
        "ck_ledger_entries_entry_type",
        "ledger_entries",
        _in_check("entry_type", _LEDGER_ENTRY_TYPES_BEFORE),
    )

    op.drop_constraint("ck_orders_interrupted_by_kind", "orders", type_="check")
    op.drop_constraint("ck_orders_interruption_reason", "orders", type_="check")
    op.drop_constraint("ck_orders_status", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_status", "orders", _in_check("status", _ORDER_STATUS_BEFORE)
    )
    op.alter_column(
        "orders",
        "status",
        existing_type=sa.String(length=23),
        type_=sa.String(length=14),
        existing_nullable=False,
    )

    for column in (
        "interrupted_by_kind",
        "interrupted_at",
        "interruption_reason",
        "destination_change_count",
        "destination_changed_at",
        "original_dropoff_location",
        "original_dropoff_address",
        "platform_fee_charged_at",
        "started_at",
        "arrival_pin_attempts",
        "arrival_confirmed_at",
        "arrival_gps_distance_m",
        "arrival_claimed_at",
    ):
        op.drop_column("orders", column)
