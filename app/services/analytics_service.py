"""Operational analytics: completed-trip earnings, bucketed, plus a daily hour profile.

What "earnings" means here
--------------------------
`orders.estimated_total_hkd` on a **COMPLETED** order — the fare the passenger
pays, which the driver keeps, because the platform is an information
intermediary rather than an operator (Cap. 374D). The column already has the
discount and the tip folded in by the fare calculator, so it needs no further
adjustment here; recomputing it from parts would create a second definition of
a fare that could drift from the first.

Not the ledger. `ledger_entries` records what the platform and the driver owe
each other — deposits, the weekly fee, penalties, refunds. Those are costs, not
revenue, so a "highest earning hour" chart built on the ledger would rank hours
by how much the platform billed the driver. The two answer different questions
and this module answers the operational one.

Why every bucket is cast to Asia/Hong_Kong first
------------------------------------------------
`completed_at` is `timestamptz`, i.e. an instant. Grouping instants by day
gives **UTC** days, so a trip finished at 08:30 Hong Kong time lands in the
previous UTC day, and the evening peak — 19:00 to 23:00 HKT, which is 11:00 to
15:00 UTC — is smeared across two buckets. Every "busiest hour" and "best day"
figure would be quietly wrong, and wrong in the direction that looks plausible,
so nobody would notice. The cast happens in SQL via `timezone('Asia/Hong_Kong',
...)` rather than in Python so the grouping key and the filter agree.

Hong Kong has had no daylight saving since 1979, so the offset is a constant
+08:00 and the bucket boundaries are stable. If that ever changed, the SQL cast
would still be correct — which is the reason for doing it in SQL rather than
adding a fixed eight hours in Python.

Denominators for the heat map
-----------------------------
"Average earnings in this hour" needs a denominator, and there are two honest
answers, so both are returned:

  * `avg_per_day_hkd` — divided by every calendar day in the selected range.
    This is the one to chart: it answers "what does a day look like", which is
    what a heat map is for, and it falls when the fleet simply does not work.
  * `avg_per_active_day_hkd` — divided only by the days that had at least one
    trip in that hour. This answers "when a driver is out at this hour, what do
    they make", which is the right number for deciding whether to work it.

Reporting only the first would understate a sparse week; only the second would
hide that the fleet was idle. Both are cheap once the rows are grouped.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Date, Select, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money_str, ratio_str
from app.models import Order, OrderStatus

# Hong Kong is UTC+8 with no daylight saving, but the name is used rather than a
# fixed offset so the database applies its own tz database — if the rules ever
# changed, the SQL would follow and a hard-coded +8 would not.
HK_TZ = "Asia/Hong_Kong"


class Granularity(str, enum.Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


# Allow-list. These end up in `date_trunc` and in `ORDER BY`, where a bind
# parameter cannot go, so they must never be interpolated from raw input.
_UNITS: dict[Granularity, str] = {
    Granularity.DAY: "day",
    Granularity.WEEK: "week",
    Granularity.MONTH: "month",
    Granularity.YEAR: "year",
}

SortBy = Literal["bucket", "orders", "earnings", "avg_fare", "distance"]
SortDir = Literal["asc", "desc"]

_SORT_COLUMNS: dict[str, str] = {
    "bucket": "bucket",
    "orders": "orders",
    "earnings": "earnings",
    "avg_fare": "avg_fare",
    "distance": "distance",
}


def _hk_day_bounds(day_from: date, day_to: date) -> tuple[datetime, datetime]:
    """Half-open [from 00:00 HKT, day_after 00:00 HKT) as UTC-aware instants.

    Half-open because a closed interval double-counts an order completed exactly
    at midnight on the boundary — it would appear in both the last day of one
    range and the first of the next.

    The conversion is done with `zoneinfo` rather than by adding eight hours, so
    the boundary is whatever Hong Kong actually observes.
    """
    from zoneinfo import ZoneInfo

    hk = ZoneInfo(HK_TZ)
    start = datetime.combine(day_from, time.min, tzinfo=hk)
    # `day_to + 1 day` and not `day_to` at 23:59:59.999: an order at 23:59:59.5
    # would slip between the two, and a fraction-of-a-second gap in a financial
    # report is the kind of thing that is only found by an auditor.
    end = datetime.combine(day_to + timedelta(days=1), time.min, tzinfo=hk)
    return start, end


def _completed_in_range(day_from: date, day_to: date, taxi_type: str | None) -> Select:
    """Base query: completed trips whose completion falls inside the HK range."""
    start, end = _hk_day_bounds(day_from, day_to)
    stmt = (
        select(Order)
        .where(Order.status == OrderStatus.COMPLETED)
        .where(Order.completed_at.is_not(None))
        .where(Order.completed_at >= start)
        .where(Order.completed_at < end)
    )
    if taxi_type is not None:
        stmt = stmt.where(Order.taxi_type == taxi_type)
    return stmt


def _ratio_2dp(numerator: int, denominator: int) -> str:
    """A plain 2-dp ratio, for non-money figures such as trips per day.

    `money_str` is deliberately not used: it exists to fix the wire form of a
    *stored amount*, and labelling a trip count as money would invite a reader
    to treat it as one. `ratio_str` is the right entry point instead — it is the
    half-up rule without the money claim, and using it here is what keeps this
    ratio rounding the same way as the money beside it.
    """
    if not denominator:
        return "0.00"
    return ratio_str(Decimal(numerator) / denominator)


class AnalyticsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def summary(
        self,
        *,
        day_from: date,
        day_to: date,
        granularity: Granularity = Granularity.DAY,
        taxi_type: str | None = None,
        sort_by: SortBy = "bucket",
        sort_dir: SortDir = "asc",
    ) -> dict[str, Any]:
        """Earnings and volume per time bucket, plus range totals.

        Sorting is applied to the *bucket list only* — the totals are always
        over the whole range, so re-sorting the table never changes the headline
        figures above it. A UI where the total moved when you clicked a column
        header would be a bug report waiting to happen.
        """
        # The local-time expression appears in both SELECT and GROUP BY, so it
        # is built once. `timezone()` on a timestamptz yields a naive local
        # timestamp, which is what `date_trunc` should bucket on.
        local_ts = func.timezone(HK_TZ, Order.completed_at)
        bucket = func.date_trunc(_UNITS[granularity], local_ts)

        rows = (
            await self.session.execute(
                _completed_in_range(day_from, day_to, taxi_type)
                .with_only_columns(
                    bucket.label("bucket"),
                    func.count().label("orders"),
                    func.coalesce(func.sum(Order.estimated_total_hkd), 0).label("earnings"),
                    func.coalesce(func.avg(Order.estimated_total_hkd), 0).label("avg_fare"),
                    func.coalesce(func.sum(Order.distance_km), 0).label("distance"),
                )
                .group_by(bucket)
            )
        ).all()

        buckets = [
            {
                "bucket": row.bucket.date().isoformat(),
                "orders": int(row.orders),
                "earnings_hkd": money_str(Decimal(row.earnings)),
                "avg_fare_hkd": money_str(Decimal(row.avg_fare)),
                "distance_km": ratio_str(Decimal(row.distance)),
            }
            for row in rows
        ]

        # Totals are summed from the buckets rather than re-queried: the buckets
        # already cover exactly the filtered range, so a second query could only
        # disagree with the table it is meant to summarise.
        # The `Decimal("0")` start value is load-bearing, not decoration. An
        # empty `buckets` (a range with no completed orders) makes a bare
        # `sum(...)` return the *int* `0`, and `int.quantize` does not exist —
        # so the zero case raised AttributeError instead of reporting zeroes.
        # Seeding the accumulator keeps the type a Decimal for every input.
        total_orders = sum(b["orders"] for b in buckets)
        total_earnings = sum((Decimal(b["earnings_hkd"]) for b in buckets), Decimal("0"))
        total_distance = sum((Decimal(b["distance_km"]) for b in buckets), Decimal("0"))

        buckets = _sort_buckets(buckets, sort_by, sort_dir)

        return {
            "range": {
                "from": day_from.isoformat(),
                "to": day_to.isoformat(),
                "granularity": granularity.value,
                "taxi_type": taxi_type,
                "timezone": HK_TZ,
            },
            "totals": {
                "orders": total_orders,
                "earnings_hkd": money_str(total_earnings),
                "avg_fare_hkd": money_str(
                    total_earnings / total_orders if total_orders else Decimal("0")
                ),
                "distance_km": ratio_str(total_distance),
                "buckets": len(buckets),
                "days": (day_to - day_from).days + 1,
            },
            "buckets": buckets,
            "sort": {"by": sort_by, "dir": sort_dir},
        }

    async def hour_profile(
        self,
        *,
        day_from: date,
        day_to: date,
        taxi_type: str | None = None,
    ) -> dict[str, Any]:
        """Earnings per hour of the day, averaged over the selected range.

        This is the data behind the day chart: 24 slots, each carrying the mean
        earnings booked in that hour. Both denominators described in the module
        docstring are returned; `avg_per_day_hkd` is the one to draw.

        Hours with no trips are returned as zeros rather than omitted. A heat
        map that silently dropped the quiet hours would compress its own x-axis
        and make a fleet that works four hours a day look like it works
        twenty-four — the gaps are information.
        """
        local_ts = func.timezone(HK_TZ, Order.completed_at)
        hour = func.extract("hour", local_ts)
        # Distinct *local* dates that had a trip in this hour, for the second
        # denominator. Cast to date so two trips in the same hour on the same
        # day count once.
        local_day = cast(local_ts, Date)

        rows = (
            await self.session.execute(
                _completed_in_range(day_from, day_to, taxi_type)
                .with_only_columns(
                    hour.label("hour"),
                    func.count().label("orders"),
                    func.coalesce(func.sum(Order.estimated_total_hkd), 0).label("earnings"),
                    func.count(func.distinct(local_day)).label("active_days"),
                )
                .group_by(hour)
            )
        ).all()

        days_in_range = (day_to - day_from).days + 1
        by_hour = {int(row.hour): row for row in rows}

        hours: list[dict[str, Any]] = []
        for h in range(24):
            row = by_hour.get(h)
            earnings = Decimal(row.earnings) if row else Decimal("0")
            orders = int(row.orders) if row else 0
            active_days = int(row.active_days) if row else 0
            hours.append(
                {
                    "hour": h,
                    # The charted value: what an average day in this range
                    # earned during this hour.
                    "avg_per_day_hkd": money_str(earnings / days_in_range),
                    # What it earned on the days it was actually worked. Equal to
                    # the above when every day had a trip; larger when the hour
                    # was worked only occasionally.
                    "avg_per_active_day_hkd": money_str(
                        earnings / active_days if active_days else Decimal("0")
                    ),
                    "earnings_hkd": money_str(earnings),
                    "orders": orders,
                    "active_days": active_days,
                    "avg_orders_per_day": _ratio_2dp(orders, days_in_range),
                }
            )

        peak = max(hours, key=lambda x: Decimal(x["avg_per_day_hkd"]))
        busiest = max(hours, key=lambda x: x["orders"])
        return {
            "range": {
                "from": day_from.isoformat(),
                "to": day_to.isoformat(),
                "taxi_type": taxi_type,
                "timezone": HK_TZ,
                "days": days_in_range,
            },
            "hours": hours,
            "max_avg_per_day_hkd": peak["avg_per_day_hkd"],
            "peak_hour": peak["hour"],
            "busiest_hour": busiest["hour"],
            # The scale for the chart's y-axis. Zero when there is nothing to
            # draw, which the client must handle rather than dividing by it.
            "scale_max_hkd": peak["avg_per_day_hkd"],
        }


def _sort_buckets(
    buckets: list[dict[str, Any]], sort_by: SortBy, sort_dir: SortDir
) -> list[dict[str, Any]]:
    """Sort the bucket list. Allow-listed keys, so no user string reaches SQL.

    The sort happens in Python rather than as an `ORDER BY`, for two reasons:
    the totals have already been summed so the order no longer matters to
    correctness, and the bucket count is bounded by the range (at most ~366 for
    a year of days, 24 for hours), so there is nothing to gain from pushing it
    into the database and a raw-SQL `ORDER BY` to lose.

    The key maps to the *numeric* field, not the formatted string. Sorting
    `"9.00"` against `"100.00"` as text puts nine before a hundred; parsing back
    to `Decimal` is what keeps a money column in money order.
    """
    key = _SORT_COLUMNS[sort_by]
    if key == "bucket":
        return sorted(buckets, key=lambda b: b["bucket"], reverse=(sort_dir == "desc"))

    numeric = {
        "orders": lambda b: b["orders"],
        "earnings": lambda b: Decimal(b["earnings_hkd"]),
        "avg_fare": lambda b: Decimal(b["avg_fare_hkd"]),
        "distance": lambda b: Decimal(b["distance_km"]),
    }[key]
    # Secondary key on the bucket, so equal values keep a stable, meaningful
    # order instead of whatever the group-by happened to return.
    return sorted(
        buckets,
        key=lambda b: (numeric(b), b["bucket"]),
        reverse=(sort_dir == "desc"),
    )
