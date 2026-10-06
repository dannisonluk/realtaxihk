import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_exception.dart';
import '../../core/security/username_policy.dart';
import '../../core/theme/app_theme.dart';
import '../../models/enums.dart';
import '../../models/identity.dart';
import '../../router/app_router.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import 'widgets.dart';

/// The choices `app/models/user.py::Gender` accepts, with their Chinese labels.
///
/// A local list rather than a Dart enum, which is the same call
/// [Profile.gender] makes: nothing in the app branches on the value, it is only
/// echoed back, so an enum would be a second place for a new server member to
/// throw on a screen that does not care. `null` is "not stated" and is sent by
/// **omitting the key**; `UNDISCLOSED` is a different, deliberate answer.
const List<(String, String)> _genders = <(String, String)>[
  ('MALE', '男'),
  ('FEMALE', '女'),
  ('OTHER', '其他'),
  ('UNDISCLOSED', '不願透露'),
];

/// How long to wait after the last keystroke before asking the server.
///
/// `GET /identity/username-check` is rate-limited at 120 a minute per IP, and a
/// field that fires on every keystroke reaches that with a fast typist. 700 ms
/// is long enough to stay well inside the budget and short enough that the
/// answer lands before the user reaches for the submit button.
const Duration _checkDebounce = Duration(milliseconds: 700);

/// What the availability check currently believes.
enum _Availability { unknown, checking, free, taken }

/// Fill in the account's own record: username, names, gender.
///
/// This is the screen `POST /identity/profile` was built for and never had.
/// `IdentityService.complete_profile` is what sets `username`, `given_name`,
/// `family_name` and `gender`, and — with a proven email and phone — what flips
/// `account_status` to `ACTIVE`. Nothing in the app called it, so every account
/// created by `POST /auth/register` stayed nameless and every receipt, order row
/// and roster line that renders a passenger rendered `—`.
///
/// Three deliberate choices:
///
///  * **It is not a gate, and there is no router rule for it.** The server never
///    refuses anything for an incomplete profile: `account_status` is a
///    completeness flag and no dependency in `app/core/deps.py` reads it, so a
///    client-side redirect here would be stricter than the API and would strand
///    a user on a screen the backend was happy to serve. It is *offered* — from
///    the account screen and from the booking screen — and the user may walk
///    away from it. `AccountStatus`'s own docstring says the same thing.
///  * **The username field checks availability while typing, and still shows the
///    server's refusal.** The check is a hint that saves a round trip; it races,
///    so "that username is taken" from the submit call has to be rendered too.
///  * **A blank optional field is omitted, not sent as null.** The server reads
///    an absent `gender` as "leave it alone", so sending `null` would be a
///    different request with the same shape. See
///    `IdentityRepository.completeProfile`.
class ProfileSetupScreen extends ConsumerStatefulWidget {
  const ProfileSetupScreen({super.key});

  @override
  ConsumerState<ProfileSetupScreen> createState() => _ProfileSetupScreenState();
}

class _ProfileSetupScreenState extends ConsumerState<ProfileSetupScreen> {
  final TextEditingController _username = TextEditingController();
  final TextEditingController _givenName = TextEditingController();
  final TextEditingController _familyName = TextEditingController();

  String? _gender;
  bool _busy = false;

  /// Whether the form has been seeded from the profile yet.
  ///
  /// A flag rather than "is the field empty", because `profileProvider` is
  /// re-read whenever the session changes and re-applying it would overwrite
  /// what the user has typed with what the server still holds — which looks
  /// exactly like the form silently rejecting an edit.
  bool _seeded = false;

  _Availability _availability = _Availability.unknown;

  /// The normalised handle the last successful answer was about. Kept so a
  /// rebuild that re-enters [_onUsernameChanged] does not re-ask the server for
  /// an answer it already has.
  String? _answered;

  Timer? _debounce;
  ProviderSubscription<AsyncValue<Profile?>>? _profileSubscription;

