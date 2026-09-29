"""Financial ledger — append-only writes with running balance_after chain.

All money mutations (deposits, weekly fees, penalties, refunds) MUST go
through LedgerService.append; direct balance edits are forbidden.

Concurrency (P0-1): the deposit row is SELECT ... FOR UPDATE — concurrent
appends serialize on the row lock, so a lost balance update is impossible.
Idempotency (P1-7): a non-null `reference` replays the original entry instead
of double-crediting; a DB partial UNIQUE index on reference backstops races.

SEC-13 — reference namespaces are OWNED BY THE SERVER:
The old contract accepted a caller-supplied `reference` and, on a hit, returned
the existing row **verbatim without checking it matched the requested
entry_type or amount**. Because `weekly:{driver}:{period}` and `refund:{id}`
shared one flat namespace, anyone who could write a ledger row (an admin, or an
attacker holding a forged admin token) could pre-plant
`weekly:<driver>:2099-W03`, after which the real settlement run saw "already
charged", skipped the driver, and silently never collected the HK$200 fee —
reported as an ordinary `skipped`. The same trick made a refund reach APPROVED
with no REFUND debit at all.

Two defences now:
1. `append()` refuses a reference hit whose entry_type or amount differs from
   what the caller asked for, so a collision can never silently no-op a charge;
2. every reference is minted by the helpers below, each with a distinct prefix,
   so cross-purpose collisions are not expressible in the first place.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError
from app.models import DriverDeposit, DriverProfile, LedgerEntry, LedgerEntryType


def reference_for_grant(driver_profile_id, client_key: str | None = None) -> str:
    """Admin deposit grant. `client_key` preserves retry-idempotency for the
    caller, but it is namespaced under `grant:<driver>:` so it can never be made
    to collide with a settlement or refund reference."""
    suffix = client_key or uuid.uuid4().hex
    return f"grant:{driver_profile_id}:{suffix}"


def reference_for_weekly(driver_profile_id, period: str) -> str:
    return f"weekly:{driver_profile_id}:{period}"


def reference_for_fleet_weekly(fleet_id, period: str, driver_profile_id) -> str:
    """A fleet member's weekly fee.

    A distinct prefix from `weekly:` on purpose. The two are charged by
    different jobs, and a shared namespace would let the platform-wide run and
    the fleet run collide — either silently swallowing a charge (if they agreed)
    or double-charging a fleet member (if they did not). The fleet id is in the
    reference so a driver's fleet history is reconstructible from the ledger
    alone.
    """
    return f"fleet:{fleet_id}:{period}:{driver_profile_id}"


def reference_for_refund(refund_id) -> str:
    return f"refund:{refund_id}"


class LedgerService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def append(
        self,
        driver_profile_id,
        entry_type: LedgerEntryType,
        amount_hkd: Decimal,
        note: str | None = None,
        order_id=None,
        created_by=None,
        reference: str | None = None,
    ) -> LedgerEntry:
        amount = Decimal(amount_hkd)
        if not amount.is_finite() or amount == 0:
            raise BusinessRuleError("ledger amount must be a finite non-zero value")

        session = self.session

        # Idempotent replay: same reference -> the SAME entry, or an error.
        # SEC-13: returning an arbitrary existing row here is what let a planted
        # reference silently swallow a charge.
        if reference:
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
                if existing.entry_type != entry_type or Decimal(existing.amount_hkd) != amount:
                    raise BusinessRuleError(
                        "ledger reference already used for a different entry",
                        {
                            "reference": reference,
                            "existing_entry_type": existing.entry_type.value,
                            "existing_amount_hkd": str(Decimal(existing.amount_hkd)),
                            "requested_entry_type": entry_type.value,
                            "requested_amount_hkd": str(amount),
                        },
                    )
                return existing

        # Row lock: serialize concurrent appends for this driver (P0-1).
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

        balance_after = Decimal(deposit.balance_hkd) + amount
        # Negative balances are allowed (arrears): a penalty may exceed the
        # remaining deposit; the driver owes the platform until topped up.

        deposit.balance_hkd = balance_after
        deposit.held_hkd = Decimal(deposit.held_hkd)
        deposit.is_fulfilled = balance_after >= Decimal(deposit.required_hkd)

        entry = LedgerEntry(
            driver_profile_id=driver_profile_id,
            entry_type=entry_type,
            amount_hkd=amount,
            balance_after_hkd=balance_after,
            order_id=order_id,
            note=note,
            created_by=created_by,
            reference=reference,
        )
        session.add(entry)
        try:
            await session.flush()
        except IntegrityError as exc:
            # Lost a same-reference race — the unique index backstop fired.
            raise BusinessRuleError("duplicate ledger reference", {"reference": reference}) from exc
        return entry

    @staticmethod
    async def ensure_deposit_row(
        session: AsyncSession, driver_profile: DriverProfile
    ) -> DriverDeposit:
        deposit = (
            (
                await session.execute(
                    select(DriverDeposit).where(
                        DriverDeposit.driver_profile_id == driver_profile.id
                    )
                )
            )
            .scalars()
            .first()
        )
        if deposit is None:
            deposit = DriverDeposit(
                driver_profile_id=driver_profile.id,
                balance_hkd=Decimal("0"),
                held_hkd=Decimal("0"),
                required_hkd=Decimal("500"),
            )
            session.add(deposit)
            await session.flush()
        return deposit
