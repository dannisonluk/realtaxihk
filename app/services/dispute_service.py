"""Dispute handling — the after-the-fact judgement about who bears a cost.

The whole reason this exists is a timing argument from
`docs/IN_TRIP_REDESIGN.md` §2.1: a trip that ends badly must reach a terminal
state *immediately*, because a passenger in a crashed taxi needs to stop, not to
submit a request and wait for an administrator. So the trip ends, and the
question of who pays is answered afterwards, by a human, here.

That ordering creates the job this service has: make sure the afterwards
actually happens. `open_for_interruption` exists so a case is created by the
system in the same breath as the event, rather than depending on a party
remembering to complain once the adrenaline has worn off. The design doc is
blunt about it — "出事那一刻人人都忙，之後就冇人記得".

Two rules are enforced structurally rather than by convention:

1. **Resolution is explicit and irreversible-in-practice.** `resolve` refuses a
   second resolution. A case whose money decision can be silently overwritten
   is a case where the first decision does not matter.

2. **A resolution that moves money needs FINANCE.** Enforced by the caller (the
   route dependency), but the reason is recorded here: judging whether a driver
   behaved badly and deciding whether to pay out are separate authorities, and
   merging them is how "approved by operations" becomes a payment authorisation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import BusinessRuleError, NotFoundError
from app.models import (
    DisputeCategory,
    DisputeMessage,
    DisputeResolution,
    DisputeSeverity,
    DisputeSource,
    DisputeStatus,
    OrderDispute,
)

logger = logging.getLogger("realtaxihk.disputes")

# Severities that should raise the safety flag. Kept as a set beside the enum
# rather than a property on it, so that "which severities are safety" is one
# visible list an operator can disagree with, not a predicate buried in a model.
_SAFETY_SEVERITIES = frozenset({DisputeSeverity.SAFETY_CRITICAL, DisputeSeverity.HIGH})

# Categories that are inherently safety-adjacent, independent of the severity
# the reporter chose. A party who files a `SAFETY` complaint and marks it `LOW`
# is a real and common pattern — the classification must not let that downgrade
# the case out of the queue it belongs in.
_SAFETY_CATEGORIES = frozenset({DisputeCategory.SAFETY})

_OPEN_STATUSES = (
    DisputeStatus.OPEN,
    DisputeStatus.INVESTIGATING,
    DisputeStatus.AWAITING_PARTY,
    DisputeStatus.ESCALATED,
)


class DisputeService:
    """Case management: open, assign, message, resolve, list."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------ open

    async def open_case(
        self,
        *,
        category: DisputeCategory | str,
        summary: str,
        source: DisputeSource | str = DisputeSource.ADMIN_CREATED,
        order_id: uuid.UUID | None = None,
        raised_by_kind: str = "ADMIN",
        raised_by_id: uuid.UUID | None = None,
        against_kind: str | None = None,
        against_id: uuid.UUID | None = None,
        severity: DisputeSeverity | str = DisputeSeverity.NORMAL,
        now: datetime | None = None,
    ) -> OrderDispute:
        """Create a case. SLA is derived from severity, never passed in.

        Taking `sla_due_at` as a parameter would let a caller set a longer
        deadline than the severity implies, which is exactly the pressure a
        busy queue applies. Deriving it means the only way to extend a deadline
        is to lower the severity — which is a visible, audited change of the
        case's importance rather than a quiet edit of its clock.
        """
        now = now or datetime.now(UTC)
        sev = DisputeSeverity(severity)
        cat = DisputeCategory(category)

        if not summary or not summary.strip():
            raise BusinessRuleError(
                "a dispute must carry a summary — an untitled case cannot be triaged",
                {"reason": "DISPUTE_SUMMARY_REQUIRED"},
            )

        safety = sev in _SAFETY_SEVERITIES or cat in _SAFETY_CATEGORIES
        dispute = OrderDispute(
            order_id=order_id,
            raised_by_kind=raised_by_kind,
            raised_by_id=raised_by_id,
            source=DisputeSource(source).value,
            against_kind=against_kind,
            against_id=against_id,
            category=cat.value,
            severity=sev.value,
            status=DisputeStatus.OPEN.value,
            summary=summary.strip(),
            safety_flag=safety,
            sla_due_at=now + timedelta(hours=sev.sla_hours),
        )
        async with self._session_factory() as session:
            session.add(dispute)
            # Flush before wiring the message to it. `id` is a Python-side
            # default, so it is still `None` in memory until the INSERT runs —
            # adding the message first binds a NULL `dispute_id` and the write
            # fails on the NOT NULL constraint.
            await session.flush()
            # The opening summary is also the first message, so the thread is
            # complete on its own — a reader of `dispute_messages` alone sees
            # how the case started without joining back to the row.
            session.add(
                DisputeMessage(
                    dispute_id=dispute.id,
                    author_kind=raised_by_kind,
                    author_id=raised_by_id,
                    body=summary.strip(),
                    is_internal=False,
                )
            )
            await session.commit()
            await session.refresh(dispute)
        logger.info(
            "dispute opened",
            extra={"dispute": str(dispute.id), "category": cat.value, "severity": sev.value},
        )
        return dispute

    async def open_for_interruption(
        self,
        session: AsyncSession,
        *,
        order_id: uuid.UUID,
        interrupted_by_kind: str,
        against_id: uuid.UUID | None,
        safety: bool,
        summary: str,
        raised_by_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> OrderDispute:
        """Open a case on the *caller's* session, for the same transaction.

        Deliberately not `open_case`: this one takes the session rather than
        opening its own, because the design requires the case to be created in
        the **same transaction** as the interruption. A separate session would
        commit separately, and a crash between the two leaves an interrupted
        trip that nobody is accountable for — the exact "之後就冇人記得" failure
        the automatic opening is meant to prevent.
        """
        now = now or datetime.now(UTC)
        sev = DisputeSeverity.SAFETY_CRITICAL if safety else DisputeSeverity.NORMAL
        # The complaint is against the *other* party: whoever interrupted is
        # the one asserting something went wrong with the counterparty.
        against_kind = (
            "DRIVER"
            if interrupted_by_kind == "PASSENGER"
            else "PASSENGER"
            if interrupted_by_kind == "DRIVER"
            else "PLATFORM"
        )
        dispute = OrderDispute(
            order_id=order_id,
            raised_by_kind="SYSTEM",
            raised_by_id=None,
            source=(
                DisputeSource.AUTO_INTERRUPTED_SAFETY.value
                if safety
                else DisputeSource.AUTO_INTERRUPTED.value
            ),
            against_kind=against_kind,
            against_id=against_id,
            category=DisputeCategory.SAFETY.value if safety else DisputeCategory.OTHER.value,
            severity=sev.value,
            status=DisputeStatus.OPEN.value,
            summary=summary,
            safety_flag=safety,
            sla_due_at=now + timedelta(hours=sev.sla_hours),
        )
        session.add(dispute)
        await session.flush()
        session.add(
            DisputeMessage(
                dispute_id=dispute.id,
                author_kind="SYSTEM",
                author_id=None,
                body=summary,
                is_internal=False,
            )
        )
        if raised_by_id is not None:
            logger.info("dispute auto-opened", extra={"dispute": str(dispute.id)})
        return dispute

    # ------------------------------------------------------------------ read

    async def get(self, dispute_id: uuid.UUID, *, with_messages: bool = False) -> OrderDispute:
        async with self._session_factory() as session:
            dispute = await session.get(OrderDispute, dispute_id)
            if dispute is None:
                raise NotFoundError(f"dispute {dispute_id} not found")
            if with_messages:
                # Touch the relationship while the session is open, or the
                # caller gets a lazy-load error on a detached instance.
                await session.refresh(dispute, ["messages"])
                dispute.messages  # noqa: B018 — force the load inside the session
            return dispute

    async def list_cases(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        assigned_admin_id: uuid.UUID | None = None,
        unassigned_only: bool = False,
        open_only: bool = False,
        overdue_only: bool = False,
        order_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
        now: datetime | None = None,
    ) -> tuple[list[OrderDispute], int]:
        """The queue. Sorted **SLA first, not newest first** — deliberately.

        An incident-based queue sorted by recency buries the case that is about
        to breach behind whatever arrived most recently, which means the cases
        that get answered are the ones that were filed when someone happened to
        be watching. Sorting by deadline makes the list a work plan.
        """
        now = now or datetime.now(UTC)
        clauses = []
        if status:
            clauses.append(OrderDispute.status == DisputeStatus(status).value)
        if open_only:
            clauses.append(OrderDispute.status.in_([s.value for s in _OPEN_STATUSES]))
        if category:
            clauses.append(OrderDispute.category == DisputeCategory(category).value)
        if severity:
            clauses.append(OrderDispute.severity == DisputeSeverity(severity).value)
        if assigned_admin_id is not None:
            clauses.append(OrderDispute.assigned_admin_id == assigned_admin_id)
        if unassigned_only:
            clauses.append(OrderDispute.assigned_admin_id.is_(None))
        if overdue_only:
            clauses.append(OrderDispute.sla_due_at < now)
            clauses.append(OrderDispute.status.in_([s.value for s in _OPEN_STATUSES]))
        if order_id is not None:
            clauses.append(OrderDispute.order_id == order_id)

        statement = select(OrderDispute)
        count_statement = select(func.count()).select_from(OrderDispute)
        if clauses:
            statement = statement.where(*clauses)
            count_statement = count_statement.where(*clauses)

        statement = statement.order_by(OrderDispute.sla_due_at.asc()).limit(limit).offset(offset)
        async with self._session_factory() as session:
            rows = list((await session.execute(statement)).scalars())
            total = int((await session.execute(count_statement)).scalar_one())
        return rows, total

    async def stats(self, *, now: datetime | None = None) -> dict:
        """Counts the console's header needs, in one round trip.

        `overdue` is computed server-side rather than by the client filtering a
        page of rows: with pagination, a client-side count is the count of
        *this page*, which silently reads as the total.
        """
        now = now or datetime.now(UTC)
        open_values = [s.value for s in _OPEN_STATUSES]
        async with self._session_factory() as session:
            total = int(
                (await session.execute(select(func.count()).select_from(OrderDispute))).scalar_one()
            )
            open_count = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(OrderDispute)
                        .where(OrderDispute.status.in_(open_values))
                    )
                ).scalar_one()
            )
            overdue = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(OrderDispute)
                        .where(
                            OrderDispute.status.in_(open_values),
                            OrderDispute.sla_due_at < now,
                        )
                    )
                ).scalar_one()
            )
            unassigned = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(OrderDispute)
                        .where(
                            OrderDispute.status.in_(open_values),
                            OrderDispute.assigned_admin_id.is_(None),
                        )
                    )
                ).scalar_one()
            )
            safety = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(OrderDispute)
                        .where(
                            OrderDispute.safety_flag.is_(True),
                            OrderDispute.status.in_(open_values),
                        )
                    )
                ).scalar_one()
            )
        return {
            "total": total,
            "open": open_count,
            "overdue": overdue,
            "unassigned": unassigned,
            "safety_flag": safety,
        }

    # ------------------------------------------------------------- mutations

    async def assign(
        self, dispute_id: uuid.UUID, *, admin_id: uuid.UUID, now: datetime | None = None
    ) -> tuple[OrderDispute, uuid.UUID | None]:
        """Claim a case. Returns the row and the previous assignee.

        Moving an `OPEN` case to `INVESTIGATING` is the point of assigning: it
        says somebody owns this now. Re-assigning an already-resolved case is
        refused, because the assignee is part of the audit of who decided.
        """
        async with self._session_factory() as session:
            dispute = await session.get(OrderDispute, dispute_id)
            if dispute is None:
                raise NotFoundError(f"dispute {dispute_id} not found")
            if dispute.status_enum.is_terminal:
                raise BusinessRuleError(
                    "a closed dispute cannot be reassigned",
                    {"reason": "DISPUTE_ALREADY_CLOSED", "status": dispute.status},
                )
            previous = dispute.assigned_admin_id
            dispute.assigned_admin_id = admin_id
            if dispute.status_enum is DisputeStatus.OPEN:
                dispute.status = DisputeStatus.INVESTIGATING.value
            await session.commit()
            await session.refresh(dispute)
        return dispute, previous

    async def add_message(
        self,
        dispute_id: uuid.UUID,
        *,
        body: str,
        author_kind: str,
        author_id: uuid.UUID | None,
        author_label: str | None = None,
        is_internal: bool = False,
    ) -> DisputeMessage:
        """Append to the thread.

        An internal note is allowed on a terminal case (post-mortem discussion
        is normal); a *party's* message is not, because a closed case must not
        accumulate statements that no longer have anyone obliged to answer them.
        """
        if not body or not body.strip():
            raise BusinessRuleError(
                "a message must have a body", {"reason": "DISPUTE_MESSAGE_EMPTY"}
            )
        async with self._session_factory() as session:
            dispute = await session.get(OrderDispute, dispute_id)
            if dispute is None:
                raise NotFoundError(f"dispute {dispute_id} not found")
            if dispute.status_enum.is_terminal and not is_internal:
                raise BusinessRuleError(
                    "this dispute is closed — reply internally, or reopen it first",
                    {"reason": "DISPUTE_ALREADY_CLOSED", "status": dispute.status},
                )
            message = DisputeMessage(
                dispute_id=dispute_id,
                author_kind=author_kind,
                author_id=author_id,
                author_label=author_label,
                body=body.strip(),
                is_internal=is_internal,
            )
            session.add(message)
            await session.commit()
            await session.refresh(message)
        return message

    async def resolve(
        self,
        dispute_id: uuid.UUID,
        *,
        resolution: DisputeResolution | str,
        note: str,
        admin_id: uuid.UUID,
        close: bool = False,
        now: datetime | None = None,
    ) -> OrderDispute:
        """Decide the money question and record who decided.

        The note is mandatory even for `NONE`. "Nobody is charged" without a
        reason is the decision that gets re-litigated, because the next person
        to look at the case cannot tell whether it was considered or forgotten.
        """
        now = now or datetime.now(UTC)
        res = DisputeResolution(resolution)
        if not note or not note.strip():
            raise BusinessRuleError(
                "a resolution requires a note explaining the decision",
                {"reason": "DISPUTE_RESOLUTION_NOTE_REQUIRED"},
            )
        async with self._session_factory() as session:
            dispute = await session.get(OrderDispute, dispute_id)
            if dispute is None:
                raise NotFoundError(f"dispute {dispute_id} not found")
            if dispute.resolution is not None:
                raise BusinessRuleError(
                    "this dispute already has a resolution",
                    {
                        "reason": "DISPUTE_ALREADY_RESOLVED",
                        "resolution": dispute.resolution,
                        "resolved_at": dispute.resolved_at.isoformat()
                        if dispute.resolved_at
                        else None,
                    },
                )
            dispute.resolution = res.value
            dispute.resolution_note = note.strip()
            dispute.resolved_by = admin_id
            dispute.resolved_at = now
            dispute.status = DisputeStatus.CLOSED.value if close else DisputeStatus.RESOLVED.value
            await session.commit()
            await session.refresh(dispute)
        logger.info(
            "dispute resolved",
            extra={"dispute": str(dispute_id), "resolution": res.value},
        )
        return dispute

    async def set_status(
        self, dispute_id: uuid.UUID, *, status: DisputeStatus | str
    ) -> OrderDispute:
        """Move a case between the non-terminal states (escalate, await party)."""
        target = DisputeStatus(status)
        if target is DisputeStatus.RESOLVED:
            # Resolution is not a status flip: it carries a decision, a note and
            # an actor. Routing it through here would let a case read as decided
            # with none of those recorded.
            raise BusinessRuleError(
                "use resolve() to resolve a dispute — it records the decision",
                {"reason": "DISPUTE_USE_RESOLVE"},
            )
        async with self._session_factory() as session:
            dispute = await session.get(OrderDispute, dispute_id)
            if dispute is None:
                raise NotFoundError(f"dispute {dispute_id} not found")
            if dispute.resolution is not None and target is not DisputeStatus.CLOSED:
                raise BusinessRuleError(
                    "a resolved dispute can only be closed",
                    {"reason": "DISPUTE_ALREADY_RESOLVED"},
                )
            dispute.status = target.value
            await session.commit()
            await session.refresh(dispute)
        return dispute


def case_is_overdue(dispute: OrderDispute, *, now: datetime | None = None) -> bool:
    """An open case past its SLA. Computed, never stored.

    Storing `is_overdue` would need a job to flip it, and that job failing is
    invisible — the flag stays `false` and the queue looks healthy while every
    case in it is late.
    """
    now = now or datetime.now(UTC)
    if dispute.status_enum.is_terminal:
        return False
    due = dispute.sla_due_at
    if due is None:
        return False
    # Both sides are tz-aware from Postgres, but a fixture-built row can be
    # naive, and comparing naive to aware raises rather than returning False.
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return due < now


__all__ = ["DisputeService", "case_is_overdue"]
