"""Settlement preview, confirmation token, and CSV export.

The run button charges every eligible driver at once and is idempotent per ISO
week — which means the *first* accidental press is not something you get to
undo, because the money is gone and the reference is spent. So the property
that matters is not "the preview computes a total" but:

1. A run without a token is **refused while there is anything to charge**.
2. A token cannot be replayed for a different week or a different fee.
3. The preview's buckets account for every eligible driver, so the number on
   the confirm screen is the number the run acts on.

(3) is the one that quietly rots. A preview whose parts do not sum to its whole
is a report an operator will read wrong, and nothing about it looks broken.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

from app.core.exceptions import BusinessRuleError
from app.services.ledger.settlement_confirm import (
    PREVIEW_TTL_SECONDS,
    issue_confirm_token,
    verify_confirm_token,
)


async def _fetch(client, sql: str, params: dict | None = None) -> list[dict]:
    async with client.db_factory() as session:
        result = await session.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]


def _make_driver(client, *, status: str = "ACTIVE", balance: str | None = "500.00") -> str:
    """A driver profile, optionally funded. Returns the profile id."""
    profile_id = uuid.uuid4()
    user_id = uuid.uuid4()
    client.exec_sql(
        "INSERT INTO users (id, phone_e164, display_name, role, is_active, "
        "account_status, created_at, updated_at) "
        "VALUES (CAST(:i AS uuid), :p, :n, 'DRIVER', true, 'ACTIVE', now(), now())",
        {
            "i": str(user_id),
            "p": f"+8529{user_id.int % 10**7:07d}",
            "n": f"d{user_id.int % 10**8:08d}",
        },
    )
    client.exec_sql(
        "INSERT INTO driver_profiles (id, user_id, status, taxi_type, "
        "taxi_driver_plate_no, vehicle_reg_mark, hk_id_last4, is_online, "
        "created_at, updated_at) "
        "VALUES (CAST(:i AS uuid), CAST(:u AS uuid), :st, 'URBAN', :plate, :reg, "
        "'1234', false, now(), now())",
        {
            "i": str(profile_id),
            "u": str(user_id),
            "st": status,
            "plate": f"D{profile_id.int % 10**5:05d}",
            "reg": f"AA{profile_id.int % 10**4:04d}",
        },
    )
    if balance is not None:
        client.exec_sql(
            "INSERT INTO driver_deposits (id, driver_profile_id, balance_hkd, held_hkd, "
            "required_hkd, is_fulfilled, created_at, updated_at) "
            "VALUES (CAST(:i AS uuid), CAST(:d AS uuid), CAST(:b AS numeric), 0, 500.00, "
            "true, now(), now())",
            {"i": str(uuid.uuid4()), "d": str(profile_id), "b": balance},
        )
    return str(profile_id)


@pytest.fixture()
def finance(client):
    return client.admin_headers(role="FINANCE")


def _mint_token(driver_ref: str, period: str) -> str:
    """Mint a confirmation token the way the preview route does.

    The fee is read from settings, not hard-coded. Writing `"200.00"` here
    would make these tests pass only because `weekly_fee_hkd` happens to be
    200 today; the moment it changes, `verify_confirm_token` compares the
    config-derived fee against the literal and refuses with
    `CONFIRM_TOKEN_MISMATCH` — a test failing for a config value it never
    claimed to care about. The token's job is to prove the *gate* works.
    """
    from decimal import Decimal

    from app.core.config import get_settings
    from app.services.ledger.settlement_confirm import issue_confirm_token

    return issue_confirm_token(period=period, fee_hkd=str(Decimal(get_settings().weekly_fee_hkd)))


class TestPreviewWritesNothing:
    async def test_preview_does_not_touch_the_ledger(self, client, finance):
        """The whole point: a preview that charged would be the blind press it
        exists to prevent."""
        _make_driver(client, balance="500.00")
        r = client.post(
            "/api/v1/admin/settlement/preview",
            headers=finance,
            json={"period": "2026-W40"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["would_charge"] == 1
        assert await _fetch(client, "SELECT id FROM ledger_entries") == []

    async def test_preview_does_not_move_the_balance(self, client, finance):
        _make_driver(client, balance="500.00")
        client.post(
            "/api/v1/admin/settlement/preview",
            headers=finance,
            json={"period": "2026-W40"},
        )
        rows = await _fetch(client, "SELECT balance_hkd FROM driver_deposits")
        assert str(rows[0]["balance_hkd"]) == "500.00"

    async def test_preview_is_audited_because_it_leaves_no_other_trace(self, client, finance):
        """ "Who looked at the numbers before the money moved" is the first
        question after a bad run, and a preview has no transaction of its own to
        ride on."""
        _make_driver(client)
        client.post(
            "/api/v1/admin/settlement/preview",
            headers=finance,
            json={"period": "2026-W40"},
        )
        rows = await _fetch(
            client,
            "SELECT event, payload FROM admin_audit_log WHERE event = 'ADMIN_SETTLEMENT_PREVIEW'",
        )
        assert len(rows) == 1
        assert rows[0]["payload"]["period"] == "2026-W40"


class TestPreviewBucketsSumToTheWhole:
    async def test_every_eligible_driver_lands_in_exactly_one_bucket(self, client, finance):
        """A report whose parts do not sum to its whole is one someone reads
        wrong, and nothing about it looks broken."""
        _make_driver(client, balance="500.00")  # chargeable
        _make_driver(client, balance="500.00")  # chargeable
        _make_driver(client, balance=None)  # no deposit account -> skipped
        _make_driver(client, status="SUSPENDED", balance="500.00")  # not eligible
        # One already charged for this period.
        pre = _make_driver(client, balance="500.00")
        client.exec_sql(
            "INSERT INTO ledger_entries (driver_profile_id, entry_type, amount_hkd, "
            "balance_after_hkd, reference, note, created_at) "
            "VALUES (CAST(:d AS uuid), 'WEEKLY_FEE_DEDUCTION', -200.00, 300.00, "
            ":ref, 'weekly', now())",
            {"d": pre, "ref": f"weekly:{pre}:2026-W40"},
        )

        r = client.post(
            "/api/v1/admin/settlement/preview",
            headers=finance,
            json={"period": "2026-W40"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        parts = (
            body["would_charge"]
            + body["already_charged"]
            + body["tampered"]
            + body["skipped_no_deposit_account"]
        )
        assert parts == body["eligible_drivers"], body
        assert body["eligible_drivers"] == 4
        assert body["would_charge"] == 2
        assert body["already_charged"] == 1
        assert body["skipped_no_deposit_account"] == 1

    async def test_a_tampered_reference_is_a_bucket_not_a_silent_skip(self, client, finance):
        """SEC-13: a row holding this week's reference with the *wrong* amount
        makes the fee vanish while the report looks healthy. Surfacing the count
        before the run puts it on the screen the operator is already reading."""
        driver = _make_driver(client, balance="500.00")
        client.exec_sql(
            "INSERT INTO ledger_entries (driver_profile_id, entry_type, amount_hkd, "
            "balance_after_hkd, reference, note, created_at) "
            "VALUES (CAST(:d AS uuid), 'ADJUSTMENT', 999.00, 1499.00, :ref, 'planted', now())",
            {"d": driver, "ref": f"weekly:{driver}:2026-W40"},
        )
        body = client.post(
            "/api/v1/admin/settlement/preview",
            headers=finance,
            json={"period": "2026-W40"},
        ).json()
        assert body["tampered"] == 1
        assert body["already_charged"] == 0
        assert body["would_charge"] == 0

    async def test_arrears_are_reported_but_not_treated_as_an_error(self, client, finance):
        """Arrears are allowed by design — a driver in arrears keeps dispatching
        and settles on the next top-up. So a shortfall is a *decision* the
        operator is making, not a failure, and it is a sub-count of
        `would_charge` rather than a fifth bucket."""
        _make_driver(client, balance="500.00")
        _make_driver(client, balance="50.00")  # HK$200 fee -> -150
        body = client.post(
            "/api/v1/admin/settlement/preview",
            headers=finance,
            json={"period": "2026-W40"},
        ).json()
        assert body["would_charge"] == 2
        assert body["would_go_negative"] == 1
        assert body["shortfall_total_hkd"] == "150.00"
        # Both are charged; the arrears driver is included in the total.
        assert body["total_charge_hkd"] == "400.00"


class TestRunRequiresAConfirmationToken:
    async def test_a_run_with_work_pending_and_no_token_is_refused(self, client, finance):
        _make_driver(client, balance="500.00")
        r = client.post(
            "/api/v1/admin/settlement/weekly/run",
            headers=finance,
            params={"period": "2026-W40"},
        )
        assert r.status_code == 400, r.text
        assert r.json()["details"]["reason"] == "CONFIRM_TOKEN_REQUIRED"
        # And nothing was charged on the way to the refusal.
        assert await _fetch(client, "SELECT id FROM ledger_entries") == []

    async def test_a_token_from_the_preview_allows_the_run(self, client, finance):
        _make_driver(client, balance="500.00")
        preview = client.post(
            "/api/v1/admin/settlement/preview",
            headers=finance,
            json={"period": "2026-W40"},
        ).json()
        r = client.post(
            "/api/v1/admin/settlement/weekly/run",
            headers=finance,
            params={"period": "2026-W40", "confirm_token": preview["confirm_token"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["charged"] == 1
        rows = await _fetch(client, "SELECT balance_hkd FROM driver_deposits")
        assert str(rows[0]["balance_hkd"]) == "300.00"

    async def test_the_fee_the_preview_showed_is_the_fee_the_token_binds(self, client, finance):
        """The preview hands back `confirm_token`; the run accepts it.

        This is the pairing the two tests around it assume, asserted directly,
        so that a `CONFIRM_TOKEN_MISMATCH` fails here — naming the fee — rather
        than inside `test_a_token_from_the_preview_allows_the_run`, where the
        confusing part is that a *token from the preview* did not match the
        *run's* fee.
        """
        preview = client.post(
            "/api/v1/admin/settlement/preview",
            headers=finance,
            json={"period": "2026-W40"},
        ).json()
        from app.core.config import get_settings
        from app.core.money import money_str

        expected_fee = money_str(get_settings().weekly_fee_hkd)
        assert preview["fee_hkd"] == expected_fee
        # `verify_confirm_token` returns the decoded claims (it raises on a bad
        # token), so "it verified" is asserted by reaching the claims, not by
        # comparing the return value to True.
        claims = verify_confirm_token(
            preview["confirm_token"], period="2026-W40", fee_hkd=expected_fee
        )
        assert claims["fee_hkd"] == expected_fee
        assert claims["period"] == "2026-W40"

    async def test_an_already_settled_period_needs_no_token(self, client, finance):
        """That run is a no-op by construction. Demanding a preview to prove
        nothing will happen trains operators to click through the gate rather
        than read it."""
        _make_driver(client, balance="500.00")
        preview = client.post(
            "/api/v1/admin/settlement/preview",
            headers=finance,
            json={"period": "2026-W40"},
        ).json()
        client.post(
            "/api/v1/admin/settlement/weekly/run",
            headers=finance,
            params={"period": "2026-W40", "confirm_token": preview["confirm_token"]},
        )
        # Second run, no token: everything is settled, so it is allowed through
        # and is a no-op.
        r = client.post(
            "/api/v1/admin/settlement/weekly/run",
            headers=finance,
            params={"period": "2026-W40"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["charged"] == 0
        assert r.json()["skipped"] == 1

    async def test_the_run_records_that_a_token_was_used(self, client, finance):
        _make_driver(client, balance="500.00")
        preview = client.post(
            "/api/v1/admin/settlement/preview",
            headers=finance,
            json={"period": "2026-W40"},
        ).json()
        client.post(
            "/api/v1/admin/settlement/weekly/run",
            headers=finance,
            params={"period": "2026-W40", "confirm_token": preview["confirm_token"]},
        )
        rows = await _fetch(
            client,
            "SELECT payload FROM admin_audit_log WHERE event = 'ADMIN_SETTLEMENT_RUN'",
        )
        assert rows[0]["payload"]["confirmed_by_token"] is True


class TestTokenBinding:
    """Pure unit tests over the signer. No HTTP, no database — these are about
    what the token binds, which is the whole safety property."""

    def test_a_token_for_one_period_is_not_valid_for_another(self):
        token = issue_confirm_token(period="2026-W40", fee_hkd="200.00")
        claims = verify_confirm_token(token, period="2026-W40", fee_hkd="200.00")
        assert claims["period"] == "2026-W40"
        with pytest.raises(BusinessRuleError) as exc:
            verify_confirm_token(token, period="2026-W41", fee_hkd="200.00")
        assert exc.value.details["reason"] == "CONFIRM_TOKEN_MISMATCH"

    def test_a_token_is_bound_to_the_fee_as_well_as_the_period(self):
        """A preview that said HK$200 and a run that charged HK$500 is exactly
        the mismatch the preview exists to prevent."""
        token = issue_confirm_token(period="2026-W40", fee_hkd="200.00")
        with pytest.raises(BusinessRuleError) as exc:
            verify_confirm_token(token, period="2026-W40", fee_hkd="500.00")
        assert exc.value.details["reason"] == "CONFIRM_TOKEN_MISMATCH"

    def test_a_tampered_body_is_rejected(self):
        token = issue_confirm_token(period="2026-W40", fee_hkd="200.00")
        body, _, sig = token.rpartition(".")
        forged = body.replace("200.00", "1.00") + "." + sig
        with pytest.raises(BusinessRuleError) as exc:
            verify_confirm_token(forged, period="2026-W40", fee_hkd="1.00")
        assert exc.value.details["reason"] == "CONFIRM_TOKEN_INVALID"

    def test_a_random_string_is_rejected(self):
        with pytest.raises(BusinessRuleError) as exc:
            verify_confirm_token("not-a-token")
        assert exc.value.details["reason"] == "CONFIRM_TOKEN_MISSING"

    def test_an_expired_token_is_rejected_with_its_own_reason(self):
        """Distinct from a mismatch: the console tells the operator to preview
        again rather than sending them to re-login."""
        import json
        import time

        from app.core.config import get_settings
        from app.services.ledger.settlement_confirm import _PURPOSE, _sign

        body = json.dumps(
            {
                "purpose": _PURPOSE,
                "exp": int(time.time()) - 1,
                "period": "2026-W40",
                "fee_hkd": "200.00",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        token = f"{body}.{_sign(get_settings().jwt_secret_key, body)}"
        with pytest.raises(BusinessRuleError) as exc:
            verify_confirm_token(token, period="2026-W40", fee_hkd="200.00")
        assert exc.value.details["reason"] == "CONFIRM_TOKEN_EXPIRED"

    def test_the_ttl_is_short_enough_to_matter_and_returned_to_the_client(self, client, finance):
        """A token that lived for a day could be spent against numbers that had
        since changed."""
        _make_driver(client)
        body = client.post(
            "/api/v1/admin/settlement/preview",
            headers=finance,
            json={"period": "2026-W40"},
        ).json()
        assert body["confirm_expires_in_seconds"] == PREVIEW_TTL_SECONDS
        assert PREVIEW_TTL_SECONDS <= 3600


class TestAccess:
    @pytest.mark.parametrize("role", ["SUPPORT", "OPERATIONS"])
    async def test_lower_roles_cannot_preview_or_run(self, client, role):
        """A preview only one role could obtain and another could act on is a
        workflow nobody can complete — so both routes carry the same guard."""
        headers = client.admin_headers(role=role)
        assert (
            client.post(
                "/api/v1/admin/settlement/preview",
                headers=headers,
                json={"period": "2026-W40"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/admin/settlement/weekly/run",
                headers=headers,
                params={"period": "2026-W40"},
            ).status_code
            == 403
        )

    async def test_lower_roles_cannot_export_the_week(self, client):
        """The export is the whole week's billing in one file."""
        r = client.get(
            "/api/v1/admin/settlement/export.csv",
            headers=client.admin_headers(role="OPERATIONS"),
            params={"period": "2026-W40"},
        )
        assert r.status_code == 403, r.text


