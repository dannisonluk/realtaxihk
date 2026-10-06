import 'package:geolocator/geolocator.dart';

// Screens that receive a fix from this service name `Position` in their own
// signatures; re-exporting it keeps them from depending on `geolocator` just to
// write the type down.
export 'package:geolocator/geolocator.dart' show Position;

/// Why a location read could not happen. Distinguishing these matters: a
/// permanently-denied permission needs the app-settings screen, a disabled
/// service needs the system toggle, and a transient denial just needs asking
/// again.
enum LocationAccess { granted, denied, deniedForever, serviceDisabled }

/// Thin wrapper over `geolocator`.
///
/// The backend's authoritative service-area gate is the `hk_bounds` polygon,
/// not the coarse box mirrored here. This client check is only a cheap,
/// advisory pre-filter so a clearly-outside fix is not offered to the server;
/// the server may still answer an `OUTSIDE_HK` refusal, and callers must
/// handle that code instead of trusting this box.
class LocationService {
  const LocationService();

  /// Coarse superset box for the cheap reject. The server polygon is the gate.
  static const double minLat = 22.1;
  static const double maxLat = 22.6;
  static const double minLng = 113.8;
  static const double maxLng = 114.5;

  static bool isInHongKong(double lat, double lng) =>
      lat >= minLat && lat <= maxLat && lng >= minLng && lng <= maxLng;

  /// Resolves permission, prompting once if needed.
  Future<LocationAccess> ensureAccess() async {
    if (!await Geolocator.isLocationServiceEnabled()) {
      return LocationAccess.serviceDisabled;
    }
    LocationPermission permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    return switch (permission) {
      LocationPermission.always || LocationPermission.whileInUse => LocationAccess.granted,
      LocationPermission.deniedForever => LocationAccess.deniedForever,
      LocationPermission.denied => LocationAccess.denied,
      _ => LocationAccess.denied,
    };
  }

  /// One fix, or null when unavailable or outside Hong Kong.
  ///
  /// Pass [access] when `ensureAccess()` has just answered, so the answer is
  /// reused. Asking twice is not harmless: a `denied` permission makes
  /// `requestPermission()` raise the system dialog again, so one tap would put
  /// the same question to the user twice.
  ///
  /// [LocationSettings.timeLimit] is set because a `getCurrentPosition` with no
  /// fix and no limit hangs indefinitely on some Android devices.
  Future<Position?> current({
    LocationAccess? access,
    LocationAccuracy accuracy = LocationAccuracy.high,
    Duration timeLimit = const Duration(seconds: 12),
  }) async {
    if ((access ?? await ensureAccess()) != LocationAccess.granted) {
      return null;
    }
    try {
      final Position position = await Geolocator.getCurrentPosition(
        locationSettings: LocationSettings(accuracy: accuracy, timeLimit: timeLimit),
      );
      return isInHongKong(position.latitude, position.longitude) ? position : null;
    } on Exception {
      return null;
    }
  }

  /// Continuous fixes while the driver is online.
  ///
  /// [distanceFilter] is in metres. 10m keeps the PostGIS write rate sane — the
  /// endpoint is rate-limited per driver (`SEC-15`) because each call is an
  /// UPDATE.
  Stream<Position> stream({
    LocationAccuracy accuracy = LocationAccuracy.high,
    int distanceFilter = 10,
  }) {
    return Geolocator.getPositionStream(
      locationSettings: LocationSettings(accuracy: accuracy, distanceFilter: distanceFilter),
    );
  }

  /// Opens the system settings the user needs for [access].
  Future<void> openSettings(LocationAccess access) async {
    if (access == LocationAccess.serviceDisabled) {
      await Geolocator.openLocationSettings();
    } else {
      await Geolocator.openAppSettings();
    }
  }
}
