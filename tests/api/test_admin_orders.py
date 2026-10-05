"""`/admin/orders` — the order monitoring view.

The console had no order page and no order API at all: `app/api/orders.py` is
entirely the passenger and driver view, which is deliberately identity-free
(a driver is never handed a passenger's account id). So when a passenger rang
to say the driver never arrived, there was no way to answer "what state is that
trip in, who is the driver, and how long ago did they accept".

These tests pin two properties that the passenger view cannot:

1. **The admin view carries identity.** It is the whole reason the route
   exists, and it would be easy to "clean up" by reusing `OrderOut`.
2. **It does not carry a *humanised* identity.** Ids, not names or phones.
   A list endpoint that inlines phone numbers turns scrolling the orders page
   into a bulk PII read.

The fixtures insert orders directly rather than driving the booking flow: the
question here is what the *admin* view returns, and going through
create/grab/arrive/complete would make every assertion depend on five other
endpoints being correct.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

OPEN_STATUSES = ("CREATED", "BROADCASTING", "ACCEPTED", "DRIVER_ARRIVED", "IN_TRIP")


async def _fetch(client, sql: str, params: dict | None = None) -> list[dict]:
    async with client.db_factory() as session:
        result = await session.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]


def _make_passenger(client) -> str:
    pid = uuid.uuid4()
    client.exec_sql(
        "INSERT INTO users (id, phone_e164, display_name, role, is_active, "
        "account_status, created_at, updated_at) "
        "VALUES (CAST(:i AS uuid), :p, :n, 'PASSENGER', true, 'ACTIVE', now(), now())",
        {
            "i": str(pid),
            "p": f"+8529{pid.int % 10**7:07d}",
            "n": f"p{pid.int % 10**8:08d}",
        },
    )
    return str(pid)


def _make_driver(client) -> str:
    """A driver *profile* id, which is what `orders.driver_id` references.

    Two UUID spaces are in play and they are easy to confuse: `users.id` is the
    account, `driver_profiles.id` is the driver. `orders.driver_id` is the
    latter, and a raw UUID with no profile row behind it is an FK violation
    rather than a driver nobody can name.
    """
    profile_id = uuid.uuid4()
    user_id = uuid.uuid4()
    client.exec_sql(
        "INSERT INTO users (id, phone_e164, display_name, role, is_active, "
        "account_status, created_at, updated_at) "
        "VALUES (CAST(:i AS uuid), :p, :n, 'DRIVER', true, 'ACTIVE', now(), now())",
        {
            "i": str(user_id),
            "p": f"+8529{user_id.int % 10**7:07d}",
            "n": f"d{user_id.int % 10**8:08d}",
        },
    )
    client.exec_sql(
        "INSERT INTO driver_profiles (id, user_id, status, taxi_type, "
        "taxi_driver_plate_no, vehicle_reg_mark, hk_id_last4, is_online, "
        "created_at, updated_at) "
        "VALUES (CAST(:i AS uuid), CAST(:u AS uuid), 'ACTIVE', 'URBAN', :plate, "
        ":reg, '1234', false, now(), now())",
        {
            "i": str(profile_id),
            "u": str(user_id),
            "plate": f"D{profile_id.int % 10**5:05d}",
            "reg": f"AA{profile_id.int % 10**4:04d}",
        },
    )
    return str(profile_id)


def _make_order(
    client,
    *,
    passenger_id: str,
    status: str = "COMPLETED",
    driver_id: str | None = None,
    accepted_at: datetime | None = None,
    arrived_at: datetime | None = None,
    completed_at: datetime | None = None,
    cancelled_at: datetime | None = None,
    cancellation_reason: str | None = None,
    created_at: datetime | None = None,
    distance_km: str = "5.250",
    fare: str = "130.20",
    fare_json: str = '{"meter_fare": "75.2", "total_fare": "130.2"}',
    broadcast_radius_km: str = "3.0",
    requirements_json: str | None = None,
    payment_methods: str | None = None,
    driver_payment_methods: str | None = None,
    receipt_requested: bool = False,
    fare_mode: str = "METER",
) -> str:
    oid = uuid.uuid4()
    created = created_at or datetime.now(UTC) - timedelta(minutes=30)
    client.exec_sql(
        "INSERT INTO orders ("
        " id, passenger_id, driver_id, status, pickup_location, pickup_address,"
        " dropoff_location, dropoff_address, distance_km, taxi_type, fare_json,"
        " tariff_version, estimated_total_hkd, discount_percent, broadcast_radius_km,"
        " fare_mode, requirements_json, payment_preference_json,"
        " driver_payment_methods_json, receipt_requested, accepted_at,"
        " driver_arrived_at, completed_at, cancelled_at,"
        " cancellation_reason, created_at, updated_at) VALUES ("
        " CAST(:oid AS uuid), CAST(:pid AS uuid), CAST(:did AS uuid),"
        " :status, ST_GeogFromText('POINT(114.158 22.284)'), 'Central',"
        " ST_GeogFromText('POINT(114.219 22.315)'), 'North Point',"
        " CAST(:dist AS numeric), 'URBAN', CAST(:fare_json AS jsonb), 'test-v1',"
        " CAST(:fare AS numeric), 0, CAST(:radius AS numeric), :fare_mode,"
        " CAST(:req AS jsonb), CAST(:pay AS jsonb), CAST(:dpm AS jsonb),"
        " CAST(:rcpt AS boolean),"
        " CAST(:accepted AS timestamptz), CAST(:arrived AS timestamptz),"
        " CAST(:completed AS timestamptz), CAST(:cancelled AS timestamptz),"
        " :reason, CAST(:created AS timestamptz), now())",
        {
            "oid": str(oid),
            "pid": passenger_id,
            "did": driver_id,
            "status": status,
            "dist": distance_km,
            "fare_json": fare_json,
            "fare": fare,
            "radius": broadcast_radius_km,
            "fare_mode": fare_mode,
            "req": requirements_json,
            "pay": payment_methods,
            "dpm": driver_payment_methods,
            "rcpt": receipt_requested,
            "accepted": accepted_at,
            "arrived": arrived_at,
            "completed": completed_at,
            "cancelled": cancelled_at,
            "reason": cancellation_reason,
            "created": created,
        },
    )
    return str(oid)


@pytest.fixture()
def passenger(client) -> str:
    return _make_passenger(client)


class TestAccess:
    def test_requires_authentication(self, client):
        assert client.get("/api/v1/admin/orders").status_code == 401

    def test_support_can_read_orders(self, client, passenger):
        """SUPPORT is exactly who takes the "the driver never showed" call.

        Restricted to OPERATIONS it would be a page the person answering the
        phone cannot open — the failure the module exists to fix.
        """
        _make_order(client, passenger_id=passenger)
        r = client.get("/api/v1/admin/orders", headers=client.admin_headers(role="SUPPORT"))
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 1


class TestListFilters:
    async def test_status_filter_narrows_to_that_status(self, client, passenger):
        _make_order(client, passenger_id=passenger, status="COMPLETED")
        _make_order(client, passenger_id=passenger, status="CANCELLED")
        r = client.get(
            "/api/v1/admin/orders",
            headers=client.admin_headers(),
            params={"status": "CANCELLED"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1
        assert [i["status"] for i in body["items"]] == ["CANCELLED"]

    async def test_unknown_status_is_a_400_not_an_empty_page(self, client):
        """A filter that silently matches nothing is how an operator concludes
        there were no cancelled trips this week. The allowed list is returned so
        the console can render a picker instead of guessing."""
        r = client.get(
            "/api/v1/admin/orders",
            headers=client.admin_headers(),
            params={"status": "NOT_A_STATUS"},
        )
        assert r.status_code == 400, r.text
        assert r.json()["details"]["reason"] == "UNKNOWN_STATUS"
        assert "COMPLETED" in r.json()["details"]["allowed"]

    async def test_open_only_excludes_terminal_states(self, client, passenger):
        """The most common question is not "cancelled trips" but "everything
        still moving". The set of open statuses belongs on the server, next to
        the enum, not re-listed in the client."""
        for status in (*OPEN_STATUSES, "COMPLETED", "CANCELLED"):
            _make_order(client, passenger_id=passenger, status=status)
        r = client.get(
            "/api/v1/admin/orders",
            headers=client.admin_headers(),
            params={"open_only": True},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == len(OPEN_STATUSES)
        assert {i["status"] for i in body["items"]} == set(OPEN_STATUSES)
        # The terminal ones are absent, and the total agrees with the page size.
        assert body["total"] == len(body["items"])

    async def test_passenger_and_driver_filters_compose(self, client, passenger):
        other = _make_passenger(client)
        driver = _make_driver(client)
        _make_order(client, passenger_id=passenger, driver_id=driver)
        _make_order(client, passenger_id=other, driver_id=driver)
        by_passenger = client.get(
            "/api/v1/admin/orders",
            headers=client.admin_headers(),
            params={"passenger_id": passenger},
        ).json()
        assert by_passenger["total"] == 1
        assert by_passenger["items"][0]["passenger_id"] == passenger

        by_driver = client.get(
            "/api/v1/admin/orders",
            headers=client.admin_headers(),
            params={"driver_id": driver},
        ).json()
        assert by_driver["total"] == 2

    async def test_date_range_is_half_open(self, client, passenger):
        """`>= since`, `< until` — matching `/audit`, so paging day by day does
        not show the boundary row twice."""
        base = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
        _make_order(client, passenger_id=passenger, created_at=base)
        _make_order(client, passenger_id=passenger, created_at=base + timedelta(days=1))
        r = client.get(
            "/api/v1/admin/orders",
            headers=client.admin_headers(),
            params={"since": base.isoformat(), "until": (base + timedelta(days=1)).isoformat()},
        )
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 1, "the upper bound must be exclusive"

    async def test_newest_first(self, client, passenger):
        base = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
        _make_order(client, passenger_id=passenger, created_at=base)
        _make_order(client, passenger_id=passenger, created_at=base + timedelta(hours=2))
        items = client.get("/api/v1/admin/orders", headers=client.admin_headers()).json()["items"]
        assert items[0]["created_at"] > items[1]["created_at"]


class TestRowShape:
    async def test_row_carries_identity_the_passenger_view_withholds(self, client, passenger):
        """This is why the route is not `OrderOut`. A driver must never see a
        passenger's account id; an operator investigating a complaint needs
        exactly that, and the driver who took the job."""
        driver = _make_driver(client)
        _make_order(client, passenger_id=passenger, driver_id=driver, status="IN_TRIP")
        item = client.get("/api/v1/admin/orders", headers=client.admin_headers()).json()["items"][0]
        assert item["passenger_id"] == passenger
        assert item["driver_id"] == driver
        assert item["pickup_address"] == "Central"
        assert item["dropoff_address"] == "North Point"

    async def test_row_carries_no_human_pii(self, client, passenger):
        """Ids, not names or phones. A list endpoint that inlines a phone number
        turns every scroll of the page into a bulk PII read."""
        _make_order(client, passenger_id=passenger)
        blob = client.get("/api/v1/admin/orders", headers=client.admin_headers()).text
        assert "+852" not in blob
        assert "phone" not in blob.lower()
        assert "display_name" not in blob

    async def test_money_uses_the_stored_2dp_rule_and_distance_the_meter_rule(
        self, client, passenger
    ):
        """`estimated_total_hkd` is a stored column (2 dp, `money_str`), while
        `distance_km` is a meter figure (1 dp, `meter_str`).

        Worth pinning because the two rules sit next to each other in the same
        row and are easy to swap — and a swapped one is a wrong number on
        screen, not a crash.

        `5.250` renders as `"5.3"`, not `"5.2"`: `meter_str` quantizes with
        `ROUND_HALF_UP` deliberately, so a half rounds away from zero. Asserting
        `"5.2"` here would have been asserting `ROUND_HALF_EVEN` — the
        `Decimal.quantize` default, and *not* what this codebase chose.
        """
        _make_order(client, passenger_id=passenger, fare="130.20", distance_km="5.250")
        item = client.get("/api/v1/admin/orders", headers=client.admin_headers()).json()["items"][0]
        assert item["estimated_total_hkd"] == "130.20"
        assert item["distance_km"] == "5.3"
        # A value that does not sit on the half stays put, so the assertion
        # above is about rounding rather than about always adding a tenth.
        _make_order(client, passenger_id=passenger, fare="99.00", distance_km="5.240")
        items = client.get("/api/v1/admin/orders", headers=client.admin_headers()).json()["items"]
        assert {i["distance_km"] for i in items} == {"5.3", "5.2"}

    async def test_unaccepted_order_has_null_driver_and_null_timestamps(self, client, passenger):
        """All-null with a CREATED status is meaningful, not missing data — it
        says nobody took the job, which is the answer to "why is this passenger
        still waiting"."""
        _make_order(client, passenger_id=passenger, status="CREATED")
        item = client.get("/api/v1/admin/orders", headers=client.admin_headers()).json()["items"][0]
        assert item["driver_id"] is None
        assert item["accepted_at"] is None
        assert item["completed_at"] is None


