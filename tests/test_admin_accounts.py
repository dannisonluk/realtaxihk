"""Admin account administration: who may manage whom, and who may not.

Three groups of assertions, and the second and third are the ones that matter:

1. The happy paths — create, change a role, reset a password.
2. **The refusals.** Every one of these is a test that an action the route
   *would* have performed is declined, so they are only meaningful if the
   action would otherwise have succeeded. `TestLastSuperAdminCannotBeRemoved`
   in particular proves its own premise: it demotes a super admin successfully
   first, then shows the same call failing once one is left.
3. **The audit rows.** A role change is the one action where the resulting row
   cannot tell you what happened — it holds only the new value — so the
   from/to pair in the audit payload is not a nicety.

The refusal tests use `client.admin_headers(role=...)` rather than a fabricated
token, so they exercise the real login path and the real `require_role`
dependency. A hand-minted token would test the fixture.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.services.audit_service import (
    EV_ADMIN_ACCOUNT_CREATE,
    EV_ADMIN_PASSWORD_RESET,
    EV_ADMIN_ROLE_CHANGE,
)


async def _fetch(client, sql: str, params: dict | None = None) -> list[dict]:
    async with client.db_factory() as session:
        result = await session.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]


async def _audit_rows(client, event: str) -> list[dict]:
    return await _fetch(
        client,
        "SELECT event, username_attempted, detail, payload FROM admin_audit_log "
        "WHERE event = :e ORDER BY created_at DESC",
        {"e": event},
    )


async def _role_of(client, admin_id: str) -> str:
    rows = await _fetch(
        client,
        "SELECT role FROM admin_accounts WHERE id = CAST(:i AS uuid)",
        {"i": admin_id},
    )
    assert len(rows) == 1, f"expected exactly one account with id {admin_id}"
    return rows[0]["role"]


class TestCreatingAnAccount:
    async def test_create_returns_the_new_accounts_identity(self, client):
        headers = client.admin_headers()
        r = client.post(
            "/api/v1/admin/accounts",
            headers=headers,
            json={
                "username": "queue-ops",
                "email": "queue-ops@realtaxi.hk",
                "password": "Harbour-Kite-9pLq",
                "full_name": "Queue Operator",
                "admin_role": "OPERATIONS",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["username"] == "queue-ops"
        assert body["admin_role"] == "OPERATIONS"
        assert body["is_active"] is True
        # The account exists but cannot be used yet — it has no second factor.
        assert body["totp_enrolled"] is False
        assert body["totp_enrolment_pending"] is True

    async def test_new_account_has_no_totp_secret_yet(self, client):
        """Enrolment is what makes the account usable, and it has not happened.

        Stated as its own test because "created" reading as "can log in now" is
        the mistake this field exists to prevent — an operator would hand over
        credentials that do not work.
        """
        client.post(
            "/api/v1/admin/accounts",
            headers=client.admin_headers(),
            json={
                "username": "fresh-admin",
                "email": "fresh-admin@realtaxi.hk",
                "password": "Harbour-Kite-9pLq",
                "admin_role": "SUPPORT",
            },
        )
        rows = await _fetch(
            client,
            "SELECT totp_secret, password_hash FROM admin_accounts WHERE username = :u",
            {"u": "fresh-admin"},
        )
        assert rows[0]["totp_secret"] is None
        # And the password is stored hashed, never in the clear.
        assert "Harbour-Kite-9pLq" not in rows[0]["password_hash"]
        assert rows[0]["password_hash"].startswith("$argon2")

    async def test_username_is_normalised_so_login_can_find_it(self, client):
        """Written upper-case, stored lower-case. Both `AdminAccount.username`
        and the login lookup normalise through the same function; if they ever
        disagree the account is unreachable, so this pins the write side."""
        r = client.post(
            "/api/v1/admin/accounts",
            headers=client.admin_headers(),
            json={
                "username": "  Mixed-Case  ",
                "email": "mixed@realtaxi.hk",
                "password": "Harbour-Kite-9pLq",
                "admin_role": "SUPPORT",
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["username"] == "mixed-case"

    async def test_duplicate_username_is_a_400_not_a_500(self, client):
        """The unique index is the arbiter, and losing the race must still be
        an answer the caller can act on. A check-then-insert would race two
        concurrent creates into both seeing "free"."""
        body = {
            "username": "dup-admin",
            "email": "dup1@realtaxi.hk",
            "password": "Harbour-Kite-9pLq",
            "admin_role": "SUPPORT",
        }
        assert (
            client.post(
                "/api/v1/admin/accounts", headers=client.admin_headers(), json=body
            ).status_code
            == 201
        )
        body["email"] = "dup2@realtaxi.hk"
        second = client.post("/api/v1/admin/accounts", headers=client.admin_headers(), json=body)
        assert second.status_code == 400, second.text
        assert second.json()["code"] == "BUSINESS_RULE_VIOLATION"

    async def test_weak_password_is_rejected_with_the_policy_sentence(self, client):
        r = client.post(
            "/api/v1/admin/accounts",
            headers=client.admin_headers(),
            json={
                "username": "weak-pw",
                "email": "weak@realtaxi.hk",
                "password": "short",
                "admin_role": "SUPPORT",
            },
        )
        assert r.status_code == 400, r.text
        assert "12 characters" in r.json()["message"]

    async def test_creation_is_audited_and_never_records_the_password(self, client):
        client.post(
            "/api/v1/admin/accounts",
            headers=client.admin_headers(),
            json={
                "username": "audited-create",
                "email": "audited-create@realtaxi.hk",
                "password": "Harbour-Kite-9pLq",
                "admin_role": "FINANCE",
            },
        )
        rows = await _audit_rows(client, EV_ADMIN_ACCOUNT_CREATE)
        assert len(rows) == 1
        assert rows[0]["payload"]["created_username"] == "audited-create"
        assert rows[0]["payload"]["admin_role"] == "FINANCE"
        # The password appears nowhere in the row — neither the plaintext nor
        # the hash. This log is readable by every role.
        blob = f"{rows[0]['detail']} {rows[0]['payload']}"
        assert "Harbour-Kite-9pLq" not in blob
        assert "$argon2" not in blob


class TestOnlySuperAdminMayManageAccounts:
    """Every route in this group is SUPER_ADMIN-only. The refusals have to be
    tested with a real under-privileged token — that is the whole control."""

    @pytest.mark.parametrize("role", ["SUPPORT", "OPERATIONS", "FINANCE"])
    async def test_lower_roles_cannot_list_accounts(self, client, role):
        headers = client.admin_headers(role=role)
        r = client.get("/api/v1/admin/accounts", headers=headers)
        assert r.status_code == 403, r.text
        assert r.json()["code"] == "FORBIDDEN"

    @pytest.mark.parametrize("role", ["SUPPORT", "OPERATIONS", "FINANCE"])
    async def test_lower_roles_cannot_change_a_role(self, client, role):
        """This is the one that would defeat the whole hierarchy: an OPERATIONS
        account promoting itself makes every other guard decorative."""
        victim = client.admin_headers(role="SUPPORT")
        r = client.patch(
            f"/api/v1/admin/accounts/{victim.admin_id}/role",
            headers=client.admin_headers(role=role),
            json={"admin_role": "SUPER_ADMIN"},
        )
        assert r.status_code == 403, r.text
        # And the role was not changed on the way to the refusal.
        assert (await _role_of(client, victim.admin_id)) == "SUPPORT"

    @pytest.mark.parametrize("role", ["SUPPORT", "OPERATIONS", "FINANCE"])
    async def test_lower_roles_cannot_create_accounts(self, client, role):
        r = client.post(
            "/api/v1/admin/accounts",
            headers=client.admin_headers(role=role),
            json={
                "username": "sneaky",
                "email": "sneaky@realtaxi.hk",
                "password": "Harbour-Kite-9pLq",
                "admin_role": "SUPER_ADMIN",
            },
        )
        assert r.status_code == 403, r.text

    async def test_plain_admin_can_still_read_the_audit_log(self, client):
        """The complement of the above: `/audit` stays readable by everyone.

        Restricting the audit trail to the most privileged role would mean the
        people least able to change anything are also the least able to notice
        that something was changed. Asserted alongside the refusals so a future
        "lock it all down" change breaks a test that explains why not.
        """
        r = client.get("/api/v1/admin/audit", headers=client.admin_headers(role="SUPPORT"))
        assert r.status_code == 200, r.text


class TestNobodyChangesTheirOwnRole:
    async def test_self_demotion_is_refused(self, client):
        """Refusing the downward direction is not politeness — it removes an
        ordering problem. With one SUPER_ADMIN the "am I the last one?" check
        is consulted *after* the change has already been applied to self."""
        headers = client.admin_headers()
        r = client.patch(
            f"/api/v1/admin/accounts/{headers.admin_id}/role",
            headers=headers,
            json={"admin_role": "OPERATIONS"},
        )
        assert r.status_code == 400, r.text
        assert "own role" in r.json()["message"]
        assert (await _role_of(client, headers.admin_id)) == "SUPER_ADMIN"


class TestLastSuperAdminCannotBeRemoved:
    async def test_the_last_super_admin_cannot_be_demoted(self, client):
        """The premise is proved first: the same call succeeds while another
        SUPER_ADMIN exists.

        Without that first half this test would pass against an implementation
        that refuses *every* demotion — a different and broken rule, under which
        the first super admin is frozen in place forever and a legitimate
        handover is impossible.
        """
        actor = client.admin_headers()  # the caller, SUPER_ADMIN
        target = client.admin_headers()  # the one to be demoted

        # Premise: with two supers, demoting one works.
        ok = client.patch(
            f"/api/v1/admin/accounts/{target.admin_id}/role",
            headers=actor,
            json={"admin_role": "OPERATIONS"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["previous_role"] == "SUPER_ADMIN"
        assert ok.json()["admin_role"] == "OPERATIONS"
        assert ok.json()["super_admin_count"] == 1

        # `actor` is now the only SUPER_ADMIN. Nobody may change their own role,
        # so a fresh super admin is needed as the caller for the blocked attempt.
        caller = client.admin_headers()
        assert await _count_supers(client) == 2

        # Two remain, so this one still succeeds and leaves exactly one.
        first_step = client.patch(
            f"/api/v1/admin/accounts/{actor.admin_id}/role",
            headers=caller,
            json={"admin_role": "SUPPORT"},
        )
        assert first_step.status_code == 200, first_step.text
        assert first_step.json()["super_admin_count"] == 1

        # `caller` is now the last active SUPER_ADMIN. A second super admin is
        # created so that the blocked call has a legitimate actor.
        witness = client.admin_headers()
        assert await _count_supers(client) == 2

        # Demote the witness: caller is the only one left, and is not the target.
        step = client.patch(
            f"/api/v1/admin/accounts/{witness.admin_id}/role",
            headers=caller,
            json={"admin_role": "SUPPORT"},
        )
        assert step.status_code == 200, step.text
        assert step.json()["super_admin_count"] == 1

        # Now drive the service directly, so the last-admin rule is exercised
        # rather than the dependency. `caller` acts on `spare`, and both the
        # caller and the check are real.
        spare = client.admin_headers()
        assert await _count_supers(client) == 2

        async with client.db_factory() as session:
            from app.models import AdminRole
            from app.services.admin_account_service import AdminAccountService

            service = AdminAccountService(session)
            # Remove the caller's super status first, leaving `spare` the sole
            # SUPER_ADMIN — done through the service so the count stays honest.
            _, _ = await service.change_role(
                account_id=uuid.UUID(caller.admin_id),
                new_role=AdminRole.FINANCE,
                actor_id=uuid.UUID(spare.admin_id),
            )
            await session.commit()

        assert await _count_supers(client) == 1

        # The last one cannot be lowered, tried from a FINANCE actor so the
        # refusal is the *service's* business rule and not `require_role`.
        async with client.db_factory() as session:
            from app.core.exceptions import BusinessRuleError

            service = AdminAccountService(session)
            with pytest.raises(BusinessRuleError) as exc:
                await service.change_role(
                    account_id=uuid.UUID(spare.admin_id),
                    new_role=AdminRole.SUPPORT,
                    actor_id=uuid.UUID(caller.admin_id),
                )
            assert exc.value.details.get("reason") == "LAST_SUPER_ADMIN"

        # And it survived the attempt.
        assert (await _role_of(client, spare.admin_id)) == "SUPER_ADMIN"

    async def test_inactive_super_admins_do_not_count(self, client):
        """A deactivated SUPER_ADMIN is not a way to grant roles.

        Counting inactive rows would let the last usable super admin be demoted
        while a disabled name kept the check satisfied — a system nobody can
        administer, and an error message insisting otherwise.
        """
        keeper = client.admin_headers()
        stale = client.admin_headers()
        client.exec_sql(
            "UPDATE admin_accounts SET is_active = false WHERE id = CAST(:i AS uuid)",
            {"i": stale.admin_id},
        )
        # Two SUPER_ADMIN values sit in the table, but only one is usable.
        assert (await _count_supers(client)) == 1
        r = client.get("/api/v1/admin/accounts", headers=keeper)
        assert r.status_code == 200, r.text
        assert r.json()["super_admin_count"] == 1


async def _count_supers(client) -> int:
    rows = await _fetch(
        client,
        "SELECT count(*) AS n FROM admin_accounts WHERE role = 'SUPER_ADMIN' AND is_active = true",
    )
    return rows[0]["n"]


class TestRoleChangeIsAudited:
    async def test_the_actor_is_named_on_the_row(self, client):
        """`Principal` carries no username, so this row's name has to be looked
        up. Left as the first draft, `actor_username` was null on every account
        event — and a from/to role change is exactly the row where "who did
        this" has to be readable without a join."""
        actor = client.admin_headers()
        target = client.admin_headers(role="SUPPORT")
        client.patch(
            f"/api/v1/admin/accounts/{target.admin_id}/role",
            headers=actor,
            json={"admin_role": "FINANCE"},
        )
        rows = await _audit_rows(client, EV_ADMIN_ROLE_CHANGE)
        assert rows[0]["username_attempted"], "the actor must be named on the row"
        # And it names the actor, not the target.
        target_name = (
            await _fetch(
                client,
                "SELECT username FROM admin_accounts WHERE id = CAST(:i AS uuid)",
                {"i": target.admin_id},
            )
        )[0]["username"]
        assert rows[0]["username_attempted"] != target_name

    async def test_the_transition_is_recorded_because_the_row_cannot_show_it(self, client):
        actor = client.admin_headers()
        target = client.admin_headers(role="SUPPORT")
        r = client.patch(
            f"/api/v1/admin/accounts/{target.admin_id}/role",
            headers=actor,
            json={"admin_role": "FINANCE"},
        )
        assert r.status_code == 200, r.text

        rows = await _audit_rows(client, EV_ADMIN_ROLE_CHANGE)
        assert len(rows) == 1
        payload = rows[0]["payload"]
        assert payload["from"] == "SUPPORT"
        assert payload["to"] == "FINANCE"
        # The response and the audit row must agree about what happened.
        assert r.json()["previous_role"] == payload["from"]
        assert r.json()["admin_role"] == payload["to"]

    async def test_setting_the_same_role_writes_nothing(self, client):
        """A no-op that writes an audit row makes the trail harder to read:
        it says something happened when the state did not change."""
        actor = client.admin_headers()
        target = client.admin_headers(role="FINANCE")
        r = client.patch(
            f"/api/v1/admin/accounts/{target.admin_id}/role",
            headers=actor,
            json={"admin_role": "FINANCE"},
        )
        assert r.status_code == 400, r.text
        assert await _audit_rows(client, EV_ADMIN_ROLE_CHANGE) == []


