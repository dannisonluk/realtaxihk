import '../core/network/api_client.dart';
import '../models/admin.dart';
import '../models/enums.dart';
import '../models/refund.dart';

/// Admin operations. Every route here is behind `require_admin`, which re-reads
/// the live user row on each call (`P0-3`) — a demoted admin loses access
/// immediately, not when their token expires.
class AdminRepository {
  AdminRepository(this._api);

  final ApiClient _api;

  /// `GET /admin/drivers` — the KYC queue, oldest first.
  Future<Paged<AdminDriverRow>> drivers({
    DriverStatus? status,
    int limit = 50,
    int offset = 0,
  }) async {
    final Map<String, dynamic> json = await _api.get(
      '/api/v1/admin/drivers',
      query: <String, dynamic>{'status_filter': ?status?.wire, 'limit': limit, 'offset': offset},
    );
    return Paged<AdminDriverRow>.fromJson(json, AdminDriverRow.fromJson);
  }

  /// `POST /admin/drivers/{id}/review` — `approve` moves the driver to
  /// `DEPOSIT_REQUIRED`, `reject` and `terminate` to `TERMINATED`, `suspend` to
  /// `SUSPENDED`. Illegal transitions are rejected by the state machine.
  Future<({String driverId, DriverStatus status})> reviewDriver(
    String driverId, {
    required String decision,
    String note = '',
  }) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/admin/drivers/$driverId/review',
      data: <String, dynamic>{'decision': decision, 'note': note},
    );
    return (
      driverId: json['id'] as String,
      status: DriverStatus.fromWire(json['status'] as String),
    );
  }

  /// `POST /admin/drivers/{id}/deposit/grant`.
  ///
  /// Idempotent when [reference] is supplied: a retry with the same key replays
  /// the original entry instead of double-crediting (`P1-7`). The server
  /// namespaces it as `grant:<driver>:<key>`, so it can never collide with a
  /// settlement or refund reference (`SEC-13`).
  ///
  /// When the grant fulfils the deposit, the driver flips
  /// `DEPOSIT_REQUIRED -> ACTIVE` in the same call — that is what actually puts
  /// them on the road.
  Future<DepositGrantResult> grantDeposit(
    String driverId, {
    required double amountHkd,
    String note = '',
    String? reference,
  }) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/admin/drivers/$driverId/deposit/grant',
      data: <String, dynamic>{
        'amount_hkd': amountHkd.toString(),
        'note': note,
        'reference': ?reference,
      },
    );
    return DepositGrantResult.fromJson(json);
  }

  /// `POST /admin/settlement/weekly/run` — the manual lever for the weekly
  /// service fee.
  ///
  /// Idempotent per ISO week, so re-running a period charges nobody twice. Pass
  /// [period] (`YYYY-Www`) to re-run a specific week after a failed batch.
  Future<SettlementRun> runWeeklySettlement({String? period}) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/admin/settlement/weekly/run',
      query: <String, dynamic>{'period': ?period},
    );
    return SettlementRun.fromJson(json);
  }

  /// `GET /admin/refunds` — newest first, so PENDING rows surface.
  Future<Paged<RefundRequest>> refunds({
    RefundStatus? status,
    int limit = 50,
    int offset = 0,
  }) async {
    final Map<String, dynamic> json = await _api.get(
      '/api/v1/admin/refunds',
      query: <String, dynamic>{'status_filter': ?status?.wire, 'limit': limit, 'offset': offset},
    );
    return Paged<RefundRequest>.fromJson(json, RefundRequest.fromJson);
  }

  /// `POST /admin/refunds/{id}/decision`.
  ///
  /// Approving is the **only** path that moves money out: it writes a `REFUND`
  /// ledger entry keyed `refund:{id}`, so a double-tap cannot pay twice, and it
  /// terminates the driver. Rejecting releases the hold and returns the driver
  /// to `ACTIVE`. A second decision on an already-decided request is rejected.
  Future<RefundRequest> decideRefund(
    String refundId, {
    required bool approve,
    String note = '',
  }) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/admin/refunds/$refundId/decision',
      data: <String, dynamic>{'decision': approve ? 'approve' : 'reject', 'note': note},
    );
    return RefundRequest.fromJson(json);
  }
}
