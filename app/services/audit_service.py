"""Append-only audit records for privileged admin actions.

Why this module exists
----------------------
`AdminAuthService.audit()` was the only writer, and it is an instance method on
a service constructed with a Redis handle and a rate limiter. That is a
reasonable shape for the login path and a hostile one for everything else: a
handler that wanted to record "this admin rejected a refund" would have to
build an `AdminAuthService` it has no other use for, so in practice nobody did,
and the audit log recorded logins while every money and state action went
unrecorded.

Extracting a module-level `record_audit()` removes that friction. It takes the
session it should write to and nothing else it does not need.

The failure mode this is designed against
-----------------------------------------
`AdminAuditLog`'s own docstring says it answers *"who logged in, from where,
and did they move money"*. Only the first half was true. A refund decision is
the single path by which money leaves the platform, and a KYC decision is the
gate on whether a driver may work at all; neither left a row. An intern
rejecting 200 KYC applications was, literally, invisible.

`ledger_entries.created_by` does not close this. It carries a value only when
an amount actually moved, and "refused this refund" and "rejected this licence"
are decisions that need to be attributable whether or not money followed.

Never raises
------------
An audit write failure must not fail the action it was recording. A full disk
would otherwise become an outage in which no refund can be processed — a
worse outcome than a missing row, and the reason this is best-effort.

That is a deliberate trade with a real cost, so the caller decides the
transaction boundary rather than this module guessing: pass the same session
the action is using and the row commits with it, or a separate one and the row
survives a rollback. Both are legitimate; the surrounding handler knows which
it needs.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_ip import client_ip as resolve_client_ip
from app.models import AdminAuditLog

__all__ = [
    "EV_ADMIN_ACCOUNT_CREATE",
    "EV_ADMIN_PASSWORD_RESET",
    "EV_ADMIN_ROLE_CHANGE",
    "EV_DEPOSIT_ADJUST",
    "EV_DEPOSIT_GRANT",
    "EV_DISPUTE_ASSIGN",
    "EV_DISPUTE_CREATE",
    "EV_DISPUTE_MESSAGE",
    "EV_DISPUTE_RESOLVE",
    "EV_FLEET_UPSERT",
    "EV_KYC_DECISION",
    "EV_REFUND_DECISION",
    "EV_SETTLEMENT_PREVIEW",
    "EV_SETTLEMENT_RUN",
    "OUTCOME_FAILURE",
    "OUTCOME_SUCCESS",
    "record_audit",
]

OUTCOME_SUCCESS = "SUCCESS"
OUTCOME_FAILURE = "FAILURE"

# --- Money and state events. Login-class events live in
# `admin_auth_service` because they belong to that flow; these are the ones
# that were missing entirely. Plain strings, not an enum: the table is
# append-only, and an enum would mean a migration for every new event.
EV_KYC_DECISION = "ADMIN_KYC_DECISION"
EV_DEPOSIT_GRANT = "ADMIN_DEPOSIT_GRANT"
EV_DEPOSIT_ADJUST = "ADMIN_DEPOSIT_ADJUST"
EV_REFUND_DECISION = "ADMIN_REFUND_DECISION"
EV_SETTLEMENT_RUN = "ADMIN_SETTLEMENT_RUN"
# A preview is a read, but it is recorded anyway: "who looked at the numbers
# before the money moved" is the first question after a bad run, and a preview
# that leaves no trace makes that question unanswerable.
EV_SETTLEMENT_PREVIEW = "ADMIN_SETTLEMENT_PREVIEW"
EV_FLEET_UPSERT = "ADMIN_FLEET_UPSERT"
EV_ADMIN_ACCOUNT_CREATE = "ADMIN_ACCOUNT_CREATE"
EV_ADMIN_ROLE_CHANGE = "ADMIN_ROLE_CHANGE"
# ruff S105 reads the name as a hardcoded credential; it is an event label.
EV_ADMIN_PASSWORD_RESET = "ADMIN_PASSWORD_RESET"  # noqa: S105
EV_DISPUTE_CREATE = "ADMIN_DISPUTE_CREATE"
EV_DISPUTE_ASSIGN = "ADMIN_DISPUTE_ASSIGN"
EV_DISPUTE_MESSAGE = "ADMIN_DISPUTE_MESSAGE"
EV_DISPUTE_RESOLVE = "ADMIN_DISPUTE_RESOLVE"


async def record_audit(
    session: AsyncSession,
    *,
    event: str,
    outcome: str = OUTCOME_SUCCESS,
    actor_id: uuid.UUID | None = None,
    username: str | None = None,
    detail: str | None = None,
    payload: dict[str, Any] | None = None,
    request: Any | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Append one audit row. Best-effort: never raises.

    `detail` is the human sentence and is truncated at 255 (the column width).
    `payload` is the structured before/after and is **not** truncated — it is
    JSONB, and silently dropping half a JSON document would be worse than
    storing a large one.

    Request context can arrive two ways, and both are needed. `request` is for
    the console handlers, which have one. The explicit `ip` / `user_agent` are
    for the login path, which has already resolved the client address through
    the trusted-proxy rules and would otherwise have to hand over a request
    object purely so this module could re-derive what it already knows.

    An earlier version of this applied `ip`/`user_agent` with a follow-up
    UPDATE on the most recently created row of the same event. That is wrong in
    two ways worth recording: it makes the "append-only" table briefly mutable,
    and under concurrency two simultaneous logins of the same event name race —
    each updates whichever row happens to be newest, so both can end up with
    one login's address. Passing them in is the fix.
    """
    client_ip = ip
    ua = user_agent
    if request is not None:
        if client_ip is None:
            try:
                client_ip = resolve_client_ip(request)
            except Exception:  # pragma: no cover - request shape is ours, not a caller's
                client_ip = None
        if ua is None:
            try:
                ua = request.headers.get("user-agent")
            except Exception:  # pragma: no cover
                ua = None

    try:
        session.add(
            AdminAuditLog(
                admin_id=actor_id,
                username_attempted=(username or None) and username[:64],
                event=event,
                outcome=outcome,
                detail=(detail or None) and detail[:255],
                payload=payload,
                ip_address=(client_ip or None) and client_ip[:45],
                user_agent=(ua or None) and ua[:255],
            )
        )
        await session.flush()
    except Exception:
        # Swallowed on purpose — see the module docstring. Logged loudly so a
        # silently degrading audit trail is at least visible somewhere.
        import logging

        logging.getLogger(__name__).exception("failed to write admin audit row event=%s", event)
