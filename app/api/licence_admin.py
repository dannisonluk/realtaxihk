"""Admin licence review API (P-3): the queue, the detail view, the decision.

    GET  /api/v1/admin/licence/submissions            — the review queue
    GET  /api/v1/admin/licence/submissions/{id}       — detail + signed image URLs
    POST /api/v1/admin/licence/submissions/{id}/decide — approve or reject

Every route requires `require_admin`, which in this codebase re-reads the live
`User` row for `is_active` (P0-3) and checks `role == ADMIN`. It is a
`User`-based principal, distinct from the P-1 `admin_accounts` console login:
the console authenticates against `admin_accounts` (password + TOTP) and its
token carries `role="ADMIN"`, so both paths land here — but the *decider* id
recorded on the submission is whichever principal acted, and the two id spaces
are different tables. That is by design; the audit row names the actor.

Signed image URLs are minted only in the detail endpoint. Putting them in the
queue would leave a working link to an identity document in every poll of the
list, and in whatever that response is logged into.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    LicenceDecisionOut,
    LicenceQueueOut,
    LicenceReviewDetailOut,
)
from app.core.db import get_session
from app.core.deps import Principal, require_admin
from app.core.exceptions import BusinessRuleError
from app.models import LicenceReviewStatus
from app.services.licence_review_service import LicenceReviewService

logger = logging.getLogger("realtaxihk.licence.admin")

router = APIRouter(prefix="/api/v1/admin/licence", tags=["admin-licence"])


class DecideIn(BaseModel):
    """A decision, plus the reason a rejection requires.

    `reason` is optional in the schema and mandatory in the service when
    `approve` is false. Enforcing it in Pydantic instead would give a generic
    422; the service raises a message that says *why* a reason is required.
    """

    approve: bool
    reason: str | None = Field(default=None, max_length=500)


async def _guard(coro, session: AsyncSession):
    """Map a service refusal onto HTTP.

    No commit here, unlike the driver-facing `_run` helpers: every refusal on
    this side happens **before** any write (the decision guard, then the evidence
    check, then the write), so there is nothing to preserve. Adding a commit
    would risk persisting a half-applied decision.

    `session` is unused but kept in the signature so these handlers read
    identically to the other routers — a reader comparing the two should see the
    missing commit as the deliberate difference, not a differently-shaped helper.
    """
    try:
        return await coro
    except BusinessRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/submissions", response_model=LicenceQueueOut)
async def list_submissions(
    request: Request,
    status_filter: Annotated[str | None, Query(alias="status")] = "PENDING",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """The review queue, oldest first.

    `status` defaults to `PENDING` — the only status an operator acts on. Pass
    `status=` (empty) explicitly to see everything, which is the audit view. An
    unknown value is refused rather than silently treated as "all", because a
    typo that quietly widens a queue is worse than an error.
    """
    status_enum: LicenceReviewStatus | None
    if status_filter is None or status_filter == "" or status_filter.lower() == "all":
        status_enum = None
    else:
        try:
            status_enum = LicenceReviewStatus(status_filter.strip().upper())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"unknown status {status_filter!r}; "
                f"expected one of {[s.value for s in LicenceReviewStatus]} or 'all'",
            ) from exc

    return await _guard(
        LicenceReviewService(session).list_submissions(
            status=status_enum, limit=limit, offset=offset
        ),
        session,
    )


@router.get("/submissions/{submission_id}", response_model=LicenceReviewDetailOut)
async def submission_detail(
    submission_id: uuid.UUID,
    ttl: Annotated[int, Query(ge=60, le=900)] = 300,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Everything needed to decide, including short-lived image URLs.

    `ttl` is bounded to 15 minutes: long enough that an operator can look at a
    few images in one sitting, short enough that a copied URL is useless by the
    time anything leaks it.
    """
    return await _guard(LicenceReviewService(session).detail(submission_id, ttl_s=ttl), session)


@router.post("/submissions/{submission_id}/decide", response_model=LicenceDecisionOut)
async def decide_submission(
    submission_id: uuid.UUID,
    payload: DecideIn,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Approve or reject one submission. Terminal — a second call is refused.

    An approval promotes a `PENDING_KYC` driver to `DEPOSIT_REQUIRED` (the same
    transition `admin.py`'s review uses). It does **not** touch a driver who is
    already trading: a renewal decision changes only the submission, so nobody
    is taken offline to swap a document.
    """
    result = await _guard(
        LicenceReviewService(session).decide(
            submission_id=submission_id,
            admin_id=admin.id,
            approve=payload.approve,
            reason=payload.reason,
        ),
        session,
    )
    # The decision is the write that matters, so it is committed explicitly —
    # a later failure in the response path must not roll back an approval the
    # operator has already seen acknowledged.
    await session.commit()
    return result
