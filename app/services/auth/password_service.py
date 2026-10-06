"""Changing a password, and recovering an account when it is forgotten.

Why this is its own module
--------------------------
`account_service` owns "prove a password to get in"; this owns "replace the
password". They share the lockout counter and the email lookup, and they share
nothing else — a change is made by someone who is already authenticated and
knows the current secret, a reset is made by someone who knows neither. Folding
the second into the first would put two different authorisation stories behind
one class name.

Two rules that are not obvious, and are the reason this module is not four lines
in the router
----------------------------------------------------------------------------
1. **Every session dies.** A password change is either routine hygiene or the
   response to "I think someone else is in my account". The second case is the
   one that matters, and in it, leaving the other party's tokens alive — even
   for the 15 minutes an access token has left to run — defeats the point. So
   both halves are revoked: every refresh row, and the access-token epoch.
   Consequence, stated plainly: **the caller is signed out too.** There is no
   per-session identity to spare (the epoch is per-user, and the refresh token
   is not presented on this route), so "sign out everywhere" is the only honest
   behaviour — and the caller's next request answers 401, which is what the
   client acts on.
2. **A wrong current password is a credential failure, not a form error.** It
   is counted against the same lockout the login route uses. Without that,
   someone holding a stolen session could sit on the change endpoint and
   brute-force the current password at leisure, never touching the login path
   that is being watched.

The forgotten-password flow is a *link*, like email verification and for the
same reason: a code would have to be typed back into a session that may not
exist (the user is on a different device — that is usually *why* they forgot).
The token is 256 bits of CSPRNG stored as a SHA-256 digest, single-use, and
short-lived (`password_reset_ttl_minutes`), so a leaked backup yields no working
links and an old link found in a mailbox is already dead.

**No account oracle.** `request_reset` answers identically whether or not the
address is registered, and does not distinguish "unknown address" from "sent" in
its response body. The residual signal is timing — a real send costs an SMTP
round trip — which is accepted and noted rather than papered over: suppressing
it would mean either sending mail to addresses that do not exist or padding
every request with a sleep, and neither is worth the cost here.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError
from app.core.passwords import hash_password, verify_password
from app.core.token_revocation import revoke_user_tokens
from app.models import PasswordResetToken, User
from app.services.auth.account_service import (
    AccountAuthError,
    AccountAuthService,
    AccountLocked,
)
from app.services.auth.refresh_service import RefreshService
from app.services.infra.notify import get_email_provider

logger = logging.getLogger("realtaxihk.password")

# 256 bits. This is a bearer credential in an email: whoever holds it can set
# the account's password.
_TOKEN_BYTES = 32


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _hash_token(raw: str) -> str:
    """Plain SHA-256, not argon2 — the same reasoning as the verification token.

    The input is 256 bits of CSPRNG output, so there is no dictionary to attack
    and a memory-hard KDF buys nothing — while argon2 at 64 MiB would run on
    every click of a reset link, which is a trivial way to make the endpoint a
    resource-exhaustion lever.
    """
    return hashlib.sha256(raw.encode()).hexdigest()


class PasswordService:
    def __init__(self, session: AsyncSession, redis) -> None:
        self.session = session
        self.redis = redis
        # The lockout counter and the email lookup live on the account service;
        # reaching for them through it keeps one definition of "how many wrong
        # guesses" and one of "which row is this address".
        self.accounts = AccountAuthService(session, redis)

    # ------------------------------------------------------------------ #
    # Change (authenticated)
    # ------------------------------------------------------------------ #

    async def change(self, user: User, *, current_password: str, new_password: str) -> int:
        """Replace a known password. Returns the number of sessions revoked.

        Order matters and is deliberate: the current password is verified
        *before* the new one is hashed, so a policy rejection cannot be used to
        probe anything, and a policy rejection does **not** count as a failed
        attempt (the caller proved they own the account; they just chose a bad
        password).
        """
        if not user.password_hash:
            # An OTP-only account. Saying so is not an oracle — the caller is
            # authenticated — and the alternative (a generic "incorrect current
            # password") would send them to support instead of to the reset link.
            raise BusinessRuleError(
                "this account has no password yet — use the reset link to set one",
                {"reason": "PASSWORD_NOT_SET"},
            )

        if AccountAuthService.is_locked(user):
            # `AccountLocked` answers 401 through the router's `_run`, on the
            # same argument as the login route: 429 would confirm the account
            # exists and has been guessed at.
            raise AccountLocked("too many failed attempts — try again later")

        if not verify_password(user.password_hash, current_password):
            await self.accounts.register_failure(user)
            # Generic, like login: "wrong current password" is the only thing
            # this route should ever say about a credential.
            raise AccountAuthError("current password is incorrect")

        if verify_password(user.password_hash, new_password):
            raise BusinessRuleError(
                "the new password must be different from the current one",
                {"reason": "PASSWORD_UNCHANGED"},
            )

        # May raise `PasswordPolicyError` (a ValueError, mapped to 400 by the
        # router) — the policy sentence is the whole message.
        user.password_hash = hash_password(new_password)
        await self.accounts.clear_failures(user)
        await self.session.flush()

        revoked = await self._revoke_every_session(user.id)
        logger.info("password changed user_id=%s sessions_revoked=%s", user.id, revoked)
        return revoked

    # ------------------------------------------------------------------ #
    # Forgot / reset (unauthenticated)
    # ------------------------------------------------------------------ #

    async def request_reset(self, email: str, *, ip: str | None = None) -> dict:
        """Email a reset link, or quietly do nothing.

        The return value is identical in both cases — including `expires_in`,
        which is in **seconds**, the same unit as `OtpRequestOut.expires_in`.
        A caller who can tell the two apart can ask this endpoint
        whether an address has an account, and that is a question this platform
        should not answer to an anonymous caller. `find_by_email` is a
        case-folded lookup, so the answer does not depend on how they typed it.
        """
        settings = get_settings()
        user = await self.accounts.find_by_email(email)

        if user is None or not user.email:
            logger.info("password reset requested for an unknown address")
            return {"sent": True, "expires_in": settings.password_reset_ttl_minutes * 60}

        raw = secrets.token_urlsafe(_TOKEN_BYTES)
        token = PasswordResetToken(
            user_id=user.id,
            token_hash=_hash_token(raw),
            expires_at=_now() + timedelta(minutes=settings.password_reset_ttl_minutes),
            requested_ip=ip,
        )
        self.session.add(token)
        await self.session.flush()
        # Harden the transaction before sending the reset link: the request-scoped
        # dependency commits only after the handler returns, so without this the
        # email can carry a token that never became durable. On send failure,
        # discard the just-created row so a retry issues a fresh token.
        link = f"{settings.public_base_url.rstrip('/')}/reset-password?token={raw}"
        await self.session.commit()
        try:
            await get_email_provider().send_email(
                user.email,
                "重設密碼 / Reset your password",
                (
                    "有人在 hkfastdc 要求重設這個電郵地址的密碼。\n"
                    "請開啟以下連結設定新密碼：\n\n"
                    f"{link}\n\n"
                    f"連結將於 {settings.password_reset_ttl_minutes} 分鐘後失效。"
                    "如果這不是你本人要求的，可以忽略這封電郵 —— 密碼不會改變。\n\n"
                    "Someone asked to reset the password for this address on hkfastdc.\n"
                    "Open the link above to choose a new one. It expires in "
                    f"{settings.password_reset_ttl_minutes} minutes. If this was not you, "
                    "ignore this message — nothing has changed."
                ),
            )
        except Exception:
            await self.session.delete(token)
            await self.session.commit()
            raise
        logger.info("password reset link issued user_id=%s", user.id)
        return {"sent": True, "expires_in": settings.password_reset_ttl_minutes * 60}

    async def reset(self, raw_token: str, new_password: str) -> int:
        """Consume a reset link and set the new password.

        Returns the number of sessions revoked. Every failure mode — unknown,
        expired, already used, orphaned account — answers with the **same**
        sentence, so a holder of a stolen token learns nothing about whether it
        was ever real.
        """
        if not raw_token:
            raise BusinessRuleError("a reset token is required")

        row = (
            await self.session.execute(
                select(PasswordResetToken).where(
                    PasswordResetToken.token_hash == _hash_token(raw_token)
                )
            )
        ).scalar_one_or_none()

        invalid = BusinessRuleError("this reset link is invalid or has expired")
        if row is None or row.consumed_at is not None:
            raise invalid
        expires_at = _as_aware(row.expires_at)
        # A NULL expiry is treated as expired: fail closed, like every other
        # branch here.
        if expires_at is None or _now() >= expires_at:
            raise invalid

        user = await self.session.get(User, row.user_id)
        if user is None:
            raise invalid

        # Hash *before* consuming the token, so a policy rejection leaves the
        # link usable — the user gets "at least 12 characters" and can try again
        # rather than having to request a fresh link.
        user.password_hash = hash_password(new_password)
        row.consumed_at = _now()
        # A reset is a proof of control, so a brute-force lockout from before it
        # must not survive it — otherwise the user resets and is still locked
        # out, which reads as "the reset did not work".
        await self.accounts.clear_failures(user)
        await self.session.flush()

        revoked = await self._revoke_every_session(user.id)
        logger.info("password reset user_id=%s sessions_revoked=%s", user.id, revoked)
        return revoked

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _revoke_every_session(self, user_id) -> int:
        """Kill every refresh row and every access token this user holds.

        Both halves are needed and neither is redundant: the epoch check runs on
        the *hot path* (`app/core/deps.py`) and is what makes an already-issued
        access token stop working, while the refresh rows are what stop the
        client from simply minting a new one. Revoking only the rows would leave
        the stolen access token valid for the rest of its life; revoking only
        the epoch would let the stolen refresh token mint a fresh one.
        """
        revoked = await RefreshService(self.session).revoke_all_for_user(user_id)
        # Commit the refresh-row revocation before writing the Redis epoch:
        # `revoke_all_for_user` only flushes, and if the request-scoped commit
        # later failed, the epoch would kill access tokens while the refresh
        # rows stayed live, letting a stolen refresh token mint a fresh access
        # token with an `iat` newer than the epoch.
        await self.session.commit()
        await revoke_user_tokens(self.redis, user_id)
        return revoked
