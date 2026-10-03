/// Decodes every captured response in `test/fixtures/` with the **real** models.
///
/// `scripts/dev/gen_mobile_fixtures.py` boots the API and writes raw responses to
/// `test/fixtures/`; this is the other half of that loop.
///
/// **Why it still exists now that the API declares response models.** It was
/// written when `/openapi.json` typed almost nothing — 28 paths, all but one
/// response schema `{}` — so every Dart model was an assumption about a
/// hand-built dict. That is no longer true: all 69 operations now declare a
/// real `response_model=` (`app/api/schemas/`). This tool is kept because it
/// checks a **different** thing: the Python schemas are validated against the
/// *handlers*, this validates the **Dart decoders** against the *bytes*. A
/// `response_model=` can be perfectly faithful to the handler and still be a
/// shape the client cannot read — a field renamed in both the model and the
/// handler passes every Python test and breaks the app. This is the only check
/// that would catch that.
///
/// The Python-side counterpart is `scripts/verify/audit_response_models.py`, which
/// proves each fixture's keys survive its `response_model=` (a response model is
/// a filter, and a missing field silently deletes data from the response). Run
/// both after a backend change — they fail in different places.
///
/// Three things this caught that reading the source did not make obvious:
///
///   * `GET /orders` returns `{items}` with **no** `next_cursor`, while
///     `GET /drivers/me/ledger` returns `{items, next_cursor}`. The two list
///     endpoints paginate differently, so "is there another page" has to be
///     inferred from a full page for orders and read directly for the ledger.
///   * `POST /admin/drivers/{id}/review` returns only `{id, status}` — not the
///     profile the list endpoint returns.
///   * the error envelope's `details` was being discarded for four endpoints,
///     because `BusinessRuleError` subclasses `ValueError` and the routes
///     caught `ValueError` first. The `retry_after_seconds` and
///     `attempts_remaining` fields never reached a client. Fixed in
///     `app/api/{auth,orders,fare}.py`; asserted below.
///
/// Run it after any backend change:
///
///     python scripts/dev/gen_mobile_fixtures.py
///     dart run tool/verify_contract.dart
///
/// It fails if a fixture has no decoder **or** a decoder has no fixture, so a
/// new endpoint cannot be added to the generator and quietly go unverified.
library;

import 'dart:convert';
import 'dart:io';

import 'package:hkfastdc_mobile/core/format/money.dart';
import 'package:hkfastdc_mobile/core/network/api_exception.dart';
import 'package:hkfastdc_mobile/core/network/wire.dart';
import 'package:hkfastdc_mobile/models/admin.dart';
import 'package:hkfastdc_mobile/models/auth.dart';
import 'package:hkfastdc_mobile/models/driver.dart';
import 'package:hkfastdc_mobile/models/enums.dart';
import 'package:hkfastdc_mobile/models/fare.dart';
import 'package:hkfastdc_mobile/models/fleet.dart';
import 'package:hkfastdc_mobile/models/ledger.dart';
import 'package:hkfastdc_mobile/models/order.dart';
import 'package:hkfastdc_mobile/models/refund.dart';
import 'package:hkfastdc_mobile/models/trip.dart';

typedef Decoder = String Function(Object? body);

// ---------------------------------------------------------------------------
// Assertions
// ---------------------------------------------------------------------------

/// The load-bearing claim in `core/network/wire.dart`: pydantic serialises
/// `Decimal` as a JSON **string**. If this ever becomes a number, every
/// hand-written model that assumed `asString` still works (they accept both),
/// but the reason for `asDouble`'s leniency disappears.
void _rawIsString(Object? body, String field) {
  final Map<String, dynamic> m = asMap(body, 'body');
  final Object? value = m[field];
  if (value is! String) {
    throw StateError(
      '$field is ${value.runtimeType}, expected a JSON string — '
      'pydantic serialises Decimal as a string',
    );
  }
}

void _expect(bool condition, String message) {
  if (!condition) {
    throw StateError(message);
  }
}

/// Parses [body] as the server's error envelope and checks the code.
ApiException _envelope(Object? body, {required int status, required String code}) {
  final ApiException error = ApiException.fromEnvelope(body, statusCode: status);
  _expect(error.code == code, 'expected code $code, got ${error.code} ("${error.message}")');
  _expect(error.statusCode == status, 'expected status $status, got ${error.statusCode}');
  return error;
}

