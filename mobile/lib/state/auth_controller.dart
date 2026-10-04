import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/network/api_exception.dart';
import '../core/storage/token_store.dart';
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
      // A reviewer account past its expiry is refused with **403, not 401** — the
      // token is structurally valid, the *account* has lapsed. So it is not an
      // auth failure, and treating it like a network blip would leave the user on
      // a splash whose retry button can never work: every retry hits the same
      // guard. Sign out instead. `AccountAuthService.login` refuses an expired
      // reviewer too, so the sentence surfaces on the login screen rather than
      // being lost.
      if (e.isReviewerExpired) {
        await store.clear();
        return null;
      }
      // Offline. Keep the cached session and let the UI offer a retry.
      rethrow;
    }
  }

  /// `POST /auth/register` — create the account and sign in, then persist it.
  ///
  /// The phone number is recorded as a **claim**: this call proves nothing about
  /// it. Proving a number is what unlocks calling a taxi, and it is a separate
  /// step (`identity/phone/request` + `confirm`) that an account can take at any
  /// time. A newly registered user therefore lands in the passenger app with
  /// `phoneVerified == false`, which is the intended state.
  ///
  /// Returns `created`, which is always true here — the flag is read off the wire
  /// rather than assumed so the three doors cannot drift apart.
  Future<bool> register({
    required String email,
    required String password,
    required String phoneE164,
    String? humanToken,
  }) async {
    final AuthOutcome outcome = await ref
        .read(authRepositoryProvider)
        .register(email: email, password: password, phoneE164: phoneE164, humanToken: humanToken);
    await _adopt(outcome.session);
    return outcome.created;
  }

  /// `POST /auth/login` — the primary credential, then persist the session.
  ///
  /// A lockout arrives as a 401, so a caller must not treat every 401 here as
  /// "wrong password". The distinction is in the message; the server chooses the
  /// status by exception type, never by the text of the refusal.
  Future<bool> login({required String email, required String password, String? humanToken}) async {
    final AuthOutcome outcome = await ref
        .read(authRepositoryProvider)
        .login(email: email, password: password, humanToken: humanToken);
    await _adopt(outcome.session);
    return outcome.created;
  }

  /// `POST /auth/otp/request`. Throws [ApiException] on 429 (per-IP or per
  /// number) and on 503 (the platform ceiling — `Retry-After` is 600s).
  ///
  /// This is the **secondary** login's first step, and the route is behind the
  /// human-verification gate: under `APP_ENV=prod` a missing [humanToken] is a
  /// 403 before any code is sent.
  Future<OtpRequestResult> requestOtp(String phoneE164, {String? humanToken}) =>
      ref.read(authRepositoryProvider).requestOtp(phoneE164, humanToken: humanToken);

  /// `POST /auth/otp/verify`, then persist the session.
  ///
  /// A secondary login: it cannot create an account, and it cannot sign in an
  /// account that merely *claims* the number — `OtpService.verify_otp` requires
  /// `phone_verified_at IS NOT NULL`. So `created` is effectively always false.
  /// It used to be hard-coded to `true` here, which told a returning user they
  /// had just signed up.
  Future<bool> verifyOtp({required String phoneE164, required String code}) async {
    final AuthOutcome outcome = await ref
        .read(authRepositoryProvider)
        .verifyOtp(phoneE164: phoneE164, code: code);

    await _adopt(outcome.session);
    return outcome.created;
  }

  /// Persist a new session and publish it.
  ///
  /// The write happens **before** `state` moves, so a rebuild triggered by the
  /// state change can already read the tokens. The router's `redirect` reacts to
  /// `state`, which is why no screen navigates itself after signing in.
  ///
  /// It deliberately does **not** invalidate `profileProvider`: that provider
  /// watches the session, so it rebuilds on its own when `state` moves. Reaching
  /// across to invalidate it would make this file import `data_providers.dart`,
  /// which already imports this one.
  Future<void> _adopt(AuthSession session) async {
    await ref.read(tokenStoreProvider).write(session);
    state = AsyncData<AppUser?>(session.user);
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