class TestFareMode:
    async def test_both_fare_modes_reach_the_list_row(self, client, passenger):
        """Without `fare_mode` the list cannot say whether a trip was metered
        or agreed at a fixed price — and that is the first thing an operator
        needs when a passenger disputes the amount."""
        _make_order(client, passenger_id=passenger, fare_mode="METER")
        _make_order(client, passenger_id=passenger, fare_mode="FIXED", status="CREATED")
        rows = client.get("/api/v1/admin/orders", headers=client.admin_headers()).json()["items"]
        assert {row["fare_mode"] for row in rows} == {"METER", "FIXED"}

    async def test_fare_mode_reaches_the_detail_view(self, client, passenger):
        oid = _make_order(client, passenger_id=passenger, fare_mode="FIXED")
        data = client.get(f"/api/v1/admin/orders/{oid}", headers=client.admin_headers()).json()
        assert data["fare_mode"] == "FIXED"

    async def test_the_filter_isolates_agreed_prices(self, client, passenger):
        """The audit question the fixed-fare feature created: "show me every
        trip where a price was agreed up front". A filter that quietly matched
        nothing would read as "there are none"."""
        _make_order(client, passenger_id=passenger, fare_mode="METER")
        _make_order(client, passenger_id=passenger, fare_mode="FIXED", status="CREATED")
        _make_order(client, passenger_id=passenger, fare_mode="FIXED", status="CANCELLED")

        rows = client.get(
            "/api/v1/admin/orders?fare_mode=FIXED", headers=client.admin_headers()
        ).json()
        assert rows["total"] == 2
        assert {row["fare_mode"] for row in rows["items"]} == {"FIXED"}

        metered = client.get(
            "/api/v1/admin/orders?fare_mode=METER", headers=client.admin_headers()
        ).json()
        assert metered["total"] == 1

    async def test_an_unknown_fare_mode_is_a_400_not_an_empty_list(self, client):
        """Same contract as `status`: a bad value is refused, because a filter
        that silently matches nothing is how an operator concludes there are no
        agreed-price trips this week."""
        r = client.get("/api/v1/admin/orders?fare_mode=BARGAIN", headers=client.admin_headers())
        assert r.status_code == 400, r.text
        assert r.json()["details"]["reason"] == "UNKNOWN_FARE_MODE"
        assert "FIXED" in r.json()["details"]["allowed"]

    async def test_the_filter_composes_with_status(self, client, passenger):
        """The two filters are separate axes, so they must AND, not replace."""
        _make_order(client, passenger_id=passenger, fare_mode="FIXED", status="CREATED")
        _make_order(client, passenger_id=passenger, fare_mode="FIXED", status="COMPLETED")
        rows = client.get(
            "/api/v1/admin/orders?fare_mode=FIXED&status=CREATED",
            headers=client.admin_headers(),
        ).json()
        assert rows["total"] == 1
        assert rows["items"][0]["status"] == "CREATED"


