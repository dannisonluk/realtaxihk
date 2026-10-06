"""Passenger-facing dispute filing (P4 §4.4).

The in-app "提出申訴" button previously only explained that the platform would
open a case; this module pins the server contract that turns that explanation
into an actual `PARTY_REPORT` row with an SLA.
"""

from typing import Any

_PICKUP = {"lat": 22.284, "lng": 114.158}  # Central
_DROPOFF = (22.315, 114.219)  # North Point

_ORDER = {
    "pickup_lat": _PICKUP["lat"],
    "pickup_lng": _PICKUP["lng"],
    "pickup_address": "Statue Square, Central",
    "dropoff_lat": _DROPOFF[0],
    "dropoff_lng": _DROPOFF[1],
    "dropoff_address": "Harbour North, North Point",
    "distance_km": "4.2",
    "taxi_type": "URBAN",
}


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _passenger(client, n: int) -> str:
    return client.activate(f"+852{916 * 10**5 + n}")


def _driver(client, n: int) -> dict[str, Any]:
    token = _passenger(client, n)
    r = client.post(
        "/api/v1/drivers/register",
        headers=_h(token),
        json={
            "hk_id_last4": "0000",
            "taxi_driver_plate_no": f"TD{n}",
            "vehicle_reg_mark": f"ZZ{n}",
            "taxi_type": "URBAN",
        },
    )
    assert r.status_code == 201, r.text
    driver_id = r.json()["id"]
    admin = client.admin_headers()
    assert (
        client.post(
            f"/api/v1/admin/drivers/{driver_id}/review",
            headers=admin,
            json={"decision": "approve"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/admin/drivers/{driver_id}/deposit/grant",
            headers=admin,
            json={"amount_hkd": "500.00"},
        ).status_code
        == 200
    )
    return {"token": token, "driver_id": driver_id}


def _create_order(client, passenger_token: str) -> dict[str, Any]:
    r = client.post("/api/v1/orders", headers=_h(passenger_token), json=_ORDER)
    assert r.status_code == 201, r.text
    return r.json()


def _complete_order(client, passenger_n: int, driver_n: int) -> tuple[str, str, dict[str, Any]]:
    passenger_token = _passenger(client, passenger_n)
    driver = _driver(client, driver_n)
    order = _create_order(client, passenger_token)
    oid = order["id"]

    grab = client.post(f"/api/v1/orders/{oid}/grab", headers=_h(driver["token"]))
    assert grab.status_code == 200, grab.text

    location = client.post(
        "/api/v1/drivers/location",
        headers=_h(driver["token"]),
        json={"lat": _PICKUP["lat"], "lng": _PICKUP["lng"]},
    )
    assert location.status_code == 200, location.text

    claim = client.post(
        f"/api/v1/orders/{oid}/arrival-claim",
        headers=_h(driver["token"]),
        json={},
    )
    assert claim.status_code == 200, claim.text

    confirm = client.post(
        f"/api/v1/orders/{oid}/arrival-confirm",
        headers=_h(passenger_token),
        json={"phone_last4": f"{passenger_n % 10**4:04d}"},
    )
    assert confirm.status_code == 200, confirm.text

    start = client.post(f"/api/v1/orders/{oid}/start", headers=_h(driver["token"]))
    assert start.status_code == 200, start.text

    complete = client.post(f"/api/v1/orders/{oid}/complete", headers=_h(driver["token"]))
    assert complete.status_code == 200, complete.text
    return oid, passenger_token, driver


def _open(client, oid: str, passenger_token: str, **overrides) -> dict[str, Any]:
    body = {"category": "FARE", "summary": "meter looked wrong", **overrides}
    r = client.post(
        f"/api/v1/orders/{oid}/disputes",
        headers=_h(passenger_token),
        json=body,
    )
    assert r.status_code == 201, r.text
    return r.json()


class TestPassengerDisputeOpening:
    def test_a_passenger_can_open_a_party_report_on_a_completed_order(self, client):
        oid, passenger_token, _driver_row = _complete_order(client, 101, 102)
        dispute = _open(client, oid, passenger_token)

        assert dispute["status"] == "OPEN"
        assert dispute["source"] == "PARTY_REPORT"
        assert dispute["category"] == "FARE"
        assert dispute["severity"] == "NORMAL"
        assert dispute["raised_by_kind"] == "PASSENGER"
        assert dispute["against_kind"] == "DRIVER"
        assert dispute["safety_flag"] is False

    def test_safety_category_is_flagged_without_party_chosen_sla(self, client):
        oid, passenger_token, _driver_row = _complete_order(client, 111, 112)
        dispute = _open(client, oid, passenger_token, category="SAFETY")

        assert dispute["severity"] == "HIGH"
        assert dispute["safety_flag"] is True

    def test_a_non_party_user_cannot_open_against_the_order(self, client):
        oid, _passenger_token, _driver_row = _complete_order(client, 121, 122)
        stranger = _passenger(client, 129)

        r = client.post(
            f"/api/v1/orders/{oid}/disputes",
            headers=_h(stranger),
            json={"category": "OTHER", "summary": "not my trip"},
        )
        assert r.status_code == 403

    def test_an_in_trip_order_is_refused(self, client):
        passenger_token = _passenger(client, 131)
        driver = _driver(client, 132)
        order = _create_order(client, passenger_token)
        oid = order["id"]

        assert (
            client.post(
                f"/api/v1/orders/{oid}/grab",
                headers=_h(driver["token"]),
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/v1/orders/{oid}/disputes",
                headers=_h(passenger_token),
                json={"category": "CONDUCT", "summary": "too early"},
            ).status_code
            == 409
        )

    def test_a_second_open_case_from_the_same_passenger_is_refused(self, client):
        oid, passenger_token, _driver_row = _complete_order(client, 141, 142)
        _open(client, oid, passenger_token)
        second = client.post(
            f"/api/v1/orders/{oid}/disputes",
            headers=_h(passenger_token),
            json={"category": "OTHER", "summary": "one more thing"},
        )
        assert second.status_code == 409
        assert second.json()["details"]["reason"] == "DISPUTE_ALREADY_OPEN"

    def test_admin_detail_exposes_arrival_evidence(self, client):
        oid, passenger_token, _driver_row = _complete_order(client, 161, 162)
        dispute = _open(client, oid, passenger_token)
        detail = client.get(
            f"/api/v1/admin/disputes/{dispute['id']}",
            headers=client.admin_headers(),
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["arrival_claimed_at"] is not None
        assert body["arrival_gps_distance_m"] is not None
        assert body["arrival_pin_attempts"] is not None

    def test_a_blank_summary_is_rejected(self, client):
        oid, passenger_token, _driver_row = _complete_order(client, 151, 152)
        r = client.post(
            f"/api/v1/orders/{oid}/disputes",
            headers=_h(passenger_token),
            json={"category": "OTHER", "summary": "   "},
        )
        assert r.status_code == 400
        assert r.json()["details"]["reason"] == "DISPUTE_SUMMARY_REQUIRED"
