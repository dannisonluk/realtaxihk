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
        assert r.json()["balance_hkd"] == "500.00"
        assert r.json()["is_fulfilled"] is True
        assert r.json()["driver_status"] == "ACTIVE"

    def test_partial_grant_stays_deposit_required(self, client, pending_driver):
        r = client.post(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}/deposit/grant",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"amount_hkd": "300.00"},
        )
        assert r.status_code == 200
        assert r.json()["balance_hkd"] == "300.00"
        assert r.json()["is_fulfilled"] is False
        assert r.json()["driver_status"] == "DEPOSIT_REQUIRED"

        # second grant crosses threshold -> ACTIVE
        r = client.post(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}/deposit/grant",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"amount_hkd": "200.00"},
        )
        assert r.json()["balance_hkd"] == "500.00"
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
        assert entry["amount_hkd"] == "500.00"
        assert entry["balance_after_hkd"] == "500.00"

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
        assert balances == ["300.00", "550.00"]


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
        assert body["deposit"]["balance_hkd"] == "500.00"
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
        assert deposit["balance_hkd"] == "0.00"
        assert deposit["required_hkd"] == "500.00"
        assert deposit["is_fulfilled"] is False
        assert deposit["shortfall_hkd"] == "500.00"
        # `held_hkd` on this branch is a fallback, not a column read. It used to
        # be the literal "0.0", which put the page's only 1-dp money value next
        # to four 2-dp ones.
        assert deposit["held_hkd"] == "0.00"

    def test_shortfall_closes_as_the_balance_grows(self, client, pending_driver):
        admin = {"Authorization": f"Bearer {_admin_token()}"}
        did = pending_driver["driver_id"]
        client.post(
            f"/api/v1/admin/drivers/{did}/deposit/grant",
            headers=admin,
            json={"amount_hkd": "300.00"},
        )
        r = client.get(f"/api/v1/admin/drivers/{did}", headers=admin)
        assert r.json()["deposit"]["shortfall_hkd"] == "200.00"

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


class TestMoneyWirePrecision:
    """The wire contract for stored money is 2 dp, matching `Numeric(10, 2)`.

    Regression guard for a defect that was invisible to every test in this
    suite: the money columns hold cents exactly, but the API serialised them
    through a 1-dp quantiser, so `0.05` left as `"0.1"` and `0.01` left as
    `"0.0"`. The console renders `Math.abs(v).toFixed(2)`, so a cent balance was
    displayed as `HK$0.10` or, worse, as `HK$0.00` — a real credit shown as
    nothing. Every pre-existing assertion used whole-dollar amounts, where 1 dp
    and 2 dp agree, which is exactly why the suite stayed green.

    Two different precisions are legitimate and must not be conflated:
    stored money (`money_str`, 2 dp) and the meter tick (`meter_str`, 1 dp).
    """

    def test_sub_dollar_grant_keeps_its_cents(self, client, pending_driver):
        admin = {"Authorization": f"Bearer {_admin_token()}"}
        did = pending_driver["driver_id"]

        for amount, expected in (("0.05", "0.05"), ("0.01", "0.06")):
            r = client.post(
                f"/api/v1/admin/drivers/{did}/deposit/grant",
                headers=admin,
                json={"amount_hkd": amount},
            )
            assert r.status_code == 200, r.text
            assert r.json()["balance_hkd"] == expected

        me = client.get(
            "/api/v1/drivers/me",
            headers={"Authorization": f"Bearer {pending_driver['token']}"},
        )
        assert me.json()["deposit"]["balance_hkd"] == "0.06"

    def test_ledger_entries_keep_their_cents(self, client, pending_driver):
        admin = {"Authorization": f"Bearer {_admin_token()}"}
        client.post(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}/deposit/grant",
            headers=admin,
            json={"amount_hkd": "0.25"},
        )
        r = client.get(
            "/api/v1/drivers/me/ledger",
            headers={"Authorization": f"Bearer {pending_driver['token']}"},
        )
        entry = r.json()["items"][0]
        assert entry["amount_hkd"] == "0.25"
        assert entry["balance_after_hkd"] == "0.25"

    def test_percent_of_a_dollar_is_not_rounded_away(self, client, pending_driver):
        """The specific value the old format turned into a false `HK$0.00`."""
        r = client.post(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}/deposit/grant",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"amount_hkd": "0.04"},
        )
        # 1 dp would yield "0.0" — indistinguishable from an empty account.
        assert r.json()["balance_hkd"] == "0.04"

    def test_whole_dollars_carry_two_decimals(self, client, pending_driver):
        r = client.post(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}/deposit/grant",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"amount_hkd": "500.00"},
        )
        assert r.json()["balance_hkd"] == "500.00"

        # `required_hkd` is not on the grant response — it comes from the
        # driver's own view, where 1 dp used to render the threshold as "500.0".
        me = client.get(
            "/api/v1/drivers/me",
            headers={"Authorization": f"Bearer {pending_driver['token']}"},
        )
        assert me.json()["deposit"]["required_hkd"] == "500.00"
        assert me.json()["deposit"]["is_fulfilled"] is True


