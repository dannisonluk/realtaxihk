import '../core/format/money.dart';
import '../core/network/wire.dart';
import 'enums.dart';
import 'refund.dart';

/// A row of the admin KYC queue (`GET /api/v1/admin/drivers`).
///
/// Deliberately narrower than [DriverProfile]: the admin list does **not**
/// expose `user_id` or `is_online`, and there is no endpoint that maps a
/// profile back to its account. Keep it that way.
class AdminDriverRow {
  const AdminDriverRow({
    required this.id,
    required this.status,
    required this.taxiType,
    required this.taxiDriverPlateNo,
    required this.vehicleRegMark,
  });

  factory AdminDriverRow.fromJson(Map<String, dynamic> json) => AdminDriverRow(
    id: asString(json['id'], 'admin.driver.id'),
    status: DriverStatus.fromWire(asString(json['status'], 'admin.driver.status')),
    taxiType: TaxiType.fromWire(asString(json['taxi_type'], 'admin.driver.taxi_type')),
    taxiDriverPlateNo: asString(json['taxi_driver_plate_no'], 'admin.driver.taxi_driver_plate_no'),
    vehicleRegMark: asString(json['vehicle_reg_mark'], 'admin.driver.vehicle_reg_mark'),
  );

  final String id;
  final DriverStatus status;
  final TaxiType taxiType;
  final String taxiDriverPlateNo;
  final String vehicleRegMark;
}

/// The admin list endpoints all return `{items, total, limit, offset}`.
class Paged<T> {
  const Paged({
    required this.items,
    required this.total,
    required this.limit,
    required this.offset,
  });

  factory Paged.fromJson(Map<String, dynamic> json, T Function(Map<String, dynamic>) decode) =>
      Paged<T>(
        items: asObjectList(json['items'], 'items', decode),
        total: asInt(json['total'], 'total'),
        limit: asInt(json['limit'], 'limit'),
        offset: asInt(json['offset'], 'offset'),
      );

  final List<T> items;
  final int total;
  final int limit;
  final int offset;

  bool get hasMore => offset + items.length < total;
}

/// Result of `POST /api/v1/admin/drivers/{id}/deposit/grant`.
///
/// `reference` is the **namespaced** key the server derived (`grant:<driver>:…`,
/// see `SEC-13`) — it is echoed back so a retry with the same client key is
/// provably idempotent (`P1-7`). Show it in a receipt; do not resend it as the
/// client key.
class DepositGrantResult {
  const DepositGrantResult({
    required this.driverId,
    required this.driverStatus,
    required this.balanceHkd,
    required this.isFulfilled,
    required this.reference,
  });

  factory DepositGrantResult.fromJson(Map<String, dynamic> json) => DepositGrantResult(
    driverId: asString(json['id'], 'grant.id'),
    driverStatus: DriverStatus.fromWire(asString(json['driver_status'], 'grant.driver_status')),
    balanceHkd: Money.parse(json['balance_hkd']),
    isFulfilled: json['is_fulfilled'] as bool? ?? false,
    reference: asString(json['reference'], 'grant.reference'),
  );

  final String driverId;
  final DriverStatus driverStatus;
  final Money balanceHkd;
  final bool isFulfilled;
  final String reference;
}

/// Result of `POST /api/v1/admin/refunds/{id}/decision`.
typedef RefundDecisionResult = RefundRequest;

/// Result of `POST /api/v1/admin/settlement/weekly/run`.
///
/// The run is idempotent per ISO week: a driver already charged for `period` is
/// counted in [skipped], not charged again. `tampered` is the one to watch — it
/// means the ledger reference for that week is held by a *different* entry, so
/// the fee was deliberately not collected (`SEC-13`). Anything above zero
/// deserves a look.
class SettlementRun {
  const SettlementRun({
    required this.period,
    required this.feeHkd,
    required this.eligibleDrivers,
    required this.charged,
    required this.skipped,
    required this.failed,
    required this.tampered,
    required this.fleetManaged,
  });

  factory SettlementRun.fromJson(Map<String, dynamic> json) => SettlementRun(
    period: asString(json['period'], 'settlement.period'),
    feeHkd: Money.parse(json['fee_hkd']),
    eligibleDrivers: asInt(json['eligible_drivers'], 'settlement.eligible_drivers'),
    charged: asInt(json['charged'], 'settlement.charged'),
    skipped: asInt(json['skipped'], 'settlement.skipped'),
    failed: asInt(json['failed'], 'settlement.failed'),
    tampered: asInt(json['tampered'], 'settlement.tampered'),
    fleetManaged: asInt(json['fleet_managed'], 'settlement.fleet_managed'),
  );

  /// ISO week key, e.g. `2026-W38`.
  final String period;
  final Money feeHkd;

  /// ACTIVE drivers the platform run considered — i.e. after fleet members were
  /// excluded. See [fleetManaged].
  final int eligibleDrivers;

  final int charged;
  final int skipped;
  final int failed;
  final int tampered;

