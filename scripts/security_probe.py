"""Security verification probe — proves each finding with a REAL request.

Run:  .venv/Scripts/python.exe scripts/security_probe.py
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402

from app.core.config import get_settings  # noqa: E402

BASE = "http://127.0.0.1:8000"
# Child-server logs go to a fresh tempdir: the repo root is not a scratch space,
# and a hard-coded `.tmp` is gitignored and absent on a clean checkout.
LOGDIR = Path(tempfile.mkdtemp(prefix="realtaxi-probe-logs-"))
RUN = int(time.time()) % 9000  # unique phones/XFF per run -> probe is re-runnable
_IPN = [0]


def _fresh_ip():
    """A new claimed client IP per call (also demonstrates the SEC-07 bypass)."""
    _IPN[0] += 1
    return f"10.{RUN % 250}.{_IPN[0] // 250}.{_IPN[0] % 250 + 1}"


FINDINGS = []
S = get_settings()

# DB/Redis/secret values come from .env; APP_ENV is deliberately withheld in the
# "no .env" tests so the field default applies — which is the real-world case
# for the Docker image, whose Dockerfile never COPYs .env.
BASE_ENV = {
    "POSTGRES_HOST": S.postgres_host,
    "POSTGRES_PORT": str(S.postgres_port),
    "POSTGRES_USER": S.postgres_user,
    "POSTGRES_PASSWORD": S.postgres_password,
    "POSTGRES_DB": S.postgres_db,
    "REDIS_URL": S.redis_url,
    "JWT_SECRET_KEY": S.jwt_secret_key,
}


def record(tag, title, proven, detail):
    # A "Control:" entry is a negative control — it is SUPPOSED to reproduce (e.g.
    # "a fixed XFF does get throttled"). Labelling it VULNERABLE would be wrong;
    # its only job is to prove the bypass next to it is a real difference.
    if title.startswith("Control"):
        label = "control ok" if proven else "CONTROL BROKEN"
    else:
        label = "VULNERABLE" if proven else "not reproduced"
    print(f"  [{label}] {tag} {title}")
    print(f"        {detail}")
    FINDINGS.append((tag, title, proven, detail))


def req(method, path, body=None, token=None, headers=None, raw=None):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(r, data=data, timeout=180) as resp:
            b = resp.read()
            return resp.status, json.loads(b or b"{}"), time.perf_counter() - t0, len(b)
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            parsed = json.loads(b or b"{}")
        except Exception:
            parsed = {"_raw": b[:200].decode("utf-8", "replace")}
        return e.code, parsed, time.perf_counter() - t0, len(b)


def port_free():
    s = socket.socket()
    free = s.connect_ex(("127.0.0.1", 8000)) != 0
    s.close()
    return free


def boot(env_overrides, log_name, cwd=None, extra_env=None):
    env = dict(os.environ)
    env.update(BASE_ENV)
    env.pop("APP_ENV", None)
    env.update(env_overrides or {})
    env.update(extra_env or {})
    log = open(LOGDIR / log_name, "w", encoding="utf-8")  # handed to the child below
    proc = subprocess.Popen(
        # SEC-31: launch the way production launches. Without --no-proxy-headers
        # uvicorn rewrites scope["client"] from X-Forwarded-For below the app, so
        # group D would report the SEC-07 bypass as still present even though the
        # app-level fix is correct — the flag is part of the fix.
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            "8000",
            "--no-proxy-headers",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=str(cwd or ROOT),
        env=env,
    )
    for _ in range(60):
        if proc.poll() is not None:
            return proc, False
        s = socket.socket()
        up = s.connect_ex(("127.0.0.1", 8000)) == 0
        s.close()
        if up:
            return proc, True
        time.sleep(0.5)
    return proc, False


def stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def reset_rate_limit_buckets():
    """Clear this app's rate-limit windows.

    Called between groups, because several checks deliberately exhaust a bucket
    to prove the finding: group B floods the per-IP OTP bucket, and the SEC-08
    check drives the fare bucket to its limit. Those counters live in Redis with
    a 600s TTL, so they outlive the server restart between groups — without a
    reset, every later group just gets 429s and the probe dies on its first
    login. Only this app's own `rl:realtaxi:*` keys are touched.
    """

    async def run():
        import redis.asyncio as aioredis

        cli = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        try:
            keys = [k async for k in cli.scan_iter("rl:realtaxi:*")]
            if keys:
                await cli.delete(*keys)
        finally:
            await cli.aclose()

    asyncio.run(run())


async def db_exec(sql, *args):
    s = get_settings()
    c = await asyncpg.connect(
        dsn=f"postgresql://{s.postgres_user}:{s.postgres_password}"
        f"@{s.postgres_host}:{s.postgres_port}/{s.postgres_db}"
    )
    try:
        return await c.fetch(sql, *args)
    finally:
        await c.close()


def mk_token(phone):
    h = {"X-Forwarded-For": _fresh_ip()}
    st, b, _, _ = req("POST", "/api/v1/auth/otp/request", {"phone_e164": phone}, headers=h)
    assert st == 200, f"otp request {st} {b}"
    code = b.get("dev_code") or "123456"
    st, b2, _, _ = req(
        "POST", "/api/v1/auth/otp/verify", {"phone_e164": phone, "code": code}, headers=h
    )
    assert st == 200, f"otp verify {st} {b2}"
    return b2["access_token"], b2["user"]["id"], b.get("dev_code")


# --------------------------------------------------------------------------- #
# A. APP_ENV default -> dev auth bypass
# --------------------------------------------------------------------------- #
def check_a():
    print("\n[A] APP_ENV default (no .env, var unset) -> dev auth bypass")
    if not port_free():
        print("  port 8000 busy, skipping")
        return
    # An empty cwd means pydantic-settings finds no .env -> field defaults apply,
    # exactly like the Docker image (the Dockerfile never COPYs .env). A fresh
    # tempdir, not a hard-coded `.tmp` (gitignored, absent on a clean checkout).
    tmp = Path(tempfile.mkdtemp(prefix="realtaxi-probe-"))
    proc, up = boot({}, "sec_a1.log", cwd=tmp, extra_env={"PYTHONPATH": str(ROOT)})
    try:
        if not up:
            # Refusing to boot IS the fix (SEC-01~05). APP_ENV is now required and
            # fail-closed, so there is no dev-defaulted server left to attack —
            # this assertion used to be `assert up, "server did not boot"`.
            for tag, title in (
                ("SEC-01", "OTP code is returned in the API response"),
                ("SEC-02", "OTP code is the fixed constant 123456, not random"),
                ("SEC-03", "Login succeeds with the hardcoded code 123456"),
            ):
                record(
                    tag,
                    title,
                    False,
                    "server refused to boot with APP_ENV unset: the dev default is gone, "
                    "so there is no dev-mode server for the attack to run against.",
                )
            return
        st, health, _, _ = req("GET", "/health")
        env_reported = health.get("env")
        print(f"        /health reports env={env_reported!r} (no .env, no APP_ENV set)")

        st, b, _, _ = req("POST", "/api/v1/auth/otp/request", {"phone_e164": f"+8529111{RUN:04d}"})
        leaked = b.get("dev_code")
        record(
            "SEC-01",
            "OTP code is returned in the API response",
            st == 200 and leaked is not None,
            f"POST /otp/request -> {st}, response contains dev_code={leaked!r}. "
            f"The code is handed to whoever asked for it, so the OTP proves nothing.",
        )

        st2, b2, _, _ = req(
            "POST", "/api/v1/auth/otp/request", {"phone_e164": f"+8529112{RUN:04d}"}
        )
        record(
            "SEC-02",
            "OTP code is the fixed constant 123456, not random",
            b2.get("dev_code") == "123456" and leaked == "123456",
            f"two different phones: dev_code={leaked!r} and {b2.get('dev_code')!r}. "
            f"Verify with 123456 for ANY phone -> instant account takeover "
            f"(and verify auto-creates the account if it does not exist).",
        )
        st, b3, _, _ = req(
            "POST",
            "/api/v1/auth/otp/verify",
            {"phone_e164": f"+8529111{RUN:04d}", "code": "123456"},
        )
        record(
            "SEC-03",
            "Login succeeds with the hardcoded code 123456",
            st == 200,
            f"POST /otp/verify with code=123456 -> {st}; returned a usable "
            f"access_token + refresh_token.",
        )
    finally:
        stop(proc)

    # The typo hole: any APP_ENV value that is not literally "prod" skips the guard.
    proc, up = boot({"APP_ENV": "production"}, "sec_a2.log")
    try:
        record(
            "SEC-04",
            "APP_ENV=production boots with the repo's DEV jwt secret + dev DB password",
            up,
            "the validator only tests `app_env == 'prod'`, so 'production', 'PROD', "
            "'staging' or a typo skips every prod safety check and boots anyway."
            if up
            else "refused to boot",
        )
        if up:
            import jwt as pyjwt

            forged = pyjwt.encode(
                {
                    "sub": "00000000-0000-0000-0000-0000000000aa",
                    "role": "ADMIN",
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 3600,
                },
                "dev-only-secret-change-in-prod-0123456789abcdef-0123456789abcdef",
                algorithm="HS256",
            )
            st, b, _, _ = req("GET", "/api/v1/admin/refunds", token=forged)
            record(
                "SEC-05",
                "An ADMIN JWT is forgeable offline from the committed secret",
                st == 200,
                f"forged a token with the JWT secret that is hardcoded in "
                f"app/core/config.py -> GET /api/v1/admin/refunds = {st}. "
                f"Admin access to refunds, KYC review and deposit grants.",
            )
    finally:
        stop(proc)


# --------------------------------------------------------------------------- #
# B. X-Forwarded-For spoofing defeats every IP rate limit
# --------------------------------------------------------------------------- #
def check_b():
    print("\n[B] Rate-limit bypass via X-Forwarded-For")
    if not port_free():
        print("  port 8000 busy, skipping")
        return
    proc, up = boot({"APP_ENV": "dev"}, "sec_b.log")
    try:
        assert up
        limit = S.otp_ip_rate_limit
        codes = [
            req(
                "POST",
                "/api/v1/auth/otp/request",
                {"phone_e164": f"+8529{i:07d}"},
                headers={"X-Forwarded-For": f"203.0.113.{RUN % 250 + 1}"},
            )[0]
            for i in range(limit + 3)
        ]
        record(
            "SEC-06",
            f"Control: a fixed XFF is throttled after {limit} requests",
            codes.count(429) > 0,
            f"statuses={codes}. Control case — proves the limiter works, so the "
            f"bypass below is a real difference.",
        )

        n = limit * 4
        codes = [
            req(
                "POST",
                "/api/v1/auth/otp/request",
                {"phone_e164": f"+8528{i:07d}"},
                headers={"X-Forwarded-For": f"198.51.100.{i % 254}"},
            )[0]
            for i in range(n)
        ]
        record(
            "SEC-07",
            "Rotating X-Forwarded-For bypasses the OTP IP limit completely",
            codes.count(429) == 0,
            f"{n} requests with a rotating XFF -> {codes.count(429)} x 429. "
            f"_client_ip() uses XFF.split(',')[0] — the client-supplied hop — so "
            f"even behind nginx ($proxy_add_x_forwarded_for APPENDS the real IP) "
            f"the first element stays attacker-controlled. Same helper guards "
            f"nothing else, but this is the only brake on OTP cost.",
        )
    finally:
        stop(proc)


# --------------------------------------------------------------------------- #
# C. Unauthenticated fare endpoint: uncapped list + no body limit
# --------------------------------------------------------------------------- #
def check_c():
    print("\n[C] Unauthenticated /api/v1/fare/estimate abuse")
    if not port_free():
        print("  port 8000 busy, skipping")
        return
    proc, up = boot({"APP_ENV": "dev"}, "sec_c.log")
    try:
        assert up
        base = {
            "taxi_type": "URBAN",
            "distance_km": "10",
            "waiting_min": "5",
            "crosses_harbour": True,
            "tunnels": ["cross_harbour"],
        }
        st, b, t0, _ = req("POST", "/api/v1/fare/estimate", base)
        # A single anonymous request succeeding is NOT the finding — the endpoint
        # is public by design. The finding was that nothing throttled it, so drive
        # the limiter and look for the 429. (SEC-15 added the per-IP limit.)
        fare_limit = get_settings().fare_estimate_ip_rate_limit
        after = [req("POST", "/api/v1/fare/estimate", base)[0] for _ in range(fare_limit + 5)]
        record(
            "SEC-08",
            "/fare/estimate is unthrottled for an anonymous caller",
            st == 200 and after.count(429) == 0,
            f"no Authorization header -> {st} in {t0:.3f}s, then {fare_limit + 5} more "
            f"requests -> {after.count(429)} x 429 (limit={fare_limit}/IP/window). "
            f"Public by design, but it was also unthrottled and uncapped.",
        )
        # Group C's remaining checks all post to this endpoint; the drive above
        # left the bucket at its limit.
        reset_rate_limit_buckets()

        # Amplification: 100k INVALID enum values -> every one is reported back.
        n_bad = 100_000
        st, b, t, size = req(
            "POST",
            "/api/v1/fare/estimate",
            {**base, "tunnels": ["x"] * n_bad},
        )
        detail = b.get("details", {})
        errs = detail.get("errors") if isinstance(detail, dict) else None
        record(
            "SEC-09",
            "Invalid enum list is echoed back in full (response amplification)",
            st == 422 and size > 500_000,
            f"{n_bad:,} invalid tunnels -> HTTP {st}, response body {size / 1e6:.1f} MB "
            f"in {t:.2f}s ({len(errs) if isinstance(errs, list) else '?'} error objects). "
            f"A small request becomes a huge response: bandwidth + CPU amplification, "
            f"and it is unauthenticated.",
        )

        # No 413: the server accepts an arbitrarily large body.
        big = 60 * 1024 * 1024
        st, b, t, _ = req(
            "POST",
            "/api/v1/fare/estimate",
            raw=b'{"taxi_type":"URBAN","distance_km":"10","tunnels":[],"_pad":"'
            + b"A" * big
            + b'"}',
        )
        record(
            "SEC-10",
            "No request-body size limit (60 MB accepted, not 413)",
            st != 413,
            f"a {big / 1e6:.0f} MB JSON body -> HTTP {st} in {t:.2f}s. "
            f"Starlette/uvicorn impose no default cap, so concurrent large POSTs "
            f"exhaust worker memory. (422 here means it was parsed, not rejected.)",
        )

        # Same uncapped list, but persisted and echoed back on the orders path.
        token, _uid, _ = mk_token(f"+8529555{RUN:04d}")
        big_tunnels = ["cross_harbour"] * 200_000
        st, order, t, size = req(
            "POST",
            "/api/v1/orders",
            {
                "pickup_lat": 22.3,
                "pickup_lng": 114.17,
                "dropoff_lat": 22.32,
                "dropoff_lng": 114.2,
                "pickup_address": "Central",
                "dropoff_address": "Causeway Bay",
                "distance_km": "3.5",
                "taxi_type": "URBAN",
                "crosses_harbour": True,
                "tunnels": big_tunnels,
            },
            token=token,
        )
        rows = asyncio.run(
            db_exec("SELECT length(fare_json::text) FROM orders ORDER BY created_at DESC LIMIT 1")
        )
        jsonb_len = rows[0][0] if rows else -1
        record(
            "SEC-11",
            "tunnels has no max_length -> unbounded row in fare_json (JSONB)",
            st == 201 and jsonb_len > 1_000_000,
            f"one order with {len(big_tunnels):,} tunnels -> HTTP {st}; "
            f"fare_json is {jsonb_len / 1e6:.1f} MB in Postgres, and every "
            f"GET /orders re-sends it. 5 orders/min/user x N users = storage and "
            f"egress amplification from an authenticated account.",
        )
    finally:
        stop(proc)


# --------------------------------------------------------------------------- #
# D. P0-3 gap: deactivated accounts keep access
# --------------------------------------------------------------------------- #
def check_d():
    print("\n[D] Deactivated-account enforcement gap (P0-3)")
    if not port_free():
        print("  port 8000 busy, skipping")
        return
    proc, up = boot({"APP_ENV": "dev"}, "sec_d.log")
    try:
        assert up
        token, user_id, _ = mk_token(f"+8529444{RUN:04d}")
        st, prof, _, _ = req(
            "POST",
            "/api/v1/drivers/register",
            {
                "hk_id_last4": "4001",
                "taxi_driver_plate_no": "TD4001",
                "vehicle_reg_mark": "SM4001",
                "taxi_type": "URBAN",
            },
            token=token,
        )
        driver_id = prof["id"]
        # Make the driver genuinely ACTIVE (bypassing KYC) so the only variable
        # left is users.is_active.
        asyncio.run(
            db_exec(
                "UPDATE driver_profiles SET status='ACTIVE' WHERE id=$1",
                __import__("uuid").UUID(driver_id),
            )
        )
        # An order so the trip-snapshot route has something to authorise against.
        st, order, _, _ = req(
            "POST",
            "/api/v1/orders",
            {
                "pickup_lat": 22.3,
                "pickup_lng": 114.17,
                "dropoff_lat": 22.32,
                "dropoff_lng": 114.2,
                "pickup_address": "Central",
                "dropoff_address": "Causeway Bay",
                "distance_km": "3.5",
                "taxi_type": "URBAN",
            },
            token=token,
        )
        order_id = order["id"]

        asyncio.run(
            db_exec(
                "UPDATE users SET is_active=false WHERE id=$1", __import__("uuid").UUID(user_id)
            )
        )

        probes = [
            ("GET", "/api/v1/drivers/me", None),
            ("GET", "/api/v1/drivers/me/ledger", None),
            ("GET", "/api/v1/drivers/me/refund", None),
            ("POST", "/api/v1/driver/location", {"lat": 22.3, "lng": 114.17}),
            ("GET", f"/api/v1/trips/{order_id}/location", None),
            ("GET", "/api/v1/orders", None),  # control: uses require_active_user
        ]
        open_routes = []
        for method, path, body in probes:
            st, b, _, _ = req(method, path, body, token=token)
            control = path == "/api/v1/orders"
            tag = "control" if control else ("OPEN" if st == 200 else "blocked")
            print(f"        {st}  {method:5} {path:38} {tag}")
            if st == 200 and not control:
                open_routes.append(f"{method} {path}")

        record(
            "SEC-12",
            "Deactivated account still reads profile/ledger/refund and streams GPS",
            bool(open_routes),
            f"after users.is_active=false these still returned 200: "
            f"{'; '.join(open_routes)}. Six routes depend on the JWT-only "
            f"get_current_user instead of require_active_user, so a banned user "
            f"keeps access until the access token expires "
            f"({S.access_token_expire_minutes} min) — contradicting orders.py's own "
            f"claim that 'P0-3 all user-facing routes run require_active_user'.",
        )
    finally:
        stop(proc)


def main():
    print("=" * 74)
    print("realtaxihk security probe — verified against a live uvicorn + Postgres")
    print("=" * 74)
    want = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    for name, fn in (("a", check_a), ("b", check_b), ("c", check_c), ("d", check_d)):
        if want in ("all", name):
            reset_rate_limit_buckets()
            fn()
    print("\n" + "=" * 74)
    proven = [f for f in FINDINGS if f[2]]
    print(f"PROVEN: {len(proven)} of {len(FINDINGS)}")
    for tag, title, ok, _ in FINDINGS:
        print(f"  {'!!' if ok else '  '} {tag}  {title}")
    print("=" * 74)


if __name__ == "__main__":
    main()
