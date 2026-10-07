"""Admin analytics: bucketed earnings and the hour-of-day profile.

The tests that carry weight here are the timezone ones. Every bucket boundary
is a chance to be quietly wrong: `completed_at` is a `timestamptz`, so grouping
instants directly gives **UTC** days, and a trip finished at 08:30 Hong Kong
time would land in yesterday. The numbers would still look plausible, which is
what makes it worth a test rather than a comment.

The orders are inserted directly rather than driven through the HTTP lifecycle.
The lifecycle is covered by `test_orders_module`; what is under test here is
the aggregation, and a direct insert lets a test place a completion at an exact
instant — which is the whole point of a timezone test and cannot be done by
clicking through a flow.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.money import money_str

# Every instant below is written in UTC and described in Hong Kong time, because
# the gap between them is the thing being tested.
HKT = "+08:00"


def _insert_completed_order(
    client,
    *,
    completed_at: datetime,
    fare: str = "100.00",
    taxi_type: str = "URBAN",
    distance_km: str = "5.000",
) -> uuid.UUID:
    """Insert a COMPLETED order with an exact completion instant.

    Needs a passenger row because `orders.passenger_id` is a NOT NULL FK. The
    row is reused across inserts within a test, so the FK is satisfied without
    inventing a new user per order.
    """
    passenger_id = uuid.uuid4()
    order_id = uuid.uuid4()
    client.exec_sql(
        """
        INSERT INTO users (
            id, phone_e164, display_name, role, is_active, account_status,
            created_at, updated_at
        ) VALUES (
            :pid, :phone, :dname, 'PASSENGER', true, 'ACTIVE', now(), now()
        )
        """,
        {
            "pid": str(passenger_id),
            "phone": f"+8529{passenger_id.int % 10**7:07d}",
            "dname": f"p{passenger_id.int % 10**8:08d}",
        },
    )
    client.exec_sql(
        """
        INSERT INTO orders (
            id, passenger_id, status, pickup_location, pickup_address,
            dropoff_location, dropoff_address, distance_km, taxi_type,
            fare_json, tariff_version, estimated_total_hkd, discount_percent,
            broadcast_radius_km, fare_mode, completed_at, created_at, updated_at
        ) VALUES (
            :oid, :pid, 'COMPLETED',
            ST_GeogFromText('POINT(114.158 22.284)'), 'Central',
            ST_GeogFromText('POINT(114.219 22.315)'), 'North Point',
            :dist, :taxi, '{}'::jsonb, 'test-v1', :fare, 0, 3.0, 'METER',
            :completed, now(), now()
        )
        """,
        {
            "oid": str(order_id),
            "pid": str(passenger_id),
            "dist": distance_km,
            "taxi": taxi_type,
            "fare": fare,
            "completed": completed_at,
        },
    )
    return order_id


def _insert_created_order(
    client,
    *,
    created_at: datetime,
    status: str,
    taxi_type: str = "URBAN",
    accepted_at: datetime | None = None,
    driver_arrived_at: datetime | None = None,
    completed_at: datetime | None = None,
    cancellation_reason: str | None = None,
    fare: str = "100.00",
    driver_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert an order created at an exact instant for operations analytics.

    The revenue tests need exact *completion* instants; the ops funnel needs
    exact *creation* instants plus optional stage timestamps and cancellation
    metadata. One helper keeps all four in the same test vocabulary.
    """
    passenger_id = uuid.uuid4()
    order_id = uuid.uuid4()
    client.exec_sql(
        """
        INSERT INTO users (
            id, phone_e164, display_name, role, is_active, account_status,
            created_at, updated_at
        ) VALUES (
            :pid, :phone, :dname, 'PASSENGER', true, 'ACTIVE', now(), now()
        )
        """,
        {
            "pid": str(passenger_id),
            "phone": f"+8529{passenger_id.int % 10**7:07d}",
            "dname": f"ops{passenger_id.int % 10**8:08d}",
        },
    )
    client.exec_sql(
        """
        INSERT INTO orders (
            id, passenger_id, driver_id, status, pickup_location, pickup_address,
            dropoff_location, dropoff_address, distance_km, taxi_type,
            fare_json, tariff_version, estimated_total_hkd, discount_percent,
            broadcast_radius_km, fare_mode, accepted_at, driver_arrived_at,
            completed_at, cancellation_reason, created_at, updated_at
        ) VALUES (
            :oid, :pid, :did, :status,
            ST_GeogFromText('POINT(114.158 22.284)'), 'Central',
            ST_GeogFromText('POINT(114.219 22.315)'), 'North Point',
            5.000, :taxi, '{}'::jsonb, 'test-v1', :fare, 0, 3.0, 'METER',
            :accepted, :arrived, :completed, :reason, :created, now()
        )
        """,
        {
            "oid": str(order_id),
            "pid": str(passenger_id),
            "did": str(driver_id) if driver_id else None,
            "status": status,
            "taxi": taxi_type,
            "fare": fare,
            "accepted": accepted_at,
            "arrived": driver_arrived_at,
            "completed": completed_at,
            "reason": cancellation_reason,
            "created": created_at,
        },
    )
    return order_id


