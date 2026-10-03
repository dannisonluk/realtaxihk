"""The weekly platform settlement: preview, run, and the CSV handover.

`/settlement*`. FINANCE only. The run requires a `confirm_token` from the
preview — see `app/services/ledger/settlement_confirm.py` for why a token rather than a
confirmation checkbox."""

from __future__ import annotations

import csv
import io
from datetime import timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.admin._roles import _require_finance
from app.api.schemas import SettlementPreviewOut, SettlementRunOut
from app.core.config import get_settings
from app.core.db import get_session, get_session_factory
from app.core.deps import Principal
from app.core.exceptions import BusinessRuleError
from app.core.money import money_str
from app.models import DriverProfile, LedgerEntry, LedgerEntryType
from app.services.admin.audit_service import EV_SETTLEMENT_PREVIEW, EV_SETTLEMENT_RUN, record_audit
from app.services.ledger.settlement_confirm import (
    PREVIEW_TTL_SECONDS,
    issue_confirm_token,
    verify_confirm_token,
)
from app.services.ledger.settlement_service import SettlementService, period_key, period_start

router = APIRouter()


class SettlementPreviewIn(BaseModel):
    """The preview request body. Only `period` — the fee comes from settings.

    The fee is deliberately **not** client-supplied. `run_weekly` reads it from
    config, so if a caller could preview at an arbitrary fee, the two numbers on
    the confirm screen and on the charge would be able to disagree — which is
    the exact confusion the confirmation token exists to eliminate.
    """

    period: str | None = Field(default=None, pattern=r"^\d{4}-W\d{2}$")


