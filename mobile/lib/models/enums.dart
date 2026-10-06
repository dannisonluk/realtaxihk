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

/// Whether an account may be used at all (`app/models/user.py::AccountStatus`).
///
/// Distinct from `is_active`, which is the admin's ban switch: this is the
/// *self-service* lifecycle. An account registers, proves an email and a phone,
/// and only then reads ACTIVE.
///
/// **It is a completeness flag, not a gate.** Nothing refuses on it any more:
/// signing in works while UNVERIFIED, and calling a taxi is gated on the phone
/// alone (`require_phone_verified`). So this decides what the profile screen
/// *says*, never what the API allows — do not turn it into a client-side gate,
/// because the server would disagree and the user would be stuck on a screen the
/// backend was happy to serve.
enum AccountStatus {
  unverified('UNVERIFIED'),
  active('ACTIVE'),
  suspended('SUSPENDED');

  const AccountStatus(this.wire);

  final String wire;

  static AccountStatus fromWire(String value) =>
      _decode(values, (AccountStatus v) => v.wire, value, 'AccountStatus');

  String get labelZh => switch (this) {
    AccountStatus.unverified => '未完成驗證',
    AccountStatus.active => '已啟用',
    AccountStatus.suspended => '已停權',
  };
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

/// The order lifecycle. P4 widened this (`docs/IN_TRIP_REDESIGN.md` §2): the
/// machine used to end at `IN_TRIP -> COMPLETED`, a dead end that could express
/// neither a mid-trip destination change nor an early end.
enum OrderStatus {
  created('CREATED'),
  broadcasting('BROADCASTING'),
  accepted('ACCEPTED'),

  /// The driver pressed "arrived" and the GPS check passed, but the passenger
  /// has not yet confirmed. Arrival is a two-sided fact, so it is a state
  /// rather than a flag — and cancelling is *not* locked here (§5.1.4).
  pendingArrivalConfirm('PENDING_ARRIVAL_CONFIRM'),
  driverArrived('DRIVER_ARRIVED'),
  inTrip('IN_TRIP'),

  /// **Non-terminal.** The trip continues; a second change re-stamps rather
  /// than looping back through `IN_TRIP`.
  destinationChanged('DESTINATION_CHANGED'),

  /// **Terminal.** The trip ended early. Distinct from `CANCELLED` ("never
  /// departed") because settlement and earnings treat the two differently.
  interrupted('INTERRUPTED'),
  completed('COMPLETED'),
  cancelled('CANCELLED');

  const OrderStatus(this.wire);

  final String wire;

  static OrderStatus fromWire(String value) =>
      _decode(values, (OrderStatus v) => v.wire, value, 'OrderStatus');

  bool get isTerminal =>
      this == OrderStatus.completed ||
      this == OrderStatus.cancelled ||
      this == OrderStatus.interrupted;

  /// The order has a driver attached, so a live-trip channel is meaningful.
  bool get hasDriver =>
      this == OrderStatus.accepted ||
      this == OrderStatus.pendingArrivalConfirm ||
      this == OrderStatus.driverArrived ||
      this == OrderStatus.inTrip ||
      this == OrderStatus.destinationChanged;

  /// A new dropoff is meaningful (P4 §4.1). `DESTINATION_CHANGED` is included:
  /// changing again is allowed, up to the server's cap.
  bool get canChangeDestination =>
      this == OrderStatus.inTrip || this == OrderStatus.destinationChanged;

  /// The trip can be ended early (P4 §4.2). `DRIVER_ARRIVED` is included on
  /// purpose: arrival locks *cancelling*, so interrupting is the only way out
  /// of a car the passenger no longer wants to be in (§5.2).
  bool get canInterrupt =>
      this == OrderStatus.driverArrived ||
      this == OrderStatus.inTrip ||
      this == OrderStatus.destinationChanged;

  /// Cancelling is still possible. From `ACCEPTED` it costs money and needs a
  /// reason; from `DRIVER_ARRIVED` onward the server answers 409 `CANCEL_LOCKED`
  /// and no cancel button is shown at all — a button that can only fail is worse
  /// than no button (P4 §6.1).
  bool get canCancel =>
      this == OrderStatus.created ||
      this == OrderStatus.broadcasting ||
      this == OrderStatus.accepted ||
      this == OrderStatus.pendingArrivalConfirm;

  /// A cancellation from here carries a penalty (passenger 100%, driver 50% of
  /// `estimated_total_hkd`) and a mandatory `reason_code` (P4 §5.2.1).
  bool get cancelNeedsReason =>
      this == OrderStatus.accepted || this == OrderStatus.pendingArrivalConfirm;

  /// The driver may claim arrival from here (P4 §4.0.1).
  bool get canClaimArrival => this == OrderStatus.accepted;

