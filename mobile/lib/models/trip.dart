import '../core/network/wire.dart';
import 'enums.dart';

/// `GET /api/v1/trips/{order_id}/location` — the reconnection fallback for the
/// live map. `lat` / `lng` are null until the assigned driver has reported a
/// position, which is the normal state between `ACCEPTED` and the first tick.
///
/// `driver_profile_id` is the driver's **profile** id, never their account id
/// (`SEC-27`).
class TripLocationSnapshot {
  const TripLocationSnapshot({
    required this.orderId,
    required this.status,
    required this.driverProfileId,
    required this.lat,
    required this.lng,
  });

  factory TripLocationSnapshot.fromJson(Map<String, dynamic> json) => TripLocationSnapshot(
    orderId: asString(json['order_id'], 'trip.order_id'),
    status: OrderStatus.fromWire(asString(json['status'], 'trip.status')),
    driverProfileId: asStringOrNull(json['driver_profile_id'], 'trip.driver_profile_id'),
    lat: asDoubleOrNull(json['lat'], 'trip.lat'),
    lng: asDoubleOrNull(json['lng'], 'trip.lng'),
  );

  final String orderId;
  final OrderStatus status;
  final String? driverProfileId;
  final double? lat;
  final double? lng;

  bool get hasFix => lat != null && lng != null;
}

/// A message on the live-trip channel `/ws/trip/{order_id}`.
///
/// Server → client types are `location`, `ping`, `ack`, `error` and `order`
/// (`app/api/ws.py`, `app/services/order/trip_event_service.py`). The driver's
/// own socket never receives `location` — it gets a direct `ack` per tick
/// instead, so it sees exactly one reply per push.
sealed class TripEvent {
  const TripEvent();

  /// Unknown types decode to [TripUnknownEvent] rather than throwing, so a
  /// server that adds a message type cannot break an older client's socket.
  static TripEvent fromJson(Map<String, dynamic> json) {
    final String type = asString(json['type'], 'ws.type');
    return switch (type) {
      'location' => TripLocationEvent(
        lat: asDouble(json['lat'], 'ws.lat'),
        lng: asDouble(json['lng'], 'ws.lng'),
        at: asDateOrNull(json['ts'], 'ws.ts'),
      ),
      'ping' => TripPingEvent(at: asDateOrNull(json['ts'], 'ws.ts')),
      'ack' => TripAckEvent(
        lat: asDouble(json['lat'], 'ws.lat'),
        lng: asDouble(json['lng'], 'ws.lng'),
      ),
      'error' => TripErrorEvent(code: asString(json['code'], 'ws.code')),
      'order' => TripOrderEvent(
        event: asString(json['event'], 'ws.order.event'),
        orderId: asString(json['order_id'], 'ws.order.order_id'),
        status: asString(json['status'], 'ws.order.status'),
      ),
      _ => TripUnknownEvent(type: type),
    };
  }
}

/// A position tick from the assigned driver. Emitted on the passenger's socket
/// (and any monitor's), not on the driver's own.
final class TripLocationEvent extends TripEvent {
  const TripLocationEvent({required this.lat, required this.lng, required this.at});

  final double lat;
  final double lng;
  final DateTime? at;
}

/// `SEC-30`: the server pings on its own timer (default every 30s) so an idle
/// connection is exercised in both directions. Receiving one proves the socket
/// is alive; the client does not need to answer.
final class TripPingEvent extends TripEvent {
  const TripPingEvent({required this.at});

  final DateTime? at;
}

/// Confirmation that the driver's tick was persisted and fanned out.
final class TripAckEvent extends TripEvent {
  const TripAckEvent({required this.lat, required this.lng});

  final double lat;
  final double lng;
}

/// A rejected push. Codes are `READ_ONLY`, `RATE_LIMITED`, `BAD_MESSAGE`,
/// `OUTSIDE_HK`, `DRIVER_NOT_ACTIVE`.
final class TripErrorEvent extends TripEvent {
  const TripErrorEvent({required this.code});

  final String code;

  bool get isFatal => code == 'DRIVER_NOT_ACTIVE' || code == 'OUTSIDE_HK';

  String get messageZh => switch (code) {
    'READ_ONLY' => '乘客連線不可推送位置',
    'RATE_LIMITED' => '位置更新過於頻繁',
    'BAD_MESSAGE' => '位置訊息格式錯誤',
    'OUTSIDE_HK' => '座標不在香港範圍內',
    'BAD_LOCATION' => '座標不在香港範圍內',
    'DRIVER_NOT_ACTIVE' => '司機帳戶未啟用，已停止推送',
    _ => '位置推送失敗（$code）',
  };
}

/// A trip lifecycle announcement (`type: "order"`), emitted by
/// `app/services/order/trip_event_service.publish_lifecycle` the moment a state
/// change is durable.
///
/// Before this message existed the other party learned about a grab, an arrival
/// claim or a cancellation on the next poll
/// ([AppConfig.locationPollInterval], 10 s). The arrival-confirmation flow is
/// the case that made the delay unacceptable: the passenger's "did the driver
/// really arrive?" prompt has to appear *when the driver claims it*.
///
/// [event] is the semantic name — `GRABBED`, `ARRIVAL_CLAIMED`,
/// `ARRIVAL_CONFIRMED`, `ARRIVAL_CONFLICT`, `TRIP_STARTED`, `TRIP_COMPLETED`,
/// `DESTINATION_CHANGED`, `INTERRUPTED`, `CANCELLED`, `DISPUTE_RESOLVED` — and
/// [status] is the order's status *after* the change. Both are open strings on
/// purpose: a server that adds an event must still cause a re-read on an older
/// build, and the screens never apply the payload as state anyway — they
/// re-fetch the order, which is the single source of truth.
final class TripOrderEvent extends TripEvent {
  const TripOrderEvent({required this.event, required this.orderId, required this.status});

  final String event;
  final String orderId;
  final String status;
}

final class TripUnknownEvent extends TripEvent {
  const TripUnknownEvent({required this.type});

  final String type;
}