String _describeError(ApiException error) {
  final String details = error.details.isEmpty
      ? 'details={}'
      : 'details=${jsonEncode(error.details)}';
  return '${error.statusCode} ${error.code} · ${error.message} · $details';
}

Order _order(Object? body, {required OrderStatus status}) {
  final Order order = Order.fromJson(asMap(body, 'order'));
  _expect(order.status == status, 'expected status ${status.wire}, got ${order.status.wire}');
  return order;
}

/// One roster row, plus the omission the server makes on purpose.
FleetMember _rosterRow(Object? body, {required FleetMemberRole expectedRole}) {
  final Map<String, dynamic> m = asMap(body, 'member');
  // `_member_out` in `app/api/fleets.py` deliberately drops the driver's
  // identity documents: an operator needs to know *who is on the roster*, not to
  // read back what the driver supplied to the platform. Adding any of these to
  // the model would be undoing a deliberate decision, so it is asserted here.
  for (final String forbidden in <String>[
    'hk_id_last4',
    'taxi_driver_plate_no',
    'vehicle_reg_mark',
    'user_id',
  ]) {
    _expect(
      !m.containsKey(forbidden),
      'the roster row must not expose $forbidden — see app/api/fleets.py::_member_out',
    );
  }
  final FleetMember member = FleetMember.fromJson(m);
  _expect(
    member.memberRole == expectedRole,
    'expected ${expectedRole.wire}, got ${member.memberRole.wire}',
  );
  _expect(member.status == FleetMemberStatus.active, 'expected an ACTIVE roster row');
  _expect(member.leftAt == null, 'an ACTIVE row has no left_at');
  // The only handle the wire gives us; it must be the profile id's tail.
  _expect(
    member.driverProfileId.endsWith(member.shortId.replaceFirst('…', '')),
    'shortId should be the tail of the profile id: ${member.shortId}',
  );
  return member;
}

/// A `{items}` roster.
String _roster(Object? body) {
  final List<FleetMember> members = FleetList<FleetMember>.fromJson(
    asMap(body, 'body'),
    FleetMember.fromJson,
  ).items;
  _expect(members.isNotEmpty, 'the fixture roster should not be empty');
  for (final FleetMember member in members) {
    _expect(member.driverProfileId.isNotEmpty, 'a roster row needs a driver_profile_id');
  }
  final FleetMember first = members.first;
  _expect(first.isBillable, 'the fixture member is ACTIVE and funded, so billable');
  return 'items=${members.length} first=${first.shortId} ${first.taxiType.wire} '
      'role=${first.memberRole.wire} billable=${first.isBillable}';
}

/// A `{items}` settlement history, newest week first.
String _settlementHistory(Object? body) {
  final List<FleetSettlementRun> runs = FleetList<FleetSettlementRun>.fromJson(
    asMap(body, 'body'),
    FleetSettlementRun.fromJson,
  ).items;
  _expect(runs.isNotEmpty, 'the fixture settlement history should not be empty');
  final FleetSettlementRun run = runs.first;
  // A *stored* row: `created_at` is present, `gross_fee_hkd` is not (it is never
  // stored), so the saving cannot be derived from history — only from the live
  // run. The model must survive both shapes.
  _expect(run.createdAt != null, 'a stored settlement row should carry created_at');
  _expect(run.grossFeeHkd == null, 'a stored row has no gross_fee_hkd to report');
  _expect(run.discountSaving == null, 'the saving is underivable without the gross');
  _expect(run.feeHkd.asDouble == 150, 'the stored per-member fee should be 150');
  _expect(run.collectedHkd.asDouble == 150, 'collected should match the single member');
  return 'items=${runs.length} period=${run.period} fee=${run.feeHkd.hkd} '
      'collected=${run.collectedHkd.hkd} gross=absent anomaly=${run.hasAnomaly}';
}

// ---------------------------------------------------------------------------
// Decoders — one per fixture, each exercising the model the app really uses
// ---------------------------------------------------------------------------

