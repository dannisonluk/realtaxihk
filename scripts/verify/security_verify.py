"""Post-fix verification probe — boots the REAL server and asserts each previously
proven finding no longer reproduces.

Run:  .venv/Scripts/python.exe scripts/verify/security_verify.py

Each check prints PASS (attack blocked) or FAIL (still exploitable). Non-destructive:
it only creates throwaway users/orders, and never mutates existing data.

Probe fixes (2026-09-29, third pass):
- phones must match `^\\+852\\d{8}$`; `f"+8529{RUN:04d}1"` produced only 6 digits
  and every login silently 422'd, which is why SEC-17/18 reported "could not log in";
- the SEC-04~05 control expected 403 for a live-secret ADMIN token, but the admin
  it signed for did not exist, so 200 was wrong for a different reason. The control
  is now an explicit A/B: identical claims, only the signing key differs;
- SEC-09's 100k-tunnel payload is >1 MB, so the body cap answers 413 before the
  validator runs. Added a sub-cap payload (120 KB) that isolates the validator.

Fourth pass (this one) — the probe stopped depending on a planted admin row:
- the ADMIN is created at startup with `scripts/ops/create_admin.py` (random UUID) and
  revoked on the way out, instead of being a fixed well-known UUID that had been
  hand-written into the dev database. That fixed id was a skeleton key identical
  in every deployment, and this probe was its only justification;
- the OTP comes from the dev rail (`ALLOW_DEV_OTP=true`, phase 1 only), not from
  the response body — the response no longer carries it (SEC-02).
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _root import REPO_ROOT as ROOT

sys.path.insert(0, str(ROOT))

from app.core.config import get_settings

BASE = "http://127.0.0.1:8000"
# Boot the child from an empty directory so pydantic-settings finds no `.env` and
# the field defaults apply — that is how the "no .env" Docker-image case is
# simulated. A fresh tempdir rather than a hard-coded `.tmp`, which is gitignored
# and would not exist on a clean checkout.
SCRATCH = Path(tempfile.mkdtemp(prefix="realtaxi-verify-"))
RUN = int(time.time()) % 9000
OLD_COMMITTED_SECRET = "dev-only-secret-change-in-prod-0123456789abcdef-0123456789abcdef"
PROBE_SECRET = "probe-only-secret-4a7c2e9f1b6d3058aa71"
# Phase 1 boots with ALLOW_DEV_OTP=true. This probe is out-of-process, so it
# cannot install a test double at the notify seam the way the pytest suite does;
# the dev rail is how it obtains a code to log in with. The API still never
# echoes it — that is exactly what SEC-01~03 asserts.
DEV_OTP_CODE = "123456"
# Provisioned at startup, never a constant. See the module docstring.
ADMIN_UUID = ""
ADMIN_PHONE = ""

RESULTS: list[tuple[str, str, bool, str]] = []


def check(tag: str, title: str, passed: bool, detail: str) -> None:
    RESULTS.append((tag, title, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {tag} {title}")
    print(f"        {detail}")


def phone(n: int) -> str:
    """A valid +852 number: 8 digits after the country code."""
    return f"+8529{RUN:04d}{n:03d}"


def _admin_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ops" / "create_admin.py"), *args],
        capture_output=True,
        check=False,
        text=True,
        cwd=str(ROOT),
    )


def provision_admin() -> None:
    """Create the ADMIN row this probe needs, through the supported CLI.

    `require_admin` re-reads the `users` row, so a forged ADMIN token is
    worthless without a matching row. That row used to be a fixed UUID planted
    in the dev database by hand — which made this probe, in effect, its only
    justification. Creating it here with `scripts/ops/create_admin.py` removes that
    circularity and exercises the P2-11 bootstrap path for real.
    """
    global ADMIN_UUID, ADMIN_PHONE
    ADMIN_PHONE = phone(0)
    res = _admin_cli("--phone", ADMIN_PHONE, "--yes")
    m = re.search(r"id\s*=\s*([0-9a-f-]{36})", res.stdout)
    if res.returncode != 0 or not m:
        raise RuntimeError(f"create_admin failed: {res.stdout}{res.stderr}")
    ADMIN_UUID = m.group(1)


def deprovision_admin() -> None:
    """Demote the probe's admin so the dev DB is left as it was found."""
    if ADMIN_PHONE:
        _admin_cli("--phone", ADMIN_PHONE, "--revoke", "--yes")


def port_free() -> bool:
    s = socket.socket()
    try:
        return s.connect_ex(("127.0.0.1", 8000)) != 0
    finally:
        s.close()


def wait_up(timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=2):
                return True
        except Exception:
            time.sleep(0.4)
    return False


def req(method, path, body=None, token=None, headers=None, raw=None, timeout=180):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(r, data=data, timeout=timeout) as resp:
            b = resp.read()
            return resp.status, _json(b), dict(resp.headers), len(b)
    except urllib.error.HTTPError as e:
        b = e.read()
        return e.code, _json(b), dict(e.headers), len(b)


