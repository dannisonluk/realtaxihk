"""The service-area gate: only Hong Kong may use the platform.

Two things are being tested and they are different in kind.

The first is that the boundary is *correct* — that Shenzhen is refused. The
box this replaced admitted it, so a regression here would silently restore a
gate that does not gate. `test_shenzhen_is_refused_on_every_write_path` is the
one that matters.

The second is that the gate is *reachable from the outside*. A boundary check
that exists only as a helper function, and is never called on a write path, is
worse than no check at all: it looks like protection. So each write path is
exercised through the real HTTP surface with a real Shenzhen coordinate.
"""

from __future__ import annotations

import pytest

# Real coordinates. Shenzhen Futian is a few kilometres north of the Hong Kong
# border and falls squarely inside the old bounding box.
SHENZHEN_FUTIAN = (22.5410, 114.0540)
SHENZHEN_LUOHU = (22.5480, 114.1230)
CENTRAL = (22.284, 114.158)
NORTH_POINT = (22.315, 114.219)


def _order_payload(pickup, dropoff, *, taxi_type: str = "URBAN") -> dict:
    return {
        "pickup_lat": pickup[0],
        "pickup_lng": pickup[1],
        "dropoff_lat": dropoff[0],
        "dropoff_lng": dropoff[1],
        "pickup_address": "Test pickup",
        "dropoff_address": "Test dropoff",
        "distance_km": "4.2",
        "taxi_type": taxi_type,
    }


def _active_driver(client, phone: str) -> dict:
    """A driver who may stream location: registered, approved, deposited."""
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
    return {"token": token, "driver_id": driver_id}


@pytest.fixture()
def passenger_token(client):
    return client.activate("+85291600001")


class TestOrderCreationIsGated:
    def test_hong_kong_order_is_accepted(self, client, passenger_token):
        """The gate must not refuse legitimate Hong Kong trips."""
        r = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {passenger_token}"},
            json=_order_payload(CENTRAL, NORTH_POINT),
        )
        assert r.status_code == 201, r.text

    def test_shenzhen_pickup_is_refused(self, client, passenger_token):
        r = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {passenger_token}"},
            json=_order_payload(SHENZHEN_FUTIAN, NORTH_POINT),
        )
        assert r.status_code == 403, r.text
        body = r.json()
        assert body["code"] == "FORBIDDEN"
        assert body["details"]["reason"] == "OUTSIDE_HK"
        assert body["details"]["field"] == "pickup"

    def test_shenzhen_dropoff_is_refused(self, client, passenger_token):
        r = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {passenger_token}"},
            json=_order_payload(CENTRAL, SHENZHEN_LUOHU),
        )
        assert r.status_code == 403, r.text
        assert r.json()["details"]["field"] == "dropoff"

    def test_the_refusal_names_the_offending_coordinate(self, client, passenger_token):
        """Without the echo, a client cannot tell which of the two points failed."""
        r = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {passenger_token}"},
            json=_order_payload(SHENZHEN_FUTIAN, NORTH_POINT),
        )
        details = r.json()["details"]
        assert details["lat"] == pytest.approx(SHENZHEN_FUTIAN[0])
        assert details["lng"] == pytest.approx(SHENZHEN_FUTIAN[1])

    def test_the_old_bounding_box_coordinate_is_now_refused(self, client, passenger_token):
        """The exact regression: this payload passed the box check.

        `lat 22.1-22.6, lng 113.8-114.5` contains Shenzhen Futian, so before
        the polygon this request created an order in Shenzhen.
        """
        lat, lng = SHENZHEN_FUTIAN
        assert 22.1 <= lat <= 22.6 and 113.8 <= lng <= 114.5, "test premise: inside old box"
        r = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {passenger_token}"},
            json=_order_payload((lat, lng), NORTH_POINT),
        )
        assert r.status_code == 403


