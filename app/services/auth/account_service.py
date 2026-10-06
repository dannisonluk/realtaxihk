"""Email + password accounts: registration, sign-in, and the lockout around them.

Why this module exists
----------------------
Signing in *was* phone verification. `POST /auth/otp/verify` both registered and
logged in, and the phone number was simultaneously the identifier and the only
secret — so merely having an account required proving a phone, and losing the
number meant losing the account with no recovery path. This module splits the
two:

    register / login (here)      email + password. No phone proof required.
    OtpService                   proves a *phone*; still a secondary login for
                                 a number that is already verified.
    PhoneBindingService          binds and proves a number on an existing
                                 account — the call車 unlock.

Modelled on `AdminAuthService`, deliberately
--------------------------------------------
That is the mature implementation in this codebase, and none of the properties
below are incidental — each one was a bug there first:

* `burn_password_time()` on a miss, so "no such account" and "wrong password"
  take the same wall-clock time. A measurable difference is a working account
  oracle and needs no tooling to exploit.
* One generic `"invalid credentials"` for every credential failure, so the
  endpoint cannot be asked "does this address exist".
* `failed_login_count` / `locked_until` live on the **row**, not in Redis:
  flushing Redis must not reset a brute-force lockout.
* `needs_rehash` -> re-hash on successful login, so the argon2 cost can be
  raised later without a forced reset or a flag day.
* A *lockout* answers 401, not 429. 429 would separate "this account is locked"
  from "this IP is throttled", which confirms the account exists and has been
  attacked. See `_run` in `app/api/auth.py`.

What is deliberately NOT here
-----------------------------
**No "is this email registered?" check on register.** Registration has to tell
the user when an address is taken, so that much is an unavoidable oracle. But
the *phone* is different: it is not the login credential, it is only a claim at
this point, and checking it here would turn registration into a "is this number
on hkfastdc?" lookup — a PDPO problem for no functional gain, since a duplicate
claim is harmless (many accounts may claim a number; exactly one may verify it).
The refusal happens at verification time, where the number is about to be bound
and the answer has to be given anyway.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError
from app.core.passwords import (
    burn_password_time,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.core.phone import is_hk_phone
from app.core.rate_limit import RateLimiter
from app.models import AccountStatus, User, UserRole
from app.services.auth.identity_service import assert_email, normalize_email

logger = logging.getLogger("realtaxihk.account_auth")

# --- Policy constants, in one place so the numbers are reviewable and the tests
# can assert them rather than restating them. Same convention as
# `admin_auth_service`; deliberately the same values, so "the passenger lockout"
# and "the admin lockout" are not two different stories in an incident.
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15

# Registration is a *creation* event, so the IP budget is an order of magnitude
# tighter than login's — a legitimate person signs up once.
REGISTER_IP_LIMIT = 10
REGISTER_IP_WINDOW_S = 3600

# Per-IP and per-account login budgets defend different attacks: a spray against
# many addresses from one host, and a targeted guess at one account. Either
# alone leaves the other open, which is why they are separate counters.
LOGIN_IP_LIMIT = 30
LOGIN_IP_WINDOW_S = 900
LOGIN_EMAIL_LIMIT = 10
LOGIN_EMAIL_WINDOW_S = 900


class AccountAuthError(BusinessRuleError):
    """A credential refusal. The message is deliberately generic to the caller."""


class AccountThrottled(AccountAuthError):
    """A rate limit refused this attempt — mapped to 429.

    A distinct *type* rather than a message convention, because the router has
    to choose a different HTTP status than an ordinary credential rejection.
    Deriving 429 from `"too many" in str(exc)` coupled a security-relevant
    status code to English wording: rewording a message, or adding one that
    happened to contain the substring, would silently move an attempt between
    "rejected" and "throttled".
    """


class AccountLocked(AccountThrottled):
    """The account is inside its lockout window, not merely rate-limited.

    Subclasses the throttle so a caller treating "throttled" as 429 keeps
    working, but the router maps it to **401** — see the module docstring.
    """


@dataclass(frozen=True)
class AuthOutcome:
    """What `register`/`login` decided, so the router does not re-derive it."""

    user: User
    created: bool


def normalize_login_email(raw: str) -> str:
    """Lower-case and strip, for **lookup only**.

    `normalize_email` (the storage rule) lower-cases only the domain, on the
    RFC 5321 §2.3.11 argument that the local part is case-sensitive. That is the
    right rule for *delivery* and the wrong one for *sign-in*: a person who
    registered `Dan.Nison@example.com` and types `dan.nison@example.com` expects
    to get in, and every consumer mail service treats the two as one mailbox.

    So the stored value keeps its case and the comparison is case-folded on both
    sides — `find_by_email` compares `lower(users.email)` to this. Doing it that
    way round means a row written before this module existed, with any casing,
    still signs in.
    """
    return (raw or "").strip().lower()


def reviewer_expired(user: User, now: dt.datetime | None = None) -> bool:
    """True when this is a reviewer account whose expiry has passed.

    `reviewer_expires_at IS NOT NULL` *is* the reviewer marker (see the column
    comment in `app/models/user.py`), so this one predicate answers both "is
    this a reviewer account" and "has it lapsed" — they cannot disagree.

    Naive datetimes are normalised to UTC before comparing. Postgres returns
    aware values, but SQLite and some drivers do not, and comparing the two
    raises `TypeError` deep inside a guard rather than at the boundary.
    """
    expires_at = user.reviewer_expires_at
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=dt.UTC)
    return expires_at <= (now or dt.datetime.now(dt.UTC))


class AccountAuthService:
    """Stateless apart from the session and Redis handles it is given."""

    def __init__(self, session: AsyncSession, redis, *, namespace: str | None = None) -> None:
        self.session = session
        self.redis = redis
        # Defaults to the configured namespace rather than a hard-coded
        # `realtaxi:`, so a test process scopes its own counters (and only
        # them) — see `Settings.redis_key_namespace`.
        self.limiter = RateLimiter(redis, namespace=namespace or get_settings().redis_key_namespace)

    # ------------------------------------------------------------------ #
    # Lookup and lockout
    # ------------------------------------------------------------------ #

    async def find_by_email(self, email: str) -> User | None:
        """Case-insensitive lookup. Returns the first match.

        `func.lower(...)` rather than `==`: see `normalize_login_email`. The
        unique index on `email` is not usable for this comparison, so it is a
        sequential scan — accepted deliberately. The dominant cost of a login
        attempt is argon2 at ~50 ms, which a scan of a table this size does not
        approach; if `users` ever grows to where it does, the answer is a
        functional index on `lower(email)` and not a change here.
        """
        normalized = normalize_login_email(email)
        if not normalized:
            return None
        result = await self.session.execute(
            select(User).where(func.lower(User.email) == normalized)
        )
        return result.scalars().first()

    @staticmethod
    def is_locked(user: User, now: dt.datetime | None = None) -> bool:
        locked_until = user.locked_until
        if locked_until is None:
            return False
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=dt.UTC)
        return locked_until > (now or dt.datetime.now(dt.UTC))

    async def register_failure(self, user: User) -> None:
        """Increment the counter and lock at the threshold.

        Written to the row rather than a Redis counter so the lock survives a
        Redis flush — losing Redis must not reset a brute-force lockout.

        Public, and named for the *signal* rather than the route, because
        `PasswordService` is a second caller: a wrong current password on
        change-password is the same evidence as a wrong password on login, and
        giving it its own counter would let someone holding a stolen session
        brute-force the current password without ever tripping the login lock.
        """
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=LOCKOUT_MINUTES)
            logger.warning("account locked user_id=%s until=%s", user.id, user.locked_until)
        await self.session.flush()

    async def clear_failures(self, user: User) -> None:
        """Clear the lockout after a *proven* credential.

        Called on login, and on a successful change/reset — without it, a user
        who resets a forgotten password while locked out stays locked out, which
        reads as "the reset did not work" and is the kind of bug that generates
        a support ticket rather than a fix.
        """
        if user.failed_login_count or user.locked_until:
            user.failed_login_count = 0
            user.locked_until = None
            await self.session.flush()

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    async def register(
        self,
        *,
        email: str,
        password: str,
        phone_e164: str,
        ip: str | None = None,
    ) -> AuthOutcome:
        """Create an account. The phone is recorded as a claim, not as proof.

        `phone_verified_at` stays NULL and `account_status` stays UNVERIFIED, so
        the new account can sign in and look around but cannot start a booking
        until it proves a number through `PhoneBindingService`. That split is
        the entire point of this module: "prove you own a phone" is a
        precondition for *calling a taxi*, not for *having an account*.
        """
        if not await self.limiter.allow(
            f"account:register:ip:{ip}", REGISTER_IP_LIMIT, REGISTER_IP_WINDOW_S
        ):
            raise AccountThrottled("too many sign-ups from this address — try again later")

        address = normalize_email(email)
        # Raises `BusinessRuleError` with a sentence rather than a regex.
        assert_email(address)

        if not is_hk_phone(phone_e164):
            raise BusinessRuleError("phone must be a Hong Kong number in E.164 form (+852XXXXXXXX)")

        # Hashing first does two jobs: it enforces the password policy before a
        # query is spent, and `PasswordPolicyError` is a `ValueError`, which the
        # router converts into a 400 with the actual reason ("must be at least
        # 12 characters") instead of a generic conflict.
        password_hash = hash_password(password)

        taken = (
            await self.session.execute(
                select(User.id).where(func.lower(User.email) == address.lower())
            )
        ).scalar_one_or_none()
        if taken is not None:
            # Unavoidable: registration cannot proceed without saying this. The
            # account-existence disclosure is accepted here and nowhere else —
            # login keeps its single generic message.
            raise BusinessRuleError("that email address is already registered")

        user = User(
            email=address,
            password_hash=password_hash,
            phone_e164=phone_e164,
            role=UserRole.PASSENGER,
            account_status=AccountStatus.UNVERIFIED,
        )
        self.session.add(user)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # The check above races; `uq_users_email` does not. Reached when two
            # requests register the same address at once — the loser gets a
            # sentence rather than a 500.
            #
            # Rolled back *here*, not left to the request-scoped rollback. The
            # router commits on a `BusinessRuleError` (so that a failed-login
            # counter survives the refusal), and committing a session holding a
            # failed statement raises `PendingRollbackError` — turning a clear
            # "that email is already registered" into a 500. Rolling back first
            # makes the router's commit a harmless no-op. Nothing else is
            # pending at this point: the row that failed is the only work.
            await self.session.rollback()
            raise BusinessRuleError("that email address is already registered") from exc

        logger.info("account registered user_id=%s", user.id)
        return AuthOutcome(user=user, created=True)

    # ------------------------------------------------------------------ #
    # Sign-in
    # ------------------------------------------------------------------ #

    async def login(
        self,
        *,
        email: str,
        password: str,
        ip: str | None = None,
    ) -> AuthOutcome:
        """Verify an email + password.

        Every early return does as much work as the happy path as it can.
        `burn_password_time()` runs when the account does not exist *and* when it
        exists without a password (a grandfathered phone-only row), so those
        three cases are indistinguishable by response time — the argon2 verify
        dominates, and skipping it is a measurable account oracle.
        """
        email_key = normalize_login_email(email)

        # Rate limit BEFORE touching the database: the point is to not do work.
        if not await self.limiter.allow(
            f"account:login:ip:{ip}", LOGIN_IP_LIMIT, LOGIN_IP_WINDOW_S
        ):
            raise AccountThrottled("too many attempts — try again later")
        if not await self.limiter.allow(
            f"account:login:email:{email_key}", LOGIN_EMAIL_LIMIT, LOGIN_EMAIL_WINDOW_S
        ):
            raise AccountThrottled("too many attempts — try again later")

        user = await self.find_by_email(email_key)
        if user is None:
            burn_password_time()
            raise AccountAuthError("invalid credentials")

        if self.is_locked(user):
            raise AccountLocked("account temporarily locked")

        if user.password_hash is None or not verify_password(user.password_hash, password):
            # One branch for both, so "no password set" costs the same as "wrong
            # password" — and so the failure counter advances in both cases.
            if user.password_hash is None:
                burn_password_time()
            await self.register_failure(user)
            raise AccountAuthError("invalid credentials")

        if not user.is_active:
            raise AccountAuthError("account disabled")

        if reviewer_expired(user):
            # A store reviewer's credential is time-boxed. Refusing here rather
            # than only in `require_active_user` means the client gets a clear
            # reason at the door instead of a token that fails on first use.
            raise AccountAuthError("this reviewer account has expired")

        # Password is correct: clear the counter so a legitimate login wipes a
        # partial brute force.
        await self.clear_failures(user)

        # Transparent hash upgrade: the verify succeeded, so re-hash with the
        # current parameters. This is how the cost is raised without a reset.
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
            await self.session.flush()
            logger.info("rehashed account password to current params user_id=%s", user.id)

        if user.reviewer_expires_at is not None:
            # The audit trail for a reviewer account: one line per **sign-in**,
            # not one per request. A reviewer browsing the app generates hundreds
            # of requests, and a log nobody can read is not a record. The account
            # is identifiable everywhere by `reviewer_expires_at IS NOT NULL`, so
            # this line is the join key for "who used it, and when".
            logger.warning(
                "reviewer account signed in user_id=%s expires_at=%s",
                user.id,
                user.reviewer_expires_at,
            )

        return AuthOutcome(user=user, created=False)
