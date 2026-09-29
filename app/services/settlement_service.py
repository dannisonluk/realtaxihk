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
`fleet:` reference namespace. See `app/services/fleet_service.py`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import BusinessRuleError
from app.models import (
    DriverDeposit,
    DriverProfile,
    DriverStatus,
    FleetMembership,
    FleetMemberStatus,
    LedgerEntry,
    LedgerEntryType,
)
from app.services.ledger_service import LedgerService, reference_for_weekly

logger = logging.getLogger("realtaxihk.settlement")


def period_key(now: datetime | None = None) -> str:
    """ISO-week key for `now` (default: now UTC), e.g. '2026-W40'.

    ISO weeks start on Monday and belong to the year holding their Thursday, so
    the key is stable for the whole week and never collides across a year
    boundary (2026-W01 follows 2025-W53, not 2025-W01).
    """
    iso = (now or datetime.now(UTC)).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


class SettlementService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

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

        charged = skipped = failed = tampered = 0
        for driver_id in driver_ids:
            reference = reference_for_weekly(driver_id, period)
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
            except BusinessRuleError:
                # Lost a same-reference race — the unique index did its job.
                logger.debug("weekly settlement: %s already charged for %s", period, driver_id)
                skipped += 1
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
            "fee_hkd": str(fee),
            "eligible_drivers": len(driver_ids),
            "fleet_managed": fleet_managed_count,
            "charged": charged,
            "skipped": skipped,
            "failed": failed,
            "tampered": tampered,
        }
