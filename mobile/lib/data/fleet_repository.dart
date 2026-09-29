import '../core/network/api_client.dart';
import '../models/enums.dart';
import '../models/fleet.dart';

/// Fleets (的士車隊) — both surfaces in one repository.
///
/// Mirrors the backend, where `app/api/fleets.py` holds the admin and the
/// driver-facing routers in one file: a fleet endpoint is nearly always paired
/// with its counterpart (`POST /admin/fleets/{id}/members` and
/// `GET /fleets/{id}/members`), and splitting them by audience would mean
/// editing two files for every change to one concept.
///
/// The authorisation split is real, though, and lives on the server:
/// `/admin/fleets/*` requires ADMIN, `/fleets/*` requires an ACTIVE account and
/// then checks membership per request, answering **404 rather than 403** for a
/// fleet that exists but is not the caller's — so the routes cannot be used to
/// enumerate which fleets exist. There is deliberately no "list all fleets" call
/// a driver can make.
class FleetRepository {
  FleetRepository(this._api);

  final ApiClient _api;

  // -------------------------------------------------------------------------
  // Driver-facing
  // -------------------------------------------------------------------------

  /// `GET /fleets/me` — the caller's fleet, or a null pair when they are not on
  /// a roster. A driver is on at most one roster (partial UNIQUE index on the
  /// server), so there is no list here.
  Future<MyFleet> myFleet() async {
    final Map<String, dynamic> json = await _api.get('/api/v1/fleets/me');
    return MyFleet.fromJson(json);
  }

  /// `GET /fleets/{id}` — 404 unless the caller is on this fleet's roster.
  Future<Fleet> detail(String fleetId) async {
    final Map<String, dynamic> json = await _api.get('/api/v1/fleets/$fleetId');
    return Fleet.fromJson(json);
  }

  /// `GET /fleets/{id}/members` — the roster, active members only.
  Future<List<FleetMember>> members(String fleetId) async {
    final Map<String, dynamic> json = await _api.get('/api/v1/fleets/$fleetId/members');
    return FleetList<FleetMember>.fromJson(json, FleetMember.fromJson).items;
  }

  /// `GET /fleets/{id}/settlement` — the fleet's own weekly history, newest
  /// first. Read-only: running a settlement is an admin action.
  Future<List<FleetSettlementRun>> settlementHistory(String fleetId, {int limit = 52}) async {
    final Map<String, dynamic> json = await _api.get(
      '/api/v1/fleets/$fleetId/settlement',
      query: <String, dynamic>{'limit': limit},
    );
    return FleetList<FleetSettlementRun>.fromJson(json, FleetSettlementRun.fromJson).items;
  }

  // -------------------------------------------------------------------------
  // Admin
  // -------------------------------------------------------------------------

  /// `GET /admin/fleets`.
  ///
  /// The fleet list is a `{items, total, limit, offset}` page, unlike the
  /// roster and settlement-history routes which send a bare `{items}`.
  Future<({List<Fleet> items, int total})> listFleets({
    FleetStatus? status,
    int limit = 50,
    int offset = 0,
  }) async {
    final Map<String, dynamic> json = await _api.get(
      '/api/v1/admin/fleets',
      query: <String, dynamic>{'status_filter': ?status?.wire, 'limit': limit, 'offset': offset},
    );
    return (
      items: FleetList<Fleet>.fromJson(json, Fleet.fromJson).items,
      total: json['total'] as int? ?? 0,
    );
  }

