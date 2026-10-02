"""Taxi fleet suite: rosters, fleet settlement, and the double-charge boundary.

Fleets are the one place in the codebase where two independent billing jobs can
reach the same driver. `SettlementService.run_weekly` charges every ACTIVE
driver the flat platform fee; `FleetSettlementService` charges a fleet's members
the discounted fleet rate. They use *different* ledger references
(`weekly:…` vs `fleet:…`), so idempotency does not protect anyone across them —
running both over the same driver simply charges them twice.

That is the invariant most of this file exists to defend.
"""

from __future__ import annotations

from decimal import Decimal

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _mk_user_token(client, phone: str) -> str:
    """A fully verified, ACTIVE account's token.

    Was a bare OTP login, which no longer reaches any business route: P-2 gates
    on `AccountStatus.ACTIVE` (`require_verified_account`) and P-4 adds a phone
    deadline on top (`require_phone_current`). This test file is about fleet
    billing, not about those gates, so it clears them and moves on — the gates
    themselves are covered by `test_identity_api` and `test_phone_reverify`.
    """
    return client.activate(phone)


def _admin_headers(client) -> dict:
    return client.admin_headers()


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, token: str, phone: str) -> str:
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
    return r.json()["id"]


def _make_active(client, phone: str, deposit: str = "500.00") -> dict:
    """KYC-approved driver with a fulfilled deposit -> ACTIVE."""
    token = _mk_user_token(client, phone)
    driver_id = _register(client, token, phone)
    client.post(
        f"/api/v1/admin/drivers/{driver_id}/review",
        headers=_admin_headers(client),
        json={"decision": "approve"},
    )
    client.post(
        f"/api/v1/admin/drivers/{driver_id}/deposit/grant",
        headers=_admin_headers(client),
        json={"amount_hkd": deposit},
    )
    return {"token": token, "driver_id": driver_id, "phone": phone}


