"""The live map: where the fleet is, and what each car is doing.

`GET /api/v1/admin/live/drivers` exists so an operator can see the whole fleet on
one screen. The properties tested here are the ones that decide whether that
screen can be trusted:

* a driver with no fix never appears — a marker with no position is not
  something the map could draw, so the query must exclude it rather than leaving
  it to the client to notice;
* longitude and latitude are not swapped. `ST_AsText` renders `POINT(lng lat)`,
  the opposite order to how every other layer here speaks, and a transposition
  puts the entire fleet in the wrong ocean while looking perfectly plausible;
* exactly one row per driver, even if a driver somehow holds two active orders,
  because a duplicate renders as two identical cars rather than as an error;
* an order that has finished — or has not started — does not attach to a car, or
  the map keeps showing trips that ended hours ago;
* `truncated` is honest, because a map that silently drops the 501st car is
  indistinguishable from a fleet that shrank;
* and the endpoint is not reachable without an admin session.
"""

from __future__ import annotations

import uuid

import pytest

LIVE = "/api/v1/admin/live/drivers"


def _mk_user(client, *, phone: str, role: str = "DRIVER") -> str:
    uid = uuid.uuid4()
    client.exec_sql(
        "INSERT INTO users (id, phone_e164, display_name, role, is_active, "
        "account_status, created_at, updated_at) "
        "VALUES (CAST(:i AS uuid), :p, 'Live Map Test', :r, true, 'ACTIVE', now(), now())",
        {"i": str(uid), "p": phone, "r": role},
    )
    return str(uid)


def _mk_driver(
    client,
    *,
    phone: str,
    lat: float | None = None,
    lng: float | None = None,
    is_online: bool = True,
    reg: str = "AA1234",
    plate: str = "BB5678",
) -> tuple[str, str]:
    """A driver profile, optionally with a persisted position.

    `lat=None` is the "never pushed a fix" case, which is inserted without the
    location columns rather than with a NULL point — the two are the same to the
    query, and this keeps the SQL honest about which case it is building.
    """
    uid = _mk_user(client, phone=phone)
    pid = uuid.uuid4()
    columns = (
        "INSERT INTO driver_profiles (id, user_id, status, taxi_type, "
        "taxi_driver_plate_no, vehicle_reg_mark, hk_id_last4, is_online, "
    )
    if lat is None:
        client.exec_sql(
            columns + "created_at, updated_at) "
            "VALUES (CAST(:i AS uuid), CAST(:u AS uuid), 'ACTIVE', 'URBAN', :plate, :reg, "
            "'1234', :online, now(), now())",
            {"i": str(pid), "u": uid, "plate": plate, "reg": reg, "online": is_online},
        )
    else:
        client.exec_sql(
            columns + "current_location, last_location_at, created_at, updated_at) "
            "VALUES (CAST(:i AS uuid), CAST(:u AS uuid), 'ACTIVE', 'URBAN', :plate, :reg, "
            "'1234', :online, ST_GeogFromText(:wkt), now(), now(), now())",
            {
                "i": str(pid),
                "u": uid,
                "plate": plate,
                "reg": reg,
                "online": is_online,
                "wkt": f"SRID=4326;POINT({lng} {lat})",
            },
        )
    return uid, str(pid)


def _mk_order(client, *, passenger_id: str, driver_id: str | None, status: str) -> str:
    oid = uuid.uuid4()
    client.exec_sql(
        """
        INSERT INTO orders (
            id, passenger_id, driver_id, status, pickup_location, pickup_address,
            dropoff_location, dropoff_address, distance_km, taxi_type,
            fare_json, tariff_version, estimated_total_hkd, discount_percent,
            broadcast_radius_km, fare_mode, created_at, updated_at
        ) VALUES (
            CAST(:oid AS uuid), CAST(:pid AS uuid),
            CAST(:did AS uuid), :status,
            ST_GeogFromText('POINT(114.158 22.284)'), 'Central',
            ST_GeogFromText('POINT(114.219 22.315)'), 'North Point',
            8.5, 'URBAN', '{}'::jsonb, 'test-v1', 120.00, 0, 3.0, 'METER', now(), now()
        )
        """,
        {"oid": str(oid), "pid": passenger_id, "did": driver_id, "status": status},
    )
    return str(oid)


