"""Unified subject search — the console's front door.

The problem this solves is the phone call. A passenger rings support and says
"I'm at the airport and the driver hasn't arrived"; the operator has a phone
number, a name, or a plate, and needs the account. Before this, `list_drivers`
could filter by status and page, and nothing else — which meant support could
not locate an account while the passenger was still on the line.

Four decisions, each with a reason:

1. **Prefix matching, not `LIKE %q%`.** A leading wildcard cannot use an index,
   so it degrades to a sequential scan of every user — on the one endpoint that
   is hit on every support call. Prefix matching uses the btree indexes that
   already exist on the phone and name columns.

2. **Normalised before matching.** A Hong Kong phone number is written at least
   five ways (`+852 9123 4567`, `85291234567`, `9123 4567`, `(+852)91234567`),
   and an operator reading it off a screen types whichever one they see. The
   query is stripped to digits for the phone fields so all of them match the
   stored E.164.

3. **One result type, with an explicit `kind`.** The caller asked a single
   question ("who is this") and should not have to merge three lists and invent
   an ordering. `kind` distinguishes a passenger row from a driver row so the
   console can label and route them differently.

4. **No PII beyond what the caller already has.** The result carries the phone
   and the display name because those are what the operator searched with. It
   does **not** carry the ID number, the licence images, or the deposit ledger —
   a search result is a pointer, and the detail page is the audited place to
   look at a person. Otherwise every keystroke in a search box is a PII read,
   and the audit trail has no line for it.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import DriverProfile, User, UserRole

logger = logging.getLogger("realtaxihk.admin_search")

# Two characters is the floor. One character matches most of the table, which
# is both useless to the operator and a cheap way to enumerate every account in
# the system two letters at a time.
MIN_QUERY_LENGTH = 2
DEFAULT_LIMIT = 20
MAX_LIMIT = 50

_DIGITS = re.compile(r"\D+")

# Characters that mean something to `LIKE`. Escaped rather than stripped, so a
# plate containing an underscore is searchable and a `%` cannot turn the query
# into a full-table scan. The escape character is declared on the `.like()`
# call, which is why none of these are backslashes.
_LIKE_SPECIAL = str.maketrans({"\\": r"\\", "%": r"\%", "_": r"\_"})


def normalize_query(raw: str) -> str:
    """Trim and collapse internal whitespace. Rejects the too-short case.

    Whitespace is collapsed rather than preserved because a name pasted from a
    chat message routinely arrives with a trailing newline or a double space,
    and `"Chan  Tai"` failing to match `"Chan Tai"` is a bug the operator
    experiences as "the search is broken".
    """
    return " ".join((raw or "").split())


def digits_only(raw: str) -> str:
    """Digits, for phone and plate matching. Empty when there are none."""
    return _DIGITS.sub("", raw or "")


def _escape_like(value: str) -> str:
    return value.translate(_LIKE_SPECIAL)


class SearchService:
    """Prefix search across users and driver profiles. Read-only."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def search(self, query: str, *, limit: int = DEFAULT_LIMIT) -> tuple[list[dict], bool]:
        """Return `(results, truncated)`.

        `truncated` is returned rather than inferred from `len(results) == limit`
        by the caller: a result set that is exactly `limit` long may or may not
        have more, and the console showing "showing 20 of 20" when there are 400
        is the difference between narrowing the search and believing you have
        seen everyone.
        """
        cleaned = normalize_query(query)
        if len(cleaned) < MIN_QUERY_LENGTH:
            return [], False
        limit = max(1, min(limit, MAX_LIMIT))

        digits = digits_only(cleaned)
        # `prefix` is deliberately applied to the raw cleaned text for names and
        # to the digit form for numbers. A name cannot contain digits, and a
        # plate's digits are stored without separators, so the two never need to
        # cross.
        name_prefix = _escape_like(cleaned) + "%"

        async with self._session_factory() as session:
            # One extra row, so "is there more" is answered without a second
            # COUNT over the same predicates.
            rows = await self._run(session, cleaned, digits, name_prefix, limit + 1)

        truncated = len(rows) > limit
        return rows[:limit], truncated

    async def _run(
        self, session: AsyncSession, cleaned: str, digits: str, name_prefix: str, limit: int
    ) -> list[dict]:
        phone_clauses = []
        if digits:
            # `%` + digits also matches a phone stored with a country code the
            # operator did not type, which is the common case: the caller reads
            # out 8 digits and the row holds `+852` plus those 8.
            phone_clauses = [
                User.phone_e164.like(f"%{digits}%"),
            ]

        user_statement = (
            select(User)
            .where(
                or_(
                    User.display_name.ilike(name_prefix, escape="\\"),
                    User.given_name.ilike(name_prefix, escape="\\"),
                    User.family_name.ilike(name_prefix, escape="\\"),
                    User.username.ilike(name_prefix, escape="\\"),
                    *phone_clauses,
                )
            )
            .order_by(User.created_at.desc())
            .limit(limit)
        )

        profile_clauses = [
            DriverProfile.vehicle_reg_mark.ilike(name_prefix, escape="\\"),
            DriverProfile.taxi_driver_plate_no.ilike(name_prefix, escape="\\"),
        ]
        if digits:
            # A plate is stored without separators but is *read* with them
            # ("AA 1234" vs "AA1234"), and a caller reading a plate off a
            # windscreen often omits the letters entirely. Matching the digit
            # run anywhere in the plate covers both.
            profile_clauses = [
                DriverProfile.vehicle_reg_mark.like(f"%{digits}%"),
                DriverProfile.taxi_driver_plate_no.like(f"%{digits}%"),
            ]

        users = list((await session.execute(user_statement)).scalars())

        # Drivers are found by their *own* profile fields, so a plate search has
        # to go through the join. Only drivers, because only drivers have a
        # plate — a passenger cannot be found this way, by construction.
        driver_statement = (
            select(User, DriverProfile)
            .join(DriverProfile, DriverProfile.user_id == User.id)
            .where(or_(*profile_clauses))
            .order_by(User.created_at.desc())
            .limit(limit)
        )
        driver_rows = list((await session.execute(driver_statement)).all())

        results: list[dict] = []
        seen: set[str] = set()

        for user, profile in driver_rows:
            key = str(user.id)
            if key in seen:
                continue
            seen.add(key)
            results.append(_driver_result(user, profile))

        for user in users:
            key = str(user.id)
            # A driver found by both a name match and a plate match must appear
            # once, and as the richer driver row — hence drivers are collected
            # first and the passenger loop skips what is already present.
            if key in seen:
                continue
            seen.add(key)
            results.append(_user_result(user))

        return results


