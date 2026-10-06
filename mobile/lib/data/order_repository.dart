import '../core/network/api_client.dart';
import '../models/enums.dart';
import '../models/nearby_filter.dart';
import '../models/order.dart';
import '../models/receipt.dart';

/// Every order endpoint, from both sides of the trip.
///
/// The server enforces *who* may call each one, so this class deliberately
/// exposes them all rather than splitting into passenger/driver variants:
/// `grab` requires an ACTIVE driver profile, the lifecycle transitions require
/// the assigned driver, `cancel` allows either party, and `detail` allows a
/// participant or an admin. A 403 is the answer when the caller is not entitled,
/// not a client-side check.
class OrderRepository {
  OrderRepository(this._api);

  final ApiClient _api;

  /// `POST /orders` (201). Rate-limited to 5 per minute per passenger.
  ///
  /// The fare is snapshotted server-side at this moment and frozen into
  /// `fare_json`, so the returned [Order] is the authoritative quote — do not
  /// show the client's own estimate afterwards.
  ///
  /// **P4:** a passenger who defaulted on a recent trip is in a 15-minute
  /// cool-down and this answers **429** `RATE_LIMITED` with `reason: COOLDOWN`
  /// and a `Retry-After` header. That is a "wait", not an "error" — read
  /// `e.reason` and `e.retryAfter` and say so, rather than showing the generic
  /// failure copy.
  Future<Order> create(OrderCreateRequest request) async {
    final Map<String, dynamic> json = await _api.post('/api/v1/orders', data: request.toJson());
    return Order.fromJson(json);
  }

  /// `GET /orders?role=…` — history, newest first.
  ///
  /// `role: 'driver'` returns orders assigned to the caller's driver profile;
  /// anything else returns orders they booked. [beforeId] is a keyset cursor
  /// that is resolved within the caller's own scope, so a foreign id yields 404
  /// rather than 403 (`SEC-26`) — there is no way to tell the two apart, and
  /// that is intentional.
  Future<OrderPage> history({String role = 'passenger', int limit = 20, String? beforeId}) async {
    final Map<String, dynamic> json = await _api.get(
      '/api/v1/orders',
      query: <String, dynamic>{'role': role, 'limit': limit, 'before_id': ?beforeId},
    );
    return OrderPage.fromJson(json, limit: limit);
  }

  /// `GET /orders/nearby` — open orders within [radiusKm] of a point.
  ///
  /// Returns `degraded: true` with an empty list when the Redis geo index is
  /// unavailable; the server fails open rather than 500-ing the driver's map
  /// (`P2-10`). Show "searching", not "no orders", in that case.
  ///
  /// [filter] is appended to the query only where set, so an unfiltered call
  /// stays byte-identical to the pre-filter client. The server validates each
  /// value and answers 422 for an unknown one — a filter that silently matched
  /// nothing would be the worse failure (`tests/api/test_nearby_filters.py`).
  Future<NearbyOrders> nearby({
    required double lat,
    required double lng,
    double radiusKm = 3,
    NearbyFilter? filter,
  }) async {
    final Map<String, dynamic> json = await _api.get(
      '/api/v1/orders/nearby',
      query: <String, dynamic>{
        'lat': lat,
        'lng': lng,
        'radius_km': radiusKm,
        ...?filter?.toQuery(),
      },
    );
    return NearbyOrders.fromJson(json);
  }

