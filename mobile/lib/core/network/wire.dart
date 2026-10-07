/// Strict decoders for the backend's JSON.
///
/// Two things make plain `as T` casts wrong for this API:
///
/// 1. **`Decimal` is a string.** Pydantic v2 serialises `Decimal` to a JSON
///    string, and the backend types `distance_km`, `waiting_min`,
///    `discount_percent` and every money field as `Decimal`. So
///    `json['distance_km'] as double` throws on every real response. Verified
///    against the running app: `FareEstimateResponse.model_dump_json()` emits
///    `"distance_km": "12.5"`.
/// 2. **A cast failure is silent-ish.** `as` throws a bare `_TypeError` with no
///    field name, which is close to useless in a crash report from a device.
///
/// Every helper here names the field it failed on.
library;

import '../format/money.dart';
import '../network/api_exception.dart';

Map<String, dynamic> asMap(Object? value, String field) {
  if (value is Map<String, dynamic>) {
    return value;
  }
  if (value is Map<Object?, Object?>) {
    return value.map((Object? k, Object? v) => MapEntry<String, dynamic>(k.toString(), v));
  }
  throw MalformedResponseException('$field: expected an object, got ${value.runtimeType}');
}

List<Object?> asList(Object? value, String field) {
  if (value is List<Object?>) {
    return value;
  }
  throw MalformedResponseException('$field: expected a list, got ${value.runtimeType}');
}

String asString(Object? value, String field) {
  if (value is String) {
    return value;
  }
  throw MalformedResponseException('$field: expected a string, got ${value.runtimeType}');
}

String? asStringOrNull(Object? value, String field) =>
    value == null ? null : asString(value, field);

bool asBool(Object? value, String field) {
  if (value is bool) {
    return value;
  }
  throw MalformedResponseException('$field: expected a bool, got ${value.runtimeType}');
}

/// `asBool` with a default — for fields that joined the wire contract after
/// old fixtures were recorded (P2-2 `is_partial`), where absence means the
/// legacy value (`false`).
bool asBoolOr(Object? value, String field, bool fallback) {
  if (value == null) {
    return fallback;
  }
  return asBool(value, field);
}

/// Accepts a JSON string (the normal case — see the library docstring) or a
/// number, since a few endpoints build dicts by hand and JSON-encode natively.
double asDouble(Object? value, String field) {
  if (value is num) {
    return value.toDouble();
  }
  if (value is String) {
    final double? parsed = double.tryParse(value);
    if (parsed != null) {
      return parsed;
    }
  }
  throw MalformedResponseException('$field: expected a numeric value, got $value');
}

double? asDoubleOrNull(Object? value, String field) =>
    value == null ? null : asDouble(value, field);

int asInt(Object? value, String field) {
  if (value is int) {
    return value;
  }
  if (value is num) {
    return value.toInt();
  }
  if (value is String) {
    final int? parsed = int.tryParse(value);
    if (parsed != null) {
      return parsed;
    }
  }
  throw MalformedResponseException('$field: expected an integer, got $value');
}

int? asIntOrNull(Object? value, String field) => value == null ? null : asInt(value, field);

/// `datetime.isoformat()` server-side; null when absent, never throws.
DateTime? asDateOrNull(Object? value, String field) {
  if (value == null) {
    return null;
  }
  final String raw = asString(value, field);
  if (raw.isEmpty) {
    return null;
  }
  return DateTime.tryParse(raw)?.toLocal();
}

/// A list of objects, decoded with [decode].
List<T> asObjectList<T>(Object? value, String field, T Function(Map<String, dynamic>) decode) {
  return asList(
    value,
    field,
  ).map((Object? item) => decode(asMap(item, '$field[]'))).toList(growable: false);
}

/// A list of strings, or an empty list when the key is absent (older fixtures).
List<String> asStringListOrEmpty(Object? value, String field) {
  if (value == null) {
    return const <String>[];
  }
  return asList(
    value,
    field,
  ).map((Object? item) => asString(item, '$field[]')).toList(growable: false);
}

/// A [Money] value, or null when the key is absent.
Money? asMoneyOrNull(Object? value, String field) {
  if (value == null) {
    return null;
  }
  return Money.parse(value as String);
}

/// A list of enums decoded from their wire tokens.
List<T> asEnumList<T>(Object? value, String field, T Function(String) decode) {
  return asList(
    value,
    field,
  ).map((Object? item) => decode(asString(item, '$field[]'))).toList(growable: false);
}
