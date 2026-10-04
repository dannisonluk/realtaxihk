import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/human/turnstile.dart';
import '../../core/network/api_exception.dart';
import '../../core/phone.dart';
import '../../core/theme/app_theme.dart';
import '../../router/app_router.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';
import 'phone_field.dart';

/// Phone entry — the **secondary** login.
///
/// Only an **already-verified** number can pass. `OtpService.verify_otp` requires
/// `phone_verified_at IS NOT NULL`, so an unproven number is refused rather than
/// turned into a new account. A brand-new user has no path through this screen;
/// that is what [/login/register] is for.
///
/// `POST /auth/otp/request` is one of the four routes behind the
/// human-verification gate, so the challenge is rendered here rather than on the
/// code screen: the send is the half that costs a billed WhatsApp message.
class PhoneLoginScreen extends ConsumerStatefulWidget {
  const PhoneLoginScreen({super.key});

  @override
  ConsumerState<PhoneLoginScreen> createState() => _PhoneLoginScreenState();
}

class _PhoneLoginScreenState extends ConsumerState<PhoneLoginScreen> {
  final TextEditingController _phone = TextEditingController();
  final GlobalKey<TurnstileChallengeState> _turnstile = GlobalKey<TurnstileChallengeState>();

  String? _humanToken;
  bool _busy = false;

  @override
  void dispose() {
    _phone.dispose();
    super.dispose();
  }

  Future<void> _sendCode() async {
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
      await ref.read(authControllerProvider.notifier).requestOtp(phone, humanToken: _humanToken);
      if (!mounted) {
        return;
      }
      // The token was spent on the send. If the user comes back from the code
      // screen and tries again, the challenge must have been reset for them.
      _spendHumanToken();
      await context.push(Routes.otp, extra: phone);
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

  void _spendHumanToken() {
    _humanToken = null;
    _turnstile.currentState?.reset();
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(title: const Text('以電話號碼登入')),
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
                '輸入已驗證的電話號碼，我們會以 WhatsApp 發送驗證碼。',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: AppTheme.space6),
              HkPhoneField(
                controller: _phone,
                autofocus: true,
                onSubmitted: (String _) => _sendCode(),
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
                onPressed: _busy ? null : _sendCode,
                child: _busy
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('發送驗證碼'),
              ),
              const SizedBox(height: AppTheme.space4),
              Text(
                '只支援已驗證的號碼登入；新號碼不會在此建立帳戶。',
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
