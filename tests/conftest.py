"""Shared fixtures.

Two tiers:
- `client`: TestClient backed by a REAL per-test Postgres database
  (cloned from a session-start template) — full SQL semantics without
  per-test schema churn. Requires the realtaxi-db container running.
- `db_session`: async session on its own per-test database, for
  service-level tests.

Legacy unit tests (fare engine, error format, JWT) never touch the DB
and run unchanged on this fixture set.

OTP codes
---------
The API never returns the code (SEC-02), so `otp_inbox` installs a test double
at the notify seam and records what the app actually sent. That is a stronger
assertion than reading a `dev_code` field out of the response: it proves the
code reached the notification layer, and it lets the suite run with the dev-OTP
switch OFF, i.e. against a random code exactly like production.

Admin identity
--------------
The template DB seeds one ADMIN so the `_admin_token()` helpers survive
`require_admin`'s live-DB re-check. The id is RANDOM per session — a fixed,
well-known admin UUID is a skeleton key: identical in every deployment, and
anyone able to forge a token would already know which `sub` to use.
"""

import asyncio
import os
import uuid

# Test-process defaults, seeded before any app module is imported.
#
# `Settings` is deliberately fail-closed (SEC-01~05): APP_ENV has no default and
# JWT_SECRET_KEY is required with an entropy check. A developer or CI runner with
# no `.env` must still be able to run the suite, so supply the same values the
# local `.env` carries. `setdefault` keeps real environment variables (CI, or a
# developer's own `.env`-exported values) authoritative.
os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-not-for-prod-0f3a9c7e1b5d2846")
# ALLOW_DEV_OTP is forced OFF, not merely left unset. A developer's `.env` sets
# it to true so manual logins work without WhatsApp credentials, and
# pydantic-settings reads `.env` — so leaving it alone means the suite silently
# runs with the dev rail on, which is NOT the configuration production runs.
# An explicit environment variable outranks `.env`, hence the assignment.
os.environ["ALLOW_DEV_OTP"] = "false"
#
# Connection details are deliberately NOT defaulted here: they are environment
# specific (ports differ between the local stack and CI) and belong in `.env` or
# in the CI job's env block. Guessing them would mask a real misconfiguration.

import asyncpg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.passwords import hash_password
from app.core.totp import _decode_key, _hotp, current_step, generate_secret
from app.models import Base

TEMPLATE_DB = "realtaxihk_test_tpl"

# Seeded into the template DB, so every per-test clone already has one admin.
# Random per session — see the module docstring.
ADMIN_ID = str(uuid.uuid4())
ADMIN_PHONE = "+85200000000"


def _admin_dsn() -> str:
    s = get_settings()
    return (
        f"postgresql://{s.postgres_user}:{s.postgres_password}"
        f"@{s.postgres_host}:{s.postgres_port}/postgres"
    )


def _db_url(name: str) -> str:
    s = get_settings()
    return (
        f"postgresql+asyncpg://{s.postgres_user}:{s.postgres_password}"
        f"@{s.postgres_host}:{s.postgres_port}/{name}"
    )


