import '../core/format/money.dart';
import '../core/network/wire.dart';
import 'enums.dart';
import 'fare.dart';

/// A premium destination from `GET /api/v1/destinations` — a map pin with an
/// avatar key, e.g. Airport T1/T2, Cathay City or the Civil Aviation Department.
class PremiumDestination {
  const PremiumDestination({
    required this.id,
    required this.code,
    required this.nameZh,
    required this.nameEn,
    required this.lat,
    required this.lng,
    required this.radiusM,
    required this.status,
    this.avatarKey,
  });

  factory PremiumDestination.fromJson(Map<String, dynamic> json) =>
      PremiumDestination(
        id: asString(json['id'], 'premium_destination.id'),
        code: asString(json['code'], 'premium_destination.code'),
        nameZh: asString(json['name_zh'], 'premium_destination.name_zh'),
        nameEn: asString(json['name_en'], 'premium_destination.name_en'),
        lat: asDouble(json['lat'], 'premium_destination.lat'),
        lng: asDouble(json['lng'], 'premium_destination.lng'),
        radiusM: asInt(json['radius_m'], 'premium_destination.radius_m'),
        status: asString(json['status'], 'premium_destination.status'),
        avatarKey: asStringOrNull(json['avatar_key'], 'premium_destination.avatar_key'),
      );

  final String id;
  final String code;
  final String nameZh;
  final String nameEn;
  final double lat;
  final double lng;
  final int radiusM;
  final String status;
  final String? avatarKey;
}

/// `GET /api/v1/destinations` — the public premium-destination map pins.
class PremiumDestinationPage {
  const PremiumDestinationPage({required this.items});

  factory PremiumDestinationPage.fromJson(Map<String, dynamic> json) =>
      PremiumDestinationPage(
        items: asObjectList(
          json['items'],
          'premium_destination.items',
          PremiumDestination.fromJson,
        ),
      );

  final List<PremiumDestination> items;
}

/// The fare snapshot frozen into `orders.fare_json` at creation time.
///
/// It is deliberately a *copy* rather than a live recomputation, so a historical
/// order stays auditable after a tariff change (`app/services/order_service.py`,
/// `fare_snapshot`). Its keys differ slightly from [FareEstimate] — there is no
/// `meter_discount` here, and `tunnels` is the deduped canonical set.
class FareSnapshot {
  const FareSnapshot({
    required this.meterFare,
    required this.meterAfterDiscount,
    required this.surchargesTotal,
    required this.tip,
    required this.totalFare,
    required this.discountPercent,
    required this.tunnels,
    required this.crossesHarbour,
    required this.tariffVersion,
    required this.isEstimate,
    required this.disclaimerEn,
    required this.disclaimerZh,
    required this.surcharges,
  });

  factory FareSnapshot.fromJson(Map<String, dynamic> json) => FareSnapshot(
    meterFare: Money.parse(json['meter_fare']),
    meterAfterDiscount: Money.parse(json['meter_after_discount']),
    surchargesTotal: Money.parse(json['surcharges_total']),
    tip: Money.parse(json['tip']),
    totalFare: Money.parse(json['total_fare']),
    discountPercent: asDouble(json['discount_percent'], 'fare.discount_percent'),
    tunnels: asEnumList(json['tunnels'], 'tunnels', Tunnel.fromWire),
    crossesHarbour: asBool(json['crosses_harbour'], 'crosses_harbour'),
    tariffVersion: asString(json['tariff_version'], 'tariff_version'),
    isEstimate: asBool(json['is_estimate'], 'fare.is_estimate'),
    disclaimerEn: asString(json['disclaimer_en'], 'disclaimer_en'),
    disclaimerZh: asString(json['disclaimer_zh'], 'disclaimer_zh'),
    surcharges: asObjectList(json['surcharges'], 'surcharges', FareSurcharge.fromJson),
  );

  final Money meterFare;
  final Money meterAfterDiscount;
  final Money surchargesTotal;
  final Money tip;
  final Money totalFare;

  /// The discount the passenger asked for, applied to the meter only. Zero for
  /// an order placed without one, so the receipt hides the line rather than
  /// showing `-HK$0.00`.
  final double discountPercent;

  final List<Tunnel> tunnels;
  final bool crossesHarbour;
  final String tariffVersion;

  /// Always true server-side (`order_service.fare_snapshot`), and the reason the
  /// receipt must be labelled an estimate rather than a quote — Cap. 374D, the
  /// platform is an information intermediary and the final fare is agreed
  /// between passenger and driver. Rendered, not assumed: if the server ever
  /// stops stamping it, the label should disappear with it.
  final bool isEstimate;

  final String disclaimerEn;
  final String disclaimerZh;
  final List<FareSurcharge> surcharges;

  /// The meter had a discount applied — so the receipt shows both the original
  /// meter fare and the discounted one, instead of one unexplained number.
  bool get hasDiscount => discountPercent > 0;
}

/// `order_out()` in `app/services/order_service.py` — the shape returned by
/// every order endpoint: create, list, detail, grab, arrive, start, complete,
/// cancel, and each item of `/orders/nearby`.
class Order {
  const Order({
    required this.id,
    required this.status,
    required this.taxiType,
    required this.fare,
    required this.estimatedTotalHkd,
    required this.completedAt,
    required this.createdAt,
    this.requirements,
    this.paymentPreference = const <String>[],
    this.driverPaymentMethods = const <String>[],
    this.premiumDestination,
    this.destinationArea,
  });