  /// `GET /orders/{id}` — participants (or an admin) only.
  Future<Order> detail(String orderId) async {
    final Map<String, dynamic> json = await _api.get('/api/v1/orders/$orderId');
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/grab`. First-writer-wins via a Redis lock plus a
  /// conditional UPDATE, so a 409 means another driver won — not a retryable
  /// error.
  ///
  /// **P4 — two gates, checked before the grab itself, in this order:**
  ///
  /// 1. **429** `RATE_LIMITED`, `reason: COOLDOWN` — a default in the last 15
  ///    minutes. Wait; the `Retry-After` header says how long.
  /// 2. **423** `reason: DEPOSIT_INSUFFICIENT` — `balance + held < 0`. This one
  ///    needs the driver to *act* (top up), which is why it is not a 429. The
  ///    server deliberately does not touch `DriverStatus` for it: an in-flight
  ///    trip is unaffected and the driver is not locked out of the app.
  ///
  /// The order is only ever locked *after* both gates pass — otherwise a driver
  /// could win the order and then be unable to start it, stranding the trip for
  /// everyone.
  Future<Order> grab(String orderId) async {
    final Map<String, dynamic> json = await _api.post('/api/v1/orders/$orderId/grab');
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/arrival-claim` — arrival, step 1. Assigned driver only.
  ///
  /// The server checks the driver's **own recorded GPS** against the pickup
  /// point, not the coordinates in this body: [lat]/[lng] are optional, are
  /// validated to be in Hong Kong, and are rejected outright if they disagree
  /// with the last WebSocket tick by more than `arrival_gps_max_disagreement_m`
  /// — a body coordinate that contradicts the verified position is the shape of
  /// a spoofing attempt.
  ///
  /// Success reaches **`PENDING_ARRIVAL_CONFIRM`, not `DRIVER_ARRIVED`.** The
  /// passenger's confirmation (see [arrivalConfirm]) is what finishes it, and
  /// the cancel right stays open until then — a driver 500 m away must not be
  /// able to take it away. Failures are 422 with `reason` of `NO_LOCATION`
  /// (no GPS tick on record), `TOO_FAR` (`distance_m`), or `GPS_MISMATCH`
  /// (`disagreement_m`).
  Future<Order> arrivalClaim(String orderId, {double? lat, double? lng}) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/orders/$orderId/arrival-claim',
      data: <String, dynamic>{'driver_lat': lat, 'driver_lng': lng},
    );
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/arrival-claim` with no coordinates — the pre-P4
  /// `/arrive` route is a deprecated server-side alias for exactly this.
  ///
  /// It can **only** reach `PENDING_ARRIVAL_CONFIRM`; no alias can skip the
  /// passenger's confirmation. Prefer [arrivalClaim] so the driver's own fix is
  /// sent along and a GPS mismatch is caught early.
  @Deprecated('Use arrivalClaim() — this is the pre-P4 route')
  Future<Order> arrive(String orderId) async {
    final Map<String, dynamic> json = await _api.post('/api/v1/orders/$orderId/arrive');
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/arrival-confirm` — arrival, step 2. Passenger only.
  ///
  /// [phoneLast4] is the last four digits of **the passenger's own** number
  /// (option A in `docs/IN_TRIP_REDESIGN.md` §5.1.3): only the passenger knows
  /// it, so the driver cannot complete this step alone — which is the entire
  /// point of the second factor.
  ///
  /// Success moves the order to `DRIVER_ARRIVED` and **locks the cancel right**.
  /// A wrong code answers **401** `reason: PIN_MISMATCH` with
  /// `attempts_remaining`; on the third the server returns the order to
  /// `ACCEPTED` and opens a dispute, so the returned order may no longer be
  /// `PENDING_ARRIVAL_CONFIRM` even though the call "succeeded".
  Future<Order> arrivalConfirm(String orderId, String phoneLast4) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/orders/$orderId/arrival-confirm',
      data: <String, dynamic>{'phone_last4': phoneLast4},
    );
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/start` — assigned driver only.
  ///
  /// **P4:** this is where the flat platform trip fee is charged to the
  /// driver's deposit. The server makes it idempotent with a unique ledger
  /// reference, so a retried `/start` cannot charge twice.
  Future<Order> start(String orderId) async {
    final Map<String, dynamic> json = await _api.post('/api/v1/orders/$orderId/start');
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/complete` — assigned driver only.
  ///
  /// Valid from `IN_TRIP` **and** `DESTINATION_CHANGED` — a trip whose dropoff
  /// moved is still a trip that ends. The platform fee was already taken at
  /// `/start`, so nothing is charged here.
  Future<Order> complete(String orderId) async {
    final Map<String, dynamic> json = await _api.post('/api/v1/orders/$orderId/complete');
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/change-destination` — passenger or assigned driver.
  ///
  /// Valid only while the trip is under way (`IN_TRIP` / `DESTINATION_CHANGED`);
  /// anything earlier is 409 `WRONG_STATUS`. The server preserves the original
  /// dropoff exactly once, re-prices from [distanceKm] when given (otherwise
  /// from the PostGIS straight line — see
  /// [FareSnapshot.distanceIsStraightLine]), and counts the change, answering
  /// 429 `TOO_MANY_CHANGES` past the cap.
  ///
  /// The returned order carries the **new estimate**; the fare itself is still
  /// agreed between passenger and driver, so the caller must present it as a
  /// new estimate rather than a new price.
  Future<Order> changeDestination(
    String orderId, {
    required double dropoffLat,
    required double dropoffLng,
    required String dropoffAddress,
    double? distanceKm,
  }) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/orders/$orderId/change-destination',
      data: <String, dynamic>{
        'dropoff_lat': dropoffLat,
        'dropoff_lng': dropoffLng,
        'dropoff_address': dropoffAddress,
        'distance_km': ?distanceKm?.toString(),
      },
    );
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/interrupt` — end the trip early. Either party.
  ///
  /// **Instant, and not a request for permission.** The trip stops on this call
  /// and the response is already `INTERRUPTED`; there is no pending state. A
  /// dispute is opened in the same transaction, and the *other* party becomes
  /// its default subject — a default, not a verdict.
  ///
  /// Valid from `DRIVER_ARRIVED` onward, which is the point: arrival locks
  /// cancelling, so interrupting is the only way out of a car the passenger no
  /// longer wants to be in (§5.2). [note] is mandatory when
  /// [reasonCode] is `OTHER` (422 `NOTE_REQUIRED` otherwise).
  ///
  /// **Nothing is refunded here.** The platform fee stays charged, so that
  /// neither side can use "interrupt" to dodge it; who owes what is settled by
  /// the dispute afterwards.
  ///
  /// [reasonCode] must come from this role's menu
  /// ([InterruptionReason.forPassenger] / [forDriver]) — the server enforces
  /// that too and answers 422 `REASON_NOT_FOR_PARTY` for a reason that names
  /// the filer (§6.1: a hidden menu option is a courtesy, not a control). Both
  /// callers here already pick from the right list, so a 422 means a stale
  /// build rather than a user error.
  Future<Order> interrupt(
    String orderId, {
    required InterruptionReason reasonCode,
    String note = '',
  }) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/orders/$orderId/interrupt',
      data: <String, dynamic>{'reason_code': reasonCode.wire, 'note': note},
    );
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/cancel` — either party, before arrival only.
  ///
  /// **P4 changed the shape of this call twice over:**
  ///
  /// * From `ACCEPTED` / `PENDING_ARRIVAL_CONFIRM` a cancellation is a
  ///   **default**: it costs money (passenger 100%, driver 50% of
  ///   `estimated_total_hkd`) and arms a 15-minute cool-down. Those states
  ///   require [reasonCode]; the server answers 422 `REASON_REQUIRED` without
  ///   it. Earlier states stay free and take free text only.
  /// * From `DRIVER_ARRIVED` onward it is **refused** with 409 `CANCEL_LOCKED` —
  ///   once arrival is proven, the only exit is [interrupt].
  ///
  /// Surface the penalty before the user commits: it is real money, taken
  /// immediately, and the appeal is a dispute afterwards rather than a
  /// client-side undo.
  Future<Order> cancel(String orderId, {String reason = '', InterruptionReason? reasonCode}) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/orders/$orderId/cancel',
      data: <String, dynamic>{'reason': reason, 'reason_code': ?reasonCode?.wire},
    );
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/receipt` — issue the receipt, idempotently.
  ///
  /// Server-side this freezes the document on first call and returns the stored
  /// copy afterwards, so calling it twice cannot change what the receipt says.
  /// The server decides who may read it (the two parties or an admin) — a 403 is
  /// the answer, not a client-side check.
  Future<Receipt> requestReceipt(String orderId) async {
    final Map<String, dynamic> json = await _api.post('/api/v1/orders/$orderId/receipt');
    return Receipt.fromJson(json);
  }

  /// `GET /orders/{id}/receipt` — read the receipt (freezing it on first read).
  Future<Receipt> receipt(String orderId) async {
    final Map<String, dynamic> json = await _api.get('/api/v1/orders/$orderId/receipt');
    return Receipt.fromJson(json);
  }
}
