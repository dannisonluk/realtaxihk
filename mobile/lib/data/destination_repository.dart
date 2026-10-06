import '../core/network/api_client.dart';
import '../models/order.dart';

/// Public premium destinations — the avatar-pinned map places.
///
/// This is the passenger/driver-facing read. Admin writes live under
/// `/api/v1/admin/destinations` and are not used by the app.
class DestinationRepository {
  DestinationRepository(this._api);

  final ApiClient _api;

  /// `GET /api/v1/destinations` — only ACTIVE rows.
  Future<PremiumDestinationPage> list() async {
    final Map<String, dynamic> json = await _api.get('/api/v1/destinations', authenticated: false);
    return PremiumDestinationPage.fromJson(json);
  }
}
