import '../core/network/wire.dart';
import 'enums.dart';

/// `GET/PUT /api/v1/drivers/me/booking-preferences`.
///
/// A driver's standing willingness to take pre-booked rides. The server stores
/// the time bounds as `"HH:MM"` locale-independent strings, so the model keeps
/// them as strings and only helps a screen convert between `TimeOfDay` and the
/// wire format.
class DriverBookingPreferences {
  const DriverBookingPreferences({
    this.categories = const <LandmarkCategory>[],
    this.preferredOriginArea = '',
    this.availableFrom = '',
    this.availableUntil = '',
    this.updatedAt,
  });

  factory DriverBookingPreferences.fromJson(Map<String, dynamic> json) => DriverBookingPreferences(
    categories: json['categories'] == null
        ? const <LandmarkCategory>[]
        : asEnumList(json['categories'], 'categories', LandmarkCategory.fromWire),
    preferredOriginArea:
        asStringOrNull(json['preferred_origin_area'], 'preferred_origin_area') ?? '',
    availableFrom: asStringOrNull(json['available_from'], 'available_from') ?? '',
    availableUntil: asStringOrNull(json['available_until'], 'available_until') ?? '',
    updatedAt: asDateOrNull(json['updated_at'], 'updated_at'),
  );

  final List<LandmarkCategory> categories;
  final String preferredOriginArea;
  final String availableFrom;
  final String availableUntil;
  final DateTime? updatedAt;

  static bool isValidTime(String value) {
    final RegExpMatch? match = RegExp(r'^([01]\d|2[0-3]):([0-5]\d)$').firstMatch(value);
    return match != null;
  }

  /// Normalises a time string to zero-padded `"HH:MM"`, or `''` when invalid.
  static String normalizeTime(String? value) {
    if (value == null || !isValidTime(value)) {
      return '';
    }
    return formatTime(int.parse(value.substring(0, 2)), int.parse(value.substring(3, 5)));
  }

  static String formatTime(int hour, int minute) =>
      '${hour.toString().padLeft(2, '0')}:${minute.toString().padLeft(2, '0')}';

  static String displayTime(String? value) {
    final String normalized = normalizeTime(value);
    return normalized.isEmpty ? '--:--' : normalized;
  }

  String get displayFrom => displayTime(availableFrom);

  String get displayUntil => displayTime(availableUntil);

  DriverBookingPreferences copyWith({
    List<LandmarkCategory>? categories,
    String? preferredOriginArea,
    String? availableFrom,
    String? availableUntil,
    DateTime? updatedAt,
  }) {
    return DriverBookingPreferences(
      categories: categories ?? this.categories,
      preferredOriginArea: preferredOriginArea ?? this.preferredOriginArea,
      availableFrom: availableFrom ?? this.availableFrom,
      availableUntil: availableUntil ?? this.availableUntil,
      updatedAt: updatedAt ?? this.updatedAt,
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
    'categories': categories.map((LandmarkCategory c) => c.wire).toList(growable: false),
    'preferred_origin_area': preferredOriginArea,
    'available_from': normalizeTime(availableFrom),
    'available_until': normalizeTime(availableUntil),
  };
}