def _insert_driver_profile(
    client,
    *,
    status: str = "ACTIVE",
    taxi_type: str = "URBAN",
    is_online: bool = False,
    has_gps: bool = False,
) -> uuid.UUID:
    """A raw `driver_profiles` row with a matching user account."""
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
    if has_gps:
        client.exec_sql(
            "INSERT INTO driver_profiles (id, user_id, status, taxi_type, "
            "taxi_driver_plate_no, vehicle_reg_mark, hk_id_last4, is_online, "
            "current_location, created_at, updated_at) "
            "VALUES (CAST(:i AS uuid), CAST(:u AS uuid), :s, :taxi, :plate, :reg, "
            "'1234', :online, ST_GeogFromText('POINT(114.18 22.30)'), now(), now())",
            {
                "i": str(profile_id),
                "u": str(user_id),
                "s": status,
                "taxi": taxi_type,
                "plate": f"D{profile_id.int % 10**5:05d}",
                "reg": f"AA{profile_id.int % 10**4:04d}",
                "online": is_online,
            },
        )
    else:
        client.exec_sql(
            "INSERT INTO driver_profiles (id, user_id, status, taxi_type, "
            "taxi_driver_plate_no, vehicle_reg_mark, hk_id_last4, is_online, "
            "created_at, updated_at) "
            "VALUES (CAST(:i AS uuid), CAST(:u AS uuid), :s, :taxi, :plate, :reg, "
            "'1234', :online, now(), now())",
            {
                "i": str(profile_id),
                "u": str(user_id),
                "s": status,
                "taxi": taxi_type,
                "plate": f"D{profile_id.int % 10**5:05d}",
                "reg": f"AA{profile_id.int % 10**4:04d}",
                "online": is_online,
            },
        )
    return profile_id


def _insert_cancel_event(
    client,
    order_id: uuid.UUID,
    *,
    actor_kind: str,
    event: str = "STATE_CHANGED",
    from_status: str = "BROADCASTING",
    actor_id: str | None = None,
    created_at: datetime | None = None,
) -> None:
    """Insert a cancellation timeline event for attribution tests."""
    client.exec_sql(
        """
        INSERT INTO order_events (
            order_id, event, from_status, to_status, actor_kind, actor_id,
            payload, created_at
        ) VALUES (
            :oid, :event, :from_status, 'CANCELLED', :actor_kind, :actor_id,
            NULL, COALESCE(:created, now())
        )
        """,
        {
            "oid": str(order_id),
            "event": event,
            "from_status": from_status,
            "actor_kind": actor_kind,
            "actor_id": actor_id,
            "created": created_at,
        },
    )


def _as_hkt(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """An instant expressed as Hong Kong local time, returned UTC-aware.

    `datetime(..., tzinfo=timezone(timedelta(hours=8)))` then converted, so the
    test reads as the local time a human would write and the database receives
    the correct instant.
    """
    from datetime import timezone

    return datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=8))).astimezone(
        UTC
    )


@pytest.fixture()
def admin(client):
    return client.admin_headers()


class TestAnalyticsAccess:
    def test_requires_admin(self, client):
        r = client.get("/api/v1/admin/analytics")
        assert r.status_code == 401

    def test_a_passenger_token_is_refused(self, client):
        token = client.activate("+85291700001")
        r = client.get("/api/v1/admin/analytics", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    def test_defaults_to_the_last_thirty_days(self, client, admin):
        r = client.get("/api/v1/admin/analytics", headers=admin)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["totals"]["days"] == 30
        assert body["range"]["timezone"] == "Asia/Hong_Kong"

    def test_an_inverted_range_is_rejected(self, client, admin):
        r = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-09-30", "to": "2026-09-01"},
        )
        assert r.status_code == 422
        assert r.json()["details"]["reason"] == "RANGE_INVERTED"

    def test_an_absurdly_wide_range_is_rejected(self, client, admin):
        """Without a cap this asks the database to bucket a century of orders."""
        r = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "1900-01-01", "to": "2026-09-30"},
        )
        assert r.status_code == 422
        assert r.json()["details"]["reason"] == "RANGE_TOO_WIDE"

    def test_a_bad_granularity_is_rejected(self, client, admin):
        r = client.get(
            "/api/v1/admin/analytics", headers=admin, params={"granularity": "fortnight"}
        )
        assert r.status_code == 422

    def test_a_bad_taxi_type_is_rejected(self, client, admin):
        r = client.get("/api/v1/admin/analytics", headers=admin, params={"taxi_type": "HELICOPTER"})
        assert r.status_code == 422


