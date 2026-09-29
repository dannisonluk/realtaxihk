"""OTP: issue (hashed, TTL, resend cooldown, attempt-capped) and verify.

Security properties:
- codes stored as sha256(phone:code) — plaintext never persisted;
- max 5 attempts per code, then the code is dead even if correct;
- resend cooldown prevents OTP-flooding a phone number;
- PDPO: expired/consumed codes are short-lived rows (purge job later).
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError
from app.models import OtpCode, User, UserRole
from app.services.notify import get_whatsapp_provider

_PHONE_RE = re.compile(r"^\+852\d{8}$")
_MAX_ATTEMPTS = 5
_RESEND_COOLDOWN_S = 60
_DEV_CODE = "123456"  # deterministic in dev for tests; prod uses random codes


def _hash_code(phone_e164: str, code: str) -> str:
    return hashlib.sha256(f"{phone_e164}:{code}".encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


class AuthResult:
    def __init__(self, user: User, created: bool):
        self.user = user
        self.created = created


class OtpService:
    _last_code: str  # dev convenience for tests (never returned in prod)

    def __init__(self, session: AsyncSession):
        self.session = session

    async def request_otp(self, phone_e164: str, ttl_seconds: int = 300) -> dict:
        if not _PHONE_RE.fullmatch(phone_e164 or ""):
            raise ValueError("phone_e164 must be an HK number in E.164 form (+852XXXXXXXX)")

        cutoff = _now() - timedelta(seconds=_RESEND_COOLDOWN_S)
        recent = (
            (
                await self.session.execute(
                    select(OtpCode)
                    .where(OtpCode.phone_e164 == phone_e164, OtpCode.created_at > cutoff)
                    .order_by(OtpCode.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if recent is not None:
            raise BusinessRuleError(
                "OTP resend cooldown active",
                {"retry_after_seconds": _RESEND_COOLDOWN_S},
            )

        settings = get_settings()
        code = _DEV_CODE if settings.app_env == "dev" else f"{secrets.randbelow(10**6):06d}"
        self._last_code = code
        otp = OtpCode(
            phone_e164=phone_e164,
            code_hash=_hash_code(phone_e164, code),
            expires_at=_now() + timedelta(seconds=ttl_seconds),
        )
        self.session.add(otp)
        await self.session.flush()

        await get_whatsapp_provider().send_otp(phone_e164, code)

        result: dict = {"sent": True, "expires_in": ttl_seconds}
        if settings.app_env == "dev":
            result["dev_code"] = code
        return result

    async def verify_otp(self, phone_e164: str, code: str) -> AuthResult:
        if not _PHONE_RE.fullmatch(phone_e164 or ""):
            raise ValueError("phone_e164 must be an HK number in E.164 form (+852XXXXXXXX)")

        otp = (
            (
                await self.session.execute(
                    select(OtpCode)
                    .where(OtpCode.phone_e164 == phone_e164)
                    .order_by(OtpCode.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if otp is None:
            raise BusinessRuleError("OTP not found — request a code first")
        if otp.attempts >= _MAX_ATTEMPTS:
            raise BusinessRuleError("OTP locked: too many attempts")
        if otp.consumed_at is not None:
            raise BusinessRuleError("OTP already used")
        if _now() >= otp.expires_at:
            raise BusinessRuleError("OTP expired")

        if otp.code_hash != _hash_code(phone_e164, code):
            otp.attempts += 1
            await self.session.flush()
            raise BusinessRuleError(
                "Invalid OTP code",
                {"attempts_remaining": _MAX_ATTEMPTS - otp.attempts},
            )

        otp.consumed_at = _now()

        user = (
            (await self.session.execute(select(User).where(User.phone_e164 == phone_e164)))
            .scalars()
            .first()
        )
        created = user is None
        if created:
            user = User(
                phone_e164=phone_e164,
                role=UserRole.PASSENGER,
                phone_verified_at=_now(),
            )
            self.session.add(user)
            await self.session.flush()
        return AuthResult(user=user, created=created)
