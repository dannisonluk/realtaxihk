import '../core/format/money.dart';
import '../core/network/wire.dart';

/// `POST|GET /api/v1/orders/{id}/receipt` — a frozen receipt document.
///
/// Every field is read from the order's own frozen columns, so two reads of the
/// same receipt return identical bytes. There is deliberately no `toJson`: a
/// client never sends a receipt back.
class Receipt {
  const Receipt({
    required this.orderId,
    required this.issuedAt,
    required this.status,
    required this.taxiType,
    required this.pickupAddress,
    required this.dropoffAddress,
    required this.distanceKm,
    required this.totalHkd,
    required this.fare,
    required this.text,
    required this.disclaimerZh,
    required this.disclaimerEn,
    required this.tariffVersion,
    this.fareMode,
    this.pickupArea,
    this.destinationArea,
    this.premiumDestination,
    this.fixedFare,
    this.requirements,
    this.passengerName,
    this.createdAt,
    this.completedAt,
    this.paymentPreference = const <String>[],
    this.driverPaymentMethods = const <String>[],
  });

  factory Receipt.fromJson(Map<String, dynamic> json) => Receipt(
    orderId: asString(json['order_id'], 'receipt.order_id'),
    issuedAt: asString(json['issued_at'], 'receipt.issued_at'),
    status: asString(json['status'], 'receipt.status'),
    taxiType: asString(json['taxi_type'], 'receipt.taxi_type'),
    pickupAddress: asString(json['pickup_address'], 'receipt.pickup_address'),
    dropoffAddress: asString(json['dropoff_address'], 'receipt.dropoff_address'),
    distanceKm: asString(json['distance_km'], 'receipt.distance_km'),
    totalHkd: Money.parse(json['total_hkd']),
    fare: asMap(json['fare'], 'receipt.fare'),
    text: asString(json['text'], 'receipt.text'),
    disclaimerZh: asString(json['disclaimer_zh'], 'receipt.disclaimer_zh'),
    disclaimerEn: asString(json['disclaimer_en'], 'receipt.disclaimer_en'),
    tariffVersion: asString(json['tariff_version'], 'receipt.tariff_version'),
    fareMode: asStringOrNull(json['fare_mode'], 'receipt.fare_mode'),
    pickupArea: asStringOrNull(json['pickup_area'], 'receipt.pickup_area'),
    destinationArea: asStringOrNull(json['destination_area'], 'receipt.destination_area'),
    premiumDestination: json['premium_destination'] == null
        ? null
        : asMap(json['premium_destination'], 'receipt.premium_destination'),
    fixedFare: json['fixed_fare'] == null ? null : asMap(json['fixed_fare'], 'receipt.fixed_fare'),
    requirements: json['requirements'] == null
        ? null
        : asMap(json['requirements'], 'receipt.requirements'),
    passengerName: asStringOrNull(json['passenger_name'], 'receipt.passenger_name'),
    createdAt: asDateOrNull(json['created_at'], 'receipt.created_at'),
    completedAt: asDateOrNull(json['completed_at'], 'receipt.completed_at'),
    paymentPreference: asStringListOrEmpty(
      json['payment_preference'],
      'receipt.payment_preference',
    ),
    driverPaymentMethods: asStringListOrEmpty(
      json['driver_payment_methods'],
      'receipt.driver_payment_methods',
    ),
  );

  final String orderId;
  final String issuedAt;
  final String status;
  final String taxiType;
  final String pickupAddress;
  final String dropoffAddress;
  final String distanceKm;

  /// The stored `Numeric(10,2)` total — 2 dp. The figures inside [fare] are
  /// meter readings and are 1 dp; the two rules are not interchangeable.
  final Money totalHkd;

  /// The order's frozen `fare_json` block, kept as a map rather than re-typed
  /// here — `FareSnapshot` in `order.dart` already parses it, and a second
  /// declaration would drift from that one.
  final Map<String, dynamic> fare;

  /// The rendered document, produced server-side from the same frozen snapshot.
  /// Show or share this rather than re-implementing the layout client-side.
  final String text;

  final String disclaimerZh;
  final String disclaimerEn;
  final String tariffVersion;

  /// `METER` or `FIXED`; null on older orders.
  final String? fareMode;
  final String? pickupArea;
  final String? destinationArea;
  final Map<String, dynamic>? premiumDestination;

  /// Present only on a fixed-fare (一口價) order: the driver payout and the
  /// platform fee as separate amounts, so the split is auditable.
  final Map<String, dynamic>? fixedFare;

  final Map<String, dynamic>? requirements;
  final String? passengerName;
  final DateTime? createdAt;
  final DateTime? completedAt;
  final List<String> paymentPreference;
  final List<String> driverPaymentMethods;

  /// True when this trip was priced as a 一口價 rather than on the meter.
  bool get isFixedFare => fareMode == 'FIXED';
}
