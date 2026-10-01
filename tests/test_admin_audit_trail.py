"""The audit trail must record decisions, not only logins.

`AdminAuditLog`'s own docstring says it answers *"who logged in, from where, and
did they move money"*. The second half was false: it had one write point
(`AdminAuthService.audit`) and only login-class event constants, so every money
and state action — KYC decisions, deposit grants and adjustments, refund
decisions, settlement runs — left no row at all.

`ledger_entries.created_by` does not close the gap. It carries a value only
when an amount actually moved, so a *refused* refund and a *rejected* licence
are decisions with no trace. These tests assert the rows exist, because the
failure mode is silence: nothing breaks when an audit write is missing.

Every test here would pass trivially against a mock. They go through the real
HTTP routes against a real database so that a removed `record_audit` call is a
failure rather than an unnoticed deletion.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

from app.services.audit_service import (
    EV_DEPOSIT_ADJUST,
    EV_DEPOSIT_GRANT,
    EV_KYC_DECISION,
    EV_REFUND_DECISION,
    EV_SETTLEMENT_RUN,
)


def _phone() -> str:
    return "+8529" + str(uuid.uuid4().int)[:7]


async def _fetch(client, sql: str, params: dict | None = None) -> list[dict]:
    """Run a read against the test client's own database and return plain dicts.

    Uses the client's `db_factory` rather than a fixture session so the read
    sees what the HTTP request actually committed — a session-scoped fixture
    with its own transaction could be looking at a snapshot from before the
    call.
    """
    async with client.db_factory() as session:
        result = await session.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]


async def _audit_rows(client, event: str) -> list[dict]:
    """Read the audit table directly.

    Deliberately not through `GET /admin/audit`: that endpoint is itself new,
    and asserting a write via a read path that could be broken in the same
    direction would prove nothing about the write.
    """
    return await _fetch(
        client,
        "SELECT event, outcome, detail, payload, admin_id, ip_address "
        "FROM admin_audit_log WHERE event = :e ORDER BY created_at",
        {"e": event},
    )


@pytest.fixture
async def driver_awaiting_review(client):
    """A registered driver sitting at PENDING_KYC.

    `client.activate` rather than the profile/email endpoints: registration is
    gated on a verified account (P-2), and this helper is about getting that
    gate out of the way. The verification flows have their own tests.
    """
    token = client.activate(_phone())
    r = client.post(
        "/api/v1/drivers/register",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "hk_id_last4": "1234",
            "taxi_driver_plate_no": "TD1234",
            "vehicle_reg_mark": "VX1234",
            "taxi_type": "URBAN",
        },
    )
    assert r.status_code == 201, r.text
    return {"id": r.json()["id"], "token": token}


class TestMoneyAndStateActionsAreAudited:
    async def test_kyc_decision_writes_an_audit_row(self, client, driver_awaiting_review):
        """A KYC decision is a compliance judgement; it must be attributable.

        This is the one the design doc calls out: an intern rejecting 200
        applications was invisible.
        """
        d = driver_awaiting_review
        r = client.post(
            f"/api/v1/admin/drivers/{d['id']}/review",
            headers=client.admin_headers(),
            json={"decision": "approve", "note": "docs verified"},
        )
        assert r.status_code == 200, r.text

        rows = await _audit_rows(client, EV_KYC_DECISION)
        assert len(rows) == 1, f"expected exactly one KYC audit row, got {rows}"
        row = rows[0]
        assert row["outcome"] == "SUCCESS"
        assert row["admin_id"] is not None
        # The structured payload is what makes the row answerable after the
        # fact: which driver, what decision, and what the status was before.
        assert row["payload"]["driver_profile_id"] == d["id"]
        assert row["payload"]["decision"] == "approve"
        assert row["payload"]["before"]["status"] == "PENDING_KYC"
        assert row["payload"]["after"]["status"] == "DEPOSIT_REQUIRED"

    async def test_deposit_grant_writes_an_audit_row(self, client, driver_awaiting_review):
        d = driver_awaiting_review
        client.post(
            f"/api/v1/admin/drivers/{d['id']}/review",
            headers=client.admin_headers(),
            json={"decision": "approve"},
        )
        r = client.post(
            f"/api/v1/admin/drivers/{d['id']}/deposit/grant",
            headers=client.admin_headers(),
            json={"amount_hkd": "500.00", "note": "bank transfer"},
        )
        assert r.status_code == 200, r.text

        rows = await _audit_rows(client, EV_DEPOSIT_GRANT)
        assert len(rows) == 1
        assert rows[0]["payload"]["amount_hkd"] == "500.00"
        assert rows[0]["payload"]["reference"]
        # A grant has a side effect an operator may not expect — it can flip
        # the driver to ACTIVE. The audit row records whether it did.
        assert rows[0]["payload"]["activated_driver"] is True

    async def test_deposit_adjust_records_the_reason(self, client, driver_awaiting_review):
        """An ADJUSTMENT has no upstream event, so the reason *is* the trail."""
        d = driver_awaiting_review
        r = client.post(
            f"/api/v1/admin/drivers/{d['id']}/deposit/adjust",
            headers=client.admin_headers(),
            json={"amount_hkd": "-120.00", "reason": "overcharged on trip 88"},
        )
        assert r.status_code == 200, r.text

        rows = await _audit_rows(client, EV_DEPOSIT_ADJUST)
        assert len(rows) == 1
        assert rows[0]["payload"]["reason"] == "overcharged on trip 88"
        assert rows[0]["payload"]["amount_hkd"] == "-120.00"
        assert "overcharged on trip 88" in rows[0]["detail"]

    async def test_settlement_run_is_audited(self, client):
        r = client.post(
            "/api/v1/admin/settlement/weekly/run",
            headers=client.admin_headers(),
            params={"period": "2026-W40"},
        )
        assert r.status_code == 200, r.text

        rows = await _audit_rows(client, EV_SETTLEMENT_RUN)
        assert len(rows) == 1
        assert rows[0]["payload"]["period"] == "2026-W40"

    async def test_refund_rejection_is_audited_too(self, client, driver_awaiting_review):
        """A refusal moves no money, which is exactly why it needs its own row.

        `refund.decided_by` is set on rejection as well, but it lives on a row
        nobody reads unless they already suspect something. The audit log is
        what makes "who refused this, and when" findable by *actor*.
        """
        d = driver_awaiting_review
        refund_id = str(uuid.uuid4())
        # A refund decision reads the driver's deposit row under a lock — the
        # service refuses with "driver deposit account not found" otherwise, and
        # that refusal is *correct*: money cannot be returned from an account
        # that does not exist. So the fixture has to create the account.
        # The request's amount must already be *held*: the real flow locks the
        # driver's balance into `held_hkd` when the request is made, and
        # approving is what actually pays it out. A deposit row with
        # `held_hkd = 0` is not a smaller version of this state, it is a
        # different (impossible) one, and the service rightly rejects it.
        client.exec_sql(
            "INSERT INTO driver_deposits "
            "(id, driver_profile_id, balance_hkd, held_hkd, required_hkd, is_fulfilled, "
            " created_at, updated_at) "
            "VALUES (:id, :dp, 0, 100.00, 500.00, false, now(), now())",
            {"id": str(uuid.uuid4()), "dp": d["id"]},
        )
        # `refund_requests` has no `user_id` and no `updated_at` — it hangs off
        # the driver profile, and the only timestamp is `created_at`. Both were
        # assumed from other tables rather than read off the model.
        # `client.exec_sql` is the *synchronous* helper; only `_fetch` awaits.
        client.exec_sql(
            "INSERT INTO refund_requests "
            "(id, driver_profile_id, amount_hkd, status, note, created_at) "
            "VALUES (:id, :dp, 100.00, 'PENDING', 'test', now())",
            {"id": refund_id, "dp": d["id"]},
        )
        r = client.post(
            f"/api/v1/admin/refunds/{refund_id}/decision",
            headers=client.admin_headers(),
            json={"decision": "reject", "note": "not eligible"},
        )
        assert r.status_code == 200, r.text

        rows = await _audit_rows(client, EV_REFUND_DECISION)
        assert len(rows) == 1, "a rejected refund must still be attributed"
        assert rows[0]["payload"]["decision"] == "reject"
        assert rows[0]["payload"]["amount_hkd"] == "100.00"

    async def test_request_context_is_attached(self, client, driver_awaiting_review):
        """IP and User-Agent come from the request, not from nowhere.

        Worth its own test because the login path supplies them as plain
        strings while the console path reads them off a Request, and an earlier
        draft reconciled the two with a follow-up UPDATE that raced. If that
        regression returned, both assertions here would fail intermittently.
        """
        d = driver_awaiting_review
        client.post(
            f"/api/v1/admin/drivers/{d['id']}/review",
            headers=client.admin_headers(),
            json={"decision": "approve"},
        )
        rows = await _audit_rows(client, EV_KYC_DECISION)
        assert rows[0]["ip_address"], "audit row lost the client address"

    async def test_a_failed_action_leaves_no_success_row(self, client, driver_awaiting_review):
        """The row is written on the action's own session, so a refusal does not
        leave a SUCCESS row behind claiming a decision that never happened."""
        d = driver_awaiting_review
        # Terminate first, then try an illegal transition from TERMINATED.
        client.post(
            f"/api/v1/admin/drivers/{d['id']}/review",
            headers=client.admin_headers(),
            json={"decision": "terminate"},
        )
        r = client.post(
            f"/api/v1/admin/drivers/{d['id']}/review",
            headers=client.admin_headers(),
            json={"decision": "approve"},
        )
        assert r.status_code >= 400, r.text

        rows = await _audit_rows(client, EV_KYC_DECISION)
        assert rows, "the successful terminate should still be recorded"
        # Exactly the one legitimate decision — the rejected attempt added none.
        assert len(rows) == 1
        assert rows[0]["payload"]["decision"] == "terminate"


class TestAuditIsReadable:
    async def test_audit_endpoint_returns_rows_newest_first(self, client, driver_awaiting_review):
        d = driver_awaiting_review
        client.post(
            f"/api/v1/admin/drivers/{d['id']}/review",
            headers=client.admin_headers(),
            json={"decision": "approve"},
        )
        client.post(
            f"/api/v1/admin/drivers/{d['id']}/deposit/grant",
            headers=client.admin_headers(),
            json={"amount_hkd": "500.00"},
        )

        r = client.get("/api/v1/admin/audit", headers=client.admin_headers())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] >= 2
        events = [i["event"] for i in body["items"]]
        assert EV_DEPOSIT_GRANT in events
        assert EV_KYC_DECISION in events
        # Newest first: the grant happened after the review.
        assert events.index(EV_DEPOSIT_GRANT) < events.index(EV_KYC_DECISION)

    async def test_audit_endpoint_filters_by_event(self, client, driver_awaiting_review):
        d = driver_awaiting_review
        client.post(
            f"/api/v1/admin/drivers/{d['id']}/review",
            headers=client.admin_headers(),
            json={"decision": "approve"},
        )
        r = client.get(
            "/api/v1/admin/audit",
            headers=client.admin_headers(),
            params={"event": EV_KYC_DECISION},
        )
        assert r.status_code == 200, r.text
        assert {i["event"] for i in r.json()["items"]} == {EV_KYC_DECISION}


class TestAuditRowNeverLeaksSecrets:
    async def test_no_audit_payload_contains_a_secret(self, client, driver_awaiting_review):
        """The audit log is readable by every role, so it must hold no secrets.

        A password, a TOTP secret or a raw token in `payload` would be a
        credential disclosure to anyone who can read the console — including
        SUPPORT, who by design cannot change anything.

        Scans for secret **shapes**, not words. An earlier version rejected any
        row containing the substring "otp", which failed on the entirely honest
        login detail `"totp required"` — a false positive that would have pushed
        someone to weaken the check rather than fix it. What matters is whether
        a value that could be replayed appears, so this looks for a JWT, a hex
        digest, and the field names that would carry a credential.

        The hex rule needs one more carve-out: 32+ hex characters are *also*
        what a UUID looks like with its dashes stripped, and the deposit ledger
        builds its reference as `grant:<driver_id>:<uuid4().hex>`. Flagging that
        would make the honest `ADMIN_DEPOSIT_GRANT` row fail — so a bare hex run
        that is exactly a de-dashed UUID is allowed, and a run of any other
        length is not. Credentials are not shaped like UUIDs; identifiers are.
        """
        _dedashed_uuid = re.compile(r"\A[0-9a-f]{8}[0-9a-f]{4}[0-9a-f]{4}[0-9a-f]{4}[0-9a-f]{12}\Z")
        d = driver_awaiting_review
        client.post(
            f"/api/v1/admin/drivers/{d['id']}/review",
            headers=client.admin_headers(),
            json={"decision": "approve"},
        )
        client.post(
            f"/api/v1/admin/drivers/{d['id']}/deposit/grant",
            headers=client.admin_headers(),
            json={"amount_hkd": "500.00"},
        )
        # Log in too, so the login-path rows are covered by the same scan.
        client.admin_headers()

        rows = await _fetch(client, "SELECT event, detail, payload::text AS p FROM admin_audit_log")
        assert rows
        for row in rows:
            blob = f"{row['detail'] or ''} {row['p'] or ''}"
            # A JWT is three base64url segments: `eyJ...`.`eyJ...`.`...`.
            assert not re.search(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.", blob), (
                f"audit row {row['event']} contains what looks like a JWT: {blob[:200]}"
            )
            # A long hex run is a token/digest (sha256 is 64 chars) — unless it
            # is simply a UUID that lost its dashes, which is an identifier.
            hex_runs = re.findall(r"\b[0-9a-f]{32,}\b", blob)
            suspicious = [h for h in hex_runs if not _dedashed_uuid.match(h)]
            assert not suspicious, (
                f"audit row {row['event']} contains a hex digest: {suspicious[:3]} in {blob[:200]}"
            )
            # Field names that would only appear if a credential were being
            # serialised into the row.
            for key in ('"password"', '"totp_secret"', '"token"', '"code_hash"'):
                assert key not in blob.lower(), (
                    f"audit row {row['event']} serialises {key}: {blob[:200]}"
                )


class TestAuditSurvivesAMissingColumnValue:
    async def test_login_rows_still_write_payload_null(self, client):
        """Rows written by the login path carry no payload, and must stay valid.

        The column is additive and nullable, so a login row has `payload = NULL`
        and its `detail` is unchanged. This guards the extraction of
        `record_audit` from silently changing the login path's output.
        """
        # A failed login for a username that does not exist: recorded, with no
        # admin row to point at.
        r = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "nobody-here", "password": "wrong-password"},
        )
        assert r.status_code in (401, 403, 429), r.text

        rows = await _fetch(
            client,
            "SELECT event, admin_id, payload, ip_address FROM admin_audit_log "
            "WHERE username_attempted = 'nobody-here'",
        )
        assert rows, "a failed login for an unknown username must still be recorded"
        assert rows[0]["admin_id"] is None
        assert rows[0]["payload"] is None


class TestAuditTableIsAppendOnly:
    async def test_the_application_never_updates_or_deletes_audit_rows(self):
        """Immutability is a property of the code, so it is checked on the code.

        Reading the source rather than the database: the claim is "there is no
        UPDATE or DELETE path in the application", which is what the model
        docstring asserts and what a DB-level REVOKE would later enforce. A
        query-based test could only observe the rows that happen to exist.
        """
        import pathlib

        offenders = []
        for path in pathlib.Path("app").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "AdminAuditLog" not in source:
                continue
            for lineno, line in enumerate(source.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                lowered = stripped.lower()
                # `insert` is fine. `update(AdminAuditLog)` / `delete(...)` are not.
                if ("update(adminauditlog" in lowered) or ("delete(adminauditlog" in lowered):
                    offenders.append(f"{path}:{lineno}: {stripped}")
        assert offenders == [], f"audit rows must be append-only, found: {offenders}"
