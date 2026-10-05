"""premium destinations + driver payment methods + order requirements

Revision ID: 5c8b2f0a1e43
Revises: b7d4e1c9a3f2
Create Date: 2026-10-05

Phase 1 foundation tables/columns for the feature expansion:

- ``premium_destinations`` is admin-managed map metadata. It uses plain lat/lng
  + radius (no PostGIS type on purpose): the current dependency set already has
  GeoAlchemy2 for driver/order points, but this feature needs a simple haversine
  matcher and a pin, not a full spatial index. The order itself stays the centre
  of truth for what was detected.
- ``driver_payment_methods`` is a small PK table so the driver can declare
  multiple methods and the passenger can see them during ride selection.
- ``driver_profiles.in_car_environment_json`` and ``orders.requirements_json``
  are JSONB with API-validated keys, so the flag set can grow without migration
  churn while older clients still decode the order shape.
- New ``orders`` columns are nullable and additive; existing orders and fixtures
  remain valid.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c8b2f0a1e43"
down_revision: str | Sequence[str] | None = "b7d4e1c9a3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "premium_destinations",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name_zh", sa.String(120), nullable=False),
        sa.Column("name_en", sa.String(120), nullable=False),
        sa.Column("lat", sa.Numeric(9, 6), nullable=False),
        sa.Column("lng", sa.Numeric(9, 6), nullable=False),
        sa.Column("radius_m", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("avatar_key", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_premium_destinations_code", "premium_destinations", ["code"], unique=True)
    op.create_index("ix_premium_destinations_status", "premium_destinations", ["status"])

    op.create_table(
        "driver_payment_methods",
        sa.Column("driver_profile_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("method", sa.String(24), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["driver_profile_id"], ["driver_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("driver_profile_id", "method"),
    )
    op.create_unique_constraint(
        "uq_driver_payment_method",
        "driver_payment_methods",
        ["driver_profile_id", "method"],
    )
    op.create_index(
        "ix_driver_payment_methods_driver_profile_id",
        "driver_payment_methods",
        ["driver_profile_id"],
    )

    op.add_column(
        "driver_profiles",
        sa.Column(
            "in_car_environment_json", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "requirements_json", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "payment_preference_json", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "driver_payment_methods_json",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column(
        "orders", sa.Column("premium_destination_id", sa.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "orders",
        sa.Column(
            "premium_destination_json", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True
        ),
    )
    op.add_column("orders", sa.Column("destination_area", sa.String(24), nullable=True))
    op.create_foreign_key(
        "fk_orders_premium_destination_id",
        "orders",
        "premium_destinations",
        ["premium_destination_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_orders_premium_destination_id", "orders", ["premium_destination_id"])


def downgrade() -> None:
    op.drop_index("ix_orders_premium_destination_id", table_name="orders")
    op.drop_constraint("fk_orders_premium_destination_id", "orders", type_="foreignkey")
    op.drop_column("orders", "destination_area")
    op.drop_column("orders", "premium_destination_json")
    op.drop_column("orders", "premium_destination_id")
    op.drop_column("orders", "driver_payment_methods_json")
    op.drop_column("orders", "payment_preference_json")
    op.drop_column("orders", "requirements_json")
    op.drop_column("driver_profiles", "in_car_environment_json")

    op.drop_constraint("uq_driver_payment_method", "driver_payment_methods", type_="unique")
    op.drop_index(
        "ix_driver_payment_methods_driver_profile_id",
        table_name="driver_payment_methods",
    )
    op.drop_table("driver_payment_methods")

    op.drop_index("ix_premium_destinations_status", table_name="premium_destinations")
    op.drop_index("ix_premium_destinations_code", table_name="premium_destinations")
    op.drop_table("premium_destinations")
