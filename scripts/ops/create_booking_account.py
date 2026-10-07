"""Seed a disposable passenger + driver account pair for QA booking tests.

Runs through the auth **service layer** (`AccountAuthService` +
`PhoneBindingService`) exactly like production signup / phone verification —
no SQL UPDATE, no direct writes. In dev (`ALLOW_DEV_OTP=true`) the phone OTP
flow auto-registers and verifies the number, so this script is one click:

    ALLOW_DEV_OTP=true .venv/Scripts/python.exe scripts/ops/create_booking_account.py

Accounts are idempotent-ish: an already-registered, already-verified phone is
detected and skipped. Logs mask phones (first raw group + last four, e.g.
`+852****0001`); no passwords or fabricated codes are ever printed.

Environment:
    ALLOW_DEV_OTP       must be true (dev OTP auto-register/verify)
    APP_ENV             must not be a production environment
    BOOKING_TEST_PASSWORD  required; minimum 12 characters. It is never printed.
    PASSENGER_PHONE / DRIVER_PHONE  optional env overrides
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import close_redis, dispose_engine, get_redis, get_session_factory
from app.core.exceptions import BusinessRuleError
from app.core.phone import is_hk_phone
from app.models.user import User
from app.services.auth.account_service import AccountAuthService
from app.services.auth.phone_binding_service import PhoneBindingService

DEFAULT_PASSENGER_PHONE = "+85291230001"
DEFAULT_DRIVER_PHONE = "+85291230002"
DEFAULT_PASSENGER_EMAIL = "booking.passenger@example.com"
DEFAULT_DRIVER_EMAIL = "booking.driver@example.com"
# The dev OTP code documented in docs/QA_TEST_ENVIRONMENT.md for ALLOW_DEV_OTP.
DEV_OTP_CODE = "123456"


def _mask_phone(phone: str) -> str:
    if len(phone) <= 4:
        return phone
    return f"{phone[:4]}****{phone[-4:]}"


def _arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--passenger-phone", default=os.environ.get("PASSENGER_PHONE", DEFAULT_PASSENGER_PHONE)
    )
    parser.add_argument(
        "--driver-phone", default=os.environ.get("DRIVER_PHONE", DEFAULT_DRIVER_PHONE)
    )
    parser.add_argument(
        "--passenger-email", default=os.environ.get("PASSENGER_EMAIL", DEFAULT_PASSENGER_EMAIL)
    )
    parser.add_argument(
        "--driver-email", default=os.environ.get("DRIVER_EMAIL", DEFAULT_DRIVER_EMAIL)
    )
    return parser


async def _ensure_account(
    session: AsyncSession,
    *,
    settings,
    label: str,
    phone: str,
    email: str,
    password: str,
) -> None:
    masked = _mask_phone(phone)
    auth = AccountAuthService(session)
    existing = await auth.find_by_email(email)
    if existing is not None and existing.phone_verified_at:
        print(f"[{label}] {masked}: already registered and verified — skipping")
        return

    if existing is None:
        await auth.register(phone=phone, password=password)
        await session.commit()
        print(f"[{label}] {masked}: account registered")

    user: User = (await auth.find_by_email(email)) or (
        await auth.find_by_email(f"dummy-{label}@example.com")
    )
    if user is None:
        raise RuntimeError(f"[{label}] account lookup failed after registration")

    if not user.phone_verified_at:
        binder = PhoneBindingService(session)
        try:
            await binder.request(user=user, phone=phone)
        except BusinessRuleError as exc:
            if "cooldown" not in str(exc).lower():
                raise
            print(f"[{label}] {masked}: OTP resend cooldown active, reusing existing code")
        await binder.confirm(user=user, phone=phone, code=DEV_OTP_CODE)
        await session.commit()
        print(f"[{label}] {masked}: phone verified (dev OTP)")
    else:
        print(f"[{label}] {masked}: already verified — skipping")


async def main() -> int:
    settings = get_settings()
    if not settings.dev_otp_enabled:
        print(
            "ALLOW_DEV_OTP must be true — dev OTP auto-register/verify is required for this script",
            file=sys.stderr,
        )
        return 2
    if settings.environment and settings.environment.lower() in {"prod", "production"}:
        print("refusing to run against a production environment", file=sys.stderr)
        return 2

    args = _arg_parser().parse_args()
    passenger_phone = args.passenger_phone.strip()
    driver_phone = args.driver_phone.strip()
    if not is_hk_phone(passenger_phone) or not is_hk_phone(driver_phone):
        print("both phones must be valid +852 numbers", file=sys.stderr)
        return 2

    password = os.environ.get("BOOKING_TEST_PASSWORD")
    if not password or len(password) < 12:
        print(
            "BOOKING_TEST_PASSWORD is required and must be at least 12 characters",
            file=sys.stderr,
        )
        return 2

    factory = get_session_factory()
    redis = get_redis()
    try:
        async with factory() as session:
            await _ensure_account(
                session,
                settings=settings,
                label="passenger",
                phone=passenger_phone,
                email=args.passenger_email,
                password=password,
            )
            await _ensure_account(
                session,
                settings=settings,
                label="driver",
                phone=driver_phone,
                email=args.driver_email,
                password=password,
            )
    finally:
        await close_redis(redis)
        await dispose_engine()
    print(f"PASSENGER_PHONE={passenger_phone} DRIVER_PHONE={driver_phone}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
