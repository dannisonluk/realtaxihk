import 'package:geolocator/geolocator.dart';

/// Why a location read could not happen. Distinguishing these matters: a
/// permanently-denied permission needs the app-settings screen, a disabled
/// service needs the system toggle, and a transient denial just needs asking
/// again.
enum LocationAccess { granted, denied, deniedForever, serviceDisabled }

/// Thin wrapper over `geolocator`.
///
/// The backend bounds every coordinate to `lat 22.1–22.6, lng 113.8–114.5`
/// (`_HK_BOUNDS` in `app/api/orders.py`, repeated in `ws.py`), and rejects
/// anything outside with a 422 or `{"type":"error","code":"BAD_LOCATION"}`. So
/// [current] returns null rather than a position the server will refuse —
/// callers then show "locating…" instead of an error the user cannot act on.
class LocationService {
  const LocationService();

  /// `HONG_KONG_BOUNDS` mirrors the server's own constant.
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
  /// [LocationSettings.timeLimit] is set because a `getCurrentPosition` with no
  /// fix and no limit hangs indefinitely on some Android devices.
  Future<Position?> current({
    LocationAccuracy accuracy = LocationAccuracy.high,
    Duration timeLimit = const Duration(seconds: 12),
  }) async {
    if (await ensureAccess() != LocationAccess.granted) {
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
