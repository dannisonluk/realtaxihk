/// Unit tests for the pure parts of the client.
///
/// **Why not `package:test` / `flutter test`?** Neither runs on this machine.
/// `flutter test` needs the Flutter tool, which cannot start (the Dart VM cannot
/// spawn piped subprocesses — Windows `ERROR_PIPE_BUSY`), and `dart test` spawns
/// its runner through `dartdev` for the same reason. See `mobile/README.md`.
///
/// So this is a self-contained harness that runs on the plain VM:
///
///     dart --packages=.dart_tool/package_config.json tool/run_tests.dart
///
/// It covers the code that has no Flutter dependency: formatting, wire decoding,
/// enums, the error envelope, websocket frames, pagination and the router
/// redirect rules. Widget tests would need `flutter test`; when that works, this
/// file is the migration list.
///
/// Exits non-zero if anything fails.
library;

// A command-line tool reports to stdout; `avoid_print` exists to keep debug
// output out of shipped app code, which this is not.
// ignore_for_file: avoid_print

import 'package:realtaxi_mobile/core/format/money.dart';
import 'package:realtaxi_mobile/core/network/api_exception.dart';
import 'package:realtaxi_mobile/core/network/wire.dart';
import 'package:realtaxi_mobile/models/auth.dart';
import 'package:realtaxi_mobile/models/admin.dart';
import 'package:realtaxi_mobile/models/driver.dart';
import 'package:realtaxi_mobile/models/enums.dart';
import 'package:realtaxi_mobile/models/ledger.dart';
import 'package:realtaxi_mobile/models/order.dart';
import 'package:realtaxi_mobile/models/refund.dart';
import 'package:realtaxi_mobile/models/trip.dart';
import 'package:realtaxi_mobile/router/routing_rules.dart';

// ---------------------------------------------------------------------------
// A very small test harness
// ---------------------------------------------------------------------------

int _passed = 0;
final List<String> _failures = <String>[];
String _suite = '';

void group(String name, void Function() body) {
  final String previous = _suite;
  _suite = previous.isEmpty ? name : '$previous › $name';
  body();
  _suite = previous;
}

void test(String name, void Function() body) {
  final String label = _suite.isEmpty ? name : '$_suite › $name';
  try {
    body();
    _passed++;
  } catch (error) {
    _failures.add('$label\n      $error');
  }
}

Never _fail(String message) => throw StateError(message);

void expect(Object? actual, Object? expected, {String? reason}) {
  if (actual != expected) {
    _fail('${reason ?? 'value'}: expected <$expected>, got <$actual>');
  }
}

void expectClose(double actual, double expected, {double epsilon = 1e-9, String? reason}) {
  if ((actual - expected).abs() > epsilon) {
    _fail('${reason ?? 'value'}: expected <$expected>, got <$actual>');
  }
}

void expectTrue(bool actual, {String? reason}) {
  if (!actual) {
    _fail(reason ?? 'expected true, got false');
  }
}

void expectFalse(bool actual, {String? reason}) {
  if (actual) {
    _fail(reason ?? 'expected false, got true');
  }
}

/// Asserts [body] throws, and that the message contains [contains] when given.
void expectThrows(void Function() body, {String? contains, String? reason}) {
  try {
    body();
  } catch (error) {
    if (contains != null && !error.toString().contains(contains)) {
      _fail('${reason ?? 'error'}: message should mention "$contains", got "$error"');
    }
    return;
  }
  _fail('${reason ?? 'call'}: expected a throw, but it returned normally');
}