class TestEarningsAggregation:
    def test_empty_range_reports_zeroes_not_an_error(self, client, admin):
        r = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-01-01", "to": "2026-01-07"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["buckets"] == []
        assert body["totals"]["orders"] == 0
        assert body["totals"]["earnings_hkd"] == "0.00"
        # No division by zero on an empty range.
        assert body["totals"]["avg_fare_hkd"] == "0.00"

    def test_completed_orders_are_summed_into_their_day(self, client, admin):
        _insert_completed_order(client, completed_at=_as_hkt(2026, 3, 10, 9), fare="100.00")
        _insert_completed_order(client, completed_at=_as_hkt(2026, 3, 10, 14), fare="150.00")
        _insert_completed_order(client, completed_at=_as_hkt(2026, 3, 11, 9), fare="200.00")

        r = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-03-10", "to": "2026-03-11"},
        )
        body = r.json()
        buckets = {b["bucket"]: b for b in body["buckets"]}
        assert buckets["2026-03-10"]["orders"] == 2
        assert buckets["2026-03-10"]["earnings_hkd"] == "250.00"
        assert buckets["2026-03-11"]["orders"] == 1
        assert buckets["2026-03-11"]["earnings_hkd"] == "200.00"
        assert body["totals"]["earnings_hkd"] == "450.00"
        assert body["totals"]["orders"] == 3
        assert body["totals"]["avg_fare_hkd"] == "150.00"

    def test_unfinished_orders_are_excluded(self, client, admin):
        """Only COMPLETED counts. A cancelled trip earned nobody anything."""
        _insert_completed_order(client, completed_at=_as_hkt(2026, 4, 1, 10), fare="100.00")
        # A BROADCASTING order in the same window, never completed.
        passenger_id = uuid.uuid4()
        client.exec_sql(
            """
            INSERT INTO users (
                id, phone_e164, display_name, role, is_active, account_status,
                created_at, updated_at
            ) VALUES (
                :pid, :phone, :dname, 'PASSENGER', true, 'ACTIVE', now(), now()
            )
            """,
            {"pid": str(passenger_id), "phone": "+85297000001", "dname": "neverfinished"},
        )
        client.exec_sql(
            """
            INSERT INTO orders (
                id, passenger_id, status, pickup_location, pickup_address,
                dropoff_location, dropoff_address, distance_km, taxi_type,
                fare_json, tariff_version, estimated_total_hkd, discount_percent,
                broadcast_radius_km, fare_mode, created_at, updated_at
            ) VALUES (
                :oid, :pid, 'BROADCASTING',
                ST_GeogFromText('POINT(114.158 22.284)'), 'Central',
                ST_GeogFromText('POINT(114.219 22.315)'), 'North Point',
                5.000, 'URBAN', '{}'::jsonb, 'test-v1', 999.00, 0, 3.0, 'METER',
                now(), now()
            )
            """,
            {"oid": str(uuid.uuid4()), "pid": str(passenger_id)},
        )

        r = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-04-01", "to": "2026-04-01"},
        )
        body = r.json()
        assert body["totals"]["orders"] == 1
        assert body["totals"]["earnings_hkd"] == "100.00", "the BROADCASTING fare leaked in"

    def test_taxi_type_filter_partitions_the_data(self, client, admin):
        _insert_completed_order(
            client, completed_at=_as_hkt(2026, 5, 1, 10), fare="100.00", taxi_type="URBAN"
        )
        _insert_completed_order(
            client, completed_at=_as_hkt(2026, 5, 1, 11), fare="300.00", taxi_type="NT"
        )

        window = {"from": "2026-05-01", "to": "2026-05-01"}

        everything = client.get("/api/v1/admin/analytics", headers=admin, params=window).json()
        assert everything["totals"]["earnings_hkd"] == "400.00"

        urban = client.get(
            "/api/v1/admin/analytics", headers=admin, params={**window, "taxi_type": "URBAN"}
        ).json()
        assert urban["totals"]["earnings_hkd"] == "100.00"

        nt = client.get(
            "/api/v1/admin/analytics", headers=admin, params={**window, "taxi_type": "NT"}
        ).json()
        assert nt["totals"]["earnings_hkd"] == "300.00"

        # The parts must add back up to the whole, or the filter is leaking.
        assert float(urban["totals"]["earnings_hkd"]) + float(
            nt["totals"]["earnings_hkd"]
        ) == float(everything["totals"]["earnings_hkd"])

    def test_granularity_month_rolls_days_up(self, client, admin):
        _insert_completed_order(client, completed_at=_as_hkt(2026, 6, 5, 10), fare="100.00")
        _insert_completed_order(client, completed_at=_as_hkt(2026, 6, 20, 10), fare="150.00")
        _insert_completed_order(client, completed_at=_as_hkt(2026, 7, 2, 10), fare="250.00")

        r = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-06-01", "to": "2026-07-31", "granularity": "month"},
        )
        buckets = {b["bucket"]: b for b in r.json()["buckets"]}
        assert set(buckets) == {"2026-06-01", "2026-07-01"}
        assert buckets["2026-06-01"]["earnings_hkd"] == "250.00"
        assert buckets["2026-07-01"]["earnings_hkd"] == "250.00"

    def test_granularity_year_rolls_months_up(self, client, admin):
        _insert_completed_order(client, completed_at=_as_hkt(2025, 6, 5, 10), fare="100.00")
        _insert_completed_order(client, completed_at=_as_hkt(2026, 6, 5, 10), fare="400.00")

        r = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2025-01-01", "to": "2026-12-31", "granularity": "year"},
        )
        buckets = {b["bucket"]: b for b in r.json()["buckets"]}
        assert buckets["2025-01-01"]["earnings_hkd"] == "100.00"
        assert buckets["2026-01-01"]["earnings_hkd"] == "400.00"


