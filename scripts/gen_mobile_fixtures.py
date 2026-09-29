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

    python scripts/gen_mobile_fixtures.py

It is a development tool: it opts into `ALLOW_DEV_OTP=true` to log in without
WhatsApp credentials, exactly as `scripts/live_smoke.py` does. The dev rail is
refused outright when APP_ENV=prod, and the code is never echoed in a response.
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
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
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

sys.path.insert(0, str(ROOT))


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

    Only the four throwaway fixture phones are touched.
    """
    import redis.asyncio as aioredis
    from sqlalchemy import delete, or_, select

    from app.core.config import get_settings
    from app.core.db import get_session_factory
    from app.models import (
        DriverProfile,
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
            profile_ids = select(DriverProfile.id).where(
                DriverProfile.user_id.in_(user_ids)
            )
            # RESTRICT -> must be removed before the rows they point at.
            await session.execute(
                delete(LedgerEntry).where(
                    LedgerEntry.driver_profile_id.in_(profile_ids)
                )
            )
            await session.execute(
                delete(Order).where(
                    or_(
                        Order.passenger_id.in_(user_ids),
                        Order.driver_id.in_(profile_ids),
                    )
                )
            )
            await session.execute(
                delete(RefreshToken).where(RefreshToken.user_id.in_(user_ids))
            )
            await session.execute(
                delete(OtpCode).where(OtpCode.phone_e164.in_(TEST_PHONES))
            )
            # CASCADE handles driver_profiles -> driver_deposits, refund_requests.
            await session.execute(
                delete(User).where(User.phone_e164.in_(TEST_PHONES))
            )
            await session.commit()

    asyncio.run(run())


def admin_cli(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/create_admin.py", *args, "--yes"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"create_admin.py {' '.join(args)} failed:\n{result.stderr}")


def login(phone: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """OTP request + verify. Returns (access_token, verify_body, refresh_body)."""
    status, body = req("POST", "/api/v1/auth/otp/request", {"phone_e164": phone})
    if status != 200:
        raise SystemExit(f"otp/request for {phone} -> {status}: {body}")
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


def main() -> int:  # noqa: C901 - a linear script, not a library
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
            except Exception:  # noqa: BLE001 - readiness probe
                time.sleep(0.5)
        else:
            raise SystemExit("server did not become ready")

        _capture(record)

        manifest = {
            "generated_by": "scripts/gen_mobile_fixtures.py",
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
        admin_cli("--phone", ADMIN_PHONE, "--revoke")
    return 0


def _capture(record: Any) -> None:  # noqa: C901 - linear capture sequence
    # ---- auth ----------------------------------------------------------
    passenger_token, verify_body, otp_request_body = login(PASSENGER_PHONE)
    record("auth_otp_request", "POST /api/v1/auth/otp/request", otp_request_body)
    record("auth_verify", "POST /api/v1/auth/otp/verify", verify_body)

    status, me = req("GET", "/api/v1/auth/me", token=passenger_token)
    assert status == 200, me
    record("auth_me", "GET /api/v1/auth/me", me)

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
    admin_token, admin_verify, _ = login(ADMIN_PHONE)
    record("auth_verify_admin", "POST /api/v1/auth/otp/verify (ADMIN role)", admin_verify)

    driver_token, driver_verify, _ = login(DRIVER_PHONE)
    record("auth_verify_new_user", "POST /api/v1/auth/otp/verify (created=true)", driver_verify)

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
        "/api/v1/driver/location",
        {"lat": 22.3200, "lng": 114.1700, "online": True},
        token=driver_token,
    )
    assert status == 200, located
    record("driver_location", "POST /api/v1/driver/location", located)

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

    status, settlement = req("POST", "/api/v1/admin/settlement/weekly/run", token=admin_token)
    assert status == 200, settlement
    record("admin_settlement", "POST /api/v1/admin/settlement/weekly/run", settlement)

    # ---- refunds (suspends the driver, so use a second account) ---------
    refund_token, _, _ = login(REFUND_PHONE)
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

    status, refund = req("POST", "/api/v1/drivers/me/refund/request", {"note": "leaving"}, token=refund_token)
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
    status, not_found = req("GET", "/api/v1/orders/00000000-0000-0000-0000-000000000000", token=passenger_token)
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
    status, business_rule = req(
        "POST", f"/api/v1/orders/{order_id}/start", token=driver_token
    )
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
    status, cooldown = req(
        "POST", "/api/v1/auth/otp/request", {"phone_e164": PASSENGER_PHONE}
    )
    assert status == 400, cooldown
    record("error_otp_cooldown", "POST /api/v1/auth/otp/request twice (400)", cooldown)


def _capture_websocket(record: Any, order_id: str, passenger_token: str, driver_token: str) -> None:
    """Capture one real location tick and the ping/heartbeat frame."""
    import websockets

    async def run() -> None:
        passenger_uri = f"{WS_BASE}/ws/trip/{order_id}?token={passenger_token}"
        driver_uri = f"{WS_BASE}/ws/trip/{order_id}?token={driver_token}"
        async with websockets.connect(passenger_uri) as passenger, websockets.connect(
            driver_uri
        ) as driver:
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
