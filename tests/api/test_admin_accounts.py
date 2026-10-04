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

from app.services.admin.audit_service import (
    EV_ADMIN_ACCOUNT_ACTIVE_CHANGE,
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
                "email": "queue-ops@hkfastdc.com",
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
                "email": "fresh-admin@hkfastdc.com",
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
                "email": "mixed@hkfastdc.com",
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
            "email": "dup1@hkfastdc.com",
            "password": "Harbour-Kite-9pLq",
            "admin_role": "SUPPORT",
        }
        assert (
            client.post(
                "/api/v1/admin/accounts", headers=client.admin_headers(), json=body
            ).status_code
            == 201
        )
        body["email"] = "dup2@hkfastdc.com"
        second = client.post("/api/v1/admin/accounts", headers=client.admin_headers(), json=body)
        assert second.status_code == 400, second.text
        assert second.json()["code"] == "BUSINESS_RULE_VIOLATION"

    async def test_weak_password_is_rejected_with_the_policy_sentence(self, client):
        r = client.post(
            "/api/v1/admin/accounts",
            headers=client.admin_headers(),
            json={
                "username": "weak-pw",
                "email": "weak@hkfastdc.com",
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
                "email": "audited-create@hkfastdc.com",
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
                "email": "sneaky@hkfastdc.com",
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
            from app.services.admin.admin_account_service import AdminAccountService

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
                "e": f"{target_name}@hkfastdc.com",
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

    async def test_reset_revokes_the_sessions_it_reports(self, client):
        """The route reports `sessions_revoked: true`, so it has to be true.

        A reset is what you do when a credential is compromised or an operator
        leaves, and a reset that leaves the old sessions alive does neither. The
        console branches on this boolean (`AccountsPage.tsx`) to tell the operator
        which of the two happened, so a `true` that was not earned is worse than
        no field at all.

        Both halves of the revocation are checked, and neither by asserting that
        a Redis key exists:

          * the access token already in the caller's hand — used *after* the
            reset, which exercises the real epoch through
            `deps.assert_not_revoked` on the hot path;
          * the refresh rows, so the family cannot rotate a new session.
        """
        target = client.admin_headers(role="SUPPORT")
        # Copied before the reset: these headers *are* the session already in
        # flight that this test is about.
        already_issued = dict(target)

        r = client.post(
            f"/api/v1/admin/accounts/{target.admin_id}/password/reset",
            headers=client.admin_headers(),
            json={"new_password": "New-Harbour-Kite-4xZ"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["sessions_revoked"] is True

        # The access token that resolved a principal a moment ago no longer does.
        stale = client.get("/api/v1/auth/me", headers=already_issued)
        assert stale.status_code == 401, stale.text

        # And the refresh family is stamped rather than left rotatable.
        rows = await _fetch(
            client,
            "SELECT revoked_at FROM admin_refresh_tokens WHERE admin_id = CAST(:i AS uuid)",
            {"i": target.admin_id},
        )
        assert rows, "the login walked the real flow, so refresh rows exist"
        assert all(row["revoked_at"] is not None for row in rows), rows

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


class TestAdminActiveChange:
    """`PATCH /admin/accounts/{id}/active` — the route that makes `is_active` real.

    `require_admin` and `require_live_principal` both re-read `is_active` on
    every request (P0-3), and `_count_supers` already ignores inactive rows.
    Every one of those guards was written and none had ever fired, because
    nothing in the codebase could set the column. A reset password does not
    cover this: a lost credential and a departed operator are different
    incidents, and only the second one wants the account gone.
    """

    async def test_deactivating_revokes_the_access_token_and_the_refresh_rows(self, client):
        """Both halves of the eviction, and neither asserted by reading a column.

        The access token dies at the revocation *epoch*, not at the `is_active`
        read: `get_current_user` consults the epoch before the row is loaded, so
        a deactivation that wrote only the column would leave a live token until
        it expired. The response says which half fired (`token has been
        revoked`), which is why the assertion pins the message and not just the
        status — 403 here would mean the epoch write did not happen.

        The refresh row is the half that cannot be left to `is_active` at all:
        it is rotated rather than re-read, so an unstamped row keeps minting
        fresh access tokens for its full lifetime.
        """
        target = client.admin_headers(role="SUPPORT")
        already_issued = dict(target)

        r = client.patch(
            f"/api/v1/admin/accounts/{target.admin_id}/active",
            headers=client.admin_headers(),
            json={"is_active": False},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == target.admin_id
        assert body["is_active"] is False
        assert body["previous_is_active"] is True
        assert body["sessions_revoked"] is True

        stale = client.get("/api/v1/auth/me", headers=already_issued)
        assert stale.status_code == 401, stale.text
        assert stale.json()["message"] == "token has been revoked"

        rows = await _fetch(
            client,
            "SELECT revoked_at FROM admin_refresh_tokens WHERE admin_id = CAST(:i AS uuid)",
            {"i": target.admin_id},
        )
        assert rows, "the login walked the real flow, so a refresh row exists"
        assert all(row["revoked_at"] is not None for row in rows), rows

    async def test_a_deactivated_row_is_refused_even_with_an_unrevoked_token(self, client):
        """The `is_active` half, isolated from the epoch half.

        The route writes both, and the epoch fires first — so the route's own
        test cannot see this guard at all. The column is flipped directly here so
        the token stays valid and the only thing left to refuse it is
        `require_live_principal`'s live read, which is what makes "a disabled
        account loses access the moment is_active flips" true rather than
        aspirational.
        """
        target = client.admin_headers(role="SUPPORT")
        client.exec_sql(
            "UPDATE admin_accounts SET is_active = false WHERE id = CAST(:i AS uuid)",
            {"i": target.admin_id},
        )

        r = client.get("/api/v1/auth/me", headers=dict(target))
        assert r.status_code == 403, r.text
        assert "disabled" in r.text

    async def test_reactivating_restores_the_row_without_revoking_anything(self, client):
        target = client.admin_headers(role="SUPPORT")
        super_headers = client.admin_headers()

        off = client.patch(
            f"/api/v1/admin/accounts/{target.admin_id}/active",
            headers=super_headers,
            json={"is_active": False},
        )
        assert off.status_code == 200, off.text

        on = client.patch(
            f"/api/v1/admin/accounts/{target.admin_id}/active",
            headers=super_headers,
            json={"is_active": True},
        )
        assert on.status_code == 200, on.text
        body = on.json()
        assert body["is_active"] is True
        assert body["previous_is_active"] is False
        # Nothing to revoke: a deactivated account cannot authenticate, so it is
        # holding no session for the reactivation to kill.
        assert body["sessions_revoked"] is False

        roster = client.get("/api/v1/admin/accounts", headers=super_headers)
        row = next(i for i in roster.json()["items"] if i["id"] == target.admin_id)
        assert row["is_active"] is True

    async def test_an_admin_cannot_switch_themselves_off(self, client):
        """The ordering problem, removed rather than solved.

        With a single SUPER_ADMIN the last-admin check is consulted *after* the
        caller has already switched themselves off, and the account cannot even
        authenticate to undo it. Refusing the self case means that order is never
        reached.
        """
        me = client.admin_headers()
        r = client.patch(
            f"/api/v1/admin/accounts/{me.admin_id}/active",
            headers=me,
            json={"is_active": False},
        )
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "BUSINESS_RULE_VIOLATION"
        assert r.json()["message"] == "an admin may not change their own active state"
        # And it survived the attempt.
        assert await _fetch(
            client,
            "SELECT is_active FROM admin_accounts WHERE id = CAST(:i AS uuid)",
            {"i": me.admin_id},
        ) == [{"is_active": True}]

    async def test_the_last_super_admin_cannot_be_switched_off(self, client):
        """Same brick as demoting them — and the premise is proved first.

        Without the first half this would pass against an implementation that
        refuses *every* deactivation, under which the first super admin could
        never be handed over.
        """
        keeper = client.admin_headers()
        spare = client.admin_headers()

        # Premise: with two usable supers, switching one off works.
        ok = client.patch(
            f"/api/v1/admin/accounts/{spare.admin_id}/active",
            headers=keeper,
            json={"is_active": False},
        )
        assert ok.status_code == 200, ok.text
        assert await _count_supers(client) == 1

        # Bring it back so the blocked attempt below has a real second super.
        back = client.patch(
            f"/api/v1/admin/accounts/{spare.admin_id}/active",
            headers=keeper,
            json={"is_active": True},
        )
        assert back.status_code == 200, back.text
        assert await _count_supers(client) == 2

        # Drive the service directly, so the rule under test is the service's
        # business rule and not `_require_super`.
        async with client.db_factory() as session:
            from app.core.exceptions import BusinessRuleError
            from app.services.admin.admin_account_service import AdminAccountService

            service = AdminAccountService(session)
            # Switch `keeper` off, leaving `spare` the sole usable SUPER_ADMIN.
            _, _ = await service.set_active(
                account_id=uuid.UUID(keeper.admin_id),
                is_active=False,
                actor_id=uuid.UUID(spare.admin_id),
            )
            await session.commit()

        assert await _count_supers(client) == 1

        async with client.db_factory() as session:
            from app.core.exceptions import BusinessRuleError

            service = AdminAccountService(session)
            with pytest.raises(BusinessRuleError) as exc:
                await service.set_active(
                    account_id=uuid.UUID(spare.admin_id),
                    is_active=False,
                    actor_id=uuid.UUID(keeper.admin_id),
                )
            assert exc.value.details.get("reason") == "LAST_SUPER_ADMIN"

        # And it survived.
        assert await _fetch(
            client,
            "SELECT is_active FROM admin_accounts WHERE id = CAST(:i AS uuid)",
            {"i": spare.admin_id},
        ) == [{"is_active": True}]

    async def test_lower_roles_cannot_change_active_state(self, client):
        target = client.admin_headers(role="SUPPORT")
        for role in ("SUPPORT", "OPERATIONS", "FINANCE"):
            r = client.patch(
                f"/api/v1/admin/accounts/{target.admin_id}/active",
                headers=client.admin_headers(role=role),
                json={"is_active": False},
            )
            assert r.status_code == 403, (role, r.text)

        # Not one of them got through.
        assert await _fetch(
            client,
            "SELECT is_active FROM admin_accounts WHERE id = CAST(:i AS uuid)",
            {"i": target.admin_id},
        ) == [{"is_active": True}]

    async def test_the_transition_is_audited_from_to(self, client):
        target = client.admin_headers(role="SUPPORT")
        super_headers = client.admin_headers()

        r = client.patch(
            f"/api/v1/admin/accounts/{target.admin_id}/active",
            headers=super_headers,
            json={"is_active": False},
        )
        assert r.status_code == 200, r.text

        rows = await _audit_rows(client, EV_ADMIN_ACCOUNT_ACTIVE_CHANGE)
        assert len(rows) == 1, rows
        payload = rows[0]["payload"]
        assert payload["account_id"] == target.admin_id
        # The row holds only the new value, so from/to is the whole record of
        # what happened — the same reason `AdminRoleChangeOut` echoes
        # `previous_role`.
        assert payload["from"] is True
        assert payload["to"] is False
        # The count *after* the change: the number an operator needs at the
        # moment they switch a SUPER_ADMIN off.
        assert payload["super_admin_count"] == 1
