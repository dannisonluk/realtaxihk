"""P4 §7 — trip lifecycle events actually reach the order's socket.

`TripHub.publish()` was built for this and reachable only from `record_tick`, so
the channel carried driver positions and no lifecycle news. The other party
learned about an arrival claim or an interruption from the 10-second poll
(`AppConfig.locationPollInterval`) — and `docs/IN_TRIP_REDESIGN.md` §7 makes that
a blocking defect for P4, because the arrival-confirmation prompt has to appear
when the driver claims, not up to ten seconds later.

**These tests subscribe to the real Redis channel.** Asserting "publish was
called" against a mock would pass even with the wrong channel name, and a
hard-coded key drifting from its owner is a failure this codebase has already
had once (`GEO_ORDERS_KEY`). Subscribing end-to-end is the only version that
catches it.

The negative case matters as much as the positive ones: a *refused* claim must
publish nothing, or every client would refresh on a non-event.
"""

import json

import redis as redis_sync

from app.core.config import get_settings
from app.services.order.trip_service import channel_for

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

_PICKUP = (_ORDER["pickup_lat"], _ORDER["pickup_lng"])
# North Point: ~7 km from the pickup, i.e. far outside the 150 m arrival radius.
_FAR = (_ORDER["dropoff_lat"], _ORDER["dropoff_lng"])


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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


def _drive_to_in_trip(client, passenger_phone: str, driver_phone: str) -> tuple:
    """Walk an order to `IN_TRIP` through the real endpoints.

    Returns `(order_id, passenger_token, driver)`. Every step is asserted so a
    regression in the arrival flow fails here rather than as a confusing
    failure inside a test about pub/sub.
    """
    passenger_token = client.activate(passenger_phone)
    driver = _mk_active_driver(client, driver_phone)
    oid = _create_order(client, passenger_token)["id"]
    _grab(client, oid, driver)

    assert _set_location(client, driver, *_PICKUP).status_code == 200
    r = client.post(f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={})
    assert r.json()["status"] == "PENDING_ARRIVAL_CONFIRM", r.text
    r = client.post(
        f"/api/v1/orders/{oid}/arrival-confirm",
        headers=_h(passenger_token),
        json={"phone_last4": passenger_phone[-4:]},
    )
    assert r.json()["status"] == "DRIVER_ARRIVED", r.text
    r = client.post(f"/api/v1/orders/{oid}/start", headers=_h(driver["token"]))
    assert r.json()["status"] == "IN_TRIP", r.text
    return oid, passenger_token, driver


