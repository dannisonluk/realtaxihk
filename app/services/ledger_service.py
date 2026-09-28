"""Financial ledger — append-only writes with running balance_after chain.

All money mutations (deposits, weekly fees, penalties, refunds) MUST go
through LedgerService.append; direct balance edits are forbidden.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError
from app.models import DriverDeposit, DriverProfile, LedgerEntry, LedgerEntryType


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
        deposit = (
            await session.execute(
                select(DriverDeposit).where(
                    DriverDeposit.driver_profile_id == driver_profile_id
                )
            )
        ).scalars().first()
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
        await session.flush()
        return entry

    @staticmethod
    async def ensure_deposit_row(
        session: AsyncSession, driver_profile: DriverProfile
    ) -> DriverDeposit:
        deposit = (
            await session.execute(
                select(DriverDeposit).where(
                    DriverDeposit.driver_profile_id == driver_profile.id
                )
            )
        ).scalars().first()
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
