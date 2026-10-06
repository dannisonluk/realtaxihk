/// The username rule, mirrored from `app/services/auth/identity_service.py`.
///
/// **This is a hint, not the authority.** The server checks with
/// `IdentityService._assert_username` and its refusal is the one that counts.
/// This exists because the availability endpoint cannot tell the two kinds of
/// "no" apart: it answers `available: false` both for a handle someone already
/// holds and for `a` (too short) or `bad handle!` (bad characters). Reporting
/// 「已被使用」for the second is a refusal the user cannot act on, so the field
/// checks the shape itself and only asks the server about handles that pass.
///
/// The two implementations are kept in step by hand, so both spell the rules out
/// rather than paraphrasing them. If `_assert_username` changes, change this too.
///
/// Everything here works on the **normalised** handle — lower-cased and
/// trimmed — because that is what the server validates. `normalize_username`
/// runs before `_assert_username` there, so `KaMing` is accepted by the server
/// as `kaming`; rejecting the raw text for its capitals would refuse a handle
/// the API is happy with.
library;

/// `_USERNAME_RE` in `app/services/auth/identity_service.py`.
final RegExp _usernamePattern = RegExp(r'^[a-z0-9][a-z0-9._-]{2,31}$');

/// The `handle in {...}` set in `_assert_username`.
///
/// Reserved because they make a naive router or log line ambiguous — a username
/// containing a path separator would otherwise let a profile URL point
/// somewhere else.
const Set<String> _reserved = <String>{
  'admin',
  'root',
  'support',
  'system',
  'realtaxi',
  'me',
  'null',
  'undefined',
};

/// `3` in the server's regex: one leading character plus two more.
const int minUsernameLength = 3;

/// `32` in the server's regex.
const int maxUsernameLength = 32;

/// `normalize_username` in `app/services/auth/identity_service.py`.
///
/// Exported because the form has to compare the answer the availability check
/// gave against the value the field holds *now*, and the server answers about
/// the normalised form. Comparing raw text would let `KaMing` look stale
/// forever.
String normalizeUsername(String raw) => raw.trim().toLowerCase();

/// A Chinese explanation of what is wrong, or null when the handle is usable.
///
/// Deliberately returns one problem at a time, in the server's own order: the
/// form shows the first thing to fix, not a checklist that redraws as the user
/// types. Same shape, and the same reasoning, as `passwordProblem`.
String? usernameProblem(String raw) {
  final String handle = normalizeUsername(raw);
  if (handle.isEmpty) {
    return '請輸入使用者名稱。';
  }
  if (handle.length < minUsernameLength) {
    return '使用者名稱至少需要 $minUsernameLength 個字元。';
  }
  if (handle.length > maxUsernameLength) {
    return '使用者名稱最多 $maxUsernameLength 個字元。';
  }
  if (!_usernamePattern.hasMatch(handle)) {
    return '使用者名稱只可用小寫英文字母、數字、點、底線或連字號，並以字母或數字開頭。';
  }
  if (_reserved.contains(handle)) {
    return '此使用者名稱已被保留，請改用其他名稱。';
  }
  return null;
}
