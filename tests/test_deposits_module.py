"""TDD — Module C (mini): deposit grant via admin, append-only ledger."""

import pytest


def _mk_user_token(client, phone: str) -> str:
    client.post("/api/v1/auth/otp/request", json={"phone_e164": phone})
    # SEC-02: the code is never in the response; read it at the notify seam.
    r = client.post(
        "/api/v1/auth/otp/verify", json={"phone_e164": phone, "code": client.otp_inbox[phone]}
    )
    return r.json()["access_token"]


def _admin_token() -> str:
    from conftest import ADMIN_ID

    from app.core.security import create_access_token

    return create_access_token({"sub": ADMIN_ID, "role": "ADMIN"})


@pytest.fixture()
def pending_driver(client):
    """Registered + KYC-approved driver sitting at DEPOSIT_REQUIRED."""
    token = _mk_user_token(client, "+85291400001")
    r = client.post(
        "/api/v1/drivers/register",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "hk_id_last4": "9012",
            "taxi_driver_plate_no": "TD90001",
            "vehicle_reg_mark": "EF9012",
            "taxi_type": "URBAN",
        },
    )
    driver_id = r.json()["id"]
    client.post(
        f"/api/v1/admin/drivers/{driver_id}/review",
        headers={"Authorization": f"Bearer {_admin_token()}"},
        json={"decision": "approve"},
    )
    return {"token": token, "driver_id": driver_id}


class TestDepositGrant:
    def test_grant_full_deposit_activates_driver(self, client, pending_driver):
        r = client.post(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}/deposit/grant",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"amount_hkd": "500.00", "note": "cash topup"},
        )
        assert r.status_code == 200
        assert r.json()["balance_hkd"] == "500.0"
        assert r.json()["is_fulfilled"] is True
        assert r.json()["driver_status"] == "ACTIVE"

    def test_partial_grant_stays_deposit_required(self, client, pending_driver):
        r = client.post(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}/deposit/grant",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"amount_hkd": "300.00"},
        )
        assert r.status_code == 200
        assert r.json()["balance_hkd"] == "300.0"
        assert r.json()["is_fulfilled"] is False
        assert r.json()["driver_status"] == "DEPOSIT_REQUIRED"

        # second grant crosses threshold -> ACTIVE
        r = client.post(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}/deposit/grant",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"amount_hkd": "200.00"},
        )
        assert r.json()["balance_hkd"] == "500.0"
        assert r.json()["driver_status"] == "ACTIVE"

    def test_grant_negative_rejected(self, client, pending_driver):
        r = client.post(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}/deposit/grant",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"amount_hkd": "-50.00"},
        )
        assert r.status_code == 422

    def test_non_admin_cannot_grant(self, client, pending_driver):
        r = client.post(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}/deposit/grant",
            headers={"Authorization": f"Bearer {pending_driver['token']}"},
            json={"amount_hkd": "500.00"},
        )
        assert r.status_code == 403


