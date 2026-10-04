"""Generate `mobile/test/fixtures/*.json` from the REAL running API.

Why this exists
---------------
The mobile client decodes a wire format that FastAPI never describes: every
response in `app/api/*` is a hand-built dict, so `/openapi.json` types only the
*request* bodies (28 paths, and all but one response is `{}`). A hand-written
Dart model is therefore an assumption, and the two assumptions most likely to be
wrong are:

* `Decimal` serialises as a JSON **string**, not a number — including
  `distance_km` and `waiting_min`. `json['distance_km'] as double` throws.
* the error envelope is `{code, message, details}`, **not** FastAPI's default
  `{"detail": ...}` — see `app/core/exceptions.py`.

So this script boots the real app, drives the real endpoints over HTTP, and
writes the raw responses to disk. `mobile/tool/verify_contract.dart` then decodes
every fixture with the real Dart models. Re-run it whenever the API changes:

    python scripts/dev/gen_mobile_fixtures.py

It is a development tool: it opts into `ALLOW_DEV_OTP=true` to log in without
WhatsApp credentials, exactly as `scripts/verify/live_smoke.py` does. The dev rail is
refused outright when APP_ENV=prod, and the code is never echoed in a response.

**The fixture accounts are created by email + password, not by OTP.** The auth
split made the phone a *claim* rather than a credential, so `otp/verify` now
refuses any number no account has already proven — an OTP can only re-enter an
account, never reach a new one. Each fixture user is therefore registered
through `POST /auth/register` and then has its number proven through
`identity/phone/request` + `identity/phone/confirm` (see `provision`), because
proving the number is the call車 unlock and the order fixtures need it. The one
exception is the ADMIN, whose row `create_admin.py` writes with
`phone_verified_at` already set and no email — that is the case the secondary
login still exists for.

It resets the five fixture phones and the fixture fleet before it starts, and
leaves `ADMIN_PHONE` as an ADMIN when it finishes — so `admin-web/` can still be
signed into afterwards. Note it starts its own server on port 8123, so it does
not need (or disturb) a server already running on 8000.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _root import REPO_ROOT as ROOT

OUT = ROOT / "mobile" / "test" / "fixtures"
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"
WS_BASE = f"ws://127.0.0.1:{PORT}"
DEV_OTP = "123456"  # requires ALLOW_DEV_OTP=true in the server environment

ADMIN_PHONE = "+85290000001"
PASSENGER_PHONE = "+85290000002"
DRIVER_PHONE = "+85290000003"
REFUND_PHONE = "+85290000004"
# Used only to submit a wrong code, so the attempt counter is not spent on a
# phone the rest of the run depends on.
OTP_PHONE = "+85290000005"

# Since the auth split an account is created by email + password, and the phone
# it names is only a **claim** — proving the number is a separate step. So each
# fixture user needs an address as well as a number, and the three of them are
# deliberately on one domain so a stray row is obviously a fixture.
PASSENGER_EMAIL = "fixture.passenger@example.com"
DRIVER_EMAIL = "fixture.driver@example.com"
REFUND_EMAIL = "fixture.refund@example.com"

# Must satisfy `app/core/passwords.py`: >= 12 characters, no leading or trailing
# whitespace, and none of the weak fragments it blocks (which include
# "realtaxi", so the obvious choice is the one that fails).
#
# S105 is suppressed rather than worked around. It is right to flag a password
# in source, but this one guards three throwaway rows in a local development
# database that `reset_dev_state` deletes on every run — and the alternative,
# reading it from the environment, would make the script fail on a fresh clone
# for a reason that has nothing to do with what it is testing. Renaming the
# constant to dodge the detector would be the dishonest fix.
FIXTURE_PASSWORD = "Fixture-Account-2026!"  # noqa: S105

# The admin **console** account, which is a different identity from
# `ADMIN_PHONE` above. `admin_accounts` and `users` are separate tables on
# purpose, and `require_admin` refuses a `users` token carrying an ADMIN claim —
# so the legacy `create_admin.py --phone` row cannot reach a single admin
# endpoint. The console needs this row, plus a TOTP enrolment, plus a two-step
# login; see `admin_console_token`.
ADMIN_CONSOLE_USER = "fixture-admin"
ADMIN_CONSOLE_EMAIL = "fixture.admin@example.com"
ADMIN_CONSOLE_PASSWORD = "Fixture-Console-2026!"  # noqa: S105

# The fleet this run creates. `fleets.name` and `fleets.license_no` are UNIQUE,
# so unlike the fixture phones this row cannot simply be left behind — a second
# run would 409 on create. `reset_dev_state` deletes it by licence number.
FLEET_LICENSE = "FLEET-STAR-001"


def req(
    method: str,
    path: str,
    body: Any = None,
    token: str | None = None,
) -> tuple[int, Any]:
    request = urllib.request.Request(BASE + path, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(request, data=data, timeout=15) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw}


TEST_PHONES = (ADMIN_PHONE, PASSENGER_PHONE, DRIVER_PHONE, REFUND_PHONE, OTP_PHONE)


def reset_dev_state() -> None:
    """Make this script re-runnable from a clean slate.

    Four pieces of state outlive a server restart, and every one of them fails
    opaquely on a second run:

    * the Redis rate-limit windows (`rl:realtaxi:*`, namespace set in
      `app/main.py`). The OTP per-IP bucket has a 600s TTL, so a re-run inside
      that window 429s before it logs anything in.
    * the OTP **resend cooldown** — not Redis at all, but a 60s lookback over
      the `otp_codes` table (`otp_service._RESEND_COOLDOWN_S`). A re-run within
      a minute dies with `BUSINESS_RULE_VIOLATION: OTP resend cooldown active`.
    * the driver profile from the previous run, which makes
      `POST /drivers/register` 409.
    * the order from the previous run, which is still in the passenger's history.

    So the four fixture users are deleted outright. The delete order is dictated
    by the FK rules in `app/models/__init__.py`: `ledger_entries.driver_profile_id`
    and `orders.passenger_id` are `RESTRICT`, as is `refresh_tokens.user_id`, so
    they must go before the owning rows. `driver_profiles` -> `driver_deposits`
    and `refund_requests` are `CASCADE`, so deleting the `users` row is enough
    for those.

    The fixture fleet is deleted too. It is not tied to a phone number, and
    `fleets.name` / `fleets.license_no` are UNIQUE, so leaving it behind makes
    the next run 409 on create. `fleet_memberships` and `fleet_settlement_runs`
    both cascade from `fleets`, so removing the fleet is enough.

    Only the four throwaway fixture phones, and that one fleet, are touched.
    """
    import redis.asyncio as aioredis
    from sqlalchemy import delete, or_, select

    from app.core.config import get_settings
    from app.core.db import dispose_engine, get_session_factory
    from app.models import (
        DriverProfile,
        Fleet,
        LedgerEntry,
        Order,
        OtpCode,
        RefreshToken,
        User,
    )

    async def run() -> None:
        client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        try:
            keys = [k async for k in client.scan_iter("rl:realtaxi:*")]
            if keys:
                await client.delete(*keys)
        finally:
            await client.aclose()

        async with get_session_factory()() as session:
            user_ids = select(User.id).where(User.phone_e164.in_(TEST_PHONES))
            profile_ids = select(DriverProfile.id).where(DriverProfile.user_id.in_(user_ids))
            # RESTRICT -> must be removed before the rows they point at.
            await session.execute(
                delete(LedgerEntry).where(LedgerEntry.driver_profile_id.in_(profile_ids))
            )
            await session.execute(
                delete(Order).where(
                    or_(
                        Order.passenger_id.in_(user_ids),
                        Order.driver_id.in_(profile_ids),
                    )
                )
            )
            await session.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(user_ids)))
            await session.execute(delete(OtpCode).where(OtpCode.phone_e164.in_(TEST_PHONES)))
            # CASCADE handles driver_profiles -> driver_deposits, refund_requests.
            await session.execute(delete(User).where(User.phone_e164.in_(TEST_PHONES)))
            # CASCADE handles fleets -> fleet_memberships, fleet_settlement_runs.
            # After the users, so a membership row is already gone with its
            # driver profile rather than being cascaded twice.
            await session.execute(delete(Fleet).where(Fleet.license_no == FLEET_LICENSE))
            await session.commit()

        # Dispose, always. `get_engine()` caches a module-level pool, and an
        # asyncpg connection belongs to the event loop that opened it — so the
        # *next* `asyncio.run` in this process would reuse a connection from a
        # loop that no longer exists and die on the first ping with
        # `'NoneType' object has no attribute 'send'`. See `_existing_admin_totp_secret`.
        await dispose_engine()

    asyncio.run(run())


def admin_cli(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/ops/create_admin.py", *args, "--yes"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"create_admin.py {' '.join(args)} failed:\n{result.stderr}")


def ops_cli(script: str, *args: str, env: dict[str, str] | None = None) -> str:
    """Run one of `scripts/ops/*` and return its stdout, or die with its stderr.

    `admin_cli` above is the `create_admin.py` special case and is left alone;
    this is the general form the console bootstrap needs.
    """
    result = subprocess.run(
        [sys.executable, f"scripts/ops/{script}", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )
    if result.returncode != 0:
        raise SystemExit(f"{script} {' '.join(args)} failed:\n{result.stderr or result.stdout}")
    return result.stdout


async def _existing_admin_totp_secret() -> str | None:
    """The console account's enrolment secret, or None if it is not enrolled.

    Read from the database rather than re-enrolling, because enrolment is a
    one-time event: `enrol_admin_totp.py` refuses to print a secret it has
    already handed out (and it should — the secret is the second factor). This
    is also what makes a second run of this script idempotent.
    """
    from sqlalchemy import select

    from app.core.db import dispose_engine, get_session_factory
    from app.models import AdminAccount

    async with get_session_factory()() as session:
        secret = (
            await session.execute(
                select(AdminAccount.totp_secret).where(AdminAccount.username == ADMIN_CONSOLE_USER)
            )
        ).scalar_one_or_none()
    # Same reason as `reset_dev_state`: this runs in its own `asyncio.run`, and a
    # pooled connection from the previous loop is not usable in this one.
    await dispose_engine()
    return secret or None


def _parse_totp_secret(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("ADMIN_TOTP_SECRET="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"no ADMIN_TOTP_SECRET= line in the enrolment output:\n{stdout}")


def admin_console_token() -> str:
    """Sign in to the admin console and return an access token.

    Three steps, because the console has a real second factor: an
    `admin_accounts` row (`create_admin_account.py`), a TOTP enrolment
    (`enrol_admin_totp.py`, which prints the secret once), and the two-step
    login. `totp_at` is imported from the app rather than reimplemented — RFC
    6238 is not something worth writing twice, and a second implementation would
    be the one that drifts when the digits or the step change.

    This replaces the OTP login the admin fixtures used to take. That rail
    cannot work: `require_admin` re-reads `admin_accounts` and refuses a `users`
    token that merely carries an ADMIN claim, which is exactly what
    `create_admin.py --phone` produces. The fixtures on disk predate that split,
    so they were no longer reproducible — which is why this script had stopped
    running end to end.
    """
    from app.core.totp import STEP_SECONDS, totp_at

    secret = asyncio.run(_existing_admin_totp_secret())
    if secret is None:
        ops_cli(
            "create_admin_account.py",
            "--username",
            ADMIN_CONSOLE_USER,
            "--email",
            ADMIN_CONSOLE_EMAIL,
            "--yes",
            env={"ADMIN_PASSWORD": ADMIN_CONSOLE_PASSWORD},
        )
        # `--super-admin` because a fresh account is SUPPORT, and several of the
        # fixtures below (settlement, deposits) need more than that.
        secret = _parse_totp_secret(
            ops_cli(
                "enrol_admin_totp.py",
                "--username",
                ADMIN_CONSOLE_USER,
                "--super-admin",
                env={"ADMIN_PASSWORD": ADMIN_CONSOLE_PASSWORD},
            )
        )
        # Enrolment just spent the code for the current 30-second step, and
        # `totp_last_counter` refuses anything at or before it as a replay — so a
        # login inside the same step is answered "invalid code", which is a
        # correct refusal and a thoroughly confusing one. Wait for the step to
        # roll over. Generating a code for a *future* step would not help: the
        # accept window is ±1 step, but the replay guard compares counters, so it
        # would be refused too.
        time.sleep(STEP_SECONDS - (time.time() % STEP_SECONDS) + 1)

    status, challenge = req(
        "POST",
        "/api/v1/admin/auth/login",
        {"username": ADMIN_CONSOLE_USER, "password": ADMIN_CONSOLE_PASSWORD},
    )
    if status != 200:
        raise SystemExit(f"admin console login -> {status}: {challenge}")
    if challenge.get("next") != "totp_required":
        raise SystemExit(
            f"expected next=totp_required, got {challenge.get('next')!r}. An "
            "'enrolment_required' here means the row exists but carries no secret — "
            f"delete admin_accounts.username='{ADMIN_CONSOLE_USER}' and re-run."
        )

    status, session = req(
        "POST",
        "/api/v1/admin/auth/totp/verify",
        {"challenge_token": challenge["challenge_token"], "code": totp_at(secret)},
    )
    if status != 200:
        raise SystemExit(f"admin totp/verify -> {status}: {session}")
    return session["access_token"]


def provision(email: str, phone: str) -> tuple[str, dict[str, Any]]:
    """Create the fixture account over the REAL endpoints, then prove its phone.

    Returns `(access_token, register_body)`.

    This used to be a bare OTP request + verify. That stopped working when the
    phone stopped being a login credential: `OtpService.verify_otp` now requires
    `phone_verified_at IS NOT NULL` and refuses with "no account has verified
    this number" otherwise — so an OTP verify can only *re-enter* an account,
    never create or reach one. The primary door is email + password, and the
    number named at registration is a claim that must then be proven through
    `identity/phone/request` + `identity/phone/confirm`.

    Doing both here is not a convenience. Proving the phone is the call車
    unlock, and the order fixtures below are created through
    `require_phone_current`; an account that has only *claimed* a number gets a
    403 with `reason: PHONE_NOT_VERIFIED`, so the run would fail at the first
    order. The two steps also mirror the order the app performs them in.

    The fallback to login exists because `reset_dev_state` deletes the fixture
    phones but a crashed previous run can leave the email row behind; signing in
    is the recovery, and it is what the app would do.
    """
    status, registered = req(
        "POST",
        "/api/v1/auth/register",
        {"email": email, "password": FIXTURE_PASSWORD, "phone_e164": phone},
    )
    if status not in (200, 201):
        status, registered = req(
            "POST",
            "/api/v1/auth/login",
            {"email": email, "password": FIXTURE_PASSWORD},
        )
        if status != 200:
            raise SystemExit(f"could not register or sign in {email} -> {status}: {registered}")
    token = registered["access_token"]

    # The call車 unlock. `ALLOW_DEV_OTP=true` is what makes DEV_OTP knowable —
    # the code is never echoed in a response (SEC-02).
    status, requested = req(
        "POST", "/api/v1/identity/phone/request", {"phone_e164": phone}, token=token
    )
    if status != 200:
        raise SystemExit(f"identity/phone/request for {phone} -> {status}: {requested}")
    status, confirmed = req(
        "POST",
        "/api/v1/identity/phone/confirm",
        {"phone_e164": phone, "code": DEV_OTP},
        token=token,
    )
    if status != 200:
        raise SystemExit(f"identity/phone/confirm for {phone} -> {status}: {confirmed}")
    return token, registered


def otp_request(phone: str) -> dict[str, Any]:
    """`POST /auth/otp/request`, waiting out the resend cooldown if it fires.

    The cooldown is a 60s lookback over `otp_codes` (`otp_service._RESEND_COOLDOWN_S`)
    keyed on the **phone**, not on which endpoint sent the code — and
    `identity/phone/request` and `auth/otp/request` are the same
    `OtpService.request_otp` underneath. So proving a number starts the very
    clock that the login rail then reads, and a fixture run cannot ask for a
    login code immediately after provisioning.

    Waiting is the honest answer. The alternative — writing `phone_verified_at`
    directly, the way `create_admin.py` does for the admin row — would skip the
    flow these fixtures exist to pin. The delay comes from the server's own
    `retry_after_seconds` rather than a hardcoded 60, so a change to the window
    does not silently start failing here.
    """
    status, body = req("POST", "/api/v1/auth/otp/request", {"phone_e164": phone})
    if status == 200:
        return body
    retry_after = (body.get("details") or {}).get("retry_after_seconds")
    if retry_after is None:
        raise SystemExit(f"otp/request for {phone} -> {status}: {body}")
    time.sleep(float(retry_after) + 1)
    status, body = req("POST", "/api/v1/auth/otp/request", {"phone_e164": phone})
    if status != 200:
        raise SystemExit(f"otp/request for {phone} after cooldown -> {status}: {body}")
    return body


def otp_login(phone: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """OTP request + verify for a number that is ALREADY verified.

    Returns `(access_token, verify_body, request_body)`.

    Only the ADMIN fixture uses this. `create_admin.py` writes
    `phone_verified_at` directly and gives the row no email or password, so the
    admin has no way in except the phone rail — which is precisely the case the
    secondary login still exists for.
    """
    body = otp_request(phone)
    verify_status, verify = req(
        "POST",
        "/api/v1/auth/otp/verify",
        {"phone_e164": phone, "code": DEV_OTP},
    )
    if verify_status != 200:
        raise SystemExit(f"otp/verify for {phone} -> {verify_status}: {verify}")
    return verify["access_token"], verify, body


FIXTURES: dict[str, Any] = {}
SOURCES: dict[str, str] = {}

# Credential fields blanked before a fixture reaches disk — see `_redact`.
_TOKEN_KEYS = frozenset({"access_token", "refresh_token"})


def _redact(payload: Any) -> Any:
    """Replace credential values with a placeholder, recursively.

    The fixtures are committed, so the contract stays reviewable in a diff. A
    dev access/refresh token does not belong in a repository even though it is
    already spent and belongs to a throwaway account. The verifier asserts the
    *shape* — `AuthSession.fromJson` needs a string, not a particular one — so a
    placeholder loses nothing.
    """
    if isinstance(payload, dict):
        return {
            key: ("<redacted>" if key in _TOKEN_KEYS else _redact(value))
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_redact(item) for item in payload]
    return payload


def record(name: str, source: str, payload: Any) -> None:
    FIXTURES[name] = _redact(payload)
    SOURCES[name] = source


def main() -> int:  # a linear script, not a library
    OUT.mkdir(parents=True, exist_ok=True)

    sock = socket.socket()
    free = sock.connect_ex(("127.0.0.1", PORT)) != 0
    sock.close()
    if not free:
        raise SystemExit(f"port {PORT} is occupied — stop the other server first")

    reset_dev_state()
    admin_cli("--phone", ADMIN_PHONE)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            str(PORT),
            # SEC-31: without this, uvicorn trusts X-Forwarded-For from
            # 127.0.0.1 and the per-IP limits become spoofable.
            "--no-proxy-headers",
        ],
        cwd=ROOT,
        env={**os.environ, "ALLOW_DEV_OTP": "true"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"{BASE}/health", timeout=2):
                    break
            except Exception:  # a readiness probe must survive anything
                time.sleep(0.5)
        else:
            raise SystemExit("server did not become ready")

        _capture(record)

        manifest = {
            "generated_by": "scripts/dev/gen_mobile_fixtures.py",
            "note": (
                "Raw responses from the real API. Each entry names the Dart model in "
                "mobile/lib/models that must decode it; mobile/tool/verify_contract.dart "
                "enforces that."
            ),
            "sources": SOURCES,
        }
        (OUT / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        for name, payload in FIXTURES.items():
            (OUT / f"{name}.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        print(f"--- wrote {len(FIXTURES)} fixture(s) + manifest.json to {OUT} ---")
        for name in sorted(FIXTURES):
            print(f"  {name}.json  <-  {SOURCES[name]}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        # Leave an ADMIN behind, deliberately. This used to `--revoke`, on the
        # theory that the grant should be undone — but `reset_dev_state()` deleted
        # the whole user row at the start of the run, so there is no earlier state
        # to restore. Revoking therefore left the development database with no
        # ADMIN at all, and the admin console's next sign-in silently created a
        # fresh PASSENGER and refused it with "此帳戶沒有管理權限" — a dead end
        # that names the symptom and not the cause.
        admin_cli("--phone", ADMIN_PHONE)
    return 0


def weekly_settlement_run(admin_token: str, period: str | None = None) -> Any:
    """Preview, then run. The run refuses without a token the preview minted.

    Two steps because the button charges every eligible driver at once and the
    first accidental press is not undoable — the token is what turns "look before
    you leap" from a runbook line into a structural constraint. A run against a
    period that is *already* settled is exempt (it is a no-op by construction),
    which is why the second call below still needs its own preview: it targets a
    fresh period.

    The token binds the period and the fee, not the actor, so it is passed
    through as a query parameter exactly as the console does.
    """
    status, preview = req(
        "POST",
        "/api/v1/admin/settlement/preview",
        {"period": period} if period else {},
        token=admin_token,
    )
    if status != 200:
        raise SystemExit(f"settlement/preview -> {status}: {preview}")

    query = {"confirm_token": preview["confirm_token"]}
    if period:
        query["period"] = period
    status, run = req(
        "POST",
        f"/api/v1/admin/settlement/weekly/run?{urllib.parse.urlencode(query)}",
        token=admin_token,
    )
    if status != 200:
        raise SystemExit(f"settlement/weekly/run -> {status}: {run}")
    return run


def _capture(record: Any) -> None:  # a linear capture sequence
    # ---- auth ----------------------------------------------------------
    # The primary door first: email + password, then the phone proof that
    # unlocks booking. Its `register` body is not captured here — the driver
    # registration further down is the one that must report `created: true`, and
    # two fixtures for one fact would drift.
    passenger_token, _ = provision(PASSENGER_EMAIL, PASSENGER_PHONE)

    # The OTP rail, exercised as what it now is: a **secondary** login. Two
    # fixtures come out of it and `created` is false in both. `auth_verify_new_user`
    # used to live here and is gone rather than fixed — no OTP verify can create
    # an account any more, so its premise ("created=true") is unproducible.
    #
    # `otp_request` waits out the resend cooldown here, because `provision` above
    # just sent a code to this same number. See its docstring.
    otp_request_body = otp_request(PASSENGER_PHONE)
    record("auth_otp_request", "POST /api/v1/auth/otp/request", otp_request_body)

    status, verify_body = req(
        "POST",
        "/api/v1/auth/otp/verify",
        {"phone_e164": PASSENGER_PHONE, "code": DEV_OTP},
    )
    assert status == 200, verify_body
    record("auth_verify", "POST /api/v1/auth/otp/verify (secondary login)", verify_body)

    status, me = req("GET", "/api/v1/auth/me", token=passenger_token)
    assert status == 200, me
    record("auth_me", "GET /api/v1/auth/me", me)

    # ---- identity: the account's own record ----------------------------
    # `identity_me` is the profile *before* it is completed: the client reads it
    # to decide whether to show the completion screen at all, so the shape that
    # matters is this one (username null, account_status UNVERIFIED).
    status, profile = req("GET", "/api/v1/identity/me", token=passenger_token)
    assert status == 200, profile
    record("identity_me", "GET /api/v1/identity/me", profile)

    # Completing it returns the **full** profile, not the echoed username:
    # filling it in is often what flips `account_status`, and the client has to
    # re-render the gate it is sitting behind.
    status, completed = req(
        "POST",
        "/api/v1/identity/profile",
        {
            "username": "fixture-passenger",
            "given_name": "Ka Ming",
            "family_name": "Chan",
            "gender": "MALE",
            "avatar_key": None,
        },
        token=passenger_token,
    )
    assert status == 200, completed
    record("identity_profile", "POST /api/v1/identity/profile", completed)

    # ---- fare (public) -------------------------------------------------
    status, fare = req(
        "POST",
        "/api/v1/fare/estimate",
        {
            "taxi_type": "URBAN",
            "distance_km": "12.5",
            "waiting_min": "3",
            "tunnels": ["cross_harbour"],
            "crosses_harbour": True,
            "baggage_count": 2,
            "tip": "10",
        },
    )
    assert status == 200, fare
    record("fare_estimate", "POST /api/v1/fare/estimate", fare)

    # ---- passenger places an order -------------------------------------
    status, order = req(
        "POST",
        "/api/v1/orders",
        {
            "pickup_lat": 22.3193,
            "pickup_lng": 114.1694,
            "dropoff_lat": 22.2783,
            "dropoff_lng": 114.1747,
            "pickup_address": "Central",
            "dropoff_address": "Causeway Bay",
            "distance_km": "6.4",
            "taxi_type": "URBAN",
            "tip": "5",
            "tunnels": ["cross_harbour"],
            "crosses_harbour": True,
        },
        token=passenger_token,
    )
    assert status == 201, order
    record("order_created", "POST /api/v1/orders (201)", order)
    order_id = order["id"]

    status, detail = req("GET", f"/api/v1/orders/{order_id}", token=passenger_token)
    assert status == 200, detail
    record("order_detail", "GET /api/v1/orders/{order_id}", detail)

    status, page = req("GET", "/api/v1/orders?role=passenger&limit=20", token=passenger_token)
    assert status == 200, page
    record("orders_page", "GET /api/v1/orders?role=passenger", page)

    status, nearby = req(
        "GET",
        "/api/v1/orders/nearby?lat=22.3193&lng=114.1694&radius_km=3",
        token=passenger_token,
    )
    assert status == 200, nearby
    record("orders_nearby", "GET /api/v1/orders/nearby", nearby)

    status, trip = req("GET", f"/api/v1/trips/{order_id}/location", token=passenger_token)
    assert status == 200, trip
    record("trip_location", "GET /api/v1/trips/{order_id}/location", trip)

    # ---- driver: register, get approved, get funded ---------------------
    # Two different admin identities, and the difference is load-bearing.
    # `ADMIN_PHONE` is the legacy `users.role = ADMIN` row: it can still use the
    # phone rail, so the `auth_verify_admin` fixture is unchanged — but it cannot
    # call a single admin endpoint, because `require_admin` re-reads
    # `admin_accounts`. `admin_token` below is a real console session and is what
    # every `/admin/*` fixture needs.
    _, admin_verify, _ = otp_login(ADMIN_PHONE)
    record("auth_verify_admin", "POST /api/v1/auth/otp/verify (ADMIN role)", admin_verify)

    admin_token = admin_console_token()

    # `provision` registers the driver through `POST /auth/register`, and that
    # response is now the only place `created: true` is producible — which is
    # exactly what the retired `auth_verify_new_user` fixture was asserting, so
    # this replaces it rather than being a new claim.
    driver_token, driver_register = provision(DRIVER_EMAIL, DRIVER_PHONE)
    record("auth_register", "POST /api/v1/auth/register (201, created=true)", driver_register)

    status, registered = req(
        "POST",
        "/api/v1/drivers/register",
        {
            "hk_id_last4": "1234",
            "taxi_driver_plate_no": "123456",
            "vehicle_reg_mark": "AB1234",
            "taxi_type": "URBAN",
        },
        token=driver_token,
    )
    assert status == 201, registered
    record("driver_register", "POST /api/v1/drivers/register (201)", registered)
    driver_profile_id = registered["id"]

    status, driver_me_pending = req("GET", "/api/v1/drivers/me", token=driver_token)
    assert status == 200, driver_me_pending
    record("driver_me_no_deposit", "GET /api/v1/drivers/me (before any deposit)", driver_me_pending)

    status, queue = req("GET", "/api/v1/admin/drivers?limit=50", token=admin_token)
    assert status == 200, queue
    record("admin_drivers", "GET /api/v1/admin/drivers", queue)

    status, reviewed = req(
        "POST",
        f"/api/v1/admin/drivers/{driver_profile_id}/review",
        {"decision": "approve", "note": "fixture"},
        token=admin_token,
    )
    assert status == 200, reviewed
    record("admin_review", "POST /api/v1/admin/drivers/{id}/review", reviewed)

    status, granted = req(
        "POST",
        f"/api/v1/admin/drivers/{driver_profile_id}/deposit/grant",
        {"amount_hkd": "500", "note": "fixture top-up", "reference": "fixture-1"},
        token=admin_token,
    )
    assert status == 200, granted
    record("admin_grant", "POST /api/v1/admin/drivers/{id}/deposit/grant", granted)

    status, driver_me = req("GET", "/api/v1/drivers/me", token=driver_token)
    assert status == 200, driver_me
    record("driver_me", "GET /api/v1/drivers/me (ACTIVE, funded)", driver_me)

    # ---- driver works the order ----------------------------------------
    status, located = req(
        "POST",
        "/api/v1/drivers/location",
        {"lat": 22.3200, "lng": 114.1700, "online": True},
        token=driver_token,
    )
    assert status == 200, located
    record("driver_location", "POST /api/v1/drivers/location", located)

    status, grabbed = req("POST", f"/api/v1/orders/{order_id}/grab", token=driver_token)
    assert status == 200, grabbed
    record("order_grabbed", "POST /api/v1/orders/{id}/grab", grabbed)

    for action in ("arrive", "start", "complete"):
        status, body = req("POST", f"/api/v1/orders/{order_id}/{action}", token=driver_token)
        assert status == 200, f"{action}: {body}"
        record(f"order_{action}", f"POST /api/v1/orders/{{id}}/{action}", body)

    status, driver_history = req("GET", "/api/v1/orders?role=driver", token=driver_token)
    assert status == 200, driver_history
    record("orders_page_driver", "GET /api/v1/orders?role=driver", driver_history)

    status, ledger = req("GET", "/api/v1/drivers/me/ledger?limit=100", token=driver_token)
    assert status == 200, ledger
    record("ledger_page", "GET /api/v1/drivers/me/ledger", ledger)

    settlement = weekly_settlement_run(admin_token)
    record("admin_settlement", "POST /api/v1/admin/settlement/weekly/run", settlement)

    # ---- fleets ---------------------------------------------------------
    # A fleet is created by an admin: HK fleets are licensed operators, so there
    # is no driver-facing "create my fleet" route to capture.
    status, fleet = req(
        "POST",
        "/api/v1/admin/fleets",
        {
            "name": "星群的士",
            "license_no": "FLEET-STAR-001",
            "weekly_fee_discount_percent": "25",
            "contact_name": "陳先生",
            "contact_phone": "+85222334455",
        },
        token=admin_token,
    )
    assert status == 201, fleet
    record("fleet_created", "POST /api/v1/admin/fleets (201)", fleet)
    fleet_id = fleet["id"]

    status, fleets = req("GET", "/api/v1/admin/fleets", token=admin_token)
    assert status == 200, fleets
    record("admin_fleets", "GET /api/v1/admin/fleets", fleets)

    status, added = req(
        "POST",
        f"/api/v1/admin/fleets/{fleet_id}/members",
        {"driver_profile_id": driver_profile_id, "member_role": "MEMBER"},
        token=admin_token,
    )
    assert status == 201, added
    record("admin_fleet_member_added", "POST /api/v1/admin/fleets/{id}/members (201)", added)

    status, admin_roster = req("GET", f"/api/v1/admin/fleets/{fleet_id}/members", token=admin_token)
    assert status == 200, admin_roster
    record("admin_fleet_members", "GET /api/v1/admin/fleets/{id}/members", admin_roster)

    # The member's own view. Every `/fleets/*` route checks membership per
    # request and answers 404 — not 403 — for someone else's fleet, so this is
    # the only driver-facing fleet read that succeeds.
    status, my_fleet = req("GET", "/api/v1/fleets/me", token=driver_token)
    assert status == 200, my_fleet
    assert my_fleet["fleet"] is not None, my_fleet
    record("fleet_me", "GET /api/v1/fleets/me (member)", my_fleet)

    status, fleet_detail = req("GET", f"/api/v1/fleets/{fleet_id}", token=driver_token)
    assert status == 200, fleet_detail
    record("fleet_detail", "GET /api/v1/fleets/{id} (member)", fleet_detail)

    status, fleet_roster = req("GET", f"/api/v1/fleets/{fleet_id}/members", token=driver_token)
    assert status == 200, fleet_roster
    record("fleet_members", "GET /api/v1/fleets/{id}/members (member)", fleet_roster)

    # A driver who is on no roster gets a null pair rather than a 404 — the
    # common state, so the client must treat it as data.
    status, no_fleet = req("GET", "/api/v1/fleets/me", token=passenger_token)
    assert status == 200, no_fleet
    assert no_fleet["fleet"] is None, no_fleet
    record("fleet_me_none", "GET /api/v1/fleets/me (not a member)", no_fleet)

    # An explicit week, so this cannot collide with the run above (which used
    # the current ISO week) and the idempotency key is deterministic.
    status, fleet_run = req(
        "POST",
        f"/api/v1/admin/fleets/{fleet_id}/settlement/run?period=2026-W45",
        token=admin_token,
    )
    assert status == 200, fleet_run
    record("admin_fleet_settlement_run", "POST /api/v1/admin/fleets/{id}/settlement/run", fleet_run)

    status, fleet_history = req(
        "GET", f"/api/v1/admin/fleets/{fleet_id}/settlement", token=admin_token
    )
    assert status == 200, fleet_history
    record("admin_fleet_settlement", "GET /api/v1/admin/fleets/{id}/settlement", fleet_history)

    status, fleet_history_member = req(
        "GET", f"/api/v1/fleets/{fleet_id}/settlement", token=driver_token
    )
    assert status == 200, fleet_history_member
    record("fleet_settlement", "GET /api/v1/fleets/{id}/settlement (member)", fleet_history_member)

    # The billing boundary, captured as a fixture: the platform-wide run now
    # skips the driver because they are on an active roster, and says so. The
    # week is explicit so this does not collide with either run above.
    settlement_excluded = weekly_settlement_run(admin_token, period="2026-W46")
    assert settlement_excluded["fleet_managed"] >= 1, settlement_excluded
    record(
        "admin_settlement_fleet_managed",
        "POST /api/v1/admin/settlement/weekly/run with a fleet member (200)",
        settlement_excluded,
    )

    # ---- refunds (suspends the driver, so use a second account) ---------
    refund_token, _ = provision(REFUND_EMAIL, REFUND_PHONE)
    status, refund_profile = req(
        "POST",
        "/api/v1/drivers/register",
        {
            "hk_id_last4": "5678",
            "taxi_driver_plate_no": "654321",
            "vehicle_reg_mark": "CD5678",
            "taxi_type": "NT",
        },
        token=refund_token,
    )
    assert status == 201, refund_profile
    refund_profile_id = refund_profile["id"]

    req(
        "POST",
        f"/api/v1/admin/drivers/{refund_profile_id}/review",
        {"decision": "approve", "note": ""},
        token=admin_token,
    )
    req(
        "POST",
        f"/api/v1/admin/drivers/{refund_profile_id}/deposit/grant",
        {"amount_hkd": "500", "note": ""},
        token=admin_token,
    )

    status, refund = req(
        "POST", "/api/v1/drivers/me/refund/request", {"note": "leaving"}, token=refund_token
    )
    assert status == 201, refund
    record("refund_request", "POST /api/v1/drivers/me/refund/request (201)", refund)

    status, my_refund = req("GET", "/api/v1/drivers/me/refund", token=refund_token)
    assert status == 200, my_refund
    record("refund_me", "GET /api/v1/drivers/me/refund", my_refund)

    status, refund_queue = req("GET", "/api/v1/admin/refunds", token=admin_token)
    assert status == 200, refund_queue
    record("admin_refunds", "GET /api/v1/admin/refunds", refund_queue)

    refund_id = refund["id"]
    status, decided = req(
        "POST",
        f"/api/v1/admin/refunds/{refund_id}/decision",
        {"decision": "approve", "note": "fixture"},
        token=admin_token,
    )
    assert status == 200, decided
    record("admin_refund_decision", "POST /api/v1/admin/refunds/{id}/decision", decided)

    # ---- errors: the envelope every client must parse -------------------
    status, not_found = req(
        "GET", "/api/v1/orders/00000000-0000-0000-0000-000000000000", token=passenger_token
    )
    assert status == 404, not_found
    record("error_not_found", "GET /api/v1/orders/{unknown} (404)", not_found)

    status, unauthorized = req("GET", "/api/v1/auth/me")
    assert status == 401, unauthorized
    record("error_unauthorized", "GET /api/v1/auth/me without a token (401)", unauthorized)

    status, validation = req("POST", "/api/v1/auth/otp/request", {"phone_e164": "91234567"})
    assert status == 422, validation
    record("error_validation", "POST /api/v1/auth/otp/request with a bad phone (422)", validation)

    # A *pydantic* range failure, not a domain rule: `distance_km` is capped at
    # 100 by the request model, so this never reaches `calculate_fare`.
    status, validation_range = req(
        "POST",
        "/api/v1/fare/estimate",
        {"taxi_type": "URBAN", "distance_km": "500"},
    )
    assert status == 422, validation_range
    record(
        "error_validation_range",
        "POST /api/v1/fare/estimate with distance_km=500 (422)",
        validation_range,
    )

    # A genuine BUSINESS_RULE_VIOLATION, and the only one whose `details` is a
    # structured object rather than `{}`: the order is already COMPLETED and
    # `COMPLETED` has no outgoing transitions, so re-starting it reports
    # `{"from": "COMPLETED", "to": "IN_TRIP"}`.
    status, business_rule = req("POST", f"/api/v1/orders/{order_id}/start", token=driver_token)
    assert status == 400, business_rule
    record(
        "error_business_rule",
        "POST /api/v1/orders/{id}/start on a COMPLETED order (400)",
        business_rule,
    )

    status, forbidden = req("GET", "/api/v1/admin/drivers", token=passenger_token)
    assert status == 403, forbidden
    record("error_forbidden", "GET /api/v1/admin/drivers as a passenger (403)", forbidden)

    # ---- websocket -----------------------------------------------------
    _capture_websocket(record, order_id, passenger_token, driver_token)

    # ---- refresh last: it rotates the pair, so the old refresh token dies --
    # Reuse the token from the login at the top of this run rather than logging
    # in again — a second `otp/request` for the same phone inside 60s is refused
    # by the resend cooldown.
    status, refreshed = req(
        "POST",
        "/api/v1/auth/refresh",
        {"refresh_token": verify_body["refresh_token"]},
    )
    assert status == 200, refreshed
    record("auth_refresh", "POST /api/v1/auth/refresh", refreshed)

    # The rotated token is now spent: a second use must be refused, and the
    # client has to treat that as a sign-out rather than a retry.
    status, reused = req(
        "POST",
        "/api/v1/auth/refresh",
        {"refresh_token": verify_body["refresh_token"]},
    )
    assert status == 401, reused
    record("auth_refresh_replay", "POST /api/v1/auth/refresh replayed (401)", reused)

    # A wrong code. `details` here is `{"attempts_remaining": 4}` — the OTP
    # screen renders that countdown, so losing it (see the `BusinessRuleError`
    # re-raise note above) makes the screen lie.
    status, _ = req("POST", "/api/v1/auth/otp/request", {"phone_e164": OTP_PHONE})
    assert status == 200, _
    status, bad_code = req(
        "POST",
        "/api/v1/auth/otp/verify",
        {"phone_e164": OTP_PHONE, "code": "000000"},
    )
    assert status == 400, bad_code
    record(
        "error_otp_bad_code",
        "POST /api/v1/auth/otp/verify with a wrong code (400)",
        bad_code,
    )

    # The cooldown, whose `details.retry_after_seconds` the client needs in
    # order to count down. The passenger phone already has an OTP row from this
    # run, so the cooldown is guaranteed live.
    status, cooldown = req("POST", "/api/v1/auth/otp/request", {"phone_e164": PASSENGER_PHONE})
    assert status == 400, cooldown
    record("error_otp_cooldown", "POST /api/v1/auth/otp/request twice (400)", cooldown)


def _capture_websocket(record: Any, order_id: str, passenger_token: str, driver_token: str) -> None:
    """Capture one real location tick and the ping/heartbeat frame."""
    import websockets

    async def run() -> None:
        passenger_uri = f"{WS_BASE}/ws/trip/{order_id}?token={passenger_token}"
        driver_uri = f"{WS_BASE}/ws/trip/{order_id}?token={driver_token}"
        async with (
            websockets.connect(passenger_uri) as passenger,
            websockets.connect(driver_uri) as driver,
        ):
            await driver.send(json.dumps({"lat": 22.3201, "lng": 114.1701}))
            ack = json.loads(await asyncio.wait_for(driver.recv(), timeout=10))
            tick = json.loads(await asyncio.wait_for(passenger.recv(), timeout=10))
            record("ws_driver_ack", "WS /ws/trip/{id} driver reply to a tick", ack)
            record("ws_location_tick", "WS /ws/trip/{id} passenger receives", tick)

            # A read-only socket earns an explicit error rather than silence.
            await passenger.send(json.dumps({"lat": 22.32, "lng": 114.17}))
            readonly = json.loads(await asyncio.wait_for(passenger.recv(), timeout=10))
            record("ws_read_only_error", "WS /ws/trip/{id} passenger push rejected", readonly)

    asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