async def _ensure_template() -> None:
    """Fresh template DB with full schema — rebuilt once per pytest session."""
    conn = await asyncpg.connect(dsn=_admin_dsn())
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", TEMPLATE_DB)
        if exists:
            await conn.execute(f'DROP DATABASE "{TEMPLATE_DB}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEMPLATE_DB}"')
    finally:
        await conn.close()
    engine = create_async_engine(_db_url(TEMPLATE_DB), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            await conn.run_sync(Base.metadata.create_all)
            # The admin every *_admin_token() helper signs for. Random id, one
            # per session — see the module docstring.
            #
            # `account_status` is named explicitly: the migration drops the
            # server_default after backfilling (so a future INSERT that forgets
            # the column cannot silently mint an ACTIVE account), which means a
            # raw INSERT here must supply it. tests/test_admin_auth_api.py
            # asserts that the default really is gone.
            await conn.execute(
                text(
                    "INSERT INTO users (id, phone_e164, role, is_active, created_at, "
                    "account_status) "
                    "VALUES (:id, :phone, 'ADMIN', true, now(), 'ACTIVE') "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": ADMIN_ID, "phone": ADMIN_PHONE},
            )
    finally:
        await engine.dispose()


async def _create_test_db(name: str) -> None:
    conn = await asyncpg.connect(dsn=_admin_dsn())
    try:
        await conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE_DB}"')
    finally:
        await conn.close()


async def _drop_test_db(name: str) -> None:
    conn = await asyncpg.connect(dsn=_admin_dsn())
    try:
        await conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    finally:
        await conn.close()


def pytest_configure(config):
    """Build the template DB once per session.

    Without this guard, a stopped Docker/Postgres surfaces as a 30-line
    `INTERNALERROR` traceback ending in a raw socket error, which reads like a
    broken test suite rather than missing infrastructure. Fail with the actual
    remedy instead.
    """
    try:
        asyncio.run(_ensure_template())
    except (OSError, asyncpg.PostgresError) as exc:
        s = get_settings()
        raise pytest.UsageError(
            f"cannot reach Postgres at {s.postgres_host}:{s.postgres_port} — "
            "start the stack first: `docker compose up -d db redis`. "
            f"Original error: {type(exc).__name__}: {exc}"
        ) from exc


def _clear_rate_limits() -> None:
    """P1-2 isolation: OTP/order rate-limit counters must not leak across tests.

    Uses a throwaway Redis client (not the app singleton) so no event-loop
    state is shared with the TestClient portal.
    """
    import redis.asyncio as aioredis

    async def _inner():
        r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        try:
            keys = [k async for k in r.scan_iter(match="rl:*")]
            if keys:
                await r.delete(*keys)
        finally:
            await r.aclose()

    asyncio.run(_inner())


class _CapturingWhatsAppProvider:
    """Records OTP codes instead of sending them (see the module docstring)."""

    def __init__(self, sink: dict[str, str]):
        self.sink = sink

    async def send_otp(self, phone_e164: str, code: str) -> None:
        self.sink[phone_e164] = code

    async def send_trip_update(self, phone_e164: str, message: str) -> None:
        return None


@pytest.fixture(autouse=True)
def otp_inbox(monkeypatch) -> dict[str, str]:
    """The code most recently sent to each phone, keyed by E.164 phone.

    `otp_service` calls `get_whatsapp_provider()` through its own module global,
    so that is the name to patch — patching `app.services.notify` would leave
    the already-bound reference in place.
    """
    from app.services import otp_service

    sink: dict[str, str] = {}
    monkeypatch.setattr(
        otp_service, "get_whatsapp_provider", lambda: _CapturingWhatsAppProvider(sink)
    )
    return sink


@pytest.fixture()
def client(otp_inbox) -> TestClient:
    """App wired to a fresh per-test database (one engine for the whole test)."""
    from app.core.db import get_session, get_session_factory
    from app.main import create_app

    dbname = f"realtaxihk_t_{uuid.uuid4().hex[:10]}"
    asyncio.run(_create_test_db(dbname))
    engine = create_async_engine(_db_url(dbname), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _gen():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _clear_rate_limits()
    try:
        app = create_app()
        app.dependency_overrides[get_session] = _gen
        app.dependency_overrides[get_session_factory] = lambda: factory
        with TestClient(app) as tc:
            tc.db_url = _db_url(dbname)  # service-level concurrency tests
            tc.db_factory = factory
            tc.otp_inbox = otp_inbox  # helpers read the code the app really sent

            # Account helpers, bound to this client's database. Attached rather
            # than exposed as fixtures so a legacy helper can become
            # `client.activate(phone)` with a one-line change, instead of every
            # call site needing a new fixture threaded through its signature.
            def _sign_in(phone: str) -> str:
                return sign_in(tc, phone)

            def _activate(phone: str, *, username: str | None = None) -> str:
                return activate(tc, phone, username=username)

            def _exec(sql: str, params: dict | None = None) -> None:
                return _exec_sync(tc, sql, params)

            def _admin_headers(username: str | None = None) -> dict[str, str]:
                return admin_headers(tc, username=username)

            tc.sign_in = _sign_in
            tc.activate = _activate
            tc.exec_sql = _exec
            tc.admin_headers = _admin_headers
            yield tc
    finally:
        asyncio.run(engine.dispose())
        asyncio.run(_drop_test_db(dbname))


class AdminHeaders(dict):
    """An Authorization header dict that carries the admin's id alongside it.

    Subclasses `dict` so it can be passed straight to `client.get(..., headers=...)`
    without a call site having to strip anything — sending an `X-Admin-Id` would
    be at best ignored and at worst a client-supplied identity. The id is kept in
    a private attribute instead of a key, so "the headers" and "the id" never
    get mixed up.
    """

    admin_id: str

    def __init__(self, authorization: str, admin_id: str) -> None:
        super().__init__(Authorization=authorization)
        self.admin_id = admin_id


def admin_headers(client, *, username: str | None = None) -> AdminHeaders:
    """Sign in as a **real console admin**, returning bearer headers.

    This is the honest way to reach `/api/v1/admin/*`: provision an
    `admin_accounts` row, walk both factors (password, then TOTP), and use the
    token that comes out. It replaces the old pattern of minting
    `create_access_token({"sub": ADMIN_ID, "role": "ADMIN"})` for a
    `users.role = ADMIN` row.

    That pattern is now refused, and deliberately so. `admin_accounts` and
    `users` are separate identity types with independent UUID spaces; a token
    whose `sub` came from `users` cannot be distinguished from one whose `sub`
    came from `admin_accounts` except by the `scope` claim, which only the real
    login sets. Keeping the shortcut working would mean keeping two ways to be
    an admin — and a stray INSERT into `users` granting access to refunds and
    settlement.

    Each call provisions a distinct account (a counter, not a fixed name) so a
    test that calls this twice does not collide on the unique username index.
    """
    admin_headers._seq += 1
    seq = admin_headers._seq
    name = username or f"testadmin{seq}"
    email = f"{name}@realtaxi.hk"
    # Not "TestAdmin!2026-hk" — `_WEAK_FRAGMENTS` blocks the substring "admin",
    # so a password containing the word its own account type is named after
    # fails the policy and every caller errors in setup. The content here is
    # arbitrary; what matters is that it clears `_assert_policy`.
    password = "Harbour-Kite-9pLq"
    admin_id = uuid.uuid4()
    secret = generate_secret()

    _exec_sync(
        client,
        "INSERT INTO admin_accounts "
        "(id, username, email, full_name, password_hash, totp_secret, totp_enrolled_at, "
        " is_active, failed_login_count, created_at, updated_at) "
        "VALUES (:id, :u, :e, 'Test Admin', :pw, CAST(:secret AS text), now(), "
        " true, 0, now(), now())",
        {
            "id": admin_id,
            "u": name,
            "e": email,
            "pw": hash_password(password),
            "secret": secret,
        },
    )

    r = client.post(
        "/api/v1/admin/auth/login", json={"username": name, "password": password}
    )
    assert r.status_code == 200, r.text
    challenge = r.json()["challenge_token"]
    r = client.post(
        "/api/v1/admin/auth/totp/verify",
        json={"challenge_token": challenge, "code": _hotp(_decode_key(secret), current_step(), 6)},
    )
    assert r.status_code == 200, r.text
    return AdminHeaders(f"Bearer {r.json()['access_token']}", str(admin_id))


admin_headers._seq = 0


@pytest.fixture()
async def db_session():
    dbname = f"realtaxihk_t_{uuid.uuid4().hex[:10]}"
    await _create_test_db(dbname)
    engine = create_async_engine(_db_url(dbname), poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with maker() as session:
            yield session
            await session.commit()
    finally:
        await engine.dispose()
        await _drop_test_db(dbname)


# --------------------------------------------------------------------------- #
# Shared account helpers
# --------------------------------------------------------------------------- #


def _exec_sync(client, sql: str, params: dict | None = None) -> None:
    """Run one statement on the test's own database, synchronously.

    Uses its own engine — not the app's, which is bound to the TestClient's
    event loop and would deadlock if driven from here.

    `asyncio.run` cannot be called from inside a running loop, and some tests are
    `async def` (they need `db_session`). Those get the statement executed on a
    worker thread with its own loop, which is the only way to reach the database
    from sync code in that situation. Detected via a try/except rather than
    `asyncio.get_running_loop()` so the sync path stays the cheap, common one.
    """

    async def _inner():
        engine = create_async_engine(client.db_url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql), params or {})
        finally:
            await engine.dispose()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_inner())
        return

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, _inner()).result()


