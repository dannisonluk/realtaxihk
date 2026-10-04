import '../core/network/wire.dart';
import 'enums.dart';

/// The account's own profile — `GET /identity/me`, and the body of every
/// `/identity/phone/*` response.
///
/// This is **not** [AppUser]. `AppUser` is the tiny block a token response
/// carries (`id`, `phone_masked`, `role`) and is deliberately kept small so the
/// login path cannot leak profile fields into a log line. This is the full
/// record, served behind a session, and it is the only place the app can learn
/// whether a number has been **proven**.
///
/// The distinction that matters: [phoneMasked] is always present, because a
/// number is required at registration; [phoneVerified] is what decides whether
/// the account may call a taxi. Many accounts may *claim* a number; exactly one
/// may *prove* it.
///
/// The raw number is never sent — `app/core/masking.py` masks it server-side, so
/// the client only ever sees `+852****1234`. There is no endpoint that returns
/// the full number, which is why the unlock screen asks the user to re-enter it.
class Profile {
  const Profile({
    required this.id,
    required this.phoneMasked,
    required this.phoneVerified,
    required this.emailVerified,
    required this.accountStatus,
    required this.role,
    required this.phoneReverifyDue,
    required this.phoneReverifyBlocked,
    this.username,
    this.givenName,
    this.familyName,
    this.gender,
    this.avatarKey,
    this.email,
    this.phoneReverifyDueAt,
    this.phoneReverifyGraceEndsAt,
    this.phoneReverifyDaysRemaining,
  });

  factory Profile.fromJson(Map<String, dynamic> json) => Profile(
    id: asString(json['id'], 'profile.id'),
    username: asStringOrNull(json['username'], 'profile.username'),
    givenName: asStringOrNull(json['given_name'], 'profile.given_name'),
    familyName: asStringOrNull(json['family_name'], 'profile.family_name'),
    gender: asStringOrNull(json['gender'], 'profile.gender'),
    avatarKey: asStringOrNull(json['avatar_key'], 'profile.avatar_key'),
    email: asStringOrNull(json['email'], 'profile.email'),
    emailVerified: asBool(json['email_verified'], 'profile.email_verified'),
    phoneMasked: asString(json['phone_masked'], 'profile.phone_masked'),
    phoneVerified: asBool(json['phone_verified'], 'profile.phone_verified'),
    phoneReverifyDueAt: asDateOrNull(
      json['phone_reverify_due_at'],
      'profile.phone_reverify_due_at',
    ),
    phoneReverifyGraceEndsAt: asDateOrNull(
      json['phone_reverify_grace_ends_at'],
      'profile.phone_reverify_grace_ends_at',
    ),
    phoneReverifyDue: asBool(json['phone_reverify_due'], 'profile.phone_reverify_due'),
    phoneReverifyBlocked: asBool(json['phone_reverify_blocked'], 'profile.phone_reverify_blocked'),
    phoneReverifyDaysRemaining: asIntOrNull(
      json['phone_reverify_days_remaining'],
      'profile.phone_reverify_days_remaining',
    ),
    accountStatus: AccountStatus.fromWire(
      asString(json['account_status'], 'profile.account_status'),
    ),
    role: UserRole.fromWire(asString(json['role'], 'profile.role')),
  );

  final String id;
  final String? username;
  final String? givenName;
  final String? familyName;

  /// One of `MALE` / `FEMALE` / `OTHER` / `UNDISCLOSED`, or null.
  ///
  /// Kept as the raw token rather than an enum: nothing in the app branches on
  /// it, it is only echoed back when completing a profile, and inventing an enum
  /// would mean a new server value could throw on a screen that does not care.
  final String? gender;

  /// An R2 object key, not a URL — the bucket and CDN host are deployment
  /// config, so a URL here would bake a hostname into the contract.
  final String? avatarKey;

  final String? email;
  final bool emailVerified;

  /// Always present. `+852****1234` — the raw number never leaves the server.
  final String phoneMasked;

  /// Whether a number has been **proven**. This is the call車 unlock.
  final bool phoneVerified;

  // --- P-4 phone re-verification, straight from `PhoneReverifyState.as_dict()`
  //
  // The server sends both the raw deadline *and* the derived decision, on
  // purpose: a reminder and a soft block are different moments about a week
  // apart, and the UI shows a banner for one and a modal for the other. A client
  // that only received the deadline would have to re-implement the policy — and
  // would get it wrong the first time the policy moved.

  /// The moment the number becomes overdue. `null` on a grandfathered row.
  final DateTime? phoneReverifyDueAt;

  /// The moment business stops working. The deadline a countdown should target.
  final DateTime? phoneReverifyGraceEndsAt;

  /// Past the deadline: show a reminder. Not yet a refusal.
  final bool phoneReverifyDue;

  /// Past the grace window: the server refuses new bookings with 403
  /// `PHONE_REVERIFY_DUE`. Reads and the profile still work.
  final bool phoneReverifyBlocked;

  /// Days until whatever happens *next* — the deadline before it, the end of
  /// grace after it. `null` once blocked, because there is no next event; the
  /// server sends null rather than a clamped `0` so the UI cannot render a
  /// countdown that never moves on an account that is already switched off.
  final int? phoneReverifyDaysRemaining;

  /// A completeness flag, not a gate. See [AccountStatus].
  final AccountStatus accountStatus;

  final UserRole role;

  /// Whether this account may start a booking.
  ///
  /// Mirrors `require_phone_verified` + `require_phone_current`: a proven number
  /// that is not past its grace window. Use it to *offer* the unlock before the
  /// user hits a 403, never as the only check — the server is the authority, and
  /// the profile can be stale.
  bool get canCallTaxi => phoneVerified && !phoneReverifyBlocked;

  /// A name to show, or null when the profile has not been filled in.
  String? get displayName {
    final String parts = <String?>[givenName, familyName].whereType<String>().join(' ');
    if (parts.isNotEmpty) {
      return parts;
    }
    return username;
  }

  /// The reminder line, or null when there is nothing to say.
  ///
  /// Lives here rather than in the two screens that show it so the policy is
  /// stated once: a reminder before the deadline, a refusal after grace. The
  /// same reasoning as `DriverStatus.labelZh`.
  String? get phoneReverifyNoticeZh {
    if (phoneReverifyBlocked) {
      return '電話驗證已逾期，需重新驗證才能繼續叫車。';
    }
    if (!phoneReverifyDue) {
      return null;
    }
    final int? days = phoneReverifyDaysRemaining;
    if (days == null) {
      return '請重新驗證電話號碼，否則將無法叫車。';
    }
    return '請於 $days 日內重新驗證電話號碼，否則將無法叫車。';
  }
}

/// `POST /identity/phone/confirm` and `POST /identity/phone/reverify`.
///
/// The wire body is the **flat** profile plus a flag — `{"verified": true, ...}`
/// and `**profile` — so both fields are read out of the same map.
///
/// [verified] is redundant with [Profile.phoneVerified], and the redundancy is
/// the point: it confirms *this request* proved the number, while
/// `phone_verified` is a state that could legitimately have been true already.
/// A screen that reported success off `phoneVerified` alone would claim credit
/// for a no-op.
class PhoneBinding {
  const PhoneBinding({required this.verified, required this.profile});

  factory PhoneBinding.fromJson(Map<String, dynamic> json) => PhoneBinding(
    verified: asBool(json['verified'], 'phone_binding.verified'),
    profile: Profile.fromJson(json),
  );

  final bool verified;
  final Profile profile;
}