@router.post("/settlement/preview", response_model=SettlementPreviewOut)
async def preview_weekly_settlement(
    payload: SettlementPreviewIn,
    request: Request,
    admin: Principal = Depends(_require_finance),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Dry-run the weekly settlement. Writes nothing, charges nobody.

    FINANCE, same as the run: a preview that only one role could obtain and
    another could act on is a workflow nobody can complete.
    """
    settings = get_settings()
    result = await SettlementService(session_factory).preview_weekly(
        settings.weekly_fee_hkd, payload.period
    )
    # Bind the token to the fee *as the preview rendered it* — `result["fee_hkd"]`
    # — not to `str(settings.weekly_fee_hkd)`. The two differ whenever the
    # configured fee is not already 2 dp (`200` vs `200.00`), and the run
    # verifies against the latter. Minting one and verifying the other refuses
    # every token the preview ever hands out, with a message naming a fee the
    # operator never saw. One source of truth for the fee string.
    token = issue_confirm_token(period=result["period"], fee_hkd=result["fee_hkd"])
    await record_audit_preview(session_factory, admin=admin, request=request, result=result)
    return {**result, "confirm_token": token, "confirm_expires_in_seconds": PREVIEW_TTL_SECONDS}


async def record_audit_preview(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    admin: Principal,
    request: Request,
    result: dict,
) -> None:
    """Log that a settlement was previewed. Best-effort, own session.

    Separate from `record_audit`'s other callers because the preview has **no
    transaction of its own to ride on** — it writes nothing — so a row here
    always needs a fresh session. Recorded at all because "who looked at the
    numbers before the money moved" is the first question after a bad run.
    """
    async with session_factory() as audit_session:
        await record_audit(
            audit_session,
            event=EV_SETTLEMENT_PREVIEW,
            actor_id=admin.id,
            detail=f"previewed weekly settlement for {result['period']}",
            payload={
                "period": result["period"],
                "fee_hkd": result["fee_hkd"],
                "would_charge": result["would_charge"],
                "already_charged": result["already_charged"],
                "would_go_negative": result["would_go_negative"],
                "total_charge_hkd": result["total_charge_hkd"],
            },
            request=request,
        )
        await audit_session.commit()


@router.post("/settlement/weekly/run", response_model=SettlementRunOut)
async def run_weekly_settlement(
    request: Request,
    period: Annotated[str | None, Query(pattern=r"^\d{4}-W\d{2}$")] = None,
    confirm_token: Annotated[str | None, Query(max_length=2048)] = None,
    admin: Principal = Depends(_require_finance),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Run the weekly service-fee settlement by hand (idempotent per ISO week).

    The scheduled job fires once at boot then every 7 days; this is the ops lever
    to re-run a specific period (e.g. after a failed batch) without touching the
    database. Re-running an already-settled period charges nobody.

    **Requires a `confirm_token` from `/settlement/preview`.** This button
    charges every eligible driver at once, and it is idempotent per ISO week —
    which means the *first* accidental press is not something you get to undo,
    because the money is gone and the reference is spent. Requiring a token the
    preview issued is what turns "look before you leap" from a runbook line into
    a structural constraint.

    Deliberately **not** required when the caller targets a period that has
    already been settled: that run is a no-op by construction, and demanding a
    preview to prove nothing will happen would train operators to click through
    the gate rather than read it.

    The token binds the **period and the fee**, not the actor. Two finance
    admins working the same incident should not have to pass a token back and
    forth, and both actions are audited with their own actor anyway.
    """
    settings = get_settings()
    service = SettlementService(session_factory)

    # Settle the period the token was issued for, and refuse a mismatch rather
    # than silently using one or the other. `period` on the query string is the
    # operator's intent; the token is what was previewed. If they disagree, one
    # of the two is a mistake and guessing which is how a wrong week gets charged.
    if confirm_token:
        wanted = period or period_key()
        verify_confirm_token(
            confirm_token,
            period=wanted,
            # Same normalization as the mint above: `Settings.weekly_fee_hkd`
            # is an int, and an int and its 2 dp rendering are different
            # strings.
            fee_hkd=money_str(settings.weekly_fee_hkd),
        )
    else:
        # No token: only allowed if this exact period is already fully settled,
        # which we can only know by checking. Ask the preview, which writes
        # nothing, and refuse if it says there is anything left to charge.
        wanted = period or period_key()
        check = await service.preview_weekly(settings.weekly_fee_hkd, wanted)
        pending = check["would_charge"]
        if pending:
            raise BusinessRuleError(
                "a settlement run requires a confirmation token from /settlement/preview",
                {
                    "reason": "CONFIRM_TOKEN_REQUIRED",
                    "period": wanted,
                    "would_charge": pending,
                    "hint": "call POST /api/v1/admin/settlement/preview first",
                },
            )
        period = wanted

    result = await service.run_weekly(settings.weekly_fee_hkd, period)
    # Audited on its own session, deliberately. `run_weekly` commits per driver
    # in its own transactions, so by the time this returns the money has already
    # moved — writing the audit row on a session that could still be rolled back
    # would leave real charges with no record of who ran them. Recording after
    # the fact is the honest ordering here; the alternative is an audit row for
    # a run that did not happen.
    async with session_factory() as audit_session:
        await record_audit(
            audit_session,
            event=EV_SETTLEMENT_RUN,
            actor_id=admin.id,
            detail=f"weekly settlement run for {period or 'current period'}",
            payload={
                "period": period or result.get("period"),
                "weekly_fee_hkd": money_str(settings.weekly_fee_hkd),
                "confirmed_by_token": bool(confirm_token),
                "result": {
                    k: (money_str(v) if isinstance(v, Decimal) else v)
                    for k, v in (result or {}).items()
                },
            },
            request=request,
        )
        await audit_session.commit()
    return result


@router.get(
    "/settlement/export.csv",
    response_class=Response,
    responses={
        200: {
            "content": {"text/csv": {"schema": {"type": "string"}}},
            "description": "Ledger rows for the ISO week, as CSV.",
        }
    },
)
async def export_settlement_csv(
    period: Annotated[str, Query(pattern=r"^\d{4}-W\d{2}$")],
    admin: Principal = Depends(_require_finance),
    session: AsyncSession = Depends(get_session),
):
    """Every ledger entry for `period`, as CSV, for the finance handover.

    FINANCE only. The row set is every fee-bearing entry of the week, so this is
    the whole week's billing in one file — a SUPPORT account has no reason to
    have it.

    `text/csv` with an explicit filename rather than JSON, because the consumer
    is a spreadsheet. The columns are chosen for a reconciliation someone does
    by eye: the driver, the direction and amount, the resulting balance, and the
    reference that makes the row traceable back to the job that wrote it.
    """
    rows = (
        await session.execute(
            select(LedgerEntry, DriverProfile)
            .join(DriverProfile, DriverProfile.id == LedgerEntry.driver_profile_id)
            .where(
                LedgerEntry.created_at >= period_start(period),
                LedgerEntry.created_at < period_start(period) + timedelta(days=7),
                LedgerEntry.entry_type.in_(
                    (
                        LedgerEntryType.WEEKLY_FEE_DEDUCTION,
                        LedgerEntryType.PENALTY_DEDUCTION,
                        LedgerEntryType.ADJUSTMENT,
                        LedgerEntryType.REFUND,
                        LedgerEntryType.DEPOSIT_TOPUP,
                    )
                ),
            )
            .order_by(LedgerEntry.created_at)
        )
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "entry_id",
            "created_at",
            "driver_profile_id",
            "vehicle_reg_mark",
            "entry_type",
            "amount_hkd",
            "balance_after_hkd",
            "order_id",
            "reference",
            "note",
        ]
    )
    total = Decimal("0")
    for entry, driver in rows:
        amount = Decimal(entry.amount_hkd)
        total += amount
        writer.writerow(
            [
                entry.id,
                entry.created_at.isoformat() if entry.created_at else "",
                str(entry.driver_profile_id),
                driver.vehicle_reg_mark,
                entry.entry_type.value,
                money_str(amount),
                money_str(Decimal(entry.balance_after_hkd)),
                str(entry.order_id) if entry.order_id else "",
                entry.reference or "",
                (entry.note or "").replace("\n", " "),
            ]
        )
    # A trailing TOTAL row rather than a header comment: a spreadsheet that sums
    # the column and gets a different answer than the file claims is a support
    # ticket, and the difference is usually a row someone filtered out.
    writer.writerow([])
    writer.writerow(["TOTAL", "", "", "", "", money_str(total), "", "", "", ""])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="settlement-{period}.csv"'},
    )