/// Element-wise comparison. `expect` uses `!=`, which on a `List` is identity.
void expectList<T>(List<T> actual, List<T> expected, {String? reason}) {
  if (actual.length != expected.length) {
    _fail('${reason ?? 'list'}: expected $expected, got $actual');
  }
  for (int i = 0; i < actual.length; i++) {
    if (actual[i] != expected[i]) {
      _fail('${reason ?? 'list'}[$i]: expected <${expected[i]}>, got <${actual[i]}>');
    }
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  _moneyTests();
  _formatTests();
  _wireTests();
  _enumTests();
  _errorEnvelopeTests();
  _tripEventTests();
  _paginationTests();
  _modelTests();
  _routingTests();

  print('');
  for (final String failure in _failures) {
    print('FAIL  $failure');
  }
  print('--- $_passed passed, ${_failures.length} failed ---');
  if (_failures.isNotEmpty) {
    throw StateError('${_failures.length} test(s) failed');
  }
}

void _moneyTests() {
  group('Money', () {
    test('keeps the server string exactly', () {
      expect(Money.parse('184.5').canonical, '184.5');
      expect(Money.parse('184.5').display, '184.5');
    });

    test('handles a fee that is not quantised to 0.1', () {
      // `admin_settlement.fee_hkd` arrives as "200", not "200.0" — the one money
      // field the backend does not run through `money_str()`.
      expect(Money.parse('200').display, '200');
      expectClose(Money.parse('200').asDouble, 200);
    });

    test('drops a trailing .0 but keeps a real decimal', () {
      expect(Money.parse('12.0').display, '12');
      expect(Money.parse('12.34').display, '12.3');
    });

    test('formats with the HK\$ prefix', () {
      expect(Money.parse('184.5').hkd, r'HK$184.5');
      expect(Money.parse('184.5').fixed, '184.50');
    });

    test('signs a ledger amount', () {
      expect(Money.parse('500.0').signedHkd, r'+HK$500');
      expect(Money.parse('-50.0').signedHkd, r'-HK$50');
    });

    test('treats a missing value as zero rather than throwing', () {
      expect(Money.parse(null).display, '0');
      expectTrue(Money.parse('0').isZero);
    });

    test('reports negativity', () {
      expectTrue(Money.parse('-1').isNegative);
      expectFalse(Money.parse('1').isNegative);
    });

    test('accepts a raw number too', () {
      expectClose(Money.parse(12.5).asDouble, 12.5);
    });
  });
}

void _formatTests() {
  group('Format', () {
    test('renders km to one decimal', () {
      expect(Format.km(12.5), '12.5 km');
      expect(Format.km(6), '6.0 km');
    });

    test('renders a duration compactly', () {
      expect(Format.duration(const Duration(minutes: 45)), '45 min');
      expect(Format.duration(const Duration(minutes: 60)), '1 h');
      expect(Format.duration(const Duration(minutes: 90)), '1 h 30 min');
    });

    test('parses the server ISO format', () {
      expectTrue(Format.tryParseIso('2026-09-29T12:20:55.950955+00:00') != null);
    });

    test('returns null rather than throwing on junk', () {
      expect(Format.tryParseIso('not a date'), null);
      expect(Format.tryParseIso(null), null);
      expect(Format.tryParseIso(''), null);
      expect(Format.tryParseIso(42), null);
    });

    test('renders a placeholder for a missing timestamp', () {
      expect(Format.clock(null), '--:--');
      expect(Format.dateTime(null), '--');
    });
  });
}

void _wireTests() {
  group('wire decoders', () {
    test('accepts a Decimal-as-string', () {
      expectClose(asDouble('12.5', 'distance_km'), 12.5);
    });

    test('accepts a raw number as well', () {
      expectClose(asDouble(12.5, 'distance_km'), 12.5);
    });

    test('names the field when a double cannot be read', () {
      expectThrows(() => asDouble('abc', 'distance_km'), contains: 'distance_km');
    });

    test('reads an integer cursor', () {
      expect(asInt('16', 'next_cursor'), 16);
      expect(asInt(16.9, 'next_cursor'), 16);
      expect(asIntOrNull(null, 'next_cursor'), null);
    });

    test('separates null from absent for strings', () {
      expect(asStringOrNull(null, 'note'), null);
      expect(asStringOrNull('x', 'note'), 'x');
      expectThrows(() => asStringOrNull(7, 'note'), contains: 'note');
    });

    test('refuses a string where a bool belongs', () {
      expectThrows(() => asBool('true', 'is_fulfilled'), contains: 'is_fulfilled');
    });

    test('decodes an enum list from wire tokens', () {
      final List<Tunnel> tunnels = asEnumList(
        <Object?>['cross_harbour'],
        'tunnels',
        Tunnel.fromWire,
      );
      expect(tunnels.length, 1);
      expect(tunnels.first, Tunnel.crossHarbour);
    });

    test('names the field when an object is expected', () {
      expectThrows(() => asMap('nope', 'fare'), contains: 'fare');
    });

    test('dates tolerate null but not garbage', () {
      expect(asDateOrNull(null, 'completed_at'), null);
      expect(asDateOrNull('', 'completed_at'), null);
      expectTrue(asDateOrNull('2026-09-29T12:20:55+00:00', 'completed_at') != null);
    });
  });
}

void _enumTests() {
  group('enums', () {
    test('maps every order status', () {
      for (final OrderStatus status in OrderStatus.values) {
        expect(OrderStatus.fromWire(status.wire), status);
      }
    });

    test('throws on an unknown value rather than defaulting', () {
      // Silently mapping a new server state onto an old one would show a driver
      // the wrong screen, so this must be loud.
      expectThrows(() => OrderStatus.fromWire('TELEPORTING'), contains: 'OrderStatus');
    });

    test('knows which statuses are terminal', () {
      expectTrue(OrderStatus.completed.isTerminal);
      expectTrue(OrderStatus.cancelled.isTerminal);
      expectFalse(OrderStatus.broadcasting.isTerminal);
    });

    test('knows when a live-trip channel is meaningful', () {
      expectTrue(OrderStatus.accepted.hasDriver);
      expectTrue(OrderStatus.driverArrived.hasDriver);
      expectTrue(OrderStatus.inTrip.hasDriver);
      expectFalse(OrderStatus.broadcasting.hasDriver);
      expectFalse(OrderStatus.completed.hasDriver);
    });

    test('gates driving on ACTIVE', () {
      expectTrue(DriverStatus.active.canDrive);
      for (final DriverStatus status in DriverStatus.values) {
        if (status != DriverStatus.active) {
          expectFalse(status.canDrive, reason: status.wire);
        }
      }
    });

    test('marks only DEPOSIT_REQUIRED as needing a deposit', () {
      expectTrue(DriverStatus.depositRequired.needsDeposit);
      expectFalse(DriverStatus.pendingKyc.needsDeposit);
      expectFalse(DriverStatus.active.needsDeposit);
    });

    test('credits only top-ups and adjustments', () {
      expectTrue(LedgerEntryType.depositTopup.isCredit);
      expectTrue(LedgerEntryType.adjustment.isCredit);
      expectFalse(LedgerEntryType.weeklyFeeDeduction.isCredit);
      expectFalse(LedgerEntryType.refund.isCredit);
    });

    test('maps every tunnel', () {
      expect(Tunnel.fromWire('tai_lam'), Tunnel.taiLam);
      expect(Tunnel.fromWire('cross_harbour'), Tunnel.crossHarbour);
      expectThrows(() => Tunnel.fromWire('euro_tunnel'), contains: 'Tunnel');
    });

    test('maps every taxi type', () {
      expect(TaxiType.fromWire('URBAN'), TaxiType.urban);
      expect(TaxiType.fromWire('NT'), TaxiType.nt);
      expect(TaxiType.fromWire('LANTAU'), TaxiType.lantau);
    });

    test('carries a Chinese label for each status', () {
      expect(OrderStatus.inTrip.labelZh, '行程中');
      expect(DriverStatus.pendingKyc.labelZh, '審核中');
      expect(RefundStatus.pending.labelZh, '待審批');
    });
  });
}

void _errorEnvelopeTests() {
  group('ApiException', () {
    test('parses the server envelope', () {
      final ApiException error = ApiException.fromEnvelope(<String, dynamic>{
        'code': 'NOT_FOUND',
        'message': 'order not found',
        'details': <String, dynamic>{},
      }, statusCode: 404);
      expect(error.code, ApiException.notFound);
      expect(error.message, 'order not found');
      expect(error.statusCode, 404);
    });

    test('keeps structured details', () {
      final ApiException error = ApiException.fromEnvelope(<String, dynamic>{
        'code': 'BUSINESS_RULE_VIOLATION',
        'message': 'OTP resend cooldown active',
        'details': <String, dynamic>{'retry_after_seconds': 60},
      }, statusCode: 400);
      expect(error.details['retry_after_seconds'], 60);
    });

    test('falls back to a plain HTTP error for a non-envelope body', () {
      // A proxy's HTML error page, or an empty body.
      final ApiException error = ApiException.fromEnvelope('<html>502</html>', statusCode: 502);
      expect(error.code, ApiException.httpError);
      expect(error.statusCode, 502);
    });

    test('falls back when the envelope is missing a field', () {
      final ApiException error = ApiException.fromEnvelope(<String, dynamic>{
        'code': 'NOT_FOUND',
      }, statusCode: 404);
      expect(error.code, ApiException.httpError);
    });

    test('parses Retry-After as whole seconds', () {
      expect(ApiException.parseRetryAfter('600'), const Duration(seconds: 600));
      expect(ApiException.parseRetryAfter(' 30 '), const Duration(seconds: 30));
      expect(ApiException.parseRetryAfter('Wed, 21 Oct 2026 07:28:00 GMT'), null);
      expect(ApiException.parseRetryAfter(null), null);
    });

    test('classifies what is worth retrying', () {
      const ApiException rateLimited = ApiException(code: ApiException.rateLimited, message: 'x');
      const ApiException notFound = ApiException(code: ApiException.notFound, message: 'x');
      expectTrue(rateLimited.isTransient);
      expectFalse(notFound.isTransient);
    });

    test('classifies what means "sign in again"', () {
      const ApiException expired = ApiException(
        code: ApiException.httpError,
        message: 'x',
        statusCode: 401,
      );
      expectTrue(expired.isAuthFailure);
      const ApiException forbidden = ApiException(
        code: ApiException.forbidden,
        message: 'x',
        statusCode: 403,
      );
      expectFalse(forbidden.isAuthFailure);
    });

    test('describes itself usefully', () {
      const ApiException error = ApiException(
        code: 'NOT_FOUND',
        message: 'order not found',
        statusCode: 404,
      );
      expectTrue(error.toString().contains('NOT_FOUND'));
      expectTrue(error.toString().contains('404'));
    });
  });
}

void _tripEventTests() {
  group('TripEvent', () {
    test('decodes a location tick', () {
      final TripEvent event = TripEvent.fromJson(<String, dynamic>{
        'type': 'location',
        'lat': 22.3201,
        'lng': 114.1701,
        'ts': '2026-09-29T12:20:56.403848+00:00',
      });
      expectTrue(event is TripLocationEvent);
      expectClose((event as TripLocationEvent).lat, 22.3201);
      expectTrue(event.at != null);
    });

    test('decodes a driver ack', () {
      final TripEvent event = TripEvent.fromJson(<String, dynamic>{
        'type': 'ack',
        'lat': 22.3201,
        'lng': 114.1701,
      });
      expectTrue(event is TripAckEvent);
    });

    test('decodes a heartbeat ping', () {
      final TripEvent event = TripEvent.fromJson(<String, dynamic>{'type': 'ping'});
      expectTrue(event is TripPingEvent);
    });

    test('decodes a rejection', () {
      final TripEvent event = TripEvent.fromJson(<String, dynamic>{
        'type': 'error',
        'code': 'READ_ONLY',
      });
      expectTrue(event is TripErrorEvent);
      expect((event as TripErrorEvent).code, 'READ_ONLY');
      // A passenger's rejected push must not tear the socket down.
      expectFalse(event.isFatal);
    });

    test('treats only DRIVER_NOT_ACTIVE as fatal', () {
      for (final String code in <String>[
        'READ_ONLY',
        'RATE_LIMITED',
        'BAD_MESSAGE',
        'BAD_LOCATION',
      ]) {
        expectFalse(TripErrorEvent(code: code).isFatal, reason: code);
      }
      expectTrue(const TripErrorEvent(code: 'DRIVER_NOT_ACTIVE').isFatal);
    });

    test('has Chinese text for every code it knows', () {
      expect(const TripErrorEvent(code: 'BAD_LOCATION').messageZh, '座標不在香港範圍內');
      expect(const TripErrorEvent(code: 'DRIVER_NOT_ACTIVE').messageZh, '司機帳戶未啟用，已停止推送');
      // An unknown code still renders, naming the code.
      expectTrue(const TripErrorEvent(code: 'NEW_THING').messageZh.contains('NEW_THING'));
    });

    test('an unknown frame type does not break the socket', () {
      final TripEvent event = TripEvent.fromJson(<String, dynamic>{'type': 'telemetry'});
      expectTrue(event is TripUnknownEvent);
      expect((event as TripUnknownEvent).type, 'telemetry');
    });
  });
}

void _paginationTests() {
  group('pagination', () {
    test('infers the cursor from a full page', () {
      // `GET /orders` sends no cursor at all, so a full page is the only signal
      // that another page exists.
      final Map<String, dynamic> full = <String, dynamic>{
        'items': List<Object?>.generate(2, (int i) => _orderJson('id-$i')),
      };
      final OrderPage page = OrderPage.fromJson(full, limit: 2);
      expect(page.items.length, 2);
      expect(page.nextCursor, 'id-1');
    });

    test('stops when the page is short', () {
      final OrderPage page = OrderPage.fromJson(<String, dynamic>{
        'items': <Object?>[_orderJson('only')],
      }, limit: 20);
      expect(page.nextCursor, null);
    });

    test('stops on an empty page', () {
      final OrderPage page = OrderPage.fromJson(<String, dynamic>{'items': <Object?>[]});
      expect(page.nextCursor, null);
    });

    test('the ledger reads its cursor directly', () {
      final LedgerPage more = LedgerPage.fromJson(<String, dynamic>{
        'items': <Object?>[],
        'next_cursor': 16,
      });
      expectTrue(more.hasMore);
      final LedgerPage done = LedgerPage.fromJson(<String, dynamic>{
        'items': <Object?>[],
        'next_cursor': null,
      });
      expectFalse(done.hasMore);
    });

    test('an admin page knows when more rows remain', () {
      // A window that covers everything: 17 rows, limit 50, offset 0.
      final Paged<AdminDriverRow> all = Paged<AdminDriverRow>.fromJson(<String, dynamic>{
        'items': List<Object?>.generate(17, _adminRowJson),
        'total': 17,
        'limit': 50,
        'offset': 0,
      }, AdminDriverRow.fromJson);
      expectFalse(all.hasMore);
      // The first page of a longer list: 10 of 17 shown, 7 still to fetch.
      final Paged<AdminDriverRow> partial = Paged<AdminDriverRow>.fromJson(<String, dynamic>{
        'items': List<Object?>.generate(10, _adminRowJson),
        'total': 17,
        'limit': 10,
        'offset': 0,
      }, AdminDriverRow.fromJson);
      expectTrue(partial.hasMore);
    });
  });
}

Map<String, dynamic> _adminRowJson(int index) => <String, dynamic>{
  'id': 'cc77ad82-9137-448e-9868-6d1864b8eed$index',
  'status': 'PENDING_KYC',
  'taxi_type': 'URBAN',
  'taxi_driver_plate_no': '123456',
  'vehicle_reg_mark': 'SM1234',
};

Map<String, dynamic> _orderJson(String id) => <String, dynamic>{
  'id': id,
  'status': 'BROADCASTING',
  'taxi_type': 'URBAN',
  'estimated_total_hkd': '130.2',
  'completed_at': null,
  'created_at': '2026-09-29T12:20:55.950955+00:00',
  'fare': <String, dynamic>{
    'meter_fare': '75.2',
    'meter_after_discount': '75.2',
    'surcharges_total': '50.0',
    'tip': '5.0',
    'total_fare': '130.2',
    'tunnels': <Object?>['cross_harbour'],
    'crosses_harbour': true,
    'tariff_version': 'meter:2024-07-14;tolls:2025-09-21',
    'disclaimer_en': 'estimate',
    'disclaimer_zh': '估價',
    'surcharges': <Object?>[],
  },
};

void _modelTests() {
  group('models', () {
    test('decodes an order and its fare snapshot', () {
      final Order order = Order.fromJson(_orderJson('28cb175c-e851-430e-9800-f936a0696bcb'));
      expect(order.status, OrderStatus.broadcasting);
      expect(order.taxiType, TaxiType.urban);
      expectList(order.fare.tunnels, <Tunnel>[Tunnel.crossHarbour]);
      expectTrue(order.fare.crossesHarbour);
      expect(order.estimatedTotalHkd.hkd, r'HK$130.2');
      expect(order.completedAt, null);
      expectTrue(order.createdAt != null);
    });

    test('defaults `degraded` when the geo index is healthy', () {
      final NearbyOrders nearby = NearbyOrders.fromJson(<String, dynamic>{
        'items': <Object?>[_orderJson('a')],
      });
      expectFalse(nearby.degraded);
    });

    test('reads a degraded geo index', () {
      final NearbyOrders nearby = NearbyOrders.fromJson(<String, dynamic>{
        'items': <Object?>[],
        'degraded': true,
      });
      expectTrue(nearby.degraded);
    });

    test('sends Decimal fields as strings on the way out', () {
      // The server parses these as Decimal; sending a JSON number would work but
      // would put binary floating point in the middle of a money value.
      const OrderCreateRequest request = OrderCreateRequest(
        pickupLat: 22.3193,
        pickupLng: 114.1694,
        dropoffLat: 22.2783,
        dropoffLng: 114.1747,
        pickupAddress: 'Central',
        dropoffAddress: 'Causeway Bay',
        distanceKm: 6.4,
        taxiType: TaxiType.urban,
        tunnels: <Tunnel>[Tunnel.crossHarbour],
        crossesHarbour: true,
      );
      final Map<String, dynamic> json = request.toJson();
      expectTrue(json['distance_km'] is String, reason: 'distance_km');
      expectTrue(json['waiting_min'] is String, reason: 'waiting_min');
      expectTrue(json['tip'] is String, reason: 'tip');
      expect(json['distance_km'], '6.4');
      expect(json['taxi_type'], 'URBAN');
      expectList(json['tunnels'] as List<dynamic>, <String>['cross_harbour']);
    });

    test('an unfunded deposit reports the shortfall', () {
      final DriverDeposit deposit = DriverDeposit.fromJson(<String, dynamic>{
        'required_hkd': '500.0',
        'is_fulfilled': false,
      });
      // The profile-less substitution carries only these two keys.
      expect(deposit.balanceHkd.display, '0');
      expect(deposit.shortfall.hkd, r'HK$500');
      expectFalse(deposit.isFulfilled);
    });

    test('a funded deposit has no shortfall', () {
      final DriverDeposit deposit = DriverDeposit.fromJson(<String, dynamic>{
        'balance_hkd': '500.0',
        'held_hkd': '0.0',
        'required_hkd': '500.0',
        'is_fulfilled': true,
      });
      expect(deposit.shortfall.display, '0');
      expectTrue(deposit.isFulfilled);
    });

    test('the register response has no deposit block', () {
      final DriverProfile profile = DriverProfile.fromJson(<String, dynamic>{
        'id': 'cb6ef815-0296-48ff-9874-ece5a02478c2',
        'user_id': 'e3b3941b-5dc5-4255-ba45-bafab4d6af4c',
        'status': 'PENDING_KYC',
        'taxi_type': 'URBAN',
        'taxi_driver_plate_no': '123456',
        'vehicle_reg_mark': 'AB1234',
        'is_online': false,
      });
      expect(profile.status, DriverStatus.pendingKyc);
      expect(profile.deposit, null);
    });

    test('the driver view of a refund omits the admin fields', () {
      final RefundRequest refund = RefundRequest.fromJson(<String, dynamic>{
        'id': '56cbbe92-428b-4db5-971e-23d10acd303a',
        'amount_hkd': '500.0',
        'status': 'PENDING',
        'note': 'leaving',
        'decision_note': null,
        'decided_at': null,
        'created_at': '2026-09-29T12:20:56.279536+00:00',
      });
      expect(refund.status, RefundStatus.pending);
      expect(refund.driverProfileId, null);
      expect(refund.decidedBy, null);
    });

    test('`{"refund": null}` means no request was ever filed', () {
      expect(MyRefund.fromJson(<String, dynamic>{'refund': null}).refund, null);
    });

    test('a grant reference is namespaced by the server', () {
      final DepositGrantResult grant = DepositGrantResult.fromJson(<String, dynamic>{
        'id': 'cb6ef815-0296-48ff-9874-ece5a02478c2',
        'driver_status': 'ACTIVE',
        'balance_hkd': '500.0',
        'is_fulfilled': true,
        'reference': 'grant:cb6ef815:fixture-1',
      });
      expect(grant.driverStatus, DriverStatus.active);
      expectTrue(grant.reference.startsWith('grant:'));
    });

    test('a settlement run flags an anomaly only on failure or tampering', () {
      SettlementRun run(int failed, int tampered) => SettlementRun.fromJson(<String, dynamic>{
        'period': '2026-W40',
        'fee_hkd': '200',
        'eligible_drivers': 8,
        'charged': 1,
        'skipped': 7,
        'failed': failed,
        'tampered': tampered,
      });
      expectFalse(run(0, 0).hasAnomaly);
      expectTrue(run(1, 0).hasAnomaly);
      expectTrue(run(0, 1).hasAnomaly);
      expect(run(0, 0).feeHkd.display, '200');
    });

    test('a trip snapshot has no fix until the driver ticks', () {
      final TripLocationSnapshot snapshot = TripLocationSnapshot.fromJson(<String, dynamic>{
        'order_id': '28cb175c-e851-430e-9800-f936a0696bcb',
        'status': 'BROADCASTING',
        'driver_profile_id': null,
        'lat': null,
        'lng': null,
      });
      expectFalse(snapshot.hasFix);
      expect(snapshot.driverProfileId, null);
    });

    test('a trip snapshot reads a fix when there is one', () {
      final TripLocationSnapshot snapshot = TripLocationSnapshot.fromJson(<String, dynamic>{
        'order_id': '28cb175c-e851-430e-9800-f936a0696bcb',
        'status': 'IN_TRIP',
        'driver_profile_id': 'cb6ef815-0296-48ff-9874-ece5a02478c2',
        'lat': 22.3201,
        'lng': 114.1701,
      });
      expectTrue(snapshot.hasFix);
      expectClose(snapshot.lat!, 22.3201);
    });

    test('the account cache round-trips through JSON', () {
      const AppUser user = AppUser(
        id: '0a373307-4767-4485-9111-2e80f5046bf4',
        phoneMasked: '+852****0002',
        role: UserRole.passenger,
      );
      final AppUser restored = AppUser.fromJson(user.toJson());
      expect(restored.id, user.id);
      expect(restored.role, user.role);
      expect(restored.phoneMasked, user.phoneMasked);
    });

    test('reads the `created` flag only where the server sends it', () {
      expectTrue(AuthSession.createdFromJson(<String, dynamic>{'created': true}));
      // `/auth/refresh` omits it entirely.
      expectFalse(AuthSession.createdFromJson(<String, dynamic>{}));
    });
  });
}

void _routingTests() {
  const AppUser passenger = AppUser(id: 'p', phoneMasked: '+852****0002', role: UserRole.passenger);
  const AppUser admin = AppUser(id: 'a', phoneMasked: '+852****0001', role: UserRole.admin);

  String? go({
    required String location,
    bool restoring = false,
    bool hasError = false,
    AppUser? user,
  }) => resolveRedirect(location: location, restoring: restoring, hasError: hasError, user: user);

  group('routing rules', () {
    test('holds on the splash while the stored session is re-validated', () {
      expect(go(location: Routes.request, restoring: true), Routes.splash);
      // Already there — stay, so the splash is not pushed repeatedly.
      expect(go(location: Routes.splash, restoring: true), null);
    });

    test('sends an anonymous visitor to login', () {
      expect(go(location: Routes.request), Routes.login);
      expect(go(location: Routes.login), null);
      expect(go(location: Routes.otp), null);
    });

    test('a restore failure is not a sign-out', () {
      // Offline: the splash owns the retry, and the tokens are still good.
      expect(go(location: Routes.request, hasError: true), Routes.splash);
      expect(go(location: Routes.splash, hasError: true), null);
      expect(go(location: Routes.login, hasError: true), Routes.splash);
    });

    test('sends a passenger home from a pre-auth route', () {
      expect(go(location: Routes.splash, user: passenger), Routes.request);
      expect(go(location: Routes.login, user: passenger), Routes.request);
    });

    test('sends an admin to the console, not the passenger app', () {
      expect(go(location: Routes.splash, user: admin), Routes.adminKyc);
      expect(go(location: Routes.request, user: admin), Routes.adminKyc);
      expect(go(location: Routes.trackTrip, user: admin), Routes.adminKyc);
    });

    test('keeps an admin inside the console', () {
      expect(go(location: Routes.adminKyc, user: admin), null);
      expect(go(location: Routes.adminRefunds, user: admin), null);
      expect(go(location: Routes.adminSettlement, user: admin), null);
    });

    test('keeps a passenger out of the console', () {
      // The admin endpoints would 403 on a missing ADMIN row, so the console is
      // not merely undesirable for a passenger — it cannot work.
      expect(go(location: Routes.adminKyc, user: passenger), Routes.request);
      expect(go(location: Routes.adminRefunds, user: passenger), Routes.request);
    });

    test('keeps a passenger inside the passenger app', () {
      expect(go(location: Routes.request, user: passenger), null);
      expect(go(location: Routes.trips, user: passenger), null);
      expect(go(location: '${Routes.trackTrip}/abc', user: passenger), null);
    });

    test('a driver-role account is routed as a passenger', () {
      // `UserRole.DRIVER` is defined but never assigned by any backend code path,
      // so this only guards against the client inventing a driver shell.
      const AppUser driver = AppUser(id: 'd', phoneMasked: '+852****0003', role: UserRole.driver);
      expect(homeRouteFor(driver), Routes.request);
      expect(go(location: Routes.adminKyc, user: driver), Routes.request);
    });

    test('lets a driver-mode account reach the driver surfaces', () {
      // Driver mode is entered from the account screen, so those paths must not
      // be redirected away for a passenger account.
      expect(go(location: Routes.driverJobs, user: passenger), null);
      expect(go(location: Routes.driverOnboarding, user: passenger), null);
      expect(go(location: '${Routes.driverActiveTrip}/abc', user: passenger), null);
    });
  });
}
