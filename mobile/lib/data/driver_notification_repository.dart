import '../core/network/api_client.dart';
import '../core/network/wire.dart';
import '../models/driver_notification.dart';

/// Driver in-app notification inbox.
///
/// The backend keeps this in Postgres, not Redis, so rows survive offline
/// periods; this repository only ever talks to the REST endpoints.
class DriverNotificationRepository {
  DriverNotificationRepository(this._api);

  final ApiClient _api;

  /// `GET /drivers/me/notifications` — one page plus the unread count.
  Future<DriverNotificationPage> list({int limit = 50, int? afterId}) async {
    final Map<String, dynamic> json = await _api.get(
      '/api/v1/drivers/me/notifications',
      query: <String, dynamic>{'limit': limit, 'after_id': ?afterId},
    );
    return DriverNotificationPage.fromJson(json);
  }

  /// `POST /drivers/me/notifications/read-all` — mark the whole inbox read.
  Future<int> readAll() async {
    final Map<String, dynamic> json = await _api.post('/api/v1/drivers/me/notifications/read-all');
    return asIntOrNull(json['updated'], 'updated') ?? 0;
  }

  /// `POST /drivers/me/notifications/{id}/read` — mark one row read.
  Future<void> read(int notificationId) async {
    await _api.post('/api/v1/drivers/me/notifications/$notificationId/read');
  }
}
