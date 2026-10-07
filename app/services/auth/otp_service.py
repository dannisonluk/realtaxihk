"""OTP: issue (hashed, TTL, resend cooldown, attempt-capped) and consume.

This module proves **a phone number** and nothing else. It used to also be the
registration path — `verify_otp` created a `users` row on first success, so
"verify a phone" and "create an account" were the same act. That is gone.
Accounts are created by `AccountAuthService.register`, and the two callers here
are

* the secondary OTP **login** (`verify_otp`), which only ever signs in an
  account whose number is already verified. It cannot create one, and it cannot
  sign in an account that merely *claims* the number; and
* phone **binding** (`phone_binding_service`), which proves a number before
  attaching it to an account that already exists.

Both go through `consume_code`, so the security properties below apply to both
without either having to restate them — and, more importantly, without either
being able to relax one of them on its own.

Security properties:
- codes stored as sha256(phone:code) — plaintext never persisted;
- max 5 attempts per code, then the code is dead even if correct;
- resend cooldown prevents OTP-flooding a phone number;
- comparison is constant-time (SEC-28);
- PDPO: expired/consumed codes are short-lived rows (periodic purge in
  `app/services/infra/maintenance.py`, started as `pdpo_purge` in
  `app/main.py`).

SEC-02: the code is NEVER returned in the response body, in any environment.
It used to be echoed as `dev_code` whenever `ALLOW_DEV_OTP` was on, which handed
a usable OTP to whoever called `/otp/request` — the response goes to the
attacker, so the OTP stopped proving anything. Test harnesses read the code at
the notify seam instead (tests/conftest.py), so nothing needs the echo.

What remains is only the *determinism*: `ALLOW_DEV_OTP` (dev/test only, rejected
outright when APP_ENV=prod) makes the code a fixed constant so an out-of-process
harness — which cannot install a test double — has a way to log in. That switch
is fail-closed and never reachable in production; the echo was not.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import mark_explicit_commit
from app.core.exceptions import BusinessRuleError
from app.core.phone import is_hk_phone
from app.models import OtpCode, User
from app.services.infra.notify import get_whatsapp_provider

_MAX_ATTEMPTS = 5
_RESEND_COOLDOWN_S = 60
# A phone-wide attempt window rather than a per-row one: a fresh code must not
# reset the budget an attacker already spent on the previous code. Kept in sync
# with the default TTL so expired rows fall out of the count.
_ATTEMPT_WINDOW_S = 300
_DEV_CODE = "123456"  # only used when settings.dev_otp_enabled is explicitly True


def _hash_code(phone_e164: str, code: str) -> str:
    return hashlib.sha256(f"{phone_e164}:{code}".encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(value: datetime | None) -> datetime | None:
    """Postgres returns aware datetimes; SQLite and some drivers do not.

    Normalise before comparing rather than raising `TypeError` from inside a
    comparison — the failure would surface as a 500 on a *login*, which is the
    worst place to discover a driver difference.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class AuthResult:
    """The outcome of a secondary OTP login.

    `created` is always `False` and is kept only because it is on the wire —
    see `TokenPairOut.created`. It used to report whether this verify had
    registered a brand-new account; an OTP can no longer create one, so the
    field now reports the truth rather than being removed. Dropping it would
    change a response the mobile client decodes for no security gain.
    """

    user: User
    created: bool = False


