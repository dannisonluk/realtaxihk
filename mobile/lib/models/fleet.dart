import '../core/format/money.dart';
import '../core/network/wire.dart';
import 'enums.dart';

/// A taxi fleet (的士車隊) — a licensed operator.
///
/// `weekly_fee_discount_percent` is a `Decimal` server-side, so it arrives as a
/// **string** (`"12.5"`). It is kept as a string rather than a double because it
/// is a rate, not money: the server applies it and sends the resulting fee, so
/// the client never needs to re-derive a charge from it.
class Fleet {
  const Fleet({
    required this.id,
    required this.name,
    required this.licenseNo,
    required this.status,
    required this.weeklyFeeDiscountPercent,
    this.contactName,
    this.contactPhone,
    this.note,
    this.createdAt,
    this.memberCount,
  });

  factory Fleet.fromJson(Map<String, dynamic> json) => Fleet(
    id: asString(json['id'], 'fleet.id'),
    name: asString(json['name'], 'fleet.name'),
    licenseNo: asString(json['license_no'], 'fleet.license_no'),
    status: FleetStatus.fromWire(asString(json['status'], 'fleet.status')),
    weeklyFeeDiscountPercent: asString(
      json['weekly_fee_discount_percent'],
      'fleet.weekly_fee_discount_percent',
    ),
    contactName: asStringOrNull(json['contact_name'], 'fleet.contact_name'),
    contactPhone: asStringOrNull(json['contact_phone'], 'fleet.contact_phone'),
    note: asStringOrNull(json['note'], 'fleet.note'),
    createdAt: asDateOrNull(json['created_at'], 'fleet.created_at'),
    // Present on the list and detail routes, absent on the 201 from create…
    // which also sends it, but the driver-facing `/fleets/me` omits it only when
    // there is no fleet at all. Nullable is the honest type.
    memberCount: asIntOrNull(json['member_count'], 'fleet.member_count'),
  );

  final String id;
  final String name;

  /// The Transport Department licence number. Displayed, never edited.
  final String licenseNo;

  final FleetStatus status;

  /// A percent, as the server's decimal string: `"0"`, `"12.5"`, `"100"`.
  final String weeklyFeeDiscountPercent;

  final String? contactName;
  final String? contactPhone;
  final String? note;
  final DateTime? createdAt;
  final int? memberCount;

  /// `true` when the fleet pays nothing each week. A legitimate configuration —
  /// the platform still records the settlement run.
  bool get isFullyDiscounted => (double.tryParse(weeklyFeeDiscountPercent) ?? 0) >= 100;

  /// `12.5%` / `0%` — no trailing `.0`, because `"12.0"` reads as a precision the
  /// operator never typed.
  String get discountLabel {
    final double value = double.tryParse(weeklyFeeDiscountPercent) ?? 0;
    return value == value.roundToDouble()
        ? '${value.toStringAsFixed(0)}%'
        : '${value.toStringAsFixed(1)}%';
  }
}

/// The caller's own membership, as returned beside `GET /fleets/me`.
class FleetMembershipInfo {
  const FleetMembershipInfo({required this.memberRole, this.joinedAt});

  factory FleetMembershipInfo.fromJson(Map<String, dynamic> json) => FleetMembershipInfo(
    memberRole: FleetMemberRole.fromWire(asString(json['member_role'], 'membership.member_role')),
    joinedAt: asDateOrNull(json['joined_at'], 'membership.joined_at'),
  );

  final FleetMemberRole memberRole;
  final DateTime? joinedAt;
}

/// Body of `GET /api/v1/fleets/me`.
///
/// Both fields are null for a driver who is not on a roster — which is the
/// common case, not an error, so it is a `data` state rather than a 404.
class MyFleet {
  const MyFleet({this.fleet, this.membership});

  factory MyFleet.fromJson(Map<String, dynamic> json) {
    final Object? raw = json['fleet'];
    if (raw == null) {
      return const MyFleet();
    }
    final Object? rawMembership = json['membership'];
    return MyFleet(
      fleet: Fleet.fromJson(asMap(raw, 'my_fleet.fleet')),
      membership: rawMembership == null
          ? null
          : FleetMembershipInfo.fromJson(asMap(rawMembership, 'my_fleet.membership')),
    );
  }

  final Fleet? fleet;
  final FleetMembershipInfo? membership;

  bool get isMember => fleet != null;
}

/// A roster row (`GET /fleets/{id}/members`, `GET /admin/fleets/{id}/members`).
///
/// Narrower than [DriverProfile] on purpose: an operator needs to know *who is
/// on the roster*, not to read back the identity documents the driver supplied
/// to the platform. There is no HK ID fragment, licence number or vehicle
/// registration here, and adding one would undo a deliberate server-side
/// omission in `app/api/fleets.py::_member_out`.
class FleetMember {
  const FleetMember({
    required this.driverProfileId,
    required this.taxiType,
    required this.driverStatus,
    required this.memberRole,
    required this.status,
    this.joinedAt,
    this.leftAt,
  });

