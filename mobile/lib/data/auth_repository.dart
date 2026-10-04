import '../core/network/api_client.dart';
import '../core/network/wire.dart';
import '../models/auth.dart';

/// Result of `POST /api/v1/auth/otp/request`.
///
/// There is no `dev_code` field, by design (`SEC-02`): the code leaves the
/// server exactly once, through the WhatsApp provider. In dev the provider logs
/// it and the fixed code is `123456`; a client must never expect to receive it
/// in the response body.
class OtpRequestResult {
  const OtpRequestResult({required this.sent, required this.expiresIn});

  factory OtpRequestResult.fromJson(Map<String, dynamic> json) => OtpRequestResult(
    sent: asBool(json['sent'], 'otp.sent'),
    expiresIn: asInt(json['expires_in'], 'otp.expires_in'),
  );

  final bool sent;

  /// Seconds the code stays valid.
  final int expiresIn;
}

class AuthRepository {
  AuthRepository(this._api);

  final ApiClient _api;

  /// `POST /auth/otp/request`. Public. Rate-limited per IP **and** per number;
  /// a 429 carries `Retry-After`, and a 503 means the platform-wide ceiling was
  /// hit and the endpoint is shedding load for 600s.
  ///
  /// ⚠️ **Not production-ready.** The endpoint sits behind the human-verification
  /// gate (`assert_human` in `app/api/auth.py`) and this call sends no
  /// `human_token`, so under `APP_ENV=prod` it is refused with 403. Dev and test
  /// hide it because `DisabledHumanVerifier` allows everything. Needs a Turnstile
  /// token before the app can log in against production.
  Future<OtpRequestResult> requestOtp(String phoneE164) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/auth/otp/request',
      data: <String, dynamic>{'phone_e164': phoneE164},
      authenticated: false,
    );
    return OtpRequestResult.fromJson(json);
  }

  /// `POST /auth/otp/verify` — a **secondary** login, not the primary one.
  ///
  /// The primary credential is now email + password (`POST /auth/register` and
  /// `POST /auth/login`, neither of which this app implements yet). This endpoint
  /// survives so a driver who has proven a number and lost their email can still
  /// get in with the phone in their hand.
  ///
  /// It **cannot create an account**, and it **cannot sign in an account that
  /// merely claims the number**: `OtpService.verify_otp` requires
  /// `phone_verified_at IS NOT NULL`. So `created` is effectively always false
  /// here, and a brand-new user has no path into this app at all — that is the
  /// missing registration screen, tracked as a known gap.
  Future<AuthSession> verifyOtp({required String phoneE164, required String code}) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/auth/otp/verify',
      data: <String, dynamic>{'phone_e164': phoneE164, 'code': code},
      authenticated: false,
    );
    return AuthSession.fromJson(json);
  }

  /// `GET /auth/me` — re-validates the stored session on cold start.
  ///
  /// This is a live DB read behind `require_active_user`, so a deactivated
  /// account fails here even with a valid token.
  Future<AppUser> me() async {
    final Map<String, dynamic> json = await _api.get('/api/v1/auth/me');
    return AppUser.fromJson(json);
  }

  /// `POST /auth/logout` — revokes every refresh token for the user *and* bumps
  /// the revocation epoch so access tokens die too (`SEC-18`). The local store
  /// must still be cleared by the caller: the server cannot revoke a token it
  /// never sees again.
  Future<void> logout() async {
    await _api.post('/api/v1/auth/logout');
  }
}
