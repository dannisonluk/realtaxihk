import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_exception.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// Six-digit code entry.
///
/// On success the controller sets the session and the router's `redirect` takes
/// over — this screen never navigates itself, so there is one place that decides
/// where a given role lands.
///
/// The code is **never** returned in the API response (`SEC-02`); it arrives only
/// via WhatsApp. In a dev environment with `ALLOW_DEV_OTP=true` the server uses
/// the fixed code `123456` and logs it through the notify provider — see
/// mobile/README.md.
class OtpScreen extends ConsumerStatefulWidget {
  const OtpScreen({required this.phoneE164, super.key});

  final String phoneE164;

  @override
  ConsumerState<OtpScreen> createState() => _OtpScreenState();
}

class _OtpScreenState extends ConsumerState<OtpScreen> {
  final TextEditingController _code = TextEditingController();
  bool _busy = false;

  /// The OTP endpoint is rate-limited per IP (60/min) and per number, so a
  /// resend button with no cooldown is a way to lock yourself out.
  int _cooldown = 0;
  Timer? _timer;

  @override
  void dispose() {
    _timer?.cancel();
    _code.dispose();
    super.dispose();
  }

  void _startCooldown() {
    _timer?.cancel();
    setState(() => _cooldown = 60);
    _timer = Timer.periodic(const Duration(seconds: 1), (Timer timer) {
      if (!mounted) {
        timer.cancel();
        return;
      }
      setState(() => _cooldown--);
      if (_cooldown <= 0) {
        timer.cancel();
      }
    });
  }

  Future<void> _verify() async {
    if (_code.text.length != 6) {
      showInfo(context, '請輸入 6 位驗證碼');
      return;
    }
    setState(() => _busy = true);
    try {
      await ref
          .read(authControllerProvider.notifier)
          .verifyOtp(phoneE164: widget.phoneE164, code: _code.text);
      // No navigation here: the router's redirect reacts to the new session.
    } on ApiException catch (e) {
      if (mounted) {
        showError(context, e);
        _code.clear();
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _resend() async {
    setState(() => _busy = true);
    try {
      await ref.read(authControllerProvider.notifier).requestOtp(widget.phoneE164);
      if (mounted) {
        _startCooldown();
        showInfo(context, '驗證碼已重新發送');
      }
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
      appBar: AppBar(title: const Text('輸入驗證碼')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(24, 32, 24, 32),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Text(
                '驗證碼已發送至 ${widget.phoneE164}',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 28),
              TextField(
                controller: _code,
                autofocus: true,
                keyboardType: TextInputType.number,
                textAlign: TextAlign.center,
                maxLength: 6,
                style: theme.textTheme.headlineSmall?.copyWith(letterSpacing: 8),
                inputFormatters: <TextInputFormatter>[
                  FilteringTextInputFormatter.digitsOnly,
                  LengthLimitingTextInputFormatter(6),
                ],
                onSubmitted: (String _) => _verify(),
                decoration: const InputDecoration(
                  labelText: '驗證碼',
                  counterText: '',
                  hintText: '000000',
                ),
              ),
              const SizedBox(height: 24),
              FilledButton(
                onPressed: _busy ? null : _verify,
                child: _busy
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('確認'),
              ),
              const SizedBox(height: 12),
              TextButton(
                onPressed: (_busy || _cooldown > 0) ? null : _resend,
                child: Text(_cooldown > 0 ? '重新發送（$_cooldown 秒）' : '重新發送驗證碼'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
