/// Wire enums. Every one of these is a `str, Enum` on the server
/// (`app/models/__init__.py`, `app/services/fare_calculator.py`), so the JSON
/// value is the upper/lower-case token and the Dart name is only a local alias.
///
/// [fromWire] throws rather than falling back to a default: an unrecognised
/// value means the server shipped a new state, and silently mapping it to, say,
/// `CREATED` would show a driver the wrong screen. Better to fail loudly.
library;

enum UserRole {
  passenger('PASSENGER'),
  driver('DRIVER'),
  admin('ADMIN');

  const UserRole(this.wire);

  final String wire;

  static UserRole fromWire(String value) =>
      _decode(values, (UserRole v) => v.wire, value, 'UserRole');
}

enum DriverStatus {
  pendingKyc('PENDING_KYC'),
  depositRequired('DEPOSIT_REQUIRED'),
  active('ACTIVE'),
  suspended('SUSPENDED'),
  terminated('TERMINATED');

  const DriverStatus(this.wire);

  final String wire;

  static DriverStatus fromWire(String value) =>
      _decode(values, (DriverStatus v) => v.wire, value, 'DriverStatus');

  /// Whether the driver may stream location and grab orders.
  bool get canDrive => this == DriverStatus.active;

  /// Whether the driver still owes the security deposit.
  bool get needsDeposit => this == DriverStatus.depositRequired;

  String get labelZh => switch (this) {
    DriverStatus.pendingKyc => '審核中',
    DriverStatus.depositRequired => '待繳按金',
    DriverStatus.active => '已啟用',
    DriverStatus.suspended => '已停權',
    DriverStatus.terminated => '已終止',
  };
}

enum OrderStatus {
  created('CREATED'),
  broadcasting('BROADCASTING'),
  accepted('ACCEPTED'),
  driverArrived('DRIVER_ARRIVED'),
  inTrip('IN_TRIP'),
  completed('COMPLETED'),
  cancelled('CANCELLED');

  const OrderStatus(this.wire);

  final String wire;

  static OrderStatus fromWire(String value) =>
      _decode(values, (OrderStatus v) => v.wire, value, 'OrderStatus');

  bool get isTerminal => this == OrderStatus.completed || this == OrderStatus.cancelled;

  /// The order has a driver attached, so a live-trip channel is meaningful.
  bool get hasDriver =>
      this == OrderStatus.accepted ||
      this == OrderStatus.driverArrived ||
      this == OrderStatus.inTrip;

  String get labelZh => switch (this) {
    OrderStatus.created => '已建立',
    OrderStatus.broadcasting => '等候司機',
    OrderStatus.accepted => '司機已接單',
    OrderStatus.driverArrived => '司機已到達',
    OrderStatus.inTrip => '行程中',
    OrderStatus.completed => '已完成',
    OrderStatus.cancelled => '已取消',
  };
}

enum LedgerEntryType {
  depositTopup('DEPOSIT_TOPUP'),
  weeklyFeeDeduction('WEEKLY_FEE_DEDUCTION'),
  penaltyDeduction('PENALTY_DEDUCTION'),
  refund('REFUND'),
  adjustment('ADJUSTMENT');

  const LedgerEntryType(this.wire);

  final String wire;

  static LedgerEntryType fromWire(String value) =>
      _decode(values, (LedgerEntryType v) => v.wire, value, 'LedgerEntryType');

  // There is deliberately no `isCredit` here. Direction is a property of the
  // *amount*, not of the entry type: `ADJUSTMENT` is signed (a correction may
  // credit or debit), so a type-level flag reports the wrong direction for half
  // its rows. The previous `ADJUSTMENT.isCredit == true` was an unverifiable
  // guess that no production code consumed. Read the sign off
  // `LedgerEntry.amountHkd` instead — see `mobile/lib/models/ledger_entry.dart`.

  String get labelZh => switch (this) {
    LedgerEntryType.depositTopup => '按金存入',
    LedgerEntryType.weeklyFeeDeduction => '每週服務費',
    LedgerEntryType.penaltyDeduction => '違規罰款',
    LedgerEntryType.refund => '按金退回',
    LedgerEntryType.adjustment => '調整',
  };
}

enum RefundStatus {
  pending('PENDING'),
  approved('APPROVED'),
  rejected('REJECTED');

  const RefundStatus(this.wire);

  final String wire;

  static RefundStatus fromWire(String value) =>
      _decode(values, (RefundStatus v) => v.wire, value, 'RefundStatus');

