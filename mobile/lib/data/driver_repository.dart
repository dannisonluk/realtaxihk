import '../core/network/api_client.dart';
import '../models/driver.dart';
import '../models/driver_attributes.dart';
import '../models/ledger.dart';
import '../models/refund.dart';

/// Driver self-service. Every route runs `require_active_user`, so a disabled
/// account stops reading its ledger immediately rather than for the remainder of
/// its token lifetime (`SEC-12`).
class DriverRepository {
  DriverRepository(this._api);

  final ApiClient _api;

  /// `POST /drivers/register` (201) — enters `PENDING_KYC`.
  /// A second registration for the same account is 409 `CONFLICT`.
  Future<DriverProfile> register(DriverRegisterRequest request) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/drivers/register',
      data: request.toJson(),
    );
    return DriverProfile.fromJson(json);
  }

  /// `GET /drivers/me` — profile plus deposit.
  /// 404 `NOT_FOUND` when the account has no driver profile yet.
  Future<DriverProfile> me() async {
    final Map<String, dynamic> json = await _api.get('/api/v1/drivers/me');
    return DriverProfile.fromJson(json);
  }

  /// `GET /drivers/me/ledger` — ascending by id, keyset on the integer id.
  ///
  /// [afterId] is the `id` of the last row already held; pass
  /// `LedgerPage.nextCursor` from the previous page.
  Future<LedgerPage> ledger({int limit = 100, int? afterId}) async {
    final Map<String, dynamic> json = await _api.get(
      '/api/v1/drivers/me/ledger',
      query: <String, dynamic>{'limit': limit, 'after_id': ?afterId},
    );
    return LedgerPage.fromJson(json);
  }

  /// `POST /drivers/me/refund/request` (201).
  ///
  /// Holds the whole remaining balance and suspends the driver — no money moves
  /// until an admin approves. At most one open request per driver, and the
  /// balance must be at least `refund_min_hkd`.
  Future<RefundRequest> requestRefund({String note = ''}) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/drivers/me/refund/request',
      data: <String, dynamic>{'note': note},
    );
    return RefundRequest.fromJson(json);
  }

  /// `GET /drivers/me/refund` — the most recent request, or null.
  Future<RefundRequest?> myRefund() async {
    final Map<String, dynamic> json = await _api.get('/api/v1/drivers/me/refund');
    return MyRefund.fromJson(json).refund;
  }

  /// `POST /drivers/location` — PostGIS upsert, one row per driver.
  ///
  /// Rate-limited per driver (`SEC-15`), because each call is an UPDATE and was
  /// previously unbounded. The driver app calls this every 3–5s while online;
  /// inside an active trip the WebSocket takes over instead.
  ///
  /// Requires an `ACTIVE` profile; `online: false` goes offline without
  /// reporting a position change.
  Future<void> pushLocation({required double lat, required double lng, bool online = true}) async {
    await _api.post(
      '/api/v1/drivers/location',
      data: <String, dynamic>{'lat': lat, 'lng': lng, 'online': online},
    );
  }

  /// `GET /drivers/me/payment-methods` — the driver's declared methods.
  Future<DriverPaymentMethods> paymentMethods() async {
    final Map<String, dynamic> json = await _api.get('/api/v1/drivers/me/payment-methods');
    return DriverPaymentMethods.fromJson(json);
  }

  /// `PUT /drivers/me/payment-methods` — replace the declared methods.
  Future<DriverPaymentMethods> setPaymentMethods(List<String> methods) async {
    final Map<String, dynamic> json = await _api.put(
      '/api/v1/drivers/me/payment-methods',
      data: DriverPaymentMethods(methods: methods).toJson(),
    );
    return DriverPaymentMethods.fromJson(json);
  }

  /// `GET /drivers/me/environment` — in-car capability flags.
  Future<DriverEnvironment> environment() async {
    final Map<String, dynamic> json = await _api.get('/api/v1/drivers/me/environment');
    return DriverEnvironment.fromJson(json);
  }

  /// `PUT /drivers/me/environment` — replace the declared capability flags.
  Future<DriverEnvironment> setEnvironment(DriverEnvironment environment) async {
    final Map<String, dynamic> json = await _api.put(
      '/api/v1/drivers/me/environment',
      data: environment.toJson(),
    );
    return DriverEnvironment.fromJson(json);
  }
}