def sign_in(client, phone: str) -> str:
    """Phone-OTP login, returning an access token. Creates the account if new.

    Backdates the previous OTP rows first. A code is single-use and a resend
    cooldown blocks a fresh request while one is recent — both are production
    behaviour, and both make a *second* sign-in in one test impossible without
    this. Backdating is arrangement, not a bypass: the app under test still sees
    a genuine single-use code inside its cooldown window.
    """
    _exec_sync(
        client,
        "UPDATE otp_codes SET created_at = created_at - interval '10 minutes', "
        "consumed_at = NULL WHERE phone_e164 = :p",
        {"p": phone},
    )
    r = client.post("/api/v1/auth/otp/request", json={"phone_e164": phone})
    assert r.status_code == 200, r.text
    r = client.post(
        "/api/v1/auth/otp/verify",
        json={"phone_e164": phone, "code": client.otp_inbox[phone]},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def activate(client, phone: str, *, username: str | None = None) -> str:
    """Sign in and bring the account to ACTIVE, returning its access token.

    P-2 gates every business route on `AccountStatus.ACTIVE` via
    `require_verified_account`, and P-4 layers a phone-deadline check on top
    (`require_phone_current`). A test that is *about* fleets, settlement or
    orders has to clear those gates first, or it asserts the wrong refusal.

    Writing the row directly rather than walking the profile + email-verify
    endpoints is deliberate: this helper is about getting an account out of the
    way, and doing it through the API would couple every unrelated test file to
    whatever P-2's registration flow looks like next month. The flows themselves
    are covered by `test_identity_api.py` and `test_phone_reverify.py`.
    """
    token = sign_in(client, phone)
    _exec_sync(
        client,
        "UPDATE users SET account_status = 'ACTIVE', "
        "phone_verified_at = COALESCE(phone_verified_at, now()), "
        "phone_reverify_due_at = now() + make_interval(days => :d), "
        "email = COALESCE(email, :e), "
        "email_verified_at = COALESCE(email_verified_at, now()), "
        "username = COALESCE(username, :u) WHERE phone_e164 = :p",
        {
            "p": phone,
            "u": username or ("u" + phone[-6:]),
            "e": f"{phone.lstrip('+')}@example.hk",
            "d": get_settings().phone_reverify_interval_days,
        },
    )
    # A fresh token: `require_active_user` compares the JWT role claim against
    # the live row, and the UPDATE above does not change the role — this
    # re-sign-in exists so callers that promote to ADMIN afterwards get a token
    # minted against current state, matching the pattern the helper documents.
    return token