class TestHongKongTimezoneBoundaries:
    """The tests that would catch a UTC-vs-HKT bug, which is otherwise silent."""

    def test_a_late_evening_trip_stays_on_its_hong_kong_day(self, client, admin):
        """23:30 HKT is 15:30 UTC on the *same* calendar day, so this passes
        even under UTC. It is here as the control for the next test."""
        _insert_completed_order(client, completed_at=_as_hkt(2026, 8, 10, 23, 30), fare="88.00")

        r = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-08-10", "to": "2026-08-10"},
        )
        body = r.json()
        assert [b["bucket"] for b in body["buckets"]] == ["2026-08-10"]
        assert body["totals"]["earnings_hkd"] == "88.00"

    def test_an_early_morning_trip_is_not_pushed_back_a_day(self, client, admin):
        """00:30 HKT is 16:30 UTC on the *previous* day.

        Under UTC grouping this trip lands on 2026-08-09, one day early — and
        the query is for the 10th, so it would vanish from the report entirely
        while still being counted in the database.
        """
        _insert_completed_order(client, completed_at=_as_hkt(2026, 8, 10, 0, 30), fare="77.00")

        r = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-08-10", "to": "2026-08-10"},
        )
        body = r.json()
        assert body["totals"]["orders"] == 1, "a 00:30 HKT trip fell outside its own day"
        assert [b["bucket"] for b in body["buckets"]] == ["2026-08-10"]

    def test_a_midnight_boundary_is_not_double_counted(self, client, admin):
        """The range is half-open, so an instant on the edge belongs to one day.

        A closed interval would put a trip completed exactly at 00:00:00 on the
        11th into both the 10th's range and the 11th's, inflating whichever
        report happened to be run.
        """
        _insert_completed_order(client, completed_at=_as_hkt(2026, 9, 11, 0, 0), fare="55.00")

        first = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-09-10", "to": "2026-09-10"},
        ).json()
        second = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-09-11", "to": "2026-09-11"},
        ).json()

        assert first["totals"]["orders"] == 0, "the boundary instant leaked into the earlier day"
        assert second["totals"]["orders"] == 1

    def test_the_hour_profile_uses_hong_kong_hours(self, client, admin):
        """09:00 HKT must land in hour 9, not hour 1 (which is 09:00 UTC).

        This is the chart's x-axis. If the cast were missing, every bar would
        sit eight hours to the left of the shift that produced it.
        """
        _insert_completed_order(client, completed_at=_as_hkt(2026, 10, 5, 9, 0), fare="120.00")

        r = client.get(
            "/api/v1/admin/analytics/heatmap",
            headers=admin,
            params={"from": "2026-10-05", "to": "2026-10-05"},
        )
        assert r.status_code == 200, r.text
        hours = {h["hour"]: h for h in r.json()["hours"]}
        assert hours[9]["orders"] == 1, "the trip is not in the 09:00 HKT slot"
        assert hours[1]["orders"] == 0, "the trip leaked into the 01:00 HKT slot"
        assert hours[9]["earnings_hkd"] == "120.00"


