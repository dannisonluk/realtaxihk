import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';
import '../../models/enums.dart';

/// What the user chose in the interrupt picker.
class InterruptChoice {
  const InterruptChoice({required this.reason, required this.note});

  final InterruptionReason reason;
  final String note;
}

/// Ask **why** the trip is being ended early (P4 §6.1 / §6.2).
///
/// Returns `null` when the user backs out — the caller must treat null as "do
/// nothing", never as a default reason. The reason is a required choice because
/// it is the evidence an operator judges the case on afterwards; a default would
/// be an answer nobody gave.
///
/// [options] is the role's menu ([InterruptionReason.forPassenger] or
/// [forDriver]). Filtering here is courtesy only — the server validates the
/// pair of `interrupted_by_kind` x reason, so an option wrongly offered would
/// still be refused.
Future<InterruptChoice?> askInterruptReason(
  BuildContext context, {
  required List<InterruptionReason> options,
  required String counterparty,
}) {
  return showDialog<InterruptChoice>(
    context: context,
    builder: (BuildContext context) =>
        _InterruptDialog(options: options, counterparty: counterparty),
  );
}

class _InterruptDialog extends StatefulWidget {
  const _InterruptDialog({required this.options, required this.counterparty});

  final List<InterruptionReason> options;
  final String counterparty;

  @override
  State<_InterruptDialog> createState() => _InterruptDialogState();
}

class _InterruptDialogState extends State<_InterruptDialog> {
  InterruptionReason? _reason;
  final TextEditingController _note = TextEditingController();
  String? _error;

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  void _submit() {
    final InterruptionReason? reason = _reason;
    if (reason == null) {
      setState(() => _error = '請選擇中斷原因');
      return;
    }
    if (reason.requiresNote && _note.text.trim().isEmpty) {
      setState(() => _error = '選擇「其他」時必須填寫說明');
      return;
    }
    Navigator.of(context).pop(InterruptChoice(reason: reason, note: _note.text.trim()));
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return AlertDialog(
      title: const Text('中斷行程'),
      content: SizedBox(
        width: 420,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text('中斷後行程即時結束，平台會稍後處理費用安排。請選擇原因：', style: theme.textTheme.bodyMedium),
              const SizedBox(height: AppTheme.space3),
              for (final InterruptionReason reason in widget.options)
                _ReasonTile(
                  label: reason.labelZh,
                  selected: _reason == reason,
                  onTap: () => setState(() {
                    _reason = reason;
                    _error = null;
                  }),
                ),
              const SizedBox(height: AppTheme.space3),
              TextField(
                controller: _note,
                maxLines: 3,
                maxLength: 500,
                decoration: InputDecoration(
                  labelText: _reason?.requiresNote == true ? '說明（必填）' : '補充說明（選填）',
                  border: const OutlineInputBorder(),
                ),
                onChanged: (_) {
                  if (_error != null) {
                    setState(() => _error = null);
                  }
                },
              ),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: AppTheme.space2),
                  child: Text(
                    _error!,
                    style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
                  ),
                ),
            ],
          ),
        ),
      ),
      actions: <Widget>[
        TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('返回')),
        FilledButton(onPressed: _submit, child: const Text('確認中斷')),
      ],
    );
  }
}

/// A radio-style row drawn by hand.
///
/// Deliberately not `RadioListTile`: its `groupValue`/`onChanged` API is
/// deprecated in the Flutter version this app pins, and a deprecation in the
/// middle of the emergency path is not worth the few lines saved.
class _ReasonTile extends StatelessWidget {
  const _ReasonTile({required this.label, required this.selected, required this.onTap});

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return ListTile(
      dense: true,
      contentPadding: EdgeInsets.zero,
      leading: Icon(
        selected ? Icons.radio_button_checked : Icons.radio_button_unchecked,
        color: selected ? scheme.primary : scheme.onSurfaceVariant,
      ),
      title: Text(label),
      onTap: onTap,
    );
  }
}
