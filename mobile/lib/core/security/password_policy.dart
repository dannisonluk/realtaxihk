/// The password policy, mirrored from `app/core/passwords.py`.
///
/// **This is a hint, not the authority.** The server checks with the real
/// implementation and its refusal is the one that counts. This exists so a
/// 12-character minimum is discoverable while typing rather than arriving as a
/// 400 after a round trip — and, on the register form, *after* the user has
/// already solved the Turnstile challenge, which is a single-use token they would
/// then have to spend a second time.
///
/// The two implementations are kept in step by hand, so both spell the rules out
/// rather than paraphrasing them. If `_assert_policy` changes, change this too.
///
/// Length is counted in **code points** (`runes`), not UTF-16 units, because
/// Python's `len` on a `str` counts code points. `'👍👍👍'.length` is 6 in Dart
/// and 3 in Python; counting code units here would let a 6-emoji password pass
/// the client and be refused by the server.
library;

/// `MIN_PASSWORD_LENGTH` in `app/core/passwords.py`.
const int minPasswordLength = 12;

/// `MAX_PASSWORD_LENGTH` in `app/core/passwords.py`.
const int maxPasswordLength = 256;

/// `_WEAK_FRAGMENTS` in `app/core/passwords.py`, lower-cased.
const List<String> _weakFragments = <String>[
  'password',
  '123456',
  'qwerty',
  'letmein',
  'welcome',
  'admin',
  'realtaxi',
  'iloveyou',
  'monkey',
  'dragon',
  'abc123',
];

/// The exact strings `_is_obviously_weak` rejects outright.
const Set<String> _banned = <String>{'0123456789ab', 'abcdefghijkl', 'aaaaaaaaaaaa'};

/// A Chinese explanation of what is wrong, or null when the password is fine.
///
/// Deliberately returns one problem at a time, in the server's own order: the
/// form shows the first thing to fix, not a checklist that redraws as the user
/// types.
String? passwordProblem(String password) {
  if (password.runes.length < minPasswordLength) {
    return '密碼至少需要 $minPasswordLength 個字元。';
  }
  if (password.runes.length > maxPasswordLength) {
    return '密碼最多 $maxPasswordLength 個字元。';
  }
  // Leading or trailing whitespace is almost always a paste accident; the server
  // rejects rather than silently trimming, so this must match.
  if (password.trim() != password) {
    return '密碼不可有開頭或結尾的空白。';
  }
  if (_isObviouslyWeak(password)) {
    return '密碼過於容易被猜到，請避免常見字詞或連續數字。';
  }
  return null;
}

bool _isObviouslyWeak(String password) {
  final String lowered = password.toLowerCase();
  if (_weakFragments.any(lowered.contains)) {
    return true;
  }
  // A single repeated character, or a straight run of digits.
  //
  // `runes`, not `split('')`: `split('')` splits by UTF-16 code unit, so
  // `'ab👍👍👍👍👍👍👍👍👍👍'` reads as four distinct "characters" instead of
  // three, and the client would accept a password the server refuses. `runes`
  // gives code points, which is what Python's `set(str)` counts.
  if (lowered.runes.toSet().length <= 3) {
    return true;
  }
  return _banned.contains(lowered);
}
