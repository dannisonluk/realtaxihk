"""Phase-1 nearby filters — fare mode, destination area, premium destination, requirements.

The whole point of these tests is the difference between an *answered* query and
a *refused* one. A filter that cannot match any row must be a 422, because an
empty page is a claim ("nothing is near you") that a driver has to be able to
trust; a typo must never be able to manufacture it.
"""

from __future__ import annotations

import uuid

_CENTRAL = {"lat": 22.284, "lng": 114.158}


def _passenger(client, n: int) -> str:
    return client.activate(f"+852{916 * 10**5 + n}")


def _driver(client, n: int) -> dict:
    token = _passenger(client, n)
    r = client.post(
        "/api/v1/drivers/register",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "hk_id_last4": "0000",
            "taxi_driver_plate_no": f"TD{n}",
            "vehicle_reg_mark": f"ZZ{n}",
            "taxi_type": "URBAN",
        },
    )
    assert r.status_code == 201, r.text
    did = r.json()["id"]
    admin = client.admin_headers()
    assert (
        client.post(
            f"/api/v1/admin/drivers/{did}/review", headers=admin, json={"decision": "approve"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/admin/drivers/{did}/deposit/grant",
            headers=admin,
            json={"amount_hkd": "500.00"},
        ).status_code
        == 200
    )
    return {"token": token, "driver_id": did}


def _go_online(client, driver: dict, at: dict | None = None) -> dict:
    """Park the driver at Central and return the auth headers."""
    h = {"Authorization": f"Bearer {driver['token']}"}
    ok = client.post(
        "/api/v1/drivers/location", headers=h, json={**(at or _CENTRAL), "online": True}
    )
    assert ok.status_code == 200, ok.text
    return h


def _order(
    client,
    token: str,
    *,
    dropoff: tuple[float, float] = (22.315, 114.219),  # North Point -> KOWLOON
    address: str = "Harbour North, North Point",
    distance_km: str = "4.2",
    requirements: dict | None = None,
) -> dict:
    payload = {
        "pickup_lat": _CENTRAL["lat"],
        "pickup_lng": _CENTRAL["lng"],
        "pickup_address": "Statue Square, Central",
        "dropoff_lat": dropoff[0],
        "dropoff_lng": dropoff[1],
        "dropoff_address": address,
        "distance_km": distance_km,
        "taxi_type": "URBAN",
    }
    if requirements is not None:
        payload["requirements"] = requirements
    r = client.post("/api/v1/orders", headers={"Authorization": f"Bearer {token}"}, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _premium(client, *, code: str = "HKG_T1") -> dict:
    r = client.post(
        "/api/v1/admin/destinations",
        headers=client.admin_headers(),
        json={
            "code": code,
            "name_zh": "機場一號客運大樓",
            "name_en": "Airport Terminal 1",
            "lat": 22.308,
            "lng": 113.9185,
            "radius_m": 300,
            "status": "ACTIVE",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _airport_order(client, token: str) -> dict:
    """A Central -> Airport order that the fixed-fare matcher will bind."""
    return _order(
        client,
        token,
        dropoff=(22.3081, 113.9187),
        address="Airport Terminal 1",
        distance_km="22.0",
    )


def _nearby(client, headers: dict, **params) -> tuple[int, dict]:
    r = client.get(
        "/api/v1/orders/nearby",
        headers=headers,
        params={**_CENTRAL, "radius_km": 3, **params},
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return r.status_code, body


def _ids(body: dict) -> set[str]:
    return {o["id"] for o in body["items"]}


class TestNearbyWithoutFilters:
    def test_no_filters_is_unchanged(self, client):
        """Regression guard: the pre-filter behaviour must survive the change."""
        d = _driver(client, 40001)
        h = _go_online(client, d)
        pax = _passenger(client, 40002)
        oid = _order(client, pax)["id"]
        status, body = _nearby(client, h)
        assert status == 200, body
        assert oid in _ids(body)

    def test_filtered_query_does_not_widen_the_answer(self, client):
        """A filter narrows. It never introduces an order that was not nearby."""
        d = _driver(client, 40003)
        h = _go_online(client, d)
        pax = _passenger(client, 40004)
        _order(client, pax, requirements={"no_smoke": True})
        # Far away: same passenger, pickup in Tung Chung, so it is not in the
        # 3 km window even though the filter would have matched it.
        far = {
            "pickup_lat": 22.308,
            "pickup_lng": 113.918,
            "pickup_address": "Tung Chung",
            "dropoff_lat": 22.315,
            "dropoff_lng": 114.219,
            "dropoff_address": "North Point",
            "distance_km": "25.0",
            "taxi_type": "URBAN",
            "requirements": {"no_smoke": True},
        }
        r = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {pax}"},
            json=far,
        )
        assert r.status_code == 201, r.text
        status, body = _nearby(client, h, requires="no_smoke")
        assert status == 200, body
        assert len(body["items"]) == 1


class TestFareModeFilter:
    def test_fare_mode_partitions_meter_from_fixed(self, client):
        d = _driver(client, 40005)
        h = _go_online(client, d)
        _premium(client)
        offer = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=h,
            json={"destination_area": "AIRPORT", "pickup_area": "KOWLOON", "price_hkd": "150.00"},
        )
        assert offer.status_code == 201, offer.text

        pax = _passenger(client, 40006)
        meter = _order(client, pax)
        assert meter["fare_mode"] == "METER"

        pax2 = _passenger(client, 40007)
        fixed = _airport_order(client, pax2)
        assert fixed["fare_mode"] == "FIXED", fixed

        status, body = _nearby(client, h)
        assert status == 200 and _ids(body) == {meter["id"], fixed["id"]}

        status, body = _nearby(client, h, fare_mode="METER")
        assert status == 200, body
        assert _ids(body) == {meter["id"]}

        status, body = _nearby(client, h, fare_mode="FIXED")
        assert status == 200, body
        assert _ids(body) == {fixed["id"]}

    def test_unknown_fare_mode_is_422(self, client):
        d = _driver(client, 40008)
        h = _go_online(client, d)
        status, body = _nearby(client, h, fare_mode="BARGAIN")
        assert status == 422, body


class TestDestinationAreaFilter:
    def test_area_filter_selects_the_dropoff_region(self, client):
        d = _driver(client, 40009)
        h = _go_online(client, d)
        _premium(client)
        offer = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=h,
            json={"destination_area": "AIRPORT", "pickup_area": "KOWLOON", "price_hkd": "150.00"},
        )
        assert offer.status_code == 201, offer.text
        pax = _passenger(client, 40010)
        kowloon = _order(client, pax)
        assert kowloon["destination_area"] == "KOWLOON", kowloon
        airport = _airport_order(client, _passenger(client, 40011))
        assert airport["destination_area"] == "AIRPORT", airport

        status, body = _nearby(client, h, destination_area="KOWLOON")
        assert status == 200, body
        assert kowloon["id"] in _ids(body)
        assert airport["id"] not in _ids(body)

        status, body = _nearby(client, h, destination_area="AIRPORT")
        assert status == 200, body
        assert _ids(body) == {airport["id"]}

    def test_area_filter_does_not_look_at_the_pickup(self, client):
        """`destination_area` is the dropoff's region — the same field the order carries.

        Both orders below are picked up at Central; only the Airport one is a
        destination-area match, which is what stops this filter from quietly
        behaving like "where did the driver start".
        """
        d = _driver(client, 40012)
        h = _go_online(client, d)
        _premium(client)
        offer = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=h,
            json={"destination_area": "AIRPORT", "pickup_area": "KOWLOON", "price_hkd": "150.00"},
        )
        assert offer.status_code == 201, offer.text
        _order(client, _passenger(client, 40013))
        airport = _airport_order(client, _passenger(client, 40014))
        status, body = _nearby(client, h, destination_area="AIRPORT")
        assert status == 200, body
        assert _ids(body) == {airport["id"]}

    def test_unknown_area_is_422_not_an_empty_page(self, client):
        d = _driver(client, 40015)
        h = _go_online(client, d)
        _order(client, _passenger(client, 40016))
        status, body = _nearby(client, h, destination_area="SHENZHEN")
        assert status == 422, body
        # The refusal is a refusal, not a page that happens to be empty.
        assert "items" not in body

    def test_every_shipped_area_code_is_accepted(self, client):
        """The filter's closed set and `destination_area()`'s outputs are one set."""
        from app.core.region import ALL_AREAS

        d = _driver(client, 40017)
        h = _go_online(client, d)
        for area in sorted(ALL_AREAS):
            status, body = _nearby(client, h, destination_area=area)
            assert status == 200, (area, body)
            assert "items" in body


class TestPremiumDestinationFilter:
    def test_premium_destination_id_selects_only_that_place(self, client):
        d = _driver(client, 40018)
        h = _go_online(client, d)
        dest = _premium(client)
        offer = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=h,
            json={"destination_area": "AIRPORT", "pickup_area": "KOWLOON", "price_hkd": "150.00"},
        )
        assert offer.status_code == 201, offer.text
        plain = _order(client, _passenger(client, 40019))
        airport = _airport_order(client, _passenger(client, 40020))
        assert airport["premium_destination"]["id"] == dest["id"]

        status, body = _nearby(client, h, premium_destination_id=dest["id"])
        assert status == 200, body
        assert _ids(body) == {airport["id"]}
        assert plain["id"] not in _ids(body)

    def test_unknown_premium_destination_is_an_empty_page_not_a_422(self, client):
        """A well-formed id that matches nothing is a legitimate empty answer.

        Unlike an unknown *area code* (a typo the server can detect), this is a
        real UUID that simply has no orders — the two must not be conflated.
        """
        d = _driver(client, 40021)
        h = _go_online(client, d)
        _order(client, _passenger(client, 40022))
        status, body = _nearby(client, h, premium_destination_id=str(uuid.uuid4()))
        assert status == 200, body
        assert body["items"] == []

    def test_malformed_premium_destination_id_is_422(self, client):
        d = _driver(client, 40023)
        h = _go_online(client, d)
        status, body = _nearby(client, h, premium_destination_id="not-a-uuid")
        assert status == 422, body


