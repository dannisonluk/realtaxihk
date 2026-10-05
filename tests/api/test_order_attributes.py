"""Order-level Phase-1 attribute tests.

These prove the passenger-facing requirements and driver-visible data actually
flow through the order lifecycle:

- `POST /orders` auto-detects a premium destination from the dropoff geofence
  and freezes requirements/payment preference onto the order.
- `POST /orders/{id}/grab` copies the winning driver's declared payment methods
  onto the order, so the passenger sees them even if the driver edits the
  profile afterwards.
"""

from __future__ import annotations


def _mk_passenger(client) -> str:
    return client.activate("+85260009901")


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


def _create_premium(client, **overrides) -> dict:
    headers = client.admin_headers()
    body = {
        "code": "HKG_T1",
        "name_zh": "機場一號客運大樓",
        "name_en": "Airport Terminal 1",
        "lat": 22.308,
        "lng": 113.9185,
        "radius_m": 300,
        "status": "ACTIVE",
    }
    body.update(overrides)
    r = client.post("/api/v1/admin/destinations", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _order_payload(dropoff_lat: float, dropoff_lng: float, **overrides) -> dict:
    body = {
        "pickup_lat": 22.284,
        "pickup_lng": 114.158,  # Central
        "dropoff_lat": dropoff_lat,
        "dropoff_lng": dropoff_lng,
        "pickup_address": "Statue Square, Central",
        "dropoff_address": "Airport Terminal 1",
        "distance_km": "22.0",
        "taxi_type": "URBAN",
    }
    body.update(overrides)
    return body


class TestOrderAttributes:
    async def test_premium_destination_auto_detected_and_requirements_frozen(self, client):
        _create_premium(client)
        token = _mk_passenger(client)
        payload = _order_payload(
            22.3081,
            113.9187,
            requirements={
                "silent_ride": True,
                "no_radio_music": True,
                "no_smoke": True,
                "animal": {"kind": "cat", "height_cm": "25", "weight_kg": "4.5"},
            },
            payment_preference=["CASH", "OCTOPUS"],
        )
        r = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["destination_area"] == "AIRPORT"
        assert body["premium_destination"] is not None
        assert body["premium_destination"]["code"] == "HKG_T1"
        assert body["requirements"]["silent_ride"] is True
        assert body["requirements"]["no_radio_music"] is True
        assert body["requirements"]["animal"]["kind"] == "cat"
        assert body["payment_preference"] == ["CASH", "OCTOPUS"]

    async def test_grab_copies_driver_payment_methods_onto_order(self, client):
        token = _mk_passenger(client)
        r = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {token}"},
            json=_order_payload(22.315, 114.219),
        )
        assert r.status_code == 201, r.text
        order_id = r.json()["id"]

        driver = _mk_active_driver(client, "+85260009902")
        dheaders = {"Authorization": f"Bearer {driver['token']}"}
        r = client.put(
            "/api/v1/drivers/me/payment-methods",
            json={"methods": ["CASH", "OCTOPUS"]},
            headers=dheaders,
        )
        assert r.status_code == 200, r.text

        r = client.post(f"/api/v1/orders/{order_id}/grab", headers=dheaders)
        assert r.status_code == 200, r.text
        assert r.json()["driver_payment_methods"] == ["CASH", "OCTOPUS"]

    async def test_unknown_payment_preference_is_422(self, client):
        token = _mk_passenger(client)
        r = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {token}"},
            json=_order_payload(
                22.315,
                114.219,
                payment_preference=["DOGE"],
            ),
        )
        assert r.status_code == 422, r.text

    async def test_animal_out_of_bounds_is_422(self, client):
        token = _mk_passenger(client)
        r = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {token}"},
            json=_order_payload(
                22.315,
                114.219,
                requirements={"animal": {"kind": "horse", "height_cm": "300", "weight_kg": "500"}},
            ),
        )
        assert r.status_code == 422, r.text