final Map<String, Decoder> _decoders = <String, Decoder>{
  // ---- auth ---------------------------------------------------------------
  'auth_otp_request': (Object? body) {
    final Map<String, dynamic> m = asMap(body, 'body');
    _expect(asBool(m['sent'], 'sent'), 'sent should be true');
    final int expires = asInt(m['expires_in'], 'expires_in');
    // SEC-02: the code itself is never echoed, in dev mode or otherwise.
    _expect(!m.containsKey('dev_code'), 'response must not echo the OTP code');
    return 'sent=true expires_in=${expires}s';
  },
  'auth_verify': (Object? body) {
    final Map<String, dynamic> m = asMap(body, 'body');
    final AuthSession session = AuthSession.fromJson(m);
    _expect(AuthSession.createdFromJson(m), 'first login should report created=true');
    return 'role=${session.user.role.wire} created=true phone=${session.user.phoneMasked}';
  },
  'auth_verify_admin': (Object? body) {
    final Map<String, dynamic> m = asMap(body, 'body');
    final AuthSession session = AuthSession.fromJson(m);
    _expect(session.user.role == UserRole.admin, 'expected ADMIN, got ${session.user.role.wire}');
    return 'role=ADMIN created=${AuthSession.createdFromJson(m)}';
  },
  'auth_verify_new_user': (Object? body) {
    final Map<String, dynamic> m = asMap(body, 'body');
    final AuthSession session = AuthSession.fromJson(m);
    _expect(AuthSession.createdFromJson(m), 'a brand-new phone must report created=true');
    _expect(
      session.user.role == UserRole.passenger,
      'signup must default to PASSENGER, got ${session.user.role.wire}',
    );
    return 'role=PASSENGER created=true';
  },
  'auth_refresh': (Object? body) {
    final Map<String, dynamic> m = asMap(body, 'body');
    final AuthSession session = AuthSession.fromJson(m);
    // `/auth/refresh` omits `created`; reading it must not throw.
    _expect(!m.containsKey('created'), 'refresh should not carry `created`');
    _expect(!AuthSession.createdFromJson(m), 'createdFromJson should default to false');
    return 'role=${session.user.role.wire} created=(absent)';
  },
  'auth_me': (Object? body) {
    final AppUser user = AppUser.fromJson(asMap(body, 'body'));
    // The full number is never returned — only a masked form.
    _expect(user.phoneMasked.contains('*'), 'phone must arrive masked, got ${user.phoneMasked}');
    return 'role=${user.role.wire} phone=${user.phoneMasked}';
  },

  // ---- errors: the envelope every failure must parse -----------------------
  'error_not_found': (Object? body) =>
      _describeError(_envelope(body, status: 404, code: ApiException.notFound)),
  'error_unauthorized': (Object? body) {
    final ApiException error = _envelope(body, status: 401, code: ApiException.unauthorized);
    _expect(error.isAuthFailure, 'a 401 must be classified as an auth failure');
    return _describeError(error);
  },
  'error_forbidden': (Object? body) =>
      _describeError(_envelope(body, status: 403, code: ApiException.forbidden)),
  'error_validation': (Object? body) {
    final ApiException error = _envelope(body, status: 422, code: ApiException.validationError);
    final Object? errors = error.details['errors'];
    _expect(errors is List, 'details.errors should be a list, got ${errors.runtimeType}');
    final Map<String, dynamic> first = asMap(asList(errors, 'errors').first, 'errors[0]');
    // SEC-09: pydantic's `input` is stripped, so a rejected credential cannot
    // be reflected back out of the response.
    _expect(!first.containsKey('input'), 'details.errors[] must not echo the input');
    _expect(asString(first['msg'], 'errors[0].msg').isNotEmpty, 'errors[0].msg is empty');
    return _describeError(error);
  },
  'error_validation_range': (Object? body) =>
      _describeError(_envelope(body, status: 422, code: ApiException.validationError)),
  'error_business_rule': (Object? body) {
    final ApiException error = _envelope(body, status: 400, code: ApiException.businessRule);
    // This is the regression guard for the `except ValueError` bug: the state
    // machine's structured `details` used to be thrown away.
    _expect(
      error.details['from'] == 'COMPLETED' && error.details['to'] == 'IN_TRIP',
      'the illegal-transition details were discarded: ${error.details}',
    );
    return _describeError(error);
  },
  'error_otp_cooldown': (Object? body) {
    final ApiException error = _envelope(body, status: 400, code: ApiException.businessRule);
    _expect(
      asInt(error.details['retry_after_seconds'], 'retry_after_seconds') == 60,
      'the resend cooldown lost its retry_after_seconds: ${error.details}',
    );
    return _describeError(error);
  },
  'error_otp_bad_code': (Object? body) {
    final ApiException error = _envelope(body, status: 400, code: ApiException.businessRule);
    _expect(
      asInt(error.details['attempts_remaining'], 'attempts_remaining') == 4,
      'the attempt counter lost its attempts_remaining: ${error.details}',
    );
    return _describeError(error);
  },
  'auth_refresh_replay': (Object? body) {
    final ApiException error = _envelope(body, status: 401, code: ApiException.unauthorized);
    _expect(error.isAuthFailure, 'a replayed refresh token must sign the user out');
    return _describeError(error);
  },

  // ---- fare ---------------------------------------------------------------
  'fare_estimate': (Object? body) {
    // The Decimal-as-string claim, asserted on the raw body before decoding.
    _rawIsString(body, 'distance_km');
    _rawIsString(body, 'total_fare');
    final FareEstimate estimate = FareEstimate.fromJson(asMap(body, 'body'));
    _expect(estimate.surcharges.isNotEmpty, 'expected surcharges for a tunnel + baggage quote');
    _expect(estimate.disclaimerZh.isNotEmpty, 'the Cap. 374D disclaimer must be present');
    return '${estimate.taxiType.wire} ${estimate.distanceKm}km '
        'total=${estimate.totalFare.hkd} surcharges=${estimate.surcharges.length}';
  },

  // ---- orders -------------------------------------------------------------
  'order_created': (Object? body) {
    final Order order = _order(body, status: OrderStatus.broadcasting);
    return '${order.status.wire} total=${order.estimatedTotalHkd.hkd} '
        'tunnels=${order.fare.tunnels.map((Tunnel t) => t.wire).join(",")}';
  },
  'order_detail': (Object? body) => _order(body, status: OrderStatus.broadcasting).status.wire,
  'order_grabbed': (Object? body) => _order(body, status: OrderStatus.accepted).status.wire,
  'order_arrive': (Object? body) => _order(body, status: OrderStatus.driverArrived).status.wire,
  'order_start': (Object? body) => _order(body, status: OrderStatus.inTrip).status.wire,
  'order_complete': (Object? body) {
    final Order order = _order(body, status: OrderStatus.completed);
    _expect(order.completedAt != null, 'a completed order must carry completed_at');
    return '${order.status.wire} completed_at=${order.completedAt}';
  },
  'orders_page': (Object? body) {
    final Map<String, dynamic> m = asMap(body, 'body');
    // The finding: this endpoint returns no cursor at all.
    _expect(
      !m.containsKey('next_cursor'),
      'GET /orders grew a next_cursor — OrderPage.nextCursor must be re-derived',
    );
    final OrderPage page = OrderPage.fromJson(m);
    _expect(page.items.isNotEmpty, 'expected the order just created');
    // One item against a limit of 20 is not a full page, so there is no next.
    _expect(page.nextCursor == null, 'a partial page must not offer a cursor');
    return 'items=${page.items.length} limit=${page.limit} next=${page.nextCursor}';
  },
  'orders_page_driver': (Object? body) {
    final OrderPage page = OrderPage.fromJson(asMap(body, 'body'));
    _expect(
      page.items.every((Order o) => o.status.isTerminal),
      'the driver history should only hold the completed order',
    );
    return 'items=${page.items.length} statuses=${page.items.map((Order o) => o.status.wire).join(",")}';
  },
  'orders_nearby': (Object? body) {
    final NearbyOrders nearby = NearbyOrders.fromJson(asMap(body, 'body'));
    // `degraded` is absent on a healthy response; the model must default it.
    _expect(!nearby.degraded, 'a healthy geo index must not report degraded');
    return 'items=${nearby.items.length} degraded=${nearby.degraded}';
  },
  'trip_location': (Object? body) {
    final TripLocationSnapshot snapshot = TripLocationSnapshot.fromJson(asMap(body, 'body'));
    // No driver had ticked yet when this was captured, so the null path is the
    // one under test — a non-null lat/lng here would mean the fixture is stale.
    _expect(!snapshot.hasFix, 'expected no fix before the driver streamed a tick');
    return '${snapshot.status.wire} driver=${snapshot.driverProfileId} fix=${snapshot.hasFix}';
  },

  // ---- driver -------------------------------------------------------------
  'driver_register': (Object? body) {
    final DriverProfile profile = DriverProfile.fromJson(asMap(body, 'body'));
    _expect(
      profile.status == DriverStatus.pendingKyc,
      'registration must enter PENDING_KYC, got ${profile.status.wire}',
    );
    // The 201 body has no `deposit` key at all.
    _expect(profile.deposit == null, 'the register response should not carry a deposit');
    return '${profile.status.wire} ${profile.taxiType.wire} deposit=absent';
  },
  'driver_me_no_deposit': (Object? body) {
    final DriverProfile profile = DriverProfile.fromJson(asMap(body, 'body'));
    final DriverDeposit deposit = profile.deposit!;
    // The substituted object carries only two keys; the model must default the
    // other two rather than throwing.
    _expect(!deposit.isFulfilled, 'an unfunded deposit must not read as fulfilled');
    _expect(deposit.balanceHkd.isZero, 'balance should default to zero');
    return '${profile.status.wire} required=${deposit.requiredHkd.hkd} '
        'balance=${deposit.balanceHkd.hkd} shortfall=${deposit.shortfall.hkd}';
  },
  'driver_me': (Object? body) {
    final DriverProfile profile = DriverProfile.fromJson(asMap(body, 'body'));
    final DriverDeposit deposit = profile.deposit!;
    _expect(
      profile.status == DriverStatus.active,
      'a funded deposit must flip the driver to ACTIVE, got ${profile.status.wire}',
    );
    _expect(deposit.isFulfilled, 'is_fulfilled should be true once funded');
    return '${profile.status.wire} balance=${deposit.balanceHkd.hkd} '
        'required=${deposit.requiredHkd.hkd} fulfilled=${deposit.isFulfilled}';
  },
  'driver_location': (Object? body) {
    final Map<String, dynamic> m = asMap(body, 'body');
    _expect(asBool(m['ok'], 'ok'), 'the location tick should acknowledge');
    return 'ok=true';
  },
  'ledger_page': (Object? body) {
    final LedgerPage page = LedgerPage.fromJson(asMap(body, 'body'));
    _expect(page.items.isNotEmpty, 'expected the deposit top-up that funded the driver');
    final LedgerEntry entry = page.items.first;
    // Unlike `/orders`, this endpoint *does* send a cursor — null at the end.
    _expect(!page.hasMore, 'a single-entry ledger should report no more pages');
    return 'items=${page.items.length} first=${entry.entryType.wire} '
        'amount=${entry.amountHkd.signedHkd} next=${page.nextCursor}';
  },

  // ---- refunds ------------------------------------------------------------
  'refund_request': (Object? body) {
    final RefundRequest refund = RefundRequest.fromJson(asMap(body, 'body'));
    _expect(
      refund.status == RefundStatus.pending,
      'a new refund must be PENDING, got ${refund.status.wire}',
    );
    // The driver's own view omits the admin-only fields.
    _expect(refund.driverProfileId == null, 'the driver view should omit driver_profile_id');
    _expect(refund.decidedBy == null, 'the driver view should omit decided_by');
    return '${refund.status.wire} ${refund.amountHkd.hkd} decided_by=absent';
  },
  'refund_me': (Object? body) {
    final MyRefund mine = MyRefund.fromJson(asMap(body, 'body'));
    _expect(mine.refund != null, 'expected the refund just filed');
    return 'refund=${mine.refund!.status.wire} ${mine.refund!.amountHkd.hkd}';
  },
  'admin_refunds': (Object? body) {
    final Paged<RefundRequest> page = Paged<RefundRequest>.fromJson(
      asMap(body, 'body'),
      RefundRequest.fromJson,
    );
    final RefundRequest first = page.items.first;
    // The admin queue is the same model with the extra fields populated.
    _expect(first.driverProfileId != null, 'the admin view must expose driver_profile_id');
    return 'total=${page.total} limit=${page.limit} hasMore=${page.hasMore} '
        'first=${first.status.wire}';
  },
  'admin_refund_decision': (Object? body) {
    final RefundRequest refund = RefundRequest.fromJson(asMap(body, 'body'));
    _expect(
      refund.status == RefundStatus.approved,
      'the decision should have approved, got ${refund.status.wire}',
    );
    _expect(refund.decidedAt != null, 'an approved refund must carry decided_at');
    _expect(refund.decidedBy != null, 'an approved refund must record who decided');
    return '${refund.status.wire} note=${refund.decisionNote}';
  },

  // ---- admin --------------------------------------------------------------
  'admin_drivers': (Object? body) {
    final Paged<AdminDriverRow> page = Paged<AdminDriverRow>.fromJson(
      asMap(body, 'body'),
      AdminDriverRow.fromJson,
    );
    _expect(page.items.isNotEmpty, 'the KYC queue should not be empty');
    return 'total=${page.total} limit=${page.limit} offset=${page.offset} '
        'hasMore=${page.hasMore}';
  },
  'admin_review': (Object? body) {
    final Map<String, dynamic> m = asMap(body, 'body');
    // The finding: the review response is `{id, status}` only, not a profile.
    _expect(
      m.keys.toSet().containsAll(<String>{'id', 'status'}) && m.length == 2,
      'POST /admin/drivers/{id}/review changed shape: ${m.keys.toList()}',
    );
    final DriverStatus status = DriverStatus.fromWire(asString(m['status'], 'status'));
    _expect(
      status == DriverStatus.depositRequired,
      'approving KYC must move to DEPOSIT_REQUIRED, got ${status.wire}',
    );
    return 'id=… status=${status.wire}';
  },
  'admin_grant': (Object? body) {
    final DepositGrantResult grant = DepositGrantResult.fromJson(asMap(body, 'body'));
    _expect(grant.isFulfilled, 'a 500 grant should fulfil the 500 deposit');
    _expect(
      grant.driverStatus == DriverStatus.active,
      'fulfilling the deposit must activate the driver, got ${grant.driverStatus.wire}',
    );
    // SEC-13: the server namespaces the caller's reference.
    _expect(
      grant.reference.startsWith('grant:'),
      'the grant reference should be namespaced, got ${grant.reference}',
    );
    return '${grant.driverStatus.wire} balance=${grant.balanceHkd.hkd} ref=${grant.reference}';
  },
  'admin_settlement': (Object? body) {
    final SettlementRun run = SettlementRun.fromJson(asMap(body, 'body'));
    _expect(run.period.contains('-W'), 'period should be an ISO week, got ${run.period}');
    // `fee_hkd` is the one money field that is NOT quantised to 0.1 — it arrives
    // as "200", not "200.0". Money keeps the exact string, so it round-trips.
    _rawIsString(body, 'fee_hkd');
    final Money fee = Money.parse(asMap(body, 'body')['fee_hkd']);
    _expect(fee.asDouble == 200, 'fee should be 200, got ${fee.asDouble}');
    // This run happened *before* the fixture fleet existed, so nobody was
    // excluded — the counter is present and zero. The non-zero case is pinned by
    // `admin_settlement_fleet_managed` below.
    _expect(run.fleetManaged == 0, 'expected no fleet members yet, got ${run.fleetManaged}');
    return 'period=${run.period} fee=${run.feeHkd.hkd} charged=${run.charged} '
        'skipped=${run.skipped} anomaly=${run.hasAnomaly} fleet=${run.fleetManaged}';
  },

  // ---- fleets -------------------------------------------------------------
  'fleet_created': (Object? body) {
    // The 201 echoes the *request* Decimal, so this one is "25", not "25.00" —
    // while `fleet_detail` below reads the same field back out of the database
    // and gets "25.00". The model must accept both, which is why the discount is
    // kept as a string rather than parsed into a number at the edge.
    _rawIsString(body, 'weekly_fee_discount_percent');
    final Fleet fleet = Fleet.fromJson(asMap(body, 'body'));
    _expect(fleet.status == FleetStatus.active, 'a new fleet is ACTIVE, got ${fleet.status.wire}');
    _expect(fleet.memberCount == 0, 'a new fleet has no members, got ${fleet.memberCount}');
    _expect(!fleet.isFullyDiscounted, 'a 25% discount is not a full discount');
    return '${fleet.status.wire} ${fleet.licenseNo} discount=${fleet.discountLabel} '
        'members=${fleet.memberCount}';
  },
  'admin_fleets': (Object? body) {
    final Map<String, dynamic> m = asMap(body, 'body');
    // A `{items, total, limit, offset}` page — unlike the roster and settlement
    // routes, which send a bare `{items}`.
    _expect(m.containsKey('total'), 'the fleet register should page: ${m.keys.toList()}');
    final List<Fleet> items = FleetList<Fleet>.fromJson(m, Fleet.fromJson).items;
    _expect(items.isNotEmpty, 'the register should hold the fleet just created');
    // Read back from the database, so the decimal is the stored form.
    _expect(
      items.first.weeklyFeeDiscountPercent.contains('.'),
      'the stored discount should be a quantised decimal string, got '
      '${items.first.weeklyFeeDiscountPercent}',
    );
    return 'items=${items.length} total=${asInt(m['total'], 'total')} '
        'first=${items.first.name} discount=${items.first.discountLabel}';
  },
  'admin_fleet_member_added': (Object? body) {
    final FleetMember member = _rosterRow(body, expectedRole: FleetMemberRole.member);
    return 'added=${member.shortId} role=${member.memberRole.wire} '
        'driver=${member.driverStatus.wire} billable=${member.isBillable}';
  },
  'admin_fleet_members': (Object? body) => _roster(body),
  'fleet_members': (Object? body) => _roster(body),
  'fleet_me': (Object? body) {
    final MyFleet mine = MyFleet.fromJson(asMap(body, 'body'));
    _expect(mine.isMember, 'the fixture driver is on a roster');
    _expect(
      mine.membership?.memberRole == FleetMemberRole.member,
      'expected MEMBER, got ${mine.membership?.memberRole}',
    );
    _expect(mine.membership?.joinedAt != null, 'a roster row should carry joined_at');
    final Fleet fleet = mine.fleet!;
    _expect(fleet.memberCount == 1, 'expected one member, got ${fleet.memberCount}');
    // "25.00" out of the database must render as "25%", not "25.0%".
    _expect(fleet.discountLabel == '25%', 'expected 25%, got ${fleet.discountLabel}');
    return '${fleet.name} role=${mine.membership!.memberRole.wire} '
        'discount=${fleet.discountLabel} members=${fleet.memberCount}';
  },
  'fleet_me_none': (Object? body) {
    final MyFleet mine = MyFleet.fromJson(asMap(body, 'body'));
    // "Not in a fleet" is a normal state, not a 404 — the model must not throw
    // and the screen must branch on it.
    _expect(!mine.isMember, 'a driver on no roster should report no fleet');
    _expect(mine.fleet == null && mine.membership == null, 'both fields should be null');
    return 'fleet=null membership=null isMember=${mine.isMember}';
  },
  'fleet_detail': (Object? body) {
    _rawIsString(body, 'weekly_fee_discount_percent');
    final Fleet fleet = Fleet.fromJson(asMap(body, 'body'));
    _expect(fleet.contactName != null, 'the fixture fleet has a contact');
    return '${fleet.name} ${fleet.licenseNo} status=${fleet.status.wire} '
        'billable=${fleet.status.isBillable} contact=${fleet.contactName}';
  },
  'admin_fleet_settlement_run': (Object? body) {
    final FleetSettlementRun run = FleetSettlementRun.fromJson(asMap(body, 'body'));
    // The live run: gross is present, created_at is not (the row it upserted is
    // not read back).
    _expect(run.grossFeeHkd != null, 'the live run should report gross_fee_hkd');
    _expect(run.createdAt == null, 'the live run should not carry created_at');
    _expect(run.grossFeeHkd!.asDouble == 200, 'gross should be the platform fee, 200');
    _expect(run.feeHkd.asDouble == 150, '25% off 200 is 150, got ${run.feeHkd.canonical}');
    _expect(run.charged == 1, 'expected one member charged, got ${run.charged}');
    _expect(run.collectedHkd.asDouble == 150, 'collected should be 150');
    _expect(!run.hasAnomaly, 'a clean run must not report an anomaly');
    _expect(
      run.discountSaving!.asDouble == 50,
      'the discount should save 50, got ${run.discountSaving!.canonical}',
    );
    return 'period=${run.period} gross=${run.grossFeeHkd!.hkd} fee=${run.feeHkd.hkd} '
        'saved=${run.discountSaving!.hkd} charged=${run.charged} created=absent';
  },
  'admin_fleet_settlement': (Object? body) => _settlementHistory(body),
  'fleet_settlement': (Object? body) => _settlementHistory(body),
  'admin_settlement_fleet_managed': (Object? body) {
    final SettlementRun run = SettlementRun.fromJson(asMap(body, 'body'));
    // The billing boundary, as a fixture: the platform-wide run now excludes the
    // rostered driver and says how many it left to the fleets. Without this the
    // exclusion would be invisible, and a driver could be charged twice — the
    // two jobs write different ledger references, so idempotency does not
    // protect anyone across them.
    _expect(
      run.fleetManaged >= 1,
      'the platform run should report the excluded fleet member, got ${run.fleetManaged}',
    );
    return 'period=${run.period} eligible=${run.eligibleDrivers} charged=${run.charged} '
        'fleet_managed=${run.fleetManaged}';
  },

  // ---- websocket ----------------------------------------------------------
  'ws_driver_ack': (Object? body) {
    final TripEvent event = TripEvent.fromJson(asMap(body, 'ws'));
    _expect(event is TripAckEvent, 'expected an ack, got ${event.runtimeType}');
    final TripAckEvent ack = event as TripAckEvent;
    return 'ack lat=${ack.lat} lng=${ack.lng}';
  },
  'ws_location_tick': (Object? body) {
    final TripEvent event = TripEvent.fromJson(asMap(body, 'ws'));
    _expect(event is TripLocationEvent, 'expected a location tick, got ${event.runtimeType}');
    final TripLocationEvent tick = event as TripLocationEvent;
    // The socket sends numbers, not strings — the Decimal-as-string rule is a
    // pydantic/REST thing and does not apply here.
    _expect(tick.at != null, 'a location tick should carry a timestamp');
    return 'location lat=${tick.lat} lng=${tick.lng} ts=${tick.at}';
  },
  'ws_read_only_error': (Object? body) {
    final TripEvent event = TripEvent.fromJson(asMap(body, 'ws'));
    _expect(event is TripErrorEvent, 'expected an error frame, got ${event.runtimeType}');
    final TripErrorEvent error = event as TripErrorEvent;
    _expect(error.code == 'READ_ONLY', 'expected READ_ONLY, got ${error.code}');
    // A passenger's push is refused but must not kill the socket.
    _expect(!error.isFatal, 'READ_ONLY must not be fatal');
    return 'error code=${error.code} fatal=${error.isFatal} msg=${error.messageZh}';
  },
};

