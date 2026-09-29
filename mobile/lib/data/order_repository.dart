import '../core/network/api_client.dart';
import '../models/order.dart';

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
  Future<NearbyOrders> nearby({
    required double lat,
    required double lng,
    double radiusKm = 3,
  }) async {
    final Map<String, dynamic> json = await _api.get(
      '/api/v1/orders/nearby',
      query: <String, dynamic>{'lat': lat, 'lng': lng, 'radius_km': radiusKm},
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
  Future<Order> grab(String orderId) async {
    final Map<String, dynamic> json = await _api.post('/api/v1/orders/$orderId/grab');
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/arrive` — assigned driver only.
  Future<Order> arrive(String orderId) async {
    final Map<String, dynamic> json = await _api.post('/api/v1/orders/$orderId/arrive');
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/start` — assigned driver only.
  Future<Order> start(String orderId) async {
    final Map<String, dynamic> json = await _api.post('/api/v1/orders/$orderId/start');
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/complete` — assigned driver only.
  Future<Order> complete(String orderId) async {
    final Map<String, dynamic> json = await _api.post('/api/v1/orders/$orderId/complete');
    return Order.fromJson(json);
  }

  /// `POST /orders/{id}/cancel` — either party.
  ///
  /// Cancelling as the **assigned driver** while `ACCEPTED` or `DRIVER_ARRIVED`
  /// writes a `PENALTY_DEDUCTION` of `no_show_penalty_hkd` to the driver's
  /// ledger. Surface that before the user commits: it is real money.
  Future<Order> cancel(String orderId, {String reason = ''}) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/orders/$orderId/cancel',
      data: <String, dynamic>{'reason': reason},
    );
    return Order.fromJson(json);
  }
}