  String get labelZh => switch (this) {
    RefundStatus.pending => '待審批',
    RefundStatus.approved => '已批准',
    RefundStatus.rejected => '已拒絕',
  };
}

/// A fleet (的士車隊) is a **licensed operator** — the Transport Department
/// grants the licence, so `SUSPENDED` and `DISSOLVED` are operator states set by
/// an admin, never something a driver can cause.
enum FleetStatus {
  active('ACTIVE'),
  suspended('SUSPENDED'),
  dissolved('DISSOLVED');

  const FleetStatus(this.wire);

  final String wire;

  static FleetStatus fromWire(String value) =>
      _decode(values, (FleetStatus v) => v.wire, value, 'FleetStatus');

  /// Only an ACTIVE fleet is settled; a suspended one is not dispatching, so it
  /// is not billing.
  bool get isBillable => this == FleetStatus.active;

  String get labelZh => switch (this) {
    FleetStatus.active => '營運中',
    FleetStatus.suspended => '已停權',
    FleetStatus.dissolved => '已解散',
  };
}

enum FleetMemberRole {
  owner('OWNER'),
  manager('MANAGER'),
  member('MEMBER');

  const FleetMemberRole(this.wire);

  final String wire;

  static FleetMemberRole fromWire(String value) =>
      _decode(values, (FleetMemberRole v) => v.wire, value, 'FleetMemberRole');

  /// Administrative rights *within the fleet*, not on the platform. A fleet
  /// manager is still an ordinary driver to every other endpoint.
  bool get isAdmin => this == FleetMemberRole.owner || this == FleetMemberRole.manager;

  String get labelZh => switch (this) {
    FleetMemberRole.owner => '車主',
    FleetMemberRole.manager => '管理員',
    FleetMemberRole.member => '成員',
  };
}

/// Roster state. A driver taken off the roster is `REMOVED`, not deleted — the
/// row survives so the week in which they left stays reconstructible.
enum FleetMemberStatus {
  active('ACTIVE'),
  removed('REMOVED');

  const FleetMemberStatus(this.wire);

  final String wire;

  static FleetMemberStatus fromWire(String value) =>
      _decode(values, (FleetMemberStatus v) => v.wire, value, 'FleetMemberStatus');

  String get labelZh => switch (this) {
    FleetMemberStatus.active => '在隊',
    FleetMemberStatus.removed => '已離隊',
  };
}

/// `URBAN` = 市區的士 (red), `NT` = 新界的士 (green), `LANTAU` = 大嶼山的士 (blue).
enum TaxiType {
  urban('URBAN'),
  nt('NT'),
  lantau('LANTAU');

  const TaxiType(this.wire);

  final String wire;

  static TaxiType fromWire(String value) =>
      _decode(values, (TaxiType v) => v.wire, value, 'TaxiType');

  String get labelZh => switch (this) {
    TaxiType.urban => '市區的士',
    TaxiType.nt => '新界的士',
    TaxiType.lantau => '大嶼山的士',
  };
}

/// The eight tolled crossings the fare engine knows. `cross_harbour` is the flat
/// taxi rate covering 紅隧 / 東隧 / 西隧 alike; the rest are individual.
enum Tunnel {
  crossHarbour('cross_harbour', '過海隧道', 'Cross-harbour'),
  taiLam('tai_lam', '大欖隧道', 'Tai Lam Tunnel'),
  tatesCairn('tates_cairn', '大老山隧道', 'Tates Cairn Tunnel'),
  lionRock('lion_rock', '獅子山隧道', 'Lion Rock Tunnel'),
  eaglesNest('eagles_nest', '尖山隧道', "Eagle's Nest Tunnel"),
  shingMun('shing_mun', '城門隧道', 'Shing Mun Tunnels'),
  aberdeen('aberdeen', '香港仔隧道', 'Aberdeen Tunnel'),
  lantauLink('lantau_link', '青嶼幹線', 'Lantau Link');

  const Tunnel(this.wire, this.labelZh, this.labelEn);

  final String wire;
  final String labelZh;
  final String labelEn;

  static Tunnel fromWire(String value) => _decode(values, (Tunnel v) => v.wire, value, 'Tunnel');
}

T _decode<T>(List<T> values, String Function(T value) wireOf, String wire, String name) {
  for (final T value in values) {
    if (wireOf(value) == wire) {
      return value;
    }
  }
  throw ArgumentError.value(wire, name, 'unknown $name value from server');
}