  factory FleetMember.fromJson(Map<String, dynamic> json) => FleetMember(
    driverProfileId: asString(json['driver_profile_id'], 'member.driver_profile_id'),
    taxiType: TaxiType.fromWire(asString(json['taxi_type'], 'member.taxi_type')),
    driverStatus: DriverStatus.fromWire(asString(json['driver_status'], 'member.driver_status')),
    memberRole: FleetMemberRole.fromWire(asString(json['member_role'], 'member.member_role')),
    status: FleetMemberStatus.fromWire(asString(json['status'], 'member.status')),
    joinedAt: asDateOrNull(json['joined_at'], 'member.joined_at'),
    leftAt: asDateOrNull(json['left_at'], 'member.left_at'),
  );

  /// The driver **profile** id, which is what the ledger is keyed on.
  final String driverProfileId;

  final TaxiType taxiType;

  /// The driver's own status, which is independent of the roster: a member can
  /// be on the roster and still be `PENDING_KYC`, in which case they are not
  /// billable (there is no deposit account to debit).
  final DriverStatus driverStatus;

  final FleetMemberRole memberRole;
  final FleetMemberStatus status;
  final DateTime? joinedAt;
  final DateTime? leftAt;

  /// Whether the fleet's weekly run will actually debit this member.
  bool get isBillable => status == FleetMemberStatus.active && driverStatus == DriverStatus.active;

  /// A short handle for the roster list. The profile id is a UUID and there is
  /// no name on the wire, so the tail is the only thing that distinguishes two
  /// rows at a glance — good enough to point at a row in a support call.
  String get shortId => driverProfileId.length <= 8
      ? driverProfileId
      : '…${driverProfileId.substring(driverProfileId.length - 8)}';
}

/// One fleet settlement run.
///
/// Two shapes decode into this, which is why two fields are nullable:
///
/// * `POST /admin/fleets/{id}/settlement/run` returns the live totals — it has
///   `gross_fee_hkd` (before the discount) and no `created_at`, because the row
///   it just upserted is not read back.
/// * `GET /…/settlement` returns stored rows — `created_at` is present,
///   `gross_fee_hkd` is not (it is not stored).
///
/// [feeHkd] is the per-member fee **after** the discount, and is what was
/// actually debited. [grossFeeHkd] is what the platform's flat rate would have
/// been, and is only available on the live run.
class FleetSettlementRun {
  const FleetSettlementRun({
    required this.period,
    required this.feeHkd,
    required this.discountPercent,
    required this.memberCount,
    required this.charged,
    required this.skipped,
    required this.failed,
    required this.tampered,
    required this.collectedHkd,
    this.grossFeeHkd,
    this.createdAt,
  });

  factory FleetSettlementRun.fromJson(Map<String, dynamic> json) => FleetSettlementRun(
    period: asString(json['period'], 'fleet_settlement.period'),
    feeHkd: Money.parse(json['fee_hkd']),
    discountPercent: asString(json['discount_percent'], 'fleet_settlement.discount_percent'),
    memberCount: asInt(json['member_count'], 'fleet_settlement.member_count'),
    charged: asInt(json['charged'], 'fleet_settlement.charged'),
    skipped: asInt(json['skipped'], 'fleet_settlement.skipped'),
    failed: asInt(json['failed'], 'fleet_settlement.failed'),
    tampered: asInt(json['tampered'], 'fleet_settlement.tampered'),
    collectedHkd: Money.parse(json['collected_hkd']),
    grossFeeHkd: json['gross_fee_hkd'] == null ? null : Money.parse(json['gross_fee_hkd']),
    createdAt: asDateOrNull(json['created_at'], 'fleet_settlement.created_at'),
  );

  /// ISO week key, e.g. `2026-W38`.
  final String period;

  /// Per-member fee after the fleet's discount — what was actually charged.
  final Money feeHkd;

  /// What the platform's flat fee would have been. Null on a stored row.
  final Money? grossFeeHkd;

  final String discountPercent;

  /// Billable members considered (ACTIVE roster row *and* ACTIVE driver).
  final int memberCount;

  final int charged;
  final int skipped;
  final int failed;

  /// The one to watch: the ledger reference for this week is held by an entry
  /// that is **not** this week's fleet fee, so the fee was deliberately not
  /// collected (`SEC-13`). Anything above zero needs a human.
  final int tampered;

  final Money collectedHkd;
  final DateTime? createdAt;

  bool get hasAnomaly => failed > 0 || tampered > 0;

  /// Total saved against the platform's flat rate. Null when the gross figure
  /// was not sent, so the UI can hide the line rather than print `HK$0`.
  Money? get discountSaving {
    final Money? gross = grossFeeHkd;
    if (gross == null) {
      return null;
    }
    return Money.parse((gross.asDouble - feeHkd.asDouble).toString());
  }
}

/// `{items}` wrapper for the roster and settlement-history routes. Neither pages,
/// so unlike [Paged] there is no `total`/`limit`/`offset`.
class FleetList<T> {
  const FleetList(this.items);

  factory FleetList.fromJson(Map<String, dynamic> json, T Function(Map<String, dynamic>) decode) =>
      FleetList<T>(asObjectList(json['items'], 'items', decode));

  final List<T> items;
}