class TestTheFleet:
    def test_a_driver_with_a_fix_appears_with_the_correct_coordinates(self, client):
        """The coordinate order is asserted, not assumed.

        `ST_AsText` renders `POINT(lng lat)`. Returning those two in the order
        they are read puts Hong Kong in the Indian Ocean, and the payload still
        looks entirely reasonable — which is exactly why this is pinned.
        """
        _, pid = _mk_driver(client, phone="+85290000001", lat=22.3193, lng=114.1694)

        body = client.get(LIVE, headers=client.admin_headers()).json()

        assert body["truncated"] is False
        assert len(body["drivers"]) == 1
        car = body["drivers"][0]
        assert car["driver_profile_id"] == pid
        assert car["lat"] == pytest.approx(22.3193)
        assert car["lng"] == pytest.approx(114.1694)
        assert car["is_online"] is True
        assert car["vehicle_reg_mark"] == "AA1234"
        assert car["last_location_at"] is not None
        assert body["generated_at"]

    def test_a_driver_without_a_fix_is_not_on_the_map(self, client):
        _mk_driver(client, phone="+85290000002")

        body = client.get(LIVE, headers=client.admin_headers()).json()

        assert body["drivers"] == []

    def test_an_offline_driver_is_hidden_unless_asked_for(self, client):
        """A car that has gone home should not clutter the operational view, but
        "where was it when it stopped" is still a question worth being able to
        answer — hence the flag rather than a hard filter."""
        _mk_driver(client, phone="+85290000003", lat=22.32, lng=114.17, is_online=False)
        headers = client.admin_headers()

        assert client.get(LIVE, headers=headers).json()["drivers"] == []

        asked = client.get(f"{LIVE}?include_offline=true", headers=headers).json()
        assert len(asked["drivers"]) == 1


class TestTheTrip:
    def test_an_active_order_attaches_to_its_car(self, client):
        passenger = _mk_user(client, phone="+85290000010", role="PASSENGER")
        _, pid = _mk_driver(client, phone="+85290000011", lat=22.32, lng=114.17)
        oid = _mk_order(client, passenger_id=passenger, driver_id=pid, status="IN_TRIP")

        car = client.get(LIVE, headers=client.admin_headers()).json()["drivers"][0]

        assert car["order_id"] == oid
        assert car["order_status"] == "IN_TRIP"

    @pytest.mark.parametrize("status", ["CREATED", "BROADCASTING", "COMPLETED", "CANCELLED"])
    def test_an_order_that_is_not_being_run_does_not_attach(self, client, status):
        """`BROADCASTING` is the subtle one.

        It is an in-flight order, so a filter written as "not finished" would
        include it — but it has no driver yet, so a car showing a trip because
        of it is a car showing a trip that is not happening.
        """
        passenger = _mk_user(client, phone="+85290000020", role="PASSENGER")
        _, pid = _mk_driver(client, phone="+85290000021", lat=22.32, lng=114.17)
        _mk_order(client, passenger_id=passenger, driver_id=pid, status=status)

        car = client.get(LIVE, headers=client.admin_headers()).json()["drivers"][0]

        assert car["order_id"] is None
        assert car["order_status"] is None

    def test_two_active_orders_still_render_one_car(self, client):
        """The `LEFT JOIN LATERAL ... LIMIT 1` is what guarantees this.

        A plain `LEFT JOIN orders ON o.driver_id = d.id AND o.status IN (...)`
        emits the driver once per matching order, and the map draws two
        identical cars — a state-machine violation rendered as a cosmetic
        duplicate, which is the worst way for it to surface.
        """
        passenger = _mk_user(client, phone="+85290000030", role="PASSENGER")
        _, pid = _mk_driver(client, phone="+85290000031", lat=22.32, lng=114.17)
        _mk_order(client, passenger_id=passenger, driver_id=pid, status="ACCEPTED")
        _mk_order(client, passenger_id=passenger, driver_id=pid, status="IN_TRIP")

        drivers = client.get(LIVE, headers=client.admin_headers()).json()["drivers"]

        assert len(drivers) == 1


class TestTruncation:
    def test_truncated_is_true_only_when_a_car_was_actually_dropped(self, client):
        """`len(rows) == limit` cannot tell "exactly this many" from "more, cut
        off", which is why one extra row is fetched and then discarded."""
        for i in range(3):
            _mk_driver(client, phone=f"+8529000010{i}", lat=22.30 + i / 100, lng=114.17)
        headers = client.admin_headers()

        exactly = client.get(f"{LIVE}?limit=3", headers=headers).json()
        assert len(exactly["drivers"]) == 3
        assert exactly["truncated"] is False

        cut = client.get(f"{LIVE}?limit=2", headers=headers).json()
        assert len(cut["drivers"]) == 2
        assert cut["truncated"] is True


class TestAccess:
    def test_an_anonymous_caller_is_refused(self, client):
        assert client.get(LIVE).status_code == 401

    def test_a_passenger_token_is_refused(self, client):
        """The map reveals where every driver is. A passenger token must not be
        enough to enumerate the fleet, however well-formed it is."""
        token = client.activate("+85290000090", username="live_map_passenger")

        response = client.get(LIVE, headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 403, response.text
