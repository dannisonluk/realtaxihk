/// The widget half of the Cloudflare Turnstile seam.
///
/// Four routes are behind the human-verification gate and take a `human_token`
/// **in the request body**: `POST /auth/register`, `POST /auth/login`,
/// `POST /auth/otp/request` and `POST /identity/phone/request`. It is not a
/// `Depends`, so the server cannot apply it by default — every caller has to pass
/// one. Under `APP_ENV=prod` a missing token is a 403
/// `HUMAN_VERIFICATION_REQUIRED`.
///
/// Cloudflare ships a browser widget and nothing else: there is no native SDK
/// that mints a token the server will accept, so the challenge is rendered by a
/// WebView. Three consequences, none of them fixable here:
///
///  1. **It is a network round trip before every gated call.** The token is
///     single-use and expires, so it is fetched fresh per attempt rather than
///     cached.
///  2. **It cannot be unit-tested in this sandbox.** `WebViewWidget` needs a
///     platform view and `flutter test` does not run here at all, so only the
///     protocol and the build-mode decision are covered — those live in
///     `turnstile_protocol.dart`, which does not import Flutter.
///  3. **The site key is baked in at build time.** No endpoint serves it (see
///     `AppConfig.turnstileSiteKey`), so rotating the key means shipping a build.
library;

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';

import '../config/app_config.dart';
import '../theme/app_theme.dart';

// Re-exported so a screen imports one file for the whole seam. The protocol half
// lives apart only so `tool/run_tests.dart` can reach it without dragging in
// Flutter — see the header of `turnstile_protocol.dart`.
export 'turnstile_protocol.dart';
import 'turnstile_protocol.dart';

/// The current build's mode.
///
/// [kReleaseMode] is a compile-time constant, so the branch that cannot happen is
/// tree-shaken out of a release build.
TurnstileMode get turnstileMode => resolveTurnstileMode(
  siteKeyPresent: AppConfig.humanVerificationConfigured,
  releaseMode: kReleaseMode,
);

/// The challenge, rendered inline in whatever form the user is filling in.
///
/// The screen owns the token: this widget reports one and then forgets it. That
/// is deliberate — a token belongs to one submit attempt, and a widget that
/// cached it would hand out a spent token on the second try.
///
/// **A token is single-use, so a failed submit must reset the challenge.** Hold
/// the widget by a `GlobalKey<TurnstileChallengeState>` and call [reset] after
/// any attempt that reached the server; otherwise the box still reads "Success"
/// while the form holds a spent token, and the next submit is refused for a
/// reason the user cannot see.
///
/// It renders **nothing** in [TurnstileMode.disabled], so a dev build's form has
/// no empty box in it. In [TurnstileMode.misconfigured] it renders a visible
/// refusal and reports the fault, because the alternative is a sign-in form that
/// silently cannot work.
class TurnstileChallenge extends StatefulWidget {
  const TurnstileChallenge({required this.onToken, this.onStale, this.onError, super.key});

  /// A fresh, usable token.
  final ValueChanged<String> onToken;

  /// The previously delivered token expired or timed out. Drop it.
  final VoidCallback? onStale;

  /// The challenge failed. [code] is Cloudflare's.
  final ValueChanged<String>? onError;

  @override
  State<TurnstileChallenge> createState() => TurnstileChallengeState();
}

/// Public so a screen can hold it by key and call [reset].
class TurnstileChallengeState extends State<TurnstileChallenge> {
  WebViewController? _webView;

  /// Ask Cloudflare for a new token, discarding the current one.
  ///
  /// Safe to call in any mode and at any time: with no WebView (dev, or a
  /// misconfigured build) there is nothing to reset and the token the screen
  /// holds was never real.
  void reset() {
    final WebViewController? controller = _webView;
    if (controller == null) {
      return;
    }
    // `turnstile.reset()` with no argument resets every widget on the page, which
    // is the only one this WebView hosts.
    unawaited(controller.runJavaScript('turnstile.reset()'));
  }

  @override
  void initState() {
    super.initState();
    switch (turnstileMode) {
      case TurnstileMode.disabled:
        break;
      case TurnstileMode.misconfigured:
        // Reported as well as rendered: a release build that cannot sign anyone
        // in is a build fault, and a red box on a phone is not a bug report.
        FlutterError.reportError(
          FlutterErrorDetails(
            exception: StateError(
              'TURNSTILE_SITE_KEY was not supplied at build time, so every gated '
              'route will answer 403. Rebuild with '
              '--dart-define=TURNSTILE_SITE_KEY=0x4AAAAAAA...',
            ),
            library: 'hkfastdc.turnstile',
          ),
        );
      case TurnstileMode.enabled:
        _webView = _buildController();
    }
  }

  WebViewController _buildController() {
    final WebViewController controller = WebViewController();
    unawaited(controller.setJavaScriptMode(JavaScriptMode.unrestricted));
    // Transparent, so the widget sits on the card rather than on a white slab in
    // dark mode.
    unawaited(controller.setBackgroundColor(const Color(0x00000000)));
    unawaited(
      controller.addJavaScriptChannel(
        'Turnstile',
        onMessageReceived: (JavaScriptMessage message) => _onMessage(message.message),
      ),
    );
    unawaited(
      controller.loadHtmlString(
        turnstileHtml(AppConfig.turnstileSiteKey),
        // Turnstile mints a token *for a hostname*, and this decides which one.
        // It must be a domain on the site key's allowlist — Cloudflare validates
        // the origin, not the caller.
        baseUrl: AppConfig.turnstileBaseUrl,
      ),
    );
    return controller;
  }

  void _onMessage(String raw) {
    final TurnstileSignal? signal = parseTurnstileSignal(raw);
    if (signal == null || !mounted) {
      return;
    }
    switch (signal) {
      case TurnstileToken(:final String token):
        widget.onToken(token);
      case TurnstileStale():
        widget.onStale?.call();
      case TurnstileError(:final String code):
        widget.onError?.call(code);
      case TurnstileUnknown():
        break;
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    switch (turnstileMode) {
      case TurnstileMode.disabled:
        return const SizedBox.shrink();
      case TurnstileMode.misconfigured:
        return Container(
          padding: const EdgeInsets.all(AppTheme.space3),
          decoration: BoxDecoration(
            color: theme.colorScheme.errorContainer,
            borderRadius: BorderRadius.circular(AppTheme.radiusCard),
          ),
          child: Row(
            children: <Widget>[
              Icon(Icons.report_gmailerrorred, color: theme.colorScheme.onErrorContainer),
              const SizedBox(width: AppTheme.space3),
              Expanded(
                child: Text(
                  '真人驗證未設定，此版本無法登入。請以 '
                  '--dart-define=TURNSTILE_SITE_KEY=… 重新建置。',
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onErrorContainer,
                  ),
                ),
              ),
            ],
          ),
        );
      case TurnstileMode.enabled:
        // A WebView with no height renders nothing at all, so this is a real
        // constraint rather than styling: Turnstile's 'flexible' size is 65dp.
        return SizedBox(height: 78, child: WebViewWidget(controller: _webView!));
    }
  }
}
