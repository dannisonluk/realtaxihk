"""Fare schemas — the estimate response and the frozen order snapshot.

There are **two** fare shapes in this system and they are not the same, which is
the single most important thing to know before editing this file:

- `FareEstimateOut` — `POST /fare/estimate`, computed live, never stored.
- `FareSnapshotOut` — `orders.fare_json`, frozen at order creation so a
  historical order stays auditable after a tariff change.

They differ by four fields, and the difference is deliberate:

| field            | estimate | snapshot | why |
|---|---|---|---|
| `taxi_type`      | yes | no  | the order row carries `taxi_type` at top level |
| `distance_km`    | yes | no  | same — it is a column on `orders` |
| `waiting_min`    | yes | no  | same |
| `meter_discount` | yes | no  | the estimate returns a *raw* breakdown |
| `tunnels`        | no  | yes | only the order needs the deduped canonical set |
| `crosses_harbour`| no  | yes | only the order needs it recorded |

The mobile side mirrors this exactly: `mobile/lib/models/fare.dart` has
`FareEstimate` and `mobile/lib/models/order.dart` has `FareSnapshot`, and
`FareSnapshot`'s docstring already states "its keys differ slightly from
FareEstimate". So the split is intended, and the two models here simply make it
explicit on the server.

Money forms — the reason every amount is `str`
----------------------------------------------
Fare figures use the **meter's 1-dp rule** (`meter_str`: `"75.2"`), not the
2-dp rule used for stored balances. `app/services/order/order_service.py` documents why
at length: `fare_snapshot` previously emitted `134.00` where the estimate
endpoint emitted `134.0` for the same trip, because one path used `money_str`
and the other `meter_str`. The fixtures pin it — `fare_estimate.json` and
`order_detail.json` both carry `"meter_fare": "75.2"`. Declaring these as `str`
rather than `Decimal` keeps that rule visible and stops pydantic from
re-formatting the value on the way out.

**Two fields are exempt**: `distance_km` and `waiting_min` on `FareEstimateOut`
are the caller's echoed inputs and are typed `Decimal` because that is what the
handler passes — see that model's docstring. They have no formatting rule, and
changing them to `str` makes the route reject its own handler's output.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from app.services.order.fare_calculator import TaxiType

__all__ = [
    "FareEstimateOut",
    "FareSnapshotOut",
    "FareSurchargeOut",
]


class FareSurchargeOut(BaseModel):
    """One line of the breakdown. `code` is the stable key — match on it, not
    the localised names, so a copy change cannot break a client's logic."""

    code: str
    name_en: str
    name_zh: str
    amount: str  # meter_str, 1 dp


class FareEstimateOut(BaseModel):
    """`POST /api/v1/fare/estimate` — live quote, never persisted.

    Cap. 374D: the response must be labelled an estimate and carry the
    disclaimer. Both are fields here, so the requirement is visible in the
    published contract rather than only in the UI code.

    **Three fields are deliberately NOT `str`**, and this is the one place in the
    package that breaks the "money is always a string" rule — so the reasoning
    is worth stating:

    - `taxi_type` is the `TaxiType` enum, not a free string. Declaring it as the
      enum is what publishes the allowed values in `/openapi.json`; as a bare
      `str` a client gets no list and has to hard-code `URBAN`/`NT`/`LANTAU`.
      It is a `str`-subclass enum, so the wire form is unchanged (`"URBAN"`).
    - `distance_km` and `waiting_min` are the **caller's inputs echoed back**,
      not computed money. `FareBreakdown` carries them as `Decimal`
      (`app/services/order/fare_calculator.py`) and the handler forwards them
      untouched — they never pass through `meter_str`. They are therefore the
      only two figures here with no canonical formatting rule, which the fixture
      confirms: `"12.5"` and `"3"` (note: *not* `"3.0"`).

    **Do not "fix" these to `str`.** Doing so makes the model reject the
    `Decimal` the handler actually passes, and the route 500s. An audit script
    caught exactly that mistake before it shipped —
    `scripts/verify/audit_response_models.py` checks the model against the handler's
    real output shape, not against a plausible-looking type.
    """

    taxi_type: TaxiType
    distance_km: Decimal
    waiting_min: Decimal
    meter_fare: str
    discount_percent: str
    meter_discount: str
    meter_after_discount: str
    surcharges: list[FareSurchargeOut]
    surcharges_total: str
    tip: str
    total_fare: str
    tariff_version: str
    is_estimate: bool
    disclaimer_en: str
    disclaimer_zh: str


class FareSnapshotOut(BaseModel):
    """`orders.fare_json` — the breakdown frozen onto an order at creation.

    Frozen rather than recomputed on read: a tariff change must not silently
    restate what a completed trip was quoted at.

    `tunnels` is the **deduped, sorted, validated** set of tunnel codes, not the
    caller's raw list (SEC-11). The raw list used to be stored verbatim, which
    let a caller write megabytes of junk into `fare_json` that every subsequent
    list call re-sent.

    Phase 2: a fixed-fare order keeps the METER breakdown for reference and
    adds `fare_mode` plus the three frozen contract amounts.
    """

    meter_fare: str
    meter_after_discount: str
    surcharges_total: str
    tip: str
    total_fare: str
    discount_percent: str
    tunnels: list[str]
    crosses_harbour: bool
    tariff_version: str
    is_estimate: bool
    disclaimer_en: str
    disclaimer_zh: str
    surcharges: list[FareSurchargeOut]
    # Phase 2 additions are optional so existing fixtures stay decodable.
    fare_mode: str | None = None
    driver_price_hkd: str | None = None
    platform_fee_hkd: str | None = None
    passenger_price_hkd: str | None = None
