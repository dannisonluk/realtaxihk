"""Order events: the per-order timeline.

Why this is not `AdminAuditLog`
-------------------------------
They answer different questions and are read by different people. The audit log
answers **"which operator did what"** — it is about staff accountability. This
table answers **"what happened to this trip"** — both parties, the state
changes, the destination edits, the fees, the arrival proof. An operator
judging a dispute reads the second, and that question has no operator in it at
all.

The design doc (`docs/IN_TRIP_REDESIGN.md` §3.4) calls this "optional but
recommended". It is not optional in practice: once `INTERRUPTED` and
`DESTINATION_CHANGED` exist, "how did this order reach this state" is a
question the dispute queue asks on every case, and reconstructing it from the
mutable `orders` row alone is impossible — the row only holds the latest value.

Append-only, like the ledger. A correction is a new event, never an edit; a
timeline that can be rewritten is not evidence.
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
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models._base import Base

__all__ = ["OrderEvent", "OrderEventType"]


class OrderEventType(str, enum.Enum):
    """What kind of thing happened. A closed set so the timeline is filterable.

    `native_enum=False` with a CHECK constraint, the repo-wide convention — see
    the note on `LedgerEntryType`.
    """

    STATE_CHANGED = "STATE_CHANGED"
    ARRIVAL_CLAIMED = "ARRIVAL_CLAIMED"
    ARRIVAL_CONFIRMED = "ARRIVAL_CONFIRMED"
    DEST_CHANGED = "DEST_CHANGED"
    FEE_CHARGED = "FEE_CHARGED"
    PENALTY_CHARGED = "PENALTY_CHARGED"
    INTERRUPTED = "INTERRUPTED"
    DISPUTE_OPENED = "DISPUTE_OPENED"
    PREBOOK_LANDMARK_CHOSEN = "PREBOOK_LANDMARK_CHOSEN"
    PREBOOK_UPGRADED = "PREBOOK_UPGRADED"
    PREBOOK_MATCHED = "PREBOOK_MATCHED"


class OrderEvent(Base):
    """One line in an order's timeline. Append-only."""

    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), index=True
    )
    event: Mapped[OrderEventType] = mapped_column(
        SAEnum(
            OrderEventType,
            name="ck_order_events_event",
            native_enum=False,
            create_constraint=True,
            length=32,
        )
    )
    # Plain strings, not FKs to an enum: this is a historical record of what the
    # status *was*, and a status that is later renamed or removed must not make
    # the row unreadable.
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    # 'PASSENGER' | 'DRIVER' | 'ADMIN' | 'SYSTEM' — the same vocabulary as
    # `DisputePartyKind`, so a timeline and a dispute read alike.
    actor_kind: Mapped[str | None] = mapped_column(String(16))
    # Not an FK: the actor may be a `users` row or an `admin_accounts` row
    # depending on `actor_kind`, and one column cannot reference both. Same
    # reasoning as `OrderDispute.raised_by_id`.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Free-form detail: old/new destination, the amount charged, the reason.
    # JSONB because the shape differs per event type and a column per shape
    # would be mostly NULL.
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        # The only read pattern: one order's timeline, oldest first.
        Index("ix_order_events_order_time", "order_id", "created_at"),
    )
