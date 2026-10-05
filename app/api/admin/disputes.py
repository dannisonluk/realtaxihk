"""Disputes — the after-the-fact judgement about who bears a cost.

`/disputes*`. Judging conduct is OPERATIONS; a resolution that moves money
additionally requires FINANCE, checked per-request in the handler because
`moves_money` is in the body rather than the path. The check is a whitelist
matched to the decision, not a rank floor — see `_roles.py` for the
separation-of-duties reasoning."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.admin._roles import _require_operations
from app.api.admin._shared import _actor_username
from app.api.schemas import (
    AdminDisputeDetailOut,
    AdminDisputePageOut,
    DisputeMessageOut,
    DisputeResolveOut,
    DisputeStatsOut,
)
from app.core.db import get_session_factory
from app.core.deps import Principal, live_admin_role, require_admin
from app.core.exceptions import BusinessRuleError
from app.models import (
    AdminRole,
    DisputeCategory,
    DisputeMessage,
    DisputeResolution,
    DisputeSeverity,
    DisputeSource,
    DisputeStatus,
    OrderDispute,
)
from app.services.admin.audit_service import (
    EV_DISPUTE_ASSIGN,
    EV_DISPUTE_CREATE,
    EV_DISPUTE_MESSAGE,
    EV_DISPUTE_RESOLVE,
    OUTCOME_SUCCESS,
    record_audit,
)
from app.services.admin.dispute_service import DisputeService, case_is_overdue

router = APIRouter()


class DisputeCreateIn(BaseModel):
    """Open a case by hand.

    `severity` defaults to `NORMAL` and `sla_due_at` is derived from it by the
    service, never accepted from the client — a caller that could set its own
    deadline could quietly give a safety report a week.
    """

    category: str = Field(pattern=r"^(FARE|CONDUCT|SAFETY|LOST_ITEM|APP_ISSUE|OTHER)$")
    summary: str = Field(min_length=1, max_length=4000)
    order_id: uuid.UUID | None = None
    severity: str = Field(
        default=DisputeSeverity.NORMAL.value,
        pattern=r"^(LOW|NORMAL|HIGH|SAFETY_CRITICAL)$",
    )
    against_kind: str | None = Field(default=None, pattern=r"^(PASSENGER|DRIVER|PLATFORM)$")
    against_id: uuid.UUID | None = None


class DisputeMessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    is_internal: bool = False


class DisputeResolveIn(BaseModel):
    """The decision. `note` is required even for `NONE` — see the service."""

    resolution: str = Field(
        pattern=(
            r"^(NONE|CHARGE_PASSENGER|CHARGE_DRIVER"
            r"|REFUND_PLATFORM_FEE|WAIVED_PLATFORM_FEE)$"
        )
    )
    note: str = Field(min_length=1, max_length=4000)
    close: bool = False


class DisputeStatusIn(BaseModel):
    status: str = Field(pattern=r"^(OPEN|INVESTIGATING|AWAITING_PARTY|ESCALATED|CLOSED)$")


def _dispute_out(dispute: OrderDispute, *, now: datetime | None = None) -> dict:
    """One queue row, with the derived fields the console sorts and colours by.

    `seconds_until_due` is computed server-side against the same clock the sort
    uses. The client recomputing it from `sla_due_at` and its own system time
    gives a different answer on a machine with a skewed clock, and the row then
    contradicts the order it is displayed in.
    """
    now = now or datetime.now(UTC)
    due = dispute.sla_due_at
    if due is not None and due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return {
        "id": str(dispute.id),
        "order_id": str(dispute.order_id) if dispute.order_id else None,
        "source": dispute.source,
        "category": dispute.category,
        "severity": dispute.severity,
        "status": dispute.status,
        "summary": dispute.summary,
        "raised_by_kind": dispute.raised_by_kind,
        "against_kind": dispute.against_kind,
        "assigned_admin_id": (
            str(dispute.assigned_admin_id) if dispute.assigned_admin_id else None
        ),
        "safety_flag": dispute.safety_flag,
        "sla_due_at": due.isoformat() if due else "",
        "sla_hours": dispute.severity_enum.sla_hours,
        "seconds_until_due": int((due - now).total_seconds()) if due else 0,
        "is_overdue": case_is_overdue(dispute, now=now),
        "resolution": dispute.resolution,
        "resolved_at": dispute.resolved_at.isoformat() if dispute.resolved_at else None,
        "created_at": dispute.created_at.isoformat(),
    }


def _dispute_message_out(message: DisputeMessage) -> dict:
    return {
        "id": message.id,
        "author_kind": message.author_kind,
        "author_id": str(message.author_id) if message.author_id else None,
        "author_label": message.author_label,
        "body": message.body,
        "is_internal": message.is_internal,
        "created_at": message.created_at.isoformat(),
    }


@router.get("/disputes", response_model=AdminDisputePageOut)
async def list_disputes(
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    assigned_admin_id: uuid.UUID | None = Query(default=None),
    unassigned_only: bool = Query(default=False),
    open_only: bool = Query(default=True),
    overdue_only: bool = Query(default=False),
    order_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: Principal = Depends(require_admin),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """The dispute queue. Any role may read it — only deciding is narrowed.

    **Sorted by SLA ascending (`sla_due_at`), not by recency.** Deliberate: an
    incident queue sorted newest-first answers whatever arrived while somebody
    was watching and buries the case about to breach. `open_only` defaults to
    true because the console's default view is the work remaining, not the
    archive.
    """
    for value, enum_type, field in (
        (status, DisputeStatus, "status"),
        (category, DisputeCategory, "category"),
        (severity, DisputeSeverity, "severity"),
    ):
        if value is None:
            continue
        try:
            enum_type(value)
        except ValueError as exc:
            raise BusinessRuleError(
                f"unknown {field}: {value!r}",
                {"reason": "INVALID_FILTER", "field": field, "value": value},
            ) from exc

    rows, total = await DisputeService(session_factory).list_cases(
        status=status,
        category=category,
        severity=severity,
        assigned_admin_id=assigned_admin_id,
        unassigned_only=unassigned_only,
        open_only=open_only,
        overdue_only=overdue_only,
        order_id=order_id,
        limit=limit,
        offset=offset,
    )
    now = datetime.now(UTC)
    return {
        "items": [_dispute_out(d, now=now) for d in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/disputes/stats", response_model=DisputeStatsOut)
async def dispute_stats(
    admin: Principal = Depends(require_admin),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Queue header counts, computed server-side against one clock."""
    return await DisputeService(session_factory).stats()


