"""Create or promote an ADMIN (P2-11 — this was a release blocker).

Why this exists
---------------
`require_admin` re-reads the `users` row, so an ADMIN claim inside a JWT is
worthless without a matching row in the database. Nothing in the codebase could
create one — no CLI, no data migration, no admin endpoint (an admin endpoint
would itself need an admin). The only ADMIN that has ever existed in a real
database was written by hand with SQL, which is exactly why this script exists.

Without it a fresh deployment cannot approve a single KYC application or refund,
i.e. the platform cannot go live.

Usage
-----
    .venv/Scripts/python.exe scripts/create_admin.py --list
    .venv/Scripts/python.exe scripts/create_admin.py --phone +85291234567
    .venv/Scripts/python.exe scripts/create_admin.py --phone +85291234567 --yes

Notes
-----
- The UUID is GENERATED, never a fixed well-known value. A hard-coded admin id
  is a skeleton key: anyone who can forge or steal a token knows which `sub` to
  use, and it would collide across every environment.
- `APP_ENV=prod` requires `--yes`, and the target database is always printed
  first, so pointing this at production is a deliberate act.
- Promoting a user does NOT retro-fit their existing tokens: `require_admin`
  compares the JWT's `role` claim against the live row and rejects a mismatch.
  The new admin must log in again.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import dispose_engine, get_session_factory  # noqa: E402
from app.models import User, UserRole  # noqa: E402

_PHONE_RE = re.compile(r"^\+852\d{8}$")


def _target() -> str:
    s = get_settings()
    return f"{s.postgres_host}:{s.postgres_port}/{s.postgres_db} (APP_ENV={s.app_env})"


async def _list_admins() -> int:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            (await session.execute(select(User).where(User.role == UserRole.ADMIN))).scalars().all()
        )
    print(f"target: {_target()}")
    if not rows:
        print("no ADMIN rows — nobody can approve KYC or refunds.")
        return 1
    print(f"{len(rows)} ADMIN row(s):")
    for u in rows:
        state = "active" if u.is_active else "DISABLED"
        print(f"  {u.id}  {u.phone_e164}  {state}")
    return 0


async def _set_role(phone: str, role: UserRole, revoke: bool) -> int:
    factory = get_session_factory()
    async with factory() as session:
        user = (
            (await session.execute(select(User).where(User.phone_e164 == phone))).scalars().first()
        )

        if revoke:
            if user is None:
                print(f"no user with phone {phone}")
                return 1
            if user.role != UserRole.ADMIN:
                print(f"{phone} is {user.role.value}, not ADMIN — nothing to revoke")
                return 1
            # Demote to PASSENGER rather than delete: the user may own ledger
            # rows, and the ledger FK is RESTRICT precisely so an account with
            # financial history cannot vanish.
            user.role = UserRole.PASSENGER
            await session.commit()
            print(f"revoked ADMIN from {phone} (now PASSENGER). id={user.id}")
            return 0

        if user is None:
            # A brand-new admin: random UUID, verified phone. Deliberately not a
            # fixed id — see the module docstring.
            user = User(
                id=uuid.uuid4(),
                phone_e164=phone,
                role=UserRole.ADMIN,
                is_active=True,
                phone_verified_at=datetime.now(UTC),
            )
            session.add(user)
            await session.commit()
            print(f"created ADMIN {phone}")
        else:
            if user.role == UserRole.ADMIN:
                print(f"{phone} is already ADMIN — nothing to do. id={user.id}")
                return 0
            previous = user.role.value
            user.role = UserRole.ADMIN
            user.is_active = True
            await session.commit()
            print(f"promoted {phone}: {previous} -> ADMIN")

        print(f"  id        = {user.id}")
        print(f"  target    = {_target()}")
        print("  next step: log in via OTP with this number — existing tokens keep")
        print("             their old role claim and will be rejected by require_admin.")
        return 0


async def _main(args: argparse.Namespace) -> int:
    if args.list:
        return await _list_admins()

    if not _PHONE_RE.fullmatch(args.phone or ""):
        print(f"--phone must be an HK number in E.164 form (+852XXXXXXXX), got {args.phone!r}")
        return 2

    settings = get_settings()
    if not args.yes:
        print(f"target: {_target()}")
        print(f"action: {'REVOKE admin from' if args.revoke else 'grant ADMIN to'} {args.phone}")
        # In prod the target line alone is easy to skim past, so require the flag.
        if settings.app_env == "prod":
            print("APP_ENV=prod — re-run with --yes to confirm this is intended.")
            return 2
    return await _set_role(args.phone, UserRole.ADMIN, args.revoke)


async def _run(args: argparse.Namespace) -> int:
    # Everything, including engine disposal, must happen in ONE event loop.
    # Two `asyncio.run` calls put the dispose on a different loop than the
    # connections it is closing, which surfaces as
    # `AttributeError: 'NoneType' object has no attribute 'send'` on exit.
    try:
        return await _main(args)
    finally:
        await dispose_engine()


def main() -> int:
    p = argparse.ArgumentParser(description="Create, promote or revoke an ADMIN user (P2-11).")
    p.add_argument("--phone", help="HK mobile in E.164 form, e.g. +85291234567")
    p.add_argument("--list", action="store_true", help="list current ADMIN rows and exit")
    p.add_argument("--revoke", action="store_true", help="demote the phone to PASSENGER")
    p.add_argument("--yes", action="store_true", help="skip the confirmation echo")
    args = p.parse_args()

    if not args.list and not args.phone:
        p.error("--phone is required unless --list is given")

    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
