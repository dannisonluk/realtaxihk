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
