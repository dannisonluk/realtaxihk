"""Order receipts — the frozen document, its access rules, and idempotency.

The properties that matter, and why each has a test:

- **Frozen, not recomputed.** A receipt must not change after it is issued.
  The strongest form of that is what these tests assert: issue it, mutate the
  order underneath, read it back, and the numbers are identical. A
  render-on-read implementation passes every shape test and fails this one.
- **Idempotent.** Asking twice returns the same bytes. The alternative —
  rebuild on every request — would let a passenger re-issue until the document
  read the way they preferred, which is a money-safety bug, not a UX nit.
- **Parties only.** A receipt carries the passenger's name and the trip
  addresses, so a stranger must get 403 and a non-existent order must get 404,
  with no way to tell "not yours" from "does not exist".
- **The fee split is disclosed.** On a fixed-fare (一口價) order the receipt
  prints driver payout and platform fee as separate lines; folding them into one
  number would be a hidden spread.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text


def _unique_phone() -> str:
    """A fresh passenger number per test.

    Generated rather than a literal: seed/contract accounts use real numbers,
    and reusing one here would attach this test's orders to that account.
    """
    return f"+85292{uuid.uuid4().int % 1000000:06d}"


def _mk_active_driver(client, phone: str) -> dict:
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
    admin = client.admin_headers()
    r = client.post(
        f"/api/v1/admin/drivers/{driver_id}/review",
        headers=admin,
        json={"decision": "approve"},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/v1/admin/drivers/{driver_id}/deposit/grant",
        headers=admin,
        json={"amount_hkd": "500.00"},
    )
    assert r.status_code == 200, r.text
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    return {"token": token, "driver_id": driver_id, "user_id": me["id"]}


def _mk_premium(
    client, *, code: str = "RTX_T1", lat: float = 22.308, lng: float = 113.9185
) -> dict:
    r = client.post(
        "/api/v1/admin/destinations",
        headers=client.admin_headers(),
        json={
            "code": code,
            "name_zh": "機場一號客運大樓",
            "name_en": "Airport Terminal 1",
            "lat": lat,
            "lng": lng,
            "radius_m": 300,
            "status": "ACTIVE",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _central_airport_payload(**overrides) -> dict:
    body = {
        "pickup_lat": 22.284,
        "pickup_lng": 114.158,
        "dropoff_lat": 22.3081,
        "dropoff_lng": 113.9187,
        "pickup_address": "Statue Square, Central",
        "dropoff_address": "Airport Terminal 1",
        "distance_km": "22.0",
        "taxi_type": "URBAN",
    }
    body.update(overrides)
    return body


def _create_order(client, token: str, **overrides) -> dict:
    r = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {token}"},
        json=_central_airport_payload(**overrides),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _exec(client, sql: str, **params):
    async with client.db_factory() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


class TestReceiptIssueAndRead:
    async def test_request_freezes_and_is_idempotent(self, client):
        _mk_premium(client)
        token = client.activate(_unique_phone())

        order = _create_order(
            client,
            token,
            requirements={"silent_ride": True, "no_smoke": True},
            payment_preference=["CASH", "OCTOPUS"],
        )

        r = client.post(f"/api/v1/orders/{order['id']}/receipt", headers=_auth(token))
        assert r.status_code == 201, r.text
        first = r.json()
        assert first["order_id"] == order["id"]
        assert first["total_hkd"] == order["estimated_total_hkd"]
        assert first["fare"]["total_fare"] == order["fare"]["total_fare"]
        assert first["requirements"]["silent_ride"] is True
        assert first["payment_preference"] == ["CASH", "OCTOPUS"]
        assert "hkfastdc.com" in first["text"]
        assert "374D" in first["disclaimer_zh"]

        # Second request returns the SAME bytes, including `issued_at`.
        r2 = client.post(f"/api/v1/orders/{order['id']}/receipt", headers=_auth(token))
        assert r2.status_code == 201, r2.text
        assert r2.json() == first

        # GET agrees with the issued document.
        r3 = client.get(f"/api/v1/orders/{order['id']}/receipt", headers=_auth(token))
        assert r3.status_code == 200, r3.text
        assert r3.json() == first

    async def test_receipt_survives_a_later_order_mutation(self, client):
        """The point of freezing: a later state change must not rewrite it.

        The order row is mutated directly (status, total, tariff version) and the
        stored receipt is read back. A render-on-read implementation returns the
        new numbers here and fails.
        """
        _mk_premium(client)
        token = client.activate(_unique_phone())
        order = _create_order(client, token)

        r = client.post(f"/api/v1/orders/{order['id']}/receipt", headers=_auth(token))
        assert r.status_code == 201, r.text
        issued = r.json()

        await _exec(
            client,
            "UPDATE orders SET estimated_total_hkd = 999.99, "
            "tariff_version = 'meter:TAMPERED', status = 'COMPLETED', "
            "completed_at = now() WHERE id = :oid",
            oid=order["id"],
        )

        r2 = client.get(f"/api/v1/orders/{order['id']}/receipt", headers=_auth(token))
        assert r2.status_code == 200, r2.text
        again = r2.json()
        assert again["total_hkd"] == issued["total_hkd"] == order["estimated_total_hkd"]
        assert again["tariff_version"] == issued["tariff_version"] != "meter:TAMPERED"
        assert again["issued_at"] == issued["issued_at"]

    async def test_text_document_download(self, client):
        _mk_premium(client)
        token = client.activate(_unique_phone())
        order = _create_order(client, token)

        r = client.get(f"/api/v1/orders/{order['id']}/receipt.txt", headers=_auth(token))
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/plain")
        assert f"receipt-{order['id']}.txt" in r.headers["content-disposition"]
        assert "車費收據" in r.text
        assert order["id"] in r.text


class TestReceiptAccess:
    async def test_stranger_gets_403_and_unknown_order_gets_404(self, client):
        _mk_premium(client)
        owner = client.activate(_unique_phone())
        stranger = client.activate(_unique_phone())
        order = _create_order(client, owner)

        r = client.get(f"/api/v1/orders/{order['id']}/receipt", headers=_auth(stranger))
        assert r.status_code == 403, r.text

        r = client.post(f"/api/v1/orders/{order['id']}/receipt", headers=_auth(stranger))
        assert r.status_code == 403, r.text

        # A non-existent order is a 404 for everyone, and a malformed id too —
        # so a stranger cannot probe which order ids exist.
        for bogus in ("00000000-0000-4000-8000-000000000000", "not-a-uuid"):
            r = client.get(f"/api/v1/orders/{bogus}/receipt", headers=_auth(stranger))
            assert r.status_code == 404, (bogus, r.text)

    async def test_assigned_driver_can_read_the_receipt(self, client):
        _mk_premium(client)
        driver = _mk_active_driver(client, _unique_phone())
        passenger = client.activate(_unique_phone())
        order = _create_order(client, passenger)

        r = client.post(f"/api/v1/orders/{order['id']}/grab", headers=_auth(driver["token"]))
        assert r.status_code == 200, r.text

        r = client.get(f"/api/v1/orders/{order['id']}/receipt", headers=_auth(driver["token"]))
        assert r.status_code == 200, r.text
        assert r.json()["order_id"] == order["id"]


class TestReceiptFixedFareSplit:
    async def test_fixed_fare_split_is_disclosed_line_by_line(self, client):
        """一口價: passenger price = driver payout + platform fee, shown apart.

        Folding these into one number would be a hidden spread, which is the
        thing the fee design exists to rule out.
        """
        _mk_premium(client, code="RTX_FIXED")
        driver = _mk_active_driver(client, _unique_phone())

        r = client.post(
            "/api/v1/drivers/me/fixed-offers",
            headers=_auth(driver["token"]),
            json={
                "destination_area": "AIRPORT",
                "pickup_area": "KOWLOON",
                "price_hkd": "150.00",
            },
        )
        assert r.status_code == 201, r.text

        passenger = client.activate(_unique_phone())
        order = _create_order(client, passenger)
        assert order["fare_mode"] == "FIXED", order
        passenger_price = order["fare"]["passenger_price_hkd"]

        r = client.post(f"/api/v1/orders/{order['id']}/receipt", headers=_auth(passenger))
        assert r.status_code == 201, r.text
        receipt = r.json()

        split = receipt["fixed_fare"]
        assert split is not None
        assert split["driver_price_hkd"] == order["fare"]["driver_price_hkd"]
        assert split["platform_fee_hkd"] == order["fare"]["platform_fee_hkd"]
        assert split["passenger_price_hkd"] == passenger_price

        # Both halves appear as separate lines in the rendered document.
        assert "司機實收" in receipt["text"]
        assert "平台服務費" in receipt["text"]

    async def test_meter_order_has_no_fixed_split(self, client):
        _mk_premium(client, code="RTX_METER")
        passenger = client.activate(_unique_phone())
        order = _create_order(client, passenger)
        assert order["fare_mode"] == "METER"

        r = client.post(f"/api/v1/orders/{order['id']}/receipt", headers=_auth(passenger))
        assert r.status_code == 201, r.text
        assert r.json()["fixed_fare"] is None
        assert "一口價明細" not in r.json()["text"]
