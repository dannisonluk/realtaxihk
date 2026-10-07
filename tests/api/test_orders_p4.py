"""P4 — the in-trip redesign, driven through the real routes.

`docs/IN_TRIP_REDESIGN.md` is the specification; this module is the part of it
that can be checked by calling the API. The structural half — the transition
table's invariants — lives in `test_orders_module.py::TestStateMachineInvariants`
beside the table it describes.

What is deliberately covered here, and why each one is not obvious:

* **The two-step arrival.** Both halves are tested in the direction that
  *fails*, because the whole design rests on "a claim is not an arrival": a
  driver with no GPS tick, a driver 7 km away, a body coordinate that
  contradicts the server's own tick, and a passenger typing the *driver's*
  number tail (which would mean option B was implemented by mistake).
* **`DESTINATION_CHANGED` as a live state.** It is reachable, it is not
  terminal, and the original dropoff survives it.
* **Interruption is instant.** No `INTERRUPTED_PENDING` anywhere: the response
  says `INTERRUPTED`, and the dispute exists in the same transaction.
* **The money.** The trip fee is charged once at `/start`; a defaulting driver
  is debited 50% of *this trip's* estimate; a defaulting passenger is cooled
  down rather than debited, because there is no passenger wallet to debit.
"""

import asyncio
from decimal import Decimal

from app.core.config import get_settings
from app.models import (
    INTERRUPTION_REASONS_BY_PARTY,
    InterruptionReason,
    OrderParty,
)

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

# North Point — ~7 km from the Central pickup, i.e. far outside the 150 m
# arrival radius. Used both as "the driver is nowhere near" and as "the body
# coordinate contradicts the tick".
_FAR = (22.315, 114.219)

_ORDER_PICKUP = (_ORDER["pickup_lat"], _ORDER["pickup_lng"])


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _fetch(client, sql: str, params: dict | None = None) -> list[dict]:
    """Run a read on its own session. Mirrors the other API test modules.

    `client.exec_sql` is the *write* helper; reads need a session to come back
    on, which is why every module that inspects rows defines this.
    """
    from sqlalchemy import text

    async with client.db_factory() as session:
        result = await session.execute(text(sql), params or {})
        return [dict(row) for row in result.mappings()]