class TestDetail:
    async def test_detail_returns_the_frozen_snapshot_not_a_recomputation(self, client, passenger):
        """`orders.fare_json` is frozen at creation so a historical order stays
        auditable after a tariff change. The disputed amount is the one the
        passenger was quoted, which is why `tariff_version` rides along."""
        oid = _make_order(
            client,
            passenger_id=passenger,
            fare_json='{"meter_fare": "75.2", "total_fare": "130.2", "tunnels": 0}',
        )
        r = client.get(f"/api/v1/admin/orders/{oid}", headers=client.admin_headers())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["fare"]["meter_fare"] == "75.2"
        assert body["fare"]["total_fare"] == "130.2"
        assert body["tariff_version"] == "test-v1"

    async def test_timeline_has_a_fixed_length_with_server_computed_deltas(self, client, passenger):
        """Every step is present even when its timestamp is not, so the console
        renders a fixed-length timeline and a missing step is visibly missing
        rather than silently shorter."""
        created = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        oid = _make_order(
            client,
            passenger_id=passenger,
            status="COMPLETED",
            created_at=created,
            accepted_at=created + timedelta(minutes=2),
            arrived_at=created + timedelta(minutes=9),
            completed_at=created + timedelta(minutes=25),
        )
        body = client.get(f"/api/v1/admin/orders/{oid}", headers=client.admin_headers()).json()
        steps = {s["step"]: s for s in body["timeline"]}
        assert set(steps) == {"created", "accepted", "driver_arrived", "completed", "cancelled"}
        assert steps["created"]["elapsed_seconds"] == 0
        assert steps["accepted"]["elapsed_seconds"] == 120
        assert steps["driver_arrived"]["elapsed_seconds"] == 540
        assert steps["completed"]["elapsed_seconds"] == 1500
        # A step that never happened carries a null instant and a null delta,
        # rather than a missing key the client has to defend against.
        assert steps["cancelled"]["at"] is None
        assert steps["cancelled"]["elapsed_seconds"] is None

    async def test_detail_includes_ledger_rows_for_this_order_only(self, client, passenger):
        """A fare is not a ledger entry; only a penalty or a manual adjustment
        is. An empty list is itself worth seeing — it says no money moved."""
        driver = _make_driver(client)
        oid = _make_order(client, passenger_id=passenger, driver_id=driver)
        other_oid = _make_order(client, passenger_id=passenger, driver_id=driver)
        client.exec_sql(
            "INSERT INTO ledger_entries (driver_profile_id, entry_type, amount_hkd, "
            "balance_after_hkd, order_id, reference, note, created_at) "
            "VALUES (CAST(:d AS uuid), 'PENALTY_DEDUCTION', -50.00, 450.00, "
            "CAST(:o AS uuid), :ref, 'no-show', now())",
            {"d": driver, "o": oid, "ref": f"penalty:{oid}"},
        )
        body = client.get(f"/api/v1/admin/orders/{oid}", headers=client.admin_headers()).json()
        assert len(body["ledger"]["items"]) == 1
        assert body["ledger"]["items"][0]["amount_hkd"] == "-50.00"

        other = client.get(
            f"/api/v1/admin/orders/{other_oid}", headers=client.admin_headers()
        ).json()
        assert other["ledger"]["items"] == []

    async def test_cancellation_reason_is_surfaced(self, client, passenger):
        created = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        oid = _make_order(
            client,
            passenger_id=passenger,
            status="CANCELLED",
            created_at=created,
            cancelled_at=created + timedelta(minutes=3),
            cancellation_reason="HARBOUR_CROSSING_DECLINED",
        )
        body = client.get(f"/api/v1/admin/orders/{oid}", headers=client.admin_headers()).json()
        assert body["cancellation_reason"] == "HARBOUR_CROSSING_DECLINED"
        assert body["timeline"][-1]["step"] == "cancelled"
        assert body["timeline"][-1]["elapsed_seconds"] == 180

    async def test_unknown_order_is_a_404(self, client):
        r = client.get(f"/api/v1/admin/orders/{uuid.uuid4()}", headers=client.admin_headers())
        assert r.status_code == 404, r.text

    async def test_malformed_order_id_is_a_422_not_a_500(self, client):
        r = client.get("/api/v1/admin/orders/not-a-uuid", headers=client.admin_headers())
        assert r.status_code == 422


