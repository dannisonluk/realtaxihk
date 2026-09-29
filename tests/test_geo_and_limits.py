"""TDD — geo dispatch (Redis GEOSEARCH) + rate limiting (Redis INCR windows)."""


def _mk_user_token(client, phone_expr: str) -> str:
    client.post("/api/v1/auth/otp/request", json={"phone_e164": phone_expr})
    # SEC-02: the code is never in the response; read it at the notify seam.
    r = client.post(
        "/api/v1/auth/otp/verify",
        json={"phone_e164": phone_expr, "code": client.otp_inbox[phone_expr]},
    )
    return r.json()["access_token"]


def _admin_headers() -> dict:
    from conftest import ADMIN_ID

    from app.core.security import create_access_token

    return {"Authorization": "Bearer " + create_access_token({"sub": ADMIN_ID, "role": "ADMIN"})}


def _mk_active_driver(client, phone_expr: str):
    token = _mk_user_token(client, phone_expr)
    r = client.post(
        "/api/v1/drivers/register",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "hk_id_last4": "0000",
            "taxi_driver_plate_no": f"TD{phone_expr[-6:]}",
            "vehicle_reg_mark": "ZZ0000",
            "taxi_type": "URBAN",
        },
    )
    did = r.json()["id"]
    client.post(
        f"/api/v1/admin/drivers/{did}/review",
        headers=_admin_headers(),
        json={"decision": "approve"},
    )
    client.post(
        f"/api/v1/admin/drivers/{did}/deposit/grant",
        headers=_admin_headers(),
        json={"amount_hkd": "500.00"},
    )
    return {"token": token, "driver_id": did}


_ORDER = {
    "pickup_lat": 22.284,
    "pickup_lng": 114.158,  # Central
    "dropoff_lat": 22.315,
    "dropoff_lng": 114.219,  # North Point
    "pickup_address": "Statue Square, Central",
    "dropoff_address": "Harbour North, North Point",
    "distance_km": "4.2",
    "taxi_type": "URBAN",
}


class TestRateLimiter:
    def test_allow_within_limit_then_deny(self, client):
        import asyncio

        from app.core.rate_limit import RateLimiter

        async def run():
            # The app's shared per-loop client. The app owns it and closes it at
            # shutdown, so the test must not close it either.
            rl = RateLimiter(client.app.state.redis_factory())
            key = f"test:{id(object())}"
            return [await rl.allow(key, limit=3, window_s=60) for _ in range(4)]

        results = asyncio.run(run())
        assert results == [True, True, True, False]

    def test_order_creation_rate_limited(self, client):
        pax = _mk_user_token(client, f"+852{916 * 10**5 + 20001}")
        statuses = []
        for i in range(6):
            r = client.post(
                "/api/v1/orders",
                headers={"Authorization": f"Bearer {pax}"},
                json={**_ORDER, "pickup_address": f"Pick {i}"},
            )
            statuses.append(r.status_code)
        assert statuses[:5] == [201] * 5
        assert statuses[5] == 429
        body = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {pax}"},
            json=_ORDER,
        ).json()
        assert body["code"] == "RATE_LIMITED"


class TestGeoDispatch:
    def test_driver_location_update_and_nearby(self, client):
        d = _mk_active_driver(client, f"+852{916 * 10**5 + 30001}")
        h = {"Authorization": f"Bearer {d['token']}"}
        r = client.post(
            "/api/v1/driver/location",
            headers=h,
            json={"lat": 22.284, "lng": 114.158, "online": True},
        )
        assert r.status_code == 200

        pax = _mk_user_token(client, f"+852{916 * 10**5 + 30002}")
        r = client.post("/api/v1/orders", headers={"Authorization": f"Bearer {pax}"}, json=_ORDER)
        assert r.status_code == 201
        oid = r.json()["id"]

        r = client.get(
            "/api/v1/orders/nearby",
            headers=h,
            params={"lat": 22.284, "lng": 114.158, "radius_km": 3},
        )
        assert r.status_code == 200
        ids = [o["id"] for o in r.json()["items"]]
        assert oid in ids
        assert all(o["status"] == "BROADCASTING" for o in r.json()["items"])

    def test_far_order_not_in_nearby(self, client):
        d = _mk_active_driver(client, f"+852{916 * 10**5 + 30003}")
        h = {"Authorization": f"Bearer {d['token']}"}
        client.post(
            "/api/v1/driver/location",
            headers=h,
            json={"lat": 22.308, "lng": 113.918, "online": True},  # Tung Chung ~25km
        )
        pax = _mk_user_token(client, f"+852{916 * 10**5 + 30004}")
        client.post("/api/v1/orders", headers={"Authorization": f"Bearer {pax}"}, json=_ORDER)
        r = client.get(
            "/api/v1/orders/nearby",
            headers=h,
            params={"lat": 22.308, "lng": 113.918, "radius_km": 3},
        )
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_accepted_order_leaves_geo_index(self, client):
        d = _mk_active_driver(client, f"+852{916 * 10**5 + 30005}")
        h = {"Authorization": f"Bearer {d['token']}"}
        client.post(
            "/api/v1/driver/location",
            headers=h,
            json={"lat": 22.284, "lng": 114.158, "online": True},
        )
        pax = _mk_user_token(client, f"+852{916 * 10**5 + 30006}")
        oid = client.post(
            "/api/v1/orders", headers={"Authorization": f"Bearer {pax}"}, json=_ORDER
        ).json()["id"]
        r = client.get(
            "/api/v1/orders/nearby",
            headers=h,
            params={"lat": 22.284, "lng": 114.158, "radius_km": 3},
        )
        assert oid in [o["id"] for o in r.json()["items"]]

        client.post(f"/api/v1/orders/{oid}/grab", headers=h)
        r = client.get(
            "/api/v1/orders/nearby",
            headers=h,
            params={"lat": 22.284, "lng": 114.158, "radius_km": 3},
        )
        assert oid not in [o["id"] for o in r.json()["items"]]

    def test_location_requires_active_driver(self, client):
        token = _mk_user_token(client, f"+852{916 * 10**5 + 30007}")  # passenger
        r = client.post(
            "/api/v1/driver/location",
            headers={"Authorization": f"Bearer {token}"},
            json={"lat": 22.284, "lng": 114.158, "online": True},
        )
        assert r.status_code == 403