  @override
  void initState() {
    super.initState();
    // Seed from whatever the cache already holds. The screen is normally opened
    // from the account screen, which has read the profile — so in the common
    // case this is the whole prefill and no rebuild is needed.
    _seed(ref.read(profileProvider).value);
    // And cover the case where it is still in flight. `listenManual` rather than
    // a `ref.listen` in `build`: this callback fires outside the build phase, so
    // assigning to the controllers from it cannot trip "setState during build".
    _profileSubscription = ref.listenManual<AsyncValue<Profile?>>(profileProvider, (
      AsyncValue<Profile?>? previous,
      AsyncValue<Profile?> next,
    ) {
      if (_seeded || next.value == null) {
        return;
      }
      setState(() => _seed(next.value));
    });
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _profileSubscription?.close();
    _username.dispose();
    _givenName.dispose();
    _familyName.dispose();
    super.dispose();
  }

  void _seed(Profile? profile) {
    if (_seeded || profile == null) {
      return;
    }
    _seeded = true;
    _username.text = profile.username ?? '';
    _givenName.text = profile.givenName ?? '';
    _familyName.text = profile.familyName ?? '';
    _gender = profile.gender;
  }

  void _onUsernameChanged(String value) {
    _debounce?.cancel();
    final String handle = normalizeUsername(value);

    // Shorter than the server's minimum, so there is nothing to ask about yet.
    if (handle.length < minUsernameLength) {
      if (_availability != _Availability.unknown || _answered != null) {
        setState(() {
          _availability = _Availability.unknown;
          _answered = null;
        });
      }
      return;
    }
    // A shape the server would refuse outright. Asking anyway would come back
    // `false`, which reads as "already taken" — see `username_policy.dart`.
    //
    // Both early returns clear `_answered` as well as the verdict. Leaving it
    // set would mean a handle that was answered, made briefly invalid (an extra
    // `!`), then restored skips the re-check on the way back and keeps showing
    // the neutral hint instead of the verdict it already has.
    if (usernameProblem(handle) != null) {
      setState(() {
        _availability = _Availability.unknown;
        _answered = null;
      });
      return;
    }
    if (handle == _answered) {
      return;
    }
    setState(() => _availability = _Availability.checking);
    _debounce = Timer(_checkDebounce, () => _check(handle));
  }

  Future<void> _check(String handle) async {
    try {
      final bool available = await ref.read(identityRepositoryProvider).usernameAvailable(handle);
      // The field may have moved on while the request was in flight; an answer
      // about a stale value would label the wrong handle.
      if (!mounted || normalizeUsername(_username.text) != handle) {
        return;
      }
      setState(() {
        _answered = handle;
        _availability = available ? _Availability.free : _Availability.taken;
      });
    } on ApiException {
      // A hint is allowed to have no opinion. The route is rate-limited on
      // purpose (120 a minute per IP), so a 429 here is a normal answer, and a
      // dropped connection is not the form's problem. Reporting either as an
      // error would make a working form look broken, and the submit call is the
      // one that decides anyway.
      if (mounted) {
        setState(() => _availability = _Availability.unknown);
      }
    }
  }

