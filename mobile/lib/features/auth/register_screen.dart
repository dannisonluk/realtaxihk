import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/human/turnstile.dart';
import '../../core/network/api_exception.dart';
import '../../core/phone.dart';
import '../../core/security/password_policy.dart';
import '../../core/theme/app_theme.dart';
import '../../router/app_router.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';
import 'phone_field.dart';

/// Create an account: email, password, and a phone number.
///
/// Three things about this form are the point of the whole change it belongs to:
///
///  1. **The phone number is required but not verified here.** It is recorded as
///     a *claim*. No code is sent, and nothing on this screen waits for one. A
///     brand-new account can sign in and look around immediately, and must still
///     prove a number before it can call a taxi.
///  2. **No "is this number taken?" check.** The server does not check either,
///     on purpose: answering it would turn registration into an "is this number
///     registered?" oracle. Many accounts may claim a number; exactly one may
///     prove it, and that refusal arrives later, at `identity/phone/confirm`.
///  3. **A taken *email* is refused, out loud.** That one is disclosed, because
///     the user has to be told they already have an account.
///
/// On success this screen does not navigate: the controller publishes the
/// session and the router's `redirect` decides where a passenger lands. One place
/// decides, so a role change cannot strand anyone.
class RegisterScreen extends ConsumerStatefulWidget {
  const RegisterScreen({super.key});

  @override
  ConsumerState<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends ConsumerState<RegisterScreen> {
  final TextEditingController _email = TextEditingController();
  final TextEditingController _password = TextEditingController();
  final TextEditingController _confirm = TextEditingController();
  final TextEditingController _phone = TextEditingController();
  final GlobalKey<TurnstileChallengeState> _turnstile = GlobalKey<TurnstileChallengeState>();

  String? _humanToken;
  bool _busy = false;
  bool _showPassword = false;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _confirm.dispose();
    _phone.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final String email = _email.text.trim();
    // A completeness check, not a policy mirror: the server validates the address
    // with `assert_email` and its refusal is the one that counts. This only stops
    // the obvious "field is empty" round trip.
    if (!email.contains('@') || email.startsWith('@') || email.endsWith('@')) {
      showInfo(context, '請輸入有效的電郵地址');
      return;
    }
    final String? problem = passwordProblem(_password.text);
    if (problem != null) {
      showInfo(context, problem);
      return;
    }
    if (_password.text != _confirm.text) {
      showInfo(context, '兩次輸入的密碼不一致');
      return;
    }
    final String? phone = hkPhoneToE164(_phone.text);
    if (phone == null) {
      showInfo(context, '請輸入 8 位香港手機號碼');
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
          .register(
            email: email,
            password: _password.text,
            phoneE164: phone,
            humanToken: _humanToken,
          );
      // No navigation: the router's redirect reacts to the new session.
    } on ApiException catch (e) {
      if (mounted) {
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
  ///
  /// Without this the box would still read "Success" while the form held a spent
  /// token, and a retry would be refused with a reason the user cannot see.
  void _spendHumanToken() {
    _humanToken = null;
    _turnstile.currentState?.reset();
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(title: const Text('建立帳戶')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(
            AppTheme.space6,
            AppTheme.space6,
            AppTheme.space6,
            AppTheme.space8,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Text(
                '以電郵及密碼建立帳戶。電話號碼於註冊時只需填寫，之後才需驗證——驗證是用來解鎖「call車」功能的。',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
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
                textInputAction: TextInputAction.next,
                autocorrect: false,
                enableSuggestions: false,
                decoration: InputDecoration(
                  labelText: '密碼',
                  helperText: '至少 $minPasswordLength 個字元',
                  suffixIcon: IconButton(
                    onPressed: () => setState(() => _showPassword = !_showPassword),
                    tooltip: _showPassword ? '隱藏密碼' : '顯示密碼',
                    icon: Icon(_showPassword ? Icons.visibility_off : Icons.visibility),
                  ),
                ),
              ),
              const SizedBox(height: AppTheme.space4),
              TextField(
                controller: _confirm,
                obscureText: !_showPassword,
                textInputAction: TextInputAction.next,
                autocorrect: false,
                enableSuggestions: false,
                decoration: const InputDecoration(labelText: '確認密碼'),
              ),
              const SizedBox(height: AppTheme.space4),
              HkPhoneField(
                controller: _phone,
                label: '手機號碼（未驗證）',
                helperText: '註冊後才需驗證，用於解鎖 call車',
                onSubmitted: (String _) => _submit(),
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
                    : const Text('建立帳戶'),
              ),
              const SizedBox(height: AppTheme.space3),
              TextButton(
                // Guarded because this screen is also reachable by deep link, and
                // on a stack with nothing under it `pop()` throws.
                onPressed: _busy
                    ? null
                    : () => context.canPop() ? context.pop() : context.go(Routes.login),
                child: const Text('已有帳戶？返回登入'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