class TestHourProfile:
    def test_always_returns_twenty_four_slots(self, client, admin):
        """The quiet hours are information; dropping them would compress the axis."""
        r = client.get(
            "/api/v1/admin/analytics/heatmap",
            headers=admin,
            params={"from": "2026-01-01", "to": "2026-01-03"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["hours"]) == 24
        assert [h["hour"] for h in body["hours"]] == list(range(24))

    def test_averages_over_every_day_in_the_range(self, client, admin):
        """The charted figure: what an average day earned in that hour.

        HK$300 earned in hour 10 across a 3-day range is HK$100 per day, not
        HK$300 — the whole point of calling it an average day.
        """
        _insert_completed_order(client, completed_at=_as_hkt(2026, 11, 1, 10), fare="100.00")
        _insert_completed_order(client, completed_at=_as_hkt(2026, 11, 2, 10), fare="200.00")

        r = client.get(
            "/api/v1/admin/analytics/heatmap",
            headers=admin,
            params={"from": "2026-11-01", "to": "2026-11-03"},
        )
        hours = {h["hour"]: h for h in r.json()["hours"]}
        slot = hours[10]
        assert slot["earnings_hkd"] == "300.00"
        assert slot["avg_per_day_hkd"] == "100.00"  # 300 over 3 calendar days
        assert slot["active_days"] == 2
        assert slot["avg_per_active_day_hkd"] == "150.00"  # 300 over the 2 worked days
        assert slot["orders"] == 2

    def test_reports_the_peak_hour(self, client, admin):
        _insert_completed_order(client, completed_at=_as_hkt(2026, 12, 1, 8), fare="50.00")
        _insert_completed_order(client, completed_at=_as_hkt(2026, 12, 1, 19), fare="500.00")

        r = client.get(
            "/api/v1/admin/analytics/heatmap",
            headers=admin,
            params={"from": "2026-12-01", "to": "2026-12-01"},
        )
        body = r.json()
        assert body["peak_hour"] == 19
        assert body["max_avg_per_day_hkd"] == "500.00"

    def test_an_empty_range_does_not_divide_by_zero(self, client, admin):
        r = client.get(
            "/api/v1/admin/analytics/heatmap",
            headers=admin,
            params={"from": "2026-02-01", "to": "2026-02-05"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["hours"]) == 24
        assert all(h["avg_per_day_hkd"] == "0.00" for h in body["hours"])
        assert body["max_avg_per_day_hkd"] == "0.00"

    def test_the_hour_profile_respects_the_taxi_filter(self, client, admin):
        _insert_completed_order(
            client, completed_at=_as_hkt(2026, 1, 5, 12), fare="100.00", taxi_type="URBAN"
        )
        _insert_completed_order(
            client, completed_at=_as_hkt(2026, 1, 5, 12), fare="900.00", taxi_type="NT"
        )

        r = client.get(
            "/api/v1/admin/analytics/heatmap",
            headers=admin,
            params={"from": "2026-01-05", "to": "2026-01-05", "taxi_type": "URBAN"},
        )
        hours = {h["hour"]: h for h in r.json()["hours"]}
        assert hours[12]["earnings_hkd"] == "100.00"


class TestSorting:
    def _seed(self, client):
        _insert_completed_order(client, completed_at=_as_hkt(2026, 3, 1, 10), fare="10.00")
        _insert_completed_order(client, completed_at=_as_hkt(2026, 3, 2, 10), fare="500.00")
        _insert_completed_order(client, completed_at=_as_hkt(2026, 3, 3, 10), fare="100.00")

    def _get(self, client, admin, **params):
        return client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-03-01", "to": "2026-03-03", **params},
        ).json()

    def test_default_sort_is_chronological(self, client, admin):
        self._seed(client)
        buckets = [b["bucket"] for b in self._get(client, admin)["buckets"]]
        assert buckets == ["2026-03-01", "2026-03-02", "2026-03-03"]

    def test_sort_by_earnings_descending(self, client, admin):
        self._seed(client)
        buckets = [
            b["earnings_hkd"]
            for b in self._get(client, admin, sort_by="earnings", sort_dir="desc")["buckets"]
        ]
        assert buckets == ["500.00", "100.00", "10.00"]

    def test_sort_by_earnings_ascending(self, client, admin):
        self._seed(client)
        buckets = [
            b["earnings_hkd"]
            for b in self._get(client, admin, sort_by="earnings", sort_dir="asc")["buckets"]
        ]
        assert buckets == ["10.00", "100.00", "500.00"]

    def test_money_sorts_numerically_not_lexically(self, client, admin):
        """`"9.00"` sorts after `"100.00"` as text — the classic money-sort bug.

        The values here are chosen so a lexical sort gives a different, and
        wrong, order: "100.00" < "9.00" as strings.

        Both orders must land inside the range `_get` queries (2026-03-01 to
        2026-03-03). The earlier draft used April dates, so the range came back
        empty and the test failed for a reason that had nothing to do with
        sorting — it would have passed even if the sort were lexical.
        """
        _insert_completed_order(client, completed_at=_as_hkt(2026, 3, 1, 10), fare="9.00")
        _insert_completed_order(client, completed_at=_as_hkt(2026, 3, 2, 10), fare="100.00")

        buckets = [
            b["earnings_hkd"]
            for b in self._get(
                client,
                admin,
                sort_by="earnings",
                sort_dir="desc",
            )["buckets"]
        ]
        assert buckets == ["100.00", "9.00"], f"lexical sort leaked through: {buckets}"

        # The mirror image, so a `sort_dir` that is ignored cannot pass: a
        # lexical sort would leave this order unchanged.
        ascending = [
            b["earnings_hkd"]
            for b in self._get(
                client,
                admin,
                sort_by="earnings",
                sort_dir="asc",
            )["buckets"]
        ]
        assert ascending == ["9.00", "100.00"]

    def test_sorting_does_not_change_the_totals(self, client, admin):
        """A total that moved when you clicked a column header would be a bug."""
        self._seed(client)
        ascending = self._get(client, admin, sort_by="earnings", sort_dir="asc")
        descending = self._get(client, admin, sort_by="earnings", sort_dir="desc")
        assert ascending["totals"] == descending["totals"]
        assert ascending["totals"]["earnings_hkd"] == "610.00"

    def test_an_unknown_sort_column_is_rejected(self, client, admin):
        r = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-03-01", "to": "2026-03-03", "sort_by": "; DROP TABLE orders"},
        )
        assert r.status_code == 422

    def test_the_sort_echo_is_reported_back(self, client, admin):
        self._seed(client)
        body = self._get(client, admin, sort_by="orders", sort_dir="desc")
        assert body["sort"] == {"by": "orders", "dir": "desc"}


