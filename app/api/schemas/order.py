"""Order schemas — the order payload, the trip-location view, and their lists.

The order payload is deliberately thin and carries no passenger or driver
identity. `order_out` (`app/services/order/order_service.py`) is the single producer,
and every route that returns an order returns exactly this shape — create,
grab, arrive, start, complete, detail, and both history pages. One model with
eight producers is why the "one shape" property is worth pinning: a route that
drifts is a client that breaks on one screen only.

`estimated_total_hkd` is the 2-dp form (`money_str`) — it is a *stored column*,
not a meter figure, so it is the one money field in an order that is not 1 dp.
That inconsistency is not an accident and it is pinned by the fixtures:
`order_detail.json` shows `"estimated_total_hkd": "130.20"` next to
`fare.total_fare: "130.2"`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.api.schemas.fare import FareSnapshotOut

__all__ = [
    "LedgerEntryOut",
    "LedgerPageOut",
    "OrderOut",
    "OrderPageOut",
    "TripLocationOut",
]


class OrderOut(BaseModel):
    """The order payload, from all eight order routes.

    `id` is a **string**, not a UUID: the client treats order ids as opaque
    tokens and this keeps the JSON free of any assumption about their form.
    """

    id: str
    status: str
    taxi_type: str
    fare: FareSnapshotOut
    estimated_total_hkd: str  # money_str, 2 dp (stored column)
    completed_at: str | None
    created_at: str | None
    # Phase 1 additions — all optional so existing fixtures and clients decode
    # unchanged. The values are stored/frozen with the order; they are not
    # recomputed from current driver state.
    requirements: dict | None = None
    payment_preference: list[str] = Field(default_factory=list)
    driver_payment_methods: list[str] = Field(default_factory=list)
    premium_destination: dict | None = None
    destination_area: str | None = None
    pickup_area: str | None = None
    # Phase 2: fixed-fare (一口價) fields, optional on METER orders.
    fare_mode: str | None = None
    fixed_offer_id: str | None = None
    driver_price_hkd: str | None = None
    platform_fee_hkd: str | None = None
    passenger_price_hkd: str | None = None


class OrderPageOut(BaseModel):
    """`{"items": [order]}` for `GET /orders` and `GET /orders/nearby`.

    **No `next_cursor`.** This is deliberate and is called out in
    `verify_contract.dart`: orders paginate by "was the page full", the ledger
    paginates by an explicit cursor. The client must not try to share one
    paging implementation across the two.

    `GET /orders/nearby` can additionally set `degraded: true` — when Redis is
    down it fails open with an empty page rather than 500-ing the driver's map
    (P2-10). `degraded` is optional because the healthy path omits it.
    """

    items: list[OrderOut]
    degraded: bool | None = None


class TripLocationOut(BaseModel):
    """`GET /api/v1/trips/{order_id}/location` — a polled trip view.

    `lat`/`lng` are `null` before a driver is assigned, which is why they are
    optional. `driver_profile_id` is the **profile** id, not a user id — the two
    are different UUID spaces (see `app/models/user.py`).
    """

    order_id: str
    status: str
    driver_profile_id: str | None
    lat: float | None
    lng: float | None


class LedgerEntryOut(BaseModel):
    """One append-only ledger row. `id` is an integer (BigInteger sequence).

    `amount_hkd` is signed: `+` credit, `-` debit. A client that renders
    `abs()` loses the distinction between a top-up and a deduction.
    """

    id: int
    entry_type: str
    amount_hkd: str
    balance_after_hkd: str
    order_id: str | None
    note: str | None
    created_at: str


class LedgerPageOut(BaseModel):
    """`{"items", "next_cursor"}` — the keyset-paginated ledger.

    `next_cursor` is `null` on the last page. Unlike `OrderPageOut` this does
    report whether more exist, so the current cursor is passed back verbatim
    rather than a full-page heuristic.
    """

    items: list[LedgerEntryOut]
    next_cursor: str | None
