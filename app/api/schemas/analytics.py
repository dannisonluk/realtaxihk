"""Admin analytics response schemas.

These models describe read-only console endpoints that summarise the order
funnel and driver response times. They are separate from `admin.py` because
the payloads belong to `AnalyticsService`, not to the admin account console's
row-level views.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AdminAnalyticsOperationsRangeOut(BaseModel):
    from_: str = Field(alias="from")
    to: str
    taxi_type: str | None = None
    timezone: str
    days: int


class AdminAnalyticsOperationsFunnelOut(BaseModel):
    created: int
    accepted: int
    completed: int
    interrupted: int
    cancelled: int
    active: int


class AdminAnalyticsCancellationsOut(BaseModel):
    passenger: int
    driver: int
    timeout: int
    unattributed: int


class AdminAnalyticsLatencyOut(BaseModel):
    acceptance_avg_s: str | None = None
    arrival_avg_s: str | None = None


class AdminAnalyticsOperationsOut(BaseModel):
    range: AdminAnalyticsOperationsRangeOut
    funnel: AdminAnalyticsOperationsFunnelOut
    cancellations: AdminAnalyticsCancellationsOut
    latency: AdminAnalyticsLatencyOut
    acceptance_rate: str
    cancellation_rate: str
