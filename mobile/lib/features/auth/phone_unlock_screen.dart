import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/human/turnstile.dart';
import '../../core/network/api_exception.dart';
import '../../core/phone.dart';
import '../../core/theme/app_theme.dart';
import '../../models/identity.dart';
import '../../router/app_router.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';
import 'phone_field.dart';

/// Prove a phone number. **This is the call車 unlock.**
///
/// Signing in does not require a proven number and never did after the split;
/// *starting a booking* does. An account can sign in, browse, and read its own
/// profile with `phone_verified_at IS NULL` — it simply cannot call a taxi until
/// it proves a number, and this is where it does that.
///
/// Two states arrive here and they need different endpoints, which is why the
/// screen reads the profile before it offers anything:
///
///  * **Never proven** (`phone_verified_at IS NULL`) — bind a number. Any number
///    nobody else has proven will do.
///  * **Overdue** (`PHONE_REVERIFY_DUE`) — the number *is* on the account and the
///    monthly deadline has passed the grace window. This must go through
///    `/identity/phone/reverify`, which accepts **only the number already on the
///    account**: the number cannot be changed in the same breath as clearing the
///    block. Sending this case to `/phone/confirm` would silently turn a
///    re-verification into a change of number.
///
/// The raw number is never sent to the client — only `+852****1234` — so the user
/// has to re-enter it even to re-verify. That is a consequence of the masking
/// rule, not an oversight.
class PhoneUnlockScreen extends ConsumerStatefulWidget {
  const PhoneUnlockScreen({super.key});

  @override
  ConsumerState<PhoneUnlockScreen> createState() => _PhoneUnlockScreenState();
}

class _PhoneUnlockScreenState extends ConsumerState<PhoneUnlockScreen> {
  final TextEditingController _phone = TextEditingController();
  final TextEditingController _code = TextEditingController();
  final GlobalKey<TurnstileChallengeState> _turnstile = GlobalKey<TurnstileChallengeState>();

  String? _humanToken;
  bool _busy = false;

  /// The E.164 number a code was actually sent to. Non-null means step two.
  ///
  /// Kept separately from [_phone] because the field stays editable while the
  /// code is being entered, and the confirm call must use the number the code was
  /// issued for — a code sent to A cannot attach B.
  String? _sentTo;

  /// The OTP send is rate-limited per IP *and* per account, so a resend button
  /// with no cooldown is a way to lock yourself out.
  int _cooldown = 0;
  Timer? _timer;