class _Channel:
    """A subscriber on one order's channel, using the synchronous client.

    Deliberately not the app's async client: the app publishes from the
    TestClient's own event loop, and a second, independent connection is what
    makes this a real end-to-end check rather than a self-delivery.
    """

    def __init__(self, order_id: str):
        self._redis = redis_sync.from_url(get_settings().redis_url, decode_responses=True)
        self._pubsub = self._redis.pubsub()
        self._pubsub.subscribe(channel_for(str(order_id)))
        # `subscribe()` returns before the server acknowledges it. Draining the
        # confirmation means the first `get_message` below can only be a
        # published event.
        assert self._pubsub.get_message(timeout=2) is not None

    def drain(self, timeout: float = 2.0) -> list[dict]:
        out: list[dict] = []
        while True:
            msg = self._pubsub.get_message(timeout=timeout, ignore_subscribe_messages=True)
            if msg is None:
                break
            out.append(json.loads(msg["data"]))
        return out

    def close(self) -> None:
        self._pubsub.close()
        self._redis.close()

    def __enter__(self) -> "_Channel":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class TestLifecycleEventsArePublished:
    def test_arrival_claim_reaches_the_channel(self, client):
        passenger = client.activate("+85291600201")
        driver = _mk_active_driver(client, "+85291600202")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)
        assert _set_location(client, driver, *_PICKUP).status_code == 200

        with _Channel(oid) as channel:
            r = client.post(
                f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={}
            )
            assert r.status_code == 200, r.text
            events = channel.drain()

        assert [e["event"] for e in events] == ["ARRIVAL_CLAIMED"], events
        assert events[0]["type"] == "order"
        assert events[0]["order_id"] == oid

    def test_arrival_confirmation_reaches_the_channel(self, client):
        """The driver's screen leaves "waiting" on this message; without it the
        driver sits on a stale prompt until their next poll."""
        passenger_phone = "+85291600211"
        passenger = client.activate(passenger_phone)
        driver = _mk_active_driver(client, "+85291600212")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)
        assert _set_location(client, driver, *_PICKUP).status_code == 200
        client.post(f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={})

        with _Channel(oid) as channel:
            r = client.post(
                f"/api/v1/orders/{oid}/arrival-confirm",
                headers=_h(passenger),
                json={"phone_last4": passenger_phone[-4:]},
            )
            assert r.status_code == 200, r.text
            events = channel.drain()

        assert [e["event"] for e in events] == ["ARRIVAL_CONFIRMED"], events
        assert events[0]["status"] == "DRIVER_ARRIVED"

    def test_interruption_reaches_the_channel_with_its_reason(self, client):
        """DECISION-2: the trip ends *now* for both parties. The reason travels
        with the event so the other screen can say why, not just that."""
        oid, _passenger, driver = _drive_to_in_trip(client, "+85291600221", "+85291600222")

        # PASSENGER_SICK is the driver's to report: a passenger cannot declare
        # themselves ill to end a trip they are paying for (IN_TRIP_REDESIGN
        # §6.1 — the server validates, the menu is courtesy).
        with _Channel(oid) as channel:
            r = client.post(
                f"/api/v1/orders/{oid}/interrupt",
                headers=_h(driver["token"]),
                json={"reason_code": "PASSENGER_SICK"},
            )
            assert r.status_code == 200, r.text
            events = channel.drain()

        assert [e["event"] for e in events] == ["INTERRUPTED"], events
        assert events[0]["status"] == "INTERRUPTED"
        assert events[0]["reason_code"] == "PASSENGER_SICK"
        assert events[0]["interrupted_by"] == "DRIVER"

    def test_destination_change_reaches_the_channel(self, client):
        oid, passenger, _driver = _drive_to_in_trip(client, "+85291600231", "+85291600232")

        with _Channel(oid) as channel:
            r = client.post(
                f"/api/v1/orders/{oid}/change-destination",
                headers=_h(passenger),
                json={
                    "dropoff_lat": 22.279,
                    "dropoff_lng": 114.162,  # Admiralty
                    "dropoff_address": "Admiralty Centre",
                    "distance_km": "5.0",
                },
            )
            assert r.status_code == 200, r.text
            events = channel.drain()

        assert [e["event"] for e in events] == ["DESTINATION_CHANGED"], events
        assert events[0]["status"] == "DESTINATION_CHANGED"
        assert events[0]["change_number"] == 1


class TestTheEventCannotOutrunItsOwnTransaction:
    def test_the_published_status_is_the_committed_one(self, client):
        """The ordering rule from `trip_event_service`: the session commits
        *before* the publish.

        `get_session` commits after the response, so a naive publish would send
        subscribers to re-read a row that had not changed yet — they would see
        `ACCEPTED` and repaint the old state, which is the staleness the whole
        change exists to remove. Reading the order back immediately after the
        event must show the new status, not the previous one.
        """
        passenger = client.activate("+85291600241")
        driver = _mk_active_driver(client, "+85291600242")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)
        assert _set_location(client, driver, *_PICKUP).status_code == 200

        with _Channel(oid) as channel:
            client.post(f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={})
            events = channel.drain()

        assert events, "arrival-claim published nothing"
        announced = events[0]["status"]
        # The same read a subscriber would make when the message arrives.
        current = client.get(f"/api/v1/orders/{oid}", headers=_h(driver["token"])).json()["status"]
        assert announced == current == "PENDING_ARRIVAL_CONFIRM"


