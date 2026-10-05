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

import 'package:hkfastdc_mobile/core/format/money.dart';
// The *protocol* half of the Turnstile seam, not `turnstile.dart`: that file
// imports `package:flutter` for `WebViewWidget` and `kReleaseMode`, and this
// script runs on the plain VM.
import 'package:hkfastdc_mobile/core/human/turnstile_protocol.dart';
import 'package:hkfastdc_mobile/core/network/api_exception.dart';
import 'package:hkfastdc_mobile/core/network/wire.dart';
import 'package:hkfastdc_mobile/core/phone.dart';
import 'package:hkfastdc_mobile/core/security/password_policy.dart';
import 'package:hkfastdc_mobile/core/security/username_policy.dart';
import 'package:hkfastdc_mobile/models/auth.dart';
import 'package:hkfastdc_mobile/models/admin.dart';
import 'package:hkfastdc_mobile/models/driver.dart';
import 'package:hkfastdc_mobile/models/enums.dart';
import 'package:hkfastdc_mobile/models/fleet.dart';
import 'package:hkfastdc_mobile/models/identity.dart';
import 'package:hkfastdc_mobile/models/ledger.dart';
import 'package:hkfastdc_mobile/models/order.dart';
import 'package:hkfastdc_mobile/models/refund.dart';
import 'package:hkfastdc_mobile/models/trip.dart';
import 'package:hkfastdc_mobile/router/routing_rules.dart';

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
  _fleetTests();
  _routingTests();
  _credentialTests();
  _humanVerificationTests();
  _identityTests();

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

    test('drops a trailing .0 but keeps real cents', () {
      expect(Money.parse('12.0').display, '12');
      expect(Money.parse('12.00').display, '12');
      // Stored money is Numeric(10,2) server-side, so a cent is a real value and
      // must survive rendering. This used to assert '12.3' — the truncation that
      // showed a 0.05 deposit as HK$0.1.
      expect(Money.parse('12.34').display, '12.34');
      expect(Money.parse('0.05').display, '0.05');
      expect(Money.parse('0.04').display, '0.04');
      expect(Money.parse('0.01').display, '0.01');
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

    test('subtracts in cents, not in binary floating point', () {
      // The defect this guards: `500.00 - 499.70` is 0.30000000000001137 as a
      // double, and `canonical` faithfully preserved it, so the driver's deposit
      // card rendered `HK$0.30000000000001137`. Confirmed by running the old
      // expression before changing it, not by reasoning about floats.
      expect(Money.parse('500.00').minus(Money.parse('499.70')).canonical, '0.30');
      expect(Money.parse('500.00').minus(Money.parse('499.70')).display, '0.3');
      expect(Money.parse('500.00').minus(Money.parse('487.30')).canonical, '12.70');
      expect(Money.parse('200.00').minus(Money.parse('199.99')).canonical, '0.01');
      // A 1 dp meter figure scales correctly: 147.1 is 14710 cents, not 147.1
      // rounded through a double.
      expect(Money.parse('147.1').minus(Money.parse('100.0')).canonical, '47.10');
    });

    test('subtracting past zero goes negative, and the caller clamps', () {
      expect(Money.parse('100.00').minus(Money.parse('250.00')).canonical, '-150.00');
      expectTrue(Money.parse('100.00').minus(Money.parse('250.00')).isNegative);
    });

    test('renders a cent count back to the server 2 dp shape', () {
      expect(Money.fromCents(30).canonical, '0.30');
      expect(Money.fromCents(5).canonical, '0.05');
      expect(Money.fromCents(0).canonical, '0.00');
      expect(Money.fromCents(-50).canonical, '-0.50');
      expect(Money.fromCents(12345).canonical, '123.45');
    });

    test('a driver who has overpaid has no shortfall', () {
      const DriverDeposit overpaid = DriverDeposit(
        balanceHkd: Money('600.00'),
        heldHkd: Money('0.00'),
        requiredHkd: Money('500.00'),
        isFulfilled: true,
      );
      expect(overpaid.shortfall.display, '0');

      const DriverDeposit shortByThirtyCents = DriverDeposit(
        balanceHkd: Money('499.70'),
        heldHkd: Money('0.00'),
        requiredHkd: Money('500.00'),
        isFulfilled: false,
      );
      expect(shortByThirtyCents.shortfall.hkd, r'HK$0.3');
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

    test('adjustments are signed, so direction is read off the amount', () {
      // The enum used to assert `adjustment.isCredit == true`, which is only
      // true for half of them: a correction may debit. Pin the rule that
      // replaced it — never infer direction from the entry type.
      expect(LedgerEntryType.fromWire('ADJUSTMENT'), LedgerEntryType.adjustment);
      expect(LedgerEntryType.adjustment.labelZh, '調整');
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

    test('treats DRIVER_NOT_ACTIVE and OUTSIDE_HK as fatal', () {
      for (final String code in <String>[
        'READ_ONLY',
        'RATE_LIMITED',
        'BAD_MESSAGE',
        'BAD_LOCATION',
      ]) {
        expectFalse(TripErrorEvent(code: code).isFatal, reason: code);
      }
      expectTrue(const TripErrorEvent(code: 'DRIVER_NOT_ACTIVE').isFatal);
      expectTrue(const TripErrorEvent(code: 'OUTSIDE_HK').isFatal);
    });

    test('has Chinese text for every code it knows', () {
      expect(const TripErrorEvent(code: 'OUTSIDE_HK').messageZh, '座標不在香港範圍內');
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

    test('a full page is not exhausted: the cursor equals the last id by design', () {
      // Regression for the history controller: `page.nextCursor == merged.last.id`
      // used to be read as "no progress", but a full page's cursor IS the last id
      // on the merged list, so that check made every full page the final page.
      final Map<String, dynamic> full = <String, dynamic>{
        'items': List<Object?>.generate(2, (int i) => _orderJson('id-$i')),
      };
      final OrderPage page = OrderPage.fromJson(full, limit: 2);
      expect(page.items.length, 2);
      expect(page.nextCursor, 'id-1');
      expect(page.nextCursor, page.items.last.id, reason: 'the inferred cursor is the last id');
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
    // Both strings on the wire: `discount_percent` is a Decimal, `is_estimate`
    // is stamped true by `order_service.fare_snapshot`.
    'discount_percent': '0',
    'is_estimate': true,
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

    test('carries the estimate flag and discount the receipt renders', () {
      // `is_estimate` drives the Cap. 374D "估價" badge on the detail screen and
      // `discount_percent` decides whether the receipt shows the original meter
      // fare alongside the discounted one. Both are on the wire; neither is
      // optional, so a server that stopped sending one must fail here rather
      // than render an unlabelled quote.
      final Order order = Order.fromJson(_orderJson('with-discount'));
      expectTrue(order.fare.isEstimate, reason: 'is_estimate');
      expect(order.fare.discountPercent, 0);
      expectFalse(order.fare.hasDiscount, reason: 'no discount -> hide the line');

      // A discounted order: the discounted meter differs from the original.
      final Map<String, dynamic> discounted = _orderJson('discounted');
      (discounted['fare']! as Map<String, dynamic>)['discount_percent'] = '20';
      (discounted['fare']! as Map<String, dynamic>)['meter_after_discount'] = '60.2';
      final Order withDiscount = Order.fromJson(discounted);
      expect(withDiscount.fare.discountPercent, 20);
      expectTrue(withDiscount.fare.hasDiscount);
      expect(withDiscount.fare.meterFare.hkd, r'HK$75.2');
      expect(withDiscount.fare.meterAfterDiscount.hkd, r'HK$60.2');
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
      SettlementRun run(int failed, int tampered, {int fleetManaged = 0}) =>
          SettlementRun.fromJson(<String, dynamic>{
            'period': '2026-W40',
            'fee_hkd': '200',
            'eligible_drivers': 8,
            'fleet_managed': fleetManaged,
            'charged': 1,
            'skipped': 7,
            'failed': failed,
            'tampered': tampered,
          });
      expectFalse(run(0, 0).hasAnomaly);
      expectTrue(run(1, 0).hasAnomaly);
      expectTrue(run(0, 1).hasAnomaly);
      expect(run(0, 0).feeHkd.display, '200');
      // `fleet_managed` is required, not defaulted: the server always sends it,
      // and defaulting to 0 would render "no fleet members were excluded" for a
      // response that simply omitted the field.
      expectThrows(
        () => SettlementRun.fromJson(<String, dynamic>{
          'period': '2026-W40',
          'fee_hkd': '200',
          'eligible_drivers': 8,
          'charged': 1,
          'skipped': 7,
          'failed': 0,
          'tampered': 0,
        }),
        contains: 'fleet_managed',
      );
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

Map<String, dynamic> _fleetJson({
  String discount = '25.00',
  String status = 'ACTIVE',
  int? memberCount = 1,
}) => <String, dynamic>{
  'id': '8f05f4a9-3fbd-4694-95f7-99cf34935465',
  'name': '星群的士',
  'license_no': 'FLEET-STAR-001',
  'status': status,
  'weekly_fee_discount_percent': discount,
  'contact_name': '陳先生',
  'contact_phone': '+85222334455',
  'note': null,
  'created_at': '2026-09-29T13:04:19.004241+00:00',
  'member_count': ?memberCount,
};

Map<String, dynamic> _memberJson({
  String memberStatus = 'ACTIVE',
  String driverStatus = 'ACTIVE',
  String role = 'MEMBER',
  String id = 'bda4c5be-f2f7-4f8b-b9d4-f0171d6635a8',
}) => <String, dynamic>{
  'driver_profile_id': id,
  'taxi_type': 'URBAN',
  'driver_status': driverStatus,
  'member_role': role,
  'status': memberStatus,
  'joined_at': '2026-09-29T13:04:19.046048+00:00',
  'left_at': memberStatus == 'REMOVED' ? '2026-10-06T13:04:19.046048+00:00' : null,
};

void _fleetTests() {
  group('fleets', () {
    test('renders the discount without inventing precision', () {
      // The create route echoes the request ("25"), the read routes come back
      // from a Numeric column ("25.00"). Both must read as 25%.
      expect(Fleet.fromJson(_fleetJson(discount: '25')).discountLabel, '25%');
      expect(Fleet.fromJson(_fleetJson(discount: '25.00')).discountLabel, '25%');
      expect(Fleet.fromJson(_fleetJson(discount: '0')).discountLabel, '0%');
      // A genuine half percent keeps its digit.
      expect(Fleet.fromJson(_fleetJson(discount: '12.50')).discountLabel, '12.5%');
      expect(Fleet.fromJson(_fleetJson(discount: '33.33')).discountLabel, '33.3%');
    });

    test('a 100% discount is a full discount, not a parse failure', () {
      final Fleet free = Fleet.fromJson(_fleetJson(discount: '100'));
      expectTrue(free.isFullyDiscounted);
      expectFalse(Fleet.fromJson(_fleetJson(discount: '99.99')).isFullyDiscounted);
    });

    test('only an ACTIVE fleet is billable', () {
      expectTrue(FleetStatus.active.isBillable);
      expectFalse(FleetStatus.suspended.isBillable);
      expectFalse(FleetStatus.dissolved.isBillable);
      // A suspended operator is not dispatching, so it is not billing.
      final Fleet suspended = Fleet.fromJson(_fleetJson(status: 'SUSPENDED'));
      expect(suspended.status, FleetStatus.suspended);
      expectFalse(suspended.status.isBillable);
    });

    test('an unknown fleet status fails loudly', () {
      expectThrows(
        () => FleetStatus.fromWire('ARCHIVED'),
        contains: 'FleetStatus',
        reason: 'a new server state must not be silently mapped',
      );
    });

    test('member_count is optional, because not every route sends it', () {
      final Fleet without = Fleet.fromJson(_fleetJson(memberCount: null));
      expect(without.memberCount, null);
      expect(Fleet.fromJson(_fleetJson(memberCount: 3)).memberCount, 3);
    });

    test('a driver on no roster is data, not an error', () {
      final MyFleet none = MyFleet.fromJson(<String, dynamic>{'fleet': null, 'membership': null});
      expectFalse(none.isMember);
      expect(none.fleet, null);
      expect(none.membership, null);
    });

    test('a member reads their fleet and role', () {
      final MyFleet mine = MyFleet.fromJson(<String, dynamic>{
        'fleet': _fleetJson(),
        'membership': <String, dynamic>{
          'member_role': 'MANAGER',
          'joined_at': '2026-09-29T13:04:19.046048+00:00',
        },
      });
      expectTrue(mine.isMember);
      expect(mine.fleet!.name, '星群的士');
      expect(mine.membership!.memberRole, FleetMemberRole.manager);
      expectTrue(mine.membership!.memberRole.isAdmin);
      expectFalse(FleetMemberRole.member.isAdmin);
      expectTrue(mine.membership!.joinedAt != null);
    });

    test('a member is billable only when both statuses are ACTIVE', () {
      // Rostered and funded.
      expectTrue(FleetMember.fromJson(_memberJson()).isBillable);
      // Rostered but still in KYC — no deposit account to debit.
      expectFalse(FleetMember.fromJson(_memberJson(driverStatus: 'PENDING_KYC')).isBillable);
      // Suspended driver.
      expectFalse(FleetMember.fromJson(_memberJson(driverStatus: 'SUSPENDED')).isBillable);
      // Taken off the roster.
      final FleetMember left = FleetMember.fromJson(_memberJson(memberStatus: 'REMOVED'));
      expectFalse(left.isBillable);
      expect(left.status, FleetMemberStatus.removed);
      expectTrue(left.leftAt != null);
    });

    test('shortId tails the profile id, and survives a short one', () {
      final FleetMember member = FleetMember.fromJson(_memberJson());
      expect(member.shortId, '…1d6635a8');
      final FleetMember tiny = FleetMember.fromJson(_memberJson(id: 'abc123'));
      expect(tiny.shortId, 'abc123');
    });

    test('a live run carries the gross fee and no timestamp', () {
      final FleetSettlementRun run = FleetSettlementRun.fromJson(<String, dynamic>{
        'fleet_id': '8f05f4a9-3fbd-4694-95f7-99cf34935465',
        'fleet_name': '星群的士',
        'period': '2026-W45',
        'gross_fee_hkd': '200',
        'discount_percent': '25.00',
        'fee_hkd': '150.00',
        'member_count': 1,
        'charged': 1,
        'skipped': 0,
        'failed': 0,
        'tampered': 0,
        'collected_hkd': '150.00',
      });
      expect(run.grossFeeHkd!.canonical, '200');
      expect(run.feeHkd.canonical, '150.00');
      expect(run.createdAt, null);
      // Derived through `Money.minus`, so it lands in the same 2 dp shape as the
      // stored money it is computed from. It used to read '50.0', which was the
      // double subtraction's own string form rather than the money convention —
      // the same path that produced `HK$0.30000000000001137` for a 30-cent gap.
      expect(run.discountSaving!.canonical, '50.00');
      expectFalse(run.hasAnomaly);
    });

    test('a stored row has a timestamp and no gross fee', () {
      final FleetSettlementRun run = FleetSettlementRun.fromJson(<String, dynamic>{
        'period': '2026-W45',
        'fee_hkd': '150.00',
        'discount_percent': '25.00',
        'member_count': 1,
        'charged': 1,
        'skipped': 0,
        'failed': 0,
        'tampered': 0,
        'collected_hkd': '150.00',
        'created_at': '2026-09-29T13:04:19.161114+00:00',
      });
      expect(run.grossFeeHkd, null);
      // The saving is underivable without the gross, so the UI must hide the
      // line rather than print HK$0.
      expect(run.discountSaving, null);
      expectTrue(run.createdAt != null);
    });

    test('a tampered or failed run is flagged', () {
      FleetSettlementRun run({int tampered = 0, int failed = 0}) =>
          FleetSettlementRun.fromJson(<String, dynamic>{
            'period': '2026-W45',
            'fee_hkd': '150.00',
            'discount_percent': '25.00',
            'member_count': 4,
            'charged': 2,
            'skipped': 0,
            'failed': failed,
            'tampered': tampered,
            'collected_hkd': '300.00',
          });
      expectFalse(run().hasAnomaly);
      expectTrue(run(tampered: 1).hasAnomaly);
      expectTrue(run(failed: 1).hasAnomaly);
    });

    test('a zero-collection week reports 0.00, not 0', () {
      // The server quantises `collected_hkd` to the cent so a fully discounted
      // week does not render differently from a partial one.
      final FleetSettlementRun run = FleetSettlementRun.fromJson(<String, dynamic>{
        'period': '2026-W45',
        'fee_hkd': '0.00',
        'discount_percent': '100.00',
        'member_count': 2,
        'charged': 0,
        'skipped': 2,
        'failed': 0,
        'tampered': 0,
        'collected_hkd': '0.00',
      });
      expect(run.collectedHkd.fixed, '0.00');
      expect(run.feeHkd.fixed, '0.00');
    });

    test('the `{items}` wrapper decodes a roster and a history', () {
      final List<FleetMember> roster = FleetList<FleetMember>.fromJson(<String, dynamic>{
        'items': <Object?>[_memberJson(), _memberJson(role: 'OWNER')],
      }, FleetMember.fromJson).items;
      expect(roster.length, 2);
      expect(roster[1].memberRole, FleetMemberRole.owner);
      // Neither route pages, so a bare `{items}` is the whole contract.
      final List<FleetSettlementRun> history = FleetList<FleetSettlementRun>.fromJson(
        <String, dynamic>{
          'items': <Object?>[
            <String, dynamic>{
              'period': '2026-W45',
              'fee_hkd': '150.00',
              'discount_percent': '25.00',
              'member_count': 1,
              'charged': 1,
              'skipped': 0,
              'failed': 0,
              'tampered': 0,
              'collected_hkd': '150.00',
            },
          ],
        },
        FleetSettlementRun.fromJson,
      ).items;
      expect(history.length, 1);
    });

    test('the platform run reports the drivers it left to the fleets', () {
      final SettlementRun run = SettlementRun.fromJson(<String, dynamic>{
        'period': '2026-W46',
        'fee_hkd': '200',
        'eligible_drivers': 7,
        'fleet_managed': 1,
        'charged': 1,
        'skipped': 6,
        'failed': 0,
        'tampered': 0,
      });
      expect(run.fleetManaged, 1);
      expect(run.eligibleDrivers, 7);
      // A run with no fleet members must still parse the counter as zero.
      final SettlementRun plain = SettlementRun.fromJson(<String, dynamic>{
        'period': '2026-W40',
        'fee_hkd': '200',
        'eligible_drivers': 8,
        'fleet_managed': 0,
        'charged': 1,
        'skipped': 7,
        'failed': 0,
        'tampered': 0,
      });
      expect(plain.fleetManaged, 0);
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

    test('the phone unlock is not a pre-auth route', () {
      // It must stay reachable for a **signed-in** account: the accounts that
      // need it are exactly the ones that already have a session, and every
      // `/login` path is redirected away from a signed-in user.
      expectFalse(Routes.phoneUnlock.startsWith(Routes.login));
      expect(go(location: Routes.phoneUnlock, user: passenger), null);
      expect(go(location: Routes.phoneUnlock), Routes.login, reason: 'still needs a session');
      // The new pre-auth doors stay pre-auth.
      expect(go(location: Routes.register), null);
      expect(go(location: Routes.phoneLogin), null);
      expect(go(location: Routes.register, user: passenger), Routes.request);
    });

    test('the profile form is reachable, and is not a gate', () {
      // Same shape as the unlock above: outside `/login`, so a signed-in account
      // can reach it, and still requiring a session.
      expectFalse(Routes.profileSetup.startsWith(Routes.login));
      expect(go(location: Routes.profileSetup, user: passenger), null);
      expect(go(location: Routes.profileSetup), Routes.login, reason: 'still needs a session');
      // And no rule sends anyone *to* it. The server never refuses anything for
      // an incomplete profile — `account_status` is a completeness flag — so a
      // client-side redirect here would be stricter than the API. If this ever
      // starts failing, someone has turned the offer into a gate.
      for (final String location in <String>[
        Routes.request,
        Routes.trips,
        Routes.passengerAccount,
        Routes.driverJobs,
      ]) {
        expect(
          go(location: location, user: passenger),
          null,
          reason: location,
        );
      }
      // An admin account has no `users` row to complete a profile on, so the
      // console keeps it out of the passenger app entirely.
      expect(go(location: Routes.profileSetup, user: admin), Routes.adminKyc);
    });
  });
}

// ---------------------------------------------------------------------------
// The auth split: credentials, the phone claim, and the human-verification seam
// ---------------------------------------------------------------------------

void _credentialTests() {
  group('password policy', () {
    test('mirrors the server minimum of 12 characters', () {
      expect(minPasswordLength, 12);
      expect(passwordProblem('abcdefghijk'), '密碼至少需要 12 個字元。');
      expect(passwordProblem('hkfastdc!2026'), null);
      // Twelve characters is not enough on its own — the server also rejects
      // `abcdefghijkl` outright, so the mirror must.
      expect(passwordProblem('abcdefghijkl'), '密碼過於容易被猜到，請避免常見字詞或連續數字。');
    });

    test('counts code points, not UTF-16 units', () {
      expect('👍👍👍'.length, 6, reason: 'UTF-16 units');
      expect('👍👍👍'.runes.length, 3, reason: 'code points, which is what Python counts');
      expect(passwordProblem('👍👍👍'), '密碼至少需要 12 個字元。');
      // Twelve identical code points is a repeated-character password, and the
      // server refuses it — so this must too.
      expect(passwordProblem('👍👍👍👍👍👍👍👍👍👍👍👍'), '密碼過於容易被猜到，請避免常見字詞或連續數字。');
      // The case `split('')` got wrong: four distinct UTF-16 units, but only
      // three distinct code points, so the server calls it weak and the old
      // mirror would have let it through.
      expect(passwordProblem('ab👍👍👍👍👍👍👍👍👍👍'), '密碼過於容易被猜到，請避免常見字詞或連續數字。');
    });

    test('refuses leading or trailing whitespace', () {
      expect(passwordProblem(' abcdefghijkl'), '密碼不可有開頭或結尾的空白。');
      expect(passwordProblem('abcdefghijkl '), '密碼不可有開頭或結尾的空白。');
    });

    test('refuses the weak fragments the server refuses', () {
      for (final String weak in <String>[
        'mypassword12',
        'qwertyuiop12',
        'Realtaxi12345',
        'letmein12345',
      ]) {
        expect(passwordProblem(weak), '密碼過於容易被猜到，請避免常見字詞或連續數字。', reason: weak);
      }
    });

    test('refuses a repeated or sequential string', () {
      expect(passwordProblem('aaaaaaaaaaaa'), '密碼過於容易被猜到，請避免常見字詞或連續數字。');
      expect(passwordProblem('0123456789ab'), '密碼過於容易被猜到，請避免常見字詞或連續數字。');
      // Twelve distinct characters with no fragment is accepted.
      expect(passwordProblem('xz9Kp2Mv7Qr4'), null);
    });

    test('reports the first problem to fix, not a checklist', () {
      // Too short *and* weak: the length message wins, because it is the first
      // thing to fix.
      expect(passwordProblem('password'), '密碼至少需要 12 個字元。');
    });
  });

  group('username policy', () {
    test('accepts what the server accepts', () {
      // `^[a-z0-9][a-z0-9._-]{2,31}$` — one leading alphanumeric, then two to
      // thirty-one more. The shortest legal handle is three characters.
      expect(usernameProblem('abc'), null);
      expect(usernameProblem('kaming.chan'), null);
      expect(usernameProblem('a_b-c9'), null);
      expect(usernameProblem(List<String>.filled(32, 'a').join()), null);
      expect(usernameProblem(List<String>.filled(33, 'a').join()), '使用者名稱最多 32 個字元。');
    });

    test('normalises before judging, because the server does', () {
      // `normalize_username` lower-cases and trims *before* `_assert_username`,
      // so `KaMing` reaches the regex as `kaming` and is accepted. Rejecting the
      // raw text would refuse a handle the API is happy with.
      expect(normalizeUsername('  KaMing  '), 'kaming');
      expect(usernameProblem('  KaMing  '), null);
      // And the normalised form is what the reserved list is checked against,
      // so an upper-cased reserved word is still reserved.
      expect(usernameProblem('ADMIN'), '此使用者名稱已被保留，請改用其他名稱。');
    });

    test('refuses the reserved handles', () {
      for (final String reserved in <String>[
        'admin',
        'root',
        'support',
        'system',
        'realtaxi',
        'null',
        'undefined',
      ]) {
        expect(usernameProblem(reserved), '此使用者名稱已被保留，請改用其他名稱。', reason: reserved);
      }
      // `me` is in the server's reserved set too, but it is two characters and
      // `_assert_username` tests the regex **before** the reserved list — so the
      // reserved branch is unreachable for it. Asserting the reserved message
      // here would pin behaviour the server never has.
      expect(usernameProblem('me'), '使用者名稱至少需要 3 個字元。');
      // A handle that merely contains a reserved word is fine — the server tests
      // the whole string, not a substring.
      expect(usernameProblem('admin2'), null);
      expect(usernameProblem('meme'), null);
    });

    test('refuses the shapes the regex refuses', () {
      expect(usernameProblem(''), '請輸入使用者名稱。');
      expect(usernameProblem('   '), '請輸入使用者名稱。');
      expect(usernameProblem('ab'), '使用者名稱至少需要 3 個字元。');
      // Must start with a letter or digit: no leading dot, underscore or hyphen.
      expect(usernameProblem('.abc'), '使用者名稱只可用小寫英文字母、數字、點、底線或連字號，並以字母或數字開頭。');
      expect(usernameProblem('-abc'), '使用者名稱只可用小寫英文字母、數字、點、底線或連字號，並以字母或數字開頭。');
      // No spaces, and no characters outside the allowed set.
      expect(usernameProblem('ka ming'), '使用者名稱只可用小寫英文字母、數字、點、底線或連字號，並以字母或數字開頭。');
      expect(usernameProblem('kaming!'), '使用者名稱只可用小寫英文字母、數字、點、底線或連字號，並以字母或數字開頭。');
      expect(usernameProblem('陳大文'), '使用者名稱只可用小寫英文字母、數字、點、底線或連字號，並以字母或數字開頭。');
    });

    test('reports the first problem to fix, not a checklist', () {
      // Too short *and* reserved: the length message wins, matching the order
      // `_assert_username` checks in.
      expect(usernameProblem('me'), '使用者名稱至少需要 3 個字元。');
    });
  });

  group('HK phone numbers', () {
    test('builds E.164 from the eight digits the field can hold', () {
      expect(hkPhoneToE164('91234567'), '+85291234567');
      expect(hkPhoneToE164(' 91234567 '), '+85291234567');
    });

    test('refuses anything that is not eight digits', () {
      expect(hkPhoneToE164('9123456'), null);
      expect(hkPhoneToE164('912345678'), null);
      expect(hkPhoneToE164(''), null);
      expect(hkPhoneToE164('+85291234567'), null, reason: 'the field holds digits only');
    });

    test('strips the country code back off for the field', () {
      expect(hkPhoneDigits('+85291234567'), '91234567');
      expect(hkPhoneDigits('91234567'), '91234567');
    });
  });
}

void _humanVerificationTests() {
  group('Turnstile build mode', () {
    test('a present site key always renders the widget', () {
      expect(resolveTurnstileMode(siteKeyPresent: true, releaseMode: false), TurnstileMode.enabled);
      expect(resolveTurnstileMode(siteKeyPresent: true, releaseMode: true), TurnstileMode.enabled);
    });

    test('an absent key is tolerated in dev and refused in release', () {
      // The asymmetry is the point: development must not need a Cloudflare
      // account, and a release must not ship a sign-in form that cannot work.
      expect(
        resolveTurnstileMode(siteKeyPresent: false, releaseMode: false),
        TurnstileMode.disabled,
      );
      expect(
        resolveTurnstileMode(siteKeyPresent: false, releaseMode: true),
        TurnstileMode.misconfigured,
      );
    });
  });

  group('Turnstile widget protocol', () {
    test('decodes a token', () {
      final TurnstileSignal? signal = parseTurnstileSignal('{"event":"token","token":"0.abc"}');
      expectTrue(signal is TurnstileToken);
      expect((signal! as TurnstileToken).token, '0.abc');
    });

    test('treats expired and timeout as "the token is spent"', () {
      expectTrue(parseTurnstileSignal('{"event":"expired"}') is TurnstileStale);
      expectTrue(parseTurnstileSignal('{"event":"timeout"}') is TurnstileStale);
    });

    test("keeps Cloudflare's error code verbatim", () {
      final TurnstileSignal? signal = parseTurnstileSignal('{"event":"error","code":"110200"}');
      expectTrue(signal is TurnstileError);
      expect((signal! as TurnstileError).code, '110200');
      // An error event with no code is still an error, not a crash.
      expect((parseTurnstileSignal('{"event":"error"}')! as TurnstileError).code, 'unknown');
    });

    test('an unknown event is data, not a failure', () {
      final TurnstileSignal? signal = parseTurnstileSignal('{"event":"something_new"}');
      expectTrue(signal is TurnstileUnknown);
      expect((signal! as TurnstileUnknown).event, 'something_new');
    });

    test('noise is ignored rather than fatal', () {
      // A widget that emitted rubbish must not be able to break a sign-in.
      expect(parseTurnstileSignal('not json'), null);
      expect(parseTurnstileSignal('[1,2,3]'), null);
      expect(parseTurnstileSignal('{"token":"0.abc"}'), null, reason: 'no event');
      expect(parseTurnstileSignal('{"event":7}'), null, reason: 'event is not a string');
      expect(parseTurnstileSignal('{"event":"token"}'), null, reason: 'no token');
      expect(parseTurnstileSignal('{"event":"token","token":""}'), null, reason: 'empty token');
    });

    test('renders the site key and emits every event the decoder understands', () {
      final String html = turnstileHtml('0x4AAAAAAA-test-key');
      expectTrue(html.contains("sitekey: '0x4AAAAAAA-test-key'"), reason: 'site key');
      expectTrue(html.contains('Turnstile.postMessage'), reason: 'JS channel name');
      expectTrue(html.contains('challenges.cloudflare.com'), reason: 'the API script');
      // Every event the Dart side decodes must be one the page actually posts. A
      // token minted but never delivered looks like a hung challenge, which is
      // the worst possible symptom: nothing errors, the button just stays dead.
      for (final String event in <String>['token', 'error', 'expired', 'timeout']) {
        expectTrue(html.contains("post('$event'"), reason: 'the page must post $event');
        expectTrue(
          parseTurnstileSignal('{"event":"$event","token":"x"}') != null ||
              parseTurnstileSignal('{"event":"$event"}') != null,
          reason: 'the decoder must understand $event',
        );
      }
      // The four callbacks that make those events fire. `callback` is unquoted —
      // it is a valid JS identifier — while the dashed names need quotes.
      expectTrue(html.contains('callback:'), reason: 'the success callback');
      for (final String option in <String>[
        'error-callback',
        'expired-callback',
        'timeout-callback',
      ]) {
        expectTrue(html.contains("'$option'"), reason: option);
      }
      // 'flexible' is what makes it fit a phone; 'normal' is a fixed 300dp.
      expectTrue(html.contains("size: 'flexible'"));
    });
  });
}

Map<String, dynamic> _profileJson({
  bool phoneVerified = false,
  bool reverifyDue = false,
  bool reverifyBlocked = false,
  int? daysRemaining,
}) => <String, dynamic>{
  'id': '0a373307-4767-4485-9111-2e80f5046bf4',
  'username': null,
  'given_name': 'Dannison',
  'family_name': 'Luk',
  'gender': null,
  'avatar_key': null,
  'email': 'd@example.com',
  'email_verified': false,
  'phone_masked': '+852****4567',
  'phone_verified': phoneVerified,
  'phone_reverify_due_at': null,
  'phone_reverify_grace_ends_at': null,
  'phone_reverify_due': reverifyDue,
  'phone_reverify_blocked': reverifyBlocked,
  'phone_reverify_days_remaining': daysRemaining,
  'account_status': 'UNVERIFIED',
  'role': 'PASSENGER',
};

void _identityTests() {
  group('Profile', () {
    test('decodes the full profile', () {
      final Profile profile = Profile.fromJson(_profileJson());
      expect(profile.phoneMasked, '+852****4567');
      expectFalse(profile.phoneVerified);
      expect(profile.accountStatus, AccountStatus.unverified);
      expect(profile.role, UserRole.passenger);
      expect(profile.displayName, 'Dannison Luk');
      expect(profile.phoneReverifyDueAt, null);
    });

    test('a proven phone is what decides whether the account can call a taxi', () {
      expectFalse(Profile.fromJson(_profileJson()).canCallTaxi, reason: 'never proven');
      expectTrue(Profile.fromJson(_profileJson(phoneVerified: true)).canCallTaxi);
      // Proven but past grace: the server refuses new business with 403
      // PHONE_REVERIFY_DUE, so the client must not offer the action either.
      expectFalse(
        Profile.fromJson(_profileJson(phoneVerified: true, reverifyBlocked: true)).canCallTaxi,
      );
      // Due but inside the grace window still works — a reminder, not a refusal.
      expectTrue(
        Profile.fromJson(
          _profileJson(phoneVerified: true, reverifyDue: true, daysRemaining: 5),
        ).canCallTaxi,
      );
    });

    test('the reminder counts down to the next event', () {
      expect(Profile.fromJson(_profileJson(phoneVerified: true)).phoneReverifyNoticeZh, null);
      expect(
        Profile.fromJson(
          _profileJson(phoneVerified: true, reverifyDue: true, daysRemaining: 5),
        ).phoneReverifyNoticeZh,
        '請於 5 日內重新驗證電話號碼，否則將無法叫車。',
      );
      // Blocked: `days_remaining` is null because there is no next event.
      expect(
        Profile.fromJson(
          _profileJson(phoneVerified: true, reverifyBlocked: true),
        ).phoneReverifyNoticeZh,
        '電話驗證已逾期，需重新驗證才能繼續叫車。',
      );
      // Due with no countdown (a grandfathered row) is still a reminder.
      expect(
        Profile.fromJson(
          _profileJson(phoneVerified: true, reverifyDue: true),
        ).phoneReverifyNoticeZh,
        '請重新驗證電話號碼，否則將無法叫車。',
      );
    });

    test('falls back to the username when no name is filled in', () {
      final Map<String, dynamic> json = _profileJson();
      json['given_name'] = null;
      json['family_name'] = null;
      expect(Profile.fromJson(json).displayName, null, reason: 'username is null too');
      json['username'] = 'dannison';
      expect(Profile.fromJson(json).displayName, 'dannison');
    });

    test('an unknown account status fails loudly', () {
      final Map<String, dynamic> json = _profileJson();
      json['account_status'] = 'ON_HOLIDAY';
      expectThrows(() => Profile.fromJson(json), contains: 'AccountStatus');
    });

    test('a missing required field names itself', () {
      final Map<String, dynamic> json = _profileJson();
      json.remove('phone_verified');
      expectThrows(() => Profile.fromJson(json), contains: 'phone_verified');
    });

    test('the binding reads `verified` and the profile off one flat body', () {
      // `PhoneBindOut` is `{"verified": true, **profile}` — not a nested object.
      final Map<String, dynamic> body = _profileJson(phoneVerified: true);
      body['verified'] = true;
      final PhoneBinding binding = PhoneBinding.fromJson(body);
      expectTrue(binding.verified);
      expectTrue(binding.profile.phoneVerified);
      // The two are independent on purpose: `verified` confirms *this request*
      // proved the number, `phone_verified` is a state that could already be true.
      final Map<String, dynamic> stale = _profileJson(phoneVerified: true);
      stale['verified'] = false;
      expectFalse(PhoneBinding.fromJson(stale).verified);
      expectTrue(PhoneBinding.fromJson(stale).profile.phoneVerified);
    });
  });

  group('refusal reasons', () {
    ApiException refusal(String reason) => ApiException.fromEnvelope(<String, dynamic>{
      'code': 'FORBIDDEN',
      'message': 'refused',
      'details': <String, dynamic>{'reason': reason},
    }, statusCode: 403);

    test("reads the guard's reason out of details", () {
      expect(refusal('PHONE_NOT_VERIFIED').reason, 'PHONE_NOT_VERIFIED');
      expectTrue(refusal('PHONE_NOT_VERIFIED').isPhoneNotVerified);
      expectTrue(refusal('PHONE_NOT_VERIFIED').needsPhoneUnlock);
      expectTrue(refusal('PHONE_REVERIFY_DUE').isPhoneReverifyDue);
      expectTrue(refusal('PHONE_REVERIFY_DUE').needsPhoneUnlock);
      expectTrue(refusal('HUMAN_VERIFICATION_REQUIRED').isHumanVerificationRequired);
      expectTrue(refusal('REVIEWER_ACCOUNT_EXPIRED').isReviewerExpired);
      // Both phone refusals land on the same screen, so neither alone is the test.
      expectFalse(refusal('HUMAN_VERIFICATION_REQUIRED').needsPhoneUnlock);
    });

    test('an unrelated refusal is not a phone problem', () {
      final ApiException forbidden = ApiException.fromEnvelope(<String, dynamic>{
        'code': 'FORBIDDEN',
        'message': 'nope',
        'details': <String, dynamic>{},
      }, statusCode: 403);
      expect(forbidden.reason, null);
      expectFalse(forbidden.needsPhoneUnlock);
    });

    test('a non-string reason is ignored rather than thrown on', () {
      // The error path is the worst place to discover a contract change: an
      // `as String?` here would turn a handled refusal into a crash.
      final ApiException odd = ApiException.fromEnvelope(<String, dynamic>{
        'code': 'FORBIDDEN',
        'message': 'refused',
        'details': <String, dynamic>{'reason': 42},
      }, statusCode: 403);
      expect(odd.reason, null);
      expectFalse(odd.needsPhoneUnlock);
    });

    test('a reviewer refusal is 403, so it is not an auth failure', () {
      // 401 would tell the client to re-authenticate, which cannot help — a
      // reviewer who retried with fresh credentials would be told the same thing.
      final ApiException expired = refusal('REVIEWER_ACCOUNT_EXPIRED');
      expectFalse(expired.isAuthFailure);
      expectFalse(expired.isTransient);
      expectFalse(expired.needsPhoneUnlock, reason: 'a lockout is not a phone problem');
    });
  });

  group('AuthOutcome', () {
    test('reads `created` off the same body as the session', () {
      final Map<String, dynamic> body = <String, dynamic>{
        'access_token': 'a',
        'refresh_token': 'r',
        'created': true,
        'user': <String, dynamic>{'id': 'u', 'phone_masked': '+852****4567', 'role': 'PASSENGER'},
      };
      expectTrue(AuthOutcome.fromJson(body).created);
      expect(AuthOutcome.fromJson(body).session.user.role, UserRole.passenger);
      // `/auth/login` says false; `/auth/refresh` omits the key entirely.
      body['created'] = false;
      expectFalse(AuthOutcome.fromJson(body).created);
      body.remove('created');
      expectFalse(AuthOutcome.fromJson(body).created);
    });
  });
}