  @override
  void dispose() {
    _timer?.cancel();
    _phone.dispose();
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

  void _spendHumanToken() {
    _humanToken = null;
    _turnstile.currentState?.reset();
  }

  /// Send a code to the number in the field.
  ///
  /// One route for both states, deliberately: `identity/phone/request` is the
  /// send that matches "prove a number on this account", and
  /// `_assert_unclaimed` excludes the caller, so re-requesting a number this
  /// account already owns is allowed. The *confirm* step is where the two states
  /// diverge — see [_confirm].
  Future<void> _send() async {
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
          .read(identityRepositoryProvider)
          .requestPhone(phoneE164: phone, humanToken: _humanToken);
      if (!mounted) {
        return;
      }
      setState(() {
        _sentTo = phone;
        _code.clear();
      });
      _startCooldown();
      showInfo(context, '驗證碼已發送');
    } on ApiException catch (e) {
      if (mounted) {
        showError(context, e);
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
      // The token is spent either way: a send that reached the server consumed
      // it, and a send refused by the human gate never had a usable one.
      _spendHumanToken();
    }
  }

  Future<void> _confirm({required bool reverify}) async {
    final String? phone = _sentTo;
    if (phone == null) {
      return;
    }
    if (_code.text.length != 6) {
      showInfo(context, '請輸入 6 位驗證碼');
      return;
    }

    setState(() => _busy = true);
    try {
      final PhoneBinding binding = reverify
          ? await ref
                .read(identityRepositoryProvider)
                .reverifyPhone(phoneE164: phone, code: _code.text)
          : await ref
                .read(identityRepositoryProvider)
                .confirmPhone(phoneE164: phone, code: _code.text);
      if (!mounted) {
        return;
      }
      // The response carries the new profile, but the cached read is what every
      // other screen paints from.
      ref.invalidate(profileProvider);
      showInfo(context, binding.verified ? '電話號碼已驗證，可以叫車了。' : '驗證完成。');
      if (context.canPop()) {
        context.pop();
      } else {
        context.go(Routes.request);
      }
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

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AsyncValue<Profile?> profile = ref.watch(profileProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('電話驗證')),
      body: SafeArea(
        child: AsyncValueView<Profile?>(
          value: profile,
          onRetry: () => ref.invalidate(profileProvider),
          builder: (Profile? data) => SingleChildScrollView(
            padding: const EdgeInsets.all(AppTheme.space4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                _StateCard(profile: data),
                const SizedBox(height: AppTheme.space4),
                if (_sentTo == null)
                  _SendStep(
                    controller: _phone,
                    reverify: data?.phoneReverifyBlocked ?? false,
                    maskedPhone: data?.phoneMasked,
                    busy: _busy,
                    turnstileKey: _turnstile,
                    onToken: (String token) => _humanToken = token,
                    onStale: () => _humanToken = null,
                    onError: (String code) => showInfo(context, '真人驗證失敗（$code），請重試。'),
                    onSubmit: _send,
                  )
                else
                  _ConfirmStep(
                    sentTo: _sentTo!,
                    controller: _code,
                    busy: _busy,
                    cooldown: _cooldown,
                    onConfirm: () => _confirm(reverify: data?.phoneReverifyBlocked ?? false),
                    onResend: _cooldown > 0 || _busy ? null : _send,
                    onBack: () => setState(() {
                      _sentTo = null;
                      _code.clear();
                    }),
                  ),
                const SizedBox(height: AppTheme.space6),
                Text(
                  '驗證電話號碼只為解鎖「call車」功能；登入與瀏覽不需要驗證。',
                  textAlign: TextAlign.center,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Where the account stands right now, and what that means.
class _StateCard extends StatelessWidget {
  const _StateCard({required this.profile});

  final Profile? profile;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Profile? data = profile;
    // Read once, so the "show it only before the block lands" rule is stated in
    // one place rather than duplicated between the guard and the widget.
    final String? notice = data?.phoneReverifyNoticeZh;
    final bool blocked = data?.phoneReverifyBlocked ?? false;
    final bool ready = data?.canCallTaxi ?? false;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.space4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                StatusChip(
                  label: ready ? '可叫車' : '未解鎖',
                  color: ready ? AppTheme.loss : AppTheme.pending,
                ),
                const SizedBox(width: AppTheme.space3),
                Expanded(child: Text(data?.phoneMasked ?? '—', style: theme.textTheme.titleMedium)),
              ],
            ),
            const SizedBox(height: AppTheme.space3),
            Text(
              // Object patterns rather than `final Profile p when …`: the binding
              // would be unused in every arm, and a getter pattern reads as the
              // state it is testing.
              switch (data) {
                null => '正在讀取帳戶狀態…',
                Profile(canCallTaxi: true) => '電話號碼已驗證，可以叫車。',
                Profile(phoneReverifyBlocked: true) => '驗證已逾期，請重新驗證帳戶上的號碼。',
                _ => '尚未驗證電話號碼，驗證後即可叫車。',
              },
              style: theme.textTheme.bodyMedium,
            ),
            // The P-4 reminder, stated once in `Profile.phoneReverifyNoticeZh`.
            // Once blocked, the line above already says it; repeating it in the
            // warning colour would read as two separate problems.
            if (notice != null && !blocked) ...<Widget>[
              const SizedBox(height: AppTheme.space2),
              Text(notice, style: theme.textTheme.bodySmall?.copyWith(color: AppTheme.pending)),
            ],
          ],
        ),
      ),
    );
  }
}

/// Step one: choose the number and ask for a code.
class _SendStep extends StatelessWidget {
  const _SendStep({
    required this.controller,
    required this.reverify,
    required this.maskedPhone,
    required this.busy,
    required this.turnstileKey,
    required this.onToken,
    required this.onStale,
    required this.onError,
    required this.onSubmit,
  });

  final TextEditingController controller;

  /// True when the account is overdue, so the number must be the one already on
  /// it. Only the copy changes here; the *endpoint* choice is made in [_confirm].
  final bool reverify;

  final String? maskedPhone;
  final bool busy;
  final GlobalKey<TurnstileChallengeState> turnstileKey;
  final ValueChanged<String> onToken;
  final VoidCallback onStale;
  final ValueChanged<String> onError;
  final VoidCallback onSubmit;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text(
          reverify
              ? '重新驗證帳戶上的號碼（${maskedPhone ?? '—'}）。此步驟不接受其他號碼；如要更改號碼，請直接輸入新號碼並完成驗證。'
              : '輸入要綁定的香港手機號碼，我們會以 WhatsApp 發送驗證碼。',
          style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
        ),
        const SizedBox(height: AppTheme.space4),
        HkPhoneField(
          controller: controller,
          label: reverify ? '帳戶上的號碼' : '手機號碼',
          helperText: reverify ? '必須與帳戶上的號碼相同' : '此號碼只可由一個帳戶驗證',
          onSubmitted: (String _) => onSubmit(),
        ),
        const SizedBox(height: AppTheme.space2),
        TurnstileChallenge(key: turnstileKey, onToken: onToken, onStale: onStale, onError: onError),
        const SizedBox(height: AppTheme.space4),
        FilledButton(
          onPressed: busy ? null : onSubmit,
          child: busy
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Text('發送驗證碼'),
        ),
      ],
    );
  }
}

/// Step two: the six-digit code.
class _ConfirmStep extends StatelessWidget {
  const _ConfirmStep({
    required this.sentTo,
    required this.controller,
    required this.busy,
    required this.cooldown,
    required this.onConfirm,
    required this.onResend,
    required this.onBack,
  });

  final String sentTo;
  final TextEditingController controller;
  final bool busy;
  final int cooldown;
  final VoidCallback onConfirm;
  final VoidCallback? onResend;
  final VoidCallback onBack;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text(
          '驗證碼已發送至 $sentTo',
          style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
        ),
        const SizedBox(height: AppTheme.space6),
        TextField(
          controller: controller,
          autofocus: true,
          keyboardType: TextInputType.number,
          textAlign: TextAlign.center,
          maxLength: 6,
          style: theme.textTheme.headlineSmall?.copyWith(letterSpacing: 8),
          inputFormatters: <TextInputFormatter>[
            FilteringTextInputFormatter.digitsOnly,
            LengthLimitingTextInputFormatter(6),
          ],
          onSubmitted: (String _) => onConfirm(),
          decoration: const InputDecoration(labelText: '驗證碼', counterText: '', hintText: '000000'),
        ),
        const SizedBox(height: AppTheme.space4),
        FilledButton(
          onPressed: busy ? null : onConfirm,
          child: busy
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Text('確認'),
        ),
        const SizedBox(height: AppTheme.space3),
        TextButton(
          onPressed: onResend,
          child: Text(cooldown > 0 ? '重新發送（$cooldown 秒）' : '重新發送驗證碼'),
        ),
        TextButton(onPressed: busy ? null : onBack, child: const Text('更改號碼')),
      ],
    );
  }
}

/// Offer the unlock when a refusal is one proving a phone number would clear.
///
/// Shared by the two gated actions a user can take — starting a booking and
/// grabbing one — so both refuse the same way. `require_phone_current` sits on
/// `POST /orders`, `POST /orders/{id}/grab`, the driver registration and licence
/// routes, so any of them can answer 403 `PHONE_NOT_VERIFIED` or
/// `PHONE_REVERIFY_DUE`.
///
/// Returns **false** when the error is unrelated, and the caller should show it
/// itself. True means the offer has been made and the message has been shown, so
/// a caller that then also calls `showError` would stack two toasts.
bool offerPhoneUnlockIfNeeded(BuildContext context, ApiException error) {
  if (!error.needsPhoneUnlock) {
    return false;
  }
  showErrorAction(
    context,
    error,
    actionLabel: error.isPhoneReverifyDue ? '重新驗證' : '驗證電話',
    onAction: () => context.push(Routes.phoneUnlock),
  );
  return true;
}