class TestDepositAdjustment:
    """The manual `ADJUSTMENT` correction — the one ledger type with no upstream
    event behind it, so its audit trail is the reason string itself."""

    def _adjust(self, client, driver_id, **body):
        body.setdefault("reason", "reconciliation correction")
        return client.post(
            f"/api/v1/admin/drivers/{driver_id}/deposit/adjust",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json=body,
        )

    def test_negative_adjustment_debits_the_balance(self, client, pending_driver):
        did = pending_driver["driver_id"]
        self._adjust(client, did, amount_hkd="500.00", reason="seed")
        r = self._adjust(client, did, amount_hkd="-120.50", reason="over-charged fares")
        assert r.status_code == 200, r.text
        assert r.json()["amount_hkd"] == "-120.50"
        assert r.json()["balance_hkd"] == "379.50"

    def test_positive_adjustment_credits_the_balance(self, client, pending_driver):
        did = pending_driver["driver_id"]
        self._adjust(client, did, amount_hkd="500.00", reason="seed")
        r = self._adjust(client, did, amount_hkd="25.25", reason="goodwill credit")
        assert r.status_code == 200, r.text
        assert r.json()["balance_hkd"] == "525.25"

    def test_arrears_are_allowed(self, client, pending_driver):
        """A correction may legitimately push a driver negative — the same rule
        the penalty and settlement flows already rely on."""
        did = pending_driver["driver_id"]
        r = self._adjust(client, did, amount_hkd="-75.00", reason="unpaid fees from 2026-W40")
        assert r.status_code == 200, r.text
        assert r.json()["balance_hkd"] == "-75.00"
        assert r.json()["is_fulfilled"] is False

    def test_writes_an_adjustment_ledger_entry(self, client, pending_driver):
        did = pending_driver["driver_id"]
        self._adjust(client, did, amount_hkd="-9.99", reason="over-charged fare")
        items = client.get(
            "/api/v1/drivers/me/ledger",
            headers={"Authorization": f"Bearer {pending_driver['token']}"},
        ).json()["items"]
        assert [e["entry_type"] for e in items] == ["ADJUSTMENT"]
        assert items[0]["amount_hkd"] == "-9.99"
        assert items[0]["balance_after_hkd"] == "-9.99"

    def test_reference_is_namespaced_and_carries_the_reason(self, client, pending_driver):
        """SEC-13: an adjustment gets its own `adj:` prefix, so it can never be
        crafted to collide with a grant/settlement/refund reference."""
        r = self._adjust(
            client, pending_driver["driver_id"], amount_hkd="-1.00", reason="Over Charged Fare!"
        )
        ref = r.json()["reference"]
        assert ref.startswith("adj:")
        assert "over-charged-fare" in ref

    def test_retry_with_same_client_key_replays(self, client, pending_driver):
        """P1-7 idempotency: a retried correction must not debit twice."""
        did = pending_driver["driver_id"]
        first = self._adjust(client, did, amount_hkd="-50.00", reason="dup", reference="k1")
        second = self._adjust(client, did, amount_hkd="-50.00", reason="dup", reference="k1")
        assert first.status_code == second.status_code == 200
        assert first.json()["reference"] == second.json()["reference"]
        assert second.json()["balance_hkd"] == "-50.00"

    def test_same_key_with_a_different_amount_is_refused(self, client, pending_driver):
        """SEC-13: a reference hit that disagrees on amount is tampering, not a
        replay — it must not silently no-op the second correction.

        The envelope is the project-wide `BusinessRuleError` -> 400. The
        diagnostic `details` matter as much as the status: they are what tells
        an operator their key collided with an unrelated entry.
        """
        did = pending_driver["driver_id"]
        self._adjust(client, did, amount_hkd="-50.00", reason="dup", reference="k2")
        r = self._adjust(client, did, amount_hkd="-60.00", reason="dup", reference="k2")
        assert r.status_code == 400
        assert r.json()["code"] == "BUSINESS_RULE_VIOLATION"
        details = r.json()["details"]
        assert details["existing_amount_hkd"] == "-50.00"
        assert details["requested_amount_hkd"] == "-60.00"

    def test_zero_amount_is_rejected(self, client, pending_driver):
        """A no-op correction is refused by `LedgerService.append`, which owns
        that rule (`amount == 0` is not a ledger event) — hence the 400."""
        r = self._adjust(client, pending_driver["driver_id"], amount_hkd="0.00")
        assert r.status_code == 400
        assert r.json()["code"] == "BUSINESS_RULE_VIOLATION"

    def test_amount_beyond_the_bound_is_rejected(self, client, pending_driver):
        """±5000 is the correction envelope; a larger move is a reconciliation
        decision, not a number typed into a form."""
        did = pending_driver["driver_id"]
        assert self._adjust(client, did, amount_hkd="5000.01").status_code == 422
        assert self._adjust(client, did, amount_hkd="-5000.01").status_code == 422
        assert self._adjust(client, did, amount_hkd="5000.00").status_code == 200

    def test_reason_is_required(self, client, pending_driver):
        """An unexplained balance change is worse than no tool at all."""
        r = client.post(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}/deposit/adjust",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"amount_hkd": "-10.00"},
        )
        assert r.status_code == 422

    def test_non_admin_cannot_adjust(self, client, pending_driver):
        r = client.post(
            f"/api/v1/admin/drivers/{pending_driver['driver_id']}/deposit/adjust",
            headers={"Authorization": f"Bearer {pending_driver['token']}"},
            json={"amount_hkd": "-10.00", "reason": "not allowed"},
        )
        assert r.status_code == 403

    def test_unknown_driver_is_404(self, client):
        r = self._adjust(client, "00000000-0000-0000-0000-000000000000", amount_hkd="-1.00")
        assert r.status_code == 404

    def test_attribution_lands_on_the_admin_detail_view(self, client, pending_driver):
        """An adjustment is discretionary, so *who* moved the balance is part of
        the record. It is exposed to admins and withheld from the driver's own
        ledger (an operator id is internal)."""
        from conftest import ADMIN_ID

        did = pending_driver["driver_id"]
        self._adjust(client, did, amount_hkd="-30.00", reason="over-charged fare")

        detail = client.get(
            f"/api/v1/admin/drivers/{did}",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        ).json()
        entry = detail["ledger"]["items"][0]
        assert entry["entry_type"] == "ADJUSTMENT"
        assert entry["created_by"] == ADMIN_ID
        assert entry["note"] == "over-charged fare"

        driver_view = client.get(
            "/api/v1/drivers/me/ledger",
            headers={"Authorization": f"Bearer {pending_driver['token']}"},
        ).json()["items"][0]
        assert "created_by" not in driver_view

    def test_adjustment_does_not_activate_a_pending_driver(self, client, pending_driver):
        """Unlike a grant, a correction must never satisfy the deposit threshold:
        adjustment is bookkeeping, not a payment, and crossing it here would let
        an operator approve a driver by moving numbers."""
        r = self._adjust(
            client, pending_driver["driver_id"], amount_hkd="500.00", reason="seed", reference="k3"
        )
        # ensure_deposit_row already set is_fulfilled from the balance, so this
        # documents the pre-existing behaviour rather than claiming new safety:
        assert r.json()["driver_status"] == "DEPOSIT_REQUIRED"
        me = client.get(
            "/api/v1/drivers/me",
            headers={"Authorization": f"Bearer {pending_driver['token']}"},
        ).json()
        assert me["status"] == "DEPOSIT_REQUIRED"
        assert me["deposit"]["is_fulfilled"] is True
