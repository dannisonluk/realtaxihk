import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../router/app_router.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// Phone entry. WhatsApp OTP only — there is no password anywhere in the
/// backend, and no endpoint that returns an existing user's number.
class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final TextEditingController _phone = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _phone.dispose();
    super.dispose();
  }

  /// The server enforces `^\+852\d{8}$`, so validate the same shape here and
  /// never send a number the API will reject with a 422.
  String? get _e164 {
    final String digits = _phone.text.trim();
    return digits.length == 8 ? '+852$digits' : null;
  }

  Future<void> _sendCode() async {
    final String? phone = _e164;
    if (phone == null) {
      showInfo(context, '請輸入 8 位香港手機號碼');
      return;
    }

    setState(() => _busy = true);
    try {
      await ref.read(authControllerProvider.notifier).requestOtp(phone);
      if (!mounted) {
        return;
      }
      await context.push(Routes.otp, extra: phone);
    } on ApiException catch (e) {
      if (mounted) {
        showError(context, e);
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
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(AppTheme.space6, 64, AppTheme.space6, AppTheme.space8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              // Centred because the column stretches. The poster is the
              // wordmark, so nothing under it repeats the name; the line below
              // says what this screen wants instead.
              const Center(child: BrandLogo(size: 160)),
              const SizedBox(height: AppTheme.space6),
              Text(
                '輸入電話號碼，我們會以 WhatsApp 發送驗證碼。',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: AppTheme.space8 + 8),
              TextField(
                controller: _phone,
                autofocus: true,
                keyboardType: TextInputType.phone,
                textInputAction: TextInputAction.done,
                maxLength: 8,
                inputFormatters: <TextInputFormatter>[
                  FilteringTextInputFormatter.digitsOnly,
                  LengthLimitingTextInputFormatter(8),
                ],
                onSubmitted: (String _) => _sendCode(),
                decoration: const InputDecoration(
                  labelText: '手機號碼',
                  prefixText: '+852  ',
                  counterText: '',
                  hintText: '91234567',
                ),
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
                '新號碼會自動註冊為乘客帳戶。',
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
