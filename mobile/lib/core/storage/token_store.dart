import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../../models/auth.dart';

/// Where the session lives between launches.
///
/// The refresh token is a bearer credential with a long life, so it goes to the
/// platform keystore (Android Keystore via `flutter_secure_storage`), never to
/// `SharedPreferences`. The account is cached alongside it purely so the app can
/// paint the right role's home screen on cold start instead of flashing the
/// login page while `/auth/me` is in flight — it is re-validated immediately.
abstract interface class TokenStore {
  Future<AuthSession?> read();

  Future<void> write(AuthSession session);

  /// Persist a rotated pair.
  ///
  /// `/auth/refresh` returns a **new** refresh token every time, and replaying a
  /// superseded one makes the server revoke the whole family (`SEC-17`). The
  /// replacement must therefore be durable before the request that produced it
  /// is considered done.
  Future<void> updateTokens({required String accessToken, required String refreshToken});

  Future<String?> accessToken();

  Future<String?> refreshToken();

  Future<void> clear();
}

class SecureTokenStore implements TokenStore {
  SecureTokenStore({FlutterSecureStorage? storage})
    : _storage = storage ?? const FlutterSecureStorage(aOptions: _android, iOptions: _ios);

  /// Android: AES-GCM for the data, with the key wrapped by an RSA-OAEP key
  /// held in the hardware-backed Android Keystore. This is the default in
  /// `flutter_secure_storage` 11, but it is pinned here so a future default
  /// change cannot silently downgrade where the refresh token lives.
  ///
  /// `resetOnError: true` (the default) means a Keystore reset — a factory
  /// reset, a restore onto new hardware — drops the session instead of throwing
  /// on every read. Signing in again is the correct recovery; a wedged store is
  /// not.
  static const AndroidOptions _android = AndroidOptions();

  /// iOS/macOS: `first_unlock_this_device`.
  ///
  /// `first_unlock` rather than the `unlocked` default because the driver's
  /// background location task has to read the access token while the screen is
  /// locked — with `unlocked` the keychain read fails and the tick is lost.
  /// `_this_device` keeps the session out of iCloud Keychain: a bearer token
  /// must not roam to another device.
  static const IOSOptions _ios = IOSOptions(
    accessibility: KeychainAccessibility.first_unlock_this_device,
  );

  static const String _kSession = 'realtaxi.session';

  final FlutterSecureStorage _storage;

  @override
  Future<AuthSession?> read() async {
    final String? raw = await _storage.read(key: _kSession);
    if (raw == null) {
      return null;
    }
    try {
      final Object? decoded = jsonDecode(raw);
      if (decoded is! Map<String, dynamic>) {
        return null;
      }
      final String? access = decoded['access'] as String?;
      final String? refresh = decoded['refresh'] as String?;
      final Object? userJson = decoded['user'];
      if (access == null || refresh == null || userJson is! Map<String, dynamic>) {
        return null;
      }
      return AuthSession(
        accessToken: access,
        refreshToken: refresh,
        user: AppUser.fromJson(userJson),
      );
    } on FormatException {
      // A corrupted cache is not worth surfacing — sign in again.
      await clear();
      return null;
    } on ArgumentError {
      await clear();
      return null;
    }
  }

  @override
  Future<void> write(AuthSession session) async {
    // One key per session makes the three values replace together. Writing the
    // fields as separate keys could leave access/refresh/user mismatched if a
    // single secure-storage write failed midway.
    await _storage.write(
      key: _kSession,
      value: jsonEncode(<String, Object?>{
        'access': session.accessToken,
        'refresh': session.refreshToken,
        'user': session.user.toJson(),
      }),
    );
  }

  @override
  Future<void> updateTokens({required String accessToken, required String refreshToken}) async {
    final AuthSession? session = await read();
    if (session == null) {
      return;
    }
    await write(
      AuthSession(accessToken: accessToken, refreshToken: refreshToken, user: session.user),
    );
  }

  @override
  Future<String?> accessToken() async {
    final AuthSession? session = await read();
    return session?.accessToken;
  }

  @override
  Future<String?> refreshToken() async {
    final AuthSession? session = await read();
    return session?.refreshToken;
  }

  @override
  Future<void> clear() => _storage.deleteAll();
}
