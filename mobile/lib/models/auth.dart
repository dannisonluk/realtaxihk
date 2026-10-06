import '../core/network/wire.dart';
import 'enums.dart';

/// Result of `POST /auth/otp/request` **and** `POST /identity/phone/request`.
///
/// Both routes answer with the same `OtpRequestOut` (`{sent, expires_in}`), so
/// the two callers share one model rather than each inventing a copy.
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

/// The authenticated account, as returned by `GET /api/v1/auth/me` and as the
/// `user` object inside the login/refresh responses.
///
/// The phone is **never** sent in full: `app/core/masking.py` masks it, so the
/// client only ever sees `phone_masked` (e.g. `+852****1234`). There is no
/// endpoint that returns the raw number, by design.
class AppUser {
  const AppUser({required this.id, required this.phoneMasked, required this.role});

  factory AppUser.fromJson(Map<String, dynamic> json) => AppUser(
    id: json['id'] as String,
    phoneMasked: json['phone_masked'] as String? ?? '',
    role: UserRole.fromWire(json['role'] as String),
  );

  final String id;
  final String phoneMasked;
  final UserRole role;

  Map<String, dynamic> toJson() => <String, dynamic>{
    'id': id,
    'phone_masked': phoneMasked,
    'role': role.wire,
  };
}

/// A token pair plus the account it belongs to.
///
/// The refresh token is **rotating**: every call to `/auth/refresh` returns a
/// new one, and replaying an old one makes the server revoke the whole token
/// family *and* every access token the user holds (`SEC-17`). So the store must
/// persist the replacement before the old value is used again — see
/// `ApiClient._refreshOnce`.
class AuthSession {
  const AuthSession({required this.accessToken, required this.refreshToken, required this.user});

  factory AuthSession.fromJson(Map<String, dynamic> json) => AuthSession(
    accessToken: json['access_token'] as String,
    refreshToken: json['refresh_token'] as String,
    user: AppUser.fromJson(json['user'] as Map<String, dynamic>),
  );

  final String accessToken;
  final String refreshToken;
  final AppUser user;

  /// `created` is true when the request **registered** an account — so
  /// `/auth/register` returns `true`, `/auth/login` returns `false`, and
  /// `/auth/refresh` omits the key entirely. Read it separately from the raw
  /// body; the absent case and the false case mean different things.
  static bool createdFromJson(Map<String, dynamic> json) => json['created'] as bool? ?? false;
}

/// A token pair plus the `created` flag, read together off one response body.
///
/// [AuthSession.createdFromJson] cannot be called after the fact — the raw map
/// is gone by the time a repository hands back a session — and the flag cannot
/// live on [AuthSession] itself: that object is persisted by `SecureTokenStore`,
/// and "this request registered an account" is a fact about one response, not a
/// property of the session. Dropping it entirely is what made the old
/// `AuthController.verifyOtp` hard-code `true` and tell a returning user they
/// had just signed up.
class AuthOutcome {
  const AuthOutcome({required this.session, required this.created});

  factory AuthOutcome.fromJson(Map<String, dynamic> json) =>
      AuthOutcome(session: AuthSession.fromJson(json), created: AuthSession.createdFromJson(json));

  final AuthSession session;

  /// True only when *this call* created the account.
  final bool created;
}

/// `{"ok": true, "revoked": <n>}` — the password routes that end every session.
///
/// `revoked` is an **int**: the number of refresh tokens killed. `POST
/// /auth/logout` shares this shape, and the admin logout route deliberately does
/// *not* (its `revoked` is a boolean) — which is exactly why the field is parsed
/// as a count here instead of being treated as a flag.
///
/// A password change or reset revokes **every** session, including the caller's.
/// The access token is dead by the time this returns, so a client that succeeds
/// must treat itself as signed out.
class PasswordChangedResult {
  const PasswordChangedResult({required this.ok, required this.revoked});

  factory PasswordChangedResult.fromJson(Map<String, dynamic> json) => PasswordChangedResult(
    ok: asBool(json['ok'], 'password.ok'),
    revoked: asInt(json['revoked'], 'password.revoked'),
  );

  final bool ok;

  /// How many refresh tokens were revoked.
  final int revoked;
}

/// `{"sent": true, "expires_in": <seconds>}` — `POST /auth/password/forgot`.
///
/// `sent` is true for **any** address, registered or not: the route answers
/// identically either way so it cannot be used to ask "does this person have an
/// account here?". Do not branch on it, and never tell the user their address
/// was not found — there is no field that says so.
///
/// `expiresIn` is seconds, not a timestamp, so a client with a skewed clock
/// still reads it correctly.
class PasswordResetRequested {
  const PasswordResetRequested({required this.sent, required this.expiresIn});

  factory PasswordResetRequested.fromJson(Map<String, dynamic> json) => PasswordResetRequested(
    sent: asBool(json['sent'], 'password_forgot.sent'),
    expiresIn: asInt(json['expires_in'], 'password_forgot.expires_in'),
  );

  final bool sent;

  /// Seconds the emailed link stays valid.
  final int expiresIn;
}
