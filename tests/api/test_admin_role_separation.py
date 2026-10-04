"""Separation of duties, asserted at the route boundary.

`app/api/admin/_roles.py` states the policy in one place:

    KYC / driver state  -> OPERATIONS
    money movement      -> FINANCE

Three routes were reachable by **any** administrator instead — the licence
decision, the fleet settlement run, and removing a fleet member — because they
carried the read-level `require_admin` rather than a role guard. A SUPPORT
operator, whose job is answering questions, could therefore approve a driver's
professional licence and start a charge run over a fleet.

Why the assertions are shaped this way
--------------------------------------
Each class asserts the refusal **and** a positive control. A lone
`assert r.status_code == 403` passes for the wrong reasons too — a typo'd path
returns 404, which is not 403, so the test would fail loudly rather than pass
silently; but a route that 403s for an unrelated reason (a missing fixture row,
a disabled account) would satisfy it while proving nothing about the role. The
control asserts a *different* status for a role that should get through, which
is what makes the 403 attributable to the role.

The guard runs before the handler, so none of this needs fixture rows: the
control proves the role got past the guard by observing that the failure it met
was the handler's, not the guard's.

Rank, not set membership
------------------------
`require_role` admits every role that *outranks* its argument, so FINANCE
reaches the OPERATIONS routes. `test_finance_outranks_operations_on_a_licence_
decision` pins that deliberately: it is the behaviour the factory documents,
and it is the opposite of what the `finance` fixture's own docstring claims.
"""

from __future__ import annotations

import uuid

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _mk_fleet(client, name: str) -> dict:
    """A fleet, created by the fixture's SUPER_ADMIN.

    No driver is involved: every route under test refuses (or admits) *before*
    the handler looks anything up, so a fleet row is the only setup needed.
    """
    r = client.post(
        "/api/v1/admin/fleets",
        headers=client.admin_headers(),
        json={
            "name": name,
            "license_no": f"FLEET-{name}",
            "weekly_fee_discount_percent": "0",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _decide(client, headers, submission_id: uuid.UUID):
    return client.post(
        f"/api/v1/admin/licence/submissions/{submission_id}/decide",
        headers=headers,
        json={"approve": True},
    )


def _run_settlement(client, fleet_id: str, headers):
    return client.post(
        f"/api/v1/admin/fleets/{fleet_id}/settlement/run",
        headers=headers,
    )


def _remove_member(client, fleet_id: str, headers):
    return client.delete(
        f"/api/v1/admin/fleets/{fleet_id}/members/{uuid.uuid4()}",
        headers=headers,
    )


# --------------------------------------------------------------------------- #
# the licence decision
# --------------------------------------------------------------------------- #


class TestLicenceDecisionIsOperations:
    """Approving a licence promotes a driver's standing — a compliance act.

    It is the same class of judgement as the KYC review in `admin/drivers.py`,
    which is already OPERATIONS, and it must not be inherited by front-line
    support: answering a question and deciding one are different jobs.
    """

    def test_support_cannot_decide(self, client, support):
        r = _decide(client, support, uuid.uuid4())
        assert r.status_code == 403, r.text

    def test_operations_reaches_the_handler(self, client, ops):
        """Positive control: the guard passed, so the *handler* refused."""
        r = _decide(client, ops, uuid.uuid4())
        assert r.status_code != 403, r.text
        assert r.status_code < 500, r.text

    def test_finance_outranks_operations_on_a_licence_decision(self, client, finance):
        """Rank, not set membership — pinned because the docs disagree.

        `require_role` is a rank comparison, so FINANCE is senior to OPERATIONS
        and reaches this route. The `finance` fixture's docstring says
        "money movement, but not KYC", which the implementation does not
        deliver. Whichever way that is resolved, this test is where it lands.
        """
        r = _decide(client, finance, uuid.uuid4())
        assert r.status_code != 403, r.text


# --------------------------------------------------------------------------- #
# the money routes
# --------------------------------------------------------------------------- #


class TestFleetSettlementIsFinance:
    """A settlement run charges every member — money movement, so FINANCE.

    The platform-wide run in `app/api/admin/settlement.py` is FINANCE on all
    three of its routes; the per-fleet run moved the same money on a weaker
    guard.
    """

    def test_support_cannot_run_a_settlement(self, client, support):
        fleet = _mk_fleet(client, "SETTLE-SUPPORT")
        r = _run_settlement(client, fleet["id"], support)
        assert r.status_code == 403, r.text

    def test_operations_cannot_run_a_settlement(self, client, ops):
        """Judging conduct is not authorising the charge that follows it."""
        fleet = _mk_fleet(client, "SETTLE-OPS")
        r = _run_settlement(client, fleet["id"], ops)
        assert r.status_code == 403, r.text

    def test_finance_reaches_the_handler(self, client, finance):
        fleet = _mk_fleet(client, "SETTLE-FINANCE")
        r = _run_settlement(client, fleet["id"], finance)
        assert r.status_code != 403, r.text
        assert r.status_code < 500, r.text


class TestFleetRosterChangeIsFinance:
    """Adding and removing a member must not sit on different roles.

    Removing a driver from a roster decides whether the fleet rate applies to
    them — the same commercial question as adding them, and the one this file
    exists for. It was on `require_admin` while adding was on FINANCE, so
    SUPPORT could quietly take a driver off a roster and change what they are
    billed.
    """

    def test_support_cannot_remove_a_member(self, client, support):
        fleet = _mk_fleet(client, "ROSTER-SUPPORT")
        r = _remove_member(client, fleet["id"], support)
        assert r.status_code == 403, r.text

    def test_operations_cannot_remove_a_member(self, client, ops):
        fleet = _mk_fleet(client, "ROSTER-OPS")
        r = _remove_member(client, fleet["id"], ops)
        assert r.status_code == 403, r.text

    def test_add_and_remove_share_a_guard(self, client, finance):
        """Both halves answer to FINANCE — the asymmetry is gone."""
        fleet = _mk_fleet(client, "ROSTER-SYMMETRY")
        add = client.post(
            f"/api/v1/admin/fleets/{fleet['id']}/members",
            headers=finance,
            json={"driver_profile_id": str(uuid.uuid4()), "member_role": "MEMBER"},
        )
        remove = _remove_member(client, fleet["id"], finance)
        assert add.status_code != 403, add.text
        assert remove.status_code != 403, remove.text
