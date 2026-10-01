"""Admin account lifecycle: create, role change, password reset.

Why this is a service and not four handlers
-------------------------------------------
Every rule in here is a rule about the *set* of admin accounts, not about the
one being edited. "Is this the last SUPER_ADMIN?" cannot be answered from the
row in front of you. Putting that logic in an HTTP handler means the next
caller — a CLI, a one-off script, a test fixture — reimplements it, and the
version that gets reimplemented is the one without the last-admin check.

The four constraints on role change
-----------------------------------
`PATCH /admin/accounts/{id}/role` looks like a field update and is not. A role
is the thing that decides who may grant roles, so the endpoint can be used to
take over the system, and it can also be used to accidentally brick it:

1. **Only SUPER_ADMIN may call it.** If OPERATIONS could, an OPERATIONS
   account promotes itself and the hierarchy is decoration. This is the only
   hole RBAC has, and it has to be closed structurally: it is enforced by the
   route's dependency *and* re-checked here, because a caller that reaches the
   service directly (a script, a future background job) never sees a route
   dependency.
2. **Nobody changes their own role.** Not to a higher one — that is the
   self-promotion path — and not to a lower one either. A demoted
   SUPER_ADMIN who is the last one has locked the system, and with only one
   SUPER_ADMIN the "am I the last one?" check is consulted *after* they have
   already changed themselves. Refusing both directions removes the ordering
   problem rather than solving it.
3. **The last SUPER_ADMIN cannot be lowered.** Otherwise the system reaches a
   state with no account able to grant roles, and the only repair is editing
   the database by hand — which is how a security control turns into an
   operational incident and gets quietly abandoned.
4. **Every change is audited with from/to.** "Who made me a SUPER_ADMIN" is
   the first question anyone asks, and it is unanswerable from the row itself
   because the row only holds the current value.

These are four faces of one constraint: the power to administer authority
must not be emptied, by accident or by intent.

Why the checks live in the service *and* the route
--------------------------------------------------
The route dependency is the primary control; the service check is defence in
depth for non-HTTP callers. Both are cheap. The `is_self` check in particular
must be in the service, because "the caller" is not something a route
dependency can express — it needs the identity of the actor, and the actor is
an argument here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.passwords import PasswordPolicyError, hash_password
from app.models import AdminAccount, AdminRole

__all__ = ["AdminAccountService", "normalize_email", "normalize_username"]

MAX_USERNAME = 32
MAX_EMAIL = 254
MAX_FULL_NAME = 120


def normalize_username(username: str) -> str:
    """Lower-case and trim. Matches how the login path looks accounts up.

    Normalising on write is what makes the unique index case-insensitive
    without CITEXT or a functional index — see `AdminAccount.username`. If
    this and the login path ever disagree, you get an account nobody can
    log into, so both go through the same function.
    """
    value = (username or "").strip().lower()
    if not value:
        raise BusinessRuleError("username is required")
    if len(value) > MAX_USERNAME:
        raise BusinessRuleError(f"username must be at most {MAX_USERNAME} characters")
    if not value.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise BusinessRuleError("username may contain only letters, digits, dot, dash, underscore")
    return value


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not value or "@" not in value:
        raise BusinessRuleError("a valid email is required")
    if len(value) > MAX_EMAIL:
        raise BusinessRuleError(f"email must be at most {MAX_EMAIL} characters")
    return value


class AdminAccountService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_accounts(self) -> list[AdminAccount]:
        """Newest seniority first, then oldest account — the order an
        operator scans when looking for "who can do what"."""
        result = await self.session.execute(select(AdminAccount).order_by(AdminAccount.username))
        return list(result.scalars().all())

    async def get(self, account_id: uuid.UUID) -> AdminAccount:
        row = await self.session.get(AdminAccount, account_id)
        if row is None:
            raise NotFoundError("admin account not found")
        return row

    async def count_super_admins(self, *, excluding: uuid.UUID | None = None) -> int:
        """How many SUPER_ADMIN accounts are active.

        Counts only *active* accounts on purpose. A deactivated SUPER_ADMIN is
        not a way to grant roles, so counting them would let the last usable
        one be demoted while a disabled name kept the check happy.
        """
        stmt = select(func.count()).select_from(AdminAccount).where(
            AdminAccount.role == AdminRole.SUPER_ADMIN.value,
            AdminAccount.is_active.is_(True),
        )
        if excluding is not None:
            stmt = stmt.where(AdminAccount.id != excluding)
        return int((await self.session.execute(stmt)).scalar_one())

    async def create(
        self,
        *,
        username: str,
        email: str,
        password: str,
        full_name: str | None,
        role: AdminRole,
        created_by: uuid.UUID,
    ) -> AdminAccount:
        """Create an account. The new admin still has to enrol TOTP to log in.

        `role` is required and has no default: a caller that forgets it should
        get a TypeError, not an account. The *column* defaults to SUPPORT so
        that a row created outside this service is least-privilege; this
        parameter exists so that the choice is explicit where it is made.
        """
        uname = normalize_username(username)
        mail = normalize_email(email)
        if full_name is not None:
            full_name = full_name.strip() or None
            if full_name and len(full_name) > MAX_FULL_NAME:
                raise BusinessRuleError(f"full name must be at most {MAX_FULL_NAME} characters")

        try:
            password_hash = hash_password(password)
        except PasswordPolicyError as exc:
            # Surfaced as a business rule so the API returns 400 with the
            # policy sentence, rather than a 500 from an unhandled ValueError.
            raise BusinessRuleError(str(exc)) from exc

        account = AdminAccount(
            username=uname,
            email=mail,
            full_name=full_name,
            password_hash=password_hash,
            role=role.value,
            created_by=created_by,
            # Enrolment is not done yet, so no secret exists. Login will
            # branch into the enrolment flow — see `_begin_enrolment`.
            totp_secret=None,
            is_active=True,
            failed_login_count=0,
        )
        self.session.add(account)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # The unique index is the real arbiter: a check-then-insert races
            # two concurrent creates into both seeing "free" and one raising.
            await self.session.rollback()
            raise BusinessRuleError("username or email is already taken") from exc
        return account

    async def change_role(
        self,
        *,
        account_id: uuid.UUID,
        new_role: AdminRole,
        actor_id: uuid.UUID,
    ) -> tuple[AdminAccount, AdminRole]:
        """Set an account's role, enforcing all four constraints.

        Returns the account and its *previous* role, so the caller can audit
        from/to without re-reading a row it is about to overwrite.
        """
        if account_id == actor_id:
            raise BusinessRuleError("an admin may not change their own role")

        account = await self.get(account_id)
        previous = account.admin_role

        if previous is new_role:
            # Not an error worth a 4xx in normal use, but it is worth not
            # writing an audit row that says nothing happened.
            raise BusinessRuleError("account already has that role")

        if previous is AdminRole.SUPER_ADMIN and new_role is not AdminRole.SUPER_ADMIN:
            remaining = await self.count_super_admins(excluding=account_id)
            if remaining == 0:
                raise BusinessRuleError(
                    "cannot demote the last active SUPER_ADMIN",
                    {"reason": "LAST_SUPER_ADMIN"},
                )

        account.role = new_role.value
        await self.session.flush()
        return account, previous

    async def reset_password(self, *, account_id: uuid.UUID, new_password: str) -> AdminAccount:
        """Set a new password and revoke everything that was already issued.

        Clearing `failed_login_count` / `locked_until` is part of the operation,
        not a courtesy: a reset is what you do for someone who is locked out,
        and leaving the lock in place means the reset did not work.

        TOTP is deliberately untouched. A lost password and a lost authenticator
        are different incidents, and clearing the second factor on a password
        reset turns one compromised credential into full account takeover.
        """
        account = await self.get(account_id)
        try:
            account.password_hash = hash_password(new_password)
        except PasswordPolicyError as exc:
            raise BusinessRuleError(str(exc)) from exc
        account.failed_login_count = 0
        account.locked_until = None
        await self.session.flush()
        return account
