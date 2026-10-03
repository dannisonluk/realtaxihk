"""P-4: periodic re-verification of a phone number.

The user's requirement: *"user needs to verify with phone number again every
month."* The decision locked in earlier was that an overdue re-verify is a
**soft block** — it stops new business, not the whole account.

Three things this module exists to keep straight:

1. **The deadline is a date, not a boolean.** `phone_reverify_due_at` holds the
   next deadline. A boolean "needs re-verify" flag cannot distinguish "due since
   this morning" from "due since March", and an operator triaging a wave of
   them needs that difference.
2. **A grace window, then a soft block.** The re-verify is driven by a
   notification, and notifications get missed. Locking a driver out mid-shift
   because a message did not arrive is worse than a week of grace — the account
   still works, only *new* orders are refused.
3. **Verifying the phone resets the clock.** `mark_verified` is what closes the
   loop; without it, an account that re-verifies stays blocked forever, which is
   the kind of bug that shows up once and then looks like an outage.

`NULL` on a grandfathered row means "not yet scheduled" and is treated as **due**,
not as unlimited. Failing closed here matters: the alternative is that every
account created before this feature landed is permanently exempt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models import User

logger = logging.getLogger(__name__)

# The two machine-readable reasons a request can be refused, so a client can
# route the user without parsing prose.
REASON_UNVERIFIED = "ACCOUNT_UNVERIFIED"
REASON_PHONE_REVERIFY_DUE = "PHONE_REVERIFY_DUE"


@dataclass(frozen=True)
class PhoneReverifyState:
    """Where an account stands on its phone re-verification.

    `grace_ends_at` is the useful field for the client: it is the point at which
    business stops working, so the app can count down to it rather than to the
    earlier — and less urgent — deadline.
    """

    due_at: datetime | None
    grace_ends_at: datetime | None
    is_due: bool
    is_blocked: bool
    days_remaining: int | None

    def as_dict(self) -> dict:
        return {
            "phone_reverify_due_at": self.due_at.isoformat() if self.due_at else None,
            "phone_reverify_grace_ends_at": (
                self.grace_ends_at.isoformat() if self.grace_ends_at else None
            ),
            "phone_reverify_due": self.is_due,
            "phone_reverify_blocked": self.is_blocked,
            "phone_reverify_days_remaining": self.days_remaining,
        }


def evaluate(
    user: User, *, now: datetime | None = None, grace_days: int | None = None
) -> PhoneReverifyState:
    """The state of one account. Pure — no I/O, so it is trivially testable.

    A `NULL` deadline is due **now**, not never. That is the fail-closed choice
    for grandfathered rows: an account predating this feature has never proven
    its number under the new rule, and treating "no deadline recorded" as
    "exempt" would silently grandfather it forever.
    """
    now = now or datetime.now(UTC)
    settings = get_settings()
    if grace_days is None:
        grace_days = settings.phone_reverify_grace_days

    due_at = user.phone_reverify_due_at
    if due_at is None:
        return PhoneReverifyState(
            due_at=None,
            grace_ends_at=None,
            is_due=True,
            # Not blocked: a missing deadline has no grace window to expire, and
            # blocking on the very first request after a deploy would take the
            # whole existing user base offline at once. It is due, so the app
            # prompts; it is not blocked, so the prompt is not a hostage
            # negotiation.
            is_blocked=False,
            days_remaining=None,
        )

    # A tz-aware comparison: a naive column value would raise here, which is
    # preferable to comparing against the server's local clock and getting a
    # different answer on two hosts.
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)

    grace_ends_at = due_at + timedelta(days=grace_days)
    is_due = now >= due_at
    is_blocked = now >= grace_ends_at
    # `days_remaining` counts down to whatever happens *next*, which is a
    # different event on either side of the deadline: before it, the deadline
    # (a reminder); after it, the end of grace (a refusal). Once blocked there
    # is no next event to count to, so it is `None` rather than a clamped `0` —
    # `0` reads as "due today", and the client would show a countdown that never
    # moves on an account that is already switched off.
    days_remaining = None
    if is_due and not is_blocked:
        days_remaining = max(0, (grace_ends_at - now).days)
    elif not is_due:
        days_remaining = max(0, (due_at - now).days)

    return PhoneReverifyState(
        due_at=due_at,
        grace_ends_at=grace_ends_at,
        is_due=is_due,
        is_blocked=is_blocked,
        days_remaining=days_remaining,
    )


def next_deadline(*, now: datetime | None = None) -> datetime:
    """The deadline to set after a successful verification."""
    now = now or datetime.now(UTC)
    return now + timedelta(days=get_settings().phone_reverify_interval_days)


async def mark_verified(session: AsyncSession, user: User, *, now: datetime | None = None) -> User:
    """Close the loop: stamp the phone verified and schedule the next deadline.

    Both writes happen together because they are one fact. Stamping the
    verification without moving the deadline would leave the account blocked —
    "you verified, now verify again" — which is the failure this function exists
    to prevent.
    """
    now = now or datetime.now(UTC)
    user.phone_verified_at = now
    user.phone_reverify_due_at = next_deadline(now=now)
    await session.flush()
    logger.info(
        "phone re-verified user=%s next_due=%s",
        user.id,
        user.phone_reverify_due_at.date().isoformat(),
    )
    return user


async def schedule_for_existing(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Give every row with no deadline one, so the gate has something to read.

    A one-shot backfill helper, used from a migration or an operator script. It
    sets the deadline to *now* — i.e. due immediately, with a grace window — or
    to the interval, depending on whether the phone was already proven:

    - already verified under the new rules -> interval from now
    - never verified -> due now (prompt, then grace, then block)

    Returns the number of rows touched.
    """
    now = now or datetime.now(UTC)
    rows = (
        (await session.execute(select(User).where(User.phone_reverify_due_at.is_(None))))
        .scalars()
        .all()
    )
    for user in rows:
        if user.phone_verified_at is not None:
            user.phone_reverify_due_at = next_deadline(now=now)
        else:
            user.phone_reverify_due_at = now
    await session.flush()
    logger.info("scheduled phone re-verification for %s accounts", len(rows))
    return len(rows)


def session_factory_marker() -> async_sessionmaker:
    """Deliberately not part of the public surface.

    A module-level session factory would let a caller hold a session open past a
    request. The backfill runs from a script, which constructs its own — so this
    only exists to be greppable if someone wonders why there is none.
    """
    raise NotImplementedError


__all__ = [
    "REASON_PHONE_REVERIFY_DUE",
    "REASON_UNVERIFIED",
    "PhoneReverifyState",
    "evaluate",
    "mark_verified",
    "next_deadline",
    "schedule_for_existing",
]
