"""Pre-booking API and background broadcaster tests.

Covers the wire contract that is not exercised elsewhere:
- landmark list is public to logged-in users and seeded by migration
- driver booking preferences can be saved and read back
- scheduled order creation enforces the 2h..3d window and landmark validation
- the broadcaster releases PENDING orders at ``prebook_visible_from``,
  indexes into Redis, and notifies matching online drivers
- grab marks a scheduled order MATCHED
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.services.infra.maintenance import MaintenanceService
from app.services.order.prebooking_service import PrebookingBroadcaster

_ORDER = {
    "pickup_lat": 22.284,
    "pickup_lng": 114.158,
    "dropoff_lat": 22.315,
    "dropoff_lng": 114.219,
    "pickup_address": "Statue Square, Central",
    "dropoff_address": "Harbour North, North Point",
    "distance_km": "4.2",
    "taxi_type": "URBAN",
}

_LANDMARK_SEEDS = (
    (
        "7a0f6e10-0001-4b3f-9d2a-000000000001",
        "HKIA",
        "Hong Kong International Airport",
        "香港國際機場",
        "AIRPORT",
        22.312599,
        113.917300,
        800,
    ),
    (
        "7a0f6e10-0002-4b3f-9d2a-000000000002",
        "ASIAWORLD",
        "AsiaWorld-Expo",
        "亞洲國際博覽館",
        "VENUE",
        22.321251,
        113.942968,
        400,
    ),
    (
        "7a0f6e10-0003-4b3f-9d2a-000000000003",
        "DISNEYLAND",
        "Hong Kong Disneyland",
        "香港迪士尼樂園",
        "THEME_PARK",
        22.313070,
        114.040985,
        600,
    ),
    (
        "7a0f6e10-0004-4b3f-9d2a-000000000004",
        "OCEAN_PARK",
        "Ocean Park",
        "海洋公園",
        "THEME_PARK",
        22.234767,
        114.170817,
        600,
    ),
    (
        "7a0f6e10-0005-4b3f-9d2a-000000000005",
        "ICC",
        "International Commerce Centre",
        "環球貿易廣場",
        "OFFICE",
        22.303379,
        114.160226,
        200,
    ),
    (
        "7a0f6e10-0006-4b3f-9d2a-000000000006",
        "IFC",
        "International Finance Centre",
        "國際金融中心",
        "OFFICE",
        22.285163,
        114.159815,
        200,
    ),
    (
        "7a0f6e10-0007-4b3f-9d2a-000000000007",
        "HK_CONVENTION",
        "Hong Kong Convention and Exhibition Centre",
        "香港會議展覽中心",
        "VENUE",
        22.282625,
        114.173069,
        300,
    ),
    (
        "7a0f6e10-0008-4b3f-9d2a-000000000008",
        "HARBOUR_CITY",
        "Harbour City",
        "海港城",
        "MALL",
        22.297002,
        114.168420,
        300,
    ),
    (
        "7a0f6e10-0009-4b3f-9d2a-000000000009",
        "TIMES_SQ",
        "Times Square",
        "時代廣場",
        "MALL",
        22.278359,
        114.182106,
        200,
    ),
    (
        "7a0f6e10-0010-4b3f-9d2a-000000000010",
        "TST_PROMENADE",
        "Tsim Sha Tsui Promenade",
        "尖沙咀海旁",
        "WATERFRONT",
        22.299419,
        114.185648,
        500,
    ),
    (
        "7a0f6e10-0011-4b3f-9d2a-000000000011",
        "KT_PROMENADE",
        "Kwun Tong Promenade",
        "觀塘海旁",
        "WATERFRONT",
        22.312288,
        114.217369,
        400,
    ),
    (
        "7a0f6e10-0012-4b3f-9d2a-000000000012",
        "HK_COLISEUM",
        "Hong Kong Coliseum",
        "香港體育館（紅館）",
        "VENUE",
        22.301318,
        114.181981,
        300,
    ),
    (
        "7a0f6e10-0013-4b3f-9d2a-000000000013",
        "HZMB_PORT",
        "Hong Kong-Zhuhai-Macao Bridge Hong Kong Port",
        "港珠澳大橋香港口岸",
        "BORDER",
        22.317868,
        113.954070,
        500,
    ),
    (
        "7a0f6e10-0014-4b3f-9d2a-000000000014",
        "LOK_MA_CHAU",
        "Lok Ma Chau Spur Line Control Point",
        "落馬洲支線管制站",
        "BORDER",
        22.515276,
        114.065632,
        400,
    ),
    (
        "7a0f6e10-0015-4b3f-9d2a-000000000015",
        "LO_WU",
        "Lo Wu Control Point",
        "羅湖管制站",
        "BORDER",
        22.529713,
        114.113850,
        300,
    ),
    (
        "7a0f6e10-0016-4b3f-9d2a-000000000016",
        "QMH",
        "Queen Mary Hospital",
        "瑪麗醫院",
        "HOSPITAL",
        22.269875,
        114.131214,
        300,
    ),
    (
        "7a0f6e10-0017-4b3f-9d2a-000000000017",
        "PWH",
        "Prince of Wales Hospital",
        "威爾斯親王醫院",
        "HOSPITAL",
        22.379609,
        114.202193,
        300,
    ),
    (
        "7a0f6e10-0018-4b3f-9d2a-000000000018",
        "MAN_KAM_TO",
        "Man Kam To Control Point",
        "文錦渡管制站",
        "BORDER",
        22.519218,
        114.124584,
        300,
    ),
    (
        "7a0f6e10-0019-4b3f-9d2a-000000000019",
        "SHENZHEN_BAY",
        "Shenzhen Bay Port Hong Kong Boundary Crossing Facilities PTI",
        "深圳灣口岸（港方口岸區）公共運輸交匯處",
        "BORDER",
        22.500992,
        113.945654,
        400,
    ),
)


def _seed_landmarks(client) -> None:
    for row in _LANDMARK_SEEDS:
        _id, code, name_en, name_zh, category, lat, lng, radius = row
        client.exec_sql(
            "INSERT INTO landmarks (id, code, name_en, name_zh, category, location, "
            "radius_m, is_active, sort_order) VALUES (:id, :code, :name_en, :name_zh, "
            ":category, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, "
            ":radius, true, 0) ON CONFLICT (id) DO NOTHING",
            {
                "id": _id,
                "code": code,
                "name_en": name_en,
                "name_zh": name_zh,
                "category": category,
                "lat": lat,
                "lng": lng,
                "radius": radius,
            },
        )


class _NoopRedis:
    async def geoadd(self, *args, **kwargs):
        return 0

    async def zrange(self, *args, **kwargs):
        return []

    async def zrem(self, *args, **kwargs):
        return 0

    async def delete(self, *args, **kwargs):
        return 0


class _FailingRedis(_NoopRedis):
    async def geoadd(self, *args, **kwargs):
        raise RuntimeError("redis down")


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk_passenger(client, phone: str) -> str:
    return client.activate(phone)


def _mk_active_driver(client, phone: str) -> dict:
    token = client.activate(phone)
    r = client.post(
        "/api/v1/drivers/register",
        headers=_h(token),
        json={
            "hk_id_last4": phone[-4:],
            "taxi_driver_plate_no": f"TD{phone[-5:]}",
            "vehicle_reg_mark": f"V{phone[-4:]}",
            "taxi_type": "URBAN",
        },
    )
    assert r.status_code == 201, r.text
    driver_id = r.json()["id"]
    client.post(
        f"/api/v1/admin/drivers/{driver_id}/review",
        headers=client.admin_headers(),
        json={"decision": "approve"},
    )
    client.post(
        f"/api/v1/admin/drivers/{driver_id}/deposit/grant",
        headers=client.admin_headers(),
        json={"amount_hkd": "500.00"},
    )
    return {"token": token, "driver_id": driver_id, "phone": phone}


def _landmark_id(client, code: str = "HKIA") -> str:
    _seed_landmarks(client)
    r = client.get("/api/v1/landmarks", headers=_h(client.activate("+85210000000")))
    assert r.status_code == 200, r.text
    rows = r.json()["items"]
    assert len(rows) >= 19, r.text
    return next(row["id"] for row in rows if row["code"] == code)


def _future(within: timedelta) -> str:
    return (datetime.now(UTC) + within).isoformat()


class TestLandmarks:
    def test_list_landmarks_is_seeded_and_cacheable(self, client):
        _seed_landmarks(client)
        token = client.activate("+85210000001")
        r = client.get("/api/v1/landmarks", headers=_h(token))
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 19
        assert r.headers.get("cache-control") == "public, max-age=3600"
        codes = {row["code"] for row in body["items"]}
        assert {"HKIA", "DISNEYLAND", "SHENZHEN_BAY", "ICC", "HARBOUR_CITY"} <= codes
        assert {"OFFICE", "MALL", "BORDER", "AIRPORT"} <= {row["category"] for row in body["items"]}

    def test_category_filter(self, client):
        _seed_landmarks(client)
        token = client.activate("+85210000002")
        r = client.get("/api/v1/landmarks?category=HOSPITAL", headers=_h(token))
        assert r.status_code == 200
        assert {row["code"] for row in r.json()["items"]} == {"QMH", "PWH"}


class TestDriverPreferences:
    def test_defaults_are_empty(self, client):
        driver = _mk_active_driver(client, "+85210000003")
        r = client.get("/api/v1/drivers/me/booking-preferences", headers=_h(driver["token"]))
        assert r.status_code == 200, r.text
        assert r.json()["categories"] == []

    def test_save_and_read_back(self, client):
        driver = _mk_active_driver(client, "+85210000004")
        r = client.put(
            "/api/v1/drivers/me/booking-preferences",
            headers=_h(driver["token"]),
            json={
                "categories": ["AIRPORT", "BORDER"],
                "preferred_origin_area": "Airport",
                "available_from": "06:00",
                "available_until": "22:00",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["categories"] == ["AIRPORT", "BORDER"]
        assert r.json()["available_from"] == "06:00"

    def test_invalid_time_range_is_422(self, client):
        driver = _mk_active_driver(client, "+85210000005")
        r = client.put(
            "/api/v1/drivers/me/booking-preferences",
            headers=_h(driver["token"]),
            json={"available_from": "22:00", "available_until": "06:00"},
        )
        assert r.status_code == 422, r.text


class TestScheduledCreate:
    def test_scheduled_order_keeps_prebook_fields(self, client):
        token = _mk_passenger(client, "+85210000006")
        landmark_id = _landmark_id(client)
        r = client.post(
            "/api/v1/orders",
            headers=_h(token),
            json={
                **_ORDER,
                "order_kind": "SCHEDULED",
                "scheduled_pickup_at": _future(timedelta(hours=3)),
                "dropoff_landmark_id": landmark_id,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["order_kind"] == "SCHEDULED"
        assert body["prebook_state"] == "PENDING"
        assert body["status"] == "CREATED"
        assert body["scheduled_pickup_at"] is not None
        assert body["prebook_visible_from"] is not None
        assert body["dropoff_landmark_id"] == landmark_id

    def test_scheduled_too_soon_is_422(self, client):
        token = _mk_passenger(client, "+85210000007")
        landmark_id = _landmark_id(client)
        r = client.post(
            "/api/v1/orders",
            headers=_h(token),
            json={
                **_ORDER,
                "order_kind": "SCHEDULED",
                "scheduled_pickup_at": _future(timedelta(minutes=30)),
                "dropoff_landmark_id": landmark_id,
            },
        )
        assert r.status_code == 422, r.text

    def test_scheduled_unknown_landmark_is_400(self, client):
        token = _mk_passenger(client, "+85210000008")
        r = client.post(
            "/api/v1/orders",
            headers=_h(token),
            json={
                **_ORDER,
                "order_kind": "SCHEDULED",
                "scheduled_pickup_at": _future(timedelta(hours=3)),
                "dropoff_landmark_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert r.status_code == 400, r.text


class TestBroadcaster:
    def test_releases_due_order_and_notifies_matching_driver(self, client):
        passenger_token = _mk_passenger(client, "+85210000009")
        driver = _mk_active_driver(client, "+85210000010")
        driver_token = driver["token"]
        # Driver opts into AIRPORT work and comes online.
        r = client.put(
            "/api/v1/drivers/me/booking-preferences",
            headers=_h(driver_token),
            json={"categories": ["AIRPORT"]},
        )
        assert r.status_code == 200, r.text
        client.post(
            "/api/v1/drivers/location",
            headers=_h(driver_token),
            json={"lat": 22.284, "lng": 114.158},
        )
        landmark_id = _landmark_id(client)
        r = client.post(
            "/api/v1/orders",
            headers=_h(passenger_token),
            json={
                **_ORDER,
                "order_kind": "SCHEDULED",
                "scheduled_pickup_at": _future(timedelta(hours=3)),
                "dropoff_landmark_id": landmark_id,
            },
        )
        assert r.status_code == 201, r.text
        oid = r.json()["id"]

        # Force the 30-minute lead time into the past, then run the job.
        client.exec_sql(
            "UPDATE orders SET prebook_visible_from = now() - interval '1 minute' WHERE id = :oid",
            {"oid": oid},
        )
        broadcaster = PrebookingBroadcaster(client.db_factory, _NoopRedis())
        changed = asyncio.run(broadcaster.run_due())
        assert changed >= 1

        detail = client.get(f"/api/v1/orders/{oid}", headers=_h(passenger_token))
        assert detail.status_code == 200, detail.text
        assert detail.json()["status"] == "BROADCASTING"
        assert detail.json()["prebook_state"] == "BROADCASTING"

        inbox = client.get("/api/v1/drivers/me/notifications", headers=_h(driver_token))
        assert inbox.status_code == 200, inbox.text
        assert any(row["kind"] == "SCHEDULED" for row in inbox.json()["items"])

    def test_release_commits_when_redis_index_fails(self, client):
        passenger_token = _mk_passenger(client, "+85290000020")
        landmark_id = _landmark_id(client)
        r = client.post(
            "/api/v1/orders",
            headers=_h(passenger_token),
            json={
                **_ORDER,
                "order_kind": "SCHEDULED",
                "scheduled_pickup_at": _future(timedelta(hours=3)),
                "dropoff_landmark_id": landmark_id,
            },
        )
        assert r.status_code == 201, r.text
        oid = r.json()["id"]
        client.exec_sql(
            "UPDATE orders SET prebook_visible_from = now() - interval '1 minute' WHERE id = :oid",
            {"oid": oid},
        )

        asyncio.run(PrebookingBroadcaster(client.db_factory, _FailingRedis()).run_due())

        detail = client.get(f"/api/v1/orders/{oid}", headers=_h(passenger_token))
        assert detail.status_code == 200, detail.text
        assert detail.json()["status"] == "BROADCASTING"
        assert detail.json()["prebook_state"] == "BROADCASTING"

    def test_grab_marks_scheduled_order_matched(self, client):
        passenger_token = _mk_passenger(client, "+85290000011")
        driver = _mk_active_driver(client, "+85290000012")
        landmark_id = _landmark_id(client)
        r = client.post(
            "/api/v1/orders",
            headers=_h(passenger_token),
            json={
                **_ORDER,
                "order_kind": "SCHEDULED",
                "scheduled_pickup_at": _future(timedelta(hours=3)),
                "dropoff_landmark_id": landmark_id,
            },
        )
        assert r.status_code == 201, r.text
        oid = r.json()["id"]
        client.exec_sql(
            "UPDATE orders SET status = 'BROADCASTING', prebook_state = 'BROADCASTING' "
            "WHERE id = :oid",
            {"oid": oid},
        )
        r = client.post(f"/api/v1/orders/{oid}/grab", headers=_h(driver["token"]))
        assert r.status_code == 200, r.text
        detail = client.get(f"/api/v1/orders/{oid}", headers=_h(passenger_token))
        assert detail.json()["prebook_state"] == "MATCHED"


class TestGeoSweep:
    def _make_released_scheduled(self, client, passenger_phone: str):
        passenger_token = _mk_passenger(client, passenger_phone)
        landmark_id = _landmark_id(client)
        r = client.post(
            "/api/v1/orders",
            headers=_h(passenger_token),
            json={
                **_ORDER,
                "order_kind": "SCHEDULED",
                "scheduled_pickup_at": _future(timedelta(hours=3)),
                "dropoff_landmark_id": landmark_id,
            },
        )
        assert r.status_code == 201, r.text
        oid = r.json()["id"]
        client.exec_sql(
            "UPDATE orders SET status = 'BROADCASTING', "
            "prebook_state = 'BROADCASTING', "
            "created_at = now() - interval '3 days', "
            "prebook_visible_from = now() - interval '1 minute' "
            "WHERE id = :oid",
            {"oid": oid},
        )
        return passenger_token, oid

    def test_newly_released_scheduled_order_is_not_age_cancelled(self, client):
        passenger_token, oid = self._make_released_scheduled(client, "+85290000021")

        result = asyncio.run(
            MaintenanceService(client.db_factory, _NoopRedis()).sweep_ghost_orders(30)
        )
        assert result["auto_cancelled"] == 0

        detail = client.get(f"/api/v1/orders/{oid}", headers=_h(passenger_token))
        assert detail.status_code == 200, detail.text
        assert detail.json()["status"] == "BROADCASTING"
        assert detail.json()["prebook_state"] == "BROADCASTING"

    def test_expired_scheduled_order_cancel_syncs_prebook_state(self, client):
        passenger_token, oid = self._make_released_scheduled(client, "+85290000022")
        client.exec_sql(
            "UPDATE orders SET prebook_visible_from = now() - interval '31 minutes' "
            "WHERE id = :oid",
            {"oid": oid},
        )

        result = asyncio.run(
            MaintenanceService(client.db_factory, _NoopRedis()).sweep_ghost_orders(30)
        )
        assert result["auto_cancelled"] == 1

        detail = client.get(f"/api/v1/orders/{oid}", headers=_h(passenger_token))
        assert detail.status_code == 200, detail.text
        assert detail.json()["status"] == "CANCELLED"
        assert detail.json()["prebook_state"] == "EXPIRED"
