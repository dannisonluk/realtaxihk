import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/human/turnstile.dart';
import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../router/app_router.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// Sign in with an email and a password — the **primary** door.
///
/// There are three ways into a session and this is the one that owns the account:
///
///  * email + password (here) — the primary credential;
///  * a phone number + a code ([/login/phone]) — secondary, and only for a number
///    that has already been **proven**;
///  * registration ([/login/register]) — which creates the account and signs in.
///
/// A phone number is no longer a login credential in its own right. It is a claim
/// at registration, a secondary login once proven, and the thing that unlocks
/// calling a taxi. Proving it lives on the phone screen in the account area, not
/// here, and it is **not** a precondition for having an account.
///
/// On success this screen does not navigate: the controller publishes the session
/// and the router's `redirect` sends the account wherever its role belongs.
class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final TextEditingController _email = TextEditingController();
  final TextEditingController _password = TextEditingController();
  final GlobalKey<TurnstileChallengeState> _turnstile = GlobalKey<TurnstileChallengeState>();

  String? _humanToken;
  bool _busy = false;
  bool _showPassword = false;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final String email = _email.text.trim();
    if (email.isEmpty || _password.text.isEmpty) {
      showInfo(context, '請輸入電郵及密碼');
      return;
    }
    if (turnstileMode == TurnstileMode.enabled && _humanToken == null) {
      showInfo(context, '請先完成真人驗證');
      return;
    }

    setState(() => _busy = true);
    try {
      await ref
          .read(authControllerProvider.notifier)
          .login(email: email, password: _password.text, humanToken: _humanToken);
      // No navigation: the router's redirect reacts to the new session.
    } on ApiException catch (e) {
      if (mounted) {
        // The server's own sentence. A lockout arrives here as a 401 with a
        // message that says so — this screen must not replace it with "wrong
        // password", which is what a bare status check would produce.
        showError(context, e);
        _spendHumanToken();
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  /// A Turnstile token is single-use, and this request already spent it.
  void _spendHumanToken() {
    _humanToken = null;
    _turnstile.currentState?.reset();
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(AppTheme.space6, 48, AppTheme.space6, AppTheme.space8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              // Centred because the column stretches. The poster is the wordmark,
              // so nothing under it repeats the name.
              const Center(child: BrandLogo(size: 140)),
              const SizedBox(height: AppTheme.space6),
              TextField(
                controller: _email,
                autofocus: true,
                keyboardType: TextInputType.emailAddress,
                textInputAction: TextInputAction.next,
                autocorrect: false,
                decoration: const InputDecoration(labelText: '電郵', hintText: 'you@example.com'),
              ),
              const SizedBox(height: AppTheme.space4),
              TextField(
                controller: _password,
                obscureText: !_showPassword,
                textInputAction: TextInputAction.done,
                autocorrect: false,
                enableSuggestions: false,
                onSubmitted: (String _) => _submit(),
                decoration: InputDecoration(
                  labelText: '密碼',
                  suffixIcon: IconButton(
                    onPressed: () => setState(() => _showPassword = !_showPassword),
                    tooltip: _showPassword ? '隱藏密碼' : '顯示密碼',
                    icon: Icon(_showPassword ? Icons.visibility_off : Icons.visibility),
                  ),
                ),
              ),
              const SizedBox(height: AppTheme.space2),
              TurnstileChallenge(
                key: _turnstile,
                onToken: (String token) => _humanToken = token,
                onStale: () => _humanToken = null,
                onError: (String code) {
                  if (mounted) {
                    showInfo(context, '真人驗證失敗（$code），請重試。');
                  }
                },
              ),
              const SizedBox(height: AppTheme.space6),
              FilledButton(
                onPressed: _busy ? null : _submit,
                child: _busy
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('登入'),
              ),
              const SizedBox(height: AppTheme.space3),
              TextButton(
                onPressed: _busy ? null : () => context.push(Routes.register),
                child: const Text('還沒有帳戶？建立帳戶'),
              ),
              const Divider(height: AppTheme.space8),
              TextButton(
                onPressed: _busy ? null : () => context.push(Routes.phoneLogin),
                child: const Text('以已驗證的電話號碼登入'),
              ),
              const SizedBox(height: AppTheme.space2),
              Text(
                '只支援已驗證的號碼；新號碼請先建立帳戶。',
                textAlign: TextAlign.center,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