class TestRefusalsPublishNothing:
    def test_a_claim_from_too_far_publishes_nothing(self, client):
        """A refusal changes no state, so an event would only cause every client
        to refresh for nothing — and would make the socket a way to watch
        someone else's failed attempts."""
        passenger = client.activate("+85291600251")
        driver = _mk_active_driver(client, "+85291600252")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)
        assert _set_location(client, driver, *_FAR).status_code == 200

        with _Channel(oid) as channel:
            r = client.post(
                f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={}
            )
            assert r.status_code == 422, r.text
            assert r.json()["details"]["reason"] == "TOO_FAR"
            assert channel.drain(timeout=0.75) == []

    def test_a_wrong_pin_does_not_publish(self, client):
        """A wrong last-4 is a retry, not an event: the passenger's own screen
        renders the attempt count from the response it just got."""
        passenger_phone = "+85291600261"
        passenger = client.activate(passenger_phone)
        driver = _mk_active_driver(client, "+85291600262")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)
        assert _set_location(client, driver, *_PICKUP).status_code == 200
        client.post(f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={})

        wrong = "0000" if passenger_phone[-4:] != "0000" else "1111"
        with _Channel(oid) as channel:
            r = client.post(
                f"/api/v1/orders/{oid}/arrival-confirm",
                headers=_h(passenger),
                json={"phone_last4": wrong},
            )
            assert r.status_code == 401, r.text
            assert channel.drain(timeout=0.75) == []

    def test_the_third_wrong_pin_publishes_the_conflict(self, client):
        """The opposite of the case above, and the reason the two are tested
        together: on the *last* attempt the order really does move (back to
        `ACCEPTED`, with a dispute open), so that one must publish."""
        passenger_phone = "+85291600271"
        passenger = client.activate(passenger_phone)
        driver = _mk_active_driver(client, "+85291600272")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)
        assert _set_location(client, driver, *_PICKUP).status_code == 200
        client.post(f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={})

        wrong = "0000" if passenger_phone[-4:] != "0000" else "1111"
        for _ in range(2):
            client.post(
                f"/api/v1/orders/{oid}/arrival-confirm",
                headers=_h(passenger),
                json={"phone_last4": wrong},
            )

        with _Channel(oid) as channel:
            r = client.post(
                f"/api/v1/orders/{oid}/arrival-confirm",
                headers=_h(passenger),
                json={"phone_last4": wrong},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "ACCEPTED", r.text
            events = channel.drain()

        assert [e["event"] for e in events] == ["ARRIVAL_CONFLICT"], events
        assert events[0]["status"] == "ACCEPTED"


class TestTheRemainingTransitionsArePublished:
    """The other half of P4 §7: the transitions a review found still on the poll.

    The events covered above all live in the arrival flow. These five come from
    four other endpoints — `/grab` writes through `GrabService`'s own session,
    `/cancel` moves money, `/start` and `/complete` are plain transitions, and
    resolve lives in the *admin* API — so each call site has its own failure
    mode and none of them is covered by the others.
    """

    def test_the_grab_reaches_the_channel(self, client):
        """The one call site where the write is already durable before the
        handler runs: `GrabService` commits in a session of its own."""
        passenger = client.activate("+85291600273")
        driver = _mk_active_driver(client, "+85291600274")
        oid = _create_order(client, passenger)["id"]

        with _Channel(oid) as channel:
            _grab(client, oid, driver)
            events = channel.drain()

        assert [e["event"] for e in events] == ["GRABBED"], events
        assert events[0]["status"] == "ACCEPTED"
        # The *profile* id, not the account id (SEC-27): a screen that matched
        # on the wrong one would show the grab and hide its own trip.
        assert events[0]["driver_profile_id"] == driver["driver_id"]

    def test_the_departure_reaches_the_channel(self, client):
        """`DRIVER_ARRIVED` → `IN_TRIP` is what turns the passenger's screen
        from "he is coming" into "the meter is running"."""
        passenger_phone = "+85291600275"
        passenger = client.activate(passenger_phone)
        driver = _mk_active_driver(client, "+85291600276")
        oid = _create_order(client, passenger)["id"]
        _grab(client, oid, driver)
        assert _set_location(client, driver, *_PICKUP).status_code == 200
        client.post(f"/api/v1/orders/{oid}/arrival-claim", headers=_h(driver["token"]), json={})
        arrived = client.post(
            f"/api/v1/orders/{oid}/arrival-confirm",
            headers=_h(passenger),
            json={"phone_last4": passenger_phone[-4:]},
        )
        assert arrived.json()["status"] == "DRIVER_ARRIVED", arrived.text

        with _Channel(oid) as channel:
            r = client.post(f"/api/v1/orders/{oid}/start", headers=_h(driver["token"]))
            assert r.status_code == 200, r.text
            events = channel.drain()

        assert [e["event"] for e in events] == ["TRIP_STARTED"], events
        assert events[0]["status"] == "IN_TRIP"

    def test_the_completion_reaches_the_channel(self, client):
        oid, _passenger, driver = _drive_to_in_trip(client, "+85291600277", "+85291600278")

        with _Channel(oid) as channel:
            r = client.post(f"/api/v1/orders/{oid}/complete", headers=_h(driver["token"]))
            assert r.status_code == 200, r.text
            events = channel.drain()

        assert [e["event"] for e in events] == ["TRIP_COMPLETED"], events
        assert events[0]["status"] == "COMPLETED"

    def test_a_free_cancel_reaches_the_channel(self, client):
        """Before anyone has committed there is no penalty and no required
        `reason_code`, but the message still matters: a driver's job list is
        showing a trip that no longer exists."""
        passenger = client.activate("+85291600279")
        oid = _create_order(client, passenger)["id"]

        with _Channel(oid) as channel:
            r = client.post(
                f"/api/v1/orders/{oid}/cancel",
                headers=_h(passenger),
                json={"reason": "changed my mind"},
            )
            assert r.status_code == 200, r.text
            events = channel.drain()

        assert [e["event"] for e in events] == ["CANCELLED"], events
        assert events[0]["status"] == "CANCELLED"
        assert events[0]["cancelled_by"] == "PASSENGER"
        assert events[0]["penalty_hkd"] == "0.00"

    def test_a_resolved_dispute_reaches_the_order_channel(self, client):
        """Resolution is the moment both parties stop waiting. The link from
        case to order is what selects the channel, so this test makes one."""
        oid, _passenger, _driver = _drive_to_in_trip(client, "+85291600280", "+85291600281")
        opened = client.post(
            "/api/v1/admin/disputes",
            headers=client.admin_headers(),
            json={
                "category": "FARE",
                "summary": "meter looked wrong",
                "severity": "NORMAL",
                "order_id": oid,
            },
        )
        assert opened.status_code == 201, opened.text
        dispute_id = opened.json()["id"]

        with _Channel(oid) as channel:
            r = client.post(
                f"/api/v1/admin/disputes/{dispute_id}/resolve",
                headers=client.admin_headers(),
                json={"resolution": "NONE", "note": "no charge warranted", "close": True},
            )
            assert r.status_code == 200, r.text
            events = channel.drain()

        assert [e["event"] for e in events] == ["DISPUTE_RESOLVED"], events
        assert events[0]["dispute_id"] == dispute_id
        assert events[0]["resolution"] == "NONE"
        assert events[0]["close"] is True


class TestACaseWithNoOrderIsStillResolvable:
    def test_resolving_an_unlinked_case_commits_and_announces_nothing(self, client):
        """The other branch of the same handler: no `order_id`, so there is no
        channel — and the commit that `publish_lifecycle` would have made still
        has to happen, or the resolution is rolled back when the request ends.

        A general conduct report is the real case: a passenger being rude to a
        bystander has no trip to announce on.
        """
        opened = client.post(
            "/api/v1/admin/disputes",
            headers=client.admin_headers(),
            json={"category": "CONDUCT", "summary": "rude to a bystander", "severity": "LOW"},
        )
        assert opened.status_code == 201, opened.text
        dispute_id = opened.json()["id"]

        r = client.post(
            f"/api/v1/admin/disputes/{dispute_id}/resolve",
            headers=client.admin_headers(),
            json={"resolution": "NONE", "note": "spoken to", "close": True},
        )
        assert r.status_code == 200, r.text

        # Durable on a *later* request: the commit really happened.
        after = client.get(
            f"/api/v1/admin/disputes/{dispute_id}", headers=client.admin_headers()
        ).json()
        assert after["status"] == "CLOSED", after
        assert after["resolved_at"] is not None