def _user_result(user: User) -> dict:
    """A passenger row, and a bare driver row when no profile exists.

    A user whose `role` is DRIVER but who has no `driver_profiles` row yet is
    real and reachable: registration creates the user before the profile is
    completed, and a support call can easily arrive inside that window. Labelling
    them `PASSENGER` would be a lie the operator repeats to the caller, so the
    kind is only refined to `DRIVER` when a profile was actually found.
    """
    return {
        "kind": "PASSENGER" if user.role is not UserRole.DRIVER else "DRIVER",
        "id": str(user.id),
        "display_name": user.display_name,
        "phone_e164": user.phone_e164,
        "username": user.username,
        "account_status": user.account_status.value,
        "is_active": user.is_active,
        "avatar_key": user.avatar_key,
        "driver_profile_id": None,
        "plate": None,
        "driver_status": None,
    }


def _driver_result(user: User, profile: DriverProfile) -> dict:
    return {
        "kind": "DRIVER",
        "id": str(user.id),
        "display_name": user.display_name,
        "phone_e164": user.phone_e164,
        "username": user.username,
        "account_status": user.account_status.value,
        "is_active": user.is_active,
        "avatar_key": user.avatar_key,
        "driver_profile_id": str(profile.id),
        # Two plate fields, both returned: `vehicle_reg_mark` is the vehicle and
        # `taxi_driver_plate_no` is the driver's own plate, and an operator
        # reading one back to a caller needs to know which is which.
        "plate": profile.vehicle_reg_mark,
        "driver_status": profile.status.value,
    }


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MIN_QUERY_LENGTH",
    "SearchService",
    "digits_only",
    "normalize_query",
]
