"""Admin authentication: username + password + TOTP, with first-login enrolment.

This is the highest-privilege authentication path in the system. An admin
account can move money (`ADJUSTMENT`), approve refunds and run fleet
settlement, so the flow is deliberately more suspicious than the passenger one.

The login is a **three-step state machine**, not a single call, and that shape
is forced by first-login enrolment rather than chosen:

    POST /admin/auth/login      username + password
      -> if TOTP enrolled:     returns a challenge token (not an access token)
         if not enrolled:      returns an enrolment secret + QR + recovery codes
    POST /admin/auth/totp/verify   challenge token + 6-digit code -> access token
    POST /admin/auth/totp/enrol    challenge token + code        -> enrolled

**Why a challenge token and not "password then code in one request".** The
password and the TOTP code are two independent factors; putting both in one
request means a wrong code is indistinguishable from a wrong password in the
logs and in the rate limiter, and it forces the client to hold the password
after submitting it. A short-lived single-purpose token between the steps keeps
each factor separately observable, separately rate-limited, and lets the client
forget the password immediately.

**The challenge token is not an access token.** It is signed with the same key
but carries `purpose=admin_totp_challenge` and a 5-minute life, and `deps.py`
will never accept it as an access token because `get_current_user` requires
`sub`+`role` and this token deliberately carries neither. That is the
distinction that stops a half-authenticated session from reaching anything.

**Rate limiting has two independent budgets** because they defend different
attacks: per-account (a targeted brute force on one admin) and per-IP (a
spray against many usernames from one host). Either alone leaves the other open.

**Lockout is separate from `is_active`.** A brute-force lock is self-clearing and
must never look like an administrative ban in the audit trail; and it must never
be usable as a denial-of-service against a real admin by simply guessing wrong
at their username — which is why the lock is per-account but a *successful*
login from the legitimate owner clears it, and why the counter is distinct from
`is_active`.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError
from app.core.passwords import (
    burn_password_time,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.core.rate_limit import RateLimiter
from app.core.security import create_access_token, decode_access_token
from app.core.totp import (
    current_step,
    generate_recovery_codes,
    generate_secret,
    hash_recovery_code,
    provisioning_uri,
    verify_totp,
)
from app.models import AdminAccount, AdminAuditLog, AdminRecoveryCode

logger = logging.getLogger("realtaxihk.admin_auth")

# --- Policy constants. Stated here rather than inline so the numbers are
# reviewable in one place, and asserted in the tests.
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
CHALLENGE_TTL_MINUTES = 5
RECOVERY_CODE_COUNT = 8

# Per-account and per-IP budgets are deliberately different: the first stops a
# targeted attack, the second a spray. A shared counter would let one attacker's
# noise lock out every admin.
LOGIN_ACCOUNT_LIMIT = 10
LOGIN_ACCOUNT_WINDOW_S = 900
LOGIN_IP_LIMIT = 30
LOGIN_IP_WINDOW_S = 900
TOTP_IP_LIMIT = 20
TOTP_IP_WINDOW_S = 900

CHALLENGE_PURPOSE = "admin_totp_challenge"
ENROL_PURPOSE = "admin_totp_enrol"

# Events recorded in admin_audit_log. String constants, not an enum: the table
# is append-only and an enum change would need a migration for every new event.
EV_LOGIN = "ADMIN_LOGIN"
EV_LOGIN_FAILED = "ADMIN_LOGIN_FAILED"
EV_LOCKED = "ADMIN_LOCKED_OUT"
EV_TOTP_VERIFY = "ADMIN_TOTP_VERIFY"
EV_TOTP_FAILED = "ADMIN_TOTP_FAILED"
EV_TOTP_ENROLLED = "ADMIN_TOTP_ENROLLED"
EV_RECOVERY_USED = "ADMIN_RECOVERY_USED"
EV_RECOVERY_REGENERATED = "ADMIN_RECOVERY_REGENERATED"


class AdminAuthError(BusinessRuleError):
    """Authentication failure. Message is deliberately generic to the caller."""


@dataclass(frozen=True)
class LoginOutcome:
    """What `login` decided, so the router does not re-derive it."""

    kind: str  # "totp_required" | "enrolment_required" | "recovery_required"
    challenge_token: str
    enrolment: EnrolmentOut | None = None


@dataclass(frozen=True)
class EnrolmentOut:
    secret: str
    otpauth_uri: str
    recovery_codes: list[str]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _as_aware(value: dt.datetime | None) -> dt.datetime | None:
    """Postgres returns aware datetimes; SQLite/naive drivers do not. Compare
    safely rather than raising TypeError deep in a comparison."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