  /// ACTIVE drivers **excluded** from this run because they are on a fleet's
  /// active roster, and are therefore billed by that fleet's own settlement at
  /// the discounted rate instead.
  ///
  /// Required rather than defaulted to zero: the server always sends it, and
  /// defaulting would render "no fleet members were excluded" for a response
  /// that simply omitted the field — the exact silence this counter exists to
  /// break. The exclusion is the fix for a double-charge hazard (the two jobs
  /// write different ledger references, so idempotency does not protect a driver
  /// across them), so a number here that disagrees with the fleets' own runs is
  /// the first sign something is wrong.
  final int fleetManaged;

  bool get hasAnomaly => failed > 0 || tampered > 0;
}

/// Result of `POST /api/v1/admin/settlement/preview` — the dry run, and the only
/// source of a `confirm_token`.
///
/// The platform-wide weekly run charges every eligible driver at once and is
/// idempotent per ISO week, so the first accidental press is **not** undoable:
/// the money is gone and the reference is spent. The server therefore requires a
/// token that only this endpoint can mint (bound to the preview's `period` and
/// `fee_hkd`, so a token from one week cannot be spent on another's).
///
/// [wouldGoNegative] is a **sub-count** of [wouldCharge], not a fifth bucket —
/// those drivers are charged and simply go into arrears. Arrears are legal by
/// design, so this is the number an operator most needs before acting: it is the
/// part that is a decision rather than arithmetic.
///
/// The counts and ids are what the wire carries; there are deliberately no
/// driver names, because a preview is a screenful and inlining identities would
/// make the safest page the largest PII export in the app.
class SettlementPreview {
  const SettlementPreview({
    required this.period,
    required this.feeHkd,
    required this.eligibleDrivers,
    required this.fleetManaged,
    required this.wouldCharge,
    required this.alreadyCharged,
    required this.tampered,
    required this.skippedNoDepositAccount,
    required this.wouldGoNegative,
    required this.wouldChargeDriverIds,
    required this.wouldGoNegativeDriverIds,
    required this.shortfallTotalHkd,
    required this.totalChargeHkd,
    required this.confirmToken,
    required this.confirmExpiresInSeconds,
  });

  factory SettlementPreview.fromJson(Map<String, dynamic> json) => SettlementPreview(
    period: asString(json['period'], 'preview.period'),
    feeHkd: Money.parse(json['fee_hkd']),
    eligibleDrivers: asInt(json['eligible_drivers'], 'preview.eligible_drivers'),
    fleetManaged: asInt(json['fleet_managed'], 'preview.fleet_managed'),
    wouldCharge: asInt(json['would_charge'], 'preview.would_charge'),
    alreadyCharged: asInt(json['already_charged'], 'preview.already_charged'),
    tampered: asInt(json['tampered'], 'preview.tampered'),
    skippedNoDepositAccount: asInt(
      json['skipped_no_deposit_account'],
      'preview.skipped_no_deposit_account',
    ),
    wouldGoNegative: asInt(json['would_go_negative'], 'preview.would_go_negative'),
    wouldChargeDriverIds: asStringListOrEmpty(
      json['would_charge_driver_ids'],
      'preview.would_charge_driver_ids',
    ),
    wouldGoNegativeDriverIds: asStringListOrEmpty(
      json['would_go_negative_driver_ids'],
      'preview.would_go_negative_driver_ids',
    ),
    shortfallTotalHkd: Money.parse(json['shortfall_total_hkd']),
    totalChargeHkd: Money.parse(json['total_charge_hkd']),
    confirmToken: asString(json['confirm_token'], 'preview.confirm_token'),
    confirmExpiresInSeconds: asInt(
      json['confirm_expires_in_seconds'],
      'preview.confirm_expires_in_seconds',
    ),
  );

  final String period;
  final Money feeHkd;
  final int eligibleDrivers;
  final int fleetManaged;
  final int wouldCharge;
  final int alreadyCharged;
  final int tampered;
  final int skippedNoDepositAccount;
  final int wouldGoNegative;

  /// The driver ids the server would actually charge, in preview order.
  final List<String> wouldChargeDriverIds;

  /// The subset of [wouldChargeDriverIds] whose balances would go negative.
  final List<String> wouldGoNegativeDriverIds;

  final Money shortfallTotalHkd;
  final Money totalChargeHkd;

  /// The signed token the run requires. **Do not render it** — it is a bearer
  /// credential for a money-moving call. Pass it straight to
  /// [AdminRepository.runWeeklySettlement].
  final String confirmToken;

  /// How long [confirmToken] stays valid, so the UI can warn before the operator
  /// is surprised by an expiry at the moment they were ready to act.
  final int confirmExpiresInSeconds;

  /// Nothing will happen if this is true — the run is a no-op by construction,
  /// and the server does not require a token for it.
  bool get isNoOp => wouldCharge == 0;

  bool get hasAnomaly => tampered > 0;
}