class TestPasswordReset:
    async def test_reset_clears_lockout_and_does_not_touch_the_second_factor(self, client):
        """A lost password and a lost authenticator are different incidents.

        Clearing TOTP here would turn one compromised credential into full
        account takeover — the second factor is exactly what stops that.
        """
        target = client.admin_headers(role="SUPPORT")
        client.exec_sql(
            "UPDATE admin_accounts SET failed_login_count = 7, "
            "locked_until = now() + interval '1 hour' "
            "WHERE id = CAST(:i AS uuid)",
            {"i": target.admin_id},
        )
        before = await _fetch(
            client,
            "SELECT totp_secret, failed_login_count, locked_until FROM admin_accounts "
            "WHERE id = CAST(:i AS uuid)",
            {"i": target.admin_id},
        )
        assert before[0]["totp_secret"], "the fixture enrols TOTP, so there is one to preserve"

        r = client.post(
            f"/api/v1/admin/accounts/{target.admin_id}/password/reset",
            headers=client.admin_headers(),
            json={"new_password": "New-Harbour-Kite-4xZ"},
        )
        assert r.status_code == 200, r.text

        after = await _fetch(
            client,
            "SELECT totp_secret, failed_login_count, locked_until FROM admin_accounts "
            "WHERE id = CAST(:i AS uuid)",
            {"i": target.admin_id},
        )
        # The lock is gone, because a reset is what you do for someone locked out.
        assert after[0]["failed_login_count"] == 0
        assert after[0]["locked_until"] is None
        # The second factor is untouched.
        assert after[0]["totp_secret"] == before[0]["totp_secret"]

    async def test_the_new_password_actually_works(self, client):
        """Asserting the column changed would not prove the reset did anything
        a user would notice. This logs in with it."""
        target_name = "reset-target"
        client.exec_sql(
            "INSERT INTO admin_accounts "
            "(id, username, email, full_name, password_hash, totp_secret, totp_enrolled_at, "
            " is_active, failed_login_count, role, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), :u, :e, 'Reset Target', :pw, NULL, NULL, true, 0, "
            " 'SUPPORT', now(), now())",
            {
                "id": str(uuid.uuid4()),
                "u": target_name,
                "e": f"{target_name}@realtaxi.hk",
                "pw": _hash("Old-Harbour-Kite-1aB"),
            },
        )
        target = await _fetch(
            client, "SELECT id FROM admin_accounts WHERE username = :u", {"u": target_name}
        )
        r = client.post(
            f"/api/v1/admin/accounts/{target[0]['id']}/password/reset",
            headers=client.admin_headers(),
            json={"new_password": "New-Harbour-Kite-4xZ"},
        )
        assert r.status_code == 200, r.text

        # The old one is dead...
        old = client.post(
            "/api/v1/admin/auth/login",
            json={"username": target_name, "password": "Old-Harbour-Kite-1aB"},
        )
        assert old.status_code >= 400, old.text
        # ...and the new one is live, landing on the enrolment challenge since
        # this account has no TOTP secret.
        new = client.post(
            "/api/v1/admin/auth/login",
            json={"username": target_name, "password": "New-Harbour-Kite-4xZ"},
        )
        assert new.status_code == 200, new.text

    async def test_reset_is_audited_without_the_password(self, client):
        target = client.admin_headers(role="SUPPORT")
        client.post(
            f"/api/v1/admin/accounts/{target.admin_id}/password/reset",
            headers=client.admin_headers(),
            json={"new_password": "New-Harbour-Kite-4xZ"},
        )
        rows = await _audit_rows(client, EV_ADMIN_PASSWORD_RESET)
        assert len(rows) == 1
        blob = f"{rows[0]['detail']} {rows[0]['payload']}"
        assert "New-Harbour-Kite-4xZ" not in blob
        assert "$argon2" not in blob

    async def test_lower_roles_cannot_reset_a_password(self, client):
        target = client.admin_headers(role="SUPPORT")
        r = client.post(
            f"/api/v1/admin/accounts/{target.admin_id}/password/reset",
            headers=client.admin_headers(role="OPERATIONS"),
            json={"new_password": "New-Harbour-Kite-4xZ"},
        )
        assert r.status_code == 403, r.text


