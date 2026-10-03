"""The audit trail, newest first. `/audit`. Readable by every admin role."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AuditPageOut
from app.core.db import get_session
from app.core.deps import Principal, require_admin
from app.models import AdminAuditLog

router = APIRouter()


def _audit_out(row: AdminAuditLog) -> dict:
    return {
        "id": str(row.id),
        "actor_id": str(row.admin_id) if row.admin_id else None,
        "actor_username": row.username_attempted,
        "event": row.event,
        "outcome": row.outcome,
        "detail": row.detail,
        "payload": row.payload,
        "ip_address": row.ip_address,
        "user_agent": row.user_agent,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


@router.get("/audit", response_model=AuditPageOut)
async def list_audit_log(
    event: Annotated[str | None, Query(max_length=48)] = None,
    admin_id: uuid.UUID | None = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Read the audit trail. Newest first, filterable by event / actor / range.

    Readable by **every** admin role, including SUPPORT, and that is
    intentional. The audit log is a record of what was done by whom; restricting
    its reading would mean the people least able to change anything are also the
    least able to notice that something was changed. It contains no secrets —
    no password, no TOTP secret, no token — only decisions and their actors.

    `since`/`until` are half-open (`created_at >= since`, `< until`), matching
    the analytics range convention, so an operator paging day by day does not
    see the boundary row twice.
    """
    q = select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc())
    count_q = select(func.count()).select_from(AdminAuditLog)
    filters = []
    if event:
        filters.append(AdminAuditLog.event == event)
    if admin_id is not None:
        filters.append(AdminAuditLog.admin_id == admin_id)
    if since is not None:
        filters.append(AdminAuditLog.created_at >= since)
    if until is not None:
        filters.append(AdminAuditLog.created_at < until)
    for f in filters:
        q = q.where(f)
        count_q = count_q.where(f)
    rows = (await session.execute(q.limit(limit).offset(offset))).scalars().all()
    total = (await session.execute(count_q)).scalar_one()
    return {
        "items": [_audit_out(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
