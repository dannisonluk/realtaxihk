"""Driver licence schemas (P-3) — upload presign, submission, review state.

The licence flow moves **no document bytes through the API**: the client PUTs
straight to R2 against a presigned URL and then names the key it used. Two
consequences show up here:

- `PresignedUploadOut.headers` is a **map, not a fixed shape**. The signing
  layer decides which headers must be echoed (`Content-Type` always; others
  depend on the storage backend), and a client that dropped them would have its
  PUT rejected by the bucket. Declaring them as named fields would force this
  contract to change whenever that set does.
- `LicenceDocumentOut` carries `object_key` and never a signed URL. Signing
  happens on a separate, short-lived, auditable endpoint; baking a working link
  into every list response would leak it into any log that captured the payload.

`LicenceSubmissionOut` is `SubmissionOut.as_dict()` from
`app/services/licence/licence_service.py` — the driver's view of one submission. Its
`documents` are already serialised dicts (not a second model), because the
service is the single place that decides a document's wire form and duplicating
that decision here is how the list and detail views would drift apart.
"""

from __future__ import annotations

from pydantic import BaseModel

__all__ = [
    "LicenceDocumentOut",
    "LicenceListOut",
    "LicenceSubmissionOut",
    "PresignedUploadOut",
]


class PresignedUploadOut(BaseModel):
    """`POST /drivers/licence/uploads` — a URL to PUT one document to.

    `expires_in` is seconds. `storage_configured` reports whether a real bucket
    backs the URL: in the dev backend it is `false` and the URL is deliberately
    non-functional, so a client cannot mistake a stub for a working upload.
    """

    upload_url: str
    object_key: str
    expires_in: int
    headers: dict[str, str]
    kind: str
    storage_configured: bool


class LicenceDocumentOut(BaseModel):
    """One attached document, as the driver sees it.

    `object_key`, not a URL — see the module docstring. `uploaded_at` is
    nullable because a row can exist before storage confirms the object landed.
    """

    id: str
    kind: str
    content_type: str
    size_bytes: int
    uploaded_at: str | None


class LicenceSubmissionOut(BaseModel):
    """One licence submission (`SubmissionOut.as_dict()`).

    `rejection_reason` is what makes a rejected submission actionable, and it is
    null on every non-rejected row — hence optional rather than an empty string,
    which would be indistinguishable from an operator rejecting with no comment.

    `documents` is typed as `list[LicenceDocumentOut]` rather than `list[dict]`:
    the service fills it from `_clean()`, whose keys are exactly that model, and
    an untyped list publishes `items: {}` — i.e. the one part of this response a
    client actually needs to render (the document list) would be undocumented.

    `reviewed_by` is deliberately absent — see `SubmissionOut` in
    `app/services/licence/licence_service.py`: an operator's internal id is not the
    driver's business.
    """

    id: str
    licence_no: str
    expires_on: str
    status: str
    submitted_note: str | None
    rejection_reason: str | None
    submitted_at: str
    reviewed_at: str | None
    documents: list[LicenceDocumentOut]


class LicenceListOut(BaseModel):
    """`GET /drivers/licence/submissions` — history plus the flow's constraints.

    Not a plain list: alongside the submissions it publishes the rules the client
    needs to render the form — `current_submission_id` (which one is open, if
    any), `driver_status`, the daily cap, and which document kinds are required.
    Sending them here is what stops the app hard-coding policy that the server
    can change.

    `required_document_kinds` is a list of strings rather than a set, and the
    order is meaningful: the client renders the checklist in this order.
    """

    items: list[LicenceSubmissionOut]
    current_submission_id: str | None
    driver_status: str
    max_submissions_per_rolling_24h: int
    required_document_kinds: list[str]