class TestDisputeRecord:
    """The frozen record of what the passenger asked for.

    These fields are the difference between an operator resolving a dispute
    from evidence and arbitrating between two phone recollections. "I asked for
    a silent car and he had the radio on", "I asked to pay by Octopus and he
    wanted cash", "I asked for a receipt and never got one" — each is a
    disagreement about a *request*, and the request was recorded at booking
    time by the platform, not recalled afterwards by either party.
    """

    async def test_detail_surfaces_the_requirements_the_passenger_set(self, client, passenger):
        oid = _make_order(
            client,
            passenger_id=passenger,
            requirements_json='{"silent_ride": true, "no_smoke": true, "animal": null}',
        )
        body = client.get(f"/api/v1/admin/orders/{oid}", headers=client.admin_headers()).json()
        assert body["requirements"]["silent_ride"] is True
        assert body["requirements"]["no_smoke"] is True
        # A JSON null survives the round trip as null, not as false: the driver
        # was told "no pet mentioned", which is not the same instruction.
        assert body["requirements"]["animal"] is None

    async def test_no_requirements_is_null_not_an_empty_object(self, client, passenger):
        """The distinction the write path preserves on purpose: `None` means
        the passenger was never asked about pets, `{}`-with-false-flags would
        mean they were asked and said no. Collapsing the two would make the
        console assert a requirement the passenger never stated."""
        oid = _make_order(client, passenger_id=passenger)
        body = client.get(f"/api/v1/admin/orders/{oid}", headers=client.admin_headers()).json()
        assert body["requirements"] is None

    async def test_both_sides_of_the_payment_question_are_visible(self, client, passenger):
        """The passenger's preference and the driver's declared methods are
        separate facts and the dispute is usually the *gap* between them."""
        oid = _make_order(
            client,
            passenger_id=passenger,
            payment_methods='{"methods": ["OCTOPUS", "CASH"]}',
            driver_payment_methods='{"methods": ["CASH"]}',
        )
        body = client.get(f"/api/v1/admin/orders/{oid}", headers=client.admin_headers()).json()
        assert body["payment_preference"] == ["OCTOPUS", "CASH"]
        assert body["driver_payment_methods"] == ["CASH"]

    async def test_missing_payment_records_are_empty_lists_not_null(self, client, passenger):
        """An order taken before the field existed has no row. An empty list
        renders as "nothing recorded", which is honest; `null` would force the
        console into a null check it would eventually forget."""
        oid = _make_order(client, passenger_id=passenger)
        body = client.get(f"/api/v1/admin/orders/{oid}", headers=client.admin_headers()).json()
        assert body["payment_preference"] == []
        assert body["driver_payment_methods"] == []

    async def test_the_receipt_request_is_visible_with_its_instant(self, client, passenger):
        """A receipt dispute is "did they ask, and when". The timestamp is the
        answer to "before or after the trip"."""
        oid = _make_order(client, passenger_id=passenger, receipt_requested=True)
        body = client.get(f"/api/v1/admin/orders/{oid}", headers=client.admin_headers()).json()
        assert body["receipt_requested"] is True

    async def test_the_list_row_does_not_carry_the_dispute_blob(self, client, passenger):
        """Scoping check: the requirements/receipt payload is detail-only. The
        list is a fixed-column table, so a JSONB blob per row for every scroll
        is a cost with no reader."""
        _make_order(
            client,
            passenger_id=passenger,
            requirements_json='{"silent_ride": true}',
            receipt_requested=True,
        )
        item = client.get("/api/v1/admin/orders", headers=client.admin_headers()).json()["items"][0]
        leaked_keys = {
            "requirements",
            "payment_preference",
            "driver_payment_methods",
            "receipt_requested",
        }
        leaked = leaked_keys & set(item)
        assert not leaked, f"dispute blob leaked into the list row: {leaked}"
