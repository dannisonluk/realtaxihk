/// The pure half of the Cloudflare Turnstile seam: the build-mode decision, the
/// widget's message protocol, and the page it renders.
///
/// **Why this is a separate file from `turnstile.dart`.** `tool/run_tests.dart`
/// runs on the plain Dart VM — `flutter test` cannot start on this machine — so
/// anything it imports must not reach `package:flutter`. `WebViewWidget` does,
/// and so does `AppConfig` (it reads `kIsWeb` and `defaultTargetPlatform`). So
/// the part that is worth asserting on — "does an absent site key behave
/// differently in release?", "is a malformed widget message ignored or fatal?" —
/// lives here, where it can be exercised, and the widget lives next door.
library;

import 'dart:convert';

/// What a build should do about the challenge.
enum TurnstileMode {
  /// Render nothing and send no token.
  ///
  /// This is the normal **development** state: `DisabledHumanVerifier` allows
  /// everything server-side, so a dev build without a Cloudflare key still works
  /// end to end. It is deliberately not the release state — see below.
  disabled,

  /// Render the widget and send whatever token it mints.
  enabled,

  /// A release build with no site key. Every gated route will answer 403, so this
  /// must not be silent: the user would otherwise sit in front of a sign-in form
  /// that can never succeed, with no explanation.
  misconfigured,
}

/// The decision, as a pure function so it can be tested without a widget tree.
///
/// The asymmetry is the whole point: **an absent key is tolerated in dev and
/// refused in release.** Development must not need a Cloudflare account, and a
/// release must not ship a sign-in form that cannot work.
///
/// [releaseMode] is passed in rather than read from `kReleaseMode` so this stays
/// Flutter-free — the caller in `turnstile.dart` supplies the constant.
TurnstileMode resolveTurnstileMode({required bool siteKeyPresent, required bool releaseMode}) {
  if (siteKeyPresent) {
    return TurnstileMode.enabled;
  }
  return releaseMode ? TurnstileMode.misconfigured : TurnstileMode.disabled;
}

/// One decoded message from the widget's JavaScript.
sealed class TurnstileSignal {
  const TurnstileSignal();
}

/// The challenge was solved. Single-use, and it expires — do not store it.
class TurnstileToken extends TurnstileSignal {
  const TurnstileToken(this.token);

  final String token;
}

/// The token is no longer usable: Cloudflare reported it expired, or the widget
/// timed out waiting for a solve. Any token the screen is holding must be
/// dropped, or the next call will be refused for a reason the user cannot see.
class TurnstileStale extends TurnstileSignal {
  const TurnstileStale();
}

/// The challenge itself failed. [code] is Cloudflare's own error code, kept
/// verbatim because it is the only thing that distinguishes "your key is wrong"
/// from "your network is down".
class TurnstileError extends TurnstileSignal {
  const TurnstileError(this.code);

  final String code;
}

/// An event this client does not know. Not an error — Cloudflare adds events over
/// time, and a new one must not be treated as a failure.
class TurnstileUnknown extends TurnstileSignal {
  const TurnstileUnknown(this.event);

  final String event;
}

/// Decodes one `Turnstile.postMessage(...)` payload.
///
/// Returns null for anything that is not a usable message — malformed JSON, a
/// non-object, a missing `event`, or a `token` event carrying an empty token.
/// Null means "ignore", never "fail", because a widget that emitted noise must
/// not be able to break a sign-in.
TurnstileSignal? parseTurnstileSignal(String raw) {
  final Object? decoded;
  try {
    decoded = jsonDecode(raw);
  } on FormatException {
    return null;
  }
  if (decoded is! Map<String, dynamic>) {
    return null;
  }
  final Object? event = decoded['event'];
  if (event is! String) {
    return null;
  }
  switch (event) {
    case 'token':
      final Object? token = decoded['token'];
      return token is String && token.isNotEmpty ? TurnstileToken(token) : null;
    case 'expired':
    case 'timeout':
      return const TurnstileStale();
    case 'error':
      final Object? code = decoded['code'];
      return TurnstileError(code is String ? code : 'unknown');
    default:
      return TurnstileUnknown(event);
  }
}

/// The page the WebView renders.
///
/// Inline rather than an asset: it is a dozen lines of glue, and a new file under
/// `assets/` would need a `pubspec.yaml` entry — the exact trap that once left
/// every branding image out of the APK, because Dart tooling cannot see the
/// difference between a file that is bundled and one that is merely present.
String turnstileHtml(String siteKey) =>
    '''
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<style>
  html, body { margin: 0; padding: 0; background: transparent; }
  #widget { display: block; width: 100%; }
</style>
<script
  src="https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onloadTurnstile"
  async defer></script>
</head>
<body>
<div id="widget"></div>
<script>
  function post(event, extra) {
    var payload = { event: event };
    for (var key in extra) { payload[key] = extra[key]; }
    Turnstile.postMessage(JSON.stringify(payload));
  }
  function onloadTurnstile() {
    turnstile.render('#widget', {
      sitekey: '$siteKey',
      // 'always' rather than 'managed': a managed widget decides on its own when
      // to challenge, so a silent pass gives the user nothing to look at while
      // the submit button stays disabled. Always shows the box, which makes
      // "waiting for the challenge" a visible state instead of an apparently
      // dead button.
      appearance: 'always',
      theme: 'auto',
      // Full width of the card, 65dp tall. 'normal' is a fixed 300dp, which
      // overflows a small phone.
      size: 'flexible',
      callback: function (token) { post('token', { token: token }); },
      'error-callback': function (code) { post('error', { code: String(code) }); },
      'expired-callback': function () { post('expired', {}); },
      'timeout-callback': function () { post('timeout', {}); }
    });
  }
</script>
</body>
</html>
''';