class TestMoneyFormatting:
    def test_amounts_are_two_decimal_strings(self, client, admin):
        """Money is a string, never a float — a float cannot hold cents exactly."""
        _insert_completed_order(client, completed_at=_as_hkt(2026, 5, 5, 10), fare="0.50")

        body = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-05-05", "to": "2026-05-05"},
        ).json()
        assert body["totals"]["earnings_hkd"] == "0.50"
        assert isinstance(body["totals"]["earnings_hkd"], str)

    def test_a_sub_cent_total_is_not_rounded_away(self, client, admin):
        """Three HK$0.01 trips are HK$0.03, not HK$0.00.

        The decile-rounding bug that `money_str` replaced turned `0.01` into
        `"0.0"`, which is indistinguishable from an empty account.
        """
        for _ in range(3):
            _insert_completed_order(client, completed_at=_as_hkt(2026, 6, 6, 10), fare="0.01")

        body = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-06-06", "to": "2026-06-06"},
        ).json()
        assert body["totals"]["earnings_hkd"] == money_str("0.03")


class TestOperationsAnalytics:
    """Order funnel and cancellation attribution for created, not just completed, trips."""

    def _get(self, client, admin, start, end, **params):
        response = client.get(
            "/api/v1/admin/analytics/operations",
            headers=admin,
            params={"from": start, "to": end, **params},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_operations_requires_an_admin(self, client):
        assert client.get("/api/v1/admin/analytics/operations").status_code == 401

        token = client.activate("+85290000030")
        r = client.get(
            "/api/v1/admin/analytics/operations",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_empty_range_reports_zeroes(self, client, admin):
        body = self._get(client, admin, start="2026-02-01", end="2026-02-07")
        assert body["funnel"] == {
            "created": 0,
            "accepted": 0,
            "completed": 0,
            "interrupted": 0,
            "cancelled": 0,
            "active": 0,
        }
        assert body["cancellations"] == {
            "passenger": 0,
            "driver": 0,
            "timeout": 0,
            "unattributed": 0,
        }
        assert body["acceptance_rate"] == "0.00"
        assert body["cancellation_rate"] == "0.00"
        assert body["latency"] == {"acceptance_avg_s": None, "arrival_avg_s": None}
        assert body["range"] == {
            "from": "2026-02-01",
            "to": "2026-02-07",
            "taxi_type": None,
            "timezone": "Asia/Hong_Kong",
            "days": 7,
        }

    def test_created_orders_are_counted_by_current_status(self, client, admin):
        completed_created = _as_hkt(2026, 3, 1, 8)
        accepted = completed_created + timedelta(minutes=2)
        arrived = accepted + timedelta(minutes=10)
        _insert_created_order(
            client,
            created_at=completed_created,
            status="COMPLETED",
            accepted_at=accepted,
            driver_arrived_at=arrived,
            completed_at=arrived,
        )
        interrupted_created = _as_hkt(2026, 3, 1, 9)
        _insert_created_order(
            client,
            created_at=interrupted_created,
            status="INTERRUPTED",
            accepted_at=interrupted_created + timedelta(seconds=60),
        )
        _insert_created_order(
            client,
            created_at=_as_hkt(2026, 3, 1, 10),
            status="BROADCASTING",
        )
        _insert_created_order(
            client,
            created_at=_as_hkt(2026, 3, 2, 8),
            status="CANCELLED",
            cancellation_reason="broadcast expired",
        )
        # Outside the range, even though it is COMPLETED.
        _insert_created_order(
            client,
            created_at=_as_hkt(2026, 2, 28, 23),
            status="COMPLETED",
            accepted_at=_as_hkt(2026, 2, 28, 23, 1),
            completed_at=_as_hkt(2026, 2, 28, 23, 2),
        )

        body = self._get(client, admin, start="2026-03-01", end="2026-03-02")
        assert body["funnel"] == {
            "created": 4,
            "accepted": 2,
            "completed": 1,
            "interrupted": 1,
            "cancelled": 1,
            "active": 1,
        }
        assert body["acceptance_rate"] == "50.00"
        assert body["cancellation_rate"] == "25.00"
        # (120 seconds + 60 seconds) / 2 accepted samples.
        assert body["latency"]["acceptance_avg_s"] == "90.00"
        assert body["latency"]["arrival_avg_s"] == "600.00"

    def test_operations_uses_creation_time_not_completion_time(self, client, admin):
        created = _as_hkt(2026, 4, 10, 0, 30)
        _insert_created_order(
            client,
            created_at=created,
            status="COMPLETED",
            accepted_at=created + timedelta(seconds=30),
            completed_at=_as_hkt(2026, 5, 1, 0),
        )

        ops = self._get(client, admin, start="2026-04-10", end="2026-04-10")
        assert ops["funnel"]["created"] == 1
        assert ops["funnel"]["completed"] == 1

        revenue = client.get(
            "/api/v1/admin/analytics",
            headers=admin,
            params={"from": "2026-04-10", "to": "2026-04-10"},
        ).json()
        assert revenue["totals"]["orders"] == 0

    def test_early_hong_kong_morning_is_not_pushed_back_a_day(self, client, admin):
        _insert_created_order(
            client,
            created_at=_as_hkt(2026, 5, 10, 0, 30),
            status="BROADCASTING",
        )

        body = self._get(client, admin, start="2026-05-10", end="2026-05-10")
        assert body["funnel"]["created"] == 1

    def test_taxi_type_filter_partitions_operations(self, client, admin):
        _insert_created_order(client, created_at=_as_hkt(2026, 6, 1, 8), status="BROADCASTING")
        _insert_created_order(
            client,
            created_at=_as_hkt(2026, 6, 1, 9),
            status="BROADCASTING",
            taxi_type="NT",
        )

        window = {"start": "2026-06-01", "end": "2026-06-01"}
        assert self._get(client, admin, **window)["funnel"]["created"] == 2
        assert self._get(client, admin, **window, taxi_type="URBAN")["funnel"]["created"] == 1
        assert self._get(client, admin, **window, taxi_type="NT")["funnel"]["created"] == 1

        bad = client.get(
            "/api/v1/admin/analytics/operations",
            headers=admin,
            params={"from": window["start"], "to": window["end"], "taxi_type": "HELICOPTER"},
        )
        assert bad.status_code == 422

    def test_cancellation_is_attributed_from_events_and_legacy_rows_stay_separate(
        self, client, admin
    ):
        passenger = _insert_created_order(
            client,
            created_at=_as_hkt(2026, 7, 1, 8),
            status="CANCELLED",
        )
        _insert_cancel_event(client, passenger, actor_kind="PASSENGER")

        driver = _insert_created_order(
            client,
            created_at=_as_hkt(2026, 7, 1, 9),
            status="CANCELLED",
        )
        _insert_cancel_event(client, driver, actor_kind="DRIVER")

        # A broadcast expiry is a timeout even when no event row exists yet.
        _insert_created_order(
            client,
            created_at=_as_hkt(2026, 7, 1, 10),
            status="CANCELLED",
            cancellation_reason="broadcast expired",
        )
        # A SYSTEM event is also a timeout; the two paths must not overwrite the
        # passenger/driver counts.
        system = _insert_created_order(
            client,
            created_at=_as_hkt(2026, 7, 1, 11),
            status="CANCELLED",
        )
        _insert_cancel_event(client, system, actor_kind="SYSTEM")
        # Pre-attribution rows fall through to `unattributed`, never negative.
        _insert_created_order(
            client,
            created_at=_as_hkt(2026, 7, 1, 12),
            status="CANCELLED",
        )

        body = self._get(client, admin, start="2026-07-01", end="2026-07-01")
        assert body["cancellations"] == {
            "passenger": 1,
            "driver": 1,
            "timeout": 2,
            "unattributed": 1,
        }
        assert body["funnel"]["cancelled"] == 5


class TestSupplyAnalytics:
    """Real-time supply snapshot for operators deciding whether demand has coverage."""

    def _get(self, client, admin, **params):
        response = client.get("/api/v1/admin/analytics/supply", headers=admin, params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def test_requires_an_admin(self, client):
        assert client.get("/api/v1/admin/analytics/supply").status_code == 401

        token = client.activate("+85290000011")
        response = client.get(
            "/api/v1/admin/analytics/supply",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    def test_counts_active_drivers_online_drivers_and_open_orders(self, client, admin):
        d1 = _insert_driver_profile(client, is_online=True, has_gps=True)
        _insert_driver_profile(client, is_online=True)
        _insert_driver_profile(client, is_online=False)
        _insert_driver_profile(client, status="SUSPENDED", is_online=True)

        _insert_created_order(
            client,
            created_at=_as_hkt(2026, 8, 1, 9),
            status="ACCEPTED",
            driver_id=d1,
        )
        d2 = _insert_driver_profile(client, is_online=True)
        _insert_created_order(
            client,
            created_at=_as_hkt(2026, 8, 1, 10),
            status="IN_TRIP",
            driver_id=d2,
        )
        _insert_created_order(
            client,
            created_at=_as_hkt(2026, 8, 1, 11),
            status="BROADCASTING",
        )
        _insert_created_order(
            client,
            created_at=_as_hkt(2026, 8, 1, 12),
            status="COMPLETED",
            driver_id=d1,
        )
        # A second open order for the same driver must not inflate `engaged_drivers`.
        _insert_created_order(
            client,
            created_at=_as_hkt(2026, 8, 1, 13),
            status="DRIVER_ARRIVED",
            driver_id=d2,
        )

        body = self._get(client, admin)
        assert body["active_drivers"] == 4
        assert body["online_drivers"] == 3
        assert body["online_with_gps"] == 1
        assert body["active_orders"] == 4
        assert body["engaged_drivers"] == 2
        # online 3 - 2 distinct drivers holding open orders
        assert body["available_drivers"] == 1
        assert body["supply_demand_ratio"] == "0.75"
        assert body["sampled_at"]  # ISO timestamp is non-empty

    def test_taxi_type_filter_partitions_supply(self, client, admin):
        _insert_driver_profile(client, is_online=True)
        _insert_driver_profile(client, is_online=True, taxi_type="NT")
        _insert_created_order(
            client,
            created_at=_as_hkt(2026, 8, 2, 9),
            status="BROADCASTING",
            taxi_type="NT",
        )

        all_types = self._get(client, admin)
        assert all_types["active_drivers"] == 2
        assert all_types["active_orders"] == 1

        nt = self._get(client, admin, taxi_type="NT")
        assert nt["active_drivers"] == 1
        assert nt["online_drivers"] == 1
        assert nt["active_orders"] == 1
        assert nt["supply_demand_ratio"] == "1.00"

        bad = client.get(
            "/api/v1/admin/analytics/supply",
            headers=admin,
            params={"taxi_type": "HELICOPTER"},
        )
        assert bad.status_code == 422