class TestCsvExport:
    async def test_export_carries_a_header_and_a_total_row(self, client, finance):
        """The trailing TOTAL is not decoration: a spreadsheet that sums the
        column and gets a different answer than the file claims is a support
        ticket, and the difference is usually a row someone filtered out."""
        driver = _make_driver(client, balance="500.00")
        client.exec_sql(
            "INSERT INTO ledger_entries (driver_profile_id, entry_type, amount_hkd, "
            "balance_after_hkd, reference, note, created_at) "
            "VALUES (CAST(:d AS uuid), 'WEEKLY_FEE_DEDUCTION', -200.00, 300.00, "
            ":ref, 'weekly fee', now())",
            {"d": driver, "ref": f"weekly:{driver}:2026-W40"},
        )
        r = client.get(
            "/api/v1/admin/settlement/export.csv",
            headers=finance,
            params={"period": "2026-W40"},
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        lines = r.text.strip().splitlines()
        assert lines[0].startswith("entry_id,created_at,driver_profile_id")
        assert "-200.00" in r.text
        assert lines[-1].startswith("TOTAL,")
        assert "-200.00" in lines[-1]

    async def test_export_is_scoped_to_the_requested_week(self, client, finance):
        driver = _make_driver(client, balance="500.00")
        for week in ("2026-W40", "2026-W41"):
            ref = f"weekly:{driver}:{week}"
            client.exec_sql(
                "INSERT INTO ledger_entries (driver_profile_id, entry_type, amount_hkd, "
                "balance_after_hkd, reference, note, created_at) "
                "VALUES (CAST(:d AS uuid), 'WEEKLY_FEE_DEDUCTION', -200.00, "
                "300.00, :ref, 'weekly fee', CAST(:ts AS timestamptz))",
                {"d": driver, "ref": ref, "ts": _week_instant(ref)},
            )
        body = client.get(
            "/api/v1/admin/settlement/export.csv",
            headers=finance,
            params={"period": "2026-W40"},
        ).text
        assert "2026-W40" in body
        assert "2026-W41" not in body

    async def test_an_impossible_week_is_a_400(self, client, finance):
        """`2026-W54` matches the query pattern but is not a real ISO week.
        `fromisocalendar` says so; letting it 500 would be a stack trace for a
        typo."""
        assert (
            client.get(
                "/api/v1/admin/settlement/export.csv",
                headers=finance,
                params={"period": "2026-W54"},
            ).status_code
            == 400
        )


def _week_instant(reference: str) -> datetime:
    """A Monday inside the week named by `reference`, as a `datetime`.

    Not `.isoformat()`: asyncpg binds a `timestamptz` parameter by *type*, and
    an ISO string is rejected with
    `expected a datetime.date or datetime.datetime instance, got 'str'`.
    """
    from app.services.ledger.settlement_service import period_start

    period = reference.rsplit(":", 1)[-1]
    return period_start(period)


class TestPeriodStartIsTheInverseOfPeriodKey:
    def test_round_trips_across_a_year_boundary(self):
        """The year-boundary weeks are where an ISO year and a calendar year
        disagree, and where day-based arithmetic silently lands in the wrong
        week. This is the case `period_key`'s docstring calls out.

        `2026-W53` is real and `2025-W53` is not — ISO years have 52 or 53
        weeks depending on where their Thursday falls, and 2026 is a 53-week
        year. Using the wrong one here would have made the test pass by
        asserting nothing.
        """
        from app.services.ledger.settlement_service import period_key, period_start

        for period in ("2024-W52", "2025-W52", "2026-W01", "2026-W40", "2026-W53"):
            assert period_key(period_start(period)) == period

    def test_2026_w53_is_valid_and_2025_w53_is_not(self):
        """A pattern-matching week is not necessarily a real one. The service
        raises a business rule for the impossible case rather than letting
        `fromisocalendar` surface a stack trace for a typo."""
        from app.core.exceptions import BusinessRuleError
        from app.services.ledger.settlement_service import period_start

        assert period_start("2026-W53") is not None
        with pytest.raises(BusinessRuleError):
            period_start("2025-W53")
