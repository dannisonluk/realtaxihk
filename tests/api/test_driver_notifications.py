"""Driver in-app notification inbox (premium / fixed-fare order alerts).

External push is out of scope by design; the durable Postgres row is the
contract the mobile badge/list consumes. These tests prove the row is created
at order time, scoped to the right driver, and can be marked read.
"""

from __future__ import annotations

_CENTRAL = {"lat": 22.284, "lng": 114.158}
_AIRPORT = {"lat": 22.3081, "lng": 113.9187}


def _passenger(client, n: int) -> str:
    return client.activate(f"+852{917 * 10**5 + n}")


def _driver(client, n: int) -> dict:
    token = _passenger(client, n)
    r = client.post(
        "/api/v1/drivers/register",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "hk_id_last4": "0000",
            "taxi_driver_plate_no": f"TD{n}",
            "vehicle_reg_mark": f"ZZ{n}",
            "taxi_type": "URBAN",
        },
    )
    assert r.status_code == 201, r.text
    driver_id = r.json()["id"]
    admin = client.admin_headers()
    r = client.post(
        f"/api/v1/admin/drivers/{driver_id}/review",
        headers=admin,
        json={"decision": "approve"},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/v1/admin/drivers/{driver_id}/deposit/grant",
        headers=admin,
        json={"amount_hkd": "500.00"},
    )
    assert r.status_code == 200, r.text
    return {"token": token, "driver_id": driver_id}


def _headers(driver: dict) -> dict:
    return {"Authorization": f"Bearer {driver['token']}"}


def _go_online(client, driver: dict, *, online: bool = True) -> dict:
    headers = _headers(driver)
    r = client.post(
        "/api/v1/drivers/location",
        headers=headers,
        json={**_CENTRAL, "online": online},
    )
    assert r.status_code == 200, r.text
    return headers


def _premium(client) -> dict:
    r = client.post(
        "/api/v1/admin/destinations",
        headers=client.admin_headers(),
        json={
            "code": "HKG_T1",
            "name_zh": "機場一號客運大樓",
            "name_en": "Airport Terminal 1",
            "lat": _AIRPORT["lat"],
            "lng": _AIRPORT["lng"],
            "radius_m": 300,
            "status": "ACTIVE",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _order(
    client,
    passenger_token: str,
    *,
    dropoff: tuple[float, float] = (22.315, 114.219),
    address: str = "Harbour North, North Point",
    distance_km: str = "4.2",
) -> dict:
    r = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {passenger_token}"},
        json={
            "pickup_lat": _CENTRAL["lat"],
            "pickup_lng": _CENTRAL["lng"],
            "pickup_address": "Statue Square, Central",
            "dropoff_lat": dropoff[0],
            "dropoff_lng": dropoff[1],
            "dropoff_address": address,
            "distance_km": distance_km,
            "taxi_type": "URBAN",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _airport_order(client, passenger_token: str) -> dict:
    return _order(
        client,
        passenger_token,
        dropoff=(_AIRPORT["lat"], _AIRPORT["lng"]),
        address="Airport Terminal 1",
        distance_km="22.0",
    )


def _inbox(client, driver: dict) -> dict:
    r = client.get("/api/v1/drivers/me/notifications", headers=_headers(driver))
    assert r.status_code == 200, r.text
    return r.json()


class TestDriverNotificationInbox:
    async def test_fixed_fare_order_notifies_only_offer_owner(self, client):
        driver = _driver(client, 50021)
        _go_online(client, driver)
        other = _driver(client, 50022)
        _go_online(client, other)

        r = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=_headers(driver),
            json={
                "destination_area": "AIRPORT",
                "pickup_area": "KOWLOON",
                "price_hkd": "150.00",
            },
        )
        assert r.status_code == 201, r.text
        offer_id = r.json()["id"]

        _premium(client)
        passenger_token = _passenger(client, 50023)
        order = _airport_order(client, passenger_token)
        assert order["fare_mode"] == "FIXED"
        assert order["fixed_offer_id"] == offer_id

        data = _inbox(client, driver)
        assert data["unread_count"] == 1
        assert data["next_cursor"] is None
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["kind"] == "FIXED_FARE"
        assert item["order_id"] == order["id"]
        assert item["read"] is False
        assert "一口價" in item["headline_zh"]
        assert item["body_en"].startswith("Statue Square")

        assert _inbox(client, other)["items"] == []

    async def test_premium_order_broadcasts_only_to_online_active_drivers(self, client):
        online = _driver(client, 50031)
        _go_online(client, online)
        offline = _driver(client, 50032)
        _go_online(client, offline, online=False)
        _premium(client)

        passenger_token = _passenger(client, 50033)
        order = _airport_order(client, passenger_token)
        assert order["fare_mode"] == "METER"
        assert order["premium_destination"]["code"] == "HKG_T1"

        data = _inbox(client, online)
        assert data["unread_count"] == 1
        assert data["items"][0]["kind"] == "PREMIUM"
        assert data["items"][0]["order_id"] == order["id"]

        assert _inbox(client, offline)["items"] == []

    async def test_read_all_clears_the_unread_badge(self, client):
        driver = _driver(client, 50041)
        _go_online(client, driver)
        _premium(client)

        passenger_token = _passenger(client, 50042)
        _airport_order(client, passenger_token)

        assert _inbox(client, driver)["unread_count"] == 1
        r = client.post(
            "/api/v1/drivers/me/notifications/read-all",
            headers=_headers(driver),
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"updated": 1}

        data = _inbox(client, driver)
        assert data["unread_count"] == 0
        assert data["items"][0]["read"] is True

    async def test_inbox_requires_a_live_driver_profile(self, client):
        token = _passenger(client, 50051)
        r = client.get(
            "/api/v1/drivers/me/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404, r.text