class AdminAuthService:
    """Stateless apart from the session and Redis handles it is given."""

    def __init__(self, session: AsyncSession, redis, *, namespace: str = "realtaxi:") -> None:
        self.session = session
        self.redis = redis
        self.limiter = RateLimiter(redis, namespace=namespace)

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #

    async def audit(
        self,
        event: str,
        outcome: str,
        *,
        admin_id: uuid.UUID | None = None,
        username: str | None = None,
        detail: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Append an audit row. Never raises: an audit failure must not fail
        a login, or a full disk becomes an outage.

        Truncation rather than rejection — a long User-Agent must not lose the
        event.
        """
        try:
            self.session.add(
                AdminAuditLog(
                    admin_id=admin_id,
                    username_attempted=(username or None) and username[:64],
                    event=event,
                    outcome=outcome,
                    detail=(detail or None) and detail[:255],
                    ip_address=(ip or None) and ip[:45],
                    user_agent=(user_agent or None) and user_agent[:255],
                )
            )
            await self.session.flush()
        except Exception:
            logger.exception("failed to write admin audit row event=%s", event)

    # ------------------------------------------------------------------ #
    # Lookup and lockout
    # ------------------------------------------------------------------ #

    async def find_by_username(self, username: str) -> AdminAccount | None:
        """Case-insensitive lookup. Usernames are stored lower-case, and the
        normalisation happens here so a caller cannot forget it and produce a
        duplicate account that differs only by case."""
        normalized = (username or "").strip().lower()
        if not normalized:
            return None
        result = await self.session.execute(
            select(AdminAccount).where(AdminAccount.username == normalized)
        )
        return result.scalar_one_or_none()

    async def find_by_email(self, email: str) -> AdminAccount | None:
        normalized = (email or "").strip().lower()
        if not normalized:
            return None
        result = await self.session.execute(
            select(AdminAccount).where(AdminAccount.email == normalized)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def is_locked(account: AdminAccount, now: dt.datetime | None = None) -> bool:
        locked_until = _as_aware(account.locked_until)
        if locked_until is None:
            return False
        return locked_until > (now or _now())

    async def _register_failure(self, account: AdminAccount) -> None:
        """Increment the counter and lock at the threshold.

        The account row is updated rather than a Redis counter so the lock
        survives a Redis flush — losing Redis must not reset a brute-force
        lockout.
        """
        account.failed_login_count = (account.failed_login_count or 0) + 1
        if account.failed_login_count >= MAX_FAILED_LOGINS:
            account.locked_until = _now() + dt.timedelta(minutes=LOCKOUT_MINUTES)
            logger.warning(
                "admin account locked username=%s until=%s",
                account.username,
                account.locked_until,
            )
        await self.session.flush()

    async def _clear_failures(self, account: AdminAccount) -> None:
        if account.failed_login_count or account.locked_until:
            account.failed_login_count = 0
            account.locked_until = None
            await self.session.flush()

    # ------------------------------------------------------------------ #
    # Step 1 — password
    # ------------------------------------------------------------------ #

    async def login(
        self,
        *,
        username: str,
        password: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> LoginOutcome:
        """Verify the password and decide which second step is needed.

        Every early return does the same amount of work as the happy path as far
        as it can: `burn_password_time()` runs when the account does not exist,
        so a username that is not registered takes the same time as one that is
        (the argon2 verify dominates, and skipping it is a measurable username
        oracle).
        """
        username_key = (username or "").strip().lower()

        # Rate limit BEFORE touching the database: the point is to not do work.
        if not await self.limiter.allow(f"admin:login:ip:{ip}", LOGIN_IP_LIMIT, LOGIN_IP_WINDOW_S):
            await self.audit(
                EV_LOGIN_FAILED, "FAILURE", username=username_key, detail="ip rate limit", ip=ip
            )
            raise AdminAuthError("too many attempts — try again later")
        if not await self.limiter.allow(
            f"admin:login:user:{username_key}", LOGIN_ACCOUNT_LIMIT, LOGIN_ACCOUNT_WINDOW_S
        ):
            await self.audit(
                EV_LOGIN_FAILED,
                "FAILURE",
                username=username_key,
                detail="account rate limit",
                ip=ip,
            )
            raise AdminAuthError("too many attempts — try again later")

        account = await self.find_by_username(username_key)
        if account is None:
            # Spend a hash's worth of time so the response time does not reveal
            # whether the username exists.
            burn_password_time()
            await self.audit(
                EV_LOGIN_FAILED, "FAILURE", username=username_key, detail="no such account", ip=ip
            )
            raise AdminAuthError("invalid credentials")

        if self.is_locked(account):
            await self.audit(
                EV_LOCKED,
                "FAILURE",
                admin_id=account.id,
                username=username_key,
                detail="locked",
                ip=ip,
            )
            raise AdminAuthError("account temporarily locked")

        if not verify_password(account.password_hash, password):
            await self._register_failure(account)
            await self.audit(
                EV_LOGIN_FAILED,
                "FAILURE",
                admin_id=account.id,
                username=username_key,
                detail="bad password",
                ip=ip,
                user_agent=user_agent,
            )
            raise AdminAuthError("invalid credentials")

        if not account.is_active:
            await self.audit(
                EV_LOGIN_FAILED,
                "FAILURE",
                admin_id=account.id,
                username=username_key,
                detail="inactive",
                ip=ip,
            )
            raise AdminAuthError("account disabled")

        # Password is correct: clear the counter so a legitimate login wipes a
        # partial brute force, then hand back a challenge. NO access token is
        # issued here — the second factor has not been proven yet.
        await self._clear_failures(account)

        # Transparent hash upgrade: verify succeeded, so re-hash with current
        # parameters. This is how the cost is raised without a password reset.
        if needs_rehash(account.password_hash):
            account.password_hash = hash_password(password)
            await self.session.flush()
            logger.info("rehashed admin password to current params username=%s", account.username)

        if account.totp_secret is None:
            enrolment = await self._begin_enrolment(account)
            await self.audit(
                EV_LOGIN,
                "SUCCESS",
                admin_id=account.id,
                username=username_key,
                detail="enrolment required",
                ip=ip,
                user_agent=user_agent,
            )
            return LoginOutcome(
                kind="enrolment_required",
                challenge_token=self._challenge_token(account, ENROL_PURPOSE),
                enrolment=enrolment,
            )

        await self.audit(
            EV_LOGIN,
            "SUCCESS",
            admin_id=account.id,
            username=username_key,
            detail="totp required",
            ip=ip,
            user_agent=user_agent,
        )
        return LoginOutcome(
            kind="totp_required",
            challenge_token=self._challenge_token(account, CHALLENGE_PURPOSE),
        )

    # ------------------------------------------------------------------ #
    # Step 2 — TOTP
    # ------------------------------------------------------------------ #

    async def verify_totp(
        self,
        *,
        challenge_token: str,
        code: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> AdminAccount:
        """Consume the challenge and a TOTP code, returning the account.

        The caller then mints an access token. Keeping token minting out of this
        method means there is exactly one place that issues admin access tokens,
        which is easier to review than several.
        """
        account = await self._resolve_challenge(challenge_token, CHALLENGE_PURPOSE)

        if not await self.limiter.allow(f"admin:totp:ip:{ip}", TOTP_IP_LIMIT, TOTP_IP_WINDOW_S):
            raise AdminAuthError("too many attempts — try again later")

        if account.totp_secret is None:
            raise AdminAuthError("TOTP is not enrolled")

        # Replay-safe: the stored counter refuses a code at or before the last
        # accepted step, so an observed code cannot be reused within its window.
        counter = verify_totp(
            account.totp_secret,
            code,
            last_used_counter=account.totp_last_counter,
        )
        if counter is None:
            await self.audit(
                EV_TOTP_FAILED,
                "FAILURE",
                admin_id=account.id,
                username=account.username,
                detail="bad or replayed code",
                ip=ip,
            )
            raise AdminAuthError("invalid code")

        account.totp_last_counter = counter
        account.last_login_at = _now()
        await self.session.flush()

        await self.audit(
            EV_TOTP_VERIFY,
            "SUCCESS",
            admin_id=account.id,
            username=account.username,
            ip=ip,
            user_agent=user_agent,
        )
        return account

    # ------------------------------------------------------------------ #
    # Step 2' — enrolment
    # ------------------------------------------------------------------ #

    async def _begin_enrolment(self, account: AdminAccount) -> EnrolmentOut:
        """Generate a secret and recovery codes, but do NOT persist them yet.

        Nothing is written until the admin proves they can generate a code from
        the secret. Persisting first is the classic lockout bug: the secret is
        stored, the admin fails to add it to their app, and the account now
        requires a second factor nobody holds.
        """
        stored = await self._pending_enrolment(account)
        if stored is not None:
            return stored

        secret = generate_secret()
        codes = generate_recovery_codes(RECOVERY_CODE_COUNT)
        payload = _EnrolmentPayload(secret=secret, codes=codes)

        # Held in Redis for the challenge window, keyed to the account. Not in
        # the token: a JWT is readable by the client, and putting the secret in
        # it would hand the second factor to anyone who sees the network
        # response — the one thing enrolment must not do.
        await self.redis.set(
            f"admin:enrol:{account.id}",
            payload.to_json(),
            ex=CHALLENGE_TTL_MINUTES * 60,
        )
        return EnrolmentOut(
            secret=secret,
            otpauth_uri=provisioning_uri(secret, account.username, issuer="RealTaxi HK"),
            recovery_codes=codes,
        )

    async def _pending_enrolment(self, account: AdminAccount) -> EnrolmentOut | None:
        raw = await self.redis.get(f"admin:enrol:{account.id}")
        if not raw:
            return None
        payload = _EnrolmentPayload.from_json(raw)
        if payload is None:
            return None
        return EnrolmentOut(
            secret=payload.secret,
            otpauth_uri=provisioning_uri(payload.secret, account.username, issuer="RealTaxi HK"),
            recovery_codes=payload.codes,
        )

    async def complete_enrolment(
        self,
        *,
        challenge_token: str,
        code: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> AdminAccount:
        """Confirm the admin can generate codes, then persist the secret.

        Requiring a valid code *before* storing is what makes enrolment
        self-verifying: a QR that did not scan cannot leave the account in a
        half-enrolled state.
        """
        account = await self._resolve_challenge(challenge_token, ENROL_PURPOSE)

        if account.totp_secret is not None:
            # Already enrolled — treat as success rather than erroring, so a
            # double-submit (a slow network, an impatient tap) is not a failure.
            return account

        pending = await self._pending_enrolment(account)
        if pending is None:
            raise AdminAuthError("enrolment expired — sign in again")

        counter = verify_totp(pending.secret, code, last_used_counter=None)
        if counter is None:
            await self.audit(
                EV_TOTP_FAILED,
                "FAILURE",
                admin_id=account.id,
                username=account.username,
                detail="enrolment code rejected",
                ip=ip,
            )
            raise AdminAuthError("invalid code — check the device clock and try again")

        account.totp_secret = pending.secret
        account.totp_enrolled_at = _now()
        account.totp_last_counter = counter
        account.last_login_at = _now()

        # Recovery codes are persisted now, hashed. Regenerating them replaces
        # any previous set, so a stolen old sheet stops working at rotation.
        for existing in await self._recovery_rows(account):
            await self.session.delete(existing)
        for raw_code in pending.recovery_codes:
            self.session.add(
                AdminRecoveryCode(admin_id=account.id, code_hash=hash_recovery_code(raw_code))
            )
        await self.session.flush()

        await self.redis.delete(f"admin:enrol:{account.id}")
        await self.audit(
            EV_TOTP_ENROLLED,
            "SUCCESS",
            admin_id=account.id,
            username=account.username,
            detail=f"{len(pending.recovery_codes)} recovery codes issued",
            ip=ip,
            user_agent=user_agent,
        )
        return account

    async def regenerate_recovery_codes(self, account: AdminAccount) -> list[str]:
        """Issue a fresh set, invalidating every previous code. Used when the
        sheet is lost or after a suspected compromise."""
        codes = generate_recovery_codes(RECOVERY_CODE_COUNT)
        for existing in await self._recovery_rows(account):
            await self.session.delete(existing)
        for raw_code in codes:
            self.session.add(
                AdminRecoveryCode(admin_id=account.id, code_hash=hash_recovery_code(raw_code))
            )
        await self.session.flush()
        await self.audit(
            EV_RECOVERY_REGENERATED, "SUCCESS", admin_id=account.id, username=account.username
        )
        return codes

    async def consume_recovery_code(
        self,
        *,
        challenge_token: str,
        code: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> AdminAccount:
        """Use a recovery code instead of a TOTP code.

        Takes the step-1 challenge token rather than a username. The challenge
        only exists after a correct password, so this endpoint cannot be used to
        ask "does this username have any recovery codes?" — and it removes the
        username from the request entirely, which means there is nothing to
        enumerate.

        Deliberately rate-limited on the same IP budget as TOTP, and each code
        is single-use. A recovery code that could be reused would be a
        permanent second-factor bypass.
        """
        account = await self._resolve_challenge(challenge_token, CHALLENGE_PURPOSE)

        if not await self.limiter.allow(f"admin:totp:ip:{ip}", TOTP_IP_LIMIT, TOTP_IP_WINDOW_S):
            raise AdminAuthError("too many attempts — try again later")

        digest = hash_recovery_code(code)
        result = await self.session.execute(
            select(AdminRecoveryCode).where(
                AdminRecoveryCode.admin_id == account.id,
                AdminRecoveryCode.code_hash == digest,
                AdminRecoveryCode.used_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            await self.audit(
                EV_RECOVERY_USED,
                "FAILURE",
                admin_id=account.id,
                username=account.username,
                detail="no matching unused code",
                ip=ip,
            )
            raise AdminAuthError("invalid code")

        # Stamped, not deleted: "this code was used at T" is what you want to
        # see when investigating an account takeover.
        row.used_at = _now()
        account.last_login_at = _now()
        await self._clear_failures(account)
        await self.session.flush()

        await self.audit(
            EV_RECOVERY_USED, "SUCCESS", admin_id=account.id, username=account.username, ip=ip,
            user_agent=user_agent,
        )
        return account

    async def _recovery_rows(self, account: AdminAccount) -> list[AdminRecoveryCode]:
        result = await self.session.execute(
            select(AdminRecoveryCode).where(AdminRecoveryCode.admin_id == account.id)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------ #
    # Challenge tokens
    # ------------------------------------------------------------------ #

    def _challenge_token(self, account: AdminAccount, purpose: str) -> str:
        """A short-lived, single-purpose token for the step between factors.

        Carries no `role` and no `sub`, which is what makes it unusable as an
        access token: `principal_from_token` requires both, so this can never
        satisfy `get_current_user`. Signed with the same key purely to avoid a
        second secret to manage.
        """
        now = dt.datetime.now(dt.UTC)
        return create_access_token(
            {
                "purpose": purpose,
                "admin_id": str(account.id),
                "username": account.username,
                "iat": now,
            },
            expires_minutes=CHALLENGE_TTL_MINUTES,
        )

    async def _resolve_challenge(self, token: str, purpose: str) -> AdminAccount:
        """Validate a challenge token and load the account it names."""
        import jwt as pyjwt

        try:
            claims = decode_access_token(token)
        except pyjwt.PyJWTError as exc:
            raise AdminAuthError("challenge expired — sign in again") from exc

        if claims.get("purpose") != purpose:
            # A token minted for enrolment must not satisfy the TOTP step, or
            # the enrolment window becomes a way to skip the second factor.
            raise AdminAuthError("challenge is not valid for this step")

        try:
            admin_id = uuid.UUID(str(claims["admin_id"]))
        except (KeyError, ValueError) as exc:
            raise AdminAuthError("challenge expired — sign in again") from exc

        account = await self.session.get(AdminAccount, admin_id)
        if account is None or not account.is_active:
            raise AdminAuthError("account not found")
        if self.is_locked(account):
            raise AdminAuthError("account temporarily locked")
        return account


# --------------------------------------------------------------------- #
# Enrolment payload held in Redis
# --------------------------------------------------------------------- #


class _EnrolmentPayload:
    """The secret and codes between "password accepted" and "code proven".

    JSON rather than a pickled object: Redis content that deserialises into
    arbitrary Python is a remote-code-execution primitive if Redis is ever
    reachable, and this payload is two strings and a list.
    """

    __slots__ = ("codes", "secret")

    def __init__(self, secret: str, codes: list[str]) -> None:
        self.secret = secret
        self.codes = codes

    def to_json(self) -> str:
        import json

        return json.dumps({"secret": self.secret, "codes": self.codes})

    @classmethod
    def from_json(cls, raw: str | bytes) -> _EnrolmentPayload | None:
        import json

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            data = json.loads(raw)
            return cls(secret=str(data["secret"]), codes=[str(c) for c in data["codes"]])
        except (ValueError, KeyError, TypeError):
            return None


def issue_admin_access_token(account: AdminAccount) -> str:
    """Mint the real access token, only after both factors are proven.

    `role` is hardcoded to ADMIN rather than read from the row: an admin account
    is an admin by virtue of being in `admin_accounts`, and deriving the claim
    from the table it came from removes any path where a passed-in value could
    grant a different role.

    `scope` is what makes `sub` unambiguous. `sub` is an `admin_accounts.id`,
    which lives in a different UUID space from `users.id`, so without this claim
    a guard cannot tell which table to load — and `require_admin` loading `users`
    rejected every admin token with a 403. See `app/core/deps.py`.
    """
    return create_access_token({"sub": str(account.id), "role": "ADMIN", "scope": "admin"})


__all__ = [
    "CHALLENGE_TTL_MINUTES",
    "EV_LOCKED",
    "EV_LOGIN",
    "EV_LOGIN_FAILED",
    "EV_RECOVERY_REGENERATED",
    "EV_RECOVERY_USED",
    "EV_TOTP_ENROLLED",
    "EV_TOTP_FAILED",
    "EV_TOTP_VERIFY",
    "LOCKOUT_MINUTES",
    "MAX_FAILED_LOGINS",
    "RECOVERY_CODE_COUNT",
    "AdminAuthError",
    "AdminAuthService",
    "EnrolmentOut",
    "LoginOutcome",
    "issue_admin_access_token",
]

# Referenced by the tests to assert the window is single-use rather than
# merely small. (Not an `assert` — those are stripped under `python -O`, and a
# silent loss of the constant would turn the replay test into a no-op.)
STEP_SECONDS = 30
if current_step(1_700_000_000) != 1_700_000_000 // STEP_SECONDS:
    raise RuntimeError("current_step disagrees with integer division")
