import 'enums.dart';

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
