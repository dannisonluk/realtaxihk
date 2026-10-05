"""Recurring ride domain service and background minting job.

A recurring ride is a template stored in PostgreSQL. The minting job is the
only writer of orders from a template and it advances `next_run_at` in the
same database transaction that creates the order, so a crash cannot double
mint: either both survive or neither does.

The job is tuned to fail closed: an inactive passenger, an overdue phone
re-verification or an invalid template pauses the ride (with a log line)
instead of silently keeping it active and retrying every sweep.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.orders import OrderCreateIn
from app.models import (
    Order,
    RecurringRide,
    RecurringStatus,
    User,
)
from app.services.auth import phone_reverify_service
from app.services.order.order_service import OrderService

logger = logging.getLogger("realtaxihk.recurring")

HONG_KONG_TZ = ZoneInfo("Asia/Hong_Kong")


def next_run_at_from(weekday: int, scheduled_time: str, now: datetime | None = None) -> datetime:
    """The UTC instant of the next `weekday`/`scheduled_time` occurrence.

    `weekday` is ISO (1=Monday .. 7=Sunday). `scheduled_time` is "HH:MM" in
    Asia/Hong_Kong. If `now` is exactly at the scheduled time we defer one
    week: firing at the same instant a template is created would immediately
    mint an order.
    """
    now = now or datetime.now(UTC)
    hk_now = now.astimezone(HONG_KONG_TZ)
    hour, minute = (int(part) for part in scheduled_time.split(":"))
    candidate = datetime(hk_now.year, hk_now.month, hk_now.day, hour, minute, tzinfo=HONG_KONG_TZ)
    for _ in range(8):
        if candidate.isoweekday() == weekday and candidate > hk_now:
            return candidate.astimezone(UTC)
        candidate += timedelta(days=1)
    raise ValueError(f"unreachable with weekday={weekday}")


class RecurringAlreadyExists(Exception):
    """Raised when a source order already has a ACTIVE recurring ride."""


class RecurringService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        passenger_user_id: uuid.UUID,
        *,
        weekday: int,
        scheduled_time: str,
        template: dict,
        source_order_id: uuid.UUID | None = None,
    ) -> RecurringRide:
        if not 1 <= weekday <= 7:
            raise ValueError("weekday must be 1 (Monday) .. 7 (Sunday)")
        try:
            next_run = next_run_at_from(weekday, scheduled_time)
        except ValueError as exc:
            raise ValueError(f"scheduled_time must be HH:MM: {exc}") from exc

        if not template:
            raise ValueError("template cannot be empty")
        if source_order_id is not None:
            source_order = await self.session.get(Order, source_order_id)
            if source_order is None or source_order.passenger_id != passenger_user_id:
                raise ValueError("source order does not belong to this passenger")
            existing = (
                await self.session.execute(
                    select(RecurringRide).where(
                        RecurringRide.source_order_id == source_order_id,
                        RecurringRide.status == RecurringStatus.ACTIVE,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise RecurringAlreadyExists("source order already has an active recurring ride")

        ride = RecurringRide(
            passenger_id=passenger_user_id,
            weekday=weekday,
            scheduled_time=scheduled_time,
            template_json=template,
            source_order_id=source_order_id,
            next_run_at=next_run,
        )
        self.session.add(ride)
        await self.session.flush()
        return ride

    async def get_for_passenger(
        self, ride_id: uuid.UUID, passenger_user_id: uuid.UUID
    ) -> RecurringRide | None:
        return (
            await self.session.execute(
                select(RecurringRide).where(
                    RecurringRide.id == ride_id,
                    RecurringRide.passenger_id == passenger_user_id,
                )
            )
        ).scalar_one_or_none()

    async def list_for_passenger(self, passenger_user_id: uuid.UUID) -> list[RecurringRide]:
        result = await self.session.execute(
            select(RecurringRide)
            .where(RecurringRide.passenger_id == passenger_user_id)
            .order_by(RecurringRide.created_at.desc())
        )
        return list(result.scalars().all())

    async def set_status(
        self,
        ride: RecurringRide,
        status: RecurringStatus,
    ) -> RecurringRide:
        current = ride.status
        if status == current:
            return ride
        if current == RecurringStatus.CANCELLED:
            raise ValueError("cancelled recurring ride cannot be changed")
        if current == RecurringStatus.PAUSED and status == RecurringStatus.ACTIVE:
            # Recompute the cursor so a pause that outlasted the next scheduled
            # run does not immediately mint a backdated order.
            ride.next_run_at = next_run_at_from(ride.weekday, ride.scheduled_time)
        ride.status = status
        await self.session.flush()
        return ride


class RecurringMinter:
    """Background job entry point: opens its own DB session per sweep."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def run_due(self, now: datetime | None = None, max_rides: int = 50) -> dict[str, int]:
        now = now or datetime.now(UTC)
        minted = 0
        paused = 0

        async with self.session_factory() as session:
            rides = (
                (
                    await session.execute(
                        select(RecurringRide)
                        .where(
                            RecurringRide.status == RecurringStatus.ACTIVE,
                            RecurringRide.next_run_at <= now,
                        )
                        .order_by(RecurringRide.next_run_at.asc())
                        .limit(max_rides)
                    )
                )
                .scalars()
                .all()
            )
            next_batch = 0
            for ride in rides:
                user = await session.get(User, ride.passenger_id)
                if user is None or not user.is_active:
                    ride.status = RecurringStatus.PAUSED
                    logger.warning("recurring ride %s paused: passenger inactive", ride.id)
                    paused += 1
                    continue

                if phone_reverify_service.evaluate(user, now=now).is_blocked:
                    ride.status = RecurringStatus.PAUSED
                    logger.warning(
                        "recurring ride %s paused: phone re-verification overdue", ride.id
                    )
                    paused += 1
                    continue

                try:
                    next_run = next_run_at_from(ride.weekday, ride.scheduled_time, now)
                    order = await OrderService(session).create(
                        ride.passenger_id,
                        OrderCreateIn.model_validate(ride.template_json),
                    )
                except Exception:
                    ride.status = RecurringStatus.PAUSED
                    logger.exception("recurring ride %s paused: template mint failed", ride.id)
                    paused += 1
                    continue
                ride.last_order_id = order.id
                ride.next_run_at = next_run
                minted += 1

            await session.commit()
            next_batch = (
                await session.scalar(
                    select(func.count(RecurringRide.id)).where(
                        RecurringRide.status == RecurringStatus.ACTIVE,
                        RecurringRide.next_run_at <= now,
                    )
                )
            ) or 0
            return {
                "minted": minted,
                "paused": paused,
                "next_batch": next_batch,
            }
