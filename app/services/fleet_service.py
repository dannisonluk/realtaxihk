"""Taxi fleets (的士車隊): management, rosters and fleet-level settlement.

Why fleets exist
----------------
HK taxi fleets are *licensed operators*, not self-service groups. The Transport
Department grants the fleet licence, so a fleet is created by an admin and never
by a driver — drivers join by being added to a roster. That shapes everything
here: there is no "create my fleet" endpoint, and no invite flow to defend.

The billing interaction that matters
------------------------------------
`SettlementService.run_weekly` charges **every** ACTIVE driver the flat platform
fee. A fleet member is charged instead by `FleetSettlementService`, at the
fleet's discounted rate. If both ran over the same driver the same week, the two
use different ledger references, so idempotency would not save anyone — the
driver would simply be charged twice. `SettlementService.run_weekly` therefore
excludes drivers on an active roster, and reports how many it left alone. The
two jobs are not independent; changing either one means checking the other.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import BusinessRuleError, DuplicateReferenceError
from app.core.money import MoneyInput, money_str, quantize_money
from app.models import (
    DriverDeposit,
    DriverProfile,
    DriverStatus,
    Fleet,
    FleetMemberRole,
    FleetMembership,
    FleetMemberStatus,
    FleetSettlementRun,
    FleetStatus,
    LedgerEntry,
    LedgerEntryType,
)
from app.services.ledger_service import (
    LedgerService,
    reference_for_fleet_weekly,
)
from app.services.settlement_service import period_key

logger = logging.getLogger("realtaxihk.fleet")


def discounted_fee(fee_hkd: MoneyInput, discount_percent: MoneyInput) -> Decimal:
    """The per-member fee after the fleet's volume discount.

    Rounded to a cent rather than truncated: truncation across a few hundred
    members is a quiet revenue leak, and it is not the kind of thing anyone
    notices until an operator queries their invoice.
    """
    discount = Decimal(discount_percent)
    if not (Decimal(0) <= discount <= Decimal(100)):
        raise BusinessRuleError(
            "fleet discount must be between 0 and 100",
            {"discount_percent": str(discount)},
        )
    net = Decimal(fee_hkd) * (Decimal(100) - discount) / Decimal(100)
    return quantize_money(net)


class FleetService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ---- fleets ----------------------------------------------------------
    async def create(
        self,
        *,
        name: str,
        license_no: str,
        discount_percent: Decimal | int | float = 0,
        contact_name: str | None = None,
        contact_phone: str | None = None,
        note: str | None = None,
    ) -> Fleet:
        discount = Decimal(str(discount_percent))
        if not (Decimal(0) <= discount <= Decimal(100)):
            raise BusinessRuleError(
                "fleet discount must be between 0 and 100",
                {"discount_percent": str(discount)},
            )

        # Checked in the service for a clear error, and enforced by the unique
        # constraints for the race.
        clash = (
            (
                await self.session.execute(
                    select(Fleet).where((Fleet.name == name) | (Fleet.license_no == license_no))
                )
            )
            .scalars()
            .first()
        )
        if clash is not None:
            field = "name" if clash.name == name else "license_no"
            raise BusinessRuleError(
                f"a fleet with that {field} already exists",
                {"field": field, "fleet_id": str(clash.id)},
            )

        fleet = Fleet(
            name=name,
            license_no=license_no,
            weekly_fee_discount_percent=discount,
            contact_name=contact_name,
            contact_phone=contact_phone,
            note=note,
            status=FleetStatus.ACTIVE,
        )
        self.session.add(fleet)
        await self.session.flush()
        return fleet

    async def get(self, fleet_id) -> Fleet | None:
        return await self.session.get(Fleet, fleet_id)

    async def require(self, fleet_id) -> Fleet:
        fleet = await self.get(fleet_id)
        if fleet is None:
            raise BusinessRuleError("fleet not found", {"fleet_id": str(fleet_id)})
        return fleet

    async def update(
        self,
        fleet_id,
        *,
        name: str | None = None,
        status: FleetStatus | None = None,
        discount_percent: Decimal | int | float | None = None,
        contact_name: str | None = None,
        contact_phone: str | None = None,
        note: str | None = None,
    ) -> Fleet:
        fleet = await self.require(fleet_id)
        if name is not None:
            fleet.name = name
        if status is not None:
            fleet.status = status
        if discount_percent is not None:
            discount = Decimal(str(discount_percent))
            if not (Decimal(0) <= discount <= Decimal(100)):
                raise BusinessRuleError(
                    "fleet discount must be between 0 and 100",
                    {"discount_percent": str(discount)},
                )
            fleet.weekly_fee_discount_percent = discount
        if contact_name is not None:
            fleet.contact_name = contact_name
        if contact_phone is not None:
            fleet.contact_phone = contact_phone
        if note is not None:
            fleet.note = note
        await self.session.flush()
        return fleet

    async def list_page(
        self, *, status: FleetStatus | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[Fleet], int]:
        q = select(Fleet).order_by(Fleet.created_at)
        count_q = select(func.count()).select_from(Fleet)
        if status is not None:
            q = q.where(Fleet.status == status)
            count_q = count_q.where(Fleet.status == status)
        rows = list((await self.session.execute(q.limit(limit).offset(offset))).scalars())
        total = (await self.session.execute(count_q)).scalar_one()
        return rows, int(total)

    # ---- roster ----------------------------------------------------------
    async def membership_of(self, driver_profile_id) -> FleetMembership | None:
        """The driver's current ACTIVE membership, if any."""
        return (
            (
                await self.session.execute(
                    select(FleetMembership).where(
                        FleetMembership.driver_profile_id == driver_profile_id,
                        FleetMembership.status == FleetMemberStatus.ACTIVE,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def roster(
        self, fleet_id, *, active_only: bool = True
    ) -> list[tuple[FleetMembership, DriverProfile]]:
        q = (
            select(FleetMembership, DriverProfile)
            .join(DriverProfile, DriverProfile.id == FleetMembership.driver_profile_id)
            .where(FleetMembership.fleet_id == fleet_id)
            .order_by(FleetMembership.joined_at)
        )
        if active_only:
            q = q.where(FleetMembership.status == FleetMemberStatus.ACTIVE)
        return [(row[0], row[1]) for row in (await self.session.execute(q)).all()]

    async def active_member_count(self, fleet_id) -> int:
        return int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(FleetMembership)
                    .where(
                        FleetMembership.fleet_id == fleet_id,
                        FleetMembership.status == FleetMemberStatus.ACTIVE,
                    )
                )
            ).scalar_one()
        )

    async def active_member_counts(self, fleet_ids: Sequence[UUID]) -> dict[UUID, int]:
        """ACTIVE member counts for many fleets in one query.

        A per-fleet `active_member_count` in a loop is an N+1: the admin fleet
        list page renders 100 rows by default, and each row awaited its own
        `SELECT count(*)`. Measured on this machine, that turned a page load into
        **17.5s** on a cold pool — the browser's six-connection-per-origin limit
        then queued the rest of the dashboard behind it and the console looked
        frozen. One grouped `COUNT ... GROUP BY` returns the same numbers in a
        single round trip.

        Fleets with no active members are simply absent from the result; callers
        default them to 0.
        """
        if not fleet_ids:
            return {}
        rows = await self.session.execute(
            select(FleetMembership.fleet_id, func.count())
            .where(
                FleetMembership.fleet_id.in_(fleet_ids),
                FleetMembership.status == FleetMemberStatus.ACTIVE,
            )
            .group_by(FleetMembership.fleet_id)
        )
        return {fleet_id: int(count) for fleet_id, count in rows.all()}

    async def add_member(
        self,
        fleet_id,
        driver_profile_id,
        *,
        member_role: FleetMemberRole | None = None,
        note: str | None = None,
    ) -> FleetMembership:
        fleet = await self.require(fleet_id)
        if fleet.status == FleetStatus.DISSOLVED:
            raise BusinessRuleError(
                "cannot add a driver to a dissolved fleet",
                {"fleet_id": str(fleet_id), "status": fleet.status.value},
            )

        driver = await self.session.get(DriverProfile, driver_profile_id)
        if driver is None:
            raise BusinessRuleError(
                "driver profile not found", {"driver_profile_id": str(driver_profile_id)}
            )

        existing = await self.membership_of(driver_profile_id)
        if existing is not None:
            if existing.fleet_id == fleet_id:
                raise BusinessRuleError(
                    "driver is already on this fleet",
                    {"fleet_id": str(fleet_id), "driver_profile_id": str(driver_profile_id)},
                )
            # The partial unique index would reject this too; the explicit check
            # turns a 500-class IntegrityError into a comprehensible 400.
            raise BusinessRuleError(
                "driver is already on another fleet",
                {
                    "driver_profile_id": str(driver_profile_id),
                    "current_fleet_id": str(existing.fleet_id),
                },
            )

        membership = FleetMembership(
            fleet_id=fleet_id,
            driver_profile_id=driver_profile_id,
            member_role=member_role or FleetMemberRole.MEMBER,
            status=FleetMemberStatus.ACTIVE,
            note=note,
        )
        self.session.add(membership)
        await self.session.flush()
        return membership

    async def remove_member(
        self, fleet_id, driver_profile_id, *, reason: str | None = None
    ) -> FleetMembership:
        membership = (
            (
                await self.session.execute(
                    select(FleetMembership).where(
                        FleetMembership.fleet_id == fleet_id,
                        FleetMembership.driver_profile_id == driver_profile_id,
                        FleetMembership.status == FleetMemberStatus.ACTIVE,
                    )
                )
            )
            .scalars()
            .first()
        )
        if membership is None:
            raise BusinessRuleError(
                "driver is not on this fleet",
                {"fleet_id": str(fleet_id), "driver_profile_id": str(driver_profile_id)},
            )
        membership.status = FleetMemberStatus.REMOVED
        membership.left_at = datetime.now(UTC)
        if reason is not None:
            membership.note = reason
        await self.session.flush()
        return membership

    # ---- settlement ------------------------------------------------------
    async def settlement_history(self, fleet_id, *, limit: int = 52) -> list[FleetSettlementRun]:
        return list(
            (
                await self.session.execute(
                    select(FleetSettlementRun)
                    .where(FleetSettlementRun.fleet_id == fleet_id)
                    .order_by(FleetSettlementRun.period.desc())
                    .limit(limit)
                )
            ).scalars()
        )


class FleetSettlementService:
    """Charges a fleet's members their weekly fee, at the fleet's rate."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def run_weekly(self, fleet_id, fee_hkd: int, period: str | None = None) -> dict:
        """Charge every ACTIVE member of `fleet_id`. Safe to re-run.

        Idempotent per (fleet, week) through the per-driver ledger reference, so
        a retry after a crash, an overlapping scheduler or an operator pulling
        the lever by hand charges each member exactly once.
        """
        period = period or period_key()
        gross = Decimal(fee_hkd)
        if gross <= 0:
            raise BusinessRuleError("weekly fee must be positive", {"fee_hkd": str(gross)})

        async with self.session_factory() as session:
            fleet = await session.get(Fleet, fleet_id)
            if fleet is None:
                raise BusinessRuleError("fleet not found", {"fleet_id": str(fleet_id)})
            if fleet.status != FleetStatus.ACTIVE:
                raise BusinessRuleError(
                    "fleet is not active — nothing to settle",
                    {"fleet_id": str(fleet_id), "status": fleet.status.value},
                )
            discount = Decimal(fleet.weekly_fee_discount_percent)
            per_member = discounted_fee(gross, discount)
            # Pulled out as plain scalars BEFORE the session closes. The loop
            # below (and the totals dict) runs outside the `async with`, so
            # touching `fleet.<attr>` there only works while the attribute is a
            # plain loaded column — the moment someone adds a relationship and
            # reads it, or `expire_on_commit` is turned on, every weekly
            # settlement raises `DetachedInstanceError` midway through. A string
            # cannot do that.
            fleet_name = fleet.name

            # Only members whose *driver profile* is ACTIVE are billable: a
            # SUSPENDED or TERMINATED member is not on the road, and one still in
            # KYC has no deposit account to debit.
            member_ids = list(
                (
                    await session.execute(
                        select(FleetMembership.driver_profile_id)
                        .join(DriverProfile, DriverProfile.id == FleetMembership.driver_profile_id)
                        .where(
                            FleetMembership.fleet_id == fleet_id,
                            FleetMembership.status == FleetMemberStatus.ACTIVE,
                            DriverProfile.status == DriverStatus.ACTIVE,
                        )
                    )
                ).scalars()
            )

        if per_member <= 0:
            # A 100% discount is a legitimate configuration — record the run so
            # the week is not silently missing from the operator's history.
            logger.info(
                "fleet settlement %s: %s is 100%% discounted — nothing to collect",
                period,
                fleet_id,
            )

        charged = skipped = failed = tampered = 0
        collected = Decimal("0")

        for driver_id in member_ids:
            reference = reference_for_fleet_weekly(fleet_id, period, driver_id)
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
                            and Decimal(existing.amount_hkd) == -per_member
                        ):
                            skipped += 1
                        else:
                            # SEC-13: the reference is held by something that is
                            # NOT this week's fleet fee.
                            logger.critical(
                                "fleet settlement: reference %s held by %s/%s — fee NOT "
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
                        skipped += 1
                        continue

                    if per_member > 0:
                        await LedgerService(session).append(
                            driver_profile_id=driver_id,
                            entry_type=LedgerEntryType.WEEKLY_FEE_DEDUCTION,
                            amount_hkd=-per_member,
                            note=f"fleet service fee {period} ({fleet_name})",
                            reference=reference,
                        )
                        await session.commit()
                        charged += 1
                        collected += per_member
                    else:
                        skipped += 1
            except DuplicateReferenceError:
                # The fee IS collected exactly once — this really is a skip.
                # Narrowed from the bare `BusinessRuleError` it used to catch: that
                # version also swallowed "driver deposit account not found", so a
                # member whose deposit row had been deleted between the pre-check
                # and the append was reported as *already paid* while the fleet
                # silently collected nothing from them. The two failures are one
                # lost fee apart and must not share a branch.
                logger.debug("fleet settlement: %s already charged for %s", period, driver_id)
                skipped += 1
            except BusinessRuleError as exc:
                # NOT charged. Surface it rather than counting it as a skip.
                logger.exception(
                    "fleet settlement: charge failed for driver %s: %s", driver_id, exc.message
                )
                failed += 1
            except Exception:
                # One member must never abort the whole fleet's settlement.
                logger.exception("fleet settlement failed for driver %s", driver_id)
                failed += 1

        totals = {
            "fleet_id": str(fleet_id),
            "fleet_name": fleet_name,
            "period": period,
            # All four money keys go through `money_str` so they are 2 dp and
            # read as the same *kind* of number. Before this, `gross`/`per_member`
            # rendered as `"200"` (they come from `str(Decimal(200))`) while
            # `collected` was quantised to `"150.00"` — one payload, two
            # precisions, and `collected_hkd` was the only one that was right.
            # `discount_percent` stays `str()`: it is a percentage, not money.
            "gross_fee_hkd": money_str(gross),
            "discount_percent": str(discount),
            "fee_hkd": money_str(per_member),
            "member_count": len(member_ids),
            "charged": charged,
            "skipped": skipped,
            "failed": failed,
            "tampered": tampered,
            "collected_hkd": money_str(collected),
        }

        # Upsert the aggregate: one row per (fleet, week), so a re-run updates
        # what the operator is shown rather than appending a second version.
        async with self.session_factory() as session:
            run = (
                (
                    await session.execute(
                        select(FleetSettlementRun).where(
                            FleetSettlementRun.fleet_id == fleet_id,
                            FleetSettlementRun.period == period,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if run is None:
                run = FleetSettlementRun(fleet_id=fleet_id, period=period)
                session.add(run)
            run.fee_hkd = per_member
            run.discount_percent = discount
            run.member_count = len(member_ids)
            run.charged = charged
            run.skipped = skipped
            run.failed = failed
            run.tampered = tampered
            run.collected_hkd = collected
            await session.commit()

        if charged or failed or tampered:
            logger.info(
                "fleet settlement %s %s: charged=%d skipped=%d failed=%d tampered=%d collected=%s",
                fleet_id,
                period,
                charged,
                skipped,
                failed,
                tampered,
                collected,
            )
        return totals
