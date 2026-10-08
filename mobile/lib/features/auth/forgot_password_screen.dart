import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/human/turnstile.dart';
import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// Ask for a password-reset link by email — the pre-auth half of the flow.
///
/// **The screen must not say whether the address exists.** The route answers
/// identically for a registered and an unregistered address on purpose, so that
/// it cannot be used as a public "does this person have an account here?"
/// lookup. The confirmation copy is therefore conditional-sounding and
/// unconditional in fact — do not "improve" it into "we sent you an email",
/// and do not branch on [PasswordResetRequested.sent] either, because it is
/// always true.
///
/// The link in the email can open the **web** page
/// (`{public_base_url}/reset-password`) or the in-app App Link route; the mobile
/// router and cold-start parser both handle the reset path.
class ForgotPasswordScreen extends ConsumerStatefulWidget {
  const ForgotPasswordScreen({super.key});

  @override
  ConsumerState<ForgotPasswordScreen> createState() => _ForgotPasswordScreenState();
}

class _ForgotPasswordScreenState extends ConsumerState<ForgotPasswordScreen> {
  final TextEditingController _email = TextEditingController();
  final GlobalKey<TurnstileChallengeState> _turnstile = GlobalKey<TurnstileChallengeState>();

  String? _humanToken;
  bool _busy = false;
  bool _sent = false;

  @override
  void dispose() {
    _email.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final String email = _email.text.trim();
    if (email.isEmpty) {
      showInfo(context, '請輸入電郵');
      return;
    }
    if (turnstileMode == TurnstileMode.enabled && _humanToken == null) {
      showInfo(context, '請先完成真人驗證');
      return;
    }

    setState(() => _busy = true);
    try {
      await ref.read(authRepositoryProvider).forgotPassword(email, humanToken: _humanToken);
      if (mounted) {
        setState(() => _sent = true);
      }
    } on ApiException catch (e) {
      if (mounted) {
        showError(context, e);
        // A Turnstile token is single-use and this request already spent it.
        _humanToken = null;
        _turnstile.currentState?.reset();
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(title: const Text('忘記密碼')),
      body: ListView(
        padding: const EdgeInsets.all(AppTheme.space4),
        children: <Widget>[
          if (_sent) ...<Widget>[
            Card(
              child: Padding(
                padding: const EdgeInsets.all(AppTheme.space4),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text('請檢查你的電郵', style: theme.textTheme.titleMedium),
                    const SizedBox(height: AppTheme.space2),
                    Text(
                      '如果 ${_email.text.trim()} 已註冊，我們已寄出重設連結。'
                      '請在電郵中開啟該連結並設定新密碼。連結有時效，逾時需重新申請。',
                      style: theme.textTheme.bodyMedium,
                    ),
                    const SizedBox(height: AppTheme.space2),
                    Text(
                      '沒有收到？請檢查垃圾郵件，或稍後再試一次。',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: AppTheme.space4),
            OutlinedButton(
              onPressed: _busy ? null : () => setState(() => _sent = false),
              child: const Text('重新輸入電郵'),
            ),
          ] else ...<Widget>[
            Text('輸入你註冊時使用的電郵，我們會寄出重設連結。', style: theme.textTheme.bodyMedium),
            const SizedBox(height: AppTheme.space4),
            TextField(
              controller: _email,
              autofocus: true,
              keyboardType: TextInputType.emailAddress,
              autocorrect: false,
              decoration: const InputDecoration(labelText: '電郵', hintText: 'you@example.com'),
            ),
            const SizedBox(height: AppTheme.space3),
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
                  : const Text('寄出重設連結'),
            ),
          ],
        ],
      ),
    );
  }
}
