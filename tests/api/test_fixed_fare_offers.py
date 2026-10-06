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


# The pickup of `_airport_order` above — the arrival claim is checked against the
# driver's recorded GPS, so the tick has to land here.
_PICKUP = (22.284, 114.158)


def _arrive(
    client, order_id: str, driver: dict, passenger_token: str, passenger_phone: str
) -> None:
    """Walk `ACCEPTED -> DRIVER_ARRIVED` through the real two-step arrival.

    P4 split arrival in two (`docs/IN_TRIP_REDESIGN.md` §4.0): the driver claims
    with a recorded GPS tick, and the passenger confirms with the last 4 digits
    of *their own* number. A bare `/arrive` can no longer reach `DRIVER_ARRIVED`
    — it stops at `PENDING_ARRIVAL_CONFIRM` — so any test that only cares about
    what happens *after* arrival still has to walk both steps.
    """
    r = client.post(
        "/api/v1/drivers/location",
        headers={"Authorization": f"Bearer {driver['token']}"},
        json={"lat": _PICKUP[0], "lng": _PICKUP[1]},
    )
    assert r.status_code == 200, r.text

    r = client.post(
        f"/api/v1/orders/{order_id}/arrival-claim",
        headers={"Authorization": f"Bearer {driver['token']}"},
        json={},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PENDING_ARRIVAL_CONFIRM", r.text

    r = client.post(
        f"/api/v1/orders/{order_id}/arrival-confirm",
        headers={"Authorization": f"Bearer {passenger_token}"},
        json={"phone_last4": passenger_phone[-4:]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "DRIVER_ARRIVED", r.text


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

    async def test_unknown_destination_area_is_rejected(self, client):
        """A typo must be a 422, not a silent offer that can never match.

        `Order.destination_area` is only ever one of the five codes in
        `app.core.region`, so an offer naming anything else would sit ACTIVE
        forever and match nothing — the failure the closed set exists to turn
        into a refusal.
        """
        driver = _mk_active_driver(client, "+85260009921")
        headers = {"Authorization": f"Bearer {driver['token']}"}
        r = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=headers,
            json={"destination_area": "KOWLOON_BAD", "price_hkd": "150.00"},
        )
        assert r.status_code == 422, r.text
        assert "unknown destination area" in r.text
        assert client.get("/api/v1/drivers/me/fixed-offers", headers=headers).json()["items"] == []

    async def test_unknown_pickup_area_is_rejected(self, client):
        driver = _mk_active_driver(client, "+85260009922")
        headers = {"Authorization": f"Bearer {driver['token']}"}
        r = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=headers,
            json={
                "destination_area": "AIRPORT",
                "pickup_area": "NOWHERE",
                "price_hkd": "150.00",
            },
        )
        assert r.status_code == 422, r.text
        assert "unknown pickup area" in r.text

    async def test_unknown_premium_destination_is_rejected(self, client):
        """A dangling premium id is its own refusal, never "duplicate route"."""
        driver = _mk_active_driver(client, "+85260009923")
        headers = {"Authorization": f"Bearer {driver['token']}"}
        r = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=headers,
            json={
                "premium_destination_id": "00000000-0000-0000-0000-0000000000ff",
                "price_hkd": "150.00",
            },
        )
        assert r.status_code == 422, r.text
        assert "unknown premium destination" in r.text

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

        _arrive(client, order["id"], driver, passenger_token, "+85260009919")

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
        # 500.00 deposit, less the $5 per-trip platform fee taken at `/start`
        # (P4 DECISION-1), less this $15 fixed-ride fee at `/complete`.
        assert fee_entries[0]["balance_after_hkd"] == "480.00"

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

        _arrive(client, order["id"], driver, passenger_token, "+85260009921")

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
