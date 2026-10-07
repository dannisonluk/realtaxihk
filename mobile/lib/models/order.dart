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

  factory PremiumDestination.fromJson(Map<String, dynamic> json) => PremiumDestination(
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

  factory PremiumDestinationPage.fromJson(Map<String, dynamic> json) => PremiumDestinationPage(
    items: asObjectList(json['items'], 'premium_destination.items', PremiumDestination.fromJson),
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
    this.fareMode,
    this.driverPriceHkd,
    this.platformFeeHkd,
    this.passengerPriceHkd,
    this.distanceSource,
    this.isDestinationChange = false,
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
    fareMode: asStringOrNull(json['fare_mode'], 'fare.fare_mode'),
    driverPriceHkd: asMoneyOrNull(json['driver_price_hkd'], 'fare.driver_price_hkd'),
    platformFeeHkd: asMoneyOrNull(json['platform_fee_hkd'], 'fare.platform_fee_hkd'),
    passengerPriceHkd: asMoneyOrNull(json['passenger_price_hkd'], 'fare.passenger_price_hkd'),
    distanceSource: asStringOrNull(json['distance_source'], 'fare.distance_source'),
    isDestinationChange: json['is_destination_change'] as bool? ?? false,
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

  /// Phase 2 fixed-fare (一口價) metadata. Null on METER orders.
  final String? fareMode;
  final Money? driverPriceHkd;
  final Money? platformFeeHkd;
  final Money? passengerPriceHkd;

  /// P4: how the distance behind a **re-priced** snapshot was obtained —
  /// `client_route` when the client supplied a routed distance, `straight_line`
  /// when the server fell back to the PostGIS line between pickup and the new
  /// dropoff. Null on a snapshot that has never been re-priced. A straight line
  /// is a *lower bound*, so the UI should not present the two the same way.
  final String? distanceSource;

  /// P4: true only on a snapshot written by a destination change. Lets the
  /// receipt say "新估價" rather than pretending the price was always this.
  final bool isDestinationChange;

  /// The meter had a discount applied — so the receipt shows both the original
  /// meter fare and the discounted one, instead of one unexplained number.
  bool get hasDiscount => discountPercent > 0;

  /// True when the re-price used a straight line rather than a driven route.
  bool get distanceIsStraightLine => distanceSource == 'straight_line';
}

/// The landmark snapshot attached to a scheduled order response.
class DropoffLandmark {
  const DropoffLandmark({
    required this.code,
    required this.nameEn,
    required this.nameZh,
    required this.category,
  });

  factory DropoffLandmark.fromJson(Map<String, dynamic> json) => DropoffLandmark(
    code: asString(json['code'], 'dropoff_landmark.code'),
    nameEn: asString(json['name_en'], 'dropoff_landmark.name_en'),
    nameZh: asString(json['name_zh'], 'dropoff_landmark.name_zh'),
    category: LandmarkCategory.fromWire(asString(json['category'], 'dropoff_landmark.category')),
  );

  final String code;
  final String nameEn;
  final String nameZh;
  final LandmarkCategory category;

  String get labelZh => nameZh.isEmpty ? nameEn : nameZh;

  String get labelEn => nameEn.isEmpty ? nameZh : nameEn;

  Map<String, dynamic> toJson() => <String, dynamic>{
    'code': code,
    'name_en': nameEn,
    'name_zh': nameZh,
    'category': category.wire,
  };
}

/// `order_out()` in `app/services/order_service.py` — the shape returned by
/// every order endpoint: create, list, detail, grab, arrival-claim,
/// arrival-confirm, start, complete, change-destination, interrupt, cancel, and
/// each item of `/orders/nearby`.
class Order {
  const Order({
    required this.id,
    required this.status,
    required this.taxiType,
    required this.fare,
    required this.estimatedTotalHkd,
    required this.completedAt,
    required this.createdAt,
    this.orderKind,
    this.scheduledPickupAt,
    this.prebookVisibleFrom,
    this.prebookState,
    this.dropoffLandmarkId,
    this.dropoffLandmark,
    this.requirements,
    this.paymentPreference = const <String>[],
    this.driverPaymentMethods = const <String>[],
    this.premiumDestination,
    this.destinationArea,
    this.pickupArea,
    this.pickupLat,
    this.pickupLng,
    this.pickupAddress,
    this.dropoffLat,
    this.dropoffLng,
    this.dropoffAddress,
    this.distanceKm,
    this.fareMode,
    this.fixedOfferId,
    this.driverPriceHkd,
    this.platformFeeHkd,
    this.passengerPriceHkd,
    this.startedAt,
    this.arrivalConfirmedAt,
    this.destinationChangeCount = 0,
    this.interruptionReason,
    this.interruptedAt,
    this.interruptedByKind,
  });

  factory Order.fromJson(Map<String, dynamic> json) => Order(
    id: asString(json['id'], 'order.id'),
    status: OrderStatus.fromWire(asString(json['status'], 'order.status')),
    taxiType: TaxiType.fromWire(asString(json['taxi_type'], 'order.taxi_type')),
    fare: FareSnapshot.fromJson(asMap(json['fare'], 'order.fare')),
    estimatedTotalHkd: Money.parse(json['estimated_total_hkd']),
    completedAt: asDateOrNull(json['completed_at'], 'order.completed_at'),
    createdAt: asDateOrNull(json['created_at'], 'order.created_at'),
    orderKind: json['order_kind'] == null
        ? null
        : OrderKind.fromWire(asString(json['order_kind'], 'order.order_kind')),
    scheduledPickupAt: asDateOrNull(json['scheduled_pickup_at'], 'order.scheduled_pickup_at'),
    prebookVisibleFrom: asDateOrNull(json['prebook_visible_from'], 'order.prebook_visible_from'),
    prebookState: json['prebook_state'] == null
        ? null
        : PrebookState.fromWire(asString(json['prebook_state'], 'order.prebook_state')),
    dropoffLandmarkId: asStringOrNull(json['dropoff_landmark_id'], 'order.dropoff_landmark_id'),
    dropoffLandmark: json['dropoff_landmark'] == null
        ? null
        : DropoffLandmark.fromJson(asMap(json['dropoff_landmark'], 'order.dropoff_landmark')),
    requirements: json['requirements'] == null
        ? null
        : asMap(json['requirements'], 'order.requirements'),
    paymentPreference: asStringListOrEmpty(json['payment_preference'], 'order.payment_preference'),
    driverPaymentMethods: asStringListOrEmpty(
      json['driver_payment_methods'],
      'order.driver_payment_methods',
    ),
    premiumDestination: json['premium_destination'] == null
        ? null
        : PremiumDestination.fromJson(
            asMap(json['premium_destination'], 'order.premium_destination'),
          ),
    destinationArea: asStringOrNull(json['destination_area'], 'order.destination_area'),
    pickupArea: asStringOrNull(json['pickup_area'], 'order.pickup_area'),
    pickupLat: asDoubleOrNull(json['pickup_lat'], 'order.pickup_lat'),
    pickupLng: asDoubleOrNull(json['pickup_lng'], 'order.pickup_lng'),
    pickupAddress: asStringOrNull(json['pickup_address'], 'order.pickup_address'),
    dropoffLat: asDoubleOrNull(json['dropoff_lat'], 'order.dropoff_lat'),
    dropoffLng: asDoubleOrNull(json['dropoff_lng'], 'order.dropoff_lng'),
    dropoffAddress: asStringOrNull(json['dropoff_address'], 'order.dropoff_address'),
    distanceKm: asDoubleOrNull(json['distance_km'], 'order.distance_km'),
    fareMode: asStringOrNull(json['fare_mode'], 'order.fare_mode'),
    fixedOfferId: asStringOrNull(json['fixed_offer_id'], 'order.fixed_offer_id'),
    driverPriceHkd: asMoneyOrNull(json['driver_price_hkd'], 'order.driver_price_hkd'),
    platformFeeHkd: asMoneyOrNull(json['platform_fee_hkd'], 'order.platform_fee_hkd'),
    passengerPriceHkd: asMoneyOrNull(json['passenger_price_hkd'], 'order.passenger_price_hkd'),
    startedAt: asDateOrNull(json['started_at'], 'order.started_at'),
    arrivalConfirmedAt: asDateOrNull(json['arrival_confirmed_at'], 'order.arrival_confirmed_at'),
    destinationChangeCount: asIntOrNull(json['destination_change_count'], 'order.dc_count') ?? 0,
    interruptionReason: json['interruption_reason'] == null
        ? null
        : InterruptionReason.fromWire(
            asString(json['interruption_reason'], 'order.interruption_reason'),
          ),
    interruptedAt: asDateOrNull(json['interrupted_at'], 'order.interrupted_at'),
    interruptedByKind: asStringOrNull(json['interrupted_by_kind'], 'order.interrupted_by_kind'),
  );

  final String id;
  final OrderStatus status;
  final TaxiType taxiType;
  final FareSnapshot fare;
  final Money estimatedTotalHkd;
  final DateTime? completedAt;
  final DateTime? createdAt;

  final OrderKind? orderKind;
  final DateTime? scheduledPickupAt;
  final DateTime? prebookVisibleFrom;
  final PrebookState? prebookState;
  final String? dropoffLandmarkId;
  final DropoffLandmark? dropoffLandmark;

  /// The passenger's frozen ride requirements (silent ride, no radio/music, no
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

  /// Coarse pickup area (e.g. `KOWLOON`) derived server-side.
  final String? pickupArea;

  /// Phase 3 route template. The server only emits these on order payloads
  /// after the route fields were added; old fixtures decode as null.
  final double? pickupLat;
  final double? pickupLng;
  final String? pickupAddress;
  final double? dropoffLat;
  final double? dropoffLng;
  final String? dropoffAddress;
  final double? distanceKm;

  bool get hasRouteTemplate =>
      pickupLat != null && pickupLng != null && dropoffLat != null && dropoffLng != null;

  /// Builds the request body for `POST /orders` from a history row, so
  /// "book again" does not need the user to re-tap the map.
  OrderCreateRequest? toOrderCreateRequest() {
    if (!hasRouteTemplate || distanceKm == null) {
      return null;
    }
    return OrderCreateRequest(
      pickupLat: pickupLat!,
      pickupLng: pickupLng!,
      dropoffLat: dropoffLat!,
      dropoffLng: dropoffLng!,
      pickupAddress: pickupAddress ?? '',
      dropoffAddress: dropoffAddress ?? '',
      distanceKm: distanceKm!,
      taxiType: taxiType,
      requirements: requirements,
      paymentPreference: paymentPreference,
      discountPercent: fare.discountPercent,
      tip: fare.tip.asDouble,
      tunnels: fare.tunnels,
      crossesHarbour: fare.crossesHarbour,
      orderKind: orderKind,
      scheduledPickupAt: scheduledPickupAt,
      dropoffLandmarkId: dropoffLandmarkId,
    );
  }

  /// Phase 2 fixed-fare (一口價) fields. Null/empty on METER orders.
  final String? fareMode;
  final String? fixedOfferId;
  final Money? driverPriceHkd;
  final Money? platformFeeHkd;
  final Money? passengerPriceHkd;

  /// P4: stamped at `/start`. Null before departure.
  final DateTime? startedAt;

  /// P4: stamped when the passenger confirmed arrival. Its presence is the
  /// client-side evidence that the cancel right is locked — the server also
  /// refuses with 409 `CANCEL_LOCKED`, but showing a cancel button that can
  /// only fail is worse than not showing one (P4 §6.1).
  final DateTime? arrivalConfirmedAt;

  /// P4: how many times the destination has been changed. The server caps it
  /// (`max_destination_changes`); the UI disables the action at the cap rather
  /// than letting the user discover a 429.
  final int destinationChangeCount;

  /// P4: why the trip ended early. Null unless `status == interrupted`.
  final InterruptionReason? interruptionReason;

  /// P4: when it ended early.
  final DateTime? interruptedAt;

  /// P4: which side ended it — `'PASSENGER'` / `'DRIVER'`. A string rather than
  /// an enum because the value is informational here and the client only needs
  /// "was it me", which is answered by comparing to the caller's own role.
  final String? interruptedByKind;

  /// P4: the trip is under way, so the in-trip actions apply.
  bool get isUnderWay => status == OrderStatus.inTrip || status == OrderStatus.destinationChanged;
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
    this.orderKind,
    this.scheduledPickupAt,
    this.dropoffLandmarkId,
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

  /// Pre-booking fields. `ON_DEMAND` and a null scheduled time are omitted,
  /// keeping older requests byte-compatible with the backend.
  final OrderKind? orderKind;
  final DateTime? scheduledPickupAt;
  final String? dropoffLandmarkId;

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
    'order_kind': ?orderKind?.wire,
    'scheduled_pickup_at': ?scheduledPickupAt?.toUtc().toIso8601String(),
    'dropoff_landmark_id': ?dropoffLandmarkId,
  };
}
