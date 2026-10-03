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
    AdminPasswordResetOut,
    AdminRoleChangeOut,
)
from app.core.db import get_session
from app.core.deps import Principal
from app.models import AdminAccount, AdminRole
from app.services.admin.admin_account_service import AdminAccountService
from app.services.admin.audit_service import (
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
    await session.commit()
    return {
        "id": str(account.id),
        # The access token is a signed JWT with no server-side session store, so
        # the reset cannot revoke tokens already in flight. Saying so is the
        # honest answer; a boolean that always reads `true` would train the
        # operator to believe a claim the system cannot make.
        "sessions_revoked": False,
    }
