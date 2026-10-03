"""Driver licence API (P-3): upload, submit, and track a manual review.

    POST /api/v1/drivers/licence/uploads       — presign one document upload
    POST /api/v1/drivers/licence/submissions   — open a submission
    GET  /api/v1/drivers/licence/submissions   — history, incl. the open one
    GET  /api/v1/drivers/licence/submissions/{id} — one submission
    POST /api/v1/drivers/licence/submissions/{id}/withdraw — cancel a pending one

Every route requires `require_active_user`. Deliberately **not**
`require_verified_account`: a driver who is mid-KYC has an ACTIVE account but has
not been approved yet, and gating the licence route behind the *verified* gate
that the licence itself feeds would make the flow unable to start.

The admin decision endpoints live in `admin_licence`, behind `require_admin`.
Keeping them in a separate router is what makes the authorisation boundary
visible in one place instead of a `Depends` difference buried in one handler.

No document bytes pass through the API. The client PUTs straight to R2 against
the presigned URL, then names the key it used. That keeps a 6 MB phone photo out
of the worker's request cycle entirely.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    LicenceListOut,
    LicenceSubmissionOut,
    PresignedUploadOut,
)
from app.core.client_ip import client_ip
from app.core.db import get_session
from app.core.deps import Principal, require_active_user, require_phone_current
from app.core.exceptions import BusinessRuleError
from app.models import DocumentKind
from app.services.licence.licence_service import MAX_SUBMISSIONS_PER_DAY, LicenceService

logger = logging.getLogger("realtaxihk.licence")

router = APIRouter(prefix="/api/v1/drivers/licence", tags=["drivers-licence"])

# Upload presigns are cheap for us (a signature, no bytes) but each one mints a
# key the client can PUT to, so they are capped per account rather than left
# unbounded. 40 an hour is far above a real driver's need (two documents, plus
# retries for a bad photo) and far below a useful abuse rate.
_UPLOAD_RATE_LIMIT = 40
_UPLOAD_RATE_WINDOW_S = 3600
_READ_RATE_LIMIT = 240
_READ_RATE_WINDOW_S = 60


class UploadIn(BaseModel):
    kind: DocumentKind
    content_type: str = Field(min_length=3, max_length=128)
    size_bytes: int = Field(gt=0, le=50 * 1024 * 1024)


class DocumentIn(BaseModel):
    """A key the client already uploaded.

    `size_bytes` is required rather than looked up: it is what the client
    declared, and the service compares it against what storage reports. Accepting
    only the key would skip that comparison.
    """

    kind: DocumentKind
    object_key: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=128)
    size_bytes: int = Field(gt=0, le=50 * 1024 * 1024)


class SubmitIn(BaseModel):
    licence_no: str = Field(min_length=5, max_length=32)
    expires_on: datetime
    documents: list[DocumentIn] = Field(min_length=1, max_length=6)
    note: str | None = Field(default=None, max_length=500)


async def _limit(request: Request, bucket: str, limit: int, window: int) -> None:
    limiter = request.app.state.rate_limiter
    if not await limiter.allow(f"licence:{bucket}:{client_ip(request)}", limit, window):
        raise HTTPException(status_code=429, detail="too many requests — slow down")


async def _run(coro, session: AsyncSession):
    """Map a service refusal onto HTTP, committing before raising.

    Same contract as `app.api.identity._run`: `get_session` rolls back on
    exception, and every refusal here is one, so any state a handler wrote
    before the refusal (a `SUPERSEDED` stamp, an updated document row) must be
    committed here or it vanishes silently.
    """
    try:
        return await coro
    except BusinessRuleError as exc:
        await session.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/uploads", response_model=PresignedUploadOut)
async def presign_upload(
    payload: UploadIn,
    request: Request,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Presign one document upload.

    Returns the URL and the `object_key` the client must send back with the
    submission. Nothing is written to the database here — a row created before
    the bytes exist would be an attachment pointing at nothing.
    """
    await _limit(request, "upload", _UPLOAD_RATE_LIMIT, _UPLOAD_RATE_WINDOW_S)
    return await _run(
        LicenceService(session).presign_document_upload(
            user_id=user.id,
            kind=payload.kind,
            content_type=payload.content_type,
            size_bytes=payload.size_bytes,
        ),
        session,
    )


