"""Weekly settlement (P2-2) — the platform's revenue model.

Every ACTIVE driver pays a flat weekly service fee, deducted from their deposit
balance through the append-only ledger. Arrears are allowed by design: a driver
whose balance goes negative keeps dispatching and settles on the next top-up.

Idempotency is per ISO week. The ledger reference is
`weekly:{driver_profile_id}:{iso_year}-W{week}`, and `uq_ledger_reference` is a
partial UNIQUE index — so running the job twice (a retry after a crash, an
overlapping scheduler, or an operator triggering it by hand) charges each driver
exactly once per week.

SEC-13: "already charged" is now verified, not assumed. Previously the run only
checked whether *a* row held the reference and, if so, counted the driver as
`skipped` — so a planted row with that reference made the HK$200 fee vanish
while the settlement report looked perfectly healthy. Now the pre-check compares
entry_type and amount: a mismatch is reported as `tampered` and logged at
CRITICAL, which is the difference between a silent revenue leak and an alert.

Fleet members are excluded from this run — they are billed by
`FleetSettlementService` at their fleet's discounted rate, under the separate
`fleet:` reference namespace. See `app/services/fleet/fleet_service.py`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import BusinessRuleError, DuplicateReferenceError
from app.core.money import money_str
from app.models import (
    DriverDeposit,
    DriverProfile,
    DriverStatus,
    FleetMembership,
    FleetMemberStatus,
    LedgerEntry,
    LedgerEntryType,
)
from app.services.ledger.ledger_service import LedgerService, reference_for_weekly

logger = logging.getLogger("realtaxihk.settlement")


def period_key(now: datetime | None = None) -> str:
    """ISO-week key for `now` (default: now UTC), e.g. '2026-W40'.

    ISO weeks start on Monday and belong to the year holding their Thursday, so
    the key is stable for the whole week and never collides across a year
    boundary (2026-W01 follows 2025-W53, not 2025-W01).
    """
    iso = (now or datetime.now(UTC)).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def period_start(period: str) -> datetime:
    """The Monday 00:00 UTC at which ISO week `period` begins.

    The inverse of `period_key`, and it lives here so the two stay inverse. A
    day-based estimate ("the week containing the 1st") would be wrong for the
    year-boundary weeks where an ISO year and a calendar year disagree — the
    case `period_key`'s docstring already calls out.

    `datetime.fromisocalendar` is exactly this conversion, and using it rather
    than arithmetic is the reason this is three lines.
    """
    year, _, week = period.partition("-W")
    try:
        return datetime.fromisocalendar(int(year), int(week), 1).replace(tzinfo=UTC)
    except ValueError as exc:
        # e.g. 2026-W54: parseable as a pattern, not a real week. Raised as a
        # business rule so the export endpoint answers 400 rather than 500.
        raise BusinessRuleError(f"not a valid ISO week: {period}") from exc


class SettlementService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def preview_weekly(self, fee_hkd: int, period: str | None = None) -> dict:
        """Compute a week's settlement **without writing anything**.

        The same eligibility rule as `run_weekly`, deliberately duplicated
        rather than approximated: a preview that used a slightly different
        query would report a number the run then contradicts, which is worse
        than no preview at all. The two share `_eligible_driver_ids`, so there
        is one definition of "who is charged".

        What it reports and why:

        * `already_charged` — drivers whose reference is already held by *this*
          week's fee. A re-run is a no-op for them, so a preview that did not
          say this would make a safe re-run look like a double charge.
        * `would_go_negative` / `shortfall_total_hkd` — the drivers whose balance
          cannot cover the fee. **Arrears are allowed by design** (a driver in
          arrears keeps dispatching and settles on the next top-up), so this is
          not an error condition. It is the thing an operator most needs to see
          before pressing the button, because it is the part that is a decision
          rather than arithmetic.
        * `tampered` — a reference held by something that is *not* this week's
          fee. `run_weekly` counts these and logs at CRITICAL; surfacing the
          count before the run means an operator sees the problem on the screen
          they are already looking at, rather than in a log they are not.
        """
        period = period or period_key()
        fee = Decimal(fee_hkd)
        if fee <= 0:
            raise BusinessRuleError("weekly fee must be positive", {"fee_hkd": str(fee)})

        async with self.session_factory() as session:
            driver_ids, fleet_managed_count = await self._eligible_driver_ids(session)

            already_charged: list[str] = []
            tampered: list[str] = []
            chargeable: list[uuid.UUID] = []
            for driver_id in driver_ids:
                reference = reference_for_weekly(driver_id, period)
                existing = (
                    (
                        await session.execute(
                            select(LedgerEntry).where(LedgerEntry.reference == reference)
                        )
                    )
                    .scalars()
                    .first()
                )
                if existing is None:
                    chargeable.append(driver_id)
                elif (
                    existing.entry_type == LedgerEntryType.WEEKLY_FEE_DEDUCTION
                    and Decimal(existing.amount_hkd) == -fee
                ):
                    already_charged.append(str(driver_id))
                else:
                    tampered.append(str(driver_id))

            # One query for every chargeable driver's balance, rather than one
            # per driver: a preview is read while someone waits.
            balances: dict[uuid.UUID, Decimal] = {}
            if chargeable:
                rows = await session.execute(
                    select(DriverDeposit.driver_profile_id, DriverDeposit.balance_hkd).where(
                        DriverDeposit.driver_profile_id.in_(chargeable)
                    )
                )
                balances = {row[0]: Decimal(row[1] or 0) for row in rows}

            # A driver with no deposit row is skipped by `run_weekly` (`deposit
            # is None`), so they must not be counted as chargeable here either.
            # Reporting them as "will be charged" and then not charging them is
            # the preview disagreeing with the run.
            without_account = [d for d in chargeable if d not in balances]
            to_charge = [d for d in chargeable if d in balances]

            would_go_negative = []
            shortfall = Decimal("0")
            for driver_id in to_charge:
                after = balances[driver_id] - fee
                if after < 0:
                    would_go_negative.append(str(driver_id))
                    shortfall += -after

            return {
                "period": period,
                # 2 dp via `money_str`, not `str()`. `str(Decimal(200))` is
                # `"200"`, which renders beside `"200.00"` in the same payload
                # and reads as a different kind of number. `run_weekly` used to
                # emit `str(fee)` here and now matches — the two endpoints are
                # read as a pair on the settlement page, and `admin.py` binds the
                # confirm token to whichever string the preview produced.
                "fee_hkd": money_str(fee),
                "eligible_drivers": len(driver_ids),
                "fleet_managed": fleet_managed_count,
                # These three partition `eligible_drivers` exactly — see the
                # test that asserts they sum to it, which is what stops a
                # future edit from silently dropping a driver out of the report.
                "would_charge": len(to_charge),
                "already_charged": len(already_charged),
                "tampered": len(tampered),
                "skipped_no_deposit_account": len(without_account),
                "would_go_negative": len(would_go_negative),
                "shortfall_total_hkd": money_str(shortfall),
                # `money_str`, not `str()`: every other money figure on the wire
                # is 2 dp, and `str(Decimal("200") * 2)` is `"400"`. A total that
                # renders as `400` beside a fee that renders as `200.00` is the
                # kind of inconsistency that ends up in a spreadsheet as text.
                "total_charge_hkd": money_str(fee * len(to_charge)),
                "would_charge_driver_ids": [str(d) for d in to_charge[:200]],
                "would_go_negative_driver_ids": would_go_negative[:200],
            }

    async def _eligible_driver_ids(self, session: AsyncSession) -> tuple[list[uuid.UUID], int]:
        """Who `run_weekly` would consider: ACTIVE, not on an active fleet.

        Extracted so the preview and the run cannot drift. The fleet exclusion
        is load-bearing (see the module docstring): a fleet member is billed by
        `FleetSettlementService` under a different reference namespace, so
        idempotency would NOT protect them and running both jobs over one driver
        would simply charge twice.
        """
        fleet_managed = select(FleetMembership.driver_profile_id).where(
            FleetMembership.status == FleetMemberStatus.ACTIVE
        )
        driver_ids = list(
            (
                await session.execute(
                    select(DriverProfile.id).where(
                        DriverProfile.status == DriverStatus.ACTIVE,
                        DriverProfile.id.not_in(fleet_managed),
                    )
                )
            ).scalars()
        )
        fleet_managed_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(FleetMembership)
                    .join(
                        DriverProfile,
                        DriverProfile.id == FleetMembership.driver_profile_id,
                    )
                    .where(
                        FleetMembership.status == FleetMemberStatus.ACTIVE,
                        DriverProfile.status == DriverStatus.ACTIVE,
                    )
                )
            ).scalar_one()
        )
        return driver_ids, fleet_managed_count

    async def run_weekly(self, fee_hkd: int, period: str | None = None) -> dict:
        """Charge every ACTIVE driver `fee_hkd` for `period`. Safe to re-run.

        Each driver is charged in its own session: one bad account must not abort
        the run, and the per-driver commit means a crash halfway through leaves
        the already-charged drivers charged (the reference makes the retry a
        no-op for them).
        """
        period = period or period_key()
        fee = Decimal(fee_hkd)
        if fee <= 0:
            raise BusinessRuleError("weekly fee must be positive", {"fee_hkd": str(fee)})

        async with self.session_factory() as session:
            # Only ACTIVE drivers trade: PENDING_KYC / DEPOSIT_REQUIRED have no
            # account to charge, and SUSPENDED / TERMINATED are not on the road.
            #
            # Drivers on an active fleet roster are excluded. They are billed by
            # `FleetSettlementService` at their fleet's discounted rate, under a
            # different ledger reference (`fleet:…` vs `weekly:…`) — so
            # idempotency would NOT protect them here, and running both jobs over
            # the same driver would simply charge them twice. The two services
            # are coupled: changing either means checking the other.
            #
            # The rule lives in `_eligible_driver_ids` so the preview reports the
            # same set the run charges. Duplicating the query here would let a
            # preview say "40 drivers" and the run charge 41.
            driver_ids, fleet_managed_count = await self._eligible_driver_ids(session)

            # Pre-load both things the loop would otherwise select one driver at a
            # time: the references already claimed for this week, and the deposit
            # rows. This is the same pair of queries `preview_weekly` already
            # batched.
            #
            # What is deliberately NOT batched: the charge itself. Each driver
            # still gets its own session and its own commit, because one bad
            # account must not abort the run and a crash halfway through must
            # leave the already-charged drivers charged. Two read-only SELECTs per
            # driver, however, bought nothing that isolation needs — they were
            # just N+1. On a 5,000-driver roster that is 10,000 round trips per
            # week, on a job that runs inside the single `_job_loop`.
            #
            # The pre-load is a *hint*, not a guarantee: a concurrent run can
            # claim a reference between this query and the append below. That case
            # is still caught by `DuplicateReferenceError` (and by the unique index
            # behind it), which is why the fast path here may never become the only
            # path.
            references = {
                driver_id: reference_for_weekly(driver_id, period) for driver_id in driver_ids
            }

            # Keyed by the stored reference. The column is typed nullable, but
            # the query below only selects the references it was given, all of
            # which came from `reference_for_weekly`.
            claimed: dict[str | None, LedgerEntry] = {}
            if references:
                claimed = {
                    row.reference: row
                    for row in (
                        (
                            await session.execute(
                                select(LedgerEntry).where(
                                    LedgerEntry.reference.in_(list(references.values()))
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                }

            # Deliberately NOT pre-loading the deposit rows.
            #
            # That was tried and reverted: hoisting the "is there a deposit row?"
            # check out of the loop makes a driver whose row was deleted between
            # the pre-check and the append look like a clean `skipped` instead of
            # a `failed`. The two are a lost fee apart — `skipped` reports a clean
            # run, `failed` reports that the platform did not collect. Letting
            # `append()` raise is what distinguishes them, so the check must stay
            # inside the try below.
            #
            # The reference pre-load above is safe to hoist precisely because it
            # short-circuits to the *same* outcome the loop body would reach:
            # "already charged -> skipped" or "reference held by something else ->
            # tampered". There is no third case hiding behind it.

        charged = skipped = failed = tampered = 0
        for driver_id in driver_ids:
            reference = references[driver_id]

            # Fast path, from the pre-load. `continue` here avoids opening a
            # session at all for the two most common outcomes (already charged, or
            # never funded); the loop body below still re-checks under its own
            # session, because the pre-load can be stale.
            existing_hint = claimed.get(reference)
            if existing_hint is not None:
                if (
                    existing_hint.entry_type == LedgerEntryType.WEEKLY_FEE_DEDUCTION
                    and Decimal(existing_hint.amount_hkd) == -fee
                ):
                    skipped += 1
                else:
                    # SEC-13: the reference is held by something that is NOT this
                    # week's fee. Do not treat it as "already charged".
                    logger.critical(
                        "weekly settlement: reference %s held by %s/%s — fee NOT "
                        "collected for driver %s",
                        reference,
                        existing_hint.entry_type.value,
                        existing_hint.amount_hkd,
                        driver_id,
                    )
                    tampered += 1
                continue

            try:
                async with self.session_factory() as session:
                    existing = (
                        (
                            await session.execute(
                                select(LedgerEntry).where(LedgerEntry.reference == reference)
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if existing is not None:
                        if (
                            existing.entry_type == LedgerEntryType.WEEKLY_FEE_DEDUCTION
                            and Decimal(existing.amount_hkd) == -fee
                        ):
                            skipped += 1
                        else:
                            # SEC-13: the reference is held by something that is NOT
                            # this week's fee. Do not treat it as "already charged".
                            logger.critical(
                                "weekly settlement: reference %s held by %s/%s — fee NOT "
                                "collected for driver %s",
                                reference,
                                existing.entry_type.value,
                                existing.amount_hkd,
                                driver_id,
                            )
                            tampered += 1
                        continue

                    deposit = (
                        (
                            await session.execute(
                                select(DriverDeposit).where(
                                    DriverDeposit.driver_profile_id == driver_id
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if deposit is None:
                        # Registered but never funded — nothing to deduct.
                        skipped += 1
                        continue

                    await LedgerService(session).append(
                        driver_profile_id=driver_id,
                        entry_type=LedgerEntryType.WEEKLY_FEE_DEDUCTION,
                        amount_hkd=-fee,
                        note=f"weekly service fee {period}",
                        reference=reference,
                    )
                    await session.commit()
                    charged += 1
            except DuplicateReferenceError:
                # The fee IS collected exactly once, so this really is a skip.
                #
                # Kept as a **separate `except` clause** rather than a test inside
                # a single `except BusinessRuleError` handler: the two outcomes
                # ("skipped" vs "failed") are one lost fee apart, and a typed
                # clause cannot be silently reclassified by rewording a message
                # three modules away. `DuplicateReferenceError` subclasses
                # `BusinessRuleError`, so this narrows the branch without changing
                # how anything else catches it.
                logger.debug("weekly settlement: %s already charged for %s", period, driver_id)
                skipped += 1
            except BusinessRuleError as exc:
                # Everything else — e.g. "driver deposit account not found", which
                # `append()` raises when the deposit row was deleted between the
                # pre-check and the append. That driver was NOT charged, so
                # counting it as `skipped` would report a clean run while the
                # platform silently lost the fee.
                #
                # `exception` (not `error`) so the traceback is attached — this
                # branch means a driver was silently NOT charged, and the stack is
                # the only thing that says why.
                logger.exception(
                    "weekly settlement: charge failed for driver %s: %s",
                    driver_id,
                    exc.message,
                )
                failed += 1
            except Exception:
                # Never let one driver abort the whole settlement run.
                logger.exception("weekly settlement failed for driver %s", driver_id)
                failed += 1

        if charged or failed or tampered:
            logger.info(
                "weekly settlement %s: charged=%d skipped=%d failed=%d tampered=%d",
                period,
                charged,
                skipped,
                failed,
                tampered,
            )
        return {
            "period": period,
            # `money_str`, not `str()`: the preview mints its confirm token from
            # exactly this key (`admin.py` binds `fee_hkd` as the preview
            # rendered it), and the console shows preview then run side by side.
            # `str(Decimal(200))` is `"200"`, so the pair read as two different
            # numbers for one fee. See the note on the preview below.
            "fee_hkd": money_str(fee),
            "eligible_drivers": len(driver_ids),
            "fleet_managed": fleet_managed_count,
            "charged": charged,
            "skipped": skipped,
            "failed": failed,
            "tampered": tampered,
        }
