"""HK Taxi Fare Estimation Engine.

Regulatory basis (verified 2026-09-28 from official sources):
- Meter tariffs effective 2024-07-14 (TD press release / Cap. 374D schedule):
    Urban (紅的):   flagfall $29 / first 2km; $2.1 per 200m-or-part (or per 1min wait)
                    until meter reaches $102.5; $1.4 per jump thereafter.
    NT (綠的):      flagfall $25.5; $1.9 until $82.5; $1.4 thereafter.
    Lantau (藍的):  flagfall $24;   $1.9 until $195;   $1.6 thereafter.
- Taxi tunnel tolls: cross-harbour (CHT/EHC/WHC) $25 + $25 return fee (return fee
  waived when boarding at a cross-harbour taxi stand or destination is on the same
  side of the harbour) — TD "分時段收費" scheme note 2; Tai Lam $28 (2025-05-31),
  Tates Cairn $20, Lion Rock / Eagle's Nest / Shing Mun / Aberdeen $8
  (Aberdeen & Shing Mun effective 2025-09-21), Lantau Link $30.
- Other charges: baggage $6/item, animal/bird $5 each, advance booking $5/trip,
  wheelchairs & crutches free.

Fares produced here are ESTIMATES ONLY. Every breakdown must carry the
information-intermediary disclaimer per Cap. 374D.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from enum import Enum

_MAX_DISTANCE_KM = Decimal("100")  # sanity cap: far beyond any HK taxi route
_MAX_WAIT_MIN = Decimal("600")  # sanity cap: 10 hours of waiting


def _as_decimal(value: Decimal | str | float | int) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def _ceil_units(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


class TaxiType(str, Enum):
    URBAN = "URBAN"  # 市區的士（紅的）
    NT = "NT"  # 新界的士（綠的）
    LANTAU = "LANTAU"  # 大嶼山的士（藍的）


# Official tariff (Cap. 374D schedule, effective 2024-07-14).
# threshold = meter amount at which the per-jump rate steps down; verified to be
# reachable exactly by whole jumps from the flagfall (see tests).
_TARIFFS: dict[TaxiType, dict] = {
    TaxiType.URBAN: {
        "flagfall": Decimal("29"),
        "inc1": Decimal("2.1"),
        "threshold": Decimal("102.5"),
        "inc2": Decimal("1.4"),
    },
    TaxiType.NT: {
        "flagfall": Decimal("25.5"),
        "inc1": Decimal("1.9"),
        "threshold": Decimal("82.5"),
        "inc2": Decimal("1.4"),
    },
    TaxiType.LANTAU: {
        "flagfall": Decimal("24"),
        "inc1": Decimal("1.9"),
        "threshold": Decimal("195"),
        "inc2": Decimal("1.6"),
    },
}


class Tunnel(str, Enum):
    CROSS_HARBOUR = "cross_harbour"  # 紅隧/東隧/西隧（的士劃一收費）
    TAI_LAM = "tai_lam"  # 大欖隧道
    TATES_CAIRN = "tates_cairn"  # 大老山隧道
    LION_ROCK = "lion_rock"  # 獅子山隧道
    EAGLES_NEST = "eagles_nest"  # 尖山隧道
    SHING_MUN = "shing_mun"  # 城門隧道
    ABERDEEN = "aberdeen"  # 香港仔隧道
    LANTAU_LINK = "lantau_link"  # 青嶼幹線


@dataclass(frozen=True)
class SurchargeItem:
    code: str
    name_en: str
    name_zh: str
    amount: Decimal


@dataclass(frozen=True)
class FareBreakdown:
    taxi_type: TaxiType
    distance_km: Decimal
    waiting_min: Decimal
    meter_fare: Decimal
    discount_percent: Decimal
    meter_discount: Decimal
    meter_after_discount: Decimal
    surcharges: tuple[SurchargeItem, ...]
    surcharges_total: Decimal
    tip: Decimal
    total_fare: Decimal
    tariff_version: str
    is_estimate: bool
    disclaimer_en: str
    disclaimer_zh: str


def estimate_meter_fare(
    taxi_type: TaxiType,
    distance_km: Decimal | str | float,
    waiting_min: Decimal | str | float = 0,
) -> Decimal:
    """Meter-only fare (flagfall + distance jumps + waiting-time jumps).

    Raises ValueError on negative/oversized inputs.
    """
    distance = _as_decimal(distance_km)
    waiting = _as_decimal(waiting_min)
    if distance < 0 or waiting < 0:
        raise ValueError("distance_km and waiting_min must be non-negative")
    if distance > _MAX_DISTANCE_KM:
        raise ValueError(f"distance_km exceeds sanity cap {_MAX_DISTANCE_KM} km")
    if waiting > _MAX_WAIT_MIN:
        raise ValueError(f"waiting_min exceeds sanity cap {_MAX_WAIT_MIN} min")

    t = _TARIFFS[taxi_type]
    distance_jumps = _ceil_units((distance - 2) / Decimal("0.2")) if distance > 2 else 0
    wait_jumps = _ceil_units(waiting / 1)  # "每分鐘或其部分"
    total_jumps = distance_jumps + wait_jumps

    fare = t["flagfall"]
    remaining_to_threshold = (t["threshold"] - t["flagfall"]) / t["inc1"]
    threshold_jumps = int(remaining_to_threshold)
    if total_jumps <= threshold_jumps:
        return fare + total_jumps * t["inc1"]
    fare += threshold_jumps * t["inc1"]
    fare += (total_jumps - threshold_jumps) * t["inc2"]
    return fare


# Taxi tolls verified: Tai Lam effective 2025-05-31 ($28), Aberdeen & Shing Mun
# effective 2025-09-21 ($8); cross-harbour $25 flat per TD time-varying scheme
# (fixed-charge vehicle class); Lantau Link $30.
_TUNNEL_TOLLS: dict[Tunnel, Decimal] = {
    Tunnel.CROSS_HARBOUR: Decimal("25"),
    Tunnel.TAI_LAM: Decimal("28"),
    Tunnel.TATES_CAIRN: Decimal("20"),
    Tunnel.LION_ROCK: Decimal("8"),
    Tunnel.EAGLES_NEST: Decimal("8"),
    Tunnel.SHING_MUN: Decimal("8"),
    Tunnel.ABERDEEN: Decimal("8"),
    Tunnel.LANTAU_LINK: Decimal("30"),
}

_TUNNEL_NAMES: dict[Tunnel, tuple[str, str]] = {
    Tunnel.CROSS_HARBOUR: ("Cross-harbour tunnel (CHT/EHC/WHC)", "過海隧道（紅隧/東隧/西隧）"),
    Tunnel.TAI_LAM: ("Tai Lam Tunnel", "大欖隧道"),
    Tunnel.TATES_CAIRN: ("Tates Cairn Tunnel", "大老山隧道"),
    Tunnel.LION_ROCK: ("Lion Rock Tunnel", "獅子山隧道"),
    Tunnel.EAGLES_NEST: ("Eagle's Nest Tunnel", "尖山隧道"),
    Tunnel.SHING_MUN: ("Shing Mun Tunnel", "城門隧道"),
    Tunnel.ABERDEEN: ("Aberdeen Tunnel", "香港仔隧道"),
    Tunnel.LANTAU_LINK: ("Lantau Link", "青嶼幹線"),
}

_METER_TARIFF_DATE = "2024-07-14"
_TOLL_TARIFF_DATE = "2025-09-21"
_CROSS_HARBOUR_RETURN_FEE = Decimal("25")
_BAGGAGE_FEE = Decimal("6")
_ANIMAL_FEE = Decimal("5")
_ADVANCE_BOOKING_FEE = Decimal("5")
_MAX_BAGGAGE = 10
_MAX_ANIMALS = 5

_DISCLAIMER_EN = (
    "Fare estimate for reference only. The platform is an information "
    "intermediary; the final fare is settled voluntarily between passenger and "
    "driver (Cap. 374D). Tolls and surcharges depend on the actual route taken."
)
_DISCLAIMER_ZH = (
    "車費估價僅供參考。平台僅屬資訊中介，最終車資由乘客與司機自願協商確認（香港法例第374D章）。"
    "隧道費及附加費視乎實際路線而定。"
)


def calculate_fare(
    *,
    taxi_type: TaxiType,
    distance_km: Decimal | str | float,
    waiting_min: Decimal | str | float = 0,
    tunnels: tuple[Tunnel, ...] | list[Tunnel] = (),
    pickup_at_cross_harbour_stand: bool = False,
    crosses_harbour: bool = False,
    baggage_count: int = 0,
    animals: int = 0,
    advance_booking: bool = False,
    discount_percent: Decimal | str | float = 0,
    tip: Decimal | str | float = 0,
) -> FareBreakdown:
    """Full fare estimate: meter + tunnel tolls + other surcharges + discount + tip.

    Discount applies to the meter fare only (market convention: tunnel tolls are
    never discounted). The return fee for cross-harbour trips is charged unless
    the passenger boards at a cross-harbour taxi stand or the destination is on
    the same side of the harbour.
    """
    distance = _as_decimal(distance_km)
    waiting = _as_decimal(waiting_min)
    meter_fare = estimate_meter_fare(taxi_type, distance, waiting)

    tunnels = tuple(dict.fromkeys(tunnels))  # dedupe, preserve order
    unknown = [t for t in tunnels if t not in _TUNNEL_TOLLS]
    if unknown:
        raise ValueError(f"unknown tunnels: {unknown}")
    if crosses_harbour and Tunnel.CROSS_HARBOUR not in tunnels:
        raise ValueError("crosses_harbour=True requires the cross-harbour tunnel")
    if pickup_at_cross_harbour_stand and Tunnel.CROSS_HARBOUR not in tunnels:
        raise ValueError("pickup_at_cross_harbour_stand requires the cross-harbour tunnel")
    if not 0 <= baggage_count <= _MAX_BAGGAGE:
        raise ValueError(f"baggage_count must be within [0, {_MAX_BAGGAGE}]")
    if not 0 <= animals <= _MAX_ANIMALS:
        raise ValueError(f"animals must be within [0, {_MAX_ANIMALS}]")
    discount_percent = _as_decimal(discount_percent)
    tip = _as_decimal(tip)
    if not 0 <= discount_percent <= 100:
        raise ValueError("discount_percent must be within [0, 100]")
    if tip < 0:
        raise ValueError("tip must be non-negative")

    surcharges: list[SurchargeItem] = []
    for tunnel in tunnels:
        amount = _TUNNEL_TOLLS[tunnel]
        surcharges.append(
            SurchargeItem(
                code=f"tunnel_{tunnel.value}",
                name_en=_TUNNEL_NAMES[tunnel][0],
                name_zh=_TUNNEL_NAMES[tunnel][1],
                amount=amount,
            )
        )
    return_fee_applicable = (
        Tunnel.CROSS_HARBOUR in tunnels and crosses_harbour and not pickup_at_cross_harbour_stand
    )
    if return_fee_applicable:
        surcharges.append(
            SurchargeItem(
                code="cross_harbour_return",
                name_en="Cross-harbour return fee",
                name_zh="過海回程費",
                amount=_CROSS_HARBOUR_RETURN_FEE,
            )
        )
    if baggage_count:
        surcharges.append(
            SurchargeItem(
                code="baggage",
                name_en="Baggage (per item)",
                name_zh="行李（每件）",
                amount=_BAGGAGE_FEE * baggage_count,
            )
        )
    if animals:
        surcharges.append(
            SurchargeItem(
                code="animals",
                name_en="Animal/bird (each)",
                name_zh="動物或雀鳥（每隻）",
                amount=_ANIMAL_FEE * animals,
            )
        )
    if advance_booking:
        surcharges.append(
            SurchargeItem(
                code="advance_booking",
                name_en="Advance booking",
                name_zh="電召預約服務",
                amount=_ADVANCE_BOOKING_FEE,
            )
        )

    surcharges_total = sum((s.amount for s in surcharges), Decimal("0"))
    discount = (
        (meter_fare * discount_percent / Decimal("100")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        if discount_percent
        else Decimal("0.0")
    )
    meter_after_discount = meter_fare - discount
    total = meter_after_discount + surcharges_total + tip
    tariff_version = f"meter:{_METER_TARIFF_DATE};tolls:{_TOLL_TARIFF_DATE}"

    return FareBreakdown(
        taxi_type=taxi_type,
        distance_km=distance,
        waiting_min=waiting,
        meter_fare=meter_fare,
        discount_percent=discount_percent,
        meter_discount=discount,
        meter_after_discount=meter_after_discount,
        surcharges=tuple(surcharges),
        surcharges_total=surcharges_total,
        tip=tip,
        total_fare=total,
        tariff_version=tariff_version,
        is_estimate=True,
        disclaimer_en=_DISCLAIMER_EN,
        disclaimer_zh=_DISCLAIMER_ZH,
    )
