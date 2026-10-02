"""Provision an admin account for the console (`admin_accounts`).

Why this exists
---------------
The admin console authenticates against `admin_accounts`, which has no
registration endpoint and no OTP signup — deliberately. An admin account can
approve refunds, adjust the deposit ledger and run fleet settlement, so an
account-creation path reachable from the internet is the last thing it should
have. That leaves a CLI as the only way to bring the first one into existence.

`scripts/ops/create_admin.py` is a *different* script for a different thing: it
grants `users.role = ADMIN`, which is the legacy console identity. This one
creates the credential-bearing account with the TOTP second factor.

TOTP is never enrolled here
---------------------------
This does not generate a TOTP secret. The admin enrols on their **first login**,
which is the only way the secret can be proven to work before it is trusted — the
flow is in `app/services/admin_auth_service.py` (`_begin_enrolment` holds the
secret in Redis until a valid code confirms it). Writing a secret from a script
would mean an operator could create an account whose second factor nobody can
generate a code for, and the account would then be locked out by design.

Usage
-----
    .venv/Scripts/python.exe scripts/ops/create_admin_account.py --list
    .venv/Scripts/python.exe scripts/ops/create_admin_account.py \\
        --username dannison --email dannison@example.com --name "Dannison"

The password is read from `ADMIN_PASSWORD` or prompted for; it is never taken as
an argument, because a password in `argv` lands in shell history and in the
process table.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import dispose_engine, get_session_factory
from app.core.passwords import (
    PasswordPolicyError,
    hash_password,
)
from app.models import AdminAccount

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _target() -> str:
    s = get_settings()
    return f"{s.postgres_host}:{s.postgres_port}/{s.postgres_db} (APP_ENV={s.app_env})"


def _norm_email(value: str) -> str:
    return value.strip().lower()


async def _list_admins() -> int:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(AdminAccount).order_by(AdminAccount.username))
        rows = list(result.scalars())
    print(f"target: {_target()}")
    if not rows:
        print("no admin_accounts — the console has no one who can sign in.")
        return 1
    print(f"{len(rows)} admin account(s):")
    for row in rows:
        state = "active" if row.is_active else "DISABLED"
        enrolled = "TOTP enrolled" if row.totp_enrolled_at else "NOT ENROLLED"
        last = row.last_login_at.isoformat() if row.last_login_at else "never"
        print(f"  {row.username:<20} {row.email:<32} {state:<9} {enrolled:<14} last={last}")
    return 0


async def _create(
    username: str, email: str, full_name: str | None, password: str, yes: bool
) -> int:
    settings = get_settings()
    username = username.strip().lower()
    email = _norm_email(email)

    if not _USERNAME_RE.fullmatch(username):
        print(
            "username must be 3-32 chars, lowercase letters/digits/._- "
            f"and start alphanumeric; got {username!r}"
        )
        return 2
    if not _EMAIL_RE.fullmatch(email):
        print(f"email does not look like an address: {email!r}")
        return 2

    try:
        password_hash = hash_password(password)
    except PasswordPolicyError as exc:
        print(f"password rejected: {exc}")
        return 2

    if not yes:
        print(f"target: {_target()}")
        print(f"action: create admin account {username} <{email}>")
        if settings.app_env == "prod":
            print("APP_ENV=prod — re-run with --yes to confirm this is intended.")
            return 2

    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(
                select(AdminAccount).where(
                    (AdminAccount.username == username) | (AdminAccount.email == email)
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            print(
                f"already taken: {existing.username} <{existing.email}> "
                f"(id={existing.id}) — nothing written."
            )
            return 1

        # Random UUID, never a well-known value: a fixed admin id is a skeleton
        # key for anyone who can forge a token.
        account = AdminAccount(
            id=uuid.uuid4(),
            username=username,
            email=email,
            full_name=full_name,
            password_hash=password_hash,
            is_active=True,
            failed_login_count=0,
        )
        session.add(account)
        await session.commit()

    print(f"created admin account {username} <{email}>")
    print(f"  id     = {account.id}")
    print(f"  target = {_target()}")
    print("  TOTP   = not enrolled. Sign in at the console; the first login walks")
    print("           through binding an authenticator app and issues recovery codes.")
    return 0


async def _run(args: argparse.Namespace) -> int:
    if args.list:
        return await _list_admins()

    password = os.environ.get("ADMIN_PASSWORD") or ""
    if not password:
        if not sys.stdin.isatty():
            print("no TTY to prompt for a password — set ADMIN_PASSWORD instead.")
            return 2
        password = getpass.getpass("password: ")
        confirm = getpass.getpass("confirm : ")
        if password != confirm:
            print("passwords do not match.")
            return 2

    # One event loop for everything, disposal included: two `asyncio.run` calls
    # dispose on a different loop than the one holding the connections, which
    # surfaces as `AttributeError: 'NoneType' object has no attribute 'send'`.
    try:
        return await _create(args.username, args.email, args.name, password, yes=bool(args.yes))
    finally:
        await dispose_engine()


def main() -> int:
    p = argparse.ArgumentParser(description="Create an admin account for the console.")
    p.add_argument("--username", help="3-32 chars, lowercase; stored lowercased")
    p.add_argument("--email", help="unique; the console shows it, login does not use it")
    p.add_argument("--name", default=None, help="optional display name")
    p.add_argument("--list", action="store_true", help="list admin accounts and exit")
    p.add_argument("--yes", action="store_true", help="skip the target confirmation echo")
    args = p.parse_args()

    if not args.list and not (args.username and args.email):
        p.error("--username and --email are required unless --list is given")

    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