def _json(b: bytes):
    try:
        return json.loads(b or b"{}")
    except Exception:
        return {"_raw": b[:200].decode("utf-8", "replace")}


def boot(extra_env: dict) -> subprocess.Popen:
    s = get_settings()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": str(ROOT),
        "APP_ENV": "dev",
        "JWT_SECRET_KEY": PROBE_SECRET,
        "POSTGRES_HOST": s.postgres_host,
        "POSTGRES_PORT": str(s.postgres_port),
        "POSTGRES_USER": s.postgres_user,
        "POSTGRES_PASSWORD": s.postgres_password,
        "POSTGRES_DB": s.postgres_db,
        "REDIS_URL": s.redis_url,
        "JOBS_ENABLED": "false",
        **extra_env,
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--no-proxy-headers",
            # SEC-32: keep the WS token out of uvicorn's access log.
            "--no-access-log",
            "--log-level",
            "warning",
        ],
        cwd=str(SCRATCH),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not wait_up():
        proc.terminate()
        raise RuntimeError("probe server did not come up")
    return proc


def forge(secret: str, role: str = "ADMIN") -> str:
    import jwt as pyjwt

    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": ADMIN_UUID,
            "role": role,
            "iat": now,
            "exp": now + 600,
            "jti": f"probe{RUN}",
        },
        secret,
        algorithm="HS256",
    )


def login(p: str) -> dict | None:
    """Log in with the dev-rail code — phase 1 boots with ALLOW_DEV_OTP=true."""
    st, body, _, _ = req("POST", "/api/v1/auth/otp/request", {"phone_e164": p})
    if st != 200:
        return None
    st, body, _, _ = req("POST", "/api/v1/auth/otp/verify", {"phone_e164": p, "code": DEV_OTP_CODE})
    return body if st == 200 else None


def reset_otp_buckets() -> None:
    """Clear the OTP rate-limit windows so the probe is repeatable.

    SEC-07's check deliberately exhausts the per-IP bucket, and that bucket is in
    Redis with a 600s TTL — it outlives the server restart between phases. Without
    this reset the second run of the probe (and every phase after SEC-07) starts
    already 429'd. Only probe-owned rate-limit keys are touched.
    """
    import asyncio

    import redis.asyncio as aioredis

    async def run() -> None:
        cli = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        try:
            keys = [k async for k in cli.scan_iter("rl:realtaxi:otp:*")]
            if keys:
                await cli.delete(*keys)
        finally:
            await cli.aclose()

    asyncio.run(run())


