"""Admin analytics API.

Read-only, and `require_admin`-guarded like every other `/api/v1/admin/*`
route. There is no write path here and no new table: everything is derived from
`orders`, so there is nothing to keep in sync and nothing that can drift from
the orders it describes.

Two endpoints, because they answer two different questions and have different
shapes:

  * `GET /api/v1/admin/analytics`          — earnings per time bucket (the table)
  * `GET /api/v1/admin/analytics/heatmap`  — earnings per hour of the day (the chart)

The date range defaults to the last 30 days including today, which is the
window an operator almost always wants and which makes the endpoint useful
without parameters. Both bounds are inclusive calendar dates **in Hong Kong
time** — see `analytics_service` for why that is not a detail.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, require_admin
from app.services.admin.analytics_service import (
    AnalyticsService,
    Granularity,
    SortBy,
    SortDir,
)

router = APIRouter(prefix="/api/v1/admin/analytics", tags=["admin"])

# Default window. 30 days is long enough to show a weekly rhythm — the pattern
# that makes the hour chart worth looking at — and short enough to stay cheap.
_DEFAULT_DAYS = 30

# A hard ceiling on the range. Without it, `from=1900-01-01` asks the database to
# bucket a century of orders, and the response grows with the range: at `day`
# granularity that is ~46,000 buckets, each a JSON object. The cap keeps a
# mistyped query a 422 rather than a slow request that ties up a worker.
_MAX_RANGE_DAYS = 1096  # three years, so a year-over-year view still fits


def _resolve_range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """Fill in the defaults and reject an inverted or oversized range.

    Defaulting `to` to *today* rather than to `from` means the common
    `?from=2026-09-01` case behaves as "since then, up to now", which is what
    someone typing a start date means.
    """
    resolved_to = date_to or date.today()
    resolved_from = date_from or (resolved_to - timedelta(days=_DEFAULT_DAYS - 1))

    if resolved_from > resolved_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "reason": "RANGE_INVERTED",
                "message": "`from` must not be later than `to`.",
                "from": resolved_from.isoformat(),
                "to": resolved_to.isoformat(),
            },
        )
    if (resolved_to - resolved_from).days + 1 > _MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "reason": "RANGE_TOO_WIDE",
                "message": f"Select a range of at most {_MAX_RANGE_DAYS} days.",
                "max_days": _MAX_RANGE_DAYS,
            },
        )
    return resolved_from, resolved_to


@router.get("")
async def analytics_summary(
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    granularity: Granularity = Granularity.DAY,
    taxi_type: Annotated[str | None, Query(pattern=r"^(URBAN|NT|LANTAU)$")] = None,
    sort_by: SortBy = "bucket",
    sort_dir: SortDir = "asc",
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Earnings and trip counts per time bucket, with range totals.

    `granularity` chooses the bucket width; `from`/`to` choose the window. They
    are independent on purpose — "by month, over the last three years" is a
    legitimate question, and so is "by day, this week".
    """
    resolved_from, resolved_to = _resolve_range(date_from, date_to)
    return await AnalyticsService(session).summary(
        day_from=resolved_from,
        day_to=resolved_to,
        granularity=granularity,
        taxi_type=taxi_type,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@router.get("/heatmap")
async def analytics_heatmap(
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    taxi_type: Annotated[str | None, Query(pattern=r"^(URBAN|NT|LANTAU)$")] = None,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Earnings per hour of the day, averaged across the range.

    Always 24 entries, including the hours with no trips. A chart that dropped
    its empty hours would misrepresent a part-time fleet as a round-the-clock
    one, so the zeros are part of the answer.

    No `granularity` here: the bucket is the hour, which is the point of the
    chart. Changing the range changes the averaging window, not the shape.
    """
    resolved_from, resolved_to = _resolve_range(date_from, date_to)
    return await AnalyticsService(session).hour_profile(
        day_from=resolved_from,
        day_to=resolved_to,
        taxi_type=taxi_type,
    )