  /// `POST /admin/fleets` (201).
  ///
  /// A fleet is **created by an admin**, never by a driver: HK fleets are
  /// licensed operators, so onboarding is an operator action on the platform
  /// side. [discountPercent] is the volume discount applied to the platform's
  /// weekly fee, `0`–`100`.
  Future<Fleet> createFleet({
    required String name,
    required String licenseNo,
    String discountPercent = '0',
    String? contactName,
    String? contactPhone,
    String? note,
  }) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/admin/fleets',
      data: <String, dynamic>{
        'name': name,
        'license_no': licenseNo,
        'weekly_fee_discount_percent': discountPercent,
        'contact_name': ?contactName,
        'contact_phone': ?contactPhone,
        'note': ?note,
      },
    );
    return Fleet.fromJson(json);
  }

  /// `PATCH /admin/fleets/{id}` — rename, re-rate, or change operator status.
  ///
  /// Suspending a fleet stops its settlement: `FleetSettlementService` refuses a
  /// fleet that is not ACTIVE, because a suspended operator is not dispatching.
  Future<Fleet> updateFleet(
    String fleetId, {
    String? name,
    FleetStatus? status,
    String? discountPercent,
    String? contactName,
    String? contactPhone,
    String? note,
  }) async {
    final Map<String, dynamic> json = await _api.patch(
      '/api/v1/admin/fleets/$fleetId',
      data: <String, dynamic>{
        'name': ?name,
        'status': ?status?.wire,
        'weekly_fee_discount_percent': ?discountPercent,
        'contact_name': ?contactName,
        'contact_phone': ?contactPhone,
        'note': ?note,
      },
    );
    return Fleet.fromJson(json);
  }

  /// `GET /admin/fleets/{id}/members`.
  ///
  /// [includeLeft] also returns rows marked `REMOVED`, so a dispute about who
  /// was on the roster in a given week can be answered.
  Future<List<FleetMember>> adminMembers(String fleetId, {bool includeLeft = false}) async {
    final Map<String, dynamic> json = await _api.get(
      '/api/v1/admin/fleets/$fleetId/members',
      query: <String, dynamic>{'include_left': includeLeft},
    );
    return FleetList<FleetMember>.fromJson(json, FleetMember.fromJson).items;
  }

  /// `POST /admin/fleets/{id}/members` (201).
  ///
  /// Adds a driver to the roster. The driver must already have a profile, and
  /// may be on at most one ACTIVE roster — a second add is a 409.
  ///
  /// Adding a member **takes them off the platform-wide weekly run**: from the
  /// next settlement they are billed at the fleet's discounted rate instead of
  /// the flat fee. That is the intended behaviour, and the reason the platform
  /// run reports how many drivers it left to the fleets.
  Future<FleetMember> addMember(
    String fleetId, {
    required String driverProfileId,
    FleetMemberRole role = FleetMemberRole.member,
  }) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/admin/fleets/$fleetId/members',
      data: <String, dynamic>{'driver_profile_id': driverProfileId, 'member_role': role.wire},
    );
    return FleetMember.fromJson(json);
  }

  /// `DELETE /admin/fleets/{id}/members/{driverProfileId}`.
  ///
  /// Marks the membership `REMOVED` rather than deleting it, so the week in
  /// which the driver left stays reconstructible. They return to the
  /// platform-wide weekly run from the next settlement.
  Future<FleetMember> removeMember(String fleetId, String driverProfileId) async {
    final Map<String, dynamic> json = await _api.delete(
      '/api/v1/admin/fleets/$fleetId/members/$driverProfileId',
    );
    return FleetMember.fromJson(json);
  }

  /// `GET /admin/fleets/{id}/settlement` — stored weekly rows, newest first.
  Future<List<FleetSettlementRun>> adminSettlementHistory(String fleetId, {int limit = 52}) async {
    final Map<String, dynamic> json = await _api.get(
      '/api/v1/admin/fleets/$fleetId/settlement',
      query: <String, dynamic>{'limit': limit},
    );
    return FleetList<FleetSettlementRun>.fromJson(json, FleetSettlementRun.fromJson).items;
  }

  /// `POST /admin/fleets/{id}/settlement/run` — charge this fleet's members.
  ///
  /// Idempotent per (fleet, ISO week): a re-run charges nobody twice and the
  /// stored aggregate is updated rather than duplicated, so this is safe to
  /// retry after a failed batch. Pass [period] (`YYYY-Www`) to re-run a specific
  /// week. Rejected for a fleet that is not ACTIVE.
  Future<FleetSettlementRun> runSettlement(String fleetId, {String? period}) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/admin/fleets/$fleetId/settlement/run',
      query: <String, dynamic>{'period': ?period},
    );
    return FleetSettlementRun.fromJson(json);
  }
}
