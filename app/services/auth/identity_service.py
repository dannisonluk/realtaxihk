"""Registration identity: email verification, and the Uber-shaped profile.

What "registration" means here
------------------------------
An account is created by `AccountAuthService.register` — email, password, and a
*claimed* phone number — and not by this module. What is left here is everything
that happens **after** the account exists: choosing a username, proving the
email address, proving the phone.

That ordering is the reverse of what it was. An account used to be born the
moment a phone number proved itself with an OTP, which is why this module had no
`register()` — the phone *was* step one. The cost of that shape was that proving
a phone became a precondition for merely having an account, and that the number
was simultaneously the identifier and the only secret, so losing it lost the
account with no recovery path. The phone is now a *capability*
(`phone_binding_service`), and the email proof here is about recovery and
notification rather than about being allowed in at all.

Where the gates live now
------------------------
`account_status` is still `UNVERIFIED` until a username, an email and a phone
are all proven — but it is now a **profile-completeness flag, not an
authorisation gate**. The gate that decides whether you may call a taxi is
`require_phone_verified` in `app/core/deps.py`, which asks only whether a number
has been proven, because that is the one thing the booking flow actually needs.
`_promote_if_ready` below stays the single definition of "complete", so the flag
cannot drift from the profile it describes.

Email is proven by a **link**, not a code
-----------------------------------------
A code would have to be typed back into an app session that may not exist yet
(the user could be on a laptop). A link works everywhere, and the token can
carry the intent. The token is stored as a SHA-256 digest, single-use, and
expiring, so a leaked database row is not a working link.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError
from app.models import AccountStatus, EmailVerificationToken, Gender, User
from app.services.infra.notify import get_email_provider

logger = logging.getLogger("realtaxihk.identity")

# Local part per RFC 5321 allows a lot; this is deliberately stricter because
# the value is echoed in a URL and stored, and nothing legitimate needs the
# exotic cases. Quoted local parts and comments are rejected on purpose.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,189}\.[A-Za-z]{2,}$")
# Same shape as the admin rule, for the same reason: lowercase, no spaces, so
# uniqueness is case-insensitive without a functional index.
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
# A URI scheme prefix: `javascript:`, `data:`, `http:`, `file:` … Rejecting
# anything that looks like one keeps the value a plain object key.
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

MAX_USERNAME_ATTEMPTS = 5
_TOKEN_BYTES = 32  # 256 bits — this is a bearer credential in an email


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def normalize_username(raw: str) -> str:
    """Lowercase and strip. No NFKC folding beyond that — see `_assert_username`."""
    return (raw or "").strip().lower()


def normalize_email(raw: str) -> str:
    """Lowercase the domain only, never the local part.

    RFC 5321 §2.3.11 says the local part is case-SENSITIVE and only the domain is
    case-insensitive. Lowercasing the whole address (the usual shortcut) merges
    `A@x.com` and `a@x.com`, which are technically different mailboxes — and more
    practically, it means the address the user typed is not the one we deliver to.
    """
    value = (raw or "").strip()
    local, _, domain = value.partition("@")
    if not domain:
        return value.lower()
    return f"{local}@{domain.lower()}"


def assert_email(address: str) -> None:
    """Reject anything that is not a plausible, deliverable address.

    Module-level and public rather than an `IdentityService` staticmethod, which
    is what it used to be. Registration (`account_service`) now validates the
    same address through the same rule, and a caller that has to reach for a
    private name to reuse a rule eventually stops reusing it — at which point
    there are two email regexes, and relaxing one of them is a hole.
    """
    if not _EMAIL_RE.fullmatch(address or ""):
        raise BusinessRuleError("that does not look like an email address")
    if len(address) > 254:
        raise BusinessRuleError("email address is too long")


def _hash_token(raw: str) -> str:
    """Plain SHA-256, not argon2.

    The token is 256 bits of CSPRNG output, so there is no dictionary to attack
    and no benefit to a memory-hard KDF — while argon2 at 64 MiB would run on
    every click of a verification link, which is a trivial way to make the
    endpoint a resource-exhaustion lever.
    """
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class ProfileOut:
    user_id: uuid.UUID
    username: str


def promote_if_ready(user: User) -> None:
    """`UNVERIFIED` -> `ACTIVE` once the profile is complete.

    The **single** definition of "ready", and it is module-level so that
    everything which can complete the last piece calls the same one:
    `confirm_email` and `complete_profile` here, and `PhoneBindingService.confirm`
    (proving a phone can be what finishes an account). It was a private method,
    which meant the third caller would have had to import a private name — and
    the version of this that gets written twice is the version that drifts.

    Note what this is *not*: an authorisation gate. `account_status` describes
    how complete a profile is; it does not decide what the account may do. The
    gate that decides whether a booking may start is `require_phone_verified`
    in `app/core/deps.py`, and keeping the two questions apart is deliberate —
    conflating them is what made "prove a phone" a precondition for signing in.
    """
    ready = (
        user.phone_verified_at is not None
        and user.email_verified_at is not None
        and user.username is not None
    )
    if ready and user.account_status == AccountStatus.UNVERIFIED:
        user.account_status = AccountStatus.ACTIVE
        logger.info("account activated user_id=%s", user.id)


class IdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    # Email verification
    # ------------------------------------------------------------------ #

    async def request_email_verification(self, user: User, email: str) -> dict:
        """Issue (or re-issue) a verification link for `email`.

        Returns the TTL, never the token. In dev the link is logged by
        `DevEmailProvider`; in prod it only exists in the message.
        """
        address = normalize_email(email)
        assert_email(address)

        # Uniqueness is checked here as well as by the index, so the caller gets
        # a sentence instead of an IntegrityError. The index is still the
        # authority — this check races, the index does not.
        taken = (
            await self.session.execute(
                select(User.id).where(User.email == address, User.id != user.id)
            )
        ).scalar_one_or_none()
        if taken is not None:
            raise BusinessRuleError("that email address is already in use")

        settings = get_settings()
        raw = secrets.token_urlsafe(_TOKEN_BYTES)
        token = EmailVerificationToken(
            user_id=user.id,
            email=address,
            token_hash=_hash_token(raw),
            expires_at=_now() + timedelta(hours=settings.email_verify_ttl_hours),
        )
        self.session.add(token)

        # The pending address lives on the token row, not on `users.email`: the
        # account column is only written after the link proves ownership. Writing
        # it here would let any authenticated account permanently squat an
        # address through the unique index before proving it can read that mail.
        await self.session.flush()

        # Harden the transaction before sending the link: the request-scoped
        # dependency commits only after the handler returns, so without this the
        # email can carry a token that never became durable. On send failure,
        # discard the just-created row so a retry issues a fresh token.
        link = f"{settings.public_base_url.rstrip('/')}/verify-email?token={raw}"
        await self.session.commit()
        try:
            await get_email_provider().send_verification_email(address, link)
        except Exception:
            await self.session.delete(token)
            await self.session.commit()
            raise

        return {
            "sent": True,
            "expires_in_hours": settings.email_verify_ttl_hours,
            "email": _mask_email(address),
        }

    async def confirm_email(self, raw_token: str) -> User:
        """Consume a verification token and mark the address proven."""
        if not raw_token:
            raise BusinessRuleError("verification token is required")

        row = (
            await self.session.execute(
                select(EmailVerificationToken).where(
                    EmailVerificationToken.token_hash == _hash_token(raw_token)
                )
            )
        ).scalar_one_or_none()

        # One message for every failure mode — unknown, expired, already used.
        # Distinguishing them tells an attacker who holds a stolen token whether
        # it was ever real, and tells nobody else anything useful.
        invalid = BusinessRuleError("this verification link is invalid or has expired")
        if row is None or row.consumed_at is not None:
            raise invalid
        expires_at = _as_aware(row.expires_at)
        # A NULL expiry cannot be trusted, so it is treated as expired -- the
        # same fail-closed direction as every other branch in this method.
        if expires_at is None or _now() >= expires_at:
            raise invalid

        user = await self.session.get(User, row.user_id)
        if user is None:
            raise invalid

        row.consumed_at = _now()
        # Re-check against the LIVE row, not the token: the address may have been
        # changed (or claimed by someone else) between issue and click.
        still_free = (
            await self.session.execute(
                select(User.id).where(User.email == row.email, User.id != user.id)
            )
        ).scalar_one_or_none()
        if still_free is not None:
            raise BusinessRuleError(
                "that email address was claimed by another account in the meantime"
            )

        user.email = row.email
        user.email_verified_at = _now()
        self._promote_if_ready(user)
        await self.session.flush()

        logger.info("email verified user_id=%s", user.id)
        return user

    # ------------------------------------------------------------------ #
    # Profile
    # ------------------------------------------------------------------ #

    async def complete_profile(
        self,
        user: User,
        *,
        username: str,
        given_name: str,
        family_name: str,
        gender: Gender | str | None = None,
        avatar_key: str | None = None,
    ) -> ProfileOut:
        handle = normalize_username(username)
        self._assert_username(handle)

        conflict = (
            await self.session.execute(
                select(User.id).where(User.username == handle, User.id != user.id)
            )
        ).scalar_one_or_none()
        if conflict is not None:
            raise BusinessRuleError("that username is taken")

        # Names are normalised to NFC for the same reason passwords are: an
        # accented name composed two ways must not render as mojibake, and must
        # not compare unequal against itself.
        user.username = handle
        user.given_name = _clean_name(given_name, "given name")
        user.family_name = _clean_name(family_name, "family name")
        if gender is not None:
            user.gender = _coerce_gender(gender)
        if avatar_key is not None:
            user.avatar_key = _assert_avatar_key(avatar_key)

        # `display_name` predates this schema and several read paths still use
        # it, so keep it in step rather than leaving two names that disagree.
        user.display_name = f"{user.given_name} {user.family_name}".strip()[:80]

        self._promote_if_ready(user)
        await self.session.flush()
        return ProfileOut(user_id=user.id, username=handle)

    async def username_available(self, raw: str) -> bool:
        handle = normalize_username(raw)
        try:
            self._assert_username(handle)
        except BusinessRuleError:
            return False
        found = (
            await self.session.execute(select(User.id).where(User.username == handle))
        ).scalar_one_or_none()
        return found is None

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _promote_if_ready(self, user: User) -> None:
        """Delegates to the module-level `promote_if_ready`.

        The logic moved out of the class because `PhoneBindingService` needs the
        same definition, and reaching into a private method from another module
        is how a second copy of a rule gets written. The method stays because
        both call sites here are inside this class and read better as
        `self._promote_if_ready(user)`.
        """
        promote_if_ready(user)

    @staticmethod
    def _assert_username(handle: str) -> None:
        if not _USERNAME_RE.fullmatch(handle or ""):
            raise BusinessRuleError(
                "username must be 3-32 characters, using lowercase letters, "
                "digits, dot, underscore or hyphen, and start with a letter or digit"
            )
        # Reserved because they make a naive router or log line ambiguous — a
        # username that contains a path separator would otherwise let a profile
        # URL point somewhere else.
        if handle in {"admin", "root", "support", "system", "realtaxi", "me", "null", "undefined"}:
            raise BusinessRuleError("that username is reserved")


def _clean_name(value: str, label: str) -> str:
    cleaned = unicodedata.normalize("NFC", (value or "").strip())
    if not cleaned:
        raise BusinessRuleError(f"{label} is required")
    if len(cleaned) > 60:
        raise BusinessRuleError(f"{label} is too long")
    # Control characters would let a name break a log line or a CSV export.
    # `unicodedata.category(ch) == "Cc"` is the actual test — `str` has no
    # `iscontrol`, and `str.isprintable()` is too broad (it rejects the
    # zero-width joiner and RTL marks that legitimate names use).
    if any(unicodedata.category(ch) == "Cc" for ch in cleaned):
        raise BusinessRuleError(f"{label} contains invalid characters")
    return cleaned


def _coerce_gender(value: Gender | str) -> Gender:
    if isinstance(value, Gender):
        return value
    try:
        return Gender(str(value).strip().upper())
    except ValueError as exc:
        allowed = ", ".join(g.value for g in Gender)
        raise BusinessRuleError(f"gender must be one of: {allowed}") from exc


def _assert_avatar_key(key: str) -> str:
    """An object key, never a URL.

    Two attacks this closes: a `javascript:` or `data:` value rendered into an
    `<img src>` (stored XSS), and an absolute URL pointing at another host.

    The scheme test is against the *prefix before the first slash*, not `"://" in
    value`: `javascript:alert(1)` and `data:text/html,...` contain no `//` at all,
    so a naive substring check passes them straight through. A key has no scheme
    by construction, so anything matching `^[a-z][a-z0-9+.-]*:` is rejected.
    """
    value = (key or "").strip()
    if not value:
        raise BusinessRuleError("avatar key must not be empty")
    if _SCHEME_RE.match(value):
        raise BusinessRuleError("avatar key must be an object key, not a URL")
    if value.startswith("/") or value.startswith("\\"):
        raise BusinessRuleError("avatar key must be a relative path")
    if ".." in value.split("/"):
        raise BusinessRuleError("avatar key must not contain '..'")
    if any(unicodedata.category(ch) == "Cc" for ch in value):
        raise BusinessRuleError("avatar key contains invalid characters")
    if len(value) > 255:
        raise BusinessRuleError("avatar key is too long")
    return value


def _mask_email(address: str) -> str:
    local, _, domain = (address or "").partition("@")
    return f"{local[:1]}*******@{domain}" if domain else "***"
