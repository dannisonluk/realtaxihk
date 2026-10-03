"""Refund flow — driver requests, admin decides. Value leaves only on approval.

Reconciliation invariant:

    balance_hkd + held_hkd == sum(ledger_entries.amount_hkd)

A pending refund moves money from *available* to *held* and writes **no** ledger
entry, because nothing has left the platform yet. The REFUND ledger entry is
written at approval — the single point where value actually exits. A rejection
releases the hold, again with no ledger entry. The identity above therefore holds
in all three states, and `held_hkd` is the audit-visible "money in flight".

Concurrency: the deposit row is taken `FOR UPDATE` before any balance arithmetic
(same discipline as LedgerService), and the refund row is locked before its
status is read, so two admins deciding the same request serialize instead of both
approving. `uq_refund_pending_per_driver` backstops a double-submit race that
slips past the service-layer check.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError
from app.models import (
    DriverDeposit,
    DriverProfile,
    DriverStatus,
    LedgerEntryType,
    Order,
    OrderStatus,
    RefundRequest,
    RefundStatus,
)
from app.services.ledger.ledger_service import LedgerService
from app.services.order.state_machine import assert_driver_transition

# Statuses that mean a trip is still running — no refund while one is open.
_OPEN_ORDER_STATUSES = (
    OrderStatus.CREATED,
    OrderStatus.BROADCASTING,
    OrderStatus.ACCEPTED,
    OrderStatus.DRIVER_ARRIVED,
    OrderStatus.IN_TRIP,
)


async def _lock_deposit(session: AsyncSession, driver_profile_id) -> DriverDeposit:
    deposit = (
        (
            await session.execute(
                select(DriverDeposit)
                .where(DriverDeposit.driver_profile_id == driver_profile_id)
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )
    if deposit is None:
        raise BusinessRuleError("driver deposit account not found")
    return deposit


class RefundService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _pending(self, driver_profile_id) -> RefundRequest | None:
        return (
            (
                await self.session.execute(
                    select(RefundRequest).where(
                        RefundRequest.driver_profile_id == driver_profile_id,
                        RefundRequest.status == RefundStatus.PENDING,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def request(
        self,
        driver_profile: DriverProfile,
        note: str | None = None,
        min_amount_hkd: Decimal | int | str = Decimal("0"),
    ) -> RefundRequest:
        """Refund the driver's whole available balance. Holds it; does not pay out."""
        # Checked before the status gate: a driver with an open request is
        # SUSPENDED *because of that request*, so "already pending" is the
        # accurate message — "not ACTIVE" would be misleading.
        if await self._pending(driver_profile.id) is not None:
            raise BusinessRuleError("a refund request is already pending")

        if driver_profile.status != DriverStatus.ACTIVE:
            raise BusinessRuleError(
                "only an ACTIVE driver can request a refund",
                {"status": driver_profile.status.value},
            )

        open_order = (
            (
                await self.session.execute(
                    select(Order.id)
                    .where(
                        Order.driver_id == driver_profile.id,
                        Order.status.in_(_OPEN_ORDER_STATUSES),
                    )
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if open_order is not None:
            raise BusinessRuleError("cannot request a refund while a trip is in progress")

        deposit = await _lock_deposit(self.session, driver_profile.id)

        amount = Decimal(deposit.balance_hkd)
        floor = Decimal(min_amount_hkd)
        # `amount <= 0` covers arrears; `amount < floor` is the configured
        # threshold below which a payout is not worth the transfer fee.
        if amount <= 0 or amount < floor:
            raise BusinessRuleError(
                "nothing to refund",
                {"balance_hkd": str(amount), "min_amount_hkd": str(floor)},
            )

        # Hold the money: available -> held. No ledger entry (value has not left).
        deposit.balance_hkd = Decimal("0")
        deposit.held_hkd = Decimal(deposit.held_hkd) + amount
        deposit.is_fulfilled = False

        # Leaving ACTIVE stops dispatch and exempts the driver from weekly fees.
        assert_driver_transition(driver_profile.status, DriverStatus.SUSPENDED)
        driver_profile.status = DriverStatus.SUSPENDED

        refund = RefundRequest(
            driver_profile_id=driver_profile.id,
            amount_hkd=amount,
            status=RefundStatus.PENDING,
            note=note,
        )
        self.session.add(refund)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # Lost a double-submit race — uq_refund_pending_per_driver fired.
            raise BusinessRuleError("a refund request is already pending") from exc
        # created_at comes from a server_default and may not be in the INSERT
        # payload; refresh so callers can serialise it without a lazy load
        # (which raises MissingGreenlet under asyncio).
        await self.session.refresh(refund)
        return refund

    async def decide(
        self,
        refund_id,
        *,
        approve: bool,
        admin_id,
        decision_note: str | None = None,
    ) -> RefundRequest:
        """Approve (pay out, driver TERMINATED) or reject (release hold, driver ACTIVE).

        `populate_existing` is required: without it SQLAlchemy may hand back the
        identity-map copy and skip the post-lock re-read, defeating the guard
        against a concurrent second decision.
        """
        refund = (
            (
                await self.session.execute(
                    select(RefundRequest)
                    .where(RefundRequest.id == refund_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .first()
        )
        if refund is None:
            raise BusinessRuleError("refund request not found")
        if refund.status != RefundStatus.PENDING:
            raise BusinessRuleError(
                "refund request already decided", {"status": refund.status.value}
            )

        driver_profile = await self.session.get(DriverProfile, refund.driver_profile_id)
        if driver_profile is None:
            raise BusinessRuleError("driver profile not found")

        amount = Decimal(refund.amount_hkd)
        deposit = await _lock_deposit(self.session, refund.driver_profile_id)
        held = Decimal(deposit.held_hkd)
        if held < amount:
            raise BusinessRuleError(
                "held balance is short of the refund amount",
                {"held_hkd": str(held), "amount_hkd": str(amount)},
            )

        # Both branches release the hold; only approval also debits it.
        deposit.held_hkd = held - amount
        deposit.balance_hkd = Decimal(deposit.balance_hkd) + amount

        if approve:
            # Release-then-debit keeps the ledger chain exact: append() derives
            # balance_after from the live balance, so the money must be back in
            # `balance_hkd` before the REFUND entry is written.
            # SEC-13: assert the entry we got back IS this refund's debit. Before
            # the append()-level check, a pre-planted `refund:{id}` reference made
            # this return someone else's row, so the refund reached APPROVED and
            # the driver TERMINATED with no money ever leaving the balance.
            entry = await LedgerService(self.session).append(
                driver_profile_id=refund.driver_profile_id,
                entry_type=LedgerEntryType.REFUND,
                amount_hkd=-amount,
                note=decision_note or f"refund approved {refund.id}",
                created_by=admin_id,
                reference=f"refund:{refund.id}",
            )
            if entry.entry_type != LedgerEntryType.REFUND or Decimal(entry.amount_hkd) != -amount:
                raise BusinessRuleError(
                    "refund ledger entry does not match this refund — approval aborted",
                    {
                        "reference": f"refund:{refund.id}",
                        "entry_type": entry.entry_type.value,
                        "amount_hkd": str(Decimal(entry.amount_hkd)),
                        "expected_amount_hkd": str(-amount),
                    },
                )
            refund.status = RefundStatus.APPROVED
            # Paying out is terminal. Skip the transition if an admin already
            # terminated the driver — the end state is the same.
            if driver_profile.status != DriverStatus.TERMINATED:
                assert_driver_transition(driver_profile.status, DriverStatus.TERMINATED)
                driver_profile.status = DriverStatus.TERMINATED
        else:
            deposit.is_fulfilled = Decimal(deposit.balance_hkd) >= Decimal(deposit.required_hkd)
            refund.status = RefundStatus.REJECTED
            # Only a refund-induced suspension is undone. A driver an admin
            # suspended for cause (or already TERMINATED) is left alone.
            if driver_profile.status == DriverStatus.SUSPENDED:
                assert_driver_transition(driver_profile.status, DriverStatus.ACTIVE)
                driver_profile.status = DriverStatus.ACTIVE

        refund.decided_by = admin_id
        refund.decided_at = datetime.now(UTC)
        refund.decision_note = decision_note
        await self.session.flush()
        return refund
