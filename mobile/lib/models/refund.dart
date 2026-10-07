import '../core/format/money.dart';
import '../core/network/wire.dart';
import 'enums.dart';

/// A deposit-withdrawal request.
///
/// Two serialisers exist server-side and they differ: the driver's own view
/// (`_refund_out` in `app/api/drivers.py`) omits `driver_profile_id` and
/// `decided_by`, while the admin queue (`app/api/admin.py`) includes them. One
/// model covers both; the extra fields are nullable.
///
/// Requesting a refund **holds** the balance and suspends the driver — no money
/// moves until an admin approves. At most one open request per driver exists.
class RefundRequest {
  const RefundRequest({
    required this.id,
    required this.amountHkd,
    required this.isPartial,
    required this.status,
    required this.note,
    required this.decisionNote,
    required this.decidedAt,
    required this.createdAt,
    this.driverProfileId,
    this.decidedBy,
  });

  factory RefundRequest.fromJson(Map<String, dynamic> json) => RefundRequest(
    id: asString(json['id'], 'refund.id'),
    amountHkd: Money.parse(json['amount_hkd']),
    isPartial: asBoolOr(json['is_partial'], 'refund.is_partial', false),
    status: RefundStatus.fromWire(asString(json['status'], 'refund.status')),
    note: asStringOrNull(json['note'], 'refund.note'),
    decisionNote: asStringOrNull(json['decision_note'], 'refund.decision_note'),
    decidedAt: asDateOrNull(json['decided_at'], 'refund.decided_at'),
    createdAt: asDateOrNull(json['created_at'], 'refund.created_at'),
    driverProfileId: asStringOrNull(json['driver_profile_id'], 'refund.driver_profile_id'),
    decidedBy: asStringOrNull(json['decided_by'], 'refund.decided_by'),
  );

  final String id;
  final Money amountHkd;

  /// True when the driver claimed less than the whole balance (P2-2).
  final bool isPartial;
  final RefundStatus status;
  final String? note;
  final String? decisionNote;
  final DateTime? decidedAt;
  final DateTime? createdAt;
  final String? driverProfileId;
  final String? decidedBy;
}

/// `GET /api/v1/drivers/me/refund` wraps the row in a `refund` key and returns
/// `{"refund": null}` when the driver has never filed one.
class MyRefund {
  const MyRefund(this.refund);

  factory MyRefund.fromJson(Map<String, dynamic> json) => MyRefund(
    json['refund'] == null ? null : RefundRequest.fromJson(asMap(json['refund'], 'refund')),
  );

  final RefundRequest? refund;
}
