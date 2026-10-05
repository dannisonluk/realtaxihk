"""Premium destinations and driver attribute APIs.

These are Phase-1 additions; they are deliberately additive and do not change
the existing order lifecycle. The tests cover the two invariants that matter
most for the console surface:

1. Admin can create, list and hide a destination; the public map list only
   ever sees ACTIVE rows.
2. A driver can declare payment methods and in-car environment flags, and the
   declaration round-trips through the wire shape the mobile app decodes.
"""

from __future__ import annotations

import uuid


def _make_driver(client, *, status: str = "ACTIVE") -> tuple[str, str]:
    """A minimal `driver_profiles` row, returning `(profile_id, phone)`.

    The phone is returned so the caller can authenticate through the same real
    OTP/sign-in helpers every other driver route uses.
    """
    profile_id = uuid.uuid4()
    user_id = uuid.uuid4()
    phone = f"+8529{user_id.int % 10**7:07d}"
    client.exec_sql(
        "INSERT INTO users (id, phone_e164, display_name, role, is_active, "
        "account_status, created_at, updated_at) "
        "VALUES (CAST(:i AS uuid), :p, :n, 'DRIVER', true, 'ACTIVE', now(), now())",
        {
            "i": str(user_id),
            "p": phone,
            "n": f"d{user_id.int % 10**8:08d}",
        },
    )
    client.exec_sql(
        "INSERT INTO driver_profiles (id, user_id, status, taxi_type, "
        "taxi_driver_plate_no, vehicle_reg_mark, hk_id_last4, is_online, "
        "created_at, updated_at) "
        "VALUES (CAST(:i AS uuid), CAST(:u AS uuid), :s, "
        "'URBAN', :plate, :reg, '1234', false, now(), now())",
        {
            "i": str(profile_id),
            "u": str(user_id),
            "s": status,
            "plate": f"D{profile_id.int % 10**5:05d}",
            "reg": f"AA{profile_id.int % 10**4:04d}",
        },
    )
    return str(profile_id), phone


_DEST = {
    "code": "HKG_T1",
    "name_zh": "機場一號客運大樓",
    "name_en": "Airport Terminal 1",
    "lat": 22.308,
    "lng": 113.9185,
    "radius_m": 300,
    "status": "ACTIVE",
}


class TestPremiumDestinations:
    async def test_admin_create_and_public_list_only_active(self, client):
        headers = client.admin_headers()
        r = client.post("/api/v1/admin/destinations", json=_DEST, headers=headers)
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["code"] == "HKG_T1"
        assert created["status"] == "ACTIVE"

        r = client.get("/api/v1/destinations")
        assert r.status_code == 200, r.text
        assert [d["code"] for d in r.json()["items"]] == ["HKG_T1"]

        r = client.patch(
            f"/api/v1/admin/destinations/{created['id']}",
            json={"status": "HIDDEN"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "HIDDEN"

        r = client.get("/api/v1/destinations")
        assert r.status_code == 200, r.text
        assert r.json()["items"] == []

    async def test_admin_duplicate_code_is_409(self, client):
        headers = client.admin_headers()
        r = client.post("/api/v1/admin/destinations", json=_DEST, headers=headers)
        assert r.status_code == 201, r.text
        r = client.post("/api/v1/admin/destinations", json=_DEST, headers=headers)
        assert r.status_code == 409, r.text

    async def test_non_admin_cannot_write_destinations(self, client):
        r = client.post("/api/v1/admin/destinations", json=_DEST)
        assert r.status_code == 401, r.text

    async def test_driver_can_round_trip_payment_methods_and_environment(self, client):
        _profile_id, phone = _make_driver(client)
        token = client.activate(phone)
        headers = {"Authorization": f"Bearer {token}"}

        r = client.put(
            "/api/v1/drivers/me/payment-methods",
            json={"methods": ["CASH", "OCTOPUS", "ALIPAY"]},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["methods"] == ["CASH", "OCTOPUS", "ALIPAY"]

        r = client.get("/api/v1/drivers/me/payment-methods", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["methods"] == ["CASH", "OCTOPUS", "ALIPAY"]

        r = client.put(
            "/api/v1/drivers/me/environment",
            json={
                "silent_ride": True,
                "no_radio_music": True,
                "no_smoke": True,
                "no_perfume": False,
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["no_smoke"] is True

        r = client.get("/api/v1/drivers/me/environment", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["silent_ride"] is True
        assert body["no_radio_music"] is True
        assert body["no_smoke"] is True
        assert body["no_perfume"] is False

    async def test_payment_methods_reject_unknown_value(self, client):
        _profile_id, phone = _make_driver(client)
        token = client.activate(phone)
        r = client.put(
            "/api/v1/drivers/me/payment-methods",
            json={"methods": ["BITCOIN"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422, r.text