class TestLedger:
    def test_grant_writes_append_only_entries(self, client, pending_driver):
        client.post(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}/deposit/grant",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"amount_hkd": "500.00", "note": "cash topup"},
        )
        r = client.get(
            "/api/v1/drivers/me/ledger",
            headers={"Authorization": f"Bearer {pending_driver['token']}"},
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        entry = items[0]
        assert entry["entry_type"] == "DEPOSIT_TOPUP"
        assert entry["amount_hkd"] == "500.0"
        assert entry["balance_after_hkd"] == "500.0"

    def test_ledger_requires_driver_profile(self, client):
        token = _mk_user_token(client, "+85291400002")
        r = client.get("/api/v1/drivers/me/ledger", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404

    def test_balance_after_chains_multiple_entries(self, client, pending_driver):
        admin = {"Authorization": f"Bearer {_admin_token()}"}
        did = pending_driver["driver_id"]
        client.post(
            f"/api/v1/admin/drivers/{did}/deposit/grant",
            headers=admin,
            json={"amount_hkd": "300.00"},
        )
        client.post(
            f"/api/v1/admin/drivers/{did}/deposit/grant",
            headers=admin,
            json={"amount_hkd": "250.00"},
        )
        r = client.get(
            "/api/v1/drivers/me/ledger",
            headers={"Authorization": f"Bearer {pending_driver['token']}"},
        )
        balances = [i["balance_after_hkd"] for i in r.json()["items"]]
        assert balances == ["300.0", "550.0"]


class TestAdminDriverDetail:
    """`GET /admin/drivers/{id}` — the console's driver detail page.

    The admin console renders this page as one screen, so the endpoint must
    compose everything That page shows. The failure this guards against is a
    partial response: the page has no way to say "deposit missing because the
    query failed" verses "there is no deposit account", and would silently render
    a $0 balance as a funded-looking driver.
    """

    def test_detail_carries_every_section_the_page_renders(self, client, pending_driver):
        admin = {"Authorization": f"Bearer {_admin_token()}"}
        did = pending_driver["driver_id"]
        client.post(
            f"/api/v1/admin/drivers/{did}/deposit/grant",
            headers=admin,
            json={"amount_hkd": "500.00", "note": "cash topup"},
        )

        r = client.get(f"/api/v1/admin/drivers/{did}", headers=admin)
        assert r.status_code == 200
        body = r.json()

        assert body["id"] == did
        assert body["status"] == "ACTIVE"
        assert body["vehicle_reg_mark"] == "EF9012"
        assert body["hk_id_last4"] == "9012"

        # Every key the view reads must exist, even when empty.
        assert set(body) >= {"deposit", "ledger", "refunds", "fleet"}
        assert body["ledger"]["items"][0]["entry_type"] == "DEPOSIT_TOPUP"
        assert body["deposit"]["balance_hkd"] == "500.0"
        assert body["deposit"]["is_fulfilled"] is True
        assert body["refunds"]["items"] == []
        assert body["fleet"] is None

    def test_missing_deposit_row_still_reports_the_threshold(self, client, pending_driver):
        """A driver who has never been credited has no deposit row at all.

        The page shows progress toward a target, so the target has to come back
        regardless: an absent row is not the same as a zero requirement.
        """
        admin = {"Authorization": f"Bearer {_admin_token()}"}
        r = client.get(f"/api/v1/admin/drivers/{pending_driver['driver_id']}", headers=admin)
        assert r.status_code == 200
        deposit = r.json()["deposit"]
        assert deposit["has_account"] is False
        assert deposit["balance_hkd"] == "0.0"
        assert deposit["required_hkd"] == "500.0"
        assert deposit["is_fulfilled"] is False
        assert deposit["shortfall_hkd"] == "500.0"

    def test_shortfall_closes_as_the_balance_grows(self, client, pending_driver):
        admin = {"Authorization": f"Bearer {_admin_token()}"}
        did = pending_driver["driver_id"]
        client.post(
            f"/api/v1/admin/drivers/{did}/deposit/grant",
            headers=admin,
            json={"amount_hkd": "300.00"},
        )
        r = client.get(f"/api/v1/admin/drivers/{did}", headers=admin)
        assert r.json()["deposit"]["shortfall_hkd"] == "200.0"

    def test_unknown_driver_is_404_not_empty(self, client):
        admin = {"Authorization": f"Bearer {_admin_token()}"}
        r = client.get(
            "/api/v1/admin/drivers/00000000-0000-0000-0000-000000000000",
            headers=admin,
        )
        assert r.status_code == 404

    def test_non_admin_cannot_read_a_driver(self, client, pending_driver):
        """A driver must not be able to enumerate the register.

        Read access is the whole risk here: the response carries another
        person's identity fragment, statement and fleet.
        """
        r = client.get(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}",
            headers={"Authorization": f"Bearer {pending_driver['token']}"},
        )
        assert r.status_code == 403