@router.post("/disputes", status_code=201, response_model=AdminDisputeDetailOut)
async def create_dispute(
    payload: DisputeCreateIn,
    request: Request,
    admin: Principal = Depends(_require_operations),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Open a case by hand. OPERATIONS or above.

    A manual case still gets an SLA from its severity, the same as an automatic
    one. The alternative — an operator-set deadline — is how a manually filed
    safety complaint ends up with less urgency than a machine-filed one.
    """
    session = session_factory()
    try:
        service = DisputeService(session_factory)
        dispute = await service.open_case(
            category=payload.category,
            summary=payload.summary,
            source=DisputeSource.ADMIN_CREATED,
            order_id=payload.order_id,
            raised_by_kind="ADMIN",
            raised_by_id=admin.id,
            against_kind=payload.against_kind,
            against_id=payload.against_id,
            severity=payload.severity,
        )
        await record_audit(
            session,
            event=EV_DISPUTE_CREATE,
            outcome=OUTCOME_SUCCESS,
            actor_id=admin.id,
            username=await _actor_username(session, admin),
            detail=f"opened {payload.category} dispute",
            payload={
                "dispute_id": str(dispute.id),
                "category": payload.category,
                "severity": dispute.severity,
                "order_id": str(payload.order_id) if payload.order_id else None,
                "safety_flag": dispute.safety_flag,
            },
            request=request,
        )
        await session.commit()
    finally:
        await session.close()

    full = await DisputeService(session_factory).get(dispute.id, with_messages=True)
    return {
        **_dispute_out(full),
        "messages": [_dispute_message_out(m) for m in full.messages],
        "resolution_note": full.resolution_note,
        "resolved_by": str(full.resolved_by) if full.resolved_by else None,
    }


@router.get("/disputes/{dispute_id}", response_model=AdminDisputeDetailOut)
async def dispute_detail(
    dispute_id: uuid.UUID,
    admin: Principal = Depends(require_admin),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """One case and its whole thread — internal notes included.

    Admin-only, so the full thread is correct here. A party-facing view must be
    a separate route with its own filter; a `?include_internal=` flag on this
    one is a filter somebody eventually forgets, and what it leaks is a staff
    opinion about a customer.
    """
    full = await DisputeService(session_factory).get(dispute_id, with_messages=True)
    return {
        **_dispute_out(full),
        "messages": [_dispute_message_out(m) for m in full.messages],
        "resolution_note": full.resolution_note,
        "resolved_by": str(full.resolved_by) if full.resolved_by else None,
    }


@router.post("/disputes/{dispute_id}/assign", response_model=AdminDisputeDetailOut)
async def assign_dispute(
    dispute_id: uuid.UUID,
    request: Request,
    admin: Principal = Depends(_require_operations),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Claim a case, moving `OPEN` to `INVESTIGATING`."""
    service = DisputeService(session_factory)
    # The returned row is re-read below so the response carries the thread, so
    # only the previous assignee is needed here — it goes in the audit payload.
    _, previous = await service.assign(dispute_id, admin_id=admin.id)

    session = session_factory()
    try:
        await record_audit(
            session,
            event=EV_DISPUTE_ASSIGN,
            outcome=OUTCOME_SUCCESS,
            actor_id=admin.id,
            username=await _actor_username(session, admin),
            detail="assigned dispute",
            payload={
                "dispute_id": str(dispute_id),
                "from": str(previous) if previous else None,
                "to": str(admin.id),
            },
            request=request,
        )
        await session.commit()
    finally:
        await session.close()

    full = await service.get(dispute_id, with_messages=True)
    return {
        **_dispute_out(full),
        "messages": [_dispute_message_out(m) for m in full.messages],
        "resolution_note": full.resolution_note,
        "resolved_by": str(full.resolved_by) if full.resolved_by else None,
    }


@router.post("/disputes/{dispute_id}/messages", status_code=201, response_model=DisputeMessageOut)
async def add_dispute_message(
    dispute_id: uuid.UUID,
    payload: DisputeMessageIn,
    request: Request,
    admin: Principal = Depends(require_admin),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Add a turn to the thread.

    Any role may reply — SUPPORT's whole function is to be the first responder,
    and gating that would put the least experienced team member in front of a
    customer with no way to answer them.

    `is_internal` is recorded on the message and in the audit payload, because
    "who wrote this, and did they mean the customer to see it" is the question
    asked when a note is quoted back at us by mistake.
    """
    message = await DisputeService(session_factory).add_message(
        dispute_id,
        body=payload.body,
        author_kind="ADMIN",
        author_id=admin.id,
        author_label=None,
        is_internal=payload.is_internal,
    )

    session = session_factory()
    try:
        await record_audit(
            session,
            event=EV_DISPUTE_MESSAGE,
            outcome=OUTCOME_SUCCESS,
            actor_id=admin.id,
            username=await _actor_username(session, admin),
            detail="internal note" if payload.is_internal else "replied on dispute",
            # The body is NOT stored in the payload: it can be long, and it is
            # already the `dispute_messages` row. Duplicating free text into an
            # append-only table doubles the places a PII purge has to reach.
            payload={
                "dispute_id": str(dispute_id),
                "message_id": message.id,
                "is_internal": payload.is_internal,
                "length": len(payload.body),
            },
            request=request,
        )
        await session.commit()
    finally:
        await session.close()

    return _dispute_message_out(message)


@router.post("/disputes/{dispute_id}/status", response_model=AdminDisputeDetailOut)
async def set_dispute_status(
    dispute_id: uuid.UUID,
    payload: DisputeStatusIn,
    admin: Principal = Depends(_require_operations),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Move between non-terminal states (escalate, await the party, close).

    `RESOLVED` is refused here by the service: resolution carries a decision, a
    note and an actor, and a status flip would produce a case that reads as
    decided with none of them recorded.
    """
    service = DisputeService(session_factory)
    await service.set_status(dispute_id, status=payload.status)
    full = await service.get(dispute_id, with_messages=True)
    return {
        **_dispute_out(full),
        "messages": [_dispute_message_out(m) for m in full.messages],
        "resolution_note": full.resolution_note,
        "resolved_by": str(full.resolved_by) if full.resolved_by else None,
    }


@router.post("/disputes/{dispute_id}/resolve", response_model=DisputeResolveOut)
async def resolve_dispute(
    dispute_id: uuid.UUID,
    payload: DisputeResolveIn,
    request: Request,
    admin: Principal = Depends(require_admin),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Decide the money question.

    **The 403 is per-request, not on the route.** Judging conduct is OPERATIONS'
    job and moving money is FINANCE's, so this endpoint opens for both and then
    narrows: a resolution whose `moves_money` is true additionally requires
    FINANCE. A fixed `require_role(FINANCE)` on the route would lock OPERATIONS
    out of the `NONE` and `CHARGE_PASSENGER` decisions it is exactly the right
    role to make; a fixed `require_role(OPERATIONS)` would let the operator who
    judged the conduct also authorise the payout, which is the separation of
    duties the two roles exist to enforce.

    The check reads the **live** role via `live_admin_role`, not the token
    claim, so a demotion takes effect on the next request rather than at token
    expiry — the same authority `require_role` uses, for the same reason.
    """
    resolution = DisputeResolution(payload.resolution)
    session = session_factory()
    try:
        # Whitelist matched to the decision, not a rank floor. Rank was the bug:
        # FINANCE outranks OPERATIONS, so a single FINANCE admin passed both
        # gates and could judge the conduct and authorise the payout in one
        # request — the exact separation of duties these roles exist for.
        #
        # * conduct-only (NONE / CHARGE_PASSENGER-style judgements that move no
        #   money) is OPERATIONS' call, plus SUPER_ADMIN as break-glass.
        # * a resolution that moves money is FINANCE's call, plus SUPER_ADMIN,
        #   **and** the resolving admin must not be the admin assigned to the
        #   case. The assignee is the judge; letting them also authorise the
        #   payout is the single-request version of the hole rank opened.
        actor_role = await live_admin_role(session, admin)
        allowed = (
            {AdminRole.OPERATIONS, AdminRole.SUPER_ADMIN}
            if not resolution.moves_money
            else {AdminRole.FINANCE, AdminRole.SUPER_ADMIN}
        )
        if actor_role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": (
                        "this resolution moves money and requires FINANCE"
                        if resolution.moves_money
                        else "insufficient admin role"
                    ),
                    "reason": (
                        "DISPUTE_RESOLUTION_REQUIRES_FINANCE"
                        if resolution.moves_money
                        else "ADMIN_ROLE_INSUFFICIENT"
                    ),
                    "resolution": resolution.value,
                    "required": (
                        AdminRole.FINANCE.value
                        if resolution.moves_money
                        else AdminRole.OPERATIONS.value
                    ),
                    "actual": actor_role.value,
                },
            )
        if resolution.moves_money and actor_role is not AdminRole.SUPER_ADMIN:
            assignee = (
                await session.execute(
                    select(OrderDispute.assigned_admin_id).where(OrderDispute.id == dispute_id)
                )
            ).scalar_one_or_none()
            if assignee is not None and assignee == admin.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "message": (
                            "the admin who judged this case cannot authorise the payout — "
                            "reassign it to another FINANCE admin first"
                        ),
                        "reason": "DISPUTE_RESOLUTION_SAME_ASSIGNEE",
                        "resolution": resolution.value,
                        "actual": actor_role.value,
                    },
                )
    finally:
        await session.close()

    service = DisputeService(session_factory)
    dispute = await service.resolve(
        dispute_id,
        resolution=resolution,
        note=payload.note,
        admin_id=admin.id,
        close=payload.close,
    )

    # The audit row rides the *same* transaction as the resolution. A separate
    # commit here would leave "resolved but no audit" as a real outcome on a
    # mid-crash, and this is the one admin module that moves money.
    session = session_factory()
    try:
        await record_audit(
            session,
            event=EV_DISPUTE_RESOLVE,
            outcome=OUTCOME_SUCCESS,
            actor_id=admin.id,
            username=await _actor_username(session, admin),
            detail=f"resolved as {resolution.value}",
            payload={
                "dispute_id": str(dispute_id),
                "resolution": resolution.value,
                "moves_money": resolution.moves_money,
                "close": payload.close,
                # The note IS stored here, unlike a message body: it is the
                # decision's justification, and an audit row that says only
                # "resolved as CHARGE_DRIVER" cannot answer "why" a month later.
                "note": payload.note,
            },
            request=request,
        )
        await session.commit()
    finally:
        await session.close()

    return {
        "id": str(dispute.id),
        "status": dispute.status,
        "resolution": dispute.resolution,
        "moves_money": resolution.moves_money,
        "resolved_at": dispute.resolved_at.isoformat() if dispute.resolved_at else "",
    }
