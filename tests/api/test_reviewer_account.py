"""The restricted production reviewer account, and the script that makes it.

Why this file exists
--------------------
Apple and Google both require a working account before they will review a
release, and an auditor needs to watch the product do its job. Neither should get
an account that can do anything *else*, and neither should be handed a real
user's credentials or a permanent key to the platform.

So the claim being tested is not "a script exists" but three specific things:

* **It stops working on its own.** `reviewer_expires_at` is enforced on every
  authenticated request, so the account lapses with nobody having to remember.
  The failure mode that matters is a forgotten revocation, and a date the guard
  reads cannot be forgotten. This is tested from the realistic sequence — sign in
  while valid, then let it lapse — because a token already issued is exactly what
  a stale reviewer holds.
* **It cannot move money.** Structurally, not by policy: value moves through
  `driver_profiles.deposit` and the append-only ledger, both of which hang off a
  driver profile that needs an *admin* KYC decision and an *admin* deposit grant.
  There is no self-service path from passenger to funded driver.
* **It cannot open the console.** The console authenticates against
  `admin_accounts`, a separate table with a mandatory TOTP second factor. A
  `users` row is not an administrator whatever its `role` column says.

The script is run as a **subprocess against this test's database**, not imported
and called. That is deliberate: it is the only way to test the thing an operator
actually runs — argument parsing, the confirmation switch, the exit codes, and a
connection opened by a different process. Calling its internals would test a
different program.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ops" / "create_reviewer_account.py"

REVIEWER_EMAIL = "reviewer@example.hk"
REVIEWER_PASSWORD = "Reviewer-Passw0rd-9"

#: The first member of the reserved range, and therefore what the allocator
#: hands out on an otherwise empty database. `tests/conftest.py` deliberately
#: keeps `ADMIN_PHONE` out of this range so that stays true.
FIRST_REVIEWER_PHONE = "+85200000000"

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
IDENTITY_ME = "/api/v1/identity/me"
ORDERS = "/api/v1/orders"
DRIVER_REGISTER = "/api/v1/drivers/register"

ORDER_BODY = {
    "pickup_lat": 22.3193,
    "pickup_lng": 114.1694,
    "dropoff_lat": 22.2783,
    "dropoff_lng": 114.1747,
    "pickup_address": "Tsim Sha Tsui",
    "dropoff_address": "Central",
    "distance_km": 8.5,
    "taxi_type": "URBAN",
}

DRIVER_BODY = {
    "hk_id_last4": "4321",
    "taxi_driver_plate_no": "TD9001",
    "vehicle_reg_mark": "RV1001",
    "taxi_type": "URBAN",
}


def _run_script(client, *args: str, allow: str = "true", drop_env: tuple[str, ...] = ()):
    """Run the ops script against *this test's* database.

    The script reads `Settings` from the environment, so pointing it at the
    per-test clone is a matter of overriding the connection variables. Env vars
    beat the repo's `.env` in pydantic-settings, which is what makes the override
    take effect rather than being silently ignored.

    `drop_env` removes variables inherited from the outer test process — needed
    for the one test that asserts what happens when `REVIEWER_PASSWORD` is
    *absent*, which is otherwise impossible to arrange from inside a process that
    already has it set.
    """
    url = urlparse(client.db_url)
    env = {
        **os.environ,
        "APP_ENV": "test",
        "ALLOW_REVIEWER_ACCOUNT": allow,
        "REVIEWER_PASSWORD": REVIEWER_PASSWORD,
        "POSTGRES_HOST": url.hostname or "127.0.0.1",
        "POSTGRES_PORT": str(url.port or 5432),
        "POSTGRES_USER": unquote(url.username or ""),
        "POSTGRES_PASSWORD": unquote(url.password or ""),
        "POSTGRES_DB": (url.path or "/").lstrip("/"),
    }
    for name in drop_env:
        env.pop(name, None)

    # `check=False` deliberately: every call site asserts on `returncode`, and the
    # exit code *is* the contract here (0 created, 1 conflict, 2 bad invocation).
    # `shell=False` is the default, so nothing in the argv is shell-interpreted.
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=180,
        check=False,
    )


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_reviewer(client, *, days: int = 30, email: str = REVIEWER_EMAIL):
    """Provision a reviewer through the script and assert it succeeded."""
    result = _run_script(client, "--email", email, "--days", str(days), "--yes")
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result


def _sign_in(client, email: str = REVIEWER_EMAIL, password: str = REVIEWER_PASSWORD):
    return client.post(LOGIN, json={"email": email, "password": password})


def _token(client, email: str = REVIEWER_EMAIL) -> str:
    response = _sign_in(client, email)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _expire(client, email: str = REVIEWER_EMAIL) -> None:
    """Move the account's expiry into the past, as the clock eventually would."""
    client.exec_sql(
        "UPDATE users SET reviewer_expires_at = now() - interval '1 minute' WHERE email = :e",
        {"e": email},
    )