  String get labelZh => switch (this) {
    OrderStatus.created => '已建立',
    OrderStatus.broadcasting => '等候司機',
    OrderStatus.accepted => '司機已接單',
    OrderStatus.pendingArrivalConfirm => '等候確認上車',
    OrderStatus.driverArrived => '司機已到達',
    OrderStatus.inTrip => '行程中',
    OrderStatus.destinationChanged => '已改目的地',
    OrderStatus.interrupted => '行程已中斷',
    OrderStatus.completed => '已完成',
    OrderStatus.cancelled => '已取消',
  };
}

/// Why a trip ended early (`app/models/user.py::InterruptionReason`).
///
/// A closed set, not free text: this is the evidence an operator judges
/// afterwards, so it has to be countable. `OTHER` still requires a note — the
/// enum classifies, the note explains.
///
/// Both parties draw from the one enum. The client shows only the options that
/// make sense for the role, but the **server validates** the pair; filtering in
/// the UI is courtesy, validating on the server is authorisation.
enum InterruptionReason {
  accident('ACCIDENT'),
  conflict('CONFLICT'),
  passengerMisconduct('PASSENGER_MISCONDUCT'),
  passengerSick('PASSENGER_SICK'),
  driverMisconduct('DRIVER_MISCONDUCT'),
  vehicleBreakdown('VEHICLE_BREAKDOWN'),
  unsafeRoute('UNSAFE_ROUTE'),
  fareDispute('FARE_DISPUTE'),
  other('OTHER');

  const InterruptionReason(this.wire);

  final String wire;

  static InterruptionReason fromWire(String value) =>
      _decode(values, (InterruptionReason v) => v.wire, value, 'InterruptionReason');

  /// `OTHER` is the only member that needs a free-text note; the server answers
  /// 422 `NOTE_REQUIRED` without one.
  bool get requiresNote => this == InterruptionReason.other;

  /// The passenger's menu (P4 §6.1). `PASSENGER_*` reasons are the driver's to
  /// raise, never the passenger's — offering them here would be a reason the
  /// passenger cannot honestly give.
  static const List<InterruptionReason> forPassenger = <InterruptionReason>[
    InterruptionReason.accident,
    InterruptionReason.conflict,
    InterruptionReason.driverMisconduct,
    InterruptionReason.vehicleBreakdown,
    InterruptionReason.unsafeRoute,
    InterruptionReason.fareDispute,
    InterruptionReason.other,
  ];

  /// The driver's menu (P4 §6.2) — the mirror image.
  static const List<InterruptionReason> forDriver = <InterruptionReason>[
    InterruptionReason.accident,
    InterruptionReason.conflict,
    InterruptionReason.passengerMisconduct,
    InterruptionReason.passengerSick,
    InterruptionReason.vehicleBreakdown,
    InterruptionReason.unsafeRoute,
    InterruptionReason.fareDispute,
    InterruptionReason.other,
  ];

  String get labelZh => switch (this) {
    InterruptionReason.accident => '交通意外 / 撞車',
    InterruptionReason.conflict => '與對方發生衝突',
    InterruptionReason.passengerMisconduct => '乘客態度惡劣',
    InterruptionReason.passengerSick => '乘客嘔吐 / 嚴重不適',
    InterruptionReason.driverMisconduct => '司機態度惡劣',
    InterruptionReason.vehicleBreakdown => '車輛故障',
    InterruptionReason.unsafeRoute => '路線不安全',
    InterruptionReason.fareDispute => '車資爭議',
    InterruptionReason.other => '其他（需填寫說明）',
  };
}

enum LedgerEntryType {
  depositTopup('DEPOSIT_TOPUP'),
  weeklyFeeDeduction('WEEKLY_FEE_DEDUCTION'),
  penaltyDeduction('PENALTY_DEDUCTION'),
  refund('REFUND'),
  adjustment('ADJUSTMENT'),
  fixedRideFee('FIXED_RIDE_FEE'),

  /// P4: the per-trip platform fee, charged to the driver's deposit at
  /// `/start`. The fare itself never passes through the platform (Cap. 374D
  /// intermediary), so this is the platform's actual revenue from a trip.
  platformTripFee('PLATFORM_TRIP_FEE'),

  /// P4: a defaulting party's cancellation penalty, based on the order's own
  /// estimate rather than a flat fee.
  cancellationPenalty('CANCELLATION_PENALTY'),

  /// P4: an operator's dispute ruling that moves money.
  disputeAdjustment('DISPUTE_ADJUSTMENT');

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
    LedgerEntryType.fixedRideFee => '一口價服務費',
    LedgerEntryType.platformTripFee => '平台行程費',
    LedgerEntryType.cancellationPenalty => '違約罰款',
    LedgerEntryType.disputeAdjustment => '爭議裁決調整',
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
///
/// `LEFT` is a driver who left of their own accord, `REMOVED` one a manager took
/// off. Nothing writes `LEFT` yet, but the column's CHECK constraint accepts it,
/// so the mirror still has to decode it: an unmapped token throws, and the roster
/// screen would die on a row that is perfectly legal in the database.
enum FleetMemberStatus {
  active('ACTIVE'),
  left('LEFT'),
  removed('REMOVED');

  const FleetMemberStatus(this.wire);

  final String wire;

  static FleetMemberStatus fromWire(String value) =>
      _decode(values, (FleetMemberStatus v) => v.wire, value, 'FleetMemberStatus');

  String get labelZh => switch (this) {
    FleetMemberStatus.active => '在隊',
    FleetMemberStatus.left => '自行離隊',
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