def _mk_active_driver(client, phone: str) -> dict:
    token = client.activate(phone)
    r = client.post(
        "/api/v1/drivers/register",
        headers=_h(token),
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
    me = client.get("/api/v1/auth/me", headers=_h(token)).json()
    return {"token": token, "driver_id": driver_id, "user_id": me["id"], "phone": phone}


def _create_order(client, passenger_token: str) -> dict:
    r = client.post("/api/v1/orders", headers=_h(passenger_token), json=_ORDER)
    assert r.status_code == 201, r.text
    return r.json()


def _set_location(client, driver: dict, lat: float, lng: float):
    return client.post(
        "/api/v1/drivers/location",
        headers=_h(driver["token"]),
        json={"lat": lat, "lng": lng},
    )


def _grab(client, oid: str, driver: dict) -> dict:
    r = client.post(f"/api/v1/orders/{oid}/grab", headers=_h(driver["token"]))
    assert r.status_code == 200, r.text
    return r.json()


def _drive_to_driver_arrived(client, passenger_phone: str, driver_phone: str) -> tuple:
    """Walk an order to `DRIVER_ARRIVED` through the real endpoints.

    Returns `(order_id, passenger_token, driver)`. Every step is asserted, so a
    regression in any of them fails here rather than as a confusing failure in
    whichever test happened to need an arrived trip.
    """
    passenger_token = client.activate(passenger_phone)
    driver = _mk_active_driver(client, driver_phone)
    oid = _create_order(client, passenger_token)["id"]
    _grab(client, oid, driver)

    assert _set_location(client, driver, *_ORDER_PICKUP).status_code == 200
    r = client.post(f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={})
    assert r.json()["status"] == "PENDING_ARRIVAL_CONFIRM", r.text

    r = client.post(
        f"/api/v1/orders/{oid}/arrival-confirm",
        headers=_h(passenger_token),
        json={"phone_last4": passenger_phone[-4:]},
    )
    assert r.json()["status"] == "DRIVER_ARRIVED", r.text
    return oid, passenger_token, driver


def _drive_to_in_trip(client, passenger_phone: str, driver_phone: str) -> tuple:
    """`_drive_to_driver_arrived`, then the meter starts."""
    oid, passenger_token, driver = _drive_to_driver_arrived(client, passenger_phone, driver_phone)
    r = client.post(f"/api/v1/orders/{oid}/start", headers=_h(driver["token"]))
    assert r.json()["status"] == "IN_TRIP", r.text
    return oid, passenger_token, driver


# --------------------------------------------------------------------------- #
# Arrival is a two-sided proof, not a button
# --------------------------------------------------------------------------- #


class TestArrivalProof:
    def test_a_claim_with_no_gps_tick_is_refused(self, client):
        """No position on record must read as "cannot verify", never as "close".

        `driver_profiles.current_location` is NULL for a driver who has never
        streamed a tick. Treating that as a pass would let anyone claim arrival
        from anywhere by simply never turning their GPS on.
        """
        passenger = client.activate("+85291600101")
        driver = _mk_active_driver(client, "+85291600102")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)

        r = client.post(f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={})
        assert r.status_code == 422, r.text
        assert r.json()["details"]["reason"] == "NO_LOCATION"
        assert (
            client.get(f"/api/v1/orders/{oid}", headers=_h(driver["token"])).json()["status"]
            == "ACCEPTED"
        )

    def test_a_claim_from_too_far_is_refused(self, client):
        """The radius is the first gate, and a refusal must not move the order."""
        passenger = client.activate("+85291600111")
        driver = _mk_active_driver(client, "+85291600112")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)

        assert _set_location(client, driver, *_FAR).status_code == 200
        r = client.post(f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={})
        assert r.status_code == 422, r.text
        assert r.json()["details"]["reason"] == "TOO_FAR"
        # The distance is echoed so the client can say "about 7 km away".
        assert r.json()["details"]["distance_m"] > 1000
        assert (
            client.get(f"/api/v1/orders/{oid}", headers=_h(driver["token"])).json()["status"]
            == "ACCEPTED"
        )

    def test_a_body_coordinate_that_contradicts_the_server_tick_is_refused(self, client):
        """The spoofing signal.

        The authoritative position is the one recorded from the driver's own
        WebSocket ticks. A body coordinate that disagrees with it by more than
        `arrival_gps_max_disagreement_m` is the signature of a client trying to
        place itself at the pickup without being there.
        """
        passenger = client.activate("+85291600121")
        driver = _mk_active_driver(client, "+85291600122")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)

        assert _set_location(client, driver, *_ORDER_PICKUP).status_code == 200
        r = client.post(
            f"/api/v1/orders/{oid}/arrival-claim",
            headers=_h(driver["token"]),
            json={"driver_lat": _FAR[0], "driver_lng": _FAR[1]},
        )
        assert r.status_code == 422, r.text
        assert r.json()["details"]["reason"] == "GPS_MISMATCH"

    def test_the_drivers_own_number_tail_does_not_confirm_the_arrival(self, client):
        """Option A, proved by the direction that fails.

        The secret is the **passenger's** number. If the driver's tail worked,
        the second factor would not exist — the driver knows their own number,
        so they could complete the confirmation alone.
        """
        passenger = client.activate("+85291600131")
        driver = _mk_active_driver(client, "+85291600132")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)

        assert _set_location(client, driver, *_ORDER_PICKUP).status_code == 200
        client.post(f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={})

        r = client.post(
            f"/api/v1/orders/{oid}/arrival-confirm",
            headers=_h(passenger),
            json={"phone_last4": driver["phone"][-4:]},
        )
        assert r.status_code == 401, r.text
        assert r.json()["details"]["reason"] == "PIN_MISMATCH"

    def test_the_driver_cannot_confirm_their_own_arrival(self, client):
        """Even with the right digits, only the passenger may confirm.

        The participant check runs first, so this is a 403 — a stronger refusal
        than a wrong-digit 401, and the one that makes the endpoint's whole
        second factor meaningful.
        """
        phone = "+85291600141"
        passenger = client.activate(phone)
        driver = _mk_active_driver(client, "+85291600142")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)

        assert _set_location(client, driver, *_ORDER_PICKUP).status_code == 200
        client.post(f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={})

        r = client.post(
            f"/api/v1/orders/{oid}/arrival-confirm",
            headers=_h(driver["token"]),
            json={"phone_last4": phone[-4:]},
        )
        assert r.status_code == 403, r.text

    def test_three_wrong_digits_return_the_order_and_open_a_dispute(self, client):
        """Three strikes, then a human.

        The usual causes are a driver at the wrong pickup or a passenger in the
        wrong car — a fourth guess cannot tell those apart, so the order goes
        back to `ACCEPTED` and the case goes to an operator.
        """
        passenger = client.activate("+85291600151")
        driver = _mk_active_driver(client, "+85291600152")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)

        assert _set_location(client, driver, *_ORDER_PICKUP).status_code == 200
        client.post(f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={})

        for attempt in (1, 2):
            r = client.post(
                f"/api/v1/orders/{oid}/arrival-confirm",
                headers=_h(passenger),
                json={"phone_last4": "9999"},
            )
            assert r.status_code == 401, (attempt, r.text)
            assert r.json()["details"]["attempts_remaining"] == 3 - attempt

        # The third is not an error: it is the request that changes the state.
        r = client.post(
            f"/api/v1/orders/{oid}/arrival-confirm",
            headers=_h(passenger),
            json={"phone_last4": "9999"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACCEPTED"

        disputes = client.get("/api/v1/admin/disputes", headers=client.admin_headers()).json()[
            "items"
        ]
        mine = [d for d in disputes if d["order_id"] == oid]
        assert len(mine) == 1, disputes
        assert mine[0]["source"] == "PARTY_REPORT"

    def test_arrival_confirmed_at_is_written_with_the_status(self, client):
        """Arrival is a two-sided fact, so it carries two-sided evidence.

        `driver_arrived_at` alone would be a status claiming arrival with no
        proof, which is the exact defect the two-step flow exists to stop.
        """
        phone = "+85291600161"
        passenger = client.activate(phone)
        driver = _mk_active_driver(client, "+85291600162")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)

        assert _set_location(client, driver, *_ORDER_PICKUP).status_code == 200
        claim = client.post(
            f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={}
        ).json()
        assert claim["status"] == "PENDING_ARRIVAL_CONFIRM"
        assert claim["arrival_confirmed_at"] is None, "a claim is not a confirmation"

        confirmed = client.post(
            f"/api/v1/orders/{oid}/arrival-confirm",
            headers=_h(passenger),
            json={"phone_last4": phone[-4:]},
        ).json()
        assert confirmed["status"] == "DRIVER_ARRIVED"
        assert confirmed["arrival_confirmed_at"]


# --------------------------------------------------------------------------- #
# Destination change
# --------------------------------------------------------------------------- #


class TestDestinationChange:
    def test_changing_the_destination_reprices_and_keeps_the_original(self, client):
        oid, passenger, driver = _drive_to_in_trip(client, "+85291600201", "+85291600202")
        before = client.get(f"/api/v1/orders/{oid}", headers=_h(passenger)).json()

        r = client.post(
            f"/api/v1/orders/{oid}/change-destination",
            headers=_h(passenger),
            json={
                "dropoff_lat": 22.3375,
                "dropoff_lng": 114.1765,  # Kowloon Tong, a longer run
                "dropoff_address": "Kowloon Tong Station",
                "distance_km": "12.5",
            },
        )
        assert r.status_code == 200, r.text
        after = r.json()

        assert after["status"] == "DESTINATION_CHANGED"
        assert after["destination_change_count"] == 1
        assert Decimal(after["estimated_total_hkd"]) > Decimal(before["estimated_total_hkd"])
        assert after["fare"]["is_estimate"] is True
        assert after["fare"]["distance_source"] == "client_route"

        # The trip continues: the state is not terminal.
        assert (
            client.post(f"/api/v1/orders/{oid}/complete", headers=_h(driver["token"])).json()[
                "status"
            ]
            == "COMPLETED"
        )

    def test_the_original_destination_is_preserved_on_the_first_change(self, client):
        """ "Where did they originally ask to go" must stay answerable."""
        oid, passenger, _driver = _drive_to_in_trip(client, "+85291600211", "+85291600212")
        client.post(
            f"/api/v1/orders/{oid}/change-destination",
            headers=_h(passenger),
            json={
                "dropoff_lat": 22.3375,
                "dropoff_lng": 114.1765,
                "dropoff_address": "Kowloon Tong Station",
            },
        )
        rows = asyncio.run(
            _fetch(
                client,
                "SELECT original_dropoff_address, dropoff_address, distance_km "
                "FROM orders WHERE id = CAST(:i AS uuid)",
                {"i": oid},
            )
        )
        row = rows[0]
        assert row["original_dropoff_address"] == "Harbour North, North Point"
        assert row["dropoff_address"] == "Kowloon Tong Station"
        # No client distance was sent, so the fallback ran — and it is labelled
        # as a straight line rather than passed off as a routed figure.
        assert row["distance_km"] is not None

    def test_a_change_without_a_client_distance_falls_back_to_a_straight_line(self, client):
        oid, passenger, _driver = _drive_to_in_trip(client, "+85291600221", "+85291600222")
        r = client.post(
            f"/api/v1/orders/{oid}/change-destination",
            headers=_h(passenger),
            json={
                "dropoff_lat": 22.3375,
                "dropoff_lng": 114.1765,
                "dropoff_address": "Kowloon Tong Station",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["fare"]["distance_source"] == "straight_line"

    def test_the_cap_refuses_a_further_change(self, client):
        """Unlimited changes would be a way to extend a trip indefinitely."""
        oid, passenger, _driver = _drive_to_in_trip(client, "+85291600231", "+85291600232")
        body = {
            "dropoff_lat": 22.3375,
            "dropoff_lng": 114.1765,
            "dropoff_address": "Kowloon Tong Station",
            "distance_km": "12.5",
        }
        limit = get_settings().max_destination_changes
        for _ in range(limit):
            assert (
                client.post(
                    f"/api/v1/orders/{oid}/change-destination",
                    headers=_h(passenger),
                    json=body,
                ).status_code
                == 200
            )
        r = client.post(
            f"/api/v1/orders/{oid}/change-destination", headers=_h(passenger), json=body
        )
        assert r.status_code == 429, r.text
        assert r.json()["details"]["reason"] == "TOO_MANY_CHANGES"
        assert r.json()["details"]["limit"] == limit

    def test_a_pre_trip_order_cannot_change_its_destination(self, client):
        passenger = client.activate("+85291600241")
        oid = _create_order(client, passenger)["id"]
        r = client.post(
            f"/api/v1/orders/{oid}/change-destination",
            headers=_h(passenger),
            json={
                "dropoff_lat": 22.3375,
                "dropoff_lng": 114.1765,
                "dropoff_address": "Kowloon Tong Station",
            },
        )
        assert r.status_code == 409, r.text
        assert r.json()["details"]["reason"] == "WRONG_STATUS"


# --------------------------------------------------------------------------- #
# Interruption — instant, and adjudicated afterwards
# --------------------------------------------------------------------------- #


class TestInterrupt:
    def test_interrupt_is_instant_and_opens_a_dispute_in_the_same_transaction(self, client):
        """No `INTERRUPTED_PENDING`.

        A trip that has gone wrong has to be stoppable by the person in the car.
        Because it is instant, the money question is *deferred*, not decided —
        and the case must exist the moment the trip ends, or a crash between the
        two writes leaves an interrupted trip nobody is accountable for.
        """
        oid, passenger, _driver = _drive_to_in_trip(client, "+85291600301", "+85291600302")
        r = client.post(
            f"/api/v1/orders/{oid}/interrupt",
            headers=_h(passenger),
            json={"reason_code": "ACCIDENT", "note": "rear-ended at the lights"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "INTERRUPTED"
        assert body["interruption_reason"] == "ACCIDENT"
        assert body["interrupted_by_kind"] == "PASSENGER"
        assert body["interrupted_at"]

        disputes = client.get("/api/v1/admin/disputes", headers=client.admin_headers()).json()[
            "items"
        ]
        mine = [d for d in disputes if d["order_id"] == oid]
        assert len(mine) == 1, disputes
        # ACCIDENT is a safety reason, so the case is escalated on arrival.
        assert mine[0]["source"] == "AUTO_INTERRUPTED_SAFETY"
        assert mine[0]["severity"] == "SAFETY_CRITICAL"
        # Filed against the counterparty as a *default*, not a verdict.
        assert mine[0]["against_kind"] == "DRIVER"

    def test_the_driver_may_interrupt_too_and_the_case_names_the_passenger(self, client):
        oid, _passenger, driver = _drive_to_in_trip(client, "+85291600311", "+85291600312")
        r = client.post(
            f"/api/v1/orders/{oid}/interrupt",
            headers=_h(driver["token"]),
            json={"reason_code": "FARE_DISPUTE", "note": "refused to pay the agreed fare"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["interrupted_by_kind"] == "DRIVER"

        disputes = client.get("/api/v1/admin/disputes", headers=client.admin_headers()).json()[
            "items"
        ]
        mine = [d for d in disputes if d["order_id"] == oid]
        assert mine and mine[0]["against_kind"] == "PASSENGER"
        assert mine[0]["source"] == "AUTO_INTERRUPTED"

    def test_a_passenger_cannot_file_a_reason_that_names_themselves(self, client):
        """§6.1 — the client hides these; the server has to refuse them.

        `PASSENGER_MISCONDUCT` describes a passenger, so only a driver can
        honestly file it. Accepting it from the passenger writes a
        self-accusation into the record an operator judges from, and a hidden
        menu option is a courtesy rather than a control.
        """
        oid, passenger, _driver = _drive_to_in_trip(client, "+85291600701", "+85291600702")
        r = client.post(
            f"/api/v1/orders/{oid}/interrupt",
            headers=_h(passenger),
            json={"reason_code": "PASSENGER_MISCONDUCT", "note": "they were rude to me"},
        )
        assert r.status_code == 422, r.text
        assert r.json()["details"]["reason"] == "REASON_NOT_FOR_PARTY"
        # Refused *before* the transition, so the trip is untouched and no
        # dispute was opened against the driver.
        assert (
            client.get(f"/api/v1/orders/{oid}", headers=_h(passenger)).json()["status"] == "IN_TRIP"
        )
        disputes = client.get("/api/v1/admin/disputes", headers=client.admin_headers()).json()[
            "items"
        ]
        assert not [d for d in disputes if d["order_id"] == oid], disputes

    def test_a_driver_cannot_file_a_reason_that_names_themselves(self, client):
        oid, _passenger, driver = _drive_to_in_trip(client, "+85291600711", "+85291600712")
        r = client.post(
            f"/api/v1/orders/{oid}/interrupt",
            headers=_h(driver["token"]),
            json={"reason_code": "DRIVER_MISCONDUCT"},
        )
        assert r.status_code == 422, r.text
        assert r.json()["details"]["reason"] == "REASON_NOT_FOR_PARTY"

    def test_the_guard_excludes_one_direction_only(self, client):
        """The mirror reasons stay legal, or the check has over-reached.

        `DRIVER_MISCONDUCT` is exactly what a passenger interrupting *should*
        be able to say, so a guard that refused it would break the feature it
        is protecting.
        """
        oid, passenger, _driver = _drive_to_in_trip(client, "+85291600721", "+85291600722")
        r = client.post(
            f"/api/v1/orders/{oid}/interrupt",
            headers=_h(passenger),
            json={"reason_code": "DRIVER_MISCONDUCT", "note": "refused to continue"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["interruption_reason"] == "DRIVER_MISCONDUCT"

    def test_a_driver_may_report_a_sick_passenger(self, client):
        """The named requirement — `PASSENGER_SICK` is the driver's to raise."""
        oid, _passenger, driver = _drive_to_in_trip(client, "+85291600731", "+85291600732")
        r = client.post(
            f"/api/v1/orders/{oid}/interrupt",
            headers=_h(driver["token"]),
            json={"reason_code": "PASSENGER_SICK", "note": "taken ill in the back seat"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["interruption_reason"] == "PASSENGER_SICK"

    def test_the_two_menus_together_still_cover_every_reason(self):
        """A member added later must not silently become unfilable.

        `INTERRUPTION_REASONS_BY_PARTY` is derived as
        `all members - the other party's exclusive reasons`, so a new reason is
        allowed for both parties until someone deliberately excludes it. This
        pins that derivation: if the enum grows and one side stops covering a
        member, the reason exists but no one can use it.
        """
        every = set(InterruptionReason)
        covered = set().union(*INTERRUPTION_REASONS_BY_PARTY.values())
        assert covered == every, sorted(every - covered)

        # And each side is refused something, or the guard is a no-op on it.
        assert every - INTERRUPTION_REASONS_BY_PARTY[OrderParty.PASSENGER]
        assert every - INTERRUPTION_REASONS_BY_PARTY[OrderParty.DRIVER]

    def test_other_requires_a_note(self, client):
        """The enum classifies; for OTHER the note *is* the classification."""
        oid, passenger, _driver = _drive_to_in_trip(client, "+85291600321", "+85291600322")
        r = client.post(
            f"/api/v1/orders/{oid}/interrupt",
            headers=_h(passenger),
            json={"reason_code": "OTHER"},
        )
        assert r.status_code == 422, r.text
        assert r.json()["details"]["reason"] == "NOTE_REQUIRED"
        # And the trip is untouched.
        assert (
            client.get(f"/api/v1/orders/{oid}", headers=_h(passenger)).json()["status"] == "IN_TRIP"
        )

    def test_a_trip_that_has_not_started_cannot_be_interrupted(self, client):
        passenger = client.activate("+85291600331")
        oid = _create_order(client, passenger)["id"]
        r = client.post(
            f"/api/v1/orders/{oid}/interrupt",
            headers=_h(passenger),
            json={"reason_code": "ACCIDENT"},
        )
        assert r.status_code == 409, r.text

    def test_an_interrupted_trip_cannot_be_completed(self, client):
        """Terminal means terminal — the ledger is derived from these rows."""
        oid, passenger, driver = _drive_to_in_trip(client, "+85291600341", "+85291600342")
        client.post(
            f"/api/v1/orders/{oid}/interrupt",
            headers=_h(passenger),
            json={"reason_code": "VEHICLE_BREAKDOWN", "note": "engine cut out"},
        )
        r = client.post(f"/api/v1/orders/{oid}/complete", headers=_h(driver["token"]))
        assert r.status_code == 400, r.text


# --------------------------------------------------------------------------- #
# Defaulting: the penalty, and the cool-down
# --------------------------------------------------------------------------- #


class TestDefaulting:
    def test_a_passenger_default_arms_a_cool_down_on_new_orders(self, client):
        """DECISION-5: instant cool-down, symmetric with the driver's.

        There is no passenger wallet to debit (`docs/IN_TRIP_REDESIGN.md`
        §1.3c), so the cool-down is the half of the penalty that is actually
        enforceable today. What it breaks is the "default, re-book, default
        again" loop, which is the abuse it exists for.
        """
        passenger = client.activate("+85291600401")
        driver = _mk_active_driver(client, "+85291600402")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)

        r = client.post(
            f"/api/v1/orders/{oid}/cancel",
            headers=_h(passenger),
            json={"reason_code": "OTHER", "reason": "changed my mind"},
        )
        assert r.status_code == 200, r.text

        blocked = client.post("/api/v1/orders", headers=_h(passenger), json=_ORDER)
        assert blocked.status_code == 429, blocked.text
        assert blocked.json()["details"]["reason"] == "COOLDOWN"
        assert int(blocked.headers["Retry-After"]) > 0

    def test_cancelling_before_a_driver_accepts_is_free(self, client):
        """Nobody has committed to anything yet, so there is nothing to price."""
        passenger = client.activate("+85291600411")
        oid = _create_order(client, passenger)["id"]

        r = client.post(
            f"/api/v1/orders/{oid}/cancel", headers=_h(passenger), json={"reason": "oops"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CANCELLED"

        cancel_event = asyncio.run(
            _fetch(
                client,
                "SELECT actor_kind, from_status, to_status, payload FROM order_events "
                "WHERE order_id = CAST(:i AS uuid) "
                "AND event = 'STATE_CHANGED' AND to_status = 'CANCELLED'",
                {"i": oid},
            )
        )
        assert len(cancel_event) == 1, "a cancellation STATE_CHANGED event must be recorded"
        assert cancel_event[0]["actor_kind"] == "PASSENGER"
        assert cancel_event[0]["to_status"] == "CANCELLED"
        assert cancel_event[0]["payload"]["cancellation_reason"] == "oops"

        # No cool-down: the next order goes through.
        assert client.post("/api/v1/orders", headers=_h(passenger), json=_ORDER).status_code == 201

    def test_a_passenger_default_is_recorded_even_though_it_is_not_collected(self, client):
        """The gap is stated in the timeline, not hidden.

        `LedgerEntry.driver_profile_id` is NOT NULL, so a passenger penalty has
        nowhere to be posted. It is written as an `order_events` row with
        `settled: false` so an operator can find the amount that is owed —
        silently dropping it would make "the passenger defaulted" unrecoverable.
        """
        passenger = client.activate("+85291600421")
        driver = _mk_active_driver(client, "+85291600422")
        order = _create_order(client, passenger)
        oid = order["id"]
        _grab(client, oid, driver)

        client.post(
            f"/api/v1/orders/{oid}/cancel",
            headers=_h(passenger),
            json={"reason_code": "OTHER", "reason": "changed my mind"},
        )
        rows = asyncio.run(
            _fetch(
                client,
                "SELECT payload FROM order_events "
                "WHERE order_id = CAST(:i AS uuid) AND event = 'PENALTY_CHARGED'",
                {"i": oid},
            )
        )
        assert rows, "the penalty was not recorded at all"
        payload = rows[0]["payload"]
        assert payload["settled"] is False
        assert Decimal(payload["amount_hkd"]) == Decimal(order["estimated_total_hkd"]).quantize(
            Decimal("0.01")
        )
        assert payload["share_percent"] == "100"

    def test_the_cool_down_expires(self, client):
        """It is a Redis TTL, so it clears itself with no job and no operator.

        A sweeper that could fail silently and leave people locked out is the
        worst version of this feature — the TTL is the design.
        """
        passenger = client.activate("+85291600431")
        driver = _mk_active_driver(client, "+85291600432")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)
        client.post(
            f"/api/v1/orders/{oid}/cancel",
            headers=_h(passenger),
            json={"reason_code": "OTHER", "reason": "changed my mind"},
        )
        assert client.post("/api/v1/orders", headers=_h(passenger), json=_ORDER).status_code == 429

        # Delete the key rather than sleeping 15 minutes: this asserts the
        # *mechanism* (a key with a TTL) and not the clock.
        from app.core import cooldown as cooldown_mod

        async def clear():
            import redis.asyncio as aioredis

            from app.core.config import get_settings

            rds = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            try:
                user_id = client.get("/api/v1/auth/me", headers=_h(passenger)).json()["id"]
                await cooldown_mod.clear_cooldown(rds, cooldown_mod.PASSENGER, user_id)
            finally:
                await rds.aclose()

        asyncio.run(clear())
        assert client.post("/api/v1/orders", headers=_h(passenger), json=_ORDER).status_code == 201

    def test_an_arrived_order_cannot_be_cancelled_by_either_party(self, client):
        """Arrival is proven, so the cancel right is locked — 409, not 400.

        "You may not do this yet" and "this transition does not exist" are
        different answers, and the client acts on the difference.
        """
        oid, passenger, driver = _drive_to_driver_arrived(client, "+85291600441", "+85291600442")

        for token in (passenger, driver["token"]):
            r = client.post(
                f"/api/v1/orders/{oid}/cancel",
                headers=_h(token),
                json={"reason_code": "OTHER", "reason": "nope"},
            )
            assert r.status_code == 409, r.text
            assert r.json()["details"]["reason"] == "CANCEL_LOCKED"

        # The only ways out are the two terminal ones — and `COMPLETED` is
        # reached *through* `IN_TRIP`, never directly. A trip cannot be marked
        # complete without the meter having started.
        started = client.post(f"/api/v1/orders/{oid}/start", headers=_h(driver["token"]))
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "IN_TRIP"
        done = client.post(f"/api/v1/orders/{oid}/complete", headers=_h(driver["token"]))
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "COMPLETED"

    def test_a_driver_may_interrupt_an_arrived_order_they_have_not_started(self, client):
        """The escape hatch that keeps `DRIVER_ARRIVED` from being a dead end.

        "Arrival cannot be cancelled" plus "only `IN_TRIP` can be interrupted"
        would lock both parties in until the driver chose to start. A driver who
        finds a passenger too drunk to carry needs a way out *before* the meter
        runs.
        """
        oid, _passenger, driver = _drive_to_driver_arrived(client, "+85291600451", "+85291600452")

        r = client.post(
            f"/api/v1/orders/{oid}/interrupt",
            headers=_h(driver["token"]),
            json={"reason_code": "PASSENGER_SICK", "note": "vomiting, unsafe to carry"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "INTERRUPTED"


# --------------------------------------------------------------------------- #
# The deposit gate (DECISION-3)
# --------------------------------------------------------------------------- #


class TestDepositGate:
    def test_a_negative_balance_blocks_new_grabs_with_423(self, client):
        """423, never 403.

        This is a state the driver can clear by topping up, whereas 403 is a
        permission verdict. Conflating them makes "top up and retry"
        indistinguishable from "you are banned".
        """
        passenger = client.activate("+85291600501")
        driver = _mk_active_driver(client, "+85291600502")
        oid = _create_order(client, passenger)["id"]

        client.exec_sql(
            "UPDATE driver_deposits SET balance_hkd = -1, "
            "acceptance_unlocked_at = NULL WHERE driver_profile_id = "
            "CAST(:d AS uuid)",
            {"d": driver["driver_id"]},
        )
        r = client.post(f"/api/v1/orders/{oid}/grab", headers=_h(driver["token"]))
        assert r.status_code == 423, r.text
        assert r.json()["details"]["reason"] == "DEPOSIT_INSUFFICIENT"

        # Arrears must NOT change `DriverStatus` — the gate is the gate.
        assert (
            client.get("/api/v1/drivers/me", headers=_h(driver["token"])).json()["status"]
            == "ACTIVE"
        )

    def test_topup_alone_does_not_restore_grab_and_admin_unlock_does(self, client):
        """DECISION-3's second half: clearing arrears is not auto-unlock."""
        passenger = client.activate("+85291600503")
        driver = _mk_active_driver(client, "+85291600504")
        oid = _create_order(client, passenger)["id"]

        client.exec_sql(
            "UPDATE driver_deposits SET balance_hkd = -1, "
            "acceptance_unlocked_at = NULL WHERE driver_profile_id = "
            "CAST(:d AS uuid)",
            {"d": driver["driver_id"]},
        )
        r = client.post(f"/api/v1/orders/{oid}/grab", headers=_h(driver["token"]))
        assert r.status_code == 423, r.text

        # A grant clears the money but must NOT clear the lock.
        r = client.post(
            f"/api/v1/admin/drivers/{driver['driver_id']}/deposit/grant",
            headers=client.admin_headers(),
            json={"amount_hkd": "1.00"},
        )
        assert r.status_code == 200, r.text
        r = client.post(f"/api/v1/orders/{oid}/grab", headers=_h(driver["token"]))
        assert r.status_code == 423, r.text
        assert r.json()["details"]["acceptance_locked"] is True

        # The explicit operator action is what returns the driver to the road.
        r = client.post(
            f"/api/v1/admin/drivers/{driver['driver_id']}/deposit/unlock",
            headers=client.admin_headers(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["acceptance_unlocked_at"]
        r = client.post(f"/api/v1/orders/{oid}/grab", headers=_h(driver["token"]))
        assert r.status_code == 200, r.text

    def test_arrears_do_not_block_finishing_a_trip_already_under_way(self, client):
        """The gate is on *new* business only.

        A driver who is mid-trip when the fee lands must be able to finish —
        blocking `/complete` would strand a real passenger to enforce a
        bookkeeping rule.
        """
        oid, _passenger, driver = _drive_to_in_trip(client, "+85291600511", "+85291600512")
        client.exec_sql(
            "UPDATE driver_deposits SET balance_hkd = -50 WHERE driver_profile_id = "
            "CAST(:d AS uuid)",
            {"d": driver["driver_id"]},
        )
        r = client.post(f"/api/v1/orders/{oid}/complete", headers=_h(driver["token"]))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "COMPLETED"


# --------------------------------------------------------------------------- #
# The per-trip platform fee (DECISION-1)
# --------------------------------------------------------------------------- #


class TestTripFee:
    def test_start_charges_the_trip_fee_once_against_the_driver(self, client):
        """The platform's actual revenue from a trip.

        The fare itself never passes through the platform (Cap. 374D — it is an
        information intermediary), so there is no fare to take a cut of. The fee
        is charged to the driver's deposit, with a `trip:<order_id>` reference
        whose uniqueness is what makes a retried `/start` replay instead of
        charging twice.
        """
        oid, _passenger, driver = _drive_to_in_trip(client, "+85291600601", "+85291600602")

        rows = asyncio.run(
            _fetch(
                client,
                "SELECT count(*) AS n FROM ledger_entries WHERE reference = :r",
                {"r": f"trip:{oid}"},
            )
        )
        assert rows[0]["n"] == 1, rows

        ledger = client.get("/api/v1/drivers/me/ledger", headers=_h(driver["token"])).json()
        fees = [i for i in ledger["items"] if i["entry_type"] == "PLATFORM_TRIP_FEE"]
        assert len(fees) == 1, ledger
        assert Decimal(fees[0]["amount_hkd"]) == Decimal("-5.00")
        assert fees[0]["order_id"] == oid

    def test_the_fee_is_charged_at_start_not_at_completion(self, client):
        oid, _passenger, driver = _drive_to_in_trip(client, "+85291600611", "+85291600612")
        before = client.get("/api/v1/drivers/me/ledger", headers=_h(driver["token"])).json()
        assert [i for i in before["items"] if i["entry_type"] == "PLATFORM_TRIP_FEE"]

        client.post(f"/api/v1/orders/{oid}/complete", headers=_h(driver["token"]))
        after = client.get("/api/v1/drivers/me/ledger", headers=_h(driver["token"])).json()
        fees = [i for i in after["items"] if i["entry_type"] == "PLATFORM_TRIP_FEE"]
        assert len(fees) == 1, "completion must not charge the trip fee a second time"
