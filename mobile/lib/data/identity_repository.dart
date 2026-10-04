import '../core/network/api_client.dart';
import '../models/auth.dart';
import '../models/identity.dart';

/// The account's own record, and the call車 unlock.
///
/// Splitting this out of `AuthRepository` is not cosmetic. Signing in and
/// proving a number are different operations with different failure modes, and
/// the whole point of the change they belong to is that the second is no longer
/// part of the first:
///
/// * `POST /identity/phone/request` + `POST /identity/phone/confirm` are the
///   **unlock**. They are guarded by `require_active_user` and deliberately
///   **not** by `require_phone_verified` — gating the unlock behind the thing it
///   unlocks is the one way this becomes a permanent lockout.
/// * `POST /identity/phone/reverify` is the narrower P-4 remedy: it accepts only
///   the number already on the account, because re-verifying proves a number you
///   own rather than choosing a new one. Changing the number is request+confirm,
///   which says so out loud.
///
/// `phone/request` is one of the four routes behind the human-verification gate,
/// so [requestPhone] needs a `human_token` in production. `phone/confirm` and
/// `phone/reverify` are **not** gated — they are already rate-limited, and the
/// code is the credential.
class IdentityRepository {
  IdentityRepository(this._api);

  final ApiClient _api;

  /// `GET /identity/me` — the full profile.
  ///
  /// Guarded by `require_active_user`, so a deactivated account fails here. It
  /// is **not** guarded by the phone gates: an account with no proven number must
  /// be able to read its own profile, or it could never find the unlock.
  Future<Profile> me() async {
    final Map<String, dynamic> json = await _api.get('/api/v1/identity/me');
    return Profile.fromJson(json);
  }

  /// `POST /identity/phone/request` — send a code to a number to be **bound**.
  ///
  /// The caller may already have proven a *different* number; this is how the
  /// number changes. The server refuses a number another account has already
  /// verified **before** sending anything, so the platform does not pay for a
  /// WhatsApp message whose only possible outcome is a refusal at the next step.
  ///
  /// Rate-limited per IP **and** per account: the IP budget stops one host
  /// walking the number space, the account budget stops one signed-in account
  /// doing the same from a pool of addresses.
  Future<OtpRequestResult> requestPhone({required String phoneE164, String? humanToken}) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/identity/phone/request',
      data: <String, dynamic>{'phone_e164': phoneE164, 'human_token': ?humanToken},
    );
    return OtpRequestResult.fromJson(json);
  }

  /// `POST /identity/phone/confirm` — prove the code. **This is the unlock.**
  ///
  /// Returns the whole profile, not a bare flag, because proving a phone is often
  /// the step that flips `account_status` to ACTIVE — the caller has to re-render
  /// the gate it is sitting behind, and `{verified: true}` would not say so.
  ///
  /// It also acts as the P-4 re-verification when the number is the one already
  /// on the account: `confirm` pushes the next deadline out either way, because
  /// proving the number *is* proving the number.
  ///
  /// The wrong-code refusal carries `attempts_remaining` in `details`, and the
  /// code is attempt-capped at five.
  Future<PhoneBinding> confirmPhone({required String phoneE164, required String code}) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/identity/phone/confirm',
      data: <String, dynamic>{'phone_e164': phoneE164, 'code': code},
    );
    return PhoneBinding.fromJson(json);
  }

  /// `POST /identity/phone/reverify` — clear the P-4 block.
  ///
  /// Deliberately **not** guarded by `require_phone_current`: the whole point of
  /// a soft block is that an overdue account can still fix itself. Gating the
  /// remedy behind the condition would make an overdue account permanently
  /// unable to clear it, which is the one way a soft block becomes a lockout.
  ///
  /// It refuses a number that is not already on the account ("this number is not
  /// the one on your account", 400) — so pass the number the profile reports,
  /// which is why the unlock screen has to have the user re-enter it: the raw
  /// number is never sent to the client.
  Future<PhoneBinding> reverifyPhone({required String phoneE164, required String code}) async {
    final Map<String, dynamic> json = await _api.post(
      '/api/v1/identity/phone/reverify',
      data: <String, dynamic>{'phone_e164': phoneE164, 'code': code},
    );
    return PhoneBinding.fromJson(json);
  }
}
