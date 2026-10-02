import 'package:intl/intl.dart';

/// HKD money, kept in the server's canonical form.
///
/// Every amount on the wire is a **string** produced by the backend, which
/// serialises `Decimal` rather than emitting a JSON number. Two precisions are
/// in play and they are not interchangeable:
///
/// * **stored money** — deposits, ledger amounts, refunds, order totals — is
///   `Numeric(10, 2)` in the database and arrives at **2 dp** (`money_str` in
///   `app/core/money.py`): `"500.00"`, `"0.05"`.
/// * **meter figures** — fares, tolls, surcharges — come from the tariff table
///   and arrive at **1 dp** (`meter_str`): `"147.1"`.
///
/// [canonical] keeps the exact string, so neither precision is lost here.
/// [asDouble] exists only for arithmetic the UI genuinely needs (map radius,
/// chart scaling).
class Money implements Comparable<Money> {
  const Money(this.canonical);

  factory Money.parse(Object? raw) {
    if (raw == null) {
      return const Money('0.00');
    }
    if (raw is num) {
      return Money(raw.toString());
    }
    return Money(raw.toString());
  }

  /// The exact decimal string as the server sent it, e.g. `"184.50"`.
  final String canonical;

  double get asDouble => double.tryParse(canonical) ?? 0;

  bool get isZero => asDouble == 0;

  bool get isNegative => asDouble < 0;

  /// `this - other`, computed in **integer cents**.
  ///
  /// Never subtract [asDouble]s. `500.00 - 499.70` is `0.30000000000001137` in
  /// binary floating point, and because [canonical] faithfully preserves
  /// whatever string it is handed, that artefact is exactly what the user
  /// reads: the deposit card printed `HK$0.30000000000001137`, and the fleet
  /// saving line `HK$12.699999999999989`.
  ///
  /// Both operands arrive from the server at 2 dp (stored money) or 1 dp (meter
  /// figures), so scaling to cents is exact — and an exact subtraction is the
  /// whole reason [canonical] is a string in the first place.
  Money minus(Money other) => Money.fromCents(_cents(canonical) - _cents(other.canonical));

  /// The canonical 2 dp form for a cent count: `30` -> `"0.30"`, `-50` -> `"-0.50"`.
  ///
  /// Producing the server's own 2 dp shape (rather than a trimmed one) keeps
  /// [display]'s trailing-zero rule the single place that decides how much of
  /// the precision to show.
  factory Money.fromCents(int cents) {
    final int magnitude = cents.abs();
    final String sign = cents < 0 ? '-' : '';
    return Money('$sign${magnitude ~/ 100}.${(magnitude % 100).toString().padLeft(2, '0')}');
  }

  /// Cents, parsed from the string rather than through a double.
  ///
  /// A fractional part shorter than 2 digits is padded (`"147.1"` is 10 cents);
  /// a longer one is cut. The cut is a guard against a malformed payload, not a
  /// rounding step — the server never sends money with more than 2 dp.
  static int _cents(String canonical) {
    final bool negative = canonical.startsWith('-');
    final String body = negative ? canonical.substring(1) : canonical;
    final List<String> parts = body.split('.');
    final int whole = int.tryParse(parts[0]) ?? 0;
    final String fraction = parts.length > 1 ? parts[1] : '';
    final int cents = int.tryParse(fraction.padRight(2, '0').substring(0, 2)) ?? 0;
    final int total = whole * 100 + cents;
    return negative ? -total : total;
  }

  /// Signed display with the HK$ prefix: `HK$184.50`, `+HK$500.00`.
  String get hkd => 'HK\$$display';

  /// Two decimal places, always: `184.50`.
  String get fixed => asDouble.toStringAsFixed(2);

  /// Cents when they are present, whole dollars when they are not.
  ///
  /// This used to truncate to one decimal place, which silently hid stored
  /// money: a `0.05` deposit rendered as `HK$0.1` and a `0.04` balance as
  /// `HK$0`. It now matches the precision the value actually carries — `500.00`
  /// -> `500`, `0.05` -> `0.05`, `147.1` -> `147.1`.
  String get display {
    final double value = asDouble;
    final String trimmed = canonical.contains('.')
        ? canonical.replaceFirst(RegExp(r'0+$'), '').replaceFirst(RegExp(r'\.$'), '')
        : canonical;
    // A value with real cents keeps both of them; a whole/decile value is
    // shown exactly as the server sent it.
    if (value == value.roundToDouble()) {
      return value.toStringAsFixed(0);
    }
    return trimmed.isEmpty ? '0' : trimmed;
  }

  /// For a ledger row: `+HK$500` / `-HK$50.00`.
  String get signedHkd =>
      '${isNegative ? '-' : '+'}HK\$${Money(canonical.replaceFirst('-', '')).display}';

  @override
  int compareTo(Money other) => asDouble.compareTo(other.asDouble);

  @override
  bool operator ==(Object other) => other is Money && other.canonical == canonical;

  @override
  int get hashCode => canonical.hashCode;

  @override
  String toString() => canonical;
}

/// Distance and duration helpers used by the request and trip screens.
abstract final class Format {
  static final NumberFormat _km = NumberFormat('0.0');

  static String km(double value) => '${_km.format(value)} km';

  /// `m:ss` — short enough for a trip ETA chip.
  static String duration(Duration d) {
    final int minutes = d.inMinutes;
    if (minutes < 60) {
      return '$minutes min';
    }
    final int hours = minutes ~/ 60;
    final int rest = minutes % 60;
    return rest == 0 ? '$hours h' : '$hours h $rest min';
  }

  /// ISO-8601 with offset, as emitted by `datetime.isoformat()` server-side.
  /// Returns null on anything unparseable rather than throwing inside a build.
  static DateTime? tryParseIso(Object? raw) {
    if (raw is! String || raw.isEmpty) {
      return null;
    }
    return DateTime.tryParse(raw)?.toLocal();
  }

  static String clock(DateTime? at) => at == null ? '--:--' : DateFormat('HH:mm').format(at);

  static String dateTime(DateTime? at) =>
      at == null ? '--' : DateFormat('yyyy-MM-dd HH:mm').format(at);
}