// ---------------------------------------------------------------------------
// Runner
// ---------------------------------------------------------------------------

Directory _locateFixtures() {
  final List<Directory> candidates = <Directory>[Directory('test/fixtures')];
  Directory dir = Directory.current;
  for (int i = 0; i < 4; i++) {
    candidates.add(Directory('${dir.path}/test/fixtures'));
    candidates.add(Directory('${dir.path}/mobile/test/fixtures'));
    dir = dir.parent;
  }
  for (final Directory candidate in candidates) {
    if (candidate.existsSync()) {
      return candidate;
    }
  }
  stderr.writeln('cannot find test/fixtures — run this from mobile/');
  exit(2);
}

void main() {
  final Directory fixtures = _locateFixtures();
  final Map<String, dynamic> manifest = asMap(
    jsonDecode(File('${fixtures.path}/manifest.json').readAsStringSync()),
    'manifest',
  );
  final Map<String, dynamic> sources = asMap(manifest['sources'], 'manifest.sources');

  final List<String> failures = <String>[];
  final List<String> notes = <String>[];
  int checked = 0;

  // Every fixture the generator wrote must have a decoder...
  for (final String name in sources.keys) {
    if (!_decoders.containsKey(name)) {
      failures.add('$name — no decoder in tool/verify_contract.dart');
    }
  }
  // ...and every decoder must correspond to a fixture.
  for (final String name in _decoders.keys) {
    if (!sources.containsKey(name)) {
      failures.add('$name — decoder exists but no fixture was captured');
    }
  }

  for (final MapEntry<String, Decoder> entry in _decoders.entries) {
    if (!sources.containsKey(entry.key)) {
      continue;
    }
    final File file = File('${fixtures.path}/${entry.key}.json');
    if (!file.existsSync()) {
      failures.add('${entry.key} — ${file.path} is missing');
      continue;
    }
    checked++;
    try {
      final Object? body = jsonDecode(file.readAsStringSync());
      notes.add('  ${entry.key.padRight(26)} ${entry.value(body)}');
    } catch (error) {
      failures.add('${entry.key}\n      ${sources[entry.key]}\n      $error');
    }
  }

  for (final String note in notes) {
    stdout.writeln(note);
  }
  stdout.writeln('--- $checked fixture(s) decoded, ${failures.length} failure(s) ---');
  for (final String failure in failures) {
    stdout.writeln('FAIL  $failure');
  }
  exit(failures.isEmpty ? 0 : 1);
}
