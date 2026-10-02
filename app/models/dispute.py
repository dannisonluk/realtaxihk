"""Disputes: the record of "something went wrong and someone disagrees".

Bounded context: **conflict handled after the fact**. A dispute is not a
suspended order and not a ticket queue bolted on the side — it is the object an
operator owns, with an SLA and a resolution, for the cases the automated flow
could not settle on its own.

Two decisions shape this module, both from `docs/ADMIN_CONSOLE_DESIGN.md` §4 and
`docs/IN_TRIP_REDESIGN.md` §3.3:

1. **`order_id` is nullable.** "Complaint" is not a subset of "order": a driver
   can report an abusive passenger account with no trip attached, and a
   passenger can report an app defect. Requiring an order would force those into
   a fake trip.

2. **The resolution is its own column, not derived from the ledger.** Reversing
   a refund out of ledger rows cannot distinguish "not yet decided" from
   "decided, and the answer was NONE" — and that difference is the whole state
   of the case. So `resolution` is written explicitly, and it has no default.

`order_disputes` is deliberately one table rather than a ticket table plus a
dispute table. The design doc calls this out: two tables would immediately
diverge, and the second one would be the one nobody keeps current.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models._base import Base

__all__ = [
    "DisputeCategory",
    "DisputeMessage",
    "DisputePartyKind",
    "DisputeResolution",
    "DisputeSeverity",
    "DisputeSource",
    "DisputeStatus",
    "OrderDispute",
]


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class DisputePartyKind(str, enum.Enum):
    """Who is on each side of the disagreement.

    `PLATFORM` exists because "the fare was wrong" is a complaint about us, not
    about the other party. Modelling it as `against the driver` would put the
    fare engine's error on a driver's record.
    """

    PASSENGER = "PASSENGER"
    DRIVER = "DRIVER"
    ADMIN = "ADMIN"
    SYSTEM = "SYSTEM"
    PLATFORM = "PLATFORM"


class DisputeSource(str, enum.Enum):
    """How the case arrived. Distinct from *why* the trip ended.

    `PARTY_REPORT` is split from `AUTO_INTERRUPTED` because the two carry
    different trust: an interruption opened by the system has a machine-verified
    reason attached, whereas a party report is one side's account with no
    independent evidence yet. An operator triaging a queue needs that difference
    visible without opening the order.
    """

    AUTO_INTERRUPTED = "AUTO_INTERRUPTED"
    AUTO_INTERRUPTED_SAFETY = "AUTO_INTERRUPTED_SAFETY"
    PARTY_REPORT = "PARTY_REPORT"
    ADMIN_CREATED = "ADMIN_CREATED"
    PARTY_CANCELLED_IN_LOCK_WINDOW = "PARTY_CANCELLED_IN_LOCK_WINDOW"


class DisputeCategory(str, enum.Enum):
    """What the disagreement is about.

    Deliberately orthogonal to `DisputeSource`. A trip can be interrupted for a
    safety reason and still leave the parties arguing about the fare, which is
    exactly why `IN_TRIP_REDESIGN` §3.3 separates "why it ended" from "why
    they object".
    """

    FARE = "FARE"
    CONDUCT = "CONDUCT"
    SAFETY = "SAFETY"
    LOST_ITEM = "LOST_ITEM"
    APP_ISSUE = "APP_ISSUE"
    OTHER = "OTHER"


class DisputeSeverity(str, enum.Enum):
    """How fast this has to be answered.

    `SAFETY_CRITICAL` is the one that matters operationally: it shortens the
    SLA to an hour and makes the case a candidate for suspending the driver.
    """

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    SAFETY_CRITICAL = "SAFETY_CRITICAL"

    @property
    def sla_hours(self) -> int:
        """Hours until `sla_due_at`. Narrow at the top, generous at the bottom.

        A single SLA for every case is the failure mode this avoids: a uniform
        72 hours means a safety report waits behind three lost-umbrella
        complaints, and the number looks fine on a dashboard because nothing in
        it knows the cases are not comparable.
        """
        return _SEVERITY_SLA_HOURS[self]


_SEVERITY_SLA_HOURS: dict[DisputeSeverity, int] = {
    DisputeSeverity.SAFETY_CRITICAL: 1,
    DisputeSeverity.HIGH: 4,
    DisputeSeverity.NORMAL: 24,
    DisputeSeverity.LOW: 72,
}


class DisputeStatus(str, enum.Enum):
    """The case's state.

    `AWAITING_PARTY` exists because the clock must be stoppable: an operator
    who has asked the passenger a question should not have the case counted as
    overdue while they wait for an answer. Without it, "fastest first" sorting
    pushes every case where we are waiting on someone to the top, and the
    queue becomes unworkable.
    """

    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    AWAITING_PARTY = "AWAITING_PARTY"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_DISPUTE_STATUSES


_TERMINAL_DISPUTE_STATUSES = frozenset({DisputeStatus.RESOLVED, DisputeStatus.CLOSED})


class DisputeResolution(str, enum.Enum):
    """Where the money ends up. No default — a money decision must be explicit.

    `NONE` is a real decision ("nobody is charged"), which is why it is a member
    rather than represented by NULL. NULL is reserved for "not decided yet", and
    conflating the two would make `resolution IS NULL` unqueryable.
    """

    NONE = "NONE"
    CHARGE_PASSENGER = "CHARGE_PASSENGER"
    CHARGE_DRIVER = "CHARGE_DRIVER"
    REFUND_PLATFORM_FEE = "REFUND_PLATFORM_FEE"
    WAIVED_PLATFORM_FEE = "WAIVED_PLATFORM_FEE"

    @property
    def moves_money(self) -> bool:
        """Whether this decision requires a ledger write.

        Used to require FINANCE rather than OPERATIONS on the resolution: the
        four members that move money are a financial act, and the role split in
        `AdminRole` exists precisely so that the operator who judges conduct is
        not automatically the one who authorises the payment.
        """
        return self is not DisputeResolution.NONE


class OrderDispute(Base):
    """A case: something happened on a trip (or to an account) and it is contested.

    Not a suspended order. The order keeps its own terminal status; this row is
    the after-the-fact judgement about who bears the cost. That separation is
    what lets the trip reach a terminal state immediately instead of hanging
    while an admin is found — a passenger in a crashed taxi needs to stop, not
    to submit a request.
    """

    __tablename__ = "order_disputes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # Nullable by design: account-level and app-level complaints have no order.
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), index=True
    )

    raised_by_kind: Mapped[str] = mapped_column(
        SAEnum(
            DisputePartyKind,
            name="ck_order_disputes_raised_by_kind",
            native_enum=False,
            create_constraint=True,
            length=16,
        )
    )
    # NULL when the system opens the case (nobody raised it) or the account is
    # gone. Not an FK: the raiser may be a `users` row or an `admin_accounts`
    # row depending on `raised_by_kind`, and one column cannot reference both.
    raised_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    source: Mapped[str] = mapped_column(
        SAEnum(
            DisputeSource,
            name="ck_order_disputes_source",
            native_enum=False,
            create_constraint=True,
            length=32,
        ),
        index=True,
    )
    against_kind: Mapped[str | None] = mapped_column(
        SAEnum(
            DisputePartyKind,
            name="ck_order_disputes_against_kind",
            native_enum=False,
            create_constraint=True,
            length=16,
        )
    )
    against_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    category: Mapped[str] = mapped_column(
        SAEnum(
            DisputeCategory,
            name="ck_order_disputes_category",
            native_enum=False,
            create_constraint=True,
            length=16,
        ),
        index=True,
    )
    severity: Mapped[str] = mapped_column(
        SAEnum(
            DisputeSeverity,
            name="ck_order_disputes_severity",
            native_enum=False,
            create_constraint=True,
            length=16,
        ),
        default=DisputeSeverity.NORMAL.value,
    )
    status: Mapped[str] = mapped_column(
        SAEnum(
            DisputeStatus,
            name="ck_order_disputes_status",
            native_enum=False,
            create_constraint=True,
            length=16,
        ),
        default=DisputeStatus.OPEN.value,
        server_default="OPEN",
        index=True,
    )

    assigned_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="SET NULL"), index=True
    )

    # Text rather than enum: a free-text summary is what a human reads in the
    # queue, and it is written by whichever party opened the case.
    summary: Mapped[str] = mapped_column(Text)

    # --- Resolution. All nullable, because "undecided" is the initial state. ---
    # No default and no `server_default`: a money decision that arrives by
    # omission is the exact bug this column exists to prevent.
    resolution: Mapped[str | None] = mapped_column(
        SAEnum(
            DisputeResolution,
            name="ck_order_disputes_resolution",
            native_enum=False,
            create_constraint=True,
            length=24,
        )
    )
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_accounts.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # `SAFETY_CRITICAL` flags a case as a candidate for suspending the driver.
    # It is a *flag*, never an action: auto-suspending on a report means one
    # false accusation takes a driver off the road with no hearing, which is a
    # worse failure than a slow response. A human makes that call.
    safety_flag: Mapped[bool] = mapped_column(Boolean, default=False)

    sla_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[DisputeMessage]] = relationship(
        back_populates="dispute",
        cascade="all, delete-orphan",
        order_by="DisputeMessage.created_at",
    )

    __table_args__ = (
        # The queue's hot path. The list view filters to non-terminal cases and
        # sorts by SLA ascending ("almost overdue first"), which is exactly this
        # pair of columns. Sorting by `created_at` instead would bury a
        # safety-critical case behind older routine ones — the design doc calls
        # this out as a deliberately different default from "newest first".
        Index("ix_order_disputes_queue", "status", "sla_due_at"),
        # "My cases" — an operator's own queue.
        Index("ix_order_disputes_assignee", "assigned_admin_id", "status"),
    )

    @property
    def status_enum(self) -> DisputeStatus:
        """Failing *open*, unlike `AdminAccount.admin_role` which fails closed.

        An unreadable status must not hide a case: assuming it is terminal
        would drop it out of the queue silently, and a lost safety complaint is
        the failure mode with the worst outcome. An unrecognised value becomes
        `OPEN`, which over-reports rather than under-reports.
        """
        try:
            return DisputeStatus(self.status)
        except ValueError:
            return DisputeStatus.OPEN

    @property
    def severity_enum(self) -> DisputeSeverity:
        try:
            return DisputeSeverity(self.severity)
        except ValueError:
            return DisputeSeverity.NORMAL


class DisputeMessage(Base):
    """One turn in the conversation: a party's account, or a note between staff.

    `is_internal` is the reason this is one table and not two. An internal note
    ("this is the third complaint about the same driver this month") belongs
    beside the conversation it is about, or the context is lost — but it must
    never be shown to the parties. A flag with an explicit filter on the read
    path is the honest shape; two tables would need joining to reconstruct a
    single thread anyway.
    """

    __tablename__ = "dispute_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dispute_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("order_disputes.id", ondelete="CASCADE"), index=True
    )
    author_kind: Mapped[str] = mapped_column(
        SAEnum(
            DisputePartyKind,
            name="ck_dispute_messages_author_kind",
            native_enum=False,
            create_constraint=True,
            length=16,
        )
    )
    # NULL for `system` messages (an automated note on the timeline).
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Denormalised: the author may be deleted or renamed, and the thread must
    # still read correctly. Same reasoning as `username_attempted` on the audit
    # log.
    author_label: Mapped[str | None] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    # Staff-only. Filtered out of anything a party can read.
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    dispute: Mapped[OrderDispute] = relationship(back_populates="messages")

    __table_args__ = (Index("ix_dispute_messages_thread", "dispute_id", "created_at"),)
