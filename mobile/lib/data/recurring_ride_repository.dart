import '../core/network/api_client.dart';
import '../core/network/wire.dart';
import '../models/order.dart';
import '../models/recurring_ride.dart';

/// Recurring ride API: create a weekly template from an order, list the
/// passenger's own templates, and pause/resume/cancel.
class RecurringRideRepository {
  RecurringRideRepository(this._api);

  final ApiClient _api;

  /// `POST /recurring-rides` — creates a template, **never an order**.
  ///
  /// The server stores [orderPayload] and that passenger's [sourceOrderId], and
  /// the scheduler mints an order each week when `next_run_at` arrives. The
  /// returned ride does not carry the order back because nothing was placed.
  Future<RecurringRide> create({
    required int weekday,
    required String scheduledTime,
    required OrderCreateRequest orderPayload,
    String? sourceOrderId,
  }) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/recurring-rides',
      data: <String, dynamic>{
        'weekday': weekday,
        'scheduled_time': scheduledTime,
        'source_order_id': sourceOrderId,
        'order': orderPayload.toJson(),
      },
    );
    return RecurringRide.fromJson(json);
  }

  /// `GET /recurring-rides` — the caller's own list, newest first.
  Future<List<RecurringRide>> list() async {
    final Map<String, dynamic> json = await _api.get('/api/v1/recurring-rides');
    return asObjectList(json['items'], 'recurring_rides.items', RecurringRide.fromJson);
  }

  /// `PATCH /recurring-rides/{id}` — status lifecycle only.
  Future<RecurringRide> setStatus(String rideId, String status) async {
    final Map<String, dynamic> json = await _api.patch(
      '/api/v1/recurring-rides/$rideId',
      data: <String, dynamic>{'status': status},
    );
    return RecurringRide.fromJson(json);
  }
}
