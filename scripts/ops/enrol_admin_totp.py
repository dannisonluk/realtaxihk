#!/usr/bin/env python
"""Enrol an existing admin account's TOTP, and print the secret.

Why this exists
---------------
`scripts/ops/create_admin_account.py` deliberately leaves the account **unenrolled**:
a TOTP secret written by a script is a secret nobody has proven they can
generate a code from, which is the classic lockout. Enrolment is therefore only
reachable by walking the real flow.

That is correct for a human with an authenticator app, but it makes the account
unusable from a script — and `admin-web/tool/verify_ui.mjs` needs a working
(username, password, totp_secret) triple. Without this script the only way to
get one is to open a browser, scan a QR code by hand, and read the secret back
out of the database. This does the same walk headlessly.

It does NOT create the account. Provision it first:

    ADMIN_PASSWORD='...' .venv/Scripts/python.exe scripts/ops/create_admin_account.py \\
        --username verify-ui --email verify-ui@realtaxihk.local --yes

Then:

    ADMIN_PASSWORD='...' .venv/Scripts/python.exe scripts/ops/enrol_admin_totp.py \\
        --username verify-ui --super-admin

`--super-admin` promotes the account afterwards, because a freshly created one
is `SUPPORT` (the lowest rank) and the console verifier drives every route —
including the finance and account screens, which a `SUPPORT` admin cannot read.

The password is read from `ADMIN_PASSWORD`, never from argv, for the same reason
`create_admin_account.py` does that: argv lands in shell history and in the
process table.

The enrolment itself goes through the real HTTP surface (`POST /admin/auth/login`
→ `POST /admin/auth/totp/enrol/confirm`) via an in-process ASGI transport, so no
server has to be running. Nothing here bypasses the server's own checks.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.core.totp import totp_at
from app.main import create_app


async def enrol(username: str, password: str, super_admin: bool) -> int:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://enrol") as c:
        r = await c.post(
            "/api/v1/admin/auth/login",
            json={"username": username, "password": password},
        )
        if r.status_code != 200:
            print(f"login failed: HTTP {r.status_code}")
            print(" ", r.json())
            print("  (a wrong password and an inactive account both land here)")
            return 1

        body = r.json()
        kind = body.get("next")

        if kind == "totp_required":
            print(f"{username} is already enrolled.")
            print("Read the existing secret from the database instead:")
            # Not an f-string: ruff's S608 flags interpolated SQL even when it
            # is only ever printed, and this string is never executed.
            print("  SELECT totp_secret FROM admin_accounts WHERE username = '<name>';")
            return 2

        if kind != "enrolment_required":
            print(f"unexpected login state: {kind!r}")
            return 1

        enrolment = body.get("enrolment") or {}
        secret = enrolment.get("secret")
        if not secret:
            print("no enrolment secret in the response:", list(body))
            return 1

        print("otpauth_uri :", enrolment.get("otpauth_uri"))
        print("recovery    :", len(enrolment.get("recovery_codes") or []), "codes issued")

        code = totp_at(secret)
        r2 = await c.post(
            "/api/v1/admin/auth/totp/enrol/confirm",
            json={"challenge_token": body.get("challenge_token"), "code": code},
        )
        if r2.status_code != 200:
            print(f"enrol/confirm failed: HTTP {r2.status_code}")
            print(" ", r2.json())
            return 1

        admin = r2.json().get("admin") or {}
        print(f"enrolled    : {admin.get('username')} role={admin.get('admin_role')}")

    if super_admin:
        await _promote(username)

    print()
    print(f"ADMIN_USERNAME={username}")
    print(f"ADMIN_PASSWORD={password}")
    print(f"ADMIN_TOTP_SECRET={secret}")
    return 0


async def _promote(username: str) -> None:
    """Set the account's RBAC rank to SUPER_ADMIN.

    Done over the ORM rather than raw SQL so the update goes through the same
    column mapping the application uses.
    """
    from sqlalchemy import select

    from app.core.db import dispose_engine, get_session_factory
    from app.models import AdminAccount

    factory = get_session_factory()
    async with factory() as session:
        account = (
            await session.execute(select(AdminAccount).where(AdminAccount.username == username))
        ).scalar_one_or_none()
        if account is None:
            print(f"  ! {username} vanished before promotion")
            return
        before = account.role
        account.role = "SUPER_ADMIN"
        await session.commit()
        print(f"role        : {before} -> SUPER_ADMIN")
    await dispose_engine()


def main() -> int:
    ap = argparse.ArgumentParser(description="Enrol an admin account's TOTP.")
    ap.add_argument("--username", required=True)
    ap.add_argument(
        "--super-admin",
        action="store_true",
        help="promote to SUPER_ADMIN afterwards (the console verifier needs it)",
    )
    args = ap.parse_args()

    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("admin password: ")
    if not password:
        print("no password given", file=sys.stderr)
        return 1

    return asyncio.run(enrol(args.username, password, args.super_admin))


if __name__ == "__main__":
    raise SystemExit(main())