def _hash(password: str) -> str:
    from app.core.passwords import hash_password

    return hash_password(password)


class TestRosterShape:
    async def test_roster_reports_the_super_admin_count_the_ui_needs(self, client):
        """The count must be derivable from the rows, not assumed.

        Two accounts are created with explicit lower roles and the caller is a
        SUPER_ADMIN, so the count is 1 out of 3. Asserted against the actual
        roster rather than a literal, so the two cannot drift.
        """
        client.admin_headers(role="FINANCE")
        client.admin_headers(role="SUPPORT")
        r = client.get("/api/v1/admin/accounts", headers=client.admin_headers())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == len(body["items"]) == 3
        # The count is over the rows the response already carries, so a UI can
        # cross-check it without a second request.
        derived = sum(1 for i in body["items"] if i["admin_role"] == "SUPER_ADMIN")
        assert body["super_admin_count"] == derived == 1
        assert {i["admin_role"] for i in body["items"]} == {"SUPER_ADMIN", "FINANCE", "SUPPORT"}

    async def test_roster_never_returns_a_credential(self, client):
        client.admin_headers()
        r = client.get("/api/v1/admin/accounts", headers=client.admin_headers())
        blob = r.text
        assert "totp_secret" not in blob
        assert "password_hash" not in blob
        assert "$argon2" not in blob