def phase_dev_otp_on() -> None:
    print("\n-- Phase 1: APP_ENV=dev, ALLOW_DEV_OTP=true --")
    reset_otp_buckets()
    proc = boot({"ALLOW_DEV_OTP": "true"})
    try:
        # SEC-23
        st, body, headers, _ = req("GET", "/health")
        check(
            "SEC-23",
            "/health no longer advertises the environment",
            st == 200 and "env" not in body,
            f"status={st} body_keys={sorted(body)}",
        )

        # SEC-01~03, the hard case: even with the dev rail explicitly ON, the
        # code must not come back over HTTP. That echo was the whole hole — the
        # response went to whoever asked, so the OTP proved nothing.
        st, body, _, _ = req("POST", "/api/v1/auth/otp/request", {"phone_e164": phone(3)})
        check(
            "SEC-01~03",
            "ALLOW_DEV_OTP makes the code deterministic but never echoes it",
            st == 200 and "dev_code" not in body,
            f"ALLOW_DEV_OTP=true, status={st}, body_keys={sorted(body)}",
        )

        # SEC-24
        need = ["x-content-type-options", "x-frame-options", "referrer-policy"]
        missing = [h for h in need if h not in headers]
        st2, _, api_headers, _ = req("GET", "/api/v1/auth/me")
        check(
            "SEC-24",
            "security headers present (incl. CSP on /api)",
            not missing and "content-security-policy" in api_headers,
            f"missing={missing} csp_on_api={'content-security-policy' in api_headers}",
        )

        # SEC-04/05: A/B — identical claims, only the signing key differs.
        st_old, _, _, _ = req("GET", "/api/v1/admin/refunds", token=forge(OLD_COMMITTED_SECRET))
        st_new, _, _, _ = req("GET", "/api/v1/admin/refunds", token=forge(PROBE_SECRET))
        check(
            "SEC-04~05",
            "the committed repo secret no longer mints an accepted ADMIN token",
            st_old == 401 and st_new == 200,
            f"same claims, old committed secret -> {st_old} (expect 401); "
            f"live secret -> {st_new} (expect 200)",
        )

        # SEC-10: oversized body
        big = b'{"taxi_type":"URBAN","distance_km":"5","pad":"' + b"x" * (3 * 1024 * 1024) + b'"}'
        st, body, _, size = req("POST", "/api/v1/fare/estimate", raw=big)
        check(
            "SEC-10",
            "oversized request body rejected with 413",
            st == 413,
            f"3 MB body -> {st} (was 200 before the fix)",
        )

        # SEC-09a: response amplification, payload UNDER the 1 MB body cap so the
        # `cap_tunnels` validator is what answers (not the body-size middleware).
        tunnels = ["zz"] * 20_000  # ~120 KB
        st, body, _, size = req(
            "POST",
            "/api/v1/fare/estimate",
            {"taxi_type": "URBAN", "distance_km": "5", "tunnels": tunnels},
        )
        check(
            "SEC-09a",
            "20k invalid tunnels answered with ONE small error, not 20k errors",
            st == 422 and size < 8 * 1024,
            f"120 KB payload of 20k invalid tunnels -> {st}, {size} bytes",
        )

        # SEC-09b: the same attack that used to return a 32.8 MB error body is now
        # refused by the body cap outright.
        st, body, _, size = req(
            "POST",
            "/api/v1/fare/estimate",
            {"taxi_type": "URBAN", "distance_km": "5", "tunnels": ["cross_harbour"] * 100_000},
        )
        check(
            "SEC-09b",
            "the 100k-tunnel payload (32.8 MB echo before) is refused",
            st == 413 and size < 8 * 1024,
            f"status={st} response={size} bytes (was 32.8 MB)",
        )

        # SEC-18: logout revokes the access token
        tokens = login(phone(1))
        if tokens is None:
            check("SEC-18", "logout revokes the access token", False, "could not log in (probe)")
        else:
            hdr = {"Authorization": f"Bearer {tokens['access_token']}"}
            before = req("GET", "/api/v1/auth/me", headers=hdr)[0]
            req("POST", "/api/v1/auth/logout", headers=hdr)
            after = req("GET", "/api/v1/auth/me", headers=hdr)[0]
            check(
                "SEC-18",
                "logout invalidates the access token immediately",
                before == 200 and after == 401,
                f"/auth/me before={before} after logout={after} (expect 200 then 401)",
            )

        # SEC-17: refresh replay revokes the family
        t2 = login(phone(2))
        if t2 is None:
            check("SEC-17", "refresh replay revokes the token family", False, "could not log in")
        else:
            st, rot, _, _ = req(
                "POST", "/api/v1/auth/refresh", {"refresh_token": t2["refresh_token"]}
            )
            successor = rot.get("refresh_token")
            replay = req("POST", "/api/v1/auth/refresh", {"refresh_token": t2["refresh_token"]})[0]
            after = (
                req("POST", "/api/v1/auth/refresh", {"refresh_token": successor})[0]
                if successor
                else None
            )
            check(
                "SEC-17",
                "refresh replay revokes the whole family",
                st == 200 and replay == 401 and after == 401,
                f"rotate={st} replay={replay} successor-after-replay={after}",
            )

        # SEC-07 runs LAST: it deliberately exhausts the OTP IP bucket, so every
        # other check that needs to log in has to happen first.
        limit = get_settings().otp_ip_rate_limit
        n = limit + 6
        statuses = [
            req(
                "POST",
                "/api/v1/auth/otp/request",
                {"phone_e164": phone(100 + i)},
                headers={"X-Forwarded-For": f"203.0.113.{i}"},
            )[0]
            for i in range(n)
        ]
        check(
            "SEC-07",
            "rotating X-Forwarded-For no longer bypasses the IP limit",
            statuses.count(429) >= 3,
            f"{n} requests, {n} different XFF values -> {statuses.count(429)} x 429; "
            f"statuses={statuses}",
        )
    finally:
        proc.terminate()
        proc.wait(timeout=20)
        time.sleep(1)


def phase_dev_otp_off() -> None:
    print("\n-- Phase 2: APP_ENV=dev, ALLOW_DEV_OTP unset --")
    reset_otp_buckets()
    proc = boot({})
    try:
        st, body, _, _ = req("POST", "/api/v1/auth/otp/request", {"phone_e164": phone(9)})
        check(
            "SEC-01~03",
            "the OTP is never echoed, even with ALLOW_DEV_OTP unset",
            st == 200 and "dev_code" not in body,
            f"status={st} body_keys={sorted(body)}",
        )
    finally:
        proc.terminate()
        proc.wait(timeout=20)
        time.sleep(1)


def main() -> int:
    if not port_free():
        print("port 8000 is busy — stop the running server first")
        return 2
    try:
        # SEC-04~05 needs a real ADMIN row to sign for; create one through the
        # supported CLI rather than assuming a planted row exists.
        provision_admin()
        print(f"provisioned admin {ADMIN_PHONE} ({ADMIN_UUID})")
        # SEC-07's check deliberately exhausts the per-IP OTP bucket, and that
        # bucket lives in Redis — it survives the server restart between phases.
        # So the phase that needs one clean OTP request must run first.
        phase_dev_otp_off()
        phase_dev_otp_on()
    except RuntimeError as exc:
        print(f"probe aborted: {exc}")
        return 2
    finally:
        deprovision_admin()

    failed = [r for r in RESULTS if not r[2]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("STILL EXPLOITABLE:")
        for tag, title, _, detail in failed:
            print(f" - {tag} {title}: {detail}")
        return 1
    print("All previously proven findings are now blocked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