  factory Order.fromJson(Map<String, dynamic> json) => Order(
    id: asString(json['id'], 'order.id'),
    status: OrderStatus.fromWire(asString(json['status'], 'order.status')),
    taxiType: TaxiType.fromWire(asString(json['taxi_type'], 'order.taxi_type')),
    fare: FareSnapshot.fromJson(asMap(json['fare'], 'order.fare')),
    estimatedTotalHkd: Money.parse(json['estimated_total_hkd']),
    completedAt: asDateOrNull(json['completed_at'], 'order.completed_at'),
    createdAt: asDateOrNull(json['created_at'], 'order.created_at'),
    requirements:
        json['requirements'] == null
            ? null
            : asMap(json['requirements'], 'order.requirements'),
    paymentPreference: asStringListOrEmpty(json['payment_preference'], 'order.payment_preference'),
    driverPaymentMethods:
        asStringListOrEmpty(json['driver_payment_methods'], 'order.driver_payment_methods'),
    premiumDestination:
        json['premium_destination'] == null
            ? null
            : PremiumDestination.fromJson(
                asMap(json['premium_destination'], 'order.premium_destination'),
              ),
    destinationArea: asStringOrNull(json['destination_area'], 'order.destination_area'),
  );

  final String id;
  final OrderStatus status;
  final TaxiType taxiType;
  final FareSnapshot fare;
  final Money estimatedTotalHkd;
  final DateTime? completedAt;
  final DateTime? createdAt;

  /// The passenger's frozen ride requirements (silence, no radio/music, no
  /// smoke, no perfume, animal details). Optional — older orders have none.
  final Map<String, dynamic>? requirements;

  /// The passenger's requested payment methods; informational, not a guarantee.
  final List<String> paymentPreference;

  /// The assigned driver's declared methods, copied onto the order at grab.
  final List<String> driverPaymentMethods;

  /// Auto-detected premium destination snapshot on the order.
  final PremiumDestination? premiumDestination;

  /// Coarse destination area (e.g. `AIRPORT`) derived server-side.
  final String? destinationArea;
}

/// `GET /api/v1/orders` — newest first, keyset-paginated.
///
/// The `before_id` cursor is resolved **inside the caller's own scope**
/// (`SEC-26`), so passing another user's order id yields 404 `cursor not found`
/// rather than 403. There is no way to tell "not yours" from "does not exist",
/// which is the point — do not try to surface a distinction in the UI.
class OrderPage {
  const OrderPage({required this.items, required this.limit});

  factory OrderPage.fromJson(Map<String, dynamic> json, {int limit = 20}) =>
      OrderPage(items: asObjectList(json['items'], 'items', Order.fromJson), limit: limit);

  final List<Order> items;

  /// The page size the request used — the server returns no cursor, so
  /// "there is another page" is inferred from a full page.
  final int limit;

  /// The id to pass as `before_id` for the next page, or null at the end.
  String? get nextCursor => items.length < limit ? null : items.last.id;
}

/// `GET /api/v1/orders/nearby`. `degraded` is true when the Redis geo index was
/// unreachable and the server failed open with an empty page (`P2-10`) — the
/// driver's map should say "searching" rather than "no orders".
class NearbyOrders {
  const NearbyOrders({required this.items, required this.degraded});

  factory NearbyOrders.fromJson(Map<String, dynamic> json) => NearbyOrders(
    items: asObjectList(json['items'], 'items', Order.fromJson),
    degraded: json['degraded'] as bool? ?? false,
  );

  final List<Order> items;
  final bool degraded;
}

/// Body of `POST /api/v1/orders`. Note `distance_km` is **client-supplied** —
/// the server does not recompute it from the coordinates — so the request screen
/// is responsible for a sane value, and the server bounds it to (0, 100].
class OrderCreateRequest {
  const OrderCreateRequest({
    required this.pickupLat,
    required this.pickupLng,
    required this.dropoffLat,
    required this.dropoffLng,
    required this.pickupAddress,
    required this.dropoffAddress,
    required this.distanceKm,
    required this.taxiType,
    this.waitingMin = 0,
    this.discountPercent = 0,
    this.tip = 0,
    this.tunnels = const <Tunnel>[],
    this.crossesHarbour = false,
    this.pickupAtCrossHarbourStand = false,
    this.requirements,
    this.paymentPreference = const <String>[],
  });

  final double pickupLat;
  final double pickupLng;
  final double dropoffLat;
  final double dropoffLng;
  final String pickupAddress;
  final String dropoffAddress;
  final double distanceKm;
  final TaxiType taxiType;
  final double waitingMin;
  final double discountPercent;
  final double tip;
  final List<Tunnel> tunnels;
  final bool crossesHarbour;
  final bool pickupAtCrossHarbourStand;

  /// Optional ride requirements: silent ride, no radio/music, no smoke/perfume,
  /// and animal details before a driver sees the order.
  final Map<String, dynamic>? requirements;

  /// Optional requested payment methods; informational, not a guarantee.
  final List<String> paymentPreference;

  Map<String, dynamic> toJson() => <String, dynamic>{
    'pickup_lat': pickupLat,
    'pickup_lng': pickupLng,
    'dropoff_lat': dropoffLat,
    'dropoff_lng': dropoffLng,
    'pickup_address': pickupAddress,
    'dropoff_address': dropoffAddress,
    'distance_km': distanceKm.toString(),
    'waiting_min': waitingMin.toString(),
    'taxi_type': taxiType.wire,
    'discount_percent': discountPercent.toString(),
    'tip': tip.toString(),
    // The server rejects more than 8 (SEC-09); the UI cannot offer more.
    'tunnels': tunnels.map((Tunnel t) => t.wire).toList(growable: false),
    'crosses_harbour': crossesHarbour,
    'pickup_at_cross_harbour_stand': pickupAtCrossHarbourStand,
    'requirements': requirements,
    'payment_preference': paymentPreference,
  };
}