  Future<void> _submit() async {
    final String username = normalizeUsername(_username.text);
    final String given = _givenName.text.trim();
    final String family = _familyName.text.trim();

    final String? problem = usernameProblem(username);
    if (problem != null) {
      showInfo(context, problem);
      return;
    }
    if (given.isEmpty || family.isEmpty) {
      showInfo(context, '請輸入名字及姓氏');
      return;
    }

    setState(() => _busy = true);
    try {
      final Profile profile = await ref
          .read(identityRepositoryProvider)
          .completeProfile(
            username: username,
            givenName: given,
            familyName: family,
            gender: _gender,
          );
      if (!mounted) {
        return;
      }
      // The response carries the new profile, but the cached read is what every
      // other screen paints from.
      ref.invalidate(profileProvider);
      showInfo(
        context,
        profile.accountStatus == AccountStatus.active ? '個人資料已補完，帳戶已啟用。' : '個人資料已儲存。',
      );
      if (context.canPop()) {
        context.pop();
      } else {
        context.go(Routes.request);
      }
    } on ApiException catch (e) {
      if (mounted) {
        // Includes "that username is taken" — the race the availability check
        // cannot close, so it has to be rendered here.
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
    final AsyncValue<Profile?> profile = ref.watch(profileProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('個人資料')),
      body: SafeArea(
        child: AsyncValueView<Profile?>(
          value: profile,
          onRetry: () => ref.invalidate(profileProvider),
          builder: (Profile? data) => ListView(
            padding: const EdgeInsets.all(AppTheme.space4),
            children: <Widget>[
              Text(
                '填寫個人資料後，訂單與收據會顯示你的名字，帳戶亦會由「未完成驗證」轉為「已啟用」。'
                '此頁並非必經步驟——未填寫一樣可以叫車。',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: AppTheme.space4),
              _StatusCard(profile: data),
              const SizedBox(height: AppTheme.space4),
              _usernameField(context),
              const SizedBox(height: AppTheme.space4),
              TextField(
                controller: _givenName,
                textInputAction: TextInputAction.next,
                textCapitalization: TextCapitalization.words,
                decoration: const InputDecoration(labelText: '名字', hintText: 'Ka Ming'),
              ),
              const SizedBox(height: AppTheme.space4),
              TextField(
                controller: _familyName,
                textInputAction: TextInputAction.done,
                textCapitalization: TextCapitalization.words,
                onSubmitted: (String _) => _submit(),
                decoration: const InputDecoration(labelText: '姓氏', hintText: 'Chan'),
              ),
              const SizedBox(height: AppTheme.space4),
              Text('性別（可選）', style: theme.textTheme.titleSmall),
              const SizedBox(height: AppTheme.space2),
              Wrap(
                spacing: AppTheme.space2,
                runSpacing: AppTheme.space1,
                children: <Widget>[
                  for (final (String token, String label) in _genders)
                    ChoiceChip(
                      label: Text(label),
                      selected: _gender == token,
                      // Re-tapping the selected chip clears it. "Not stated" is
                      // the absence of an answer, and it is not the same as
                      // UNDISCLOSED — see `_genders`.
                      onSelected: (bool selected) =>
                          setState(() => _gender = selected ? token : null),
                    ),
                ],
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
                    : const Text('儲存'),
              ),
              const SizedBox(height: AppTheme.space3),
              TextButton(
                // Guarded because this screen is also reachable by deep link,
                // and on a stack with nothing under it `pop()` throws.
                onPressed: _busy
                    ? null
                    : () => context.canPop() ? context.pop() : context.go(Routes.request),
                child: const Text('稍後再填'),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _usernameField(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final (String helper, Color? colour) = switch (_availability) {
      _Availability.unknown => ('3–32 個字元，可用小寫英文字母、數字、. _ -', null),
      _Availability.checking => ('檢查中…', null),
      _Availability.free => ('可以使用', theme.colorScheme.primary),
      _Availability.taken => ('已被使用，請換一個', theme.colorScheme.error),
    };

    return TextField(
      controller: _username,
      autocorrect: false,
      enableSuggestions: false,
      textInputAction: TextInputAction.next,
      onChanged: _onUsernameChanged,
      decoration: InputDecoration(
        labelText: '使用者名稱',
        hintText: 'kaming.chan',
        prefixText: '@',
        helperText: helper,
        helperStyle: colour == null ? null : TextStyle(color: colour),
      ),
    );
  }
}

/// Where the account stands, and what filling this in changes.
class _StatusCard extends StatelessWidget {
  const _StatusCard({required this.profile});

  final Profile? profile;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Profile? data = profile;
    final AccountStatus status = data?.accountStatus ?? AccountStatus.unverified;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.space4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                StatusChip(
                  label: status.labelZh,
                  color: status == AccountStatus.active
                      ? AppTheme.loss
                      : theme.colorScheme.onSurfaceVariant,
                ),
                const SizedBox(width: AppTheme.space3),
                Expanded(
                  child: Text(data?.displayName ?? '尚未填寫', style: theme.textTheme.titleMedium),
                ),
              ],
            ),
            const SizedBox(height: AppTheme.space3),
            // The two other pieces of "complete" are proven elsewhere, so say
            // which ones are outstanding rather than implying this form can
            // finish the job on its own — it cannot.
            Text(switch (data) {
              null => '正在讀取帳戶狀態…',
              Profile(username: null) => '尚未設定使用者名稱。',
              _ => '使用者名稱：@${data.username}',
            }, style: theme.textTheme.bodyMedium),
            if (data != null && status != AccountStatus.active) ...<Widget>[
              const SizedBox(height: AppTheme.space2),
              Text(
                '帳戶轉為「已啟用」需要個人資料、已驗證的電郵及已驗證的電話號碼。'
                '${data.emailVerified ? '' : '電郵尚未驗證。'}'
                '${data.phoneVerified ? '' : '電話尚未驗證。'}',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
