import 'package:flutter/foundation.dart';

/// Runtime configuration, resolved from `--dart-define` with a dev fallback.
///
/// The backend serves its API directly under `/api/v1` with no extra prefix, so
/// [apiBaseUrl] is the bare origin. The WebSocket lives at `/ws/trip/{order_id}`
/// on the same origin.
///
/// Build against a real host with:
///   flutter run --dart-define=API_BASE_URL=https://hkfastdc.com
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

  /// Cloudflare Turnstile **site** key. Not a secret — it is embedded in the page
  /// the WebView renders, exactly like the Maps key is embedded in the manifest.
  ///
  ///   flutter build apk --dart-define=TURNSTILE_SITE_KEY=0x4AAAAAAA...
  ///
  /// There is **no endpoint that serves this**. `app/core/config.py` defines
  /// `turnstile_site_key`, but no route exposes it, so a build-time define is the
  /// only way the app can learn it. That is a deliberate gap worth knowing about:
  /// rotating the key needs a new build, not a config change.
  static const String turnstileSiteKey = String.fromEnvironment('TURNSTILE_SITE_KEY');

  /// The origin the Turnstile widget is loaded under.
  ///
  /// Turnstile mints a token *for a hostname*, and the WebView's `baseUrl`
  /// decides which one. It must therefore be a host listed on the site key's
  /// allowed-domain list, and it must not be the API host unless that host is on
  /// the list too — Cloudflare validates the origin, not the caller.
  static const String turnstileBaseUrl = String.fromEnvironment(
    'TURNSTILE_BASE_URL',
    defaultValue: 'https://hkfastdc.com/',
  );

  /// Whether this build can solve a challenge at all.
  ///
  /// False is normal in development: `DisabledHumanVerifier` allows everything
  /// server-side, so a dev build sends no `human_token` and still works. Under
  /// `APP_ENV=prod` the same request is a 403 — see `core/human/turnstile.dart`
  /// for what a release build does about it.
  static bool get humanVerificationConfigured => turnstileSiteKey.isNotEmpty;

  /// SHA-256 fingerprints of the API host's certificate, for TLS pinning.
  ///
  /// Comma-separated so more than one pin can be supplied during a rotation —
  /// the client accepts any of them, so a new certificate can be pinned before
  /// the old one is retired:
  ///
  ///   flutter build apk --release --dart-define=API_CERT_SHA256=aa:bb:…
  ///
  /// **A release build refuses to start without it** (fail-closed), the same way
  /// [apiBaseUrl] refuses a missing `API_BASE_URL` — see
  /// `core/network/cert_pinning.dart` and `ApiClient`. A debug/profile build may
  /// omit it, and logs a warning instead.
  static const String apiCertSha256 = String.fromEnvironment('API_CERT_SHA256');

  /// The Android emulator reaches the host machine on 10.0.2.2, never on
  /// 127.0.0.1 — the latter is the emulator's own loopback.
  static String get _devHost {
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) {
      return 'http://10.0.2.2:8000';
    }
    return 'http://127.0.0.1:8000';
  }

  static String get apiBaseUrl {
    if (_apiOverride.isEmpty && kReleaseMode) {
      throw StateError(
        'API_BASE_URL must be set with --dart-define in release builds; '
        'the loopback fallback is for development only.',
      );
    }
    if (kReleaseMode && _apiOverride.startsWith('http://')) {
      throw StateError(
        'API_BASE_URL must use https:// in release builds; plaintext HTTP is '
        'only allowed for local development. Got: $_apiOverride',
      );
    }
    return _apiOverride.isEmpty ? _devHost : _apiOverride;
  }

  static String get wsBaseUrl {
    if (_wsOverride.isNotEmpty) {
      if (kReleaseMode && !_wsOverride.startsWith('wss://')) {
        throw StateError(
          'WS_BASE_URL must use wss:// in release builds; the token travels in '
          'the query string and must never leave over plaintext. '
          'Got: $_wsOverride',
        );
      }
      return _wsOverride;
    }
    final Uri base = Uri.parse(apiBaseUrl);
    if (kReleaseMode && base.scheme != 'https') {
      throw StateError(
        'WS_BASE_URL cannot be derived from a non-https API_BASE_URL in '
        'release builds; the token would travel over plaintext. '
        'Got: $base',
      );
    }
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
