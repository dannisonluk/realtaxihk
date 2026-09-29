import '../core/network/api_client.dart';
import '../models/fare.dart';

class FareRepository {
  FareRepository(this._api);

  final ApiClient _api;

  /// `POST /api/v1/fare/estimate`.
  ///
  /// Public and unauthenticated, so the request screen can quote before login.
  /// Rate-limited per IP (`fare_estimate_ip_rate_limit`), and the server caps
  /// `tunnels` at 8 — [FareEstimateRequest] can only ever build a list that
  /// short, since [Tunnel] has exactly eight members.
  Future<FareEstimate> estimate(FareEstimateRequest request) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/fare/estimate',
      data: request.toJson(),
      authenticated: false,
    );
    return FareEstimate.fromJson(json);
  }
}