# --------------------------------------------------------------------------- #
# The two switches
# --------------------------------------------------------------------------- #


def test_the_script_refuses_without_its_own_switch(client):
    """`ALLOW_REVIEWER_ACCOUNT` is a separate switch from the script existing.

    Same shape as `ALLOW_DEV_OTP`, for the same reason: a credential that
    bypasses the normal sign-up path should never be one mistyped environment
    away from being created.
    """
    result = _run_script(client, "--email", REVIEWER_EMAIL, "--yes", allow="false")
    assert result.returncode == 2
    assert "ALLOW_REVIEWER_ACCOUNT" in result.stdout
    assert "created" not in result.stdout


def test_the_switch_is_checked_before_the_password_is_collected(client):
    """Ordering, and it is not cosmetic.

    The other order asks an operator for a secret and then throws it away — and a
    password typed into `getpass` is a password that existed in the process for
    no reason. With the switch off and no `REVIEWER_PASSWORD` in the environment,
    the refusal must come first: there is no TTY here, so a prompt would surface
    as a different error entirely.
    """
    result = _run_script(
        client, "--email", REVIEWER_EMAIL, "--yes", allow="false", drop_env=("REVIEWER_PASSWORD",)
    )
    assert result.returncode == 2
    assert "ALLOW_REVIEWER_ACCOUNT" in result.stdout
    assert "no TTY" not in result.stdout, "the password was collected before the switch was read"


def test_list_on_an_empty_database_says_so(client):
    """A read-only mode that an operator runs first, so it must not create
    anything and must not look broken when there is nothing to show."""
    result = _run_script(client, "--list")
    assert result.returncode == 0, result.stderr
    assert "no reviewer accounts" in result.stdout


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #


def test_the_script_creates_a_usable_reviewer_account(client):
    """End to end: the script writes the row in its own process, and the API
    signs that row in with a password the script never printed."""
    result = _create_reviewer(client)
    assert REVIEWER_EMAIL in result.stdout
    assert FIRST_REVIEWER_PHONE in result.stdout
    assert "reserved range" in result.stdout

    response = _sign_in(client)
    assert response.status_code == 200, response.text
    assert response.json()["created"] is False


def test_the_reviewer_is_complete_so_every_gate_is_behind_it(client):
    """Created verified, not merely created.

    A reviewer cannot receive an OTP or click a link in an inbox we do not own,
    so every gate they are meant to get *past* has to be satisfied at creation —
    otherwise they meet the verification wall and cannot test the product at all.
    """
    _create_reviewer(client)
    token = _token(client)

    profile = client.get(IDENTITY_ME, headers=_auth(token))
    assert profile.status_code == 200, profile.text
    body = profile.json()
    assert body["phone_verified"] is True
    assert body["email_verified"] is True
    assert body["account_status"] == "ACTIVE"
    assert body["phone_reverify_blocked"] is False
    # Ten years out: a deadline the reviewer cannot meet is a deadline they will
    # fail, and P-4's monthly re-verification must never be able to block them.
    assert body["phone_reverify_due"] is False


def test_the_reviewer_can_book_a_taxi(client):
    """The product has to actually work for them, or the review is of an error
    screen."""
    _create_reviewer(client)
    token = _token(client)
    assert client.post(ORDERS, json=ORDER_BODY, headers=_auth(token)).status_code == 201