@router.post(
    "/submissions", status_code=status.HTTP_201_CREATED, response_model=LicenceSubmissionOut
)
async def submit_licence(
    payload: SubmitIn,
    user: Principal = Depends(require_phone_current),
    session: AsyncSession = Depends(get_session),
):
    """Open a licence submission for manual review.

    Both a driving licence and a taxi driver pass (的士司機證) are required. A
    second submission while one is pending is refused rather than queued — the
    client is told to wait for the decision or withdraw.
    """
    result = await _run(
        LicenceService(session).submit(
            user_id=user.id,
            licence_no=payload.licence_no,
            expires_on=payload.expires_on,
            documents=[d.model_dump() for d in payload.documents],
            note=payload.note,
        ),
        session,
    )
    await session.commit()
    return result.as_dict()


@router.get("/submissions", response_model=LicenceListOut)
async def list_my_submissions(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Everything this driver has submitted, newest first.

    This is where a rejection reason lives, and what distinguishes "you have
    never applied" from "your renewal is pending" for the app's UI.
    """
    await _limit(request, "read", _READ_RATE_LIMIT, _READ_RATE_WINDOW_S)
    svc = LicenceService(session)
    profile = await svc.require_profile(user.id)
    items = await _run(svc.history(profile.id, limit=limit), session)
    current = await svc.current_submission(profile.id)
    return {
        "items": [s.as_dict() for s in items],
        "current_submission_id": str(current.id) if current else None,
        "driver_status": profile.status.value,
        "max_submissions_per_day": MAX_SUBMISSIONS_PER_DAY,
        "required_document_kinds": [
            k.value for k in (DocumentKind.DRIVER_LICENCE, DocumentKind.TAXI_DRIVER_PASS)
        ],
    }


@router.get("/submissions/{submission_id}", response_model=LicenceSubmissionOut)
async def get_my_submission(
    submission_id: uuid.UUID,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    svc = LicenceService(session)
    profile = await svc.require_profile(user.id)
    sub = await _run(svc.get_submission(profile.id, submission_id), session)
    return _detail(sub)


@router.post("/submissions/{submission_id}/withdraw", response_model=LicenceSubmissionOut)
async def withdraw_submission(
    submission_id: uuid.UUID,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Cancel your own pending submission, so a mistake needs no operator.

    Becomes `SUPERSEDED` rather than being deleted: the record of what was
    withdrawn has to survive, both for the audit trail and so a driver cannot
    erase a submission that was already looked at.
    """
    result = await _run(
        LicenceService(session).withdraw(user_id=user.id, submission_id=submission_id),
        session,
    )
    await session.commit()
    return result.as_dict()


def _detail(sub) -> dict:
    """One submission, driver-facing.

    Composed from the ORM object rather than `SubmissionOut.as_dict()` because
    the single-submission view carries `documents` with their ids, which the
    list view also has — this exists so the two cannot drift.
    """
    return {
        "id": str(sub.id),
        "licence_no": sub.licence_no,
        "expires_on": sub.expires_on.isoformat(),
        "status": sub.status.value,
        "submitted_note": sub.submitted_note,
        "rejection_reason": sub.rejection_reason,
        "submitted_at": sub.submitted_at.isoformat(),
        "reviewed_at": sub.reviewed_at.isoformat() if sub.reviewed_at else None,
        "documents": [
            {
                "id": str(d.id),
                "kind": d.kind.value,
                "content_type": d.content_type,
                "size_bytes": d.size_bytes,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            }
            for d in sub.documents
        ],
    }
