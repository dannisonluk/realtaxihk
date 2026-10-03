"""Helpers that more than one admin resource module needs.

Kept in one private module rather than duplicated, because a second copy of a
serialiser is a second place for the wire shape to drift. `_ledger_out` is used by
both the driver detail view and order detail; `_refund_out` by driver detail and
the refund queue; `_actor_username` by every route that writes an audit row naming
its actor."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Principal
from app.core.money import money_str
from app.models import AdminAccount, LedgerEntry, RefundRequest


def _ledger_out(entry: LedgerEntry) -> dict:
    """Ledger row as the admin detail page shows it.

    `created_by` is exposed here and deliberately NOT on the driver-facing
    `/drivers/me/ledger`: an operator's user id is internal, but for an
    `ADJUSTMENT` — a discretionary move with no upstream event — attribution is
    the point of keeping the record at all. Automated entries carry NULL.
    """
    return {
        "id": entry.id,
        "entry_type": entry.entry_type.value,
        "amount_hkd": money_str(Decimal(entry.amount_hkd)),
        "balance_after_hkd": money_str(Decimal(entry.balance_after_hkd)),
        "order_id": str(entry.order_id) if entry.order_id else None,
        "note": entry.note,
        "reference": entry.reference,
        "created_by": str(entry.created_by) if entry.created_by else None,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _refund_out(r: RefundRequest) -> dict:
    return {
        "id": str(r.id),
        "driver_profile_id": str(r.driver_profile_id),
        "amount_hkd": money_str(Decimal(r.amount_hkd)),
        "status": r.status.value,
        "note": r.note,
        "decision_note": r.decision_note,
        "decided_by": str(r.decided_by) if r.decided_by else None,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


async def _actor_username(session: AsyncSession, admin: Principal) -> str | None:
    """The calling admin's username, for the audit row's denormalised copy.

    `Principal` carries an id and a role but no name, and `record_audit`'s
    `username` column is otherwise only populated by the login path. A role
    change is exactly the row where "who did this" has to be legible without a
    join — the whole point of the from/to payload is that it answers the
    question on its own. One extra read on a route that changes an account is
    not a cost worth trading that for.

    Returns `None` rather than raising if the row is gone: the caller is
    authenticated and the action will succeed or fail on its own merits, and a
    missing username must not be the thing that breaks it.
    """
    row = await session.get(AdminAccount, admin.id)
    return row.username if row is not None else None
