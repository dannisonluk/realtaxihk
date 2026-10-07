"""Refund flow — driver requests, admin decides. Value leaves only on approval.

Reconciliation invariant:

    balance_hkd + held_hkd == sum(ledger_entries.amount_hkd)

A pending refund moves money from *available* to *held* and writes **no** ledger
entry, because nothing has left the platform yet. The REFUND ledger entry is
written at approval — the single point where value actually exits. A rejection
releases the hold, again with no ledger entry. The identity above therefore holds
in all three states, and `held_hkd` is the audit-visible "money in flight".

Two kinds of claim exist since P2-2. A **full** claim (`is_partial = false`)
holds the whole balance and is terminal on approval. A **partial** claim
(`is_partial = true`) holds a requested subset; approval pays that subset out,
releases the rest, and returns the driver to ACTIVE, so withdrawing part of the
balance does not force them off the platform.

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
    OrderStatus.PENDING_ARRIVAL_CONFIRM,
    OrderStatus.DRIVER_ARRIVED,
    OrderStatus.IN_TRIP,
    OrderStatus.DESTINATION_CHANGED,
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
        *,
        amount_hkd: Decimal | int | str | None = None,
        min_amount_hkd: Decimal | int | str = Decimal("0"),
    ) -> RefundRequest:
        """Claim all — or, with `amount_hkd`, part — of the available balance.

        Holds the claimed amount; does not pay out. Requesting suspends the
        driver either way: money in flight must not race new dispatch, and the
        weekly-fee exemption follows from US not ACTIVE. A partial claim
        differs from a full claim only at decision time — approval of a partial
        claim releases the unclaimed remainder and returns the driver to
        ACTIVE, while a full claim is terminal.
        """
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

        balance = Decimal(deposit.balance_hkd)
        floor = Decimal(min_amount_hkd)
        if amount_hkd is not None:
            amount = Decimal(amount_hkd)
            # A partial claim must be less than the whole balance, positive,
            # and still worth the transfer fee. Equal to the balance is a full
            # refund in disguise and keeps the old terminal semantics.
            if amount >= balance:
                raise BusinessRuleError(
                    "amount must be less than the available balance for a partial refund",
                    {"balance_hkd": str(balance), "amount_hkd": str(amount)},
                )
            is_partial = True
        else:
            amount = balance
            is_partial = False
        # `amount <= 0` covers arrears; `amount < floor` is the configured
        # threshold below which a payout is not worth the transfer fee.
        if amount <= 0 or amount < floor:
            raise BusinessRuleError(
                "nothing to refund",
                {"balance_hkd": str(balance), "min_amount_hkd": str(floor)},
            )

        # Hold the money: available -> held. No ledger entry (value has not left).
        deposit.balance_hkd = balance - amount
        deposit.held_hkd = Decimal(deposit.held_hkd) + amount
        deposit.is_fulfilled = False

        # Leaving ACTIVE stops dispatch and exempts the driver from weekly fees.
        # A partial claim also leaves ACTIVE: money is in flight and the claimed
        # amount is no longer available to settle fees.
        assert_driver_transition(driver_profile.status, DriverStatus.SUSPENDED)
        driver_profile.status = DriverStatus.SUSPENDED

        refund = RefundRequest(
            driver_profile_id=driver_profile.id,
            amount_hkd=amount,
            is_partial=is_partial,
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
        """Approve or reject: full refunds terminate, partial ones reactivate.

        A **full** refund is the driver leaving: approval pays out and moves
        the driver to TERMINATED. A **partial** refund is a withdrawal while
        staying on the platform: approval pays out the claimed amount and
        returns the driver to ACTIVE (unless they were suspended for cause).
        Rejecting releases the hold and returns the driver to ACTIVE in both
        cases.

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
            if refund.is_partial:
                # A partial withdrawal is *not* the driver leaving: they claimed
                # part of the balance, so the remainder stays on the account and
                # the driver returns to the road. Re-compute fulfilment against
                # the deposit requirement, not the full balance.
                deposit.is_fulfilled = Decimal(deposit.balance_hkd) >= Decimal(deposit.required_hkd)
                # Only a refund-induced suspension is undone (mirrors the reject
                # branch): a driver suspended for cause is left alone.
                if driver_profile.status == DriverStatus.SUSPENDED:
                    assert_driver_transition(driver_profile.status, DriverStatus.ACTIVE)
                    driver_profile.status = DriverStatus.ACTIVE
            else:
                # Paying out is terminal. Skip the transition if an admin
                # already terminated the driver — the end state is the same.
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
