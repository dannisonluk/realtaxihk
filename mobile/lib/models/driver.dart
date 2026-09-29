import '../core/format/money.dart';
import '../core/network/wire.dart';
import 'enums.dart';

/// The driver's deposit ledger balance. `required_hkd` is the platform's
/// minimum; `is_fulfilled` flips when `balance_hkd >= required_hkd`, which is
/// what moves the driver from `DEPOSIT_REQUIRED` to `ACTIVE`.
class DriverDeposit {
  const DriverDeposit({
    required this.balanceHkd,
    required this.heldHkd,
    required this.requiredHkd,
    required this.isFulfilled,
  });

  /// Tolerant of a missing row: `GET /drivers/me` substitutes
  /// `{"required_hkd": "500.0", "is_fulfilled": false}` when the driver has no
  /// deposit yet, so `balance_hkd` / `held_hkd` may be absent.
  factory DriverDeposit.fromJson(Map<String, dynamic> json) => DriverDeposit(
    balanceHkd: Money.parse(json['balance_hkd'] ?? '0.0'),
    heldHkd: Money.parse(json['held_hkd'] ?? '0.0'),
    requiredHkd: Money.parse(json['required_hkd'] ?? '0.0'),
    isFulfilled: json['is_fulfilled'] as bool? ?? false,
  );

  final Money balanceHkd;
  final Money heldHkd;
  final Money requiredHkd;
  final bool isFulfilled;

  Money get shortfall => Money.parse(
    (requiredHkd.asDouble - balanceHkd.asDouble).clamp(0, double.infinity).toString(),
  );
}

/// `GET /api/v1/drivers/me` (with `deposit`) and the 201 body of
/// `POST /api/v1/drivers/register` (without).
class DriverProfile {
  const DriverProfile({
    required this.id,
    required this.userId,
    required this.status,
    required this.taxiType,
    required this.taxiDriverPlateNo,
    required this.vehicleRegMark,
    required this.isOnline,
    this.deposit,
  });

  factory DriverProfile.fromJson(Map<String, dynamic> json) => DriverProfile(
    id: asString(json['id'], 'driver.id'),
    userId: asString(json['user_id'], 'driver.user_id'),
    status: DriverStatus.fromWire(asString(json['status'], 'driver.status')),
    taxiType: TaxiType.fromWire(asString(json['taxi_type'], 'driver.taxi_type')),
    taxiDriverPlateNo: asString(json['taxi_driver_plate_no'], 'driver.taxi_driver_plate_no'),
    vehicleRegMark: asString(json['vehicle_reg_mark'], 'driver.vehicle_reg_mark'),
    isOnline: json['is_online'] as bool? ?? false,
    deposit: json['deposit'] == null
        ? null
        : DriverDeposit.fromJson(asMap(json['deposit'], 'driver.deposit')),
  );

  /// The driver **profile** id. Order assignment (`orders.driver_id`) is this
  /// id, not the account id — see `SEC-27`, which is also why
  /// `GET /trips/{id}/location` returns it and never the account UUID.
  final String id;

  /// The account id, i.e. the `sub` claim of the driver's JWTs.
  final String userId;

  final DriverStatus status;
  final TaxiType taxiType;
  final String taxiDriverPlateNo;
  final String vehicleRegMark;
  final bool isOnline;
  final DriverDeposit? deposit;
}

/// Body of `POST /api/v1/drivers/register`. Enters `PENDING_KYC`; a duplicate
/// registration for the same account is a 409 `CONFLICT`.
class DriverRegisterRequest {
  const DriverRegisterRequest({
    required this.hkIdLast4,
    required this.taxiDriverPlateNo,
    required this.vehicleRegMark,
    required this.taxiType,
  });

  final String hkIdLast4;
  final String taxiDriverPlateNo;
  final String vehicleRegMark;
  final TaxiType taxiType;

  Map<String, dynamic> toJson() => <String, dynamic>{
    'hk_id_last4': hkIdLast4,
    'taxi_driver_plate_no': taxiDriverPlateNo,
    'vehicle_reg_mark': vehicleRegMark,
    'taxi_type': taxiType.wire,
  };
}
