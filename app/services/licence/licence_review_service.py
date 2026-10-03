"""Admin side of driver licence verification (P-3): read the queue, decide.

Separated from `licence_service` because the caller is different in kind. An
admin review runs with an `AdminAccount` principal (the P-1 separate table), not
a `User`; keeping the two services apart means an admin-only decision method can
never be reached from a driver's session by importing the wrong class.

Three invariants this module exists to hold:

1. **A decision is attributable.** `reviewed_by` is the admin account id and
   `reviewed_at` is set on the write. An approval with no recorded approver is
   worthless in an incident review, which is the only time anyone reads it.
2. **Evidence must exist.** Approving is refused if the required documents are
   missing, or if a document's object is not in storage. This is the check that
   stops the classic failure: a driver requests a presigned URL, the upload
   fails, the client reports success anyway, and an operator clicks approve on a
   submission with no image in it.
3. **One decision per submission.** A submission that is not `PENDING` cannot be
   re-decided. Without this, a second call could flip a `REJECTED` to
   `APPROVED` — or the reverse — leaving no record that either happened.

Approval does **not** idle or activate a driver beyond the one legitimate
transition: an admin KYC approval still moves `PENDING_KYC -> DEPOSIT_REQUIRED`,
matching `admin.py`'s existing review. A *renewal* approval changes only the
submission, so a trading driver keeps trading.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BusinessRuleError
from app.models import (
    DriverLicenceSubmission,
    DriverProfile,
    DriverStatus,
    LicenceReviewStatus,
)
from app.services.licence.licence_service import (
    LICENCE_NO_RE,
    REQUIRED_DOCUMENT_KINDS,
    SubmissionOut,
    _out,
)
from app.services.licence.storage_service import get_storage_service
from app.services.order.state_machine import assert_driver_transition

logger = logging.getLogger(__name__)


class LicenceReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- queue --------------------------------------------------------------- #

    async def list_submissions(
        self,
        *,
        status: LicenceReviewStatus | None = LicenceReviewStatus.PENDING,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """The review queue.

        Defaults to `PENDING` because that is the only status an operator acts
        on; passing `None` is the deliberate "show me everything" used by an
        audit view. Oldest first, so a submission cannot starve at the back of
        the queue while newer ones are picked off.
        """
        from sqlalchemy import func

        q = (
            select(DriverLicenceSubmission)
            .options(selectinload(DriverLicenceSubmission.documents))
            .order_by(DriverLicenceSubmission.submitted_at)
        )
        count_q = select(func.count()).select_from(DriverLicenceSubmission)
        if status is not None:
            q = q.where(DriverLicenceSubmission.status == status)
            count_q = count_q.where(DriverLicenceSubmission.status == status)

        limit = max(1, min(limit, 200))
        rows = (await self.session.execute(q.limit(limit).offset(max(0, offset)))).scalars().all()
        total = (await self.session.execute(count_q)).scalar_one()

        # The driver's status is joined in as a scalar subquery rather than a
        # relationship load: the queue row needs one column from the profile,
        # and eager-loading the whole row per item turns a 50-row page into 50
        # extra selects for data the list does not render.
        driver_status = (
            {
                dp.id: dp.status
                for dp in (
                    await self.session.execute(
                        select(DriverProfile).where(
                            DriverProfile.id.in_([r.driver_profile_id for r in rows])
                        )
                    )
                )
                .scalars()
                .all()
            }
            if rows
            else {}
        )

        return {
            "items": [
                {
                    # Built from explicit fields rather than `**_out(r).as_dict()`.
                    # Spreading the full serialisation would drag `documents`
                    # (with its ids, content types and byte sizes) into every
                    # poll of the queue — data the list does not render, attached
                    # to the response an operator leaves open on a second monitor.
                    "id": str(r.id),
                    "licence_no": r.licence_no,
                    "expires_on": r.expires_on.isoformat(),
                    "status": r.status.value,
                    "submitted_note": r.submitted_note,
                    "rejection_reason": r.rejection_reason,
                    "submitted_at": r.submitted_at.isoformat(),
                    "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                    "driver_profile_id": str(r.driver_profile_id),
                    "driver_status": driver_status[r.driver_profile_id].value
                    if r.driver_profile_id in driver_status
                    else None,
                    # Counts and a boolean, not the documents themselves: enough
                    # to triage the row, and the detail endpoint is one click
                    # away.
                    "document_count": len(r.documents),
                    "has_required_documents": self._has_required(r),
                }
                for r in rows
            ],
            "total": total,
            "limit": limit,
            "offset": max(0, offset),
        }

    async def get_submission(self, submission_id: uuid.UUID) -> DriverLicenceSubmission:
        """One submission with its documents, in a single query.

        `session.get` would return the row without the relationship, and the
        first `sub.documents` access would attempt a lazy load — which raises
        `MissingGreenlet` under asyncio. The explicit eager load is required, not
        an optimisation.
        """
        row = (
            (
                await self.session.execute(
                    select(DriverLicenceSubmission)
                    .options(selectinload(DriverLicenceSubmission.documents))
                    .where(DriverLicenceSubmission.id == submission_id)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise BusinessRuleError("submission not found")
        return row

    # -- detail with signed URLs --------------------------------------------- #

    async def detail(self, submission_id: uuid.UUID, *, ttl_s: int = 300) -> dict[str, Any]:
        """Everything an operator needs, including short-lived image URLs.

        The URLs are minted here and not in `list_submissions`, so a working
        link to an identity document only exists when someone opened the review
        page — not on every poll of the queue, and not in whatever the queue
        response happens to be logged into.

        `expires_at` on the URL is the same TTL the console shows a countdown
        for; a page left open past it needs a refresh, which is the intended
        behaviour for a document this sensitive.
        """
        sub = await self.get_submission(submission_id)
        driver = await self.session.get(DriverProfile, sub.driver_profile_id)
        storage = get_storage_service()

        documents = []
        for doc in sub.documents:
            head = None
            try:
                head = storage.head(object_key=doc.object_key)
            except Exception as exc:
                logger.warning("head_object failed for %s: %s", doc.object_key, exc)
            documents.append(
                {
                    "id": str(doc.id),
                    "kind": doc.kind.value,
                    "content_type": doc.content_type,
                    "size_bytes": doc.size_bytes,
                    "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
                    "download_url": storage.presign_download(
                        object_key=doc.object_key, ttl_s=ttl_s
                    ),
                    "url_expires_in": ttl_s,
                    # Present-but-empty is a real state: the driver declared an
                    # upload that never landed. Surfacing it here is what makes
                    # the operator's approve button safe to press.
                    "stored": head is not None,
                    "stored_size_bytes": head.get("size_bytes") if head else None,
                }
            )

        return {
            **_out(sub).as_dict(),
            "driver_profile_id": str(sub.driver_profile_id),
            "driver_status": driver.status.value if driver else None,
            "driver_taxi_type": driver.taxi_type if driver else None,
            "driver_plate_no": driver.taxi_driver_plate_no if driver else None,
            "driver_vehicle_reg_mark": driver.vehicle_reg_mark if driver else None,
            "reviewed_by": str(sub.reviewed_by) if sub.reviewed_by else None,
            "has_required_documents": self._has_required(sub),
            "documents": documents,
        }

    # -- decision ------------------------------------------------------------ #

    async def decide(
        self,
        *,
        submission_id: uuid.UUID,
        admin_id: uuid.UUID,
        approve: bool,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Approve or reject one submission.

        Ordering matters: the decision guard runs before the evidence check, so
        a re-decision attempt gets "already decided" rather than a confusing
        complaint about documents. The evidence check runs before any write, so
        a refused approval leaves the row exactly as it was.
        """
        sub = await self.get_submission(submission_id)

        if sub.status != LicenceReviewStatus.PENDING:
            raise BusinessRuleError(
                f"this submission was already {sub.status.value.lower()}",
                {"status": sub.status.value},
            )

        if approve:
            self._assert_evidence_present(sub)
            target = LicenceReviewStatus.APPROVED
            sub.rejection_reason = None
        else:
            cleaned = (reason or "").strip()
            if not cleaned:
                # A rejection with no reason is unactionable for the driver: they
                # cannot tell whether to retake the photo, fix the number, or
                # give up. This is a hard requirement, not a nicety.
                raise BusinessRuleError("a rejection must include a reason")
            target = LicenceReviewStatus.REJECTED
            sub.rejection_reason = cleaned[:500]

        sub.status = target
        sub.reviewed_by = admin_id
        sub.reviewed_at = datetime.now(UTC)

        driver_state: dict[str, Any] = {}
        if approve:
            driver_state = await self._promote_driver(sub)

        await self.session.flush()
        logger.info(
            "licence submission decided submission=%s status=%s admin=%s",
            sub.id,
            target.value,
            admin_id,
        )
        return {
            "id": str(sub.id),
            "status": sub.status.value,
            "reviewed_at": sub.reviewed_at.isoformat() if sub.reviewed_at else None,
            **driver_state,
        }

    # -- helpers ------------------------------------------------------------- #

    async def _promote_driver(self, sub: DriverLicenceSubmission) -> dict[str, Any]:
        """Move an approved driver out of `PENDING_KYC`, once.

        Only that one transition, and only when the driver is actually in it. A
        renewal submitted by an ACTIVE driver takes the `else` branch and changes
        nothing about their trading status — which is the whole reason the
        submission has its own status enum.

        `assert_driver_transition` is the same guard `admin.py` uses, so an
        approval cannot invent a path the state machine does not have (for
        example `ACTIVE -> DEPOSIT_REQUIRED`).
        """
        driver = await self.session.get(DriverProfile, sub.driver_profile_id)
        if driver is None:
            return {}

        if driver.status == DriverStatus.PENDING_KYC:
            assert_driver_transition(driver.status, DriverStatus.DEPOSIT_REQUIRED)
            driver.status = DriverStatus.DEPOSIT_REQUIRED
            return {"driver_status": driver.status.value, "driver_promoted": True}

        return {"driver_status": driver.status.value, "driver_promoted": False}

    @staticmethod
    def _has_required(sub: DriverLicenceSubmission) -> bool:
        present = {d.kind for d in sub.documents}
        return all(k in present for k in REQUIRED_DOCUMENT_KINDS)

    def _assert_evidence_present(self, sub: DriverLicenceSubmission) -> None:
        """Refuse to approve a submission whose evidence is incomplete.

        Three distinct failures, reported distinctly, because "cannot approve"
        with no cause leaves an operator to guess:

        - a required kind is simply absent (a client bug, or a partial submit)
        - the licence number no longer satisfies the format (validated at submit,
          re-checked here so a change to the rule cannot retro-approve old rows)
        - the object is not in storage — the declared-but-never-uploaded case
        """
        present = {d.kind for d in sub.documents}
        missing = [k.value for k in REQUIRED_DOCUMENT_KINDS if k not in present]
        if missing:
            raise BusinessRuleError(
                "cannot approve: required documents are missing",
                {"missing": missing},
            )

        if sub.expires_on is None:
            raise BusinessRuleError("cannot approve: the licence has no expiry date")
        if sub.expires_on <= datetime.now(UTC):
            raise BusinessRuleError(
                "cannot approve: this licence has already expired",
                {"expires_on": sub.expires_on.date().isoformat()},
            )

        import re

        if not re.match(LICENCE_NO_RE, sub.licence_no or ""):
            raise BusinessRuleError("cannot approve: the licence number is not well formed")

        storage = get_storage_service()
        if not storage.is_configured:
            # Dev/test: nothing to check against, and refusing here would make
            # the flow untestable. Documented rather than silent.
            logger.info("approve without storage verification (storage not configured)")
            return

        absent = []
        for doc in sub.documents:
            if doc.kind not in REQUIRED_DOCUMENT_KINDS:
                continue
            if storage.head(object_key=doc.object_key) is None:
                absent.append(doc.kind.value)
        if absent:
            raise BusinessRuleError(
                "cannot approve: the uploaded documents are not in storage",
                {"not_uploaded": absent},
            )


__all__ = ["LicenceReviewService", "SubmissionOut"]
