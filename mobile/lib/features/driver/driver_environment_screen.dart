import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../models/driver_attributes.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// The driver's declared in-car environment, as a set of capability flags.
///
/// These are **declarations, not guarantees** — the same footing as the payment
/// methods screen. A passenger asks for a silent ride; whether this particular
/// car delivers it is the driver's own statement, and the platform is an
/// information intermediary (Cap. 374D), not a party to that promise. The
/// footnote says so, because a chip with no caveat reads as a platform promise.
///
/// `PUT /drivers/me/environment` replaces the whole set rather than patching one
/// flag (`api_client.dart` documents the same choice for payment methods), so
/// this screen holds local state and writes all four on save.
class DriverEnvironmentScreen extends ConsumerStatefulWidget {
  const DriverEnvironmentScreen({super.key});

  @override
  ConsumerState<DriverEnvironmentScreen> createState() => _DriverEnvironmentScreenState();
}

class _DriverEnvironmentScreenState extends ConsumerState<DriverEnvironmentScreen> {
  DriverEnvironment? _draft;
  bool _busy = false;

  Future<void> _save() async {
    final DriverEnvironment? draft = _draft;
    if (draft == null) {
      return;
    }
    setState(() => _busy = true);
    try {
      await ref.read(driverRepositoryProvider).setEnvironment(draft);
      ref.invalidate(driverEnvironmentProvider);
      if (mounted) {
        showInfo(context, '已更新車內環境設定');
        await Navigator.of(context).maybePop();
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
    final AsyncValue<DriverEnvironment> env = ref.watch(driverEnvironmentProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('車內環境')),
      body: AsyncValueView<DriverEnvironment>(
        value: env,
        onRetry: () => ref.invalidate(driverEnvironmentProvider),
        builder: (DriverEnvironment loaded) {
          // First build seeds the draft from the server; afterwards the draft is
          // the user's own edit and must not be reset by a rebuild.
          final DriverEnvironment draft = _draft ??= loaded;
          return ListView(
            padding: const EdgeInsets.all(AppTheme.space4),
            children: <Widget>[
              GroupedSection(
                title: '我可以提供的環境',
                footnote:
                    '這些是你自行聲明的車廂條件，乘客會在下單時看到；'
                    '平台僅屬資訊中介，不會代你保證。',
                children: <Widget>[
                  _flag(
                    title: '完全靜音',
                    subtitle: '全程不交談、不播放聲音',
                    value: draft.silentRide,
                    onChanged: (bool v) => setState(() => _draft = _copy(silentRide: v)),
                  ),
                  _flag(
                    title: '不播放電台／音樂',
                    subtitle: '收音機與音響全程關閉',
                    value: draft.noRadioMusic,
                    onChanged: (bool v) => setState(() => _draft = _copy(noRadioMusic: v)),
                  ),
                  _flag(
                    title: '無煙車廂',
                    subtitle: '車內及行程期間不吸煙',
                    value: draft.noSmoke,
                    onChanged: (bool v) => setState(() => _draft = _copy(noSmoke: v)),
                  ),
                  _flag(
                    title: '無香水／香薰',
                    subtitle: '不使用香水、香薰或空氣清新劑',
                    value: draft.noPerfume,
                    onChanged: (bool v) => setState(() => _draft = _copy(noPerfume: v)),
                  ),
                ],
              ),
              const SizedBox(height: AppTheme.space6),
              FilledButton(
                onPressed: _busy ? null : _save,
                child: _busy
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('儲存'),
              ),
            ],
          );
        },
      ),
    );
  }

  /// A copy with one flag replaced. `DriverEnvironment` is immutable, so every
  /// toggle rebuilds the whole value rather than mutating a field.
  DriverEnvironment _copy({bool? silentRide, bool? noRadioMusic, bool? noSmoke, bool? noPerfume}) {
    final DriverEnvironment base = _draft!;
    return DriverEnvironment(
      silentRide: silentRide ?? base.silentRide,
      noRadioMusic: noRadioMusic ?? base.noRadioMusic,
      noSmoke: noSmoke ?? base.noSmoke,
      noPerfume: noPerfume ?? base.noPerfume,
    );
  }

  Widget _flag({
    required String title,
    required String subtitle,
    required bool value,
    required ValueChanged<bool> onChanged,
  }) {
    return SwitchListTile(
      contentPadding: EdgeInsets.zero,
      title: Text(title),
      subtitle: Text(subtitle),
      value: value,
      onChanged: onChanged,
    );
  }
}