def test_the_reviewer_cannot_open_the_admin_console(client):
    """Structural, not a policy flag.

    The console authenticates against `admin_accounts` — a separate table with a
    mandatory TOTP second factor and no registration path — and `require_admin`
    reads that table. A `users` row is not an administrator whatever its `role`
    column says, which is why the script can leave `role` at PASSENGER and still
    be safe.
    """
    _create_reviewer(client)
    token = _token(client)
    response = client.get("/api/v1/admin/drivers", headers=_auth(token))
    assert response.status_code == 403, response.text


def test_the_reviewer_cannot_promote_itself_to_a_funded_driver(client):
    """The money barrier, pinned at the first of its two locks.

    Value moves through `driver_profiles.deposit` and the append-only ledger, and
    a driver profile needs an **admin** KYC decision plus an **admin** deposit
    grant. Registering is the only step the reviewer can take on their own, and
    it lands in PENDING_KYC — where it stays, because the next transition is
    somebody else's decision.
    """
    _create_reviewer(client)
    token = _token(client)

    registered = client.post(DRIVER_REGISTER, json=DRIVER_BODY, headers=_auth(token))
    assert registered.status_code == 201, registered.text
    assert registered.json()["status"] == "PENDING_KYC"

    still_pending = client.get("/api/v1/drivers/me", headers=_auth(token)).json()
    assert still_pending["status"] == "PENDING_KYC"
    # No deposit row exists yet, so there is no balance to move and no route to
    # one: the next transition is an admin's decision, not the account's.
    assert "balance_hkd" not in still_pending["deposit"]
    assert still_pending["deposit"]["is_fulfilled"] is False

    # And the ledger — the only place value is ever recorded — is untouched.
    ledger = client.get("/api/v1/drivers/me/ledger", headers=_auth(token))
    assert ledger.status_code == 200, ledger.text
    assert ledger.json()["items"] == []


# --------------------------------------------------------------------------- #
# Expiry — the property the whole design exists for
# --------------------------------------------------------------------------- #


def test_an_expired_reviewer_is_refused_on_a_normal_route(client):
    """The realistic sequence: sign in while valid, then let it lapse.

    A token issued before the expiry is exactly what a stale reviewer holds, so
    testing only "an expired account cannot sign in" would miss the case that
    matters. `require_active_user` reads the row on every request, so the session
    dies the moment the date passes.
    """
    _create_reviewer(client, days=1)
    token = _token(client)
    assert client.get(IDENTITY_ME, headers=_auth(token)).status_code == 200

    _expire(client)

    refused = client.get(IDENTITY_ME, headers=_auth(token))
    assert refused.status_code == 403, refused.text
    assert refused.json()["details"]["reason"] == "REVIEWER_ACCOUNT_EXPIRED"


def test_an_expired_reviewer_is_refused_on_a_business_route(client):
    """Not just the profile screen. The guard is on every user-facing route, so
    an expired account cannot book, and the reason is machine-readable."""
    _create_reviewer(client, days=1)
    token = _token(client)
    _expire(client)

    refused = client.post(ORDERS, json=ORDER_BODY, headers=_auth(token))
    assert refused.status_code == 403, refused.text
    assert refused.json()["details"]["reason"] == "REVIEWER_ACCOUNT_EXPIRED"


def test_an_expired_reviewer_is_refused_by_the_scope_aware_guard_too(client):
    """`/auth/me` resolves the principal a different way, which is why
    `require_live_principal` repeats the check rather than inheriting it.

    A guard applied only on the common path is exactly the guard a reviewer
    account would walk around — and `/auth/me` is what the client calls on boot
    to decide whether its stored session is still good.
    """
    _create_reviewer(client, days=1)
    token = _token(client)
    assert client.get(ME, headers=_auth(token)).status_code == 200

    _expire(client)

    refused = client.get(ME, headers=_auth(token))
    assert refused.status_code == 403, refused.text
    assert refused.json()["details"]["reason"] == "REVIEWER_ACCOUNT_EXPIRED"


