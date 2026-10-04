"""Provision a restricted reviewer account — for an app store reviewer or an auditor.

Why this exists
---------------
Apple and Google both require a working account before they will review a
release, and an auditor needs to watch the product do its job. Neither should get
an account that can do anything *else*, and neither should be handed a real
user's credentials or a permanent key to the platform.

What this account can do, and what stops it
-------------------------------------------
It is a **PASSENGER** with a proven phone and a complete profile, so it can sign
in, browse and book a taxi. It cannot:

* **move money** — structurally, not by policy. Every movement of value here goes
  through `driver_profiles.deposit` and the append-only `ledger_entries`, and both
  hang off a driver profile, which this script never creates and which a
  passenger-only account has no route to.
* **open the admin console** — the console authenticates against
  `admin_accounts`, a separate table with a mandatory TOTP second factor and no
  registration path at all. A `users` row is not an admin whatever its `role`
  column says: `require_admin` reads the other table.
* **outlive its purpose** — `reviewer_expires_at` is enforced on every
  authenticated request by `require_active_user`, so the account stops working at
  that timestamp with nobody having to remember to revoke it. That is the entire
  reason the expiry is a column and not a runbook step: a forgotten revocation is
  the failure mode that matters, and a date the guard reads cannot be forgotten.

The reserved number range
-------------------------
`+8520000XXXX`. The `0000` prefix is not one OFCA allocates, so it cannot collide
with a real subscriber — and it does not have to be deliverable, because the
account is created already verified and never needs to receive a code. It is
still validated against the same `^\\+852\\d{8}$` pattern the API enforces, so
every row this creates is indistinguishable from a real one to everything
downstream.

Two switches, not one
---------------------
`ALLOW_REVIEWER_ACCOUNT` must be true, and `APP_ENV=prod` additionally requires
`--yes`. The same shape as `ALLOW_DEV_OTP`, for the same reason: a credential that
bypasses the normal sign-up path should never be one mistyped environment away
from existing.

Usage
-----
    .venv/Scripts/python.exe scripts/ops/create_reviewer_account.py --list
    .venv/Scripts/python.exe scripts/ops/create_reviewer_account.py \\
        --email reviewer@example.com --days 14
    .venv/Scripts/python.exe scripts/ops/create_reviewer_account.py \\
        --revoke reviewer@example.com

The password is read from `REVIEWER_PASSWORD` or prompted for; it is never taken
as an argument, because a password in `argv` lands in shell history and in the
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
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import dispose_engine, get_session_factory
from app.core.passwords import PasswordPolicyError, hash_password
from app.core.phone import is_hk_phone
from app.models import AccountStatus, User, UserRole
from app.services.auth.refresh_service import RefreshService

#: Reserved range: `+8520000` + four digits. See the module docstring.
REVIEWER_PHONE_PREFIX = "+8520000"
REVIEWER_PHONE_CAPACITY = 10_000

DEFAULT_DAYS = 30

#: Ten years. P-4's monthly phone re-verification must never be able to block a
#: reviewer: they cannot receive a code, so a deadline they can be asked to meet
#: is a deadline they will fail. The account's own expiry is what bounds its life,
#: and that is a separate, much nearer date.
_P4_NEVER_DUE_DAYS = 3650

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")


def _target() -> str:
    s = get_settings()
    return f"{s.postgres_host}:{s.postgres_port}/{s.postgres_db} (APP_ENV={s.app_env})"


def _phone_for(index: int) -> str:
    return f"{REVIEWER_PHONE_PREFIX}{index:04d}"


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _list_reviewers() -> int:
    factory = get_session_factory()
    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    select(User)
                    .where(User.reviewer_expires_at.is_not(None))
                    .order_by(User.reviewer_expires_at)
                )
            ).scalars()
        )

    print(f"target: {_target()}")
    if not rows:
        print("no reviewer accounts.")
        return 0

    now = datetime.now(UTC)
    print(f"{len(rows)} reviewer account(s):")
    for row in rows:
        expires = _as_aware(row.reviewer_expires_at)
        if expires is not None and expires <= now:
            state = "EXPIRED"
        elif not row.is_active:
            state = "DISABLED"
        else:
            state = "active"
        stamp = expires.isoformat() if expires else "-"
        print(f"  {row.email or '-':<34} {row.phone_e164:<14} {state:<9} expires={stamp}")
    return 0


async def _create(
    *,
    email: str,
    phone: str | None,
    username: str | None,
    password: str,
    days: int,
    yes: bool,
) -> int:
    settings = get_settings()

    if not settings.allow_reviewer_account:
        print("ALLOW_REVIEWER_ACCOUNT is not true — refusing to create a reviewer account.")
        print("Set ALLOW_REVIEWER_ACCOUNT=true in the environment and re-run.")
        return 2

    address = email.strip()
    if not _EMAIL_RE.fullmatch(address):
        print(f"email does not look like an address: {address!r}")
        return 2
    address = f"{address.partition('@')[0]}@{address.partition('@')[2].lower()}"

    if days < 1:
        print(f"--days must be at least 1, got {days}")
        return 2

    try:
        password_hash = hash_password(password)
    except PasswordPolicyError as exc:
        print(f"password rejected: {exc}")
        return 2

    if not yes:
        print(f"target: {_target()}")
        print(f"action: create reviewer account <{address}>, expires in {days} day(s)")
        if settings.app_env == "prod":
            print("APP_ENV=prod — re-run with --yes to confirm this is intended.")
            return 2

    factory = get_session_factory()
    async with factory() as session:
        clash = (await session.execute(select(User).where(User.email == address))).scalars().first()
        if clash is not None:
            print(f"email already in use by user {clash.id} — nothing written.")
            return 1

        # Every phone already in the table, not just the reviewer ones: a real
        # account could in principle hold an address in this range, and handing
        # out a number that is already taken would only fail later at the
        # partial unique index, with a far less useful message.
        taken = set((await session.execute(select(User.phone_e164))).scalars())

        if phone is None:
            index = next(
                (i for i in range(REVIEWER_PHONE_CAPACITY) if _phone_for(i) not in taken),
                None,
            )
            if index is None:
                print("the reserved reviewer range is full — that should not be possible.")
                return 1
            phone = _phone_for(index)
        else:
            if not is_hk_phone(phone) or not phone.startswith(REVIEWER_PHONE_PREFIX):
                print(
                    f"--phone must be inside the reserved range "
                    f"{REVIEWER_PHONE_PREFIX}0000-{REVIEWER_PHONE_PREFIX}9999, got {phone!r}"
                )
                return 2
            if phone in taken:
                print(f"phone already in use: {phone} — nothing written.")
                return 1

        handle = (username or f"reviewer{phone[-4:]}").strip().lower()
        if not _USERNAME_RE.fullmatch(handle):
            print(f"username is not usable: {handle!r}")
            return 2
        if (
            await session.execute(select(User.id).where(User.username == handle))
        ).scalar_one_or_none() is not None:
            print(f"username already taken: {handle} — nothing written.")
            return 1

        now = datetime.now(UTC)
        account = User(
            id=uuid.uuid4(),
            phone_e164=phone,
            display_name="Reviewer",
            role=UserRole.PASSENGER,
            is_active=True,
            username=handle,
            given_name="Store",
            family_name="Reviewer",
            email=address,
            password_hash=password_hash,
            # Created complete and verified. A reviewer cannot receive an OTP or
            # click a link in an inbox we do not own, so every gate they are meant
            # to get past has to be satisfied at creation — otherwise they meet
            # the verification wall and cannot test the product at all.
            account_status=AccountStatus.ACTIVE,
            phone_verified_at=now,
            email_verified_at=now,
            phone_reverify_due_at=now + timedelta(days=_P4_NEVER_DUE_DAYS),
            failed_login_count=0,
            reviewer_expires_at=now + timedelta(days=days),
        )
        session.add(account)
        await session.commit()

    print(f"created reviewer account <{address}>")
    print(f"  id      = {account.id}")
    print(f"  username= {account.username}")
    print(f"  phone   = {account.phone_e164}  (reserved range; not a real subscriber)")
    print(f"  expires = {account.reviewer_expires_at.isoformat()}")
    print(f"  target  = {_target()}")
    print()
    print("Sign in with POST /api/v1/auth/login {email, password} — the password is")
    print("the one you just entered, and it is not recoverable from this script.")
    print("The account is a PASSENGER: it can book a taxi and cannot touch money or")
    print("the admin console. It stops working at the expiry above, automatically.")
    return 0


async def _revoke(identifier: str) -> int:
    """Disable a reviewer account early and kill its refresh tokens."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            (
                await session.execute(
                    select(User).where(
                        (User.email == identifier.strip())
                        | (User.phone_e164 == identifier.strip()),
                        User.reviewer_expires_at.is_not(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            print(f"no reviewer account matches {identifier!r} — nothing changed.")
            return 1

        now = datetime.now(UTC)
        row.is_active = False
        # Both, not just `is_active`: setting the expiry to now makes the account
        # read as expired even to a code path that only checks the date, so the
        # revocation does not depend on this one column being read.
        row.reviewer_expires_at = now
        revoked = await RefreshService(session).revoke_all_for_user(row.id)
        await session.commit()

    print(f"revoked reviewer account <{row.email}> ({row.phone_e164})")
    print(f"  refresh tokens revoked: {revoked}")
    print("  access tokens: already issued ones stay valid for up to 15 minutes.")
    print("  There is no revocation epoch without Redis, so that window is the")
    print("  worst case — it is bounded by ACCESS_TOKEN_EXPIRE_MINUTES.")
    return 0


async def _run(args: argparse.Namespace) -> int:
    try:
        if args.list:
            return await _list_reviewers()
        if args.revoke:
            return await _revoke(args.revoke)

        # Checked *before* the password prompt, not only inside `_create`. The
        # other order asks for a secret that is then thrown away, and a password
        # typed into `getpass` is a password that existed in the process for no
        # reason. `_create` keeps its own check as the authority; this one is
        # about not collecting something we will not use.
        if not get_settings().allow_reviewer_account:
            print("ALLOW_REVIEWER_ACCOUNT is not true — refusing to create a reviewer account.")
            print("Set ALLOW_REVIEWER_ACCOUNT=true in the environment and re-run.")
            return 2

        password = os.environ.get("REVIEWER_PASSWORD") or ""
        if not password:
            if not sys.stdin.isatty():
                print("no TTY to prompt for a password — set REVIEWER_PASSWORD instead.")
                return 2
            password = getpass.getpass("password: ")
            confirm = getpass.getpass("confirm : ")
            if password != confirm:
                print("passwords do not match.")
                return 2

        return await _create(
            email=args.email,
            phone=args.phone,
            username=args.username,
            password=password,
            days=args.days,
            yes=bool(args.yes),
        )
    finally:
        # Disposed on the same loop that opened the connections. Two
        # `asyncio.run` calls would dispose on a different loop, which surfaces as
        # `AttributeError: 'NoneType' object has no attribute 'send'`.
        await dispose_engine()


def main() -> int:
    p = argparse.ArgumentParser(description="Create or revoke a restricted reviewer account.")
    p.add_argument("--email", help="the reviewer's sign-in address")
    p.add_argument(
        "--phone",
        default=None,
        help=f"optional; must be in {REVIEWER_PHONE_PREFIX}0000-9999 (default: first free)",
    )
    p.add_argument("--username", default=None, help="optional; default is derived from the phone")
    p.add_argument(
        "--days", type=int, default=DEFAULT_DAYS, help=f"lifetime (default {DEFAULT_DAYS})"
    )
    p.add_argument("--list", action="store_true", help="list reviewer accounts and exit")
    p.add_argument("--revoke", metavar="EMAIL_OR_PHONE", help="disable one early and exit")
    p.add_argument("--yes", action="store_true", help="skip the target confirmation echo")
    args = p.parse_args()

    if not args.list and not args.revoke and not args.email:
        p.error("--email is required unless --list or --revoke is given")

    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
