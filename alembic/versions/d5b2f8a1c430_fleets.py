"""fleets: licensed taxi fleets, rosters, and fleet-level weekly settlement

Revision ID: d5b2f8a1c430
Revises: c7a3e5b2f104
Create Date: 2026-09-29

Taxi fleet support (的士車隊):

- `fleets` — a licensed operator. Created by an admin, never self-service: the
  Transport Department grants the fleet licence, so onboarding is an operator
  action on the platform side.
- `fleet_memberships` — the roster. Rows are never deleted; `status` moves to
  LEFT/REMOVED and `left_at` is stamped, so which fleet a driver was billed
  under in a past week stays auditable.
- `fleet_settlement_runs` — the aggregate of one fleet's weekly settlement, one
  row per (fleet, ISO week).

Two partial UNIQUE indexes carry the correctness:

- `uq_fleet_active_member_per_driver` — a driver may be on at most ONE active
  roster. Without it a driver could be added to two fleets and billed by both.
- `uq_fleet_settlement_period` — one settlement row per fleet per week, so a
  re-run updates rather than appends.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d5b2f8a1c430"
down_revision: Union[str, Sequence[str], None] = "c7a3e5b2f104"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fleets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("license_no", sa.String(length=40), nullable=False),
        sa.Column("contact_phone", sa.String(length=20), nullable=True),
        sa.Column("contact_name", sa.String(length=80), nullable=True),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "SUSPENDED", "DISSOLVED", name="fleet_status", native_enum=False),
            nullable=False,
        ),
        sa.Column("weekly_fee_discount_percent", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("license_no"),
    )
    op.create_index(op.f("ix_fleets_status"), "fleets", ["status"], unique=False)

    op.create_table(
        "fleet_memberships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("fleet_id", sa.UUID(), nullable=False),
        sa.Column("driver_profile_id", sa.UUID(), nullable=False),
        sa.Column(
            "member_role",
            sa.Enum(
                "OWNER", "MANAGER", "MEMBER", name="fleet_member_role", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE", "LEFT", "REMOVED", name="fleet_member_status", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["fleet_id"], ["fleets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["driver_profile_id"], ["driver_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_fleet_memberships_fleet_id"), "fleet_memberships", ["fleet_id"], unique=False
    )
    op.create_index(
        op.f("ix_fleet_memberships_driver_profile_id"),
        "fleet_memberships",
        ["driver_profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fleet_memberships_status"), "fleet_memberships", ["status"], unique=False
    )
    # At most one active roster per driver.
    op.create_index(
        "uq_fleet_active_member_per_driver",
        "fleet_memberships",
        ["driver_profile_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        "fleet_settlement_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("fleet_id", sa.UUID(), nullable=False),
        sa.Column("period", sa.String(length=12), nullable=False),
        sa.Column("fee_hkd", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("discount_percent", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("member_count", sa.BigInteger(), nullable=False),
        sa.Column("charged", sa.BigInteger(), nullable=False),
        sa.Column("skipped", sa.BigInteger(), nullable=False),
        sa.Column("failed", sa.BigInteger(), nullable=False),
        sa.Column("tampered", sa.BigInteger(), nullable=False),
        sa.Column("collected_hkd", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["fleet_id"], ["fleets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fleet_id", "period", name="uq_fleet_settlement_period"),
    )
    op.create_index(
        op.f("ix_fleet_settlement_runs_fleet_id"),
        "fleet_settlement_runs",
        ["fleet_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fleet_settlement_runs_created_at"),
        "fleet_settlement_runs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_fleet_settlement_runs_created_at"), table_name="fleet_settlement_runs")
    op.drop_index(op.f("ix_fleet_settlement_runs_fleet_id"), table_name="fleet_settlement_runs")
    op.drop_table("fleet_settlement_runs")

    op.drop_index("uq_fleet_active_member_per_driver", table_name="fleet_memberships")
    op.drop_index(op.f("ix_fleet_memberships_status"), table_name="fleet_memberships")
    op.drop_index(
        op.f("ix_fleet_memberships_driver_profile_id"), table_name="fleet_memberships"
    )
    op.drop_index(op.f("ix_fleet_memberships_fleet_id"), table_name="fleet_memberships")
    op.drop_table("fleet_memberships")

    op.drop_index(op.f("ix_fleets_status"), table_name="fleets")
    op.drop_table("fleets")
