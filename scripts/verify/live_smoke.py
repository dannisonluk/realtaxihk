"""End-to-end live smoke: boot uvicorn, exercise Modules A/B/C/D, self-terminate.

The API never returns the OTP (SEC-02), so this harness opts into the dev rail
explicitly: `ALLOW_DEV_OTP=true` makes the code the fixed constant below. That
is this script's own choice, not a production concession — `allow_dev_otp` is
rejected outright when APP_ENV=prod, and the code is never echoed back.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8000"
TOKEN = None
DEV_OTP_CODE = "123456"  # requires ALLOW_DEV_OTP=true in the server environment


def clear_rate_limits() -> None:
    """Drop this app's rate-limit windows before booting.

    The OTP per-IP bucket has a 600s TTL and lives in Redis, so it survives a
    server restart. Run this straight after `scripts/verify/security_verify.py` — which
    deliberately exhausts that bucket to prove SEC-07 — and the first OTP request
    here fails with an opaque 429. Only this app's own `rl:realtaxi:*` keys are
    touched.
    """
    import asyncio

    import redis.asyncio as aioredis

    from app.core.config import get_settings

    async def run() -> None:
        cli = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        try:
            keys = [k async for k in cli.scan_iter("rl:realtaxi:*")]
            if keys:
                await cli.delete(*keys)
        finally:
            await cli.aclose()

    asyncio.run(run())


def req(method, path, body=None, auth=False, token=None):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    elif auth and TOKEN:
        r.add_header("Authorization", f"Bearer {TOKEN}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(r, data=data, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


# 0. port check
s = socket.socket()
port_free = s.connect_ex(("127.0.0.1", 8000)) != 0
s.close()
print(f"port 8000 free: {port_free}")
assert port_free, "port 8000 occupied — stop other servers first"

# 1. boot uvicorn
clear_rate_limits()
# SEC-31: --no-proxy-headers so X-Forwarded-For cannot rewrite the client address
# (uvicorn trusts it from 127.0.0.1 by default, which made IP limits spoofable).
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8000", "--no-proxy-headers"],
    env={**os.environ, "ALLOW_DEV_OTP": "true"},
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
print("uvicorn booting...")

try:
    # wait for readiness
    for _ in range(40):
        s = socket.socket()
        up = s.connect_ex(("127.0.0.1", 8000)) == 0
        s.close()
        if up:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("uvicorn never came up")
    print("server ready")

    # 2. fare API (Cap. 374D compliance surface)
    st, body = req(
        "POST",
        "/api/v1/fare/estimate",
        {
            "taxi_type": "URBAN",
            "distance_km": "10",
            "waiting_min": "5",
            "tunnels": ["cross_harbour"],
            "crosses_harbour": True,
            "discount_percent": "15",
        },
    )
    err = body if st != 200 else ""
    print(
        f"fare estimate: {st} total={body.get('total_fare')} "
        f"v={body.get('tariff_version')} err={err}"
    )
    assert st == 200 and body["total_fare"] == "149.0"

    # 3. passenger OTP login (rotating number: live DB has 60s resend cooldown)
    phone = f"+852{61000000 + int(time.time()) % 1000000}"
    st, body = req("POST", "/api/v1/auth/otp/request", {"phone_e164": phone})
    print(f"otp request: {st} dev_code_echoed={'dev_code' in body}")
    # SEC-02: the code must never come back over HTTP, even with the dev rail on.
    assert st == 200 and "dev_code" not in body, body
    st, body = req("POST", "/api/v1/auth/otp/verify", {"phone_e164": phone, "code": DEV_OTP_CODE})
    print(f"otp verify: {st}")
    assert st == 200
    TOKEN = body["access_token"]
    st, body = req("GET", "/api/v1/auth/me", auth=True)
    print(f"me: {st} phone={body.get('phone_masked')}")
    assert st == 200 and body["phone_masked"].startswith("+852")

    # 4. create order (rate-limit budget 1 used)
    st, order = req(
        "POST",
        "/api/v1/orders",
        {
            "pickup_lat": 22.30,
            "pickup_lng": 114.17,
            "dropoff_lat": 22.32,
            "dropoff_lng": 114.20,
            "pickup_address": "Central Ferry Piers",
            "dropoff_address": "Causeway Bay",
            "distance_km": "3.5",
            "taxi_type": "URBAN",
        },
        auth=True,
    )
    print(f"order create: {st} status={order.get('status')}")
    assert st == 201 and order["status"] == "BROADCASTING"
    oid = order["id"]

    # 5. driver flow: register -> KYC approve -> deposit -> ACTIVE -> nearby
    drv_phone = f"+852{92000000 + int(time.time()) % 100000}"
    st, body = req("POST", "/api/v1/auth/otp/request", {"phone_e164": drv_phone})
    assert st == 200 and "dev_code" not in body, body
    st, body = req(
        "POST", "/api/v1/auth/otp/verify", {"phone_e164": drv_phone, "code": DEV_OTP_CODE}
    )
    print(f"driver otp verify: {st}")
    assert st == 200
    driver_token = body["access_token"]

    def dreq(method, path, body=None):
        return req(method, path, body, auth=True, token=driver_token)

    st, body = dreq(
        "POST",
        "/api/v1/drivers/register",
        {
            "hk_id_last4": "1234",
            "taxi_driver_plate_no": "123456",
            "vehicle_reg_mark": "SM1234",
            "taxi_type": "URBAN",
        },
    )
    print(f"driver register: {st}")
    assert st in (200, 201), body
    drv_id = body["id"]

    st, body = req(
        "GET",
        "/api/v1/orders/nearby?lat=22.30&lng=114.17&radius_km=3",
        auth=True,
        token=driver_token,
    )
    print(f"nearby (PENDING_KYC driver): {st} count={len(body.get('items', []))}")
    assert st == 200 and len(body["items"]) >= 1  # live DB accrues orders across runs

    # 6. WS snapshot (non-participant 403 check via passenger ok path)
    st, body = req("GET", f"/api/v1/trips/{oid}/location", auth=True)
    print(f"trip snapshot: {st} (404 expected — no ticks yet)")
    assert st in (200, 404)

    # 7. rate limit: same passenger counts up -> verify 429 after the budget (5/60s)
    def _order_payload():
        return {
            "pickup_lat": 22.30,
            "pickup_lng": 114.17,
            "dropoff_lat": 22.32,
            "dropoff_lng": 114.20,
            "pickup_address": "Central Ferry Piers",
            "dropoff_address": "Causeway Bay",
            "distance_km": "3.5",
            "taxi_type": "URBAN",
        }

    for _ in range(4):
        req("POST", "/api/v1/orders", _order_payload(), auth=True)
    st, body = req("POST", "/api/v1/orders", _order_payload(), auth=True)
    print(f"6th order (rate-limited): {st} code={body.get('code')}")
    assert st == 429, f"expected 429, got {st}"

    print("ALL LIVE CHECKS PASSED")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("server stopped")