class TestDriverLocationIsGated:
    def test_hong_kong_location_is_accepted(self, client):
        d = _active_driver(client, "+85291600002")
        r = client.post(
            "/api/v1/drivers/location",
            headers={"Authorization": f"Bearer {d['token']}"},
            json={"lat": CENTRAL[0], "lng": CENTRAL[1], "online": True},
        )
        assert r.status_code == 200, r.text

    def test_shenzhen_location_is_refused(self, client):
        """A driver in Shenzhen must not be able to publish a position.

        This is the one that protects the dispatch index: the coordinate this
        rejects is the one that would otherwise be fed to `geo:orders:active`
        and to every passenger's map.
        """
        d = _active_driver(client, "+85291600003")
        r = client.post(
            "/api/v1/drivers/location",
            headers={"Authorization": f"Bearer {d['token']}"},
            json={"lat": SHENZHEN_FUTIAN[0], "lng": SHENZHEN_FUTIAN[1], "online": True},
        )
        assert r.status_code == 403, r.text
        assert r.json()["details"]["reason"] == "OUTSIDE_HK"

    async def test_a_refused_location_does_not_move_the_stored_position(self, client):
        """The refusal must happen *before* the write, not after it.

        Checked against the database rather than the response, because a
        handler that wrote and then raised would still return 403 while leaving
        the driver parked in Shenzhen — and the stored coordinate is what
        dispatch and every passenger's map actually read.
        """
        from sqlalchemy import text

        d = _active_driver(client, "+85291600004")
        ok = client.post(
            "/api/v1/drivers/location",
            headers={"Authorization": f"Bearer {d['token']}"},
            json={"lat": CENTRAL[0], "lng": CENTRAL[1], "online": True},
        )
        assert ok.status_code == 200, ok.text
        refused = client.post(
            "/api/v1/drivers/location",
            headers={"Authorization": f"Bearer {d['token']}"},
            json={"lat": SHENZHEN_FUTIAN[0], "lng": SHENZHEN_FUTIAN[1], "online": True},
        )
        assert refused.status_code == 403, refused.text

        async with client.db_factory() as session:
            stored = (
                await session.execute(
                    text("SELECT ST_AsText(current_location) FROM driver_profiles WHERE id = :id"),
                    {"id": d["driver_id"]},
                )
            ).scalar()

        assert "114.158" in stored, f"position moved despite the refusal: {stored}"
        assert "114.054" not in stored, f"driver was parked in Shenzhen: {stored}"


class TestServiceAreaCheckEndpoint:
    def test_reports_allowed_inside_hong_kong(self, client, passenger_token):
        r = client.get(
            "/api/v1/service-area/check",
            headers={"Authorization": f"Bearer {passenger_token}"},
            params={"lat": CENTRAL[0], "lng": CENTRAL[1]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["allowed"] is True
        assert r.json()["reason"] is None

    def test_reports_not_allowed_outside_without_an_error_status(self, client, passenger_token):
        """A valid question with a negative answer is a 200, not a 403.

        Making this an error would push every client into treating an expected
        outcome as an exception, which is how a boundary check ends up in a
        crash log instead of on a screen.
        """
        r = client.get(
            "/api/v1/service-area/check",
            headers={"Authorization": f"Bearer {passenger_token}"},
            params={"lat": SHENZHEN_FUTIAN[0], "lng": SHENZHEN_FUTIAN[1]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["allowed"] is False
        assert r.json()["reason"] == "OUTSIDE_HK"

    def test_bounds_are_a_superset_of_the_polygons(self, client, passenger_token):
        from app.core.hk_bounds import HK_POLYGONS

        r = client.get(
            "/api/v1/service-area/bounds",
            headers={"Authorization": f"Bearer {passenger_token}"},
        )
        assert r.status_code == 200, r.text
        b = r.json()
        for polygon in HK_POLYGONS:
            for point in polygon:
                assert b["lat_min"] <= point.lat <= b["lat_max"]
                assert b["lng_min"] <= point.lng <= b["lng_max"]

    def test_requires_authentication(self, client):
        r = client.get("/api/v1/service-area/check", params={"lat": 22.28, "lng": 114.15})
        assert r.status_code == 401
