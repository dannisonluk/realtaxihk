"""Receipts — a frozen document, built once from the order's own snapshots.

Why a frozen document
---------------------
The platform is an information intermediary under Cap. 374D: the fare on an
order is *reference*, frozen into `orders.fare_json` at creation, and settled
voluntarily between passenger and driver. A receipt is therefore a record of
what a particular order was priced at — not a live recomputation.

So a requested receipt is built once and stored in
`orders.receipt_snapshot_json`. Re-rendering it on every read would let a later
tariff change, a driver attribute edit or a ledger adjustment silently rewrite a
document the passenger already holds, which is precisely the property a receipt
exists to deny.

Re-requesting is **idempotent**: it returns the document already on the order.
That is the money-safe direction — the alternative (rebuild and overwrite) would
mean a passenger could refresh a receipt until it said something they preferred.

Rendering
---------
`render_receipt_text` turns a snapshot into a plain-text document. It is a pure
function of the snapshot, so the text a user downloads and the JSON an API
client reads can never disagree. There is deliberately no PDF writer: the
project has no PDF dependency and adding one is a deployment decision, not a
receipt decision.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money_str
from app.models import Order, User

# The intermediary statement. Repeated verbatim on every receipt so the
# document is self-contained — a receipt forwarded to an accountant must not
# depend on a disclaimer living somewhere else in the app.
PLATFORM_DISCLAIMER_ZH = (
    "本收據由 hkfastdc.com 平台發出。平台僅屬資訊中介（非承運人），"
    "車資為下單時凍結之估價，實際車資由乘客與司機自願協商確認"
    "（香港法例第374D章）。"
)
PLATFORM_DISCLAIMER_EN = (
    "This receipt is issued by the hkfastdc.com platform. The platform is an "
    "information intermediary (not a carrier). The fare shown is the estimate "
    "frozen when the order was created; the final fare is settled voluntarily "
    "between passenger and driver (Cap. 374D)."
)


def build_receipt_snapshot(order: Order, passenger: User | None = None) -> dict:
    """Freeze one order into a receipt document.

    Everything is read from the order's own frozen columns — `fare_json`,
    `requirements_json`, the fixed-fare split — never from current driver or
    tariff state. `passenger` is optional and exists only to put a name on the
    document; a receipt for an account with no name is still a valid receipt.
    """
    fare = order.fare_json or {}
    fare_mode = order.fare_mode.value if order.fare_mode is not None else None

    fixed_fare = None
    if order.driver_price_hkd is not None or order.platform_fee_hkd is not None:
        # The fixed-fare (一口價) split, disclosed line by line. The platform fee
        # is never a hidden spread: passenger price = driver payout + fee, and
        # both halves are printed.
        fixed_fare = {
            "driver_price_hkd": money_str(order.driver_price_hkd)
            if order.driver_price_hkd is not None
            else None,
            "platform_fee_hkd": money_str(order.platform_fee_hkd)
            if order.platform_fee_hkd is not None
            else None,
            "passenger_price_hkd": money_str(order.passenger_price_hkd)
            if order.passenger_price_hkd is not None
            else None,
        }

    return {
        "order_id": str(order.id),
        "issued_at": datetime.now(UTC).isoformat(),
        # A receipt is issued on request, but it reports the order's *current*
        # status so a receipt for a cancelled trip cannot read as completed.
        "status": order.status.value,
        "taxi_type": order.taxi_type,
        "fare_mode": fare_mode,
        "pickup_address": order.pickup_address,
        "dropoff_address": order.dropoff_address,
        "distance_km": str(order.distance_km),
        "pickup_area": order.pickup_area,
        "destination_area": order.destination_area,
        "premium_destination": order.premium_destination_json,
        # `estimated_total_hkd` is the stored Numeric(10,2) column, so 2 dp —
        # unlike the meter figures inside `fare`, which are 1 dp.
        "total_hkd": money_str(order.estimated_total_hkd),
        "fare": fare,
        "fixed_fare": fixed_fare,
        "requirements": order.requirements_json,
        "payment_preference": (order.payment_preference_json or {}).get("methods", []),
        "driver_payment_methods": (order.driver_payment_methods_json or {}).get("methods", []),
        "passenger_name": (passenger.given_name if passenger else None),
        "tariff_version": order.tariff_version,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "completed_at": order.completed_at.isoformat() if order.completed_at else None,
        "disclaimer_zh": PLATFORM_DISCLAIMER_ZH,
        "disclaimer_en": PLATFORM_DISCLAIMER_EN,
    }


def render_receipt_text(snapshot: dict) -> str:
    """Render a receipt snapshot as a plain-text document.

    A pure function of the snapshot (no I/O, no session), so the same bytes are
    produced for the same frozen document forever.
    """
    fare = snapshot.get("fare") or {}
    lines: list[str] = [
        "hkfastdc.com — 車費收據 / Fare Receipt",
        "========================================",
        f"訂單編號 Order ID : {snapshot.get('order_id')}",
        f"簽發時間 Issued   : {snapshot.get('issued_at')}",
        f"狀態 Status       : {snapshot.get('status')}",
        f"的士類型 Taxi     : {snapshot.get('taxi_type')}",
        f"收費模式 Mode     : {snapshot.get('fare_mode') or 'METER'}",
        "",
        f"上車 Pickup  : {snapshot.get('pickup_address')}",
        f"落車 Dropoff : {snapshot.get('dropoff_address')}",
        f"距離 Distance: {snapshot.get('distance_km')} km",
        "",
        "車費明細 / Fare breakdown",
    ]

    # Meter figures are 1 dp (the tariff's own increment) — `meter_str`, not
    # `money_str`, or the receipt would print 134.00 where the quote said 134.0.
    lines.append(f"  錶費 Meter            : HK$ {fare.get('meter_fare', '—')}")
    if str(fare.get("discount_percent", "0")) not in {"0", "0.0", ""}:
        lines.append(
            f"  折扣 Discount {fare.get('discount_percent')}% : "
            f"HK$ {fare.get('meter_after_discount', '—')}"
        )
    for surcharge in fare.get("surcharges") or []:
        lines.append(f"  {surcharge.get('name_zh', '')} : HK$ {surcharge.get('amount')}")
    if str(fare.get("tip", "0")) not in {"0", "0.0", ""}:
        lines.append(f"  貼士 Tip              : HK$ {fare.get('tip')}")
    surcharges_total = fare.get("surcharges_total", "—")
    lines.append(f"  附加費合計 Surcharges : HK$ {surcharges_total}")
    lines.append(f"  總額 Total            : HK$ {fare.get('total_fare', '—')}")

    fixed = snapshot.get("fixed_fare")
    if fixed:
        lines += [
            "",
            "一口價明細 / Fixed-fare breakdown",
            f"  司機實收 Driver payout  : HK$ {fixed.get('driver_price_hkd')}",
            f"  平台服務費 Platform fee : HK$ {fixed.get('platform_fee_hkd')}",
            f"  乘客支付 Passenger pays : HK$ {fixed.get('passenger_price_hkd')}",
        ]

    requirements = snapshot.get("requirements") or {}
    flags = [
        ("完全靜音 Silent ride", "silent_ride"),
        ("無電台／音樂 No radio/music", "no_radio_music"),
        ("無煙 No smoke", "no_smoke"),
        ("無香水 No perfume", "no_perfume"),
    ]
    active = [label for label, key in flags if requirements.get(key)]
    animal = requirements.get("animal")
    if active or animal:
        lines += ["", "行程要求 / Ride requirements"]
        for label in active:
            lines.append(f"  - {label}")
        if animal:
            lines.append(
                f"  - 動物 Animal: {animal.get('kind')} "
                f"({animal.get('height_cm')} cm, {animal.get('weight_kg')} kg)"
            )

    preference = snapshot.get("payment_preference") or []
    driver_methods = snapshot.get("driver_payment_methods") or []
    if preference or driver_methods:
        lines += ["", "付款方式 / Payment"]
        if preference:
            lines.append(f"  乘客要求 Requested : {', '.join(preference)}")
        if driver_methods:
            lines.append(f"  司機接受 Accepted  : {', '.join(driver_methods)}")
        lines.append(
            "  （付款方式為偏好／聲明，非平台保證 / preference, not a platform guarantee）"
        )

    lines += [
        "",
        "收費標準版本 Tariff version: " + str(snapshot.get("tariff_version")),
        "",
        snapshot.get("disclaimer_zh", ""),
        snapshot.get("disclaimer_en", ""),
        "",
    ]
    return "\n".join(lines)


class ReceiptService:
    """Create and read the frozen receipt document for one order."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def request(self, order: Order) -> Order:
        """Freeze the receipt onto `order`, or return the existing one.

        Idempotent on purpose: a second request returns the document already
        stored rather than rebuilding it, so a refresh cannot change what a
        receipt says.
        """
        if order.receipt_snapshot_json is None:
            passenger = (
                (await self.session.execute(select(User).where(User.id == order.passenger_id)))
                .scalars()
                .first()
            )
            order.receipt_snapshot_json = build_receipt_snapshot(order, passenger)
            order.receipt_requested = True
            order.receipt_requested_at = datetime.now(UTC)
            await self.session.flush()
        return order

    async def get(self, order: Order) -> str:
        """The receipt as text, freezing it first if the passenger has not asked.

        Reading a receipt *orders* one: the document a client downloads and the
        document the passenger requested are then the same frozen bytes, rather
        than two renderings that could drift apart.
        """
        await self.request(order)
        return render_receipt_text(order.receipt_snapshot_json or {})


__all__ = [
    "PLATFORM_DISCLAIMER_EN",
    "PLATFORM_DISCLAIMER_ZH",
    "ReceiptService",
    "build_receipt_snapshot",
    "render_receipt_text",
]