def test_an_expired_reviewer_cannot_sign_in_again(client):
    """The door as well as the rooms.

    Refusing only at the guard would hand out a token that fails on first use,
    which reads to the reviewer as a broken build rather than as an expired
    account.
    """
    _create_reviewer(client, days=1)
    _expire(client)

    response = _sign_in(client)
    assert response.status_code == 401, response.text
    assert "expired" in response.json()["message"]


def test_an_ordinary_account_is_never_treated_as_a_reviewer(client):
    """`reviewer_expires_at IS NOT NULL` *is* the marker, so an ordinary account
    must not accidentally carry a date — and one that does not must not be
    refused.

    This is the guard against the marker drifting into a separate boolean: there
    is only one column, so "expires" and "is a reviewer" cannot disagree.
    """
    token = client.activate("+85290004001")
    assert client.get(IDENTITY_ME, headers=_auth(token)).status_code == 200
    assert client.get(ME, headers=_auth(token)).status_code == 200


# --------------------------------------------------------------------------- #
# Listing and revocation
# --------------------------------------------------------------------------- #


def test_list_finds_the_created_account_and_its_state(client):
    _create_reviewer(client)
    result = _run_script(client, "--list")
    assert result.returncode == 0, result.stderr
    assert REVIEWER_EMAIL in result.stdout
    assert FIRST_REVIEWER_PHONE in result.stdout
    assert "active" in result.stdout


def test_revoke_disables_the_account_and_kills_its_refresh_tokens(client):
    """Early revocation, for when the review finishes sooner than the date.

    Both halves matter: `is_active = false` closes the doors, and revoking the
    refresh tokens stops the holder minting a fresh session. The script also sets
    the expiry to now, so the account reads as expired even to a code path that
    only looks at the date — a revocation that depends on one column being read
    is a revocation that a refactor can undo.
    """
    _create_reviewer(client)
    session = _sign_in(client)
    assert session.status_code == 200
    refresh_token = session.json()["refresh_token"]

    revoked = _run_script(client, "--revoke", REVIEWER_EMAIL)
    assert revoked.returncode == 0, f"stdout={revoked.stdout}\nstderr={revoked.stderr}"
    assert "revoked" in revoked.stdout

    assert _sign_in(client).status_code == 401

    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert replay.status_code == 401, replay.text


def test_revoking_an_unknown_identifier_changes_nothing(client):
    """A typo must not look like a successful revocation — the operator would
    walk away believing the account was closed."""
    result = _run_script(client, "--revoke", "nobody@example.hk")
    assert result.returncode == 1
    assert "nothing changed" in result.stdout


# --------------------------------------------------------------------------- #
# Input validation — the switches that stop a bad invocation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (("--days", "0"), "at least 1"),
        (("--email", "not-an-address"), "does not look like an address"),
        (("--phone", "+85291234567"), "reserved range"),
        (("--username", "Bad Name"), "not usable"),
    ],
)
def test_a_bad_invocation_is_refused_with_a_reason(client, args, needle):
    """Exit 2 and a sentence, rather than a traceback or — worse — a half-created
    account. `--days 0` in particular would mint an account that is expired the
    instant it exists."""
    result = _run_script(client, "--email", REVIEWER_EMAIL, "--yes", *args)
    assert result.returncode == 2, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert needle in result.stdout


def test_a_taken_email_is_refused_rather_than_overwritten(client):
    """The script must never take over an existing account.

    It looks the address up first and stops; the alternative is an UPDATE that
    silently rewrites somebody's password and hands a reviewer their identity.
    """
    _create_reviewer(client)
    result = _run_script(client, "--email", REVIEWER_EMAIL, "--yes")
    assert result.returncode == 1
    assert "already in use" in result.stdout


def test_a_phone_outside_the_reserved_range_cannot_be_forced(client):
    """Even a *valid* Hong Kong number is refused, because the range is what
    keeps a reviewer from colliding with a real subscriber — the account is
    created already verified, so it would otherwise take a number off a person
    who might one day want it."""
    result = _run_script(client, "--email", REVIEWER_EMAIL, "--phone", "+85261234567", "--yes")
    assert result.returncode == 2
    assert "reserved range" in result.stdout
