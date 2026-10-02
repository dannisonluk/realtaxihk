"""Driver licence verification (P-3): the driver side of a manual KYC review.

The shape of this flow, and why it is not the same as P-2's email check:

- The user said plainly that the licence is *"done with manual verification and
  admins will handle the status change."* So there is no automatic decision
  anywhere in this module. It submits, it withdraws, it reports state. A human
  decides.
- **It must not idle a trading driver.** A renewal is a recurring event, so the
  submission carries its own `LicenceReviewStatus` and never writes
  `DriverProfile.status` directly. Coupling them would take a driver offline to
  re-upload a document — the behaviour that makes drivers leave a platform.
  The only place this module touches `DriverStatus` is the initial promotion out
  of `PENDING_KYC`, and only when a first approval makes the driver eligible.
- **A submission is evidence, so it is never deleted.** Rejected and superseded
  rows stay. An admin acting on the wrong submission is a real failure mode, and
  a deleted row makes it undiagnosable after the fact.

The document bytes never pass through here. The client uploads straight to R2
against a presigned URL (`storage_service`), then declares the key. That means
this module's job on the document is verification: the key must be one we minted
for this driver, the object must actually exist, and its real size and type must
match what was declared.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BusinessRuleError
from app.models import (
    DocumentKind,
    DriverDocument,
    DriverLicenceSubmission,
    DriverProfile,
    DriverStatus,
    LicenceReviewStatus,
)
from app.services.storage_service import (
    ALLOWED_IMAGE_TYPES,
    MAX_DOCUMENT_BYTES,
    get_storage_service,
)

logger = logging.getLogger(__name__)

# A licence that expires within this window is refused at submission rather than
# approved and then immediately stale. Two weeks is short enough not to block a
# genuine renewal and long enough that an operator never approves something that
# expires before the driver's next shift.
MIN_REMAINING_VALIDITY_DAYS = 14

# Ceiling on submissions per driver per day. Not a security control (the licence
# is manual, so a flood costs an operator attention, not the platform money) —
# it is a guard against a retry loop in the app turning into a queue of
# near-identical rows.
MAX_SUBMISSIONS_PER_DAY = 5

# How many documents one submission must carry. Both are required: the driving
# licence establishes the class of vehicle, the taxi driver pass (的士司機證)
# establishes the right to drive a taxi. Accepting only one would let an
# operator approve half the evidence.
REQUIRED_DOCUMENT_KINDS = (DocumentKind.DRIVER_LICENCE, DocumentKind.TAXI_DRIVER_PASS)

LICENCE_NO_RE = r"^[A-Z0-9\-]{5,32}$"


@dataclass(frozen=True)
class SubmissionOut:
    """The driver-facing view of a submission.

    `reviewed_by` is deliberately absent: an operator's internal id is not the
    driver's business, and exposing it invites a driver to argue with a named
    individual. The driver needs the *reason*, which is present.
    """

    id: uuid.UUID
    licence_no: str
    expires_on: datetime
    status: LicenceReviewStatus
    submitted_note: str | None
    rejection_reason: str | None
    submitted_at: datetime
    reviewed_at: datetime | None
    documents: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "licence_no": self.licence_no,
            "expires_on": self.expires_on.isoformat(),
            "status": self.status.value,
            "submitted_note": self.submitted_note,
            "rejection_reason": self.rejection_reason,
            "submitted_at": self.submitted_at.isoformat(),
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "documents": self.documents,
        }


def _clean(doc: DriverDocument) -> dict[str, Any]:
    """A document as the driver sees it: the key, never a signed URL.

    Signing happens on a separate endpoint so the URL is short-lived and
    auditable on its own. Baking one into every list response would leak a
    working link into any log or error report that captured the payload.
    """
    return {
        "id": str(doc.id),
        "kind": doc.kind.value,
        "content_type": doc.content_type,
        "size_bytes": doc.size_bytes,
        "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
    }


def _assert_documents_loaded(sub: DriverLicenceSubmission) -> None:
    """Fail loudly if a caller would trigger a lazy load.

    A plain `sub.documents` access is the bug this catches; `inspect(...).unloaded`
    reports it without performing the I/O that would raise `MissingGreenlet`.
    """
    from sqlalchemy import inspect as sa_inspect

    state = sa_inspect(sub)
    if "documents" in state.unloaded:
        raise RuntimeError(
            "DriverLicenceSubmission.documents was not eager-loaded; "
            "add selectinload() to the query (a lazy load raises MissingGreenlet "
            "under asyncio)"
        )


def _out(sub: DriverLicenceSubmission) -> SubmissionOut:
    """Serialise a submission, reading `documents` from the identity map.

    `sub.documents` must already be loaded. SQLAlchemy's default lazy load emits
    a SELECT on attribute access, and under asyncio that raises
    `MissingGreenlet` the moment the object is touched outside an awaited
    context — which is exactly what happens when a response model walks the
    relationship after the service returned. Every query in this module therefore
    eager-loads with `selectinload`; `_assert_documents_loaded` below turns a
    missed one into a clear error instead of a confusing greenlet traceback.
    """
    _assert_documents_loaded(sub)
    return SubmissionOut(
        id=sub.id,
        licence_no=sub.licence_no,
        expires_on=sub.expires_on,
        status=sub.status,
        submitted_note=sub.submitted_note,
        rejection_reason=sub.rejection_reason,
        submitted_at=sub.submitted_at,
        reviewed_at=sub.reviewed_at,
        documents=[_clean(d) for d in sub.documents],
    )


class LicenceService:
    """Submit, withdraw, and read licence submissions.

    Admin decisions live in `licence_review_service` — a separate module because
    the console is a different caller with a different principal type
    (`AdminAccount`, not `User`), and mixing the two in one class is how an
    admin-only method ends up reachable from a driver's session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- reads -------------------------------------------------------------- #

    async def get_profile(self, user_id: uuid.UUID) -> DriverProfile | None:
        """Thin wrapper so this service keeps its own seam for tests.

        Delegates to `DriverProfile.for_user`, which is the one place the query
        lives. The method is kept rather than replaced at the call sites because
        `require_profile` below turns the `None` into a domain error, and tests
        patch this seam.
        """
        return await DriverProfile.for_user(self.session, user_id)

    async def require_profile(self, user_id: uuid.UUID) -> DriverProfile:
        profile = await self.get_profile(user_id)
        if profile is None:
            raise BusinessRuleError("register as a driver before submitting a licence")
        if profile.status == DriverStatus.TERMINATED:
            raise BusinessRuleError("this driver account is terminated")
        return profile

    async def current_submission(
        self, driver_profile_id: uuid.UUID
    ) -> DriverLicenceSubmission | None:
        """The open submission, or None.

        Reads through the same `PENDING` predicate the partial unique index is
        built on, so "there can only be one" holds in the query and in the
        schema rather than only in a comment.
        """
        return (
            (
                await self.session.execute(
                    select(DriverLicenceSubmission)
                    .options(selectinload(DriverLicenceSubmission.documents))
                    .where(
                        DriverLicenceSubmission.driver_profile_id == driver_profile_id,
                        DriverLicenceSubmission.status == LicenceReviewStatus.PENDING,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def history(self, driver_profile_id: uuid.UUID, limit: int = 20) -> list[SubmissionOut]:
        """Newest first, including the open one.

        This is what tells a driver "your renewal is pending" versus "you have
        never submitted" — and after a rejection, it is where the reason lives.
        """
        rows = (
            (
                await self.session.execute(
                    select(DriverLicenceSubmission)
                    .options(selectinload(DriverLicenceSubmission.documents))
                    .where(DriverLicenceSubmission.driver_profile_id == driver_profile_id)
                    .order_by(DriverLicenceSubmission.submitted_at.desc())
                    .limit(max(1, min(limit, 100)))
                )
            )
            .scalars()
            .all()
        )
        return [_out(r) for r in rows]

    async def get_submission(
        self, driver_profile_id: uuid.UUID, submission_id: uuid.UUID
    ) -> DriverLicenceSubmission:
        """One submission, scoped to its owner.

        The `driver_profile_id` predicate is the authorisation check, not a
        filter: without it, any driver could read another's submission by id.
        """
        row = (
            (
                await self.session.execute(
                    select(DriverLicenceSubmission)
                    .options(selectinload(DriverLicenceSubmission.documents))
                    .where(
                        DriverLicenceSubmission.id == submission_id,
                        DriverLicenceSubmission.driver_profile_id == driver_profile_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise BusinessRuleError("submission not found")
        return row

    # -- presigned upload ---------------------------------------------------- #

    async def presign_document_upload(
        self,
        *,
        user_id: uuid.UUID,
        kind: DocumentKind,
        content_type: str,
        size_bytes: int,
    ) -> dict[str, Any]:
        """Hand the client a URL to upload one document to.

        Deliberately does NOT create a `DriverDocument` row. A row created here
        would exist before any bytes did, so a client that requested a URL and
        then failed would leave an attachment pointing at nothing — and the
        approve path would have to defend against it. The row is created when
        the client declares the upload (`attach_document`), where existence can
        be checked.
        """
        profile = await self.require_profile(user_id)

        # The kind is validated here rather than trusted from the client: the
        # completion check counts REQUIRED_DOCUMENT_KINDS, so an unrecognised
        # kind would silently leave a submission unsatisfiable.
        if not isinstance(kind, DocumentKind):
            raise BusinessRuleError(f"unknown document kind: {kind}")

        storage = get_storage_service()
        presigned = storage.presign_document(
            driver_profile_id=str(profile.id),
            content_type=content_type,
            size_bytes=size_bytes,
        )
        return {
            "upload_url": presigned.upload_url,
            "object_key": presigned.object_key,
            "expires_in": presigned.expires_in,
            "headers": presigned.headers,
            "kind": kind.value,
            "storage_configured": bool(storage.is_configured),
        }

    async def attach_document(
        self,
        *,
        submission: DriverLicenceSubmission,
        kind: DocumentKind,
        object_key: str,
        content_type: str,
        size_bytes: int,
    ) -> DriverDocument:
        """Record an object the client says it just uploaded.

        Verifies rather than trusts, on three axes:

        1. **The key is one we minted for this driver.** The prefix is
           `documents/<profile_id>/`, so a key belonging to another driver — or
           a hand-written key — is refused. Without this check, a driver could
           attach someone else's document and be approved on their evidence.
        2. **The object exists**, via `head_object`. A declared upload that never
           landed must not become approvable evidence.
        3. **The stored size and type match the declaration.** R2 reports what
           it actually holds; a client that lied about a 50 MB file, or stored
           HTML where an image was promised, is caught here.

        In dev/test, where storage is unconfigured, `head` returns None and the
        existence check is skipped — the flow stays exercisable, and the risk is
        confined to an environment with no bucket to abuse.
        """
        if kind not in REQUIRED_DOCUMENT_KINDS and kind != DocumentKind.OTHER:
            raise BusinessRuleError(f"unsupported document kind: {kind.value}")

        expected_prefix = f"documents/{submission.driver_profile_id}/"
        if not object_key.startswith(expected_prefix):
            raise BusinessRuleError("object key does not belong to this driver")
        if ".." in object_key.split("/"):
            raise BusinessRuleError("object key must not contain '..'")

        ct = (content_type or "").strip().lower()
        if ct not in ALLOWED_IMAGE_TYPES:
            raise BusinessRuleError(f"unsupported image type: {content_type or '(empty)'}")
        if size_bytes <= 0 or size_bytes > MAX_DOCUMENT_BYTES:
            raise BusinessRuleError(
                f"file is too large (limit {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB)"
            )

        storage = get_storage_service()
        head = storage.head(object_key=object_key)
        if head is not None:
            stored_size = int(head.get("size_bytes") or 0)
            stored_type = (head.get("content_type") or "").lower()
            if stored_size <= 0:
                raise BusinessRuleError("the uploaded file is empty")
            if stored_size > MAX_DOCUMENT_BYTES:
                raise BusinessRuleError("the uploaded file exceeds the size limit")
            if stored_type and stored_type != ct:
                raise BusinessRuleError(
                    "the uploaded file's content type does not match what was declared"
                )
            size_bytes = stored_size

        # Fetch the existing rows for this submission **directly** rather than
        # reading `submission.documents`. On a freshly-inserted submission that
        # relationship is not loaded, and touching it triggers a lazy load that
        # raises `MissingGreenlet` under asyncio. Querying also sees rows written
        # earlier in the same request, which the relationship would not.
        existing_rows = (
            (
                await self.session.execute(
                    select(DriverDocument).where(
                        DriverDocument.submission_id == submission.id,
                        DriverDocument.kind == kind,
                    )
                )
            )
            .scalars()
            .all()
        )
        # One row per kind. `all()` + a loop rather than `first()`: two rows of
        # the same kind would already mean an earlier bug, and silently updating
        # one of them would leave the operator's view ambiguous.
        if existing_rows:
            keep = existing_rows[0]
            keep.object_key = object_key
            keep.content_type = ct
            keep.size_bytes = size_bytes
            keep.uploaded_at = datetime.now(UTC)
            for duplicate in existing_rows[1:]:
                await self.session.delete(duplicate)
            await self.session.flush()
            return keep

        doc = DriverDocument(
            submission_id=submission.id,
            kind=kind,
            object_key=object_key,
            content_type=ct,
            size_bytes=size_bytes,
            uploaded_at=datetime.now(UTC),
        )
        self.session.add(doc)
        await self.session.flush()
        return doc

    # -- submission ---------------------------------------------------------- #

    async def submit(
        self,
        *,
        user_id: uuid.UUID,
        licence_no: str,
        expires_on: datetime,
        documents: list[dict[str, str]],
        note: str | None = None,
    ) -> SubmissionOut:
        """Open a submission for review.

        Refuses a second open submission rather than queueing it: the partial
        unique index would reject the INSERT anyway, and a 500 from a constraint
        violation is a worse answer than a message the client can render.

        `documents` is the list of keys the client already uploaded. Order does
        not matter; missing kinds are refused, because a submission the operator
        cannot decide on is a submission that sits in the queue.
        """
        profile = await self.require_profile(user_id)

        now = datetime.now(UTC)
        if await self.current_submission(profile.id) is not None:
            raise BusinessRuleError(
                "a licence submission is already awaiting review",
                {"hint": "wait for the decision, or withdraw and resubmit"},
            )

        if await self._submissions_today(profile.id, now) >= MAX_SUBMISSIONS_PER_DAY:
            raise BusinessRuleError("too many submissions today; try again tomorrow")

        licence_no = self._clean_licence_no(licence_no)

        if expires_on.tzinfo is None:
            # A naive datetime is treated as UTC rather than as local time. The
            # alternative — guessing the server's timezone — makes the same
            # request behave differently on two hosts, and an expiry is compared
            # against a tz-aware `now()`.
            expires_on = expires_on.replace(tzinfo=UTC)
        if expires_on <= now + timedelta(days=MIN_REMAINING_VALIDITY_DAYS):
            raise BusinessRuleError(
                "this licence expires too soon to be accepted",
                {
                    "expires_on": expires_on.date().isoformat(),
                    "minimum_remaining_days": MIN_REMAINING_VALIDITY_DAYS,
                },
            )

        by_kind = self._index_documents(documents)
        missing = [k.value for k in REQUIRED_DOCUMENT_KINDS if k not in by_kind]
        if missing:
            raise BusinessRuleError(
                "licence and taxi driver pass are both required",
                {"missing": missing},
            )

        submission = DriverLicenceSubmission(
            driver_profile_id=profile.id,
            licence_no=licence_no,
            expires_on=expires_on,
            status=LicenceReviewStatus.PENDING,
            submitted_note=(note or "").strip()[:500] or None,
        )
        self.session.add(submission)
        await self.session.flush()

        for kind, doc_in in by_kind.items():
            await self.attach_document(
                submission=submission,
                kind=kind,
                object_key=doc_in["object_key"],
                content_type=doc_in["content_type"],
                size_bytes=int(doc_in["size_bytes"]),
            )

        # Re-read through a `selectinload` query rather than `session.refresh`:
        # refresh does not eagerly load a relationship, so the object would come
        # back with `documents` unloaded and `_out` would raise.
        await self.session.flush()
        loaded = await self._reload(submission.id)
        logger.info(
            "licence submission opened driver=%s submission=%s expires=%s",
            profile.id,
            submission.id,
            expires_on.date().isoformat(),
        )
        return _out(loaded)

    async def _reload(self, submission_id: uuid.UUID) -> DriverLicenceSubmission:
        """Re-read a submission with its documents loaded.

        Every path that returns a `SubmissionOut` goes through here, so the
        eager-load requirement lives in one place instead of at each call site.
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
        if row is None:  # pragma: no cover — the row was just flushed
            raise BusinessRuleError("submission not found")
        return row

    async def withdraw(self, *, user_id: uuid.UUID, submission_id: uuid.UUID) -> SubmissionOut:
        """A driver cancels their own pending submission.

        This is what makes a mistaken submission recoverable without an
        operator: the driver fixes the photo and resubmits rather than waiting
        for a rejection. It becomes `SUPERSEDED`, not deleted, so the record of
        what was withdrawn survives — and so a driver cannot erase evidence of
        an attempt that was already examined.
        """
        profile = await self.require_profile(user_id)
        sub = await self.get_submission(profile.id, submission_id)
        if sub.status != LicenceReviewStatus.PENDING:
            raise BusinessRuleError(
                f"this submission is already {sub.status.value.lower()} and cannot be withdrawn"
            )
        sub.status = LicenceReviewStatus.SUPERSEDED
        sub.reviewed_at = datetime.now(UTC)
        await self.session.flush()
        logger.info("licence submission withdrawn submission=%s", sub.id)
        return _out(await self._reload(sub.id))

    # -- helpers ------------------------------------------------------------- #

    async def _submissions_today(self, driver_profile_id: uuid.UUID, now: datetime) -> int:
        start = now - timedelta(hours=24)
        rows = (
            (
                await self.session.execute(
                    select(DriverLicenceSubmission.id).where(
                        DriverLicenceSubmission.driver_profile_id == driver_profile_id,
                        DriverLicenceSubmission.submitted_at >= start,
                    )
                )
            )
            .scalars()
            .all()
        )
        return len(rows)

    @staticmethod
    def _clean_licence_no(raw: str) -> str:
        """Uppercase and validate.

        Uppercasing is normalisation, not cosmetics: HK licence numbers are
        issued in a single case, and a driver typing `ad1234` on a phone
        keyboard against a stored `AD1234` produces two different identifiers
        for the same licence — which is exactly the collision an operator
        checking for a duplicate run needs to see.
        """
        import re

        value = (raw or "").strip().upper()
        if not re.match(LICENCE_NO_RE, value):
            raise BusinessRuleError(
                "licence number must be 5-32 characters of letters, digits or dashes"
            )
        return value

    @staticmethod
    def _index_documents(documents: list[dict[str, str]]) -> dict[DocumentKind, dict[str, str]]:
        """Key the client's document list by kind, refusing duplicates.

        A duplicate kind is not "last one wins": the client sending two
        `DRIVER_LICENCE` entries means its own state is inconsistent, and
        silently picking one would have the operator approve an image the driver
        did not intend.
        """
        out: dict[DocumentKind, dict[str, str]] = {}
        for item in documents or []:
            raw_kind = (item.get("kind") or "").strip().upper()
            try:
                kind = DocumentKind(raw_kind)
            except ValueError as exc:
                raise BusinessRuleError(f"unknown document kind: {item.get('kind')!r}") from exc
            if kind in out:
                raise BusinessRuleError(f"duplicate document kind: {kind.value}")
            for field in ("object_key", "content_type", "size_bytes"):
                if not item.get(field):
                    raise BusinessRuleError(f"document {kind.value} is missing {field}")
            out[kind] = item
        return out


__all__ = [
    "LICENCE_NO_RE",
    "MAX_SUBMISSIONS_PER_DAY",
    "MIN_REMAINING_VALIDITY_DAYS",
    "REQUIRED_DOCUMENT_KINDS",
    "LicenceService",
    "SubmissionOut",
]
