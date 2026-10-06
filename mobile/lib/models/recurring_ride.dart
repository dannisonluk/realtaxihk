import '../core/network/api_exception.dart';
import '../core/network/wire.dart';

DateTime _requiredDate(Object? value, String field) =>
    asDateOrNull(value, field) ??
    (throw MalformedResponseException('$field: expected an ISO timestamp'));

/// One weekly ride template from `GET /api/v1/recurring-rides`.
///
/// The server is the store of truth for the template. `next_run_at` is stored
/// as UTC and displayed in local device time when known; the server itself
/// computes the next Asia/Hong_Kong occurrence.
class RecurringRide {
  const RecurringRide({
    required this.id,
    required this.status,
    required this.frequency,
    required this.weekday,
    required this.scheduledTime,
    required this.nextRunAt,
    this.sourceOrderId,
    this.lastOrderId,
    required this.createdAt,
    required this.updatedAt,
  });

  factory RecurringRide.fromJson(Map<String, dynamic> json) => RecurringRide(
    id: asString(json['id'], 'recurring_ride.id'),
    status: asString(json['status'], 'recurring_ride.status'),
    frequency: asString(json['frequency'], 'recurring_ride.frequency'),
    weekday: asInt(json['weekday'], 'recurring_ride.weekday'),
    scheduledTime: asString(json['scheduled_time'], 'recurring_ride.scheduled_time'),
    nextRunAt: _requiredDate(json['next_run_at'], 'recurring_ride.next_run_at'),
    sourceOrderId: asStringOrNull(json['source_order_id'], 'recurring_ride.source_order_id'),
    lastOrderId: asStringOrNull(json['last_order_id'], 'recurring_ride.last_order_id'),
    createdAt: _requiredDate(json['created_at'], 'recurring_ride.created_at'),
    updatedAt: _requiredDate(json['updated_at'], 'recurring_ride.updated_at'),
  );

  final String id;
  final String status;
  final String frequency;
  final int weekday;
  final String scheduledTime;
  final DateTime nextRunAt;
  final String? sourceOrderId;
  final String? lastOrderId;
  final DateTime createdAt;
  final DateTime updatedAt;

  bool get isActive => status == 'ACTIVE';
  bool get isPaused => status == 'PAUSED';
  bool get isCancelled => status == 'CANCELLED';
}

/// `GET /api/v1/recurring-rides` — `{items: [...]}`.
class RecurringRidePage {
  const RecurringRidePage({required this.items});

  factory RecurringRidePage.fromJson(Map<String, dynamic> json) => RecurringRidePage(
    items: asObjectList(json['items'], 'recurring_ride.items', RecurringRide.fromJson),
  );

  final List<RecurringRide> items;
}