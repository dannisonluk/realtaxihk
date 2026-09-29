import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_exception.dart';
import '../../models/driver.dart';
import '../../models/enums.dart';
import '../../router/app_router.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// Driver registration and status.
///
/// One screen covers three states, because they are the same question — "can I
/// drive yet?":
/// * no profile → the registration form (`POST /drivers/register`, enters
///   `PENDING_KYC`);
/// * a profile that is not `ACTIVE` → why, and what unlocks it;
/// * `ACTIVE` → a way into the job list.
///
/// Registration is one-shot: a second attempt for the same account is a 409, and
/// there is no endpoint that edits a profile, so the form is only reachable when
/// `GET /drivers/me` 404s.
class DriverOnboardingScreen extends ConsumerStatefulWidget {
  const DriverOnboardingScreen({super.key});

  @override
  ConsumerState<DriverOnboardingScreen> createState() => _DriverOnboardingScreenState();
}

class _DriverOnboardingScreenState extends ConsumerState<DriverOnboardingScreen> {
  final TextEditingController _idLast4 = TextEditingController();
  final TextEditingController _plate = TextEditingController();
  final TextEditingController _regMark = TextEditingController();
  TaxiType _taxiType = TaxiType.urban;
  bool _busy = false;

  @override
  void dispose() {
    _idLast4.dispose();
    _plate.dispose();
    _regMark.dispose();
    super.dispose();
  }

  Future<void> _register() async {
    final String last4 = _idLast4.text.trim();
    final String plate = _plate.text.trim();
    final String mark = _regMark.text.trim();

    if (last4.length != 4) {
      showInfo(context, '請輸入香港身份證號碼最後 4 位數字');
      return;
    }
    if (plate.length < 4 || mark.length < 4) {
      showInfo(context, '請填寫的士證號及車輛登記號碼');
      return;
    }

    setState(() => _busy = true);
    try {
      await ref
          .read(driverRepositoryProvider)
          .register(
            DriverRegisterRequest(
              hkIdLast4: last4,
              taxiDriverPlateNo: plate,
              vehicleRegMark: mark,
              taxiType: _taxiType,
            ),
          );
      ref.invalidate(driverProfileProvider);
      if (mounted) {
        showInfo(context, '已提交申請，等待平台審核');
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
    final AsyncValue<DriverProfile?> profile = ref.watch(driverProfileProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('司機帳戶')),
      body: AsyncValueView<DriverProfile?>(
        value: profile,
        onRetry: () => ref.invalidate(driverProfileProvider),
        builder: (DriverProfile? data) =>
            data == null ? _registrationForm(context) : _statusView(context, data),
      ),
    );
  }

  Widget _registrationForm(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return ListView(
      padding: const EdgeInsets.all(16),
      children: <Widget>[
        Text('登記成為司機', style: theme.textTheme.titleLarge),
        const SizedBox(height: 8),
        Text(
          '提交後進入身份審核。審核通過後需繳交按金，按金達標即會自動啟用接單功能。',
          style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
        ),
        const SizedBox(height: 24),
        TextField(
          controller: _idLast4,
          keyboardType: TextInputType.number,
          maxLength: 4,
          decoration: const InputDecoration(
            labelText: '香港身份證最後 4 位',
            counterText: '',
            hintText: '1234',
          ),
        ),
        const SizedBox(height: 16),
        TextField(
          controller: _plate,
          decoration: const InputDecoration(labelText: '的士司機證號碼', hintText: '例如 123456'),
        ),
        const SizedBox(height: 16),
        TextField(
          controller: _regMark,
          textCapitalization: TextCapitalization.characters,
          decoration: const InputDecoration(labelText: '車輛登記號碼', hintText: '例如 AB1234'),
        ),
        const SizedBox(height: 20),
        Text('的士種類', style: theme.textTheme.titleSmall),
        const SizedBox(height: 8),
        SegmentedButton<TaxiType>(
          segments: <ButtonSegment<TaxiType>>[
            for (final TaxiType type in TaxiType.values)
              ButtonSegment<TaxiType>(value: type, label: Text(type.labelZh)),
          ],
          selected: <TaxiType>{_taxiType},
          onSelectionChanged: (Set<TaxiType> value) => setState(() => _taxiType = value.first),
        ),
        const SizedBox(height: 28),
        FilledButton(
          onPressed: _busy ? null : _register,
          child: _busy
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Text('提交申請'),
        ),
      ],
    );
  }

  Widget _statusView(BuildContext context, DriverProfile profile) {
    final ThemeData theme = Theme.of(context);
    final DriverDeposit? deposit = profile.deposit;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: <Widget>[
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    StatusChip.driver(profile.status, context),
                    const Spacer(),
                    IconButton(
                      onPressed: () => ref.invalidate(driverProfileProvider),
                      icon: const Icon(Icons.refresh),
                      tooltip: '重新整理',
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Text(_statusExplanation(profile.status), style: theme.textTheme.bodyMedium),
              ],
            ),
          ),
        ),
        const SizedBox(height: 16),
        Text('車輛資料', style: theme.textTheme.titleSmall),
        const SizedBox(height: 8),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              children: <Widget>[
                DetailRow(label: '的士種類', value: profile.taxiType.labelZh),
                DetailRow(label: '的士司機證', value: profile.taxiDriverPlateNo),
                DetailRow(label: '車輛登記', value: profile.vehicleRegMark),
              ],
            ),
          ),
        ),
        if (deposit != null) ...<Widget>[
          const SizedBox(height: 16),
          Text('按金', style: theme.textTheme.titleSmall),
          const SizedBox(height: 8),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                children: <Widget>[
                  DetailRow(label: '目前餘額', valueWidget: MoneyText(deposit.balanceHkd)),
                  DetailRow(label: '凍結中', valueWidget: MoneyText(deposit.heldHkd)),
                  DetailRow(label: '要求金額', valueWidget: MoneyText(deposit.requiredHkd)),
                  if (!deposit.isFulfilled)
                    DetailRow(label: '尚欠', valueWidget: MoneyText(deposit.shortfall)),
                  const SizedBox(height: 8),
                  LinearProgressIndicator(
                    value: deposit.requiredHkd.asDouble <= 0
                        ? 0
                        : (deposit.balanceHkd.asDouble / deposit.requiredHkd.asDouble).clamp(
                            0.0,
                            1.0,
                          ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '按金由平台管理員以銀行轉帳方式入帳，App 內不設付款。',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
        const SizedBox(height: 24),
        if (profile.status.canDrive)
          FilledButton(onPressed: () => context.go(Routes.driverJobs), child: const Text('開始接單')),
        const SizedBox(height: 8),
        OutlinedButton(onPressed: () => context.go(Routes.request), child: const Text('返回乘客模式')),
      ],
    );
  }

  static String _statusExplanation(DriverStatus status) => switch (status) {
    DriverStatus.pendingKyc => '你的申請正在審核中。平台確認身份證及司機證後會通知你繳交按金。',
    DriverStatus.depositRequired => '審核已通過。請聯絡平台繳交按金，達標後帳戶會自動啟用。',
    DriverStatus.active => '帳戶已啟用，可以開始接單。',
    DriverStatus.suspended => '帳戶已被停權。如已完成退款申請，這是預期狀態；否則請聯絡平台。',
    DriverStatus.terminated => '帳戶已終止，無法再接單。',
  };
}
