import 'package:flutter/foundation.dart';

/// Runtime configuration, resolved from `--dart-define` with a dev fallback.
///
/// The backend serves its API directly under `/api/v1` with no extra prefix, so
/// [apiBaseUrl] is the bare origin. The WebSocket lives at `/ws/trip/{order_id}`
/// on the same origin.
///
/// Build against a real host with:
///   flutter run --dart-define=API_BASE_URL=https://api.hkfastdc.com
class AppConfig {
  const AppConfig._();

  static const String _apiOverride = String.fromEnvironment('API_BASE_URL');
  static const String _wsOverride = String.fromEnvironment('WS_BASE_URL');

  /// Google Maps key. Not a secret — the Android key is restricted by package
  /// name and signing certificate, and it also has to sit in the app manifest
  /// in plain text. Supply it at build time:
  ///
  ///   flutter run --dart-define=GOOGLE_MAPS_API_KEY=AIza...
  ///
  /// The Gradle build reads the same value from `android/local.properties` (also
  /// gitignored) to fill the `googleMapsApiKey` manifest placeholder. When it is
  /// absent the app renders a coordinate panel instead of a map, so a build
  /// without a key is still usable rather than a grey rectangle.
  static const String googleMapsApiKey = String.fromEnvironment('GOOGLE_MAPS_API_KEY');

  static bool get mapsConfigured => googleMapsApiKey.isNotEmpty;

  /// The Android emulator reaches the host machine on 10.0.2.2, never on
  /// 127.0.0.1 — the latter is the emulator's own loopback.
  static String get _devHost {
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) {
      return 'http://10.0.2.2:8000';
    }
    return 'http://127.0.0.1:8000';
  }

  static String get apiBaseUrl => _apiOverride.isEmpty ? _devHost : _apiOverride;

  static String get wsBaseUrl {
    if (_wsOverride.isNotEmpty) {
      return _wsOverride;
    }
    final Uri base = Uri.parse(apiBaseUrl);
    return base.replace(scheme: base.scheme == 'https' ? 'wss' : 'ws').toString();
  }

  /// Live-trip channel. The access token travels as a query parameter because
  /// browsers and Dart's WebSocket both lack a header channel on connect.
  static Uri tripSocket({required String orderId, required String accessToken}) {
    final Uri base = Uri.parse('$wsBaseUrl/ws/trip/$orderId');
    return base.replace(queryParameters: <String, String>{'token': accessToken});
  }

  /// Server close codes for the trip channel.
  static const int wsUnauthenticated = 4401;
  static const int wsForbidden = 4403;
  static const int wsUnknownOrder = 4404;
  static const int wsCapacity = 4408;

  /// The driver app pushes a location tick on this cadence while in a trip.
  /// The server caps sustained ticks at `ws_ticks_per_second` (default 2/s) with
  /// a burst allowance of 5, so 3s is comfortably inside the budget.
  static const Duration locationTickInterval = Duration(seconds: 3);

  /// The passenger map falls back to REST polling at this cadence when the
  /// socket is down. `GET /trips/{id}/location` is a single indexed read.
  static const Duration locationPollInterval = Duration(seconds: 10);
}
