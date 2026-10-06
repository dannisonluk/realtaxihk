import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../router/app_router.dart';
import '../../state/providers.dart';

/// Set a new password forwarded from the emailed reset App Link.
///
/// The token is held in a hidden controller so it can be prefilled from the
/// deep-link query string without exposing it in the UI. Success revokes every
/// session, so this screen signs out instead of pretending the current login is
/// still valid.
class PasswordResetScreen extends ConsumerStatefulWidget {
  const PasswordResetScreen({super.key, this.token});

  final String? token;

  @override
  ConsumerState<PasswordResetScreen> createState() =>
      _PasswordResetScreenState();
}

class _PasswordResetScreenState extends ConsumerState<PasswordResetScreen> {
  static const int _minLength = 12;

  final TextEditingController _token = TextEditingController();
  final TextEditingController _next = TextEditingController();
  final TextEditingController _confirm = TextEditingController();

  bool _busy = false;
  bool _show = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _token.text = widget.token ?? '';
  }

  @override
  void dispose() {
    _token.dispose();
    _next.dispose();
    _confirm.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_token.text.isEmpty) {
      setState(() => _error = '重設連結無效或已過期，請重新申請。');
      return;
    }
    if (_next.text.length < _minLength) {
      setState(() => _error = '新密碼至少需要 $_minLength 個字元');
      return;
    }
    if (_next.text != _confirm.text) {
      setState(() => _error = '兩次輸入的新密碼不一致');
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref
          .read(authRepositoryProvider)
          .resetPassword(token: _token.text, newPassword: _next.text);
      if (!mounted) {
        return;
      }
      await showDialog<void>(
        context: context,
        barrierDismissible: false,
        builder: (BuildContext context) => AlertDialog(
          title: const Text('密碼已重設'),
          content: const Text('所有裝置已登出。請以新密碼重新登入。'),
          actions: <Widget>[
            FilledButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('重新登入'),
            ),
          ],
        ),
      );
      if (!mounted) {
        return;
      }
      await ref.read(authControllerProvider.notifier).signOut();
      if (mounted) {
        context.go(Routes.login);
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _error = e.message);
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
      appBar: AppBar(title: const Text('重設密碼')),
      body: ListView(
        padding: const EdgeInsets.all(AppTheme.space4),
        children: <Widget>[
          Text(
            '設定新密碼。重設連結只能使用一次，且有時效。',
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
          const SizedBox(height: AppTheme.space4),
          _PasswordField(
            controller: _next,
            label: '新密碼（至少 $_minLength 個字元）',
            show: _show,
            onToggle: () => setState(() => _show = !_show),
          ),
          const SizedBox(height: AppTheme.space4),
          _PasswordField(
            controller: _confirm,
            label: '確認新密碼',
            show: _show,
            onToggle: () => setState(() => _show = !_show),
            onSubmitted: (String _) => _submit(),
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(top: AppTheme.space3),
              child: Text(
                _error!,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.error,
                ),
              ),
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
                : const Text('重設密碼'),
          ),
        ],
      ),
    );
  }
}

class _PasswordField extends StatelessWidget {
  const _PasswordField({
    required this.controller,
    required this.label,
    required this.show,
    required this.onToggle,
    this.onSubmitted,
  });

  final TextEditingController controller;
  final String label;
  final bool show;
  final VoidCallback onToggle;
  final ValueChanged<String>? onSubmitted;

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      obscureText: !show,
      autocorrect: false,
      enableSuggestions: false,
      onSubmitted: onSubmitted,
      decoration: InputDecoration(
        labelText: label,
        suffixIcon: IconButton(
          onPressed: onToggle,
          tooltip: show ? '隱藏密碼' : '顯示密碼',
          icon: Icon(show ? Icons.visibility_off : Icons.visibility),
        ),
      ),
    );
  }
}
