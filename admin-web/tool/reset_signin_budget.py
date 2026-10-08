"""Clear the OTP budget and pending OTP rows, so the UI verifier can sign in again.

Why this exists
---------------
`admin-web/tool/verify_ui.mjs` drives the React build through username +
password + TOTP, not the legacy phone-OTP flow. The OTP budget below only
matters when manually driving the legacy console; a React verifier run needs
nothing cleared.

  - `otp_ip_rate_limit`     10 requests / 600s  per client address
  - `otp_phone_rate_limit`   5 requests / 3600s per phone number
  - `_VERIFY_IP_RATE_LIMIT`  a handful of verify attempts per address
  - `otp_service._RESEND_COOLDOWN_S`  60s between codes for one number

Those numbers are correct for production and must not be relaxed. But a developer
iterating on the console runs the verifier several times in a few minutes from one
address and one number, blows the per-phone cap, and then gets a bare
`429 RATE_LIMITED` — which `login.js` correctly renders as an error while staying
on the phone step, so the verifier's `waitForSelector('#login-code')` times out
and looks like a broken console. It is not. It is a spent budget.

This script spends nothing and relaxes nothing: it just deletes the counters and
the stale codes in a development database, exactly as `gen_mobile_fixtures.py`
resets fixture state. Run it before a verifier run; do not run it in production.

Usage
-----
    .venv/Scripts/python.exe admin-web/tool/reset_signin_budget.py
    .venv/Scripts/python.exe admin-web/tool/reset_signin_budget.py --phone +85290000001

Notes
-----
- Refuses to run when `APP_ENV=prod` unless `--yes` is passed, and always prints
  the target database first, so this cannot be pointed at production by accident.
- Deleting the `otp_codes` rows matters as much as the Redis counters: `verify_otp`
  reads the *newest* row for the number, so a leftover consumed row would make the
  next correct code fail with "OTP already used".
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import dispose_engine, get_redis, get_session_factory  # noqa: E402
from app.models import OtpCode  # noqa: E402

_PHONE_RE = re.compile(r"^\+852\d{8}$")

# Scoped to OTP keys and namespace-agnostic: the limiter is built with a namespace
# in `app/main.py`, and hard-coding that string here would silently stop matching
# the day it changes.
_OTP_KEY_PATTERN = "rl:*otp*"


def _target() -> str:
    s = get_settings()
    return f"{s.postgres_host}:{s.postgres_port}/{s.postgres_db} (APP_ENV={s.app_env})"


async def _reset(phone: str | None, confirmed: bool) -> int:
    settings = get_settings()
    print(f"target: {_target()}")

    if settings.app_env == "prod" and not confirmed:
        print("APP_ENV=prod — refusing. Pass --yes if this really is intended.")
        return 2

    redis = get_redis()
    try:
        keys = [key async for key in redis.scan_iter(match=_OTP_KEY_PATTERN)]
        if keys:
            await redis.delete(*keys)
        print(f"redis : cleared {len(keys)} OTP counter(s)")
    finally:
        await redis.aclose()

    factory = get_session_factory()
    async with factory() as session:
        statement = delete(OtpCode)
        if phone:
            statement = statement.where(OtpCode.phone_e164 == phone)
        result = await session.execute(statement)
        await session.commit()
    print(f"db    : deleted {result.rowcount} OTP row(s)" + (f" for {phone}" if phone else ""))

    print("done — the next OTP request starts from a full budget and no cooldown.")
    return 0


async def _run(args: argparse.Namespace) -> int:
    try:
        return await _reset(args.phone, args.yes)
    finally:
        # One event loop for the whole run: disposing the engine on a different
        # loop than the one that opened the connections raises on exit.
        await dispose_engine()


def main() -> int:
    p = argparse.ArgumentParser(
        description="Clear OTP rate-limit counters and pending OTP rows (dev only).",
    )
    p.add_argument(
        "--phone",
        default="+85290000001",
        help="limit the OTP row deletion to one number (default: the verifier's admin)",
    )
    p.add_argument("--all-phones", action="store_true", help="delete OTP rows for every number")
    p.add_argument("--yes", action="store_true", help="required to run when APP_ENV=prod")
    args = p.parse_args()

    if args.all_phones:
        args.phone = None
    elif args.phone and not _PHONE_RE.fullmatch(args.phone):
        print(f"--phone must be an HK number in E.164 form (+852XXXXXXXX), got {args.phone!r}")
        return 2

    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
