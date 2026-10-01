"""Administrator console identity: separate accounts, TOTP, sessions, audit.

Split out of the original single `app/models/__init__.py`; every public name is
re-exported from `app.models`, so no call site changed.

Bounded context: **privileged identities**, kept deliberately apart from
`User` (`app.models.user`). An admin is not a user row with a flag — the two
have different credentials, different second factors, and different blast
radius. Keeping them in separate tables is what makes "who can move money"
answerable by looking at one table. See `AdminAccount` for the full argument.

`EmailVerificationToken` also lives here: it belongs to `users`, but it is part
of the self-service credential-claiming story that started with admin-shaped
registration, and it is read on the same paths as the recovery codes.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models._base import Base

__all__ = [
    "AdminAccount",
    "AdminAuditLog",
    "AdminRecoveryCode",
    "AdminRefreshToken",
    "AdminRole",
    "EmailVerificationToken",
]


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class AdminRole(str, enum.Enum):
    """Who an administrator is allowed to be, ordered by blast radius.

    Four levels, not three, for two separations that matter:

    * `SUPPORT` and `OPERATIONS` are apart because customer support's job is
      to *record* a problem and operations' job is to *decide* one. Merged,
      front-line support inherits KYC approval — a compliance judgement about
      whether a driver may operate, which is not a first-line task.
    * `OPERATIONS` and `FINANCE` are apart because KYC must not be loosened by
      whoever benefits from more drivers being online, and money must not be
      moved by whoever approved the paperwork. Standard separation of duties.

    `SUPER_ADMIN` is the only role that may change a role. If `OPERATIONS` or
    `FINANCE` could, the hierarchy would be bypassable by self-promotion and
    every other boundary here would be decorative. That is the one hole RBAC
    cannot close by discipline, so it is closed by the dependency instead.

    **Rank comparison, not set membership.** Permission checks ask
    `role.rank >= AdminRole.FINANCE.rank`. A set would mean every new role
    requires re-reading every tuple that enumerates roles, and one missed
    tuple is an open route. Rank makes the default-deny direction automatic:
    a role added later at the bottom cannot reach anything above it.
    """

    SUPPORT = "SUPPORT"
    OPERATIONS = "OPERATIONS"
    FINANCE = "FINANCE"
    SUPER_ADMIN = "SUPER_ADMIN"

    @property
    def rank(self) -> int:
        """Position in the hierarchy; higher means more authority."""
        return _ADMIN_ROLE_RANK[self]

    def at_least(self, other: AdminRole) -> bool:
        return self.rank >= other.rank


# Deliberately a module-level dict rather than `IntEnum` or a class attribute:
# the ordering must be readable in one place, and `str, enum.Enum` is what the
# rest of the codebase already stores (SQLAlchemy maps it to a VARCHAR).
_ADMIN_ROLE_RANK: dict[AdminRole, int] = {
    AdminRole.SUPPORT: 0,
    AdminRole.OPERATIONS: 1,
    AdminRole.FINANCE: 2,
    AdminRole.SUPER_ADMIN: 3,
}

# The default for a newly created admin. Deliberately the *lowest* role and not
# `SUPER_ADMIN`: a new account must start with the least authority, and the
# safe direction is one somebody has to deliberately widen. The opposite
# default fails silently — nobody notices that the new hire can approve refunds.
DEFAULT_ADMIN_ROLE = AdminRole.SUPPORT


class AdminAccount(Base):
    """An administrator / management-team account — a **separate identity type**.

    Deliberately not a `users` row with extra columns, and not reachable through
    the phone-OTP login path. The reasons are load-bearing:

    1. **Different credential.** A user is identified by a unique *phone*; an
       admin by a unique *username* plus a unique *email*. Sharing one table
       means three uniquely-constrained columns that are all nullable and whose
       meaning depends on `role` — a shape where a bug produces an admin with no
       phone, or a passenger with an email that collides with an admin's.

    2. **Different second factor.** Admins require TOTP. There is no passenger
       equivalent, so `totp_secret` would sit NULL on ~all user rows.

    3. **Blast radius.** This account can move money (`ADJUSTMENT`, refund
       approval, fleet settlement). Keeping it in its own table means a bug in
       passenger registration cannot mint an admin, and `require_admin` has
       exactly one table to check.

    The Trade-off accepted: an admin cannot also be a passenger on the same
    account. That is intentional — it keeps "who can move money" unambiguous.
    """

    __tablename__ = "admin_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # Lower-cased on write by the service layer, so uniqueness is
    # case-insensitive without a functional index (which would need CITEXT or
    # a migration-specific expression index).
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))

    # Coarse role. Stored as VARCHAR rather than a Postgres enum for the same
    # reason the audit `event` column is a string: adding a role should not
    # require a migration on a table that every authenticated request reads.
    #
    # The stored value is *not* the authority. `require_role` reads this column
    # live on every request rather than trusting the `admin_role` claim in the
    # access token, so a demotion takes effect on the next request instead of
    # whenever a 15-minute token happens to expire. See `app/core/deps.py`.
    role: Mapped[str] = mapped_column(
        String(16), default=DEFAULT_ADMIN_ROLE.value, server_default="SUPPORT", index=True
    )

    # --- TOTP (RFC 6238). Enrolled on first login; see `AdminTotpService`.
    # NULL until enrolment completes, which is the state that makes the login
    # flow a state machine: password is proven, TOTP is not yet configured.
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Replay protection: the last accepted 30-second step. A code at or before
    # this counter is refused, making each code single-use. Without it the
    # ±1-step window leaves a code replayable for up to 90 seconds.
    totp_last_counter: Mapped[int | None] = mapped_column(BigInteger)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Brute-force lockout, self-clearing. Kept off `is_active` so an automated
    # lock never looks like an admin ban in the audit trail.
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Which admin created this one, for the audit trail.
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    recovery_codes: Mapped[list[AdminRecoveryCode]] = relationship(
        back_populates="admin", cascade="all, delete-orphan"
    )

    @property
    def admin_role(self) -> AdminRole:
        """The stored role as an enum, failing *closed* on an unknown value.

        A row carrying a value this build does not recognise — a downgrade, a
        hand-edited row, a role added by a newer deploy — must not be treated
        as whatever it happens to sort like. `SUPPORT` is the floor, so an
        unrecognised role collapses to the least authority rather than the
        most. Raising instead would turn a data problem into an outage for
        every request that touches this account.
        """
        try:
            return AdminRole(self.role)
        except ValueError:
            return DEFAULT_ADMIN_ROLE


class AdminRecoveryCode(Base):
    """Single-use codes for when the authenticator device is lost.

    Without these, a lost phone is a locked-out administrator and a hand-edited
    database row — which is how a security control becomes an operational
    incident, and why second-factor rollouts get quietly abandoned.

    Stored one-way (SHA-256; the code is 50 bits of CSPRNG output, so there is
    no dictionary to attack and no need for a slow KDF on the login path).
    `used_at` rather than deletion keeps the fact that a code *was* used, which
    is exactly what you want to see when investigating an account takeover.
    """

    __tablename__ = "admin_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    admin: Mapped[AdminAccount] = relationship(back_populates="recovery_codes")

    __table_args__ = (UniqueConstraint("admin_id", "code_hash", name="uq_admin_recovery_code"),)


class AdminRefreshToken(Base):
    """Rotating refresh tokens for **admin** sessions — the console's session life.

    Why a second table rather than a second column on `refresh_tokens`
    -----------------------------------------------------------------
    `RefreshToken.user_id` is a `ForeignKey("users.id")`. An admin id lives in a
    different UUID space, so storing one there would mean either a nullable FK
    that is correct only for one of its two meanings, or dropping the FK — and
    with it the `ondelete="RESTRICT"` guarantee. Neither is worth it to save a
    table. The two flows also differ in kind: a user refresh token is issued by
    the OTP path and consumed by `/auth/refresh`, an admin one is issued only
    after a TOTP code and is delivered inside an `HttpOnly` cookie rather than a
    response body.

    Why this exists at all
    ----------------------
    An admin access token is deliberately short-lived (15 minutes, SEC-18) so a
    logout is real and a stolen token expires. Without a refresh path that
    shortness is not a security win, it is an outage: every console session died
    at the 15-minute mark and discarded whatever the operator had half-typed.
    This table is what lets the session outlive the access token without
    lengthening the access token.

    Single-use rotation with replay detection, exactly as `RefreshToken`:
    `rotate_admin` revokes the presented row and issues a new one, and a
    *revoked* row presented again revokes the whole set for that admin. That is
    what makes a stolen cookie detectable rather than silently shareable.

    The raw token is never stored — only its SHA-256 digest. SHA-256 and not
    argon2 because the value is 48 bytes of CSPRNG output: there is no
    dictionary to attack, and a memory-hard KDF on the refresh path would make
    the endpoint a resource-exhaustion lever.
    """

    __tablename__ = "admin_refresh_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # sha256
    # The CSRF token bound to this refresh token. Stored *alongside* rather than
    # derived from it, so the pair is a two-part credential: the cookie is sent
    # automatically by the browser (which is why it must be HttpOnly and
    # SameSite), and the header value must be read by a script that shares this
    # origin. An attacker who can make the browser send the cookie cannot read
    # the value to put in the header, which is the whole of the CSRF defence.
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdminAuditLog(Base):
    """Append-only record of privileged actions and authentication events.

    A separate table from the driver/staff audit needs because the questions are
    different: this one answers "who logged in, from where, and did they move
    money", which is the first thing asked after an incident and the one thing
    the application logs (JSON to stdout) are worst at — logs rotate, this does
    not.

    There is no UPDATE or DELETE path in the application. `created_at` is
    indexed because every real query is "since when".
    """

    __tablename__ = "admin_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # Nullable: a failed login for a username that does not exist still has to
    # be recorded, or credential stuffing is invisible.
    admin_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    # Denormalised on purpose: the row must stay readable after the account is
    # deleted, and it is what makes a failed-login row attributable.
    username_attempted: Mapped[str | None] = mapped_column(String(64))
    event: Mapped[str] = mapped_column(String(48), index=True)
    outcome: Mapped[str] = mapped_column(String(16))  # SUCCESS / FAILURE
    detail: Mapped[str | None] = mapped_column(String(255))
    # Structured before/after values for events whose interesting content does
    # not fit a 255-char sentence — "adjust HK$-1,200" is legible in `detail`,
    # but "which fields changed and to what" is not. Nullable and additive:
    # read it as `{"before": {...}, "after": {...}}` when present.
    payload: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(45))  # 45 = IPv6 max
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class EmailVerificationToken(Base):
    """A single-use link token proving ownership of an email address (P-2).

    Stored as a SHA-256 digest, never the raw token: the raw value is a bearer
    credential that travels through email, so a leaked table (a backup, a
    replica, a log of a bad query) must not yield working links.

    SHA-256 rather than argon2, unlike passwords: the token is 256 bits of
    CSPRNG output, so there is no dictionary and no need for a memory-hard KDF —
    while argon2 at 64 MiB per click would turn the verify endpoint into a
    resource-exhaustion lever.

    `email` is denormalised whatever the user row says at issue time. The address
    can change between issue and click, and the click must prove the address the
    link was SENT to, not the one currently on the profile.
    """

    __tablename__ = "email_verification_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(254))
    # Unique so a digest collision (or a replayed insert) cannot create a second
    # live token for the same secret.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Stamped rather than deleted: "this link was used at T, from this IP" is the
    # audit trail for an account takeover. Same reasoning as the recovery codes.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
