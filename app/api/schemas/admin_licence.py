"""Admin licence review schemas — the queue, the detail view, the decision.

The admin view of a licence submission is **much wider** than the driver's. The
queue row joins in the driver's trading status and adds two triage fields
(`document_count`, `has_required_documents`) so an operator can pick a row
without opening it; the detail view adds the driver's identifiers and, crucially,
**short-lived signed download URLs**.

Signed URLs appear here and nowhere else. Putting them on the queue would leave
a live link to an identity document in every poll of the list — and in whatever
that response is logged into. `_DocumentWithUrl` therefore extends the
driver-facing document only on this side, so the two contracts cannot be
confused.

`LicenceDecisionOut` carries `driver_promoted` because an approval can move a
driver out of `PENDING_KYC` — and the operator should see that it happened
without a follow-up request. On a renewal it is `false` and nothing about the
driver's trading status changed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "LicenceDecisionOut",
    "LicenceQueueOut",
    "LicenceQueueRowOut",
    "LicenceReviewDetailOut",
    "ReviewDocumentOut",
]


class ReviewDocumentOut(BaseModel):
    """One document on the review detail page.

    Extends the driver-facing document with the evidence checks an operator
    needs: `download_url` (short-lived, signed), how long it lasts, and whether
    storage actually holds the object. `stored` is `false` for a
    declared-but-never-uploaded row — surfacing that is what makes the approve
    button safe to press.
    """

    id: str
    kind: str
    content_type: str
    size_bytes: int
    uploaded_at: str | None
    download_url: str
    url_expires_in: int
    stored: bool
    stored_size_bytes: int | None


class LicenceQueueRowOut(BaseModel):
    """One row of the review queue.

    Deliberately **narrower** than the detail view: it carries counts and a
    boolean instead of the documents themselves, because the queue is a page an
    operator leaves open and the documents are one click away.

    `driver_status` is joined in rather than loaded per row — it is one column
    from the profile, and eager-loading the whole row per item would turn a
    50-row page into 50 extra selects. It is nullable because the profile is
    looked up in a separate query and a row whose profile has since been removed
    would otherwise be a KeyError rather than a visible gap.
    """

    id: str
    licence_no: str
    expires_on: str
    status: str
    submitted_note: str | None
    rejection_reason: str | None
    submitted_at: str
    reviewed_at: str | None
    driver_profile_id: str
    driver_status: str | None
    document_count: int
    has_required_documents: bool


class LicenceQueueOut(BaseModel):
    """`GET /admin/licence/submissions` — offset-paginated queue.

    `total` counts rows matching the **same status filter** as the page, not the
    whole table: the console renders "N pending" from it, and a total that
    ignored the filter would misreport the backlog.
    """

    items: list[LicenceQueueRowOut]
    total: int
    limit: int
    offset: int


class LicenceReviewDetailOut(BaseModel):
    """`GET /admin/licence/submissions/{id}` — everything needed to decide.

    Extends the submission with the driver's identifiers (`driver_taxi_type`,
    `driver_plate_no`, `driver_vehicle_reg_mark`) so an operator can check the
    licence against the vehicle on the road, and with the reviewer and the
    evidence verdict.

    The four `driver_*` fields are all nullable: the profile is fetched
    separately and may be gone, in which case the page still renders the
    submission rather than 500-ing.
    """

    id: str
    licence_no: str
    expires_on: str
    status: str
    submitted_note: str | None
    rejection_reason: str | None
    submitted_at: str
    reviewed_at: str | None
    driver_profile_id: str
    driver_status: str | None
    driver_taxi_type: str | None
    driver_plate_no: str | None
    driver_vehicle_reg_mark: str | None
    reviewed_by: str | None
    has_required_documents: bool
    documents: list[ReviewDocumentOut]


class LicenceDecisionOut(BaseModel):
    """`POST /admin/licence/submissions/{id}/decide` — the terminal decision.

    `driver_promoted` is `false` on every decision except an approval of a
    `PENDING_KYC` driver, which is the only transition an approval performs.
    `driver_status` is absent (`None`) only when the driver profile has been
    removed — see `LicenceReviewService._promote_driver`, which returns `{}` in
    that case; the `Field(default=None)` here is what keeps that from being a
    validation error.
    """

    id: str
    status: str
    reviewed_at: str | None
    driver_status: str | None = Field(default=None)
    driver_promoted: bool = Field(default=False)
