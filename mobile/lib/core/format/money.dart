import 'package:intl/intl.dart';

/// HKD money, kept in the server's canonical form.
///
/// Every amount on the wire is a **string** produced by the backend's
/// `Decimal.quantize(Decimal("0.1"))` — `money_str()` in `app/core/money.py`
/// and the identical helpers in `app/api/fare.py` / `app/api/drivers.py`. The
/// server never sends a JSON number, so parsing into `double` at the edge and
/// formatting back would round-trip through binary floating point for no
/// benefit. [canonical] keeps the exact string; [asDouble] exists only for
/// arithmetic the UI genuinely needs (map radius, chart scaling).
class Money implements Comparable<Money> {
  const Money(this.canonical);

  factory Money.parse(Object? raw) {
    if (raw == null) {
      return const Money('0.0');
    }
    if (raw is num) {
      return Money(raw.toString());
    }
    return Money(raw.toString());
  }

  /// The exact decimal string as the server sent it, e.g. `"184.5"`.
  final String canonical;

  double get asDouble => double.tryParse(canonical) ?? 0;

  bool get isZero => asDouble == 0;

  bool get isNegative => asDouble < 0;

  /// Signed display with the HK$ prefix and no trailing `.0` noise: `HK$184.5`.
  String get hkd => 'HK\$$display';

  /// Two decimal places, always: `184.50`.
  String get fixed => asDouble.toStringAsFixed(2);

  /// Up to one decimal place — the meter's own precision. `184.5`, `12`.
  String get display {
    final double value = asDouble;
    if (value == value.roundToDouble()) {
      return value.toStringAsFixed(0);
    }
    return value.toStringAsFixed(1);
  }

  /// For a ledger row: `+HK$500` / `-HK$50`.
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
