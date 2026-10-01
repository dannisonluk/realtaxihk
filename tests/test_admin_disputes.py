"""Disputes: the queue, the thread, the SLA, and the role split on resolution.

The property this file exists to protect is not "a dispute can be created". It
is that the *judgement* is (a) recorded, (b) not silently repeatable, and (c)
not makeable by a role that is not entitled to the consequence. Those three are
what turn a complaint from a support conversation into an auditable decision.

The SLA tests are the other half. A queue with deadlines is only useful if the
deadline is derived rather than chosen, and if "almost due" sorts above "just
arrived" — the design doc is explicit that recency sorting is the wrong default
for incidents.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text


async def _fetch(client, sql: str, params: dict | None = None) -> list[dict]:
    """Run a read on its own session. Mirrors the other admin test modules."""
    async with client.db_factory() as session:
        result = await session.execute(text(sql), params or {})
        return [dict(row) for row in result.mappings()]


# `ops` / `finance` / `support` come from conftest — one fixture per role.


def _open(client, headers, **overrides) -> dict:
    body = {
        "category": "CONDUCT",
        "summary": "driver was rude",
        "severity": "NORMAL",
    }
    body.update(overrides)
    r = client.post("/api/v1/admin/disputes", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


class TestOpeningACase:
    async def test_operations_can_open_one_and_it_is_audited(self, client, ops):
        d = _open(client, ops, category="FARE", summary="meter looked wrong")
        assert d["status"] == "OPEN"
        assert d["category"] == "FARE"
        # The opening summary is also the first message, so the thread explains
        # itself without joining back to the row.
        assert len(d["messages"]) == 1
        assert d["messages"][0]["body"] == "meter looked wrong"
        assert d["messages"][0]["is_internal"] is False

        rows = await _fetch(
            client,
            "SELECT event, payload FROM admin_audit_log WHERE event = 'ADMIN_DISPUTE_CREATE'",
        )
        assert len(rows) == 1
        assert rows[0]["payload"]["dispute_id"] == d["id"]
        assert rows[0]["payload"]["category"] == "FARE"

    async def test_support_cannot_open_one(self, client, support):
        """Recording a problem and deciding one are different jobs. SUPPORT
        answers questions; opening a case is the start of a decision."""
        r = client.post(
            "/api/v1/admin/disputes",
            headers=support,
            json={"category": "CONDUCT", "summary": "x"},
        )
        assert r.status_code == 403
        assert r.json()["details"]["reason"] == "ADMIN_ROLE_INSUFFICIENT"

    async def test_a_blank_summary_is_refused(self, client, ops):
        r = client.post(
            "/api/v1/admin/disputes",
            headers=ops,
            json={"category": "CONDUCT", "summary": "   "},
        )
        # `min_length=1` lets whitespace through; the service is what refuses it,
        # because a case titled "   " cannot be triaged.
        assert r.status_code == 400
        assert r.json()["details"]["reason"] == "DISPUTE_SUMMARY_REQUIRED"

    async def test_an_unknown_category_is_a_422(self, client, ops):
        r = client.post(
            "/api/v1/admin/disputes",
            headers=ops,
            json={"category": "NOT_A_CATEGORY", "summary": "x"},
        )
        assert r.status_code == 422

    async def test_an_order_is_optional(self, client, ops):
        """Account- and app-level complaints have no trip. Requiring one would
        force those into a fake order, which then pollutes trip reports."""
        d = _open(client, ops, category="APP_ISSUE", summary="app crashed")
        assert d["order_id"] is None


class TestSlaIsDerivedNotChosen:
    """The deadline comes from severity. A caller that could set its own could
    quietly give a safety report a week."""

    @pytest.mark.parametrize(
        ("severity", "hours"),
        [
            ("SAFETY_CRITICAL", 1),
            ("HIGH", 4),
            ("NORMAL", 24),
            ("LOW", 72),
        ],
    )
    async def test_each_severity_gets_its_own_budget(self, client, ops, severity, hours):
        d = _open(client, ops, severity=severity)
        assert d["sla_hours"] == hours
        # `seconds_until_due` is computed against the same clock as the sort,
        # so it is the number the UI shows rather than one it recomputes.
        assert hours * 3600 - 60 <= d["seconds_until_due"] <= hours * 3600 + 60
        assert d["is_overdue"] is False

    async def test_the_sla_cannot_be_injected(self, client, ops):
        """Extra keys are ignored by pydantic's default, and the derived value
        must not take the caller's word for it."""
        r = client.post(
            "/api/v1/admin/disputes",
            headers=ops,
            json={
                "category": "SAFETY",
                "summary": "brake failure",
                "severity": "SAFETY_CRITICAL",
                "sla_due_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["sla_hours"] == 1

    async def test_a_safety_category_flags_even_at_low_severity(self, client, ops):
        """A party who files SAFETY and marks it LOW is a common pattern; the
        classification must not let that downgrade it out of the safety queue."""
        d = _open(client, ops, category="SAFETY", severity="LOW")
        assert d["safety_flag"] is True

    async def test_high_severity_flags_too_and_normal_does_not(self, client, ops):
        assert _open(client, ops, severity="HIGH")["safety_flag"] is True
        assert _open(client, ops, severity="NORMAL")["safety_flag"] is False


class TestQueueOrderingIsByDeadline:
    """The design doc's most load-bearing queue decision: SLA first, not newest
    first. An incidents queue sorted by recency answers whatever arrived last
    and buries the case about to breach."""

    async def test_the_case_closest_to_breach_sorts_first(self, client, ops):
        # Opened in this order, so newest-first would reverse the expectation.
        slow = _open(client, ops, severity="LOW", summary="lost umbrella")
        fast = _open(client, ops, severity="SAFETY_CRITICAL", summary="assault")
        mid = _open(client, ops, severity="NORMAL", summary="overcharged")

        items = client.get("/api/v1/admin/disputes", headers=ops).json()["items"]
        assert [i["id"] for i in items] == [fast["id"], mid["id"], slow["id"]]

    async def test_an_overdue_case_is_reported_and_sorts_first(self, client, ops):
        d = _open(client, ops, severity="NORMAL")
        # Backdate the deadline rather than the clock: the route computes
        # `is_overdue` against now, and waiting would make the suite slow.
        client.exec_sql(
            "UPDATE order_disputes SET sla_due_at = :due WHERE id = CAST(:id AS uuid)",
            {
                "due": datetime.now(UTC) - timedelta(hours=2),
                "id": d["id"],
            },
        )
        (_later,) = [
            i
            for i in client.get("/api/v1/admin/disputes", headers=ops).json()["items"]
            if i["id"] == d["id"]
        ]
        assert _later["is_overdue"] is True
        assert _later["seconds_until_due"] < 0

    async def test_a_resolved_case_is_not_overdue(self, client, ops, finance):
        """Overdue is a property of open work. A closed case past its deadline
        is history, not a failure, and counting it would make the header number
        grow forever."""
        d = _open(client, ops, severity="NORMAL")
        client.post(
            f"/api/v1/admin/disputes/{d['id']}/resolve",
            headers=finance,
            json={"resolution": "NONE", "note": "no charge warranted"},
        )
        client.exec_sql(
            "UPDATE order_disputes SET sla_due_at = :due WHERE id = CAST(:id AS uuid)",
            {"due": datetime.now(UTC) - timedelta(hours=5), "id": d["id"]},
        )
        body = client.get(f"/api/v1/admin/disputes/{d['id']}", headers=ops).json()
        assert body["is_overdue"] is False


class TestFiltersAndStats:
    async def test_default_view_hides_closed_cases(self, client, ops, finance):
        open_one = _open(client, ops, summary="still open")
        closed = _open(client, ops, summary="will be resolved")
        client.post(
            f"/api/v1/admin/disputes/{closed['id']}/resolve",
            headers=finance,
            json={"resolution": "NONE", "note": "fine", "close": True},
        )
        items = client.get("/api/v1/admin/disputes", headers=ops).json()["items"]
        assert [i["id"] for i in items] == [open_one["id"]]

        # And it is still reachable explicitly — a default filter, not a wall.
        all_items = client.get(
            "/api/v1/admin/disputes", headers=ops, params={"open_only": False}
        ).json()["items"]
        assert {i["id"] for i in all_items} == {open_one["id"], closed["id"]}

    async def test_overdue_only_and_unassigned_only(self, client, ops):
        assigned = _open(client, ops, summary="claimed")
        client.post(f"/api/v1/admin/disputes/{assigned['id']}/assign", headers=ops)
        stalled = _open(client, ops, summary="nobody wants this")
        client.exec_sql(
            "UPDATE order_disputes SET sla_due_at = :due WHERE id = CAST(:id AS uuid)",
            {"due": datetime.now(UTC) - timedelta(hours=1), "id": stalled["id"]},
        )

        overdue = client.get(
            "/api/v1/admin/disputes", headers=ops, params={"overdue_only": True}
        ).json()["items"]
        assert [i["id"] for i in overdue] == [stalled["id"]]

        unassigned = client.get(
            "/api/v1/admin/disputes", headers=ops, params={"unassigned_only": True}
        ).json()["items"]
        assert [i["id"] for i in unassigned] == [stalled["id"]]

    async def test_an_unknown_filter_value_is_a_400_not_an_empty_list(self, client, ops):
        """An empty list reads as "nothing overdue", which is the wrong answer to
        a typo and the one that gets believed."""
        r = client.get("/api/v1/admin/disputes", headers=ops, params={"status": "OPENISH"})
        assert r.status_code == 400
        assert r.json()["details"]["reason"] == "INVALID_FILTER"

    async def test_stats_are_counted_server_side(self, client, ops):
        _open(client, ops, category="CONDUCT")
        _open(client, ops, category="SAFETY", severity="SAFETY_CRITICAL")
        stalled = _open(client, ops, category="FARE")
        client.exec_sql(
            "UPDATE order_disputes SET sla_due_at = :due WHERE id = CAST(:id AS uuid)",
            {"due": datetime.now(UTC) - timedelta(hours=3), "id": stalled["id"]},
        )

        stats = client.get("/api/v1/admin/disputes/stats", headers=ops).json()
        assert stats["total"] == 3
        assert stats["open"] == 3
        assert stats["unassigned"] == 3
        assert stats["overdue"] == 1
        assert stats["safety_flag"] == 1

    async def test_pagination_reports_the_true_total(self, client, ops):
        for i in range(3):
            _open(client, ops, summary=f"case {i}")
        body = client.get(
            "/api/v1/admin/disputes", headers=ops, params={"limit": 1, "offset": 0}
        ).json()
        assert len(body["items"]) == 1
        # Not 1: the count is over the filter, not the page.
        assert body["total"] == 3
        assert body["limit"] == 1


class TestThread:
    async def test_any_role_may_reply_including_support(self, client, ops, support):
        d = _open(client, ops)
        r = client.post(
            f"/api/v1/admin/disputes/{d['id']}/messages",
            headers=support,
            json={"body": "passenger called back, says the fare was fine"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["is_internal"] is False

    async def test_an_internal_note_is_flagged(self, client, ops):
        d = _open(client, ops)
        r = client.post(
            f"/api/v1/admin/disputes/{d['id']}/messages",
            headers=ops,
            json={"body": "third complaint about this plate", "is_internal": True},
        )
        assert r.json()["is_internal"] is True

        detail = client.get(f"/api/v1/admin/disputes/{d['id']}", headers=ops).json()
        internal = [m for m in detail["messages"] if m["is_internal"]]
        assert len(internal) == 1
        assert internal[0]["body"] == "third complaint about this plate"

    async def test_the_message_body_is_not_copied_into_the_audit_log(self, client, ops):
        """Deliberate: the body is already a row, and duplicating free text into
        an append-only table doubles the places a PII purge has to reach."""
        d = _open(client, ops)
        secret_text = "passenger's mobile is +85298765432"
        client.post(
            f"/api/v1/admin/disputes/{d['id']}/messages",
            headers=ops,
            json={"body": secret_text},
        )
        rows = await _fetch(
            client,
            "SELECT payload FROM admin_audit_log WHERE event = 'ADMIN_DISPUTE_MESSAGE'",
        )
        assert len(rows) == 1
        assert secret_text not in str(rows[0]["payload"])
        assert rows[0]["payload"]["length"] == len(secret_text)

    async def test_an_empty_body_is_refused(self, client, ops):
        d = _open(client, ops)
        r = client.post(
            f"/api/v1/admin/disputes/{d['id']}/messages",
            headers=ops,
            json={"body": "  "},
        )
        assert r.status_code == 400
        assert r.json()["details"]["reason"] == "DISPUTE_MESSAGE_EMPTY"

    async def test_a_party_message_on_a_closed_case_is_refused(self, client, ops, finance):
        """A closed case must not accumulate statements nobody is obliged to
        answer. An internal note is still allowed — post-mortem discussion is
        normal."""
        d = _open(client, ops)
        client.post(
            f"/api/v1/admin/disputes/{d['id']}/resolve",
            headers=finance,
            json={"resolution": "NONE", "note": "no charge", "close": True},
        )
        blocked = client.post(
            f"/api/v1/admin/disputes/{d['id']}/messages",
            headers=ops,
            json={"body": "one more thing"},
        )
        assert blocked.status_code == 400
        assert blocked.json()["details"]["reason"] == "DISPUTE_ALREADY_CLOSED"

        allowed = client.post(
            f"/api/v1/admin/disputes/{d['id']}/messages",
            headers=ops,
            json={"body": "note for the file", "is_internal": True},
        )
        assert allowed.status_code == 201

    async def test_the_thread_is_ordered_oldest_first(self, client, ops):
        d = _open(client, ops, summary="first")
        for body in ("second", "third"):
            client.post(
                f"/api/v1/admin/disputes/{d['id']}/messages",
                headers=ops,
                json={"body": body},
            )
        detail = client.get(f"/api/v1/admin/disputes/{d['id']}", headers=ops).json()
        assert [m["body"] for m in detail["messages"]] == ["first", "second", "third"]


class TestAssignment:
    async def test_assigning_moves_open_to_investigating(self, client, ops):
        d = _open(client, ops)
        assert d["status"] == "OPEN"
        body = client.post(f"/api/v1/admin/disputes/{d['id']}/assign", headers=ops).json()
        assert body["status"] == "INVESTIGATING"
        assert body["assigned_admin_id"] is not None

    async def test_the_previous_assignee_is_in_the_audit_payload(self, client, ops, finance):
        """Re-assignment is how a case gets bounced. "Who had it before" is the
        question asked when it turns out nothing happened for three days."""
        d = _open(client, ops)
        client.post(f"/api/v1/admin/disputes/{d['id']}/assign", headers=ops)
        first = client.get(f"/api/v1/admin/disputes/{d['id']}", headers=ops).json()
        client.post(f"/api/v1/admin/disputes/{d['id']}/assign", headers=finance)

        rows = await _fetch(
            client,
            "SELECT payload FROM admin_audit_log "
            "WHERE event = 'ADMIN_DISPUTE_ASSIGN' ORDER BY created_at",
        )
        assert len(rows) == 2
        assert rows[0]["payload"]["from"] is None
        assert rows[1]["payload"]["from"] == first["assigned_admin_id"]

    async def test_a_closed_case_cannot_be_reassigned(self, client, ops, finance):
        d = _open(client, ops)
        client.post(
            f"/api/v1/admin/disputes/{d['id']}/resolve",
            headers=finance,
            json={"resolution": "NONE", "note": "done", "close": True},
        )
        r = client.post(f"/api/v1/admin/disputes/{d['id']}/assign", headers=ops)
        assert r.status_code == 400
        assert r.json()["details"]["reason"] == "DISPUTE_ALREADY_CLOSED"


class TestResolution:
    async def test_a_none_resolution_needs_no_money_role(self, client, ops):
        """Judging that nobody is charged is exactly OPERATIONS' call."""
        d = _open(client, ops)
        r = client.post(
            f"/api/v1/admin/disputes/{d['id']}/resolve",
            headers=ops,
            json={"resolution": "NONE", "note": "the fare was correct"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolution"] == "NONE"
        assert body["status"] == "RESOLVED"
        assert body["moves_money"] is False

    @pytest.mark.parametrize(
        "resolution",
        ["CHARGE_PASSENGER", "CHARGE_DRIVER", "REFUND_PLATFORM_FEE", "WAIVED_PLATFORM_FEE"],
    )
    async def test_every_money_resolution_needs_finance(self, client, ops, resolution):
        """This is the separation-of-duties test. The operator who judges the
        conduct must not be able to authorise the payout — otherwise
        'approved by operations' is a payment authorisation."""
        d = _open(client, ops)
        r = client.post(
            f"/api/v1/admin/disputes/{d['id']}/resolve",
            headers=ops,
            json={"resolution": resolution, "note": "looks bad"},
        )
        assert r.status_code == 403
        assert r.json()["details"]["reason"] == "DISPUTE_RESOLUTION_REQUIRES_FINANCE"
        # And nothing was recorded — a refused decision is not a decision.
        detail = client.get(f"/api/v1/admin/disputes/{d['id']}", headers=ops).json()
        assert detail["resolution"] is None
        assert detail["status"] == "OPEN"

    async def test_finance_can_resolve_and_it_is_audited(self, client, ops, finance):
        d = _open(client, ops)
        r = client.post(
            f"/api/v1/admin/disputes/{d['id']}/resolve",
            headers=finance,
            json={"resolution": "CHARGE_DRIVER", "note": "detour confirmed on GPS"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["moves_money"] is True

        rows = await _fetch(
            client,
            "SELECT payload FROM admin_audit_log WHERE event = 'ADMIN_DISPUTE_RESOLVE'",
        )
        assert len(rows) == 1
        # The note IS stored here, unlike a message body: it is the decision's
        # justification, and "resolved as CHARGE_DRIVER" alone cannot answer why.
        assert rows[0]["payload"]["note"] == "detour confirmed on GPS"
        assert rows[0]["payload"]["moves_money"] is True

    async def test_support_cannot_resolve_even_nothing(self, client, ops, support):
        d = _open(client, ops)
        r = client.post(
            f"/api/v1/admin/disputes/{d['id']}/resolve",
            headers=support,
            json={"resolution": "NONE", "note": "x"},
        )
        assert r.status_code == 403
        assert r.json()["details"]["reason"] == "ADMIN_ROLE_INSUFFICIENT"

    async def test_a_note_is_required_even_for_none(self, client, ops):
        """'Nobody is charged' without a reason is the decision that gets
        re-litigated — the next reader cannot tell considered from forgotten."""
        d = _open(client, ops)
        r = client.post(
            f"/api/v1/admin/disputes/{d['id']}/resolve",
            headers=ops,
            json={"resolution": "NONE", "note": "   "},
        )
        assert r.status_code == 400
        assert r.json()["details"]["reason"] == "DISPUTE_RESOLUTION_NOTE_REQUIRED"

    async def test_a_second_resolution_is_refused(self, client, ops, finance):
        """A money decision that can be silently overwritten is one where the
        first decision does not matter."""
        d = _open(client, ops)
        first = client.post(
            f"/api/v1/admin/disputes/{d['id']}/resolve",
            headers=finance,
            json={"resolution": "CHARGE_DRIVER", "note": "first call"},
        )
        assert first.status_code == 200

        second = client.post(
            f"/api/v1/admin/disputes/{d['id']}/resolve",
            headers=finance,
            json={"resolution": "REFUND_PLATFORM_FEE", "note": "on reflection"},
        )
        assert second.status_code == 400
        assert second.json()["details"]["reason"] == "DISPUTE_ALREADY_RESOLVED"
        # The first decision still stands.
        detail = client.get(f"/api/v1/admin/disputes/{d['id']}", headers=ops).json()
        assert detail["resolution"] == "CHARGE_DRIVER"
        assert detail["resolution_note"] == "first call"

    async def test_the_resolver_is_recorded(self, client, ops, finance):
        d = _open(client, ops)
        client.post(
            f"/api/v1/admin/disputes/{d['id']}/resolve",
            headers=finance,
            json={"resolution": "NONE", "note": "ok"},
        )
        # The console's own identity endpoint is `GET /api/v1/auth/me`, shared
        # with passengers and returning `AdminMeOut` for an admin token — there
        # is deliberately no `/admin/auth/me`.
        me = client.get("/api/v1/auth/me", headers=finance).json()
        detail = client.get(f"/api/v1/admin/disputes/{d['id']}", headers=ops).json()
        assert detail["resolved_by"] == me["id"]
        assert detail["resolved_at"] is not None


class TestStatusTransitions:
    @pytest.mark.parametrize("target", ["INVESTIGATING", "AWAITING_PARTY", "ESCALATED", "CLOSED"])
    async def test_operations_can_move_between_non_terminal_states(self, client, ops, target):
        d = _open(client, ops)
        r = client.post(
            f"/api/v1/admin/disputes/{d['id']}/status",
            headers=ops,
            json={"status": target},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == target

    async def test_resolved_cannot_be_reached_by_a_status_flip(self, client, ops):
        """Resolution carries a decision, a note and an actor. A flip would
        produce a case that reads as decided with none of them recorded."""
        d = _open(client, ops)
        r = client.post(
            f"/api/v1/admin/disputes/{d['id']}/status",
            headers=ops,
            json={"status": "RESOLVED"},
        )
        assert r.status_code == 422  # the pattern excludes it at the edge

    async def test_a_resolved_case_can_only_be_closed(self, client, ops, finance):
        d = _open(client, ops)
        client.post(
            f"/api/v1/admin/disputes/{d['id']}/resolve",
            headers=finance,
            json={"resolution": "NONE", "note": "done"},
        )
        reopened = client.post(
            f"/api/v1/admin/disputes/{d['id']}/status",
            headers=ops,
            json={"status": "INVESTIGATING"},
        )
        assert reopened.status_code == 400
        assert reopened.json()["details"]["reason"] == "DISPUTE_ALREADY_RESOLVED"

        closed = client.post(
            f"/api/v1/admin/disputes/{d['id']}/status",
            headers=ops,
            json={"status": "CLOSED"},
        )
        assert closed.status_code == 200
        assert closed.json()["status"] == "CLOSED"


class TestAccessControl:
    async def test_reads_are_open_to_every_admin_role(self, client, ops, support):
        d = _open(client, ops)
        for headers in (support, ops):
            assert client.get("/api/v1/admin/disputes", headers=headers).status_code == 200
            assert (
                client.get(f"/api/v1/admin/disputes/{d['id']}", headers=headers).status_code == 200
            )
            assert client.get("/api/v1/admin/disputes/stats", headers=headers).status_code == 200

    async def test_an_unauthenticated_caller_is_a_401(self, client):
        assert client.get("/api/v1/admin/disputes").status_code == 401

    async def test_a_missing_dispute_is_a_404(self, client, ops):
        r = client.get(f"/api/v1/admin/disputes/{uuid.uuid4()}", headers=ops)
        assert r.status_code == 404
        assert r.json()["code"] == "NOT_FOUND"

    async def test_a_malformed_id_is_a_422(self, client, ops):
        assert client.get("/api/v1/admin/disputes/not-a-uuid", headers=ops).status_code == 422


class TestModelProperties:
    """Unit-level checks on the properties the handlers rely on. These are the
    ones whose failure mode is silent: a wrong `sla_hours` would compute a
    plausible deadline, and a wrong `is_terminal` would hide a case."""

    def test_sla_is_narrowest_at_the_top(self):
        from app.models import DisputeSeverity

        assert DisputeSeverity.SAFETY_CRITICAL.sla_hours == 1
        assert (
            DisputeSeverity.SAFETY_CRITICAL.sla_hours
            < DisputeSeverity.HIGH.sla_hours
            < DisputeSeverity.NORMAL.sla_hours
            < DisputeSeverity.LOW.sla_hours
        )

    def test_only_resolved_and_closed_are_terminal(self):
        from app.models import DisputeStatus

        terminal = {s for s in DisputeStatus if s.is_terminal}
        assert terminal == {DisputeStatus.RESOLVED, DisputeStatus.CLOSED}

    def test_none_is_a_decision_and_the_rest_move_money(self):
        from app.models import DisputeResolution

        assert DisputeResolution.NONE.moves_money is False
        moving = {r for r in DisputeResolution if r.moves_money}
        assert moving == {
            DisputeResolution.CHARGE_PASSENGER,
            DisputeResolution.CHARGE_DRIVER,
            DisputeResolution.REFUND_PLATFORM_FEE,
            DisputeResolution.WAIVED_PLATFORM_FEE,
        }

    def test_an_unknown_status_fails_open_not_closed(self):
        """Unlike `AdminAccount.admin_role`, which fails closed. A case with an
        unreadable status must stay in the queue: assuming terminal would drop
        it silently, and a lost safety complaint is the worst outcome here."""
        from app.models import DisputeStatus, OrderDispute

        row = OrderDispute(
            category="SAFETY",
            summary="x",
            severity="LOW",
            status="SOMETHING_NEWER_THAN_THIS_BUILD",
            sla_due_at=datetime.now(UTC),
        )
        assert row.status_enum is DisputeStatus.OPEN

    def test_an_unknown_severity_falls_back_to_normal(self):
        from app.models import DisputeSeverity, OrderDispute

        row = OrderDispute(
            category="OTHER",
            summary="x",
            severity="EXTREME",
            status="OPEN",
            sla_due_at=datetime.now(UTC),
        )
        assert row.severity_enum is DisputeSeverity.NORMAL
