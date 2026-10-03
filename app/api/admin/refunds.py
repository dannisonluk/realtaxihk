"""The refund queue and its decision. `/refunds*`. FINANCE only."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin._roles import _require_finance
from app.api.admin._shared import _refund_out
from app.api.schemas import RefundDecisionOut, RefundPageOut
from app.core.db import get_session
from app.core.deps import Principal, require_admin
from app.core.money import money_str
from app.models import RefundRequest, RefundStatus
from app.services.audit_service import EV_REFUND_DECISION, record_audit
from app.services.refund_service import RefundService

router = APIRouter()


@router.get("/refunds", response_model=RefundPageOut)
async def list_refunds(
    status_filter: Annotated[str | None, Query(pattern=r"^(PENDING|APPROVED|REJECTED)$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Refund queue. Default ordering is newest-first so PENDING rows surface."""
    q = select(RefundRequest).order_by(RefundRequest.created_at.desc())
    count_q = select(func.count()).select_from(RefundRequest)
    if status_filter:
        q = q.where(RefundRequest.status == RefundStatus(status_filter))
        count_q = count_q.where(RefundRequest.status == RefundStatus(status_filter))
    rows = (await session.execute(q.limit(limit).offset(offset))).scalars().all()
    total = (await session.execute(count_q)).scalar_one()
    return {
        "items": [_refund_out(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


class RefundDecisionIn(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")
    note: str = ""


@router.post("/refunds/{refund_id}/decision", response_model=RefundDecisionOut)
async def decide_refund(
    refund_id: uuid.UUID,
    payload: RefundDecisionIn,
    request: Request,
    admin: Principal = Depends(_require_finance),
    session: AsyncSession = Depends(get_session),
):
    """Approve (pays out, driver TERMINATED) or reject (releases hold, driver ACTIVE).

    Approval is the only path that moves money out: it writes the REFUND ledger
    entry keyed `refund:{id}`, so a double-click cannot pay twice. A second
    decision on an already-decided request is rejected.
    """
    refund = await RefundService(session).decide(
        refund_id,
        approve=payload.decision == "approve",
        admin_id=admin.id,
        decision_note=payload.note or None,
    )
    # Both outcomes are audited, not just the payout. A refusal is also a
    # decision somebody may later have to justify, and it leaves no ledger trace
    # at all — which is precisely why `refund.decided_by` alone was not enough.
    await record_audit(
        session,
        event=EV_REFUND_DECISION,
        actor_id=admin.id,
        detail=f"{payload.decision} refund {refund_id}",
        request=request,
        payload={
            "refund_id": str(refund_id),
            "driver_profile_id": str(refund.driver_profile_id),
            "decision": payload.decision,
            "amount_hkd": money_str(Decimal(refund.amount_hkd)),
            "note": payload.note or None,
        },
    )
    return _refund_out(refund)
