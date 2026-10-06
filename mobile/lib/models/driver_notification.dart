import '../core/network/wire.dart';

/// One row in the driver's in-app notification inbox from
/// `/api/v1/drivers/me/notifications`.
class DriverNotification {
  const DriverNotification({
    required this.id,
    required this.kind,
    required this.headlineZh,
    required this.headlineEn,
    required this.bodyZh,
    required this.bodyEn,
    required this.read,
    required this.createdAt,
    this.orderId,
  });

  factory DriverNotification.fromJson(Map<String, dynamic> json) => DriverNotification(
    id: asInt(json['id'], 'driver_notification.id'),
    orderId: asStringOrNull(json['order_id'], 'driver_notification.order_id'),
    kind: asString(json['kind'], 'driver_notification.kind'),
    headlineZh: asString(json['headline_zh'], 'driver_notification.headline_zh'),
    headlineEn: asString(json['headline_en'], 'driver_notification.headline_en'),
    bodyZh: asString(json['body_zh'], 'driver_notification.body_zh'),
    bodyEn: asString(json['body_en'], 'driver_notification.body_en'),
    read: asBool(json['read'], 'driver_notification.read'),
    createdAt: asDateOrNull(json['created_at'], 'driver_notification.created_at'),
  );

  final int id;
  final String? orderId;
  final String kind;
  final String headlineZh;
  final String headlineEn;
  final String bodyZh;
  final String bodyEn;
  final bool read;
  final DateTime? createdAt;

  bool get isPremium => kind == 'PREMIUM';
  bool get isFixedFare => kind == 'FIXED_FARE';
}

/// `GET /api/v1/drivers/me/notifications` — newest first, keyset-paginated on
/// the integer `id`. `unread_count` is always present so the jobs badge never
/// needs a second call.
class DriverNotificationPage {
  const DriverNotificationPage({required this.items, required this.unreadCount, this.nextCursor});

  factory DriverNotificationPage.fromJson(Map<String, dynamic> json) => DriverNotificationPage(
    items: asObjectList(json['items'], 'items', DriverNotification.fromJson),
    nextCursor: asIntOrNull(json['next_cursor'], 'next_cursor'),
    unreadCount: asInt(json['unread_count'], 'unread_count'),
  );

  final List<DriverNotification> items;
  final int? nextCursor;
  final int unreadCount;
}