class TestRequirementFilter:
    def test_require_flag_selects_orders_that_asked_for_it(self, client):
        d = _driver(client, 40024)
        h = _go_online(client, d)
        pax = _passenger(client, 40025)
        wanted = _order(client, pax, requirements={"silent_ride": True})
        plain = _order(client, _passenger(client, 40026))
        status, body = _nearby(client, h, requires="silent_ride")
        assert status == 200, body
        assert _ids(body) == {wanted["id"]}
        assert plain["id"] not in _ids(body)

    def test_multiple_requirements_all_must_hold(self, client):
        """`requires` is an AND, not an OR — a driver picking two boxes wants both."""
        d = _driver(client, 40027)
        h = _go_online(client, d)
        both = _order(
            client,
            _passenger(client, 40028),
            requirements={"silent_ride": True, "no_perfume": True},
        )
        _order(client, _passenger(client, 40029), requirements={"silent_ride": True})
        _order(client, _passenger(client, 40030), requirements={"no_perfume": True})
        status, body = _nearby(client, h, requires="silent_ride,no_perfume")
        assert status == 200, body
        assert _ids(body) == {both["id"]}

    def test_exclude_flag_drops_orders_that_asked_for_it(self, client):
        """The inverse direction: "no pets in my car" is expressed as exclude_animal."""
        d = _driver(client, 40031)
        h = _go_online(client, d)
        pet = _order(
            client,
            _passenger(client, 40032),
            requirements={"animal": {"kind": "DOG", "height_cm": 35, "weight_kg": 8}},
        )
        plain = _order(client, _passenger(client, 40033))
        status, body = _nearby(client, h, excludes="animal")
        assert status == 200, body
        assert _ids(body) == {plain["id"]}
        assert pet["id"] not in _ids(body)

    def test_an_order_without_requirements_matches_only_the_neutral_query(self, client):
        """A missing `requirements` object is not "asked for nothing" by accident.

        Orders created before this field existed carry no key at all; they must
        still come back from an unfiltered query, but must never be silently
        claimed to have satisfied a stated preference.
        """
        d = _driver(client, 40034)
        h = _go_online(client, d)
        legacy = _order(client, _passenger(client, 40035))
        status, body = _nearby(client, h)
        assert status == 200 and legacy["id"] in _ids(body)
        for key in ("silent_ride", "no_smoke", "no_radio_music", "no_perfume"):
            status, body = _nearby(client, h, requires=key)
            assert status == 200, (key, body)
            assert legacy["id"] not in _ids(body), key

    def test_unknown_requirement_key_is_422(self, client):
        """An unsupported key would otherwise mean "match nothing" — silently."""
        d = _driver(client, 40036)
        h = _go_online(client, d)
        status, body = _nearby(client, h, requires="chauffeur_hat")
        assert status == 422, body

    def test_requirement_filter_combines_with_fare_mode(self, client):
        d = _driver(client, 40037)
        h = _go_online(client, d)
        _premium(client)
        offer = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=h,
            json={"destination_area": "AIRPORT", "pickup_area": "KOWLOON", "price_hkd": "150.00"},
        )
        assert offer.status_code == 201, offer.text
        quiet_fixed = _airport_with_requirements(
            client, _passenger(client, 40038), {"no_smoke": True}
        )
        _airport_with_requirements(client, _passenger(client, 40039), {"no_radio_music": True})
        _order(client, _passenger(client, 40040), requirements={"no_smoke": True})
        status, body = _nearby(client, h, fare_mode="FIXED", requires="no_smoke")
        assert status == 200, body
        assert _ids(body) == {quiet_fixed["id"]}


def _airport_with_requirements(client, token: str, requirements: dict) -> dict:
    return _order(
        client,
        token,
        dropoff=(22.3081, 113.9187),
        address="Airport Terminal 1",
        distance_km="22.0",
        requirements=requirements,
    )
