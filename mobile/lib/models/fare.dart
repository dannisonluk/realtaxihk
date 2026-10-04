import '../core/format/money.dart';
import '../core/network/wire.dart';
import 'enums.dart';

/// One line of the fare breakdown. `code` is stable (`tunnel_<tunnel>`,
/// `cross_harbour_return`, `baggage`, `animals`, `advance_booking`) — match on
/// it rather than on the localised name.
class FareSurcharge {
  const FareSurcharge({
    required this.code,
    required this.nameEn,
    required this.nameZh,
    required this.amount,
  });

  factory FareSurcharge.fromJson(Map<String, dynamic> json) => FareSurcharge(
    code: asString(json['code'], 'surcharge.code'),
    nameEn: asString(json['name_en'], 'surcharge.name_en'),
    nameZh: asString(json['name_zh'], 'surcharge.name_zh'),
    amount: Money.parse(json['amount']),
  );

  final String code;
  final String nameEn;
  final String nameZh;
  final Money amount;
}

/// `POST /api/v1/fare/estimate`. Public — no token required, so the request
/// screen can quote a price before the user signs in. Rate-limited per IP.
class FareEstimate {
  const FareEstimate({
    required this.taxiType,
    required this.distanceKm,
    required this.waitingMin,
    required this.meterFare,
    required this.discountPercent,
    required this.meterDiscount,
    required this.meterAfterDiscount,
    required this.surcharges,
    required this.surchargesTotal,
    required this.tip,
    required this.totalFare,
    required this.tariffVersion,
    required this.isEstimate,
    required this.disclaimerEn,
    required this.disclaimerZh,
  });

  factory FareEstimate.fromJson(Map<String, dynamic> json) => FareEstimate(
    taxiType: TaxiType.fromWire(asString(json['taxi_type'], 'taxi_type')),
    distanceKm: asDouble(json['distance_km'], 'distance_km'),
    waitingMin: asDouble(json['waiting_min'], 'waiting_min'),
    meterFare: Money.parse(json['meter_fare']),
    discountPercent: Money.parse(json['discount_percent']),
    meterDiscount: Money.parse(json['meter_discount']),
    meterAfterDiscount: Money.parse(json['meter_after_discount']),
    surcharges: asObjectList(json['surcharges'], 'surcharges', FareSurcharge.fromJson),
    surchargesTotal: Money.parse(json['surcharges_total']),
    tip: Money.parse(json['tip']),
    totalFare: Money.parse(json['total_fare']),
    tariffVersion: asString(json['tariff_version'], 'tariff_version'),
    isEstimate: json['is_estimate'] as bool? ?? true,
    disclaimerEn: asString(json['disclaimer_en'], 'disclaimer_en'),
    disclaimerZh: asString(json['disclaimer_zh'], 'disclaimer_zh'),
  );

  final TaxiType taxiType;
  final double distanceKm;
  final double waitingMin;
  final Money meterFare;
  final Money discountPercent;
  final Money meterDiscount;
  final Money meterAfterDiscount;
  final List<FareSurcharge> surcharges;
  final Money surchargesTotal;
  final Money tip;
  final Money totalFare;
  final String tariffVersion;
  final bool isEstimate;

  /// Cap. 374D requires the quote to be labelled an estimate and to carry the
  /// disclaimer; the server sends both and the UI must render them.
  final String disclaimerEn;
  final String disclaimerZh;
}

/// Request body for the estimate. `Decimal` fields go out as strings to match
/// the server's own precision model — see `core/network/wire.dart`.
class FareEstimateRequest {
  const FareEstimateRequest({
    required this.taxiType,
    required this.distanceKm,
    this.waitingMin = 0,
    this.tunnels = const <Tunnel>[],
    this.pickupAtCrossHarbourStand = false,
    this.crossesHarbour = false,
    this.baggageCount = 0,
    this.animals = 0,
    this.advanceBooking = false,
    this.discountPercent = 0,
    this.tip = 0,
  });

  final TaxiType taxiType;
  final double distanceKm;
  final double waitingMin;
  final List<Tunnel> tunnels;
  final bool pickupAtCrossHarbourStand;
  final bool crossesHarbour;
  final int baggageCount;
  final int animals;
  final bool advanceBooking;
  final double discountPercent;
  final double tip;

  Map<String, dynamic> toJson() => <String, dynamic>{
    'taxi_type': taxiType.wire,
    'distance_km': distanceKm.toString(),
    'waiting_min': waitingMin.toString(),
    'tunnels': tunnels.map((Tunnel t) => t.wire).toList(growable: false),
    'pickup_at_cross_harbour_stand': pickupAtCrossHarbourStand,
    'crosses_harbour': crossesHarbour,
    'baggage_count': baggageCount,
    'animals': animals,
    'advance_booking': advanceBooking,
    'discount_percent': discountPercent.toString(),
    'tip': tip.toString(),
  };
}
