"""Receipt schemas — the frozen receipt document.

One model, because the receipt is one shape from both routes: `POST` issues it
and `GET` reads it, and both return the stored snapshot. `text` is the rendered
document included alongside the structured fields, so a client can show or share
the human-readable form without re-implementing the layout — and it is produced
by the same frozen snapshot, so the two can never disagree.

`fare` and `requirements` are left as `dict`: they are the order's own frozen
JSONB blocks, already typed by `FareSnapshotOut` elsewhere. Re-declaring them
here would create a second definition of the same frozen object, and the two
would drift.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["ReceiptOut"]


class ReceiptOut(BaseModel):
    """A receipt for one order.

    Every money field is a string in the project's canonical form:
    `total_hkd` is the stored `Numeric(10,2)` column (2 dp, `money_str`), while
    the figures inside `fare` are meter readings (1 dp, `meter_str`). See
    `app/core/money.py` — the two rules are not interchangeable.
    """

    order_id: str
    issued_at: str
    status: str
    taxi_type: str
    fare_mode: str | None = None
    pickup_address: str
    dropoff_address: str
    distance_km: str
    pickup_area: str | None = None
    destination_area: str | None = None
    premium_destination: dict | None = None
    total_hkd: str
    fare: dict
    # Present only on a fixed-fare (一口價) order. The platform fee is disclosed
    # here rather than folded into a single number, so the split is auditable.
    fixed_fare: dict | None = None
    requirements: dict | None = None
    payment_preference: list[str] = Field(default_factory=list)
    driver_payment_methods: list[str] = Field(default_factory=list)
    # Only set when the passenger has a name on their account. A receipt without
    # one is still a valid receipt.
    passenger_name: str | None = None
    tariff_version: str
    created_at: str | None = None
    completed_at: str | None = None
    # The rendered plain-text document, from the same frozen snapshot.
    text: str
    disclaimer_zh: str
    disclaimer_en: str
