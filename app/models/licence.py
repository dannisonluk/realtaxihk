"""P-3: driver licence verification (manual, admin-decided).

Split out of the original single `app/models/__init__.py`; every public name is
re-exported from `app.models`, so no call site changed.

Bounded context: the *documents* a driver submits to prove the right to drive a
taxi, and the admin decision on each. Kept apart from `DriverProfile`
(`app.models.user`) because a submission is a recurring event with its own
lifecycle and history, not a property of the driver — see
`DriverLicenceSubmission` for the reasoning. The link between the two is the
`driver_profile_id` FK plus a back-populated relationship on each side.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models._base import Base

__all__ = [
    "DocumentKind",
    "DriverDocument",
    "DriverLicenceSubmission",
    "LicenceReviewStatus",
]


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class LicenceReviewStatus(str, enum.Enum):
    """The lifecycle of one licence submission (P-3).

    Deliberately NOT the same enum as `DriverStatus`. A driver can be ACTIVE and
    trading while a *renewal* submission sits PENDING — coupling the two would
    force a trading driver offline to re-upload a document, which is exactly the
    behaviour that makes drivers abandon a platform.

    `SUPERSEDED` is a real terminal state, not a delete: when a driver
    re-submits after a rejection, the old row must stay for the audit trail. An
    admin acting on the wrong submission is a real failure mode, and a deleted
    row makes it undiagnosable.
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class DocumentKind(str, enum.Enum):
    """What a stored object actually is.

    `DRIVER_LICENCE` and `TAXI_DRIVER_PASS` are distinct on purpose: the HK taxi
    driver pass (的士司機證) is issued by the Transport Department and proves the
    right to drive a taxi, while the driving licence proves the right to drive.
    An operator checking "is this a real taxi driver" needs the first. One field
    for both would silently accept either.
    """

    DRIVER_LICENCE = "DRIVER_LICENCE"
    # `noqa: S105` — bandit matches the `_PASS` suffix and reads this as a
    # hardcoded password. It is a document kind (the HK 的士司機證), and the
    # value is an enum member, not a credential.
    TAXI_DRIVER_PASS = "TAXI_DRIVER_PASS"  # noqa: S105
    VEHICLE_REGISTRATION = "VEHICLE_REGISTRATION"
    INSURANCE = "INSURANCE"
    OTHER = "OTHER"


class DriverLicenceSubmission(Base):
    """One submission of a taxi driver's licence for manual review (P-3).

    A separate table rather than columns on `driver_profiles`, for three
    reasons:

    1. **History.** HK taxi driver passes run on a fixed term, so this recurs.
       Columns would overwrite the previous decision and lose who approved what,
       and when — which is the record an operator is asked for after an incident.
    2. **The decision is about a document, not a person.** A rejection reason
       ("the expiry date is in shadow") belongs to the image, not the driver.
    3. **Status coupling.** As in `LicenceReviewStatus`: a renewal must not idle
       a trading driver.

    The full HKID is still never collected — only `hk_id_last4` on the profile —
    so the most damaging identifier cannot leak from this table at all.
    """

    __tablename__ = "driver_licence_submissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    driver_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver_profiles.id", ondelete="CASCADE"), index=True
    )
    licence_no: Mapped[str] = mapped_column(String(32))
    # A datetime, not a string: the expiry is compared against now on every
    # read, and "31/12/2027" cannot be ordered or indexed. Refusing a submission
    # that is already expired is the whole point of collecting it.
    expires_on: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[LicenceReviewStatus] = mapped_column(
        SAEnum(LicenceReviewStatus, name="licence_review_status", native_enum=False),
        default=LicenceReviewStatus.PENDING,
        index=True,
    )
    # The driver's own note at submission ("renewal", "new pass").
    submitted_note: Mapped[str | None] = mapped_column(String(500))
    # Admin decision. `reviewed_by` is an admin ACCOUNT id (admin_accounts), not
    # a user id — the two are different tables on purpose (P-1), and a decision
    # is always attributable to a console operator.
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    driver_profile: Mapped[DriverProfile] = relationship(  # noqa: F821
        back_populates="licence_submissions"
    )
    documents: Mapped[list[DriverDocument]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )


# At most one *open* submission per driver. Partial, not plain: decided and
# superseded rows accumulate forever, so a plain unique constraint would make
# the second submission impossible rather than the second OPEN one impossible.
Index(
    "uq_licence_one_open_per_driver",
    DriverLicenceSubmission.driver_profile_id,
    unique=True,
    postgresql_where=text("status = 'PENDING'"),
)


class DriverDocument(Base):
    """An uploaded object key, tied to a licence submission (P-3).

    Only the **object key** is stored — never the bytes, and never a URL. The
    key is a reference into R2 and URLs are signed on read with a short TTL, so
    a leaked database row (a backup, a replica, a bad log) does not yield a
    downloadable copy of someone's identity document. Same reasoning as
    `avatar_key` being a key rather than a URL.

    `content_type` and `size_bytes` are recorded so the read path can refuse to
    sign something that is not the image it claims to be, and so an operator can
    see at a glance that a 40 MB "scan" is not a scan.
    """

    __tablename__ = "driver_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("driver_licence_submissions.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[DocumentKind] = mapped_column(
        SAEnum(DocumentKind, name="document_kind", native_enum=False)
    )
    object_key: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    submission: Mapped[DriverLicenceSubmission] = relationship(back_populates="documents")
