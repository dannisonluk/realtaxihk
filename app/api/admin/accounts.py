"""Admin account management. `/accounts*`. SUPER_ADMIN only, all four routes.

These are the routes that decide who may do everything else, so they are the one
place where the RBAC hierarchy has to hold without help: `require_role` is applied
at the route *and* the constraints are re-checked in `AdminAccountService`,
because an OPERATIONS account promoting itself is the single move that makes every
other guard meaningless."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin._roles import _require_super
from app.api.admin._shared import _actor_username
from app.api.schemas import (
    AdminAccountCreatedOut,
    AdminAccountPageOut,
    AdminActiveChangeOut,
    AdminPasswordResetOut,
    AdminRoleChangeOut,
)
from app.core.db import get_session
from app.core.deps import Principal
from app.core.token_revocation import revoke_user_tokens
from app.models import AdminAccount, AdminRole
from app.services.admin.admin_account_service import AdminAccountService
from app.services.admin.admin_refresh_service import AdminRefreshService
from app.services.admin.audit_service import (
    EV_ADMIN_ACCOUNT_ACTIVE_CHANGE,
    EV_ADMIN_ACCOUNT_CREATE,
    EV_ADMIN_PASSWORD_RESET,
    EV_ADMIN_ROLE_CHANGE,
    record_audit,
)

router = APIRouter()


class AdminAccountCreateIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    email: str = Field(min_length=3, max_length=254)
    # Password *policy* is enforced in `hash_password` (length, whitespace,
    # predictability); the bound here only stops an unbounded body.
    password: str = Field(min_length=1, max_length=256)
    full_name: str | None = Field(default=None, max_length=120)
    admin_role: AdminRole = AdminRole.SUPPORT


class AdminRoleChangeIn(BaseModel):
    admin_role: AdminRole


class AdminPasswordResetIn(BaseModel):
    new_password: str = Field(min_length=1, max_length=256)


class AdminActiveChangeIn(BaseModel):
    is_active: bool


def _account_out(row: AdminAccount) -> dict:
    return {
        "id": str(row.id),
        "username": row.username,
        "email": row.email,
        "full_name": row.full_name,
        "admin_role": row.admin_role.value,
        "is_active": row.is_active,
        # Derived, never the secret: the secret does not leave the server and
        # enrolment is the only fact a client acts on.
        "totp_enrolled": row.totp_secret is not None,
        "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


@router.get("/accounts", response_model=AdminAccountPageOut)
async def list_admin_accounts(
    admin: Principal = Depends(_require_super),
    session: AsyncSession = Depends(get_session),
):
    """The admin roster, with the count the demote button depends on.

    SUPER_ADMIN only, unlike `/audit` which is readable by all. The difference
    is the data: an audit row is a decision, an account row is a credential
    holder's identity, and knowing who the super admins are is the
    reconnaissance step before an attack on one of them.
    """
    service = AdminAccountService(session)
    rows = await service.list_accounts()
    return {
        "items": [_account_out(r) for r in rows],
        "super_admin_count": await service.count_super_admins(),
        "total": len(rows),
    }


@router.post("/accounts", response_model=AdminAccountCreatedOut, status_code=201)
async def create_admin_account(
    payload: AdminAccountCreateIn,
    request: Request,
    admin: Principal = Depends(_require_super),
    session: AsyncSession = Depends(get_session),
):
    """Create an admin account. It must enrol TOTP before it can log in.

    A newly created account has no `totp_secret`, so the login flow routes it
    into enrolment — which is why the response says
    `totp_enrolment_pending: true` rather than leaving the caller to infer it
    from an absent field.
    """
    service = AdminAccountService(session)
    account = await service.create(
        username=payload.username,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        role=payload.admin_role,
        created_by=admin.id,
    )
    await record_audit(
        session,
        event=EV_ADMIN_ACCOUNT_CREATE,
        actor_id=admin.id,
        username=await _actor_username(session, admin),
        detail=f"created admin {account.username} as {account.role}",
        payload={
            "created_id": str(account.id),
            "created_username": account.username,
            "admin_role": account.role,
        },
        request=request,
    )
    await session.commit()
    return {**_account_out(account), "totp_enrolment_pending": True}


@router.patch("/accounts/{account_id}/role", response_model=AdminRoleChangeOut)
async def change_admin_role(
    account_id: uuid.UUID,
    payload: AdminRoleChangeIn,
    request: Request,
    admin: Principal = Depends(_require_super),
    session: AsyncSession = Depends(get_session),
):
    """Change an account's role. Four constraints, all enforced in the service.

    Kept as a `PATCH` on `/role` rather than a general `PATCH /accounts/{id}`
    on purpose: a single update endpoint that accepts `is_active` alongside
    `admin_role` is one where a future field gets added without anyone
    re-reading which constraints applied to the neighbours. Role change is the
    dangerous transition and it gets its own door.
    """
    service = AdminAccountService(session)
    account, previous = await service.change_role(
        account_id=account_id, new_role=payload.admin_role, actor_id=admin.id
    )
    remaining = await service.count_super_admins()
    await record_audit(
        session,
        event=EV_ADMIN_ROLE_CHANGE,
        actor_id=admin.id,
        username=await _actor_username(session, admin),
        detail=f"changed {account.username} from {previous.value} to {account.role}",
        # `from`/`to` rather than the account's current state: the row itself
        # holds only the new value, so the transition exists nowhere else.
        payload={
            "account_id": str(account.id),
            "account_username": account.username,
            "from": previous.value,
            "to": account.role,
            "super_admin_count": remaining,
        },
        request=request,
    )
    await session.commit()
    return {
        "id": str(account.id),
        "previous_role": previous.value,
        "admin_role": account.role,
        "super_admin_count": remaining,
    }


@router.post("/accounts/{account_id}/password/reset", response_model=AdminPasswordResetOut)
async def reset_admin_password(
    account_id: uuid.UUID,
    payload: AdminPasswordResetIn,
    request: Request,
    admin: Principal = Depends(_require_super),
    session: AsyncSession = Depends(get_session),
):
    """Set another admin's password. SUPER_ADMIN only.

    Changing **your own** password is a different operation with a different
    precondition — it must require the current password, or anyone who finds an
    unlocked session can lock the owner out of their own account. That route is
    not this one; this one exists for the "my only admin is locked out" case,
    which is why it does not require the old password and why it clears the
    lockout counters.
    """
    service = AdminAccountService(session)
    account = await service.reset_password(account_id=account_id, new_password=payload.new_password)
    await record_audit(
        session,
        event=EV_ADMIN_PASSWORD_RESET,
        actor_id=admin.id,
        username=await _actor_username(session, admin),
        detail=f"reset password for {account.username}",
        # Never the password and never the hash — an audit log readable by
        # every role is not a place a credential can appear.
        payload={"account_id": str(account.id), "account_username": account.username},
        request=request,
    )
    # A password reset is what you do when a credential is compromised or an
    # operator leaves, so leaving the old sessions alive defeats the operation.
    # Revoke the same two things `/admin/auth/logout` does (SEC-18):
    #   * the refresh rows, inside this transaction, so the family cannot rotate
    #     and the revocation commits atomically with the password change;
    #   * the access-token epoch in Redis, so tokens already minted die now
    #     instead of surviving their remaining 15 minutes.
    # The Redis step fails open by design (`app/core/token_revocation.py`), so the
    # guarantee is: the refresh family is gone the moment this returns, and every
    # access token is dead within `ACCESS_TOKEN_EXPIRE_MINUTES` at the very worst.
    await AdminRefreshService(session).revoke_all_for_admin(account.id)
    await session.commit()
    await revoke_user_tokens(request.app.state.auth_redis, account.id)
    return {
        "id": str(account.id),
        "sessions_revoked": True,
    }


@router.patch("/accounts/{account_id}/active", response_model=AdminActiveChangeOut)
async def change_admin_active(
    account_id: uuid.UUID,
    payload: AdminActiveChangeIn,
    request: Request,
    admin: Principal = Depends(_require_super),
    session: AsyncSession = Depends(get_session),
):
    """Deactivate or reactivate an admin. SUPER_ADMIN only.

    A door of its own rather than a field on a general `PATCH /accounts/{id}`,
    for the reason `/role` is separate: the constraints differ per field — the
    last active SUPER_ADMIN cannot be lowered *or* switched off, the self check
    applies to both — and a combined endpoint is one where the next field is
    added without anyone re-reading which constraints applied to the neighbours.

    This is the half of "revoke an admin" that a password reset cannot do. A
    reset restores access to an account that should keep it; deactivation ends
    it. `require_admin` already re-reads `is_active` on every request, so the
    access token stops working at the account's next call — but that check has
    never had anything to act on, because no route could set the column. The
    refresh family is revoked here because it is *rotated* rather than re-read,
    so an unrevoked row would keep minting fresh access tokens for its full
    lifetime.
    """
    service = AdminAccountService(session)
    account, previous = await service.set_active(
        account_id=account_id, is_active=payload.is_active, actor_id=admin.id
    )
    await record_audit(
        session,
        event=EV_ADMIN_ACCOUNT_ACTIVE_CHANGE,
        actor_id=admin.id,
        username=await _actor_username(session, admin),
        detail=(
            f"reactivated admin {account.username}"
            if account.is_active
            else f"deactivated admin {account.username}"
        ),
        # `from`/`to`, matching `AdminActiveChangeOut`, so the response and the
        # audit row cannot disagree. `super_admin_count` is the count *after*
        # the change: switching off a SUPER_ADMIN is the moment an operator
        # needs to see how many are left.
        payload={
            "account_id": str(account.id),
            "account_username": account.username,
            "from": previous,
            "to": account.is_active,
            "super_admin_count": await service.count_super_admins(),
        },
        request=request,
    )
    if not account.is_active:
        # The same pair as a password reset, for the same reason: the refresh
        # rows inside this transaction, the access-token epoch after it.
        await AdminRefreshService(session).revoke_all_for_admin(account.id)
    await session.commit()
    if not account.is_active:
        await revoke_user_tokens(request.app.state.auth_redis, account.id)
    return {
        "id": str(account.id),
        "is_active": account.is_active,
        "previous_is_active": previous,
        "sessions_revoked": not account.is_active,
    }