def _mk_fleet(client, name: str = "星群的士", discount: str = "0", license_no: str | None = None):
    r = client.post(
        "/api/v1/admin/fleets",
        headers=_admin_headers(client),
        json={
            "name": name,
            "license_no": license_no or f"FLEET-{name}",
            "weekly_fee_discount_percent": discount,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _add_member(client, fleet_id: str, driver_id: str, role: str = "MEMBER"):
    return client.post(
        f"/api/v1/admin/fleets/{fleet_id}/members",
        headers=_admin_headers(client),
        json={"driver_profile_id": driver_id, "member_role": role},
    )


def _ledger(client, token: str) -> list[dict]:
    r = client.get("/api/v1/drivers/me/ledger", headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _fee_entries(client, token: str) -> list[Decimal]:
    """Every weekly-fee deduction on this driver's ledger, as positive amounts."""
    return [
        -Decimal(entry["amount_hkd"])
        for entry in _ledger(client, token)
        if entry["entry_type"] == "WEEKLY_FEE_DEDUCTION"
    ]


def _run_fleet_settlement(client, fleet_id: str, period: str | None = None):
    url = f"/api/v1/admin/fleets/{fleet_id}/settlement/run"
    if period:
        url += f"?period={period}"
    return client.post(url, headers=_admin_headers(client))


def _run_platform_settlement(client, period: str | None = None):
    """Preview, then run — the platform run is gated on a confirmation token.

    The fleet run (`/fleets/{id}/settlement/run`) is not gated; only the
    platform-wide run charges every driver at once.
    """
    url = "/api/v1/admin/settlement/weekly/run"
    admin = _admin_headers(client)
    if period:
        preview = client.post(
            "/api/v1/admin/settlement/preview",
            headers=admin,
            json={"period": period},
        ).json()
        url += f"?period={period}&confirm_token={preview['confirm_token']}"
    else:
        preview = client.post("/api/v1/admin/settlement/preview", headers=admin, json={}).json()
        url += f"?confirm_token={preview['confirm_token']}"
    return client.post(url, headers=admin)


# --------------------------------------------------------------------------- #
# fleet management
# --------------------------------------------------------------------------- #


class TestFleetManagement:
    def test_admin_creates_a_fleet(self, client):
        fleet = _mk_fleet(client, "忠誠車隊", discount="15")
        assert fleet["status"] == "ACTIVE"
        assert fleet["weekly_fee_discount_percent"] == "15"
        assert fleet["member_count"] == 0
        assert fleet["license_no"] == "FLEET-忠誠車隊"

    def test_a_driver_cannot_create_a_fleet(self, client):
        """HK fleets are licensed operators — onboarding is not self-service."""
        token = _mk_user_token(client, "+85260000001")
        r = client.post(
            "/api/v1/admin/fleets",
            headers=_h(token),
            json={"name": "Rogue Fleet", "license_no": "X1"},
        )
        assert r.status_code == 403, r.text

    def test_duplicate_name_is_rejected(self, client):
        _mk_fleet(client, "八達通車隊", license_no="L-1")
        r = client.post(
            "/api/v1/admin/fleets",
            headers=_admin_headers(client),
            json={"name": "八達通車隊", "license_no": "L-2"},
        )
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "BUSINESS_RULE_VIOLATION"
        assert r.json()["details"]["field"] == "name"

    def test_duplicate_licence_is_rejected(self, client):
        _mk_fleet(client, "A 車隊", license_no="SAME")
        r = client.post(
            "/api/v1/admin/fleets",
            headers=_admin_headers(client),
            json={"name": "B 車隊", "license_no": "SAME"},
        )
        assert r.status_code == 400, r.text
        assert r.json()["details"]["field"] == "license_no"

    def test_discount_outside_zero_to_hundred_is_rejected(self, client):
        r = client.post(
            "/api/v1/admin/fleets",
            headers=_admin_headers(client),
            json={"name": "Too Generous", "license_no": "L-3", "weekly_fee_discount_percent": 120},
        )
        assert r.status_code == 422, r.text

    def test_listing_reports_member_counts(self, client):
        fleet = _mk_fleet(client, "大車隊")
        driver = _make_active(client, "+85260000002")
        _add_member(client, fleet["id"], driver["driver_id"])

        r = client.get("/api/v1/admin/fleets", headers=_admin_headers(client))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["member_count"] == 1

    def test_listing_counts_each_fleet_independently(self, client):
        """One grouped COUNT must not smear one fleet's roster onto another.

        The list page used to call `active_member_count` per row — an N+1 that
        made `limit=100` a 17.5s page on a cold pool. It was replaced by a single
        grouped query, and a grouped query is exactly the shape that goes wrong
        by returning one total for every row, so this pins the per-fleet split.
        """
        big = _mk_fleet(client, "一號車隊", license_no="FLEET-A")
        empty = _mk_fleet(client, "二號車隊", license_no="FLEET-B")
        solo = _mk_fleet(client, "三號車隊", license_no="FLEET-C")

        for phone in ("+85260000010", "+85260000011"):
            driver = _make_active(client, phone)
            assert _add_member(client, big["id"], driver["driver_id"]).status_code == 201

        driver = _make_active(client, "+85260000012")
        assert _add_member(client, solo["id"], driver["driver_id"]).status_code == 201

        r = client.get("/api/v1/admin/fleets", headers=_admin_headers(client))
        assert r.status_code == 200, r.text
        counts = {item["id"]: item["member_count"] for item in r.json()["items"]}

        assert counts[big["id"]] == 2
        assert counts[solo["id"]] == 1
        # A fleet with no active members is simply absent from a GROUP BY result,
        # so the endpoint must default it to 0 rather than crash on a missing key.
        assert counts[empty["id"]] == 0

    def test_updating_a_fleet_changes_the_discount(self, client):
        fleet = _mk_fleet(client, "調整車隊", discount="0")
        r = client.patch(
            f"/api/v1/admin/fleets/{fleet['id']}",
            headers=_admin_headers(client),
            json={"weekly_fee_discount_percent": "25", "status": "SUSPENDED"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["weekly_fee_discount_percent"] == "25"
        assert r.json()["status"] == "SUSPENDED"


class TestRoster:
    def test_adding_a_driver_puts_them_on_the_roster(self, client):
        fleet = _mk_fleet(client, "名單車隊")
        driver = _make_active(client, "+85260000003")
        r = _add_member(client, fleet["id"], driver["driver_id"], role="MANAGER")
        assert r.status_code == 201, r.text
        assert r.json()["member_role"] == "MANAGER"
        assert r.json()["status"] == "ACTIVE"

        roster = client.get(
            f"/api/v1/admin/fleets/{fleet['id']}/members", headers=_admin_headers(client)
        )
        assert roster.status_code == 200, roster.text
        assert len(roster.json()["items"]) == 1

    def test_a_driver_cannot_be_on_two_fleets(self, client):
        """Two rosters would mean two bills for the same week."""
        first = _mk_fleet(client, "第一車隊")
        second = _mk_fleet(client, "第二車隊")
        driver = _make_active(client, "+85260000004")
        assert _add_member(client, first["id"], driver["driver_id"]).status_code == 201

        r = _add_member(client, second["id"], driver["driver_id"])
        assert r.status_code == 400, r.text
        assert r.json()["details"]["current_fleet_id"] == first["id"]

    def test_adding_the_same_driver_twice_is_rejected(self, client):
        fleet = _mk_fleet(client, "重複車隊")
        driver = _make_active(client, "+85260000005")
        _add_member(client, fleet["id"], driver["driver_id"])
        r = _add_member(client, fleet["id"], driver["driver_id"])
        assert r.status_code == 400, r.text

    def test_unknown_driver_is_rejected(self, client):
        fleet = _mk_fleet(client, "空車隊")
        r = _add_member(client, fleet["id"], "00000000-0000-0000-0000-000000000000")
        assert r.status_code == 400, r.text

    def test_removing_keeps_the_row_for_audit(self, client):
        fleet = _mk_fleet(client, "離隊車隊")
        driver = _make_active(client, "+85260000006")
        _add_member(client, fleet["id"], driver["driver_id"])

        r = client.delete(
            f"/api/v1/admin/fleets/{fleet['id']}/members/{driver['driver_id']}",
            headers=_admin_headers(client),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "REMOVED"
        assert r.json()["left_at"] is not None

        active = client.get(
            f"/api/v1/admin/fleets/{fleet['id']}/members", headers=_admin_headers(client)
        ).json()
        assert active["items"] == []
        # The history is still there — an operator dispute needs it.
        full = client.get(
            f"/api/v1/admin/fleets/{fleet['id']}/members?include_left=true",
            headers=_admin_headers(client),
        ).json()
        assert len(full["items"]) == 1
        assert full["items"][0]["status"] == "REMOVED"

    def test_a_removed_driver_can_join_another_fleet(self, client):
        first = _mk_fleet(client, "舊車隊")
        second = _mk_fleet(client, "新車隊")
        driver = _make_active(client, "+85260000007")
        _add_member(client, first["id"], driver["driver_id"])
        client.delete(
            f"/api/v1/admin/fleets/{first['id']}/members/{driver['driver_id']}",
            headers=_admin_headers(client),
        )
        assert _add_member(client, second["id"], driver["driver_id"]).status_code == 201

    def test_roster_hides_identity_documents(self, client):
        """An operator needs the roster, not the KYC documents behind it."""
        fleet = _mk_fleet(client, "私隱車隊")
        driver = _make_active(client, "+85260000008")
        _add_member(client, fleet["id"], driver["driver_id"])
        row = client.get(
            f"/api/v1/admin/fleets/{fleet['id']}/members", headers=_admin_headers(client)
        ).json()["items"][0]
        assert "hk_id_last4" not in row
        assert "taxi_driver_plate_no" not in row
        assert "vehicle_reg_mark" not in row


class TestDriverFacing:
    def test_a_driver_with_no_fleet_gets_null(self, client):
        token = _mk_user_token(client, "+85260000009")
        _register(client, token, "+85260000009")
        r = client.get("/api/v1/fleets/me", headers=_h(token))
        assert r.status_code == 200, r.text
        assert r.json() == {"fleet": None, "membership": None}

    def test_a_member_sees_their_fleet(self, client):
        fleet = _mk_fleet(client, "自己車隊")
        driver = _make_active(client, "+85260000010")
        _add_member(client, fleet["id"], driver["driver_id"], role="OWNER")

        r = client.get("/api/v1/fleets/me", headers=_h(driver["token"]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["fleet"]["id"] == fleet["id"]
        assert body["membership"]["member_role"] == "OWNER"

    def test_a_driver_cannot_read_a_fleet_they_are_not_on(self, client):
        """404, not 403 — the endpoint must not confirm which fleets exist."""
        other = _mk_fleet(client, "別家車隊")
        driver = _make_active(client, "+85260000011")

        for path in (
            f"/api/v1/fleets/{other['id']}",
            f"/api/v1/fleets/{other['id']}/members",
            f"/api/v1/fleets/{other['id']}/settlement",
        ):
            r = client.get(path, headers=_h(driver["token"]))
            assert r.status_code == 404, f"{path} -> {r.status_code} {r.text}"

    def test_a_member_can_read_their_own_roster_and_settlement(self, client):
        fleet = _mk_fleet(client, "可讀車隊")
        driver = _make_active(client, "+85260000012")
        _add_member(client, fleet["id"], driver["driver_id"])

        roster = client.get(f"/api/v1/fleets/{fleet['id']}/members", headers=_h(driver["token"]))
        assert roster.status_code == 200, roster.text
        assert len(roster.json()["items"]) == 1

        history = client.get(
            f"/api/v1/fleets/{fleet['id']}/settlement", headers=_h(driver["token"])
        )
        assert history.status_code == 200, history.text


# --------------------------------------------------------------------------- #
# settlement
# --------------------------------------------------------------------------- #


class TestFleetSettlement:
    def test_a_member_is_charged_the_discounted_fee(self, client):
        fleet = _mk_fleet(client, "折扣車隊", discount="25")
        driver = _make_active(client, "+85260000020", deposit="500.00")
        _add_member(client, fleet["id"], driver["driver_id"])

        r = _run_fleet_settlement(client, fleet["id"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["gross_fee_hkd"] == "200.00"
        assert body["fee_hkd"] == "150.00"  # 200 less 25%
        assert body["charged"] == 1
        assert body["collected_hkd"] == "150.00"

        assert _fee_entries(client, driver["token"]) == [Decimal("150.00")]

    def test_a_zero_discount_charges_the_platform_fee(self, client):
        fleet = _mk_fleet(client, "無折扣車隊", discount="0")
        driver = _make_active(client, "+85260000021")
        _add_member(client, fleet["id"], driver["driver_id"])
        _run_fleet_settlement(client, fleet["id"])
        assert _fee_entries(client, driver["token"]) == [Decimal("200.00")]

    def test_the_discount_is_rounded_to_a_cent(self, client):
        """Truncation across a few hundred members is a quiet revenue leak."""
        from app.services.fleet_service import discounted_fee

        # 33% of 200 = 134 exactly; 1/3 of 100 does not divide.
        assert discounted_fee(Decimal("200"), Decimal("33")) == Decimal("134.00")
        assert discounted_fee(Decimal("100"), Decimal("33.33")) == Decimal("66.67")
        # Rounded, not truncated: 200 * 0.6667 = 133.34
        assert discounted_fee(Decimal("200"), Decimal("33.33")) == Decimal("133.34")

    def test_a_full_discount_collects_nothing_but_records_the_run(self, client):
        fleet = _mk_fleet(client, "全免車隊", discount="100")
        driver = _make_active(client, "+85260000022")
        _add_member(client, fleet["id"], driver["driver_id"])

        r = _run_fleet_settlement(client, fleet["id"])
        assert r.status_code == 200, r.text
        assert r.json()["collected_hkd"] == "0.00"
        assert _fee_entries(client, driver["token"]) == []

        history = client.get(
            f"/api/v1/admin/fleets/{fleet['id']}/settlement", headers=_admin_headers(client)
        ).json()
        assert len(history["items"]) == 1
        assert history["items"][0]["member_count"] == 1

    def test_rerunning_the_same_week_charges_nobody_twice(self, client):
        fleet = _mk_fleet(client, "重跑車隊", discount="10")
        driver = _make_active(client, "+85260000023")
        _add_member(client, fleet["id"], driver["driver_id"])

        first = _run_fleet_settlement(client, fleet["id"], period="2026-W30").json()
        assert first["charged"] == 1

        second = _run_fleet_settlement(client, fleet["id"], period="2026-W30").json()
        assert second["charged"] == 0
        assert second["skipped"] == 1
        # The aggregate reflects the re-run, not a doubled total.
        assert second["collected_hkd"] == "0.00"

        assert _fee_entries(client, driver["token"]) == [Decimal("180.00")]

    def test_the_aggregate_row_is_updated_not_duplicated(self, client):
        fleet = _mk_fleet(client, "彙總車隊", discount="0")
        driver = _make_active(client, "+85260000024")
        _add_member(client, fleet["id"], driver["driver_id"])

        _run_fleet_settlement(client, fleet["id"], period="2026-W31")
        _run_fleet_settlement(client, fleet["id"], period="2026-W31")

        history = client.get(
            f"/api/v1/admin/fleets/{fleet['id']}/settlement", headers=_admin_headers(client)
        ).json()
        assert len(history["items"]) == 1
        assert history["items"][0]["period"] == "2026-W31"

    def test_each_week_is_charged_separately(self, client):
        fleet = _mk_fleet(client, "跨週車隊", discount="0")
        driver = _make_active(client, "+85260000025")
        _add_member(client, fleet["id"], driver["driver_id"])

        _run_fleet_settlement(client, fleet["id"], period="2026-W32")
        _run_fleet_settlement(client, fleet["id"], period="2026-W33")

        assert _fee_entries(client, driver["token"]) == [Decimal("200.00")] * 2

    def test_a_suspended_fleet_is_not_billed(self, client):
        fleet = _mk_fleet(client, "停權車隊")
        driver = _make_active(client, "+85260000026")
        _add_member(client, fleet["id"], driver["driver_id"])
        client.patch(
            f"/api/v1/admin/fleets/{fleet['id']}",
            headers=_admin_headers(client),
            json={"status": "SUSPENDED"},
        )

        r = _run_fleet_settlement(client, fleet["id"])
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "BUSINESS_RULE_VIOLATION"
        assert _fee_entries(client, driver["token"]) == []

    def test_a_member_whose_profile_is_not_active_is_not_billed(self, client):
        """A member still in KYC has no deposit account to debit."""
        fleet = _mk_fleet(client, "未啟用車隊")
        token = _mk_user_token(client, "+85260000027")
        driver_id = _register(client, token, "+85260000027")
        _add_member(client, fleet["id"], driver_id)

        r = _run_fleet_settlement(client, fleet["id"])
        assert r.status_code == 200, r.text
        assert r.json()["member_count"] == 0
        assert r.json()["charged"] == 0

    def test_removing_a_member_stops_the_billing(self, client):
        fleet = _mk_fleet(client, "退隊車隊")
        driver = _make_active(client, "+85260000028")
        _add_member(client, fleet["id"], driver["driver_id"])
        client.delete(
            f"/api/v1/admin/fleets/{fleet['id']}/members/{driver['driver_id']}",
            headers=_admin_headers(client),
        )

        r = _run_fleet_settlement(client, fleet["id"], period="2026-W34")
        assert r.json()["member_count"] == 0
        assert _fee_entries(client, driver["token"]) == []

    def test_a_non_admin_cannot_run_a_settlement(self, client):
        fleet = _mk_fleet(client, "權限車隊")
        token = _mk_user_token(client, "+85260000029")
        r = client.post(f"/api/v1/admin/fleets/{fleet['id']}/settlement/run", headers=_h(token))
        assert r.status_code == 403, r.text


class TestFleetBillingBoundary:
    """The reason fleets are not just a grouping feature."""

    def test_a_fleet_member_is_not_charged_by_the_platform_run(self, client):
        """Both jobs reach ACTIVE drivers; only one of them may bill this one.

        The two use different ledger references, so idempotency does not help —
        if the platform run did not exclude fleet members, this driver would be
        charged twice for the same week.
        """
        fleet = _mk_fleet(client, "分帳車隊", discount="50")
        member = _make_active(client, "+85260000030")
        outsider = _make_active(client, "+85260000031")
        _add_member(client, fleet["id"], member["driver_id"])

        fleet_run = _run_fleet_settlement(client, fleet["id"], period="2026-W40").json()
        assert fleet_run["charged"] == 1
        assert fleet_run["fee_hkd"] == "100.00"

        platform_run = _run_platform_settlement(client, period="2026-W40").json()
        # The outsider is charged the full platform fee; the member is not
        # charged at all, and is reported rather than silently absent.
        assert platform_run["charged"] == 1
        assert platform_run["fleet_managed"] == 1
        assert platform_run["eligible_drivers"] == 1

        assert _fee_entries(client, member["token"]) == [Decimal("100.00")]
        assert _fee_entries(client, outsider["token"]) == [Decimal("200.00")]

    def test_running_the_platform_run_first_does_not_exempt_a_fleet_member(self, client):
        """Order must not matter: the exclusion is by roster, not by ledger."""
        fleet = _mk_fleet(client, "次序車隊", discount="0")
        member = _make_active(client, "+85260000032")
        _add_member(client, fleet["id"], member["driver_id"])

        platform_run = _run_platform_settlement(client, period="2026-W41").json()
        assert platform_run["charged"] == 0
        assert platform_run["fleet_managed"] == 1

        fleet_run = _run_fleet_settlement(client, fleet["id"], period="2026-W41").json()
        assert fleet_run["charged"] == 1
        assert _fee_entries(client, member["token"]) == [Decimal("200.00")]

    def test_leaving_a_fleet_returns_the_driver_to_the_platform_run(self, client):
        fleet = _mk_fleet(client, "回歸車隊", discount="0")
        driver = _make_active(client, "+85260000033")
        _add_member(client, fleet["id"], driver["driver_id"])
        client.delete(
            f"/api/v1/admin/fleets/{fleet['id']}/members/{driver['driver_id']}",
            headers=_admin_headers(client),
        )

        platform_run = _run_platform_settlement(client, period="2026-W42").json()
        assert platform_run["fleet_managed"] == 0
        assert platform_run["charged"] == 1
        assert _fee_entries(client, driver["token"]) == [Decimal("200.00")]


class TestSettlementIntegrity:
    def test_a_planted_reference_is_reported_as_tampered(self, client):
        """SEC-13, applied to the fleet namespace.

        A reference held by a different entry must not be mistaken for "already
        charged" — that is how a fee vanishes while the report looks healthy.
        """
        fleet = _mk_fleet(client, "篡改車隊", discount="0")
        driver = _make_active(client, "+85260000034")
        _add_member(client, fleet["id"], driver["driver_id"])

        async def _plant():
            from sqlalchemy import text

            async with client.db_factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO ledger_entries "
                        "(driver_profile_id, entry_type, amount_hkd, balance_after_hkd, "
                        " reference, note) "
                        "VALUES (:d, 'ADJUSTMENT', -1, 499, :ref, 'planted')"
                    ),
                    {
                        "d": driver["driver_id"],
                        "ref": f"fleet:{fleet['id']}:2026-W43:{driver['driver_id']}",
                    },
                )
                await session.commit()

        import asyncio

        asyncio.run(_plant())

        r = _run_fleet_settlement(client, fleet["id"], period="2026-W43")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tampered"] == 1
        assert body["charged"] == 0
        assert body["collected_hkd"] == "0.00"

        history = client.get(
            f"/api/v1/admin/fleets/{fleet['id']}/settlement", headers=_admin_headers(client)
        ).json()
        assert history["items"][0]["tampered"] == 1

    def test_arrears_are_allowed(self, client):
        """A member whose deposit cannot cover the fee goes negative, as the
        platform-wide run already does — a fleet arrangement must not change
        that behaviour."""
        fleet = _mk_fleet(client, "欠款車隊", discount="0")
        driver = _make_active(client, "+85260000035", deposit="500.00")

        # Drain the deposit with an admin-side adjustment, then bill.
        async def _drain():
            from sqlalchemy import text

            async with client.db_factory() as session:
                await session.execute(
                    text(
                        "UPDATE driver_deposits SET balance_hkd = 10 WHERE driver_profile_id = :d"
                    ),
                    {"d": driver["driver_id"]},
                )
                await session.commit()

        import asyncio

        asyncio.run(_drain())
        _add_member(client, fleet["id"], driver["driver_id"])

        r = _run_fleet_settlement(client, fleet["id"], period="2026-W44")
        assert r.status_code == 200, r.text
        assert r.json()["charged"] == 1

        detail = client.get("/api/v1/drivers/me", headers=_h(driver["token"])).json()
        assert Decimal(detail["deposit"]["balance_hkd"]) == Decimal("-190.00")
