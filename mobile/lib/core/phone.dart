/// Hong Kong mobile numbers, as the API accepts them.
///
/// The server enforces `HK_PHONE_PATTERN` (`app/core/phone.py`) on every phone
/// field, so the client validates the same shape and never sends a number the API
/// will reject with a 422. Kept as a pure library so `tool/run_tests.dart` can
/// exercise it on the plain VM.
library;

/// `HK_PHONE_PATTERN` in `app/core/phone.py`.
final RegExp hkPhonePattern = RegExp(r'^\+852\d{8}$');

/// `'91234567'` → `'+85291234567'`, or null when the input is not 8 digits.
///
/// Returns null rather than throwing: this is called from a form's submit
/// handler, where "not filled in yet" is a normal state and an exception would be
/// a crash on a typo.
///
/// There is no case for a landline or a non-HK number — the platform is a Hong
/// Kong taxi broker, and the server has no pattern for anything else. Accepting
/// one here would only move the refusal to the next screen.
String? hkPhoneToE164(String input) {
  final String digits = input.trim();
  if (digits.length != 8) {
    return null;
  }
  final String e164 = '+852$digits';
  return hkPhonePattern.hasMatch(e164) ? e164 : null;
}

/// The 8 digits of an E.164 HK number, or the input unchanged.
///
/// Used to seed a field from a stored number. The app never receives a full
/// number from the server — only `+852****1234` — so in practice this is only
/// reachable from a value the user typed earlier in the same session.
String hkPhoneDigits(String e164) {
  final String trimmed = e164.trim();
  return trimmed.startsWith('+852') ? trimmed.substring(4) : trimmed;
}
