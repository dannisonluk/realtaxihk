"""fixed-fare offers + order fare_mode columns

Revision ID: 8f2a1c5d3b40
Revises: 5c8b2f0a1e43
Create Date: 2026-10-05

Phase 2: driver-priced flat fares (一口價). An order can be minted in FIXED
mode when a standing offer matches the route and is competitive. The order
freezes:

- `fare_mode` (METER/FIXED)
- `pickup_area` (server-derived, same coarse codes as destination_area)
- `fixed_offer_id` — the specific offer that priced this order
- `driver_price_hkd` / `platform_fee_hkd` / `passenger_price_hkd`

Only the offer's owner may grab a FIXED order; the backend enforces that in
`GrabService`, not the client.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8f2a1c5d3b40"
down_revision: str | Sequence[str] | None = "5c8b2f0a1e43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fixed_price_offers",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("driver_profile_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("destination_area", sa.String(24), nullable=True),
        sa.Column("premium_destination_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("pickup_area", sa.String(24), nullable=True),
        sa.Column("price_hkd", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["driver_profile_id"], ["driver_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["premium_destination_id"], ["premium_destinations.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_fixed_price_offers_driver_profile_id",
        "fixed_price_offers",
        ["driver_profile_id"],
    )
    op.create_index(
        "ix_fixed_price_offers_premium_destination_id",
        "fixed_price_offers",
        ["premium_destination_id"],
    )
    op.create_index("ix_fixed_price_offers_status", "fixed_price_offers", ["status"])
    op.create_index(
        "uq_fixed_offer_active_route",
        "fixed_price_offers",
        [
            "driver_profile_id",
            sa.text("COALESCE(premium_destination_id, '00000000-0000-0000-0000-000000000000')"),
            sa.text("COALESCE(destination_area, '')"),
            sa.text("COALESCE(pickup_area, '')"),
        ],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    op.add_column(
        "orders",
        sa.Column(
            "fare_mode",
            sa.Enum(
                "METER",
                "FIXED",
                name="ck_orders_fare_mode",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
    )
    op.add_column("orders", sa.Column("pickup_area", sa.String(24), nullable=True))
    op.add_column("orders", sa.Column("fixed_offer_id", sa.UUID(as_uuid=True), nullable=True))
    op.add_column("orders", sa.Column("driver_price_hkd", sa.Numeric(10, 2), nullable=True))
    op.add_column("orders", sa.Column("platform_fee_hkd", sa.Numeric(10, 2), nullable=True))
    op.add_column("orders", sa.Column("passenger_price_hkd", sa.Numeric(10, 2), nullable=True))
    op.create_foreign_key(
        "fk_orders_fixed_offer_id",
        "orders",
        "fixed_price_offers",
        ["fixed_offer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_orders_fixed_offer_id", "orders", ["fixed_offer_id"])
    op.create_index("ix_orders_fare_mode", "orders", ["fare_mode"])
    # Backfill existing orders as METER so reads never see a NULL fare mode.
    op.execute("UPDATE orders SET fare_mode = 'METER' WHERE fare_mode IS NULL")
    op.alter_column("orders", "fare_mode", nullable=False)


def downgrade() -> None:
    op.drop_index("ix_orders_fare_mode", table_name="orders")
    op.drop_index("ix_orders_fixed_offer_id", table_name="orders")
    op.drop_constraint("fk_orders_fixed_offer_id", "orders", type_="foreignkey")
    op.drop_column("orders", "passenger_price_hkd")
    op.drop_column("orders", "platform_fee_hkd")
    op.drop_column("orders", "driver_price_hkd")
    op.drop_column("orders", "fixed_offer_id")
    op.drop_column("orders", "pickup_area")
    op.drop_column("orders", "fare_mode")

    op.drop_index("uq_fixed_offer_active_route", table_name="fixed_price_offers")
    op.drop_index("ix_fixed_price_offers_premium_destination_id", table_name="fixed_price_offers")
    op.drop_index("ix_fixed_price_offers_driver_profile_id", table_name="fixed_price_offers")
    op.drop_index("ix_fixed_price_offers_status", table_name="fixed_price_offers")
    op.drop_table("fixed_price_offers")
