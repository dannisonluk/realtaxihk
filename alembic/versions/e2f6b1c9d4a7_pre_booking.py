"""pre-booking: landmarks, driver booking preferences, scheduled orders

Revision ID: e2f6b1c9d4a7
Revises: 9f3a1d2b4c6e
Create Date: 2026-10-07 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e2f6b1c9d4a7"
down_revision = "9f3a1d2b4c6e"
branch_labels = None
depends_on = None

_LANDMARKS = (
    {
        "id": "7a0f6e10-0001-4b3f-9d2a-000000000001",
        "code": "HKIA",
        "name_en": "Hong Kong International Airport",
        "name_zh": "香港國際機場",
        "category": "AIRPORT",
        "lat": 22.312599,
        "lng": 113.917300,
        "radius_m": 800,
    },
    {
        "id": "7a0f6e10-0002-4b3f-9d2a-000000000002",
        "code": "ASIAWORLD",
        "name_en": "AsiaWorld-Expo",
        "name_zh": "亞洲國際博覽館",
        "category": "VENUE",
        "lat": 22.321251,
        "lng": 113.942968,
        "radius_m": 400,
    },
    {
        "id": "7a0f6e10-0003-4b3f-9d2a-000000000003",
        "code": "DISNEYLAND",
        "name_en": "Hong Kong Disneyland",
        "name_zh": "香港迪士尼樂園",
        "category": "THEME_PARK",
        "lat": 22.313070,
        "lng": 114.040985,
        "radius_m": 600,
    },
    {
        "id": "7a0f6e10-0004-4b3f-9d2a-000000000004",
        "code": "OCEAN_PARK",
        "name_en": "Ocean Park",
        "name_zh": "海洋公園",
        "category": "THEME_PARK",
        "lat": 22.234767,
        "lng": 114.170817,
        "radius_m": 600,
    },
    {
        "id": "7a0f6e10-0005-4b3f-9d2a-000000000005",
        "code": "ICC",
        "name_en": "International Commerce Centre",
        "name_zh": "環球貿易廣場",
        "category": "OFFICE",
        "lat": 22.303379,
        "lng": 114.160226,
        "radius_m": 200,
    },
    {
        "id": "7a0f6e10-0006-4b3f-9d2a-000000000006",
        "code": "IFC",
        "name_en": "International Finance Centre",
        "name_zh": "國際金融中心",
        "category": "OFFICE",
        "lat": 22.285163,
        "lng": 114.159815,
        "radius_m": 200,
    },
    {
        "id": "7a0f6e10-0007-4b3f-9d2a-000000000007",
        "code": "HK_CONVENTION",
        "name_en": "Hong Kong Convention and Exhibition Centre",
        "name_zh": "香港會議展覽中心",
        "category": "VENUE",
        "lat": 22.282625,
        "lng": 114.173069,
        "radius_m": 300,
    },
    {
        "id": "7a0f6e10-0008-4b3f-9d2a-000000000008",
        "code": "HARBOUR_CITY",
        "name_en": "Harbour City",
        "name_zh": "海港城",
        "category": "MALL",
        "lat": 22.297002,
        "lng": 114.168420,
        "radius_m": 300,
    },
    {
        "id": "7a0f6e10-0009-4b3f-9d2a-000000000009",
        "code": "TIMES_SQ",
        "name_en": "Times Square",
        "name_zh": "時代廣場",
        "category": "MALL",
        "lat": 22.278359,
        "lng": 114.182106,
        "radius_m": 200,
    },
    {
        "id": "7a0f6e10-0010-4b3f-9d2a-000000000010",
        "code": "TST_PROMENADE",
        "name_en": "Tsim Sha Tsui Promenade",
        "name_zh": "尖沙咀海旁",
        "category": "WATERFRONT",
        "lat": 22.299419,
        "lng": 114.185648,
        "radius_m": 500,
    },
    {
        "id": "7a0f6e10-0011-4b3f-9d2a-000000000011",
        "code": "KT_PROMENADE",
        "name_en": "Kwun Tong Promenade",
        "name_zh": "觀塘海旁",
        "category": "WATERFRONT",
        "lat": 22.312288,
        "lng": 114.217369,
        "radius_m": 400,
    },
    {
        "id": "7a0f6e10-0012-4b3f-9d2a-000000000012",
        "code": "HK_COLISEUM",
        "name_en": "Hong Kong Coliseum",
        "name_zh": "香港體育館（紅館）",
        "category": "VENUE",
        "lat": 22.301318,
        "lng": 114.181981,
        "radius_m": 300,
    },
    {
        "id": "7a0f6e10-0013-4b3f-9d2a-000000000013",
        "code": "HZMB_PORT",
        "name_en": "Hong Kong-Zhuhai-Macao Bridge Hong Kong Port",
        "name_zh": "港珠澳大橋香港口岸",
        "category": "BORDER",
        "lat": 22.317868,
        "lng": 113.954070,
        "radius_m": 500,
    },
    {
        "id": "7a0f6e10-0014-4b3f-9d2a-000000000014",
        "code": "LOK_MA_CHAU",
        "name_en": "Lok Ma Chau Spur Line Control Point",
        "name_zh": "落馬洲支線管制站",
        "category": "BORDER",
        "lat": 22.515276,
        "lng": 114.065632,
        "radius_m": 400,
    },
    {
        "id": "7a0f6e10-0015-4b3f-9d2a-000000000015",
        "code": "LO_WU",
        "name_en": "Lo Wu Control Point",
        "name_zh": "羅湖管制站",
        "category": "BORDER",
        "lat": 22.529713,
        "lng": 114.113850,
        "radius_m": 300,
    },
    {
        "id": "7a0f6e10-0016-4b3f-9d2a-000000000016",
        "code": "QMH",
        "name_en": "Queen Mary Hospital",
        "name_zh": "瑪麗醫院",
        "category": "HOSPITAL",
        "lat": 22.269875,
        "lng": 114.131214,
        "radius_m": 300,
    },
    {
        "id": "7a0f6e10-0017-4b3f-9d2a-000000000017",
        "code": "PWH",
        "name_en": "Prince of Wales Hospital",
        "name_zh": "威爾斯親王醫院",
        "category": "HOSPITAL",
        "lat": 22.379609,
        "lng": 114.202193,
        "radius_m": 300,
    },
    {
        "id": "7a0f6e10-0018-4b3f-9d2a-000000000018",
        "code": "MAN_KAM_TO",
        "name_en": "Man Kam To Control Point",
        "name_zh": "文錦渡管制站",
        "category": "BORDER",
        "lat": 22.519218,
        "lng": 114.124584,
        "radius_m": 300,
    },
    {
        "id": "7a0f6e10-0019-4b3f-9d2a-000000000019",
        "code": "SHENZHEN_BAY",
        "name_en": "Shenzhen Bay Port Hong Kong Boundary Crossing Facilities PTI",
        "name_zh": "深圳灣口岸（港方口岸區）公共運輸交匯處",
        "category": "BORDER",
        "lat": 22.500992,
        "lng": 113.945654,
        "radius_m": 400,
    },
)


def _order_kind_enum() -> sa.Enum:
    return sa.Enum(
        "ON_DEMAND",
        "SCHEDULED",
        name="ck_orders_order_kind",
        length=16,
        native_enum=False,
        create_constraint=True,
    )


def _prebook_state_enum() -> sa.Enum:
    return sa.Enum(
        "PENDING",
        "BROADCASTING",
        "MATCHED",
        "EXPIRED",
        name="ck_orders_prebook_state",
        length=16,
        native_enum=False,
        create_constraint=True,
    )


def _landmark_category_enum() -> sa.Enum:
    return sa.Enum(
        "AIRPORT",
        "VENUE",
        "HOSPITAL",
        "BORDER",
        "WATERFRONT",
        "THEME_PARK",
        "OFFICE",
        "MALL",
        "OTHER",
        name="ck_landmarks_category",
        length=24,
        native_enum=False,
        create_constraint=True,
    )


def _in_check(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


_PREBOOK_EVENT_TYPES = (
    "STATE_CHANGED",
    "ARRIVAL_CLAIMED",
    "ARRIVAL_CONFIRMED",
    "DEST_CHANGED",
    "FEE_CHARGED",
    "PENALTY_CHARGED",
    "INTERRUPTED",
    "DISPUTE_OPENED",
    "PREBOOK_LANDMARK_CHOSEN",
    "PREBOOK_UPGRADED",
    "PREBOOK_MATCHED",
)


def upgrade() -> None:
    op.create_table(
        "landmarks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name_en", sa.String(length=120), nullable=False),
        sa.Column("name_zh", sa.String(length=120), nullable=False),
        sa.Column("category", _landmark_category_enum(), nullable=False),
        sa.Column(
            "location",
            Geography(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("radius_m", sa.Integer(), nullable=False, server_default="800"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    landmarks = sa.table(
        "landmarks",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String(40)),
        sa.column("name_en", sa.String(120)),
        sa.column("name_zh", sa.String(120)),
        sa.column("category", sa.String(24)),
        sa.column("location", Geography(geometry_type="POINT", srid=4326)),
        sa.column("radius_m", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    for index, row in enumerate(_LANDMARKS, start=1):
        op.execute(
            landmarks.insert().values(
                {
                    "id": row["id"],
                    "code": row["code"],
                    "name_en": row["name_en"],
                    "name_zh": row["name_zh"],
                    "category": row["category"],
                    "location": sa.text(
                        f"ST_SetSRID(ST_MakePoint({row['lng']}, {row['lat']}), 4326)::geography"
                    ),
                    "radius_m": row["radius_m"],
                    "is_active": True,
                    "sort_order": index,
                }
            )
        )

    op.create_table(
        "driver_booking_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "driver_profile_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "categories",
            postgresql.ARRAY(sa.String(length=24)),
            nullable=True,
        ),
        sa.Column(
            "preferred_origin_area",
            sa.String(length=120),
            nullable=True,
        ),
        sa.Column("available_from", sa.Time(), nullable=True),
        sa.Column("available_until", sa.Time(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["driver_profile_id"],
            ["driver_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("driver_profile_id"),
    )

    op.add_column(
        "orders",
        sa.Column("order_kind", _order_kind_enum(), nullable=False, server_default="ON_DEMAND"),
    )
    op.add_column(
        "orders",
        sa.Column("scheduled_pickup_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("prebook_visible_from", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("prebook_state", _prebook_state_enum(), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column(
            "dropoff_landmark_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_orders_scheduled_pickup_at",
        "orders",
        ["scheduled_pickup_at"],
    )
    op.create_index("ix_orders_prebook_state", "orders", ["prebook_state"])
    op.create_index(
        "ix_orders_prebook_due",
        "orders",
        ["prebook_visible_from", "prebook_state"],
    )
    op.create_foreign_key(
        "fk_orders_dropoff_landmark_id_landmarks",
        "orders",
        "landmarks",
        ["dropoff_landmark_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute("CREATE INDEX ix_landmarks_location ON landmarks USING gist (location)")
    op.execute("CREATE INDEX ix_landmarks_category ON landmarks (category) WHERE is_active")

    op.drop_constraint("ck_order_events_event", "order_events", type_="check")
    op.create_check_constraint(
        "ck_order_events_event",
        "order_events",
        _in_check("event", _PREBOOK_EVENT_TYPES),
    )


def downgrade() -> None:
    op.drop_constraint("ck_order_events_event", "order_events", type_="check")
    op.create_check_constraint(
        "ck_order_events_event",
        "order_events",
        _in_check(
            "event",
            (
                "STATE_CHANGED",
                "ARRIVAL_CLAIMED",
                "ARRIVAL_CONFIRMED",
                "DEST_CHANGED",
                "FEE_CHARGED",
                "PENALTY_CHARGED",
                "INTERRUPTED",
                "DISPUTE_OPENED",
            ),
        ),
    )
    op.drop_constraint(
        "fk_orders_dropoff_landmark_id_landmarks",
        "orders",
        type_="foreignkey",
    )
    op.execute("DROP INDEX IF EXISTS ix_landmarks_category")
    op.execute("DROP INDEX IF EXISTS ix_landmarks_location")
    op.drop_index("ix_orders_prebook_due", table_name="orders")
    op.drop_index("ix_orders_prebook_state", table_name="orders")
    op.drop_index("ix_orders_scheduled_pickup_at", table_name="orders")
    op.drop_column("orders", "dropoff_landmark_id")
    op.drop_column("orders", "prebook_state")
    op.drop_column("orders", "prebook_visible_from")
    op.drop_column("orders", "scheduled_pickup_at")
    op.drop_constraint("ck_orders_order_kind", "orders", type_="check")
    op.drop_column("orders", "order_kind")
    op.drop_table("driver_booking_preferences")
    op.drop_table("landmarks")
