"""Recurring ride template API and background minter tests.

The minter is DB-driven and idempotent per template: it mints one order at a
time when `next_run_at` is due and advances the cursor. Fail-closed rules:
no mint when the passenger is inactive, when phone re-verification is overdue,
or when the template can no longer be validated — the ride is paused instead.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from app.services.recurring.recurring_service import RecurringMinter

_ORDER = {
    "pickup_lat": 22.3193,
    "pickup_lng": 114.1694,
    "dropoff_lat": 22.3080,
    "dropoff_lng": 113.9185,
    "pickup_address": "Hong Kong Convention and Exhibition Centre",
    "dropoff_address": "Hong Kong International Airport",
    "distance_km": "35.2",
    "waiting_min": "0",
    "taxi_type": "URBAN",
    "discount_percent": "0",
    "tip": "0",
    "tunnels": [],
    "crosses_harbour": False,
    "pickup_at_cross_harbour_stand": False,
    "requirements": None,
    "payment_preference": [],
}


def _rides_payload(**overrides):
    payload = {
        "weekday": 5,
        "scheduled_time": "08:30",
        "source_order_id": None,
        "order": _ORDER,
    }
    payload.update(overrides)
    return payload


def _unique_phone() -> str:
    return f"+85291{uuid.uuid4().int % 1000000:06d}"


async def _fetch_one(client, sql: str, **params):
    async with client.db_factory() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


class TestRecurringRideApi:
    async def test_create_list_own_ride(self, client):
        phone = _unique_phone()
        token = client.activate(phone)
        headers = {"Authorization": f"Bearer {token}"}

        r = client.post("/api/v1/recurring-rides", json=_rides_payload(), headers=headers)
        assert r.status_code == 201, r.text
        ride = r.json()
        assert ride["status"] == "ACTIVE"
        assert ride["frequency"] == "WEEKLY"
        assert ride["weekday"] == 5
        assert ride["scheduled_time"] == "08:30"
        assert ride["source_order_id"] is None
        assert ride["last_order_id"] is None

        r = client.get("/api/v1/recurring-rides", headers=headers)
        assert r.status_code == 200, r.text
        assert [item["id"] for item in r.json()["items"]] == [ride["id"]]

        other_token = client.activate(_unique_phone())
        r = client.get(
            "/api/v1/recurring-rides",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert r.json()["items"] == []

    async def test_create_rejects_invalid_schedule(self, client):
        token = client.activate(_unique_phone())
        headers = {"Authorization": f"Bearer {token}"}

        r = client.post(
            "/api/v1/recurring-rides",
            json=_rides_payload(weekday=0),
            headers=headers,
        )
        assert r.status_code == 422, r.text

        r = client.post(
            "/api/v1/recurring-rides",
            json=_rides_payload(scheduled_time="25:99"),
            headers=headers,
        )
        assert r.status_code == 422, r.text

    async def test_source_order_dedupe_and_ownership(self, client):
        token = client.activate(_unique_phone())
        headers = {"Authorization": f"Bearer {token}"}

        r = client.post("/api/v1/orders", json=_ORDER, headers=headers)
        assert r.status_code == 201, r.text
        order_id = r.json()["id"]

        payload = _rides_payload(source_order_id=order_id)
        r = client.post("/api/v1/recurring-rides", json=payload, headers=headers)
        assert r.status_code == 201, r.text

        # Same source order -> conflict (one active template per source order).
        r = client.post("/api/v1/recurring-rides", json=payload, headers=headers)
        assert r.status_code == 409, r.text

        # Someone else's source order -> rejected (ownership fail-closed).
        stranger_token = client.activate(_unique_phone())
        r = client.post(
            "/api/v1/recurring-rides",
            json=payload,
            headers={"Authorization": f"Bearer {stranger_token}"},
        )
        assert r.status_code == 422, r.text

    async def test_patch_pause_activate_cancel(self, client):
        token = client.activate(_unique_phone())
        headers = {"Authorization": f"Bearer {token}"}

        r = client.post("/api/v1/recurring-rides", json=_rides_payload(), headers=headers)
        ride_id = r.json()["id"]

        r = client.patch(
            f"/api/v1/recurring-rides/{ride_id}",
            json={"status": "PAUSED"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PAUSED"

        r = client.patch(
            f"/api/v1/recurring-rides/{ride_id}",
            json={"status": "ACTIVE"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACTIVE"

        r = client.patch(
            f"/api/v1/recurring-rides/{ride_id}",
            json={"status": "CANCELLED"},
            headers=headers,
        )
        assert r.status_code == 200, r.text

        # CANCELLED is terminal.
        r = client.patch(
            f"/api/v1/recurring-rides/{ride_id}",
            json={"status": "ACTIVE"},
            headers=headers,
        )
        assert r.status_code == 409, r.text

    async def test_other_passenger_cannot_touch_ride(self, client):
        owner = client.activate(_unique_phone())
        r = client.post(
            "/api/v1/recurring-rides",
            json=_rides_payload(),
            headers={"Authorization": f"Bearer {owner}"},
        )
        ride_id = r.json()["id"]

        stranger = client.activate(_unique_phone())
        r = client.patch(
            f"/api/v1/recurring-rides/{ride_id}",
            json={"status": "PAUSED"},
            headers={"Authorization": f"Bearer {stranger}"},
        )
        assert r.status_code == 404, r.text


class TestRecurringMinter:
    async def test_mints_one_order_and_advances_cursor(self, client):
        token = client.activate(_unique_phone())
        headers = {"Authorization": f"Bearer {token}"}
        r = client.post("/api/v1/recurring-rides", json=_rides_payload(), headers=headers)
        ride_id = r.json()["id"]

        # Force the cursor into the past so the minter considers it due.
        await _fetch_one(
            client,
            "UPDATE recurring_rides SET next_run_at = now() - interval '1 minute' "
            "WHERE id = CAST(:id AS uuid)",
            id=ride_id,
        )

        result = await RecurringMinter(client.db_factory).run_due(now=datetime.now(UTC))
        assert result == {"minted": 1, "paused": 0, "next_batch": 0}
        assert result["paused"] == 0

        row = (
            await _fetch_one(
                client,
                "SELECT status, last_order_id IS NOT NULL AS minted, "
                "next_run_at > now() AS advanced FROM recurring_rides "
                "WHERE id = CAST(:id AS uuid)",
                id=ride_id,
            )
        ).one()
        assert row[0] == "ACTIVE"
        assert row[1] is True
        assert row[2] is True

    async def test_no_mint_when_passenger_inactive(self, client):
        phone = _unique_phone()
        token = client.activate(phone)
        headers = {"Authorization": f"Bearer {token}"}
        r = client.post("/api/v1/recurring-rides", json=_rides_payload(), headers=headers)
        ride_id = r.json()["id"]

        await _fetch_one(
            client,
            "UPDATE recurring_rides SET next_run_at = now() - interval '1 minute' "
            "WHERE id = CAST(:id AS uuid)",
            id=ride_id,
        )
        await _fetch_one(
            client,
            "UPDATE users SET is_active = false WHERE phone_e164 = :p",
            p=phone,
        )

        result = await RecurringMinter(client.db_factory).run_due(now=datetime.now(UTC))
        assert result["minted"] == 0
        assert result["paused"] == 1

        row = (
            await _fetch_one(
                client,
                "SELECT status FROM recurring_rides WHERE id = CAST(:id AS uuid)",
                id=ride_id,
            )
        ).one()
        assert row[0] == "PAUSED"

    async def test_no_mint_when_phone_reverify_blocked(self, client):
        phone = _unique_phone()
        token = client.activate(phone)
        headers = {"Authorization": f"Bearer {token}"}
        r = client.post("/api/v1/recurring-rides", json=_rides_payload(), headers=headers)
        ride_id = r.json()["id"]

        await _fetch_one(
            client,
            "UPDATE recurring_rides SET next_run_at = now() - interval '1 minute' "
            "WHERE id = CAST(:id AS uuid)",
            id=ride_id,
        )
        await _fetch_one(
            client,
            "UPDATE users SET phone_reverify_due_at = now() - interval '60 days' "
            "WHERE phone_e164 = :p",
            p=phone,
        )

        result = await RecurringMinter(client.db_factory).run_due(now=datetime.now(UTC))
        assert result["minted"] == 0
        assert result["paused"] == 1

        row = (
            await _fetch_one(
                client,
                "SELECT status FROM recurring_rides WHERE id = CAST(:id AS uuid)",
                id=ride_id,
            )
        ).one()
        assert row[0] == "PAUSED"

    async def test_invalid_template_pauses_ride(self, client):
        token = client.activate(_unique_phone())
        headers = {"Authorization": f"Bearer {token}"}
        r = client.post("/api/v1/recurring-rides", json=_rides_payload(), headers=headers)
        ride_id = r.json()["id"]

        # Corrupt the template so the minter can no longer validate it
        # (fail-closed: pause, never mint garbage).
        await _fetch_one(
            client,
            "UPDATE recurring_rides SET template_json = jsonb_build_object("
            "'distance_km', 'not-a-number'), next_run_at = now() - interval '1 minute' "
            "WHERE id = CAST(:id AS uuid)",
            id=ride_id,
        )

        result = await RecurringMinter(client.db_factory).run_due(now=datetime.now(UTC))
        assert result["minted"] == 0
        assert result["paused"] == 1

        row = (
            await _fetch_one(
                client,
                "SELECT status FROM recurring_rides WHERE id = CAST(:id AS uuid)",
                id=ride_id,
            )
        ).one()
        assert row[0] == "PAUSED"
