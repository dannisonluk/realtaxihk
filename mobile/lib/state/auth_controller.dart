import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/network/api_exception.dart';
import '../core/storage/token_store.dart';
import '../data/auth_repository.dart';
import '../models/auth.dart';
import 'providers.dart';

/// Owns the session: restores it on cold start, obtains it on OTP verify, and
/// destroys it on sign-out or when the API reports the refresh token is gone.
///
/// State is `AsyncValue<AppUser?>`:
/// * `loading` — the stored token is being re-validated;
/// * `data(null)` — signed out;
/// * `data(user)` — signed in;
/// * `error` — the server could not be reached during restore. The stored
///   session is *kept* in that case: a network blip must not log the user out.
class AuthController extends AsyncNotifier<AppUser?> {
  @override
  Future<AppUser?> build() async {
    final apiClient = ref.watch(apiClientProvider);

    // A 401 that survived the refresh attempt means the session is dead. Sign
    // out from here rather than at the next user interaction.
    final StreamSubscription<void> expired = apiClient.sessionExpired.listen((void _) {
      if (ref.mounted) {
        state = const AsyncData<AppUser?>(null);
      }
    });
    ref.onDispose(expired.cancel);

    final TokenStore store = ref.read(tokenStoreProvider);
    final AuthSession? stored = await store.read();
    if (stored == null) {
      return null;
    }

    try {
      // A live read behind `require_active_user`: a deactivated account fails
      // here even with a structurally valid token, and the role may have
      // changed since the cache was written.
      final AppUser user = await ref.read(authRepositoryProvider).me();
      await store.write(
        AuthSession(accessToken: stored.accessToken, refreshToken: stored.refreshToken, user: user),
      );
      return user;
    } on ApiException catch (e) {
      if (e.isAuthFailure) {
        await store.clear();
        return null;
      }
      // Offline. Keep the cached session and let the UI offer a retry.
      rethrow;
    }
  }

  /// `POST /auth/otp/request`. Throws [ApiException] on 429 (per-IP or per
  /// number) and on 503 (the platform ceiling — `Retry-After` is 600s).
  Future<OtpRequestResult> requestOtp(String phoneE164) =>
      ref.read(authRepositoryProvider).requestOtp(phoneE164);

  /// `POST /auth/otp/verify`, then persist the session.
  ///
  /// Returns true when this phone signed up just now (`created`), so the caller
  /// can route a brand-new passenger somewhere different from a returning one.
  Future<bool> verifyOtp({required String phoneE164, required String code}) async {
    final AuthSession session = await ref
        .read(authRepositoryProvider)
        .verifyOtp(phoneE164: phoneE164, code: code);

    await ref.read(tokenStoreProvider).write(session);
    state = AsyncData<AppUser?>(session.user);
    return true;
  }

  /// Re-read `/auth/me`. Used after a role change or a KYC approval, when the
  /// cached role may be stale.
  Future<void> refreshUser() async {
    final AppUser user = await ref.read(authRepositoryProvider).me();
    final TokenStore store = ref.read(tokenStoreProvider);
    final AuthSession? stored = await store.read();
    if (stored != null) {
      await store.write(
        AuthSession(accessToken: stored.accessToken, refreshToken: stored.refreshToken, user: user),
      );
    }
    state = AsyncData<AppUser?>(user);
  }

  /// Revoke server-side (best effort) then clear locally.
  ///
  /// The local clear is unconditional: `/auth/logout` can fail because the
  /// network is down or the token is already invalid, and neither is a reason to
  /// leave a credential on the device.
  Future<void> signOut() async {
    try {
      await ref.read(authRepositoryProvider).logout();
    } on ApiException {
      // Already invalid or unreachable — the local clear below is what matters.
    } finally {
      await ref.read(tokenStoreProvider).clear();
      state = const AsyncData<AppUser?>(null);
    }
  }
}