class OtpService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _recent_phone_attempts(self, phone_e164: str) -> int:
        """Sum wrong attempts on recent codes for one phone."""
        cutoff = _now() - timedelta(seconds=_ATTEMPT_WINDOW_S)
        rows = (
            (
                await self.session.execute(
                    select(OtpCode.attempts).where(
                        OtpCode.phone_e164 == phone_e164,
                        OtpCode.created_at >= cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )
        return sum(rows)

    async def request_otp(self, phone_e164: str, ttl_seconds: int = 300) -> dict:
        if not is_hk_phone(phone_e164):
            raise BusinessRuleError("phone must be a Hong Kong number in E.164 form (+852XXXXXXXX)")

        cutoff = _now() - timedelta(seconds=_RESEND_COOLDOWN_S)
        if await self._recent_phone_attempts(phone_e164) >= _MAX_ATTEMPTS:
            raise BusinessRuleError("OTP locked: too many attempts")
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
        dev_mode = settings.dev_otp_enabled
        code = _DEV_CODE if dev_mode else f"{secrets.randbelow(10**6):06d}"
        otp = OtpCode(
            phone_e164=phone_e164,
            code_hash=_hash_code(phone_e164, code),
            expires_at=_now() + timedelta(seconds=ttl_seconds),
        )
        self.session.add(otp)
        await self.session.flush()
        # The WhatsApp call is an outbound side-effect. If we sent it before the
        # DB transaction had hardened, a lost connection at commit time would
        # deliver an OTP that had no row behind it. Commit first so the
        # external send is ordered after persistence; if delivery itself fails,
        # remove the row so the cooldown does not lock the user out of retrying.
        await self.session.commit()
        mark_explicit_commit(self.session)
        try:
            await get_whatsapp_provider().send_otp(phone_e164, code)
        except Exception:
            await self.session.delete(otp)
            await self.session.commit()
            mark_explicit_commit(self.session)
            raise

        # SEC-02: no `dev_code` echo. The code leaves this function exactly once,
        # through the notification provider. See the module docstring.
        return {"sent": True, "expires_in": ttl_seconds}

    async def verify_otp(self, phone_e164: str, code: str) -> AuthResult:
        """Secondary login: prove a number that is already verified on an account.

        The `phone_verified_at IS NOT NULL` filter is the entire safety argument
        for keeping an OTP login at all. The previous version looked the user up
        by phone alone and *created* one when nothing matched — so anyone who
        could receive a code for a number became whatever account that number
        named, and the lookup could not tell "I am proving my own number" from
        "I am logging in as whoever owns this number". Requiring a prior
        verification means this path can only re-enter an account that already
        proved it owns the number, so a stolen OTP reaches nothing that the SIM
        did not already reach.

        `uq_users_phone_e164_verified` guarantees at most one such row, so
        `.first()` is not a tie-break between candidates; it is what makes the
        query total for the type checker.
        """
        await self.consume_code(phone_e164, code)

        user = (
            (
                await self.session.execute(
                    select(User).where(
                        User.phone_e164 == phone_e164,
                        User.phone_verified_at.is_not(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if user is None:
            # The code was genuine, so the number is real and reachable — no
            # account has ever verified it. Saying so leaks nothing: the caller
            # had to receive a code sent to that number to get here.
            raise BusinessRuleError(
                "no account has verified this number — sign in with your email and password"
            )
        return AuthResult(user=user)

    async def verify_otp_for_user(self, user: User, code: str) -> None:
        """P-4: prove that `user` still controls the number on their account.

        Different from `verify_otp` in exactly one way, and it is the way that
        matters: this asserts an *existing* binding rather than establishing
        one. It takes the number from the row, not from the request, so it
        cannot be used to move a deadline onto somebody else's number — the
        caller cannot pass a number at all.
        """
        await self.consume_code(user.phone_e164, code)

    async def consume_code(self, phone_e164: str, code: str) -> None:
        """Validate `code` for `phone_e164` and mark it used.

        **Returns nothing, deliberately.** It used to return the `User` it had
        looked up — or created — by phone, which is precisely what let the two
        callers above accidentally share a policy that suited neither. The only
        thing this method establishes is "whoever holds this phone received this
        code a moment ago". What that is worth is the caller's business.
        """
        if not is_hk_phone(phone_e164):
            raise BusinessRuleError("phone must be a Hong Kong number in E.164 form (+852XXXXXXXX)")

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
        phone_attempts = await self._recent_phone_attempts(phone_e164)
        if phone_attempts >= _MAX_ATTEMPTS:
            raise BusinessRuleError("OTP locked: too many attempts")
        if otp.consumed_at is not None:
            raise BusinessRuleError("OTP already used")
        expires_at = _as_aware(otp.expires_at)
        # A NULL expiry cannot be trusted, so it counts as expired — the same
        # fail-closed direction as every other branch here.
        if expires_at is None or _now() >= expires_at:
            raise BusinessRuleError("OTP expired")

        # SEC-28: constant-time compare so the response latency does not leak how
        # many leading hex characters of the stored hash matched.
        if not hmac.compare_digest(otp.code_hash, _hash_code(phone_e164, code)):
            otp.attempts += 1
            await self.session.flush()
            raise BusinessRuleError(
                "Invalid OTP code",
                {"attempts_remaining": max(0, _MAX_ATTEMPTS - phone_attempts - 1)},
            )

        otp.consumed_at = _now()
        # Flushed rather than left to the caller's commit: single-use is a
        # security property of this method, and a caller that forgot to commit
        # would silently make the code reusable. The caller still commits the
        # rest of its work.
        await self.session.flush()
