import '../core/network/api_client.dart';
import '../models/auth.dart';

/// Sign-in. Owns the three doors into a session and nothing else.
///
/// The account model is split across three services server-side and this mirrors
/// it exactly, because the split is the whole point:
///
/// | what | where | credential |
/// |---|---|---|
/// | create an account | `POST /auth/register` | email + password (+ a phone *claim*) |
/// | sign in | `POST /auth/login` | email + password |
/// | sign in, secondary | `POST /auth/otp/verify` | a **already-proven** phone |
/// | prove a number | `POST /identity/phone/*` | an OTP — see `IdentityRepository` |
///
/// A phone number is no longer a login credential in its own right. It is a
/// claim at registration, a secondary login once proven, and the thing that
/// unlocks calling a taxi. Proving it lives in [IdentityRepository], not here.
///
/// Four routes sit behind the human-verification gate and take a `human_token`
/// in the request body: `register`, `login`, `auth/otp/request` and
/// `identity/phone/request`. It is **not** a `Depends`, so it cannot be applied
/// by default — every caller has to pass it. Under `APP_ENV=prod` a missing
/// token is a 403 `HUMAN_VERIFICATION_REQUIRED`; dev and test hide that,
/// because `DisabledHumanVerifier` allows everything.
class AuthRepository {
  AuthRepository(this._api);

  final ApiClient _api;

  /// `POST /auth/register` — create an account and sign in immediately (201).
  ///
  /// [phoneE164] is **required and unverified**. The number is recorded as a
  /// claim, not as proof: the new account can sign in and look around at once,
  /// and must still prove a number before it can call a taxi. That is why this
  /// does not send a code and does not wait for one.
  ///
  /// **The number is not checked for uniqueness here.** A taken *email* is
  /// disclosed (400, "email already registered"), but a taken phone is not —
  /// checking it would turn this route into an "is this number registered?"
  /// oracle. Many accounts may claim a number; exactly one may prove it, and the
  /// refusal arrives at `identity/phone/confirm` instead.
  Future<AuthOutcome> register({
    required String email,
    required String password,
    required String phoneE164,
    String? humanToken,
  }) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/auth/register',
      data: <String, dynamic>{
        'email': email,
        'password': password,
        'phone_e164': phoneE164,
        'human_token': ?humanToken,
      },
      authenticated: false,
    );
    return AuthOutcome.fromJson(json);
  }

  /// `POST /auth/login` — the primary credential.
  ///
  /// A **lockout answers 401, not 429**, and its `code` is `UNAUTHORIZED` — so a
  /// caller must not branch on the status alone. `AccountLocked` and
  /// `AccountThrottled` are distinguished server-side by exception type, and
  /// both surface here as a plain `ApiException`; the message is the only thing
  /// that says which. See `app/services/auth/account_service.py`.
  Future<AuthOutcome> login({
    required String email,
    required String password,
    String? humanToken,
  }) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/auth/login',
      data: <String, dynamic>{'email': email, 'password': password, 'human_token': ?humanToken},
      authenticated: false,
    );
    return AuthOutcome.fromJson(json);
  }

  /// `POST /auth/otp/request` — send a login code to a proven number.
  ///
  /// Rate-limited per IP **and** per number; a 429 carries `Retry-After`, and a
  /// 503 means the platform-wide ceiling was hit and the endpoint is shedding
  /// load for 600s.
  ///
  /// [humanToken] is required in production. The local rate limits run *before*
  /// the human check server-side, so a 429 here means the token was never
  /// looked at.
  Future<OtpRequestResult> requestOtp(String phoneE164, {String? humanToken}) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/auth/otp/request',
      data: <String, dynamic>{'phone_e164': phoneE164, 'human_token': ?humanToken},
      authenticated: false,
    );
    return OtpRequestResult.fromJson(json);
  }

  /// `POST /auth/otp/verify` — a **secondary** login, not the primary one.
  ///
  /// The primary credential is email + password ([register] and [login]). This
  /// endpoint survives so someone who has proven a number and lost their email
  /// can still get in with the phone in their hand.
  ///
  /// It **cannot create an account**, and it **cannot sign in an account that
  /// merely claims the number**: `OtpService.verify_otp` requires
  /// `phone_verified_at IS NOT NULL`. So `created` is effectively always false
  /// here, and a brand-new user has no path through this route — that is what
  /// [register] is for.
  Future<AuthOutcome> verifyOtp({required String phoneE164, required String code}) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/auth/otp/verify',
      data: <String, dynamic>{'phone_e164': phoneE164, 'code': code},
      authenticated: false,
    );
    return AuthOutcome.fromJson(json);
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

  /// `POST /auth/password/change` — change the signed-in account's password.
  ///
  /// The **current** password is required even though the caller already holds a
  /// valid token: a stolen session must not be enough to take the account over
  /// permanently. A wrong current password counts toward the same lockout as a
  /// failed sign-in, so five wrong guesses lock the account for
  /// `LOCKOUT_MINUTES`.
  ///
  /// **Every session is revoked, this one included.** The access token is dead
  /// when this returns, so the caller must clear the local store and send the
  /// user back to sign in — that is the point, not a side effect: if the password
  /// was changed because someone else had it, leaving other devices signed in
  /// would defeat it.
  Future<PasswordChangedResult> changePassword({
    required String currentPassword,
    required String newPassword,
  }) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/auth/password/change',
      data: <String, dynamic>{'current_password': currentPassword, 'new_password': newPassword},
    );
    return PasswordChangedResult.fromJson(json);
  }

  /// `POST /auth/password/forgot` — email a reset link.
  ///
  /// The answer is identical for a registered and an unregistered address, so
  /// the UI must show the same "if that address exists, a link is on its way"
  /// copy either way. [humanToken] is required in production.
  Future<PasswordResetRequested> forgotPassword(String email, {String? humanToken}) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/auth/password/forgot',
      data: <String, dynamic>{'email': email, 'human_token': ?humanToken},
      authenticated: false,
    );
    return PasswordResetRequested.fromJson(json);
  }

  /// `POST /auth/password/reset` — set a new password from the emailed token.
  ///
  /// The token is **single-use** and time-limited. A bad, already-used or
  /// expired token all answer the same 400 sentence on purpose, so the caller
  /// cannot tell them apart — and neither can anyone probing the route. Like
  /// [changePassword], success revokes every session.
  Future<PasswordChangedResult> resetPassword({
    required String token,
    required String newPassword,
  }) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/auth/password/reset',
      data: <String, dynamic>{'token': token, 'new_password': newPassword},
      authenticated: false,
    );
    return PasswordChangedResult.fromJson(json);
  }
}
