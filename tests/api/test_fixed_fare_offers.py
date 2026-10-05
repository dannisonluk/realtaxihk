"""Fixed-fare offers (一口價) — driver offer lifecycle, matching, and grab gate.

These tests exercise the whole feature without a browser: the driver creates a
standing offer, the passenger creates an order on that route, the server freezes
a fixed passenger price, and only the offer owner can grab it.
"""

from __future__ import annotations


def _mk_active_driver(client, phone: str) -> dict:
    token = client.activate(phone)
    r = client.post(
        "/api/v1/drivers/register",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "hk_id_last4": phone[-4:],
            "taxi_driver_plate_no": f"TD{phone[-5:]}",
            "vehicle_reg_mark": f"V{phone[-4:]}",
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
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    return {"token": token, "driver_id": driver_id, "user_id": me["id"]}


def _mk_premium(
    client, *, code: str = "HKG_T1", lat: float = 22.308, lng: float = 113.9185
) -> dict:
    headers = client.admin_headers()
    r = client.post(
        "/api/v1/admin/destinations",
        headers=headers,
        json={
            "code": code,
            "name_zh": "機場一號客運大樓",
            "name_en": "Airport Terminal 1",
            "lat": lat,
            "lng": lng,
            "radius_m": 300,
            "status": "ACTIVE",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _airport_order(passenger_token: str, client) -> dict:
    r = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {passenger_token}"},
        json={
            "pickup_lat": 22.284,
            "pickup_lng": 114.158,
            "dropoff_lat": 22.3081,
            "dropoff_lng": 113.9187,
            "pickup_address": "Statue Square, Central",
            "dropoff_address": "Airport Terminal 1",
            "distance_km": "22.0",
            "taxi_type": "URBAN",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


class TestFixedOfferLifecycle:
    async def test_driver_creates_and_updates_offer(self, client):
        driver = _mk_active_driver(client, "+85260009911")
        headers = {"Authorization": f"Bearer {driver['token']}"}
        r = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=headers,
            json={
                "destination_area": "AIRPORT",
                "pickup_area": "KOWLOON",
                "price_hkd": "150.00",
            },
        )
        assert r.status_code == 201, r.text
        offer = r.json()
        assert offer["status"] == "ACTIVE"
        assert offer["price_hkd"] == "150.00"

        r = client.patch(
            f"/api/v1/drivers/me/fixed-offers/{offer['id']}",
            headers=headers,
            json={"status": "PAUSED"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PAUSED"

        r = client.get("/api/v1/drivers/me/fixed-offers", headers=headers)
        assert r.status_code == 200, r.text
        assert len(r.json()["items"]) == 1

    async def test_duplicate_active_offer_rejected(self, client):
        driver = _mk_active_driver(client, "+85260009912")
        headers = {"Authorization": f"Bearer {driver['token']}"}
        payload = {
            "destination_area": "AIRPORT",
            "pickup_area": "KOWLOON",
            "price_hkd": "150.00",
        }
        r = client.post("/api/v1/drivers/me/fixed-offers", headers=headers, json=payload)
        assert r.status_code == 201, r.text
        r = client.post("/api/v1/drivers/me/fixed-offers", headers=headers, json=payload)
        assert r.status_code == 422, r.text

    async def test_fixed_order_freezes_fare_and_only_owner_can_grab(self, client):
        premium = _mk_premium(client)
        driver = _mk_active_driver(client, "+85260009913")
        driver_headers = {"Authorization": f"Bearer {driver['token']}"}

        # A competitive offer: passenger price = 150 + 15 (10%) = 165, well
        # under a 22 km urban meter estimate.
        r = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=driver_headers,
            json={
                "destination_area": "AIRPORT",
                "pickup_area": "KOWLOON",
                "price_hkd": "150.00",
            },
        )
        assert r.status_code == 201, r.text
        offer_id = r.json()["id"]

        passenger_token = client.activate("+85260009914")
        order = _airport_order(passenger_token, client)
        assert order["fare_mode"] == "FIXED"
        assert order["fixed_offer_id"] == offer_id
        assert order["premium_destination"]["code"] == premium["code"]
        assert order["fare"]["passenger_price_hkd"] == "165.00"
        assert order["fare"]["driver_price_hkd"] == "150.00"
        assert order["fare"]["platform_fee_hkd"] == "15.00"

        # A different ACTIVE driver without the offer cannot grab it.
        other = _mk_active_driver(client, "+85260009915")
        r = client.post(
            f"/api/v1/orders/{order['id']}/grab",
            headers={"Authorization": f"Bearer {other['token']}"},
        )
        assert r.status_code == 409, r.text

        # The offer owner can.
        r = client.post(f"/api/v1/orders/{order['id']}/grab", headers=driver_headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACCEPTED"

    async def test_fixed_complete_posts_platform_fee_to_ledger(self, client):
        _mk_premium(client)
        driver = _mk_active_driver(client, "+85260009918")
        driver_headers = {"Authorization": f"Bearer {driver['token']}"}

        r = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=driver_headers,
            json={
                "destination_area": "AIRPORT",
                "pickup_area": "KOWLOON",
                "price_hkd": "150.00",
            },
        )
        assert r.status_code == 201, r.text

        passenger_token = client.activate("+85260009919")
        order = _airport_order(passenger_token, client)
        assert order["fare_mode"] == "FIXED"

        r = client.post(
            f"/api/v1/orders/{order['id']}/grab",
            headers=driver_headers,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/v1/orders/{order['id']}/arrive",
            headers=driver_headers,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/v1/orders/{order['id']}/start",
            headers=driver_headers,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/v1/orders/{order['id']}/complete",
            headers=driver_headers,
        )
        assert r.status_code == 200, r.text

        ledger = client.get(
            "/api/v1/drivers/me/ledger",
            headers=driver_headers,
        ).json()["items"]
        fee_entries = [e for e in ledger if e["entry_type"] == "FIXED_RIDE_FEE"]
        assert len(fee_entries) == 1, fee_entries
        assert fee_entries[0]["amount_hkd"] == "-15.00"
        assert fee_entries[0]["order_id"] == order["id"]
        assert fee_entries[0]["balance_after_hkd"] == "485.00"

    async def test_meter_complete_does_not_post_fixed_fee(self, client):
        driver = _mk_active_driver(client, "+85260009920")
        driver_headers = {"Authorization": f"Bearer {driver['token']}"}

        passenger_token = client.activate("+85260009921")
        order = _airport_order(passenger_token, client)
        assert order["fare_mode"] == "METER"

        r = client.post(
            f"/api/v1/orders/{order['id']}/grab",
            headers=driver_headers,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/v1/orders/{order['id']}/arrive",
            headers=driver_headers,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/v1/orders/{order['id']}/start",
            headers=driver_headers,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/v1/orders/{order['id']}/complete",
            headers=driver_headers,
        )
        assert r.status_code == 200, r.text

        ledger = client.get(
            "/api/v1/drivers/me/ledger",
            headers=driver_headers,
        ).json()["items"]
        assert all(e["entry_type"] != "FIXED_RIDE_FEE" for e in ledger)

    async def test_noncompetitive_offer_stays_meter(self, client):
        _mk_premium(client)
        driver = _mk_active_driver(client, "+85260009916")
        headers = {"Authorization": f"Bearer {driver['token']}"}
        # 1000 + 100 fee is above a 22km meter estimate, so no fixed match.
        r = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=headers,
            json={"destination_area": "AIRPORT", "price_hkd": "1000.00"},
        )
        assert r.status_code == 201, r.text

        passenger_token = client.activate("+85260009917")
        order = _airport_order(passenger_token, client)
        assert order["fare_mode"] == "METER"
        assert order["fixed_offer_id"] is None
