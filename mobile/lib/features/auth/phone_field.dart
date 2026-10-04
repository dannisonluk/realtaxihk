import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// The 8-digit HK mobile field, shared by the three screens that take a number.
///
/// `maxLength: 8` plus `FilteringTextInputFormatter.digitsOnly` means the only
/// reachable values are 0–8 digits, so a caller's only job is
/// [hkPhoneToE164] — there is no partial-input state to defend against.
class HkPhoneField extends StatelessWidget {
  const HkPhoneField({
    required this.controller,
    this.onSubmitted,
    this.autofocus = false,
    this.enabled = true,
    this.label = '手機號碼',
    this.helperText,
    super.key,
  });

  final TextEditingController controller;
  final ValueChanged<String>? onSubmitted;
  final bool autofocus;
  final bool enabled;
  final String label;

  /// A line under the field, for the one thing the user needs to know about this
  /// particular number (e.g. "this is the number on your account").
  final String? helperText;

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      autofocus: autofocus,
      enabled: enabled,
      keyboardType: TextInputType.phone,
      textInputAction: TextInputAction.done,
      maxLength: 8,
      inputFormatters: <TextInputFormatter>[
        FilteringTextInputFormatter.digitsOnly,
        LengthLimitingTextInputFormatter(8),
      ],
      onSubmitted: onSubmitted,
      decoration: InputDecoration(
        labelText: label,
        prefixText: '+852  ',
        counterText: '',
        hintText: '91234567',
        helperText: helperText,
      ),
    );
  }
}
