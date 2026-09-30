import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../models/admin.dart';
import '../../models/enums.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// The KYC queue and deposit grants.
///
/// `approve` moves a driver to `DEPOSIT_REQUIRED`; a grant that satisfies the
/// deposit then flips them to `ACTIVE` in the same call, which is what actually
/// puts them on the road. `reject` and `terminate` both land on `TERMINATED` and
/// cannot be undone through the API.
class AdminKycScreen extends ConsumerStatefulWidget {
  const AdminKycScreen({super.key});

  @override
  ConsumerState<AdminKycScreen> createState() => _AdminKycScreenState();
}

class _AdminKycScreenState extends ConsumerState<AdminKycScreen> {
  DriverStatus? _filter;
  bool _busy = false;

  Future<void> _review(AdminDriverRow driver, String decision) async {
    final bool destructive = decision == 'reject' || decision == 'terminate';
    if (destructive) {
      final bool confirmed = await confirmDestructive(
        context,
        title: '確定要${decision == 'reject' ? '拒絕' : '終止'}此司機？',
        message: '帳戶會進入 TERMINATED，無法透過 App 回復。',
        confirmLabel: '確定',
        cancelLabel: '取消',
      );
      if (!confirmed) {
        return;
      }
    }

    setState(() => _busy = true);
    try {
      await ref.read(adminRepositoryProvider).reviewDriver(driver.id, decision: decision);
      ref.invalidate(adminDriversProvider);
      if (mounted) {
        showInfo(context, '已更新司機狀態');
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

  Future<void> _grant(AdminDriverRow driver) async {
    final TextEditingController amount = TextEditingController();
    final String? result = await showDialog<String>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('按金入帳'),
        content: TextField(
          controller: amount,
          autofocus: true,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: const InputDecoration(
            labelText: '金額',
            prefixText: 'HK\$ ',
            helperText: '司機已於線下付款後才在此入帳',
          ),
        ),
        actions: <Widget>[
          TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('取消')),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(amount.text.trim()),
            child: const Text('入帳'),
          ),
        ],
      ),
    );
    amount.dispose();

    final double? value = result == null ? null : double.tryParse(result);
    if (value == null || value <= 0) {
      return;
    }
    if (!mounted) {
      return;
    }

    setState(() => _busy = true);
    try {
      final DepositGrantResult grant = await ref
          .read(adminRepositoryProvider)
          .grantDeposit(driver.id, amountHkd: value);
      ref.invalidate(adminDriversProvider);
      if (mounted) {
        showInfo(
          context,
          '已入帳 HK\$${value.toStringAsFixed(1)}，'
          '餘額 ${grant.balanceHkd.display}，狀態 ${grant.driverStatus.labelZh}',
        );
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
    final AsyncValue<Paged<AdminDriverRow>> drivers = ref.watch(adminDriversProvider(_filter));

    return Scaffold(
      appBar: AppBar(
        title: const Text('司機審核'),
        actions: <Widget>[
          IconButton(
            onPressed: () => ref.invalidate(adminDriversProvider),
            tooltip: '重新整理',
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Column(
        children: <Widget>[
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(
              horizontal: AppTheme.space4,
              vertical: AppTheme.space3,
            ),
            child: Row(
              children: <Widget>[
                ChoiceChip(
                  label: const Text('全部'),
                  selected: _filter == null,
                  onSelected: (bool _) => setState(() => _filter = null),
                ),
                for (final DriverStatus status in DriverStatus.values) ...<Widget>[
                  const SizedBox(width: AppTheme.space2),
                  ChoiceChip(
                    label: Text(status.labelZh),
                    selected: _filter == status,
                    onSelected: (bool selected) =>
                        setState(() => _filter = selected ? status : null),
                  ),
                ],
              ],
            ),
          ),
          Expanded(
            child: AsyncValueView<Paged<AdminDriverRow>>(
              value: drivers,
              onRetry: () => ref.invalidate(adminDriversProvider),
              builder: (Paged<AdminDriverRow> page) {
                if (page.items.isEmpty) {
                  return const EmptyView(icon: Icons.inbox_outlined, title: '沒有符合條件的司機');
                }
                return RefreshIndicator(
                  onRefresh: () async => ref.invalidate(adminDriversProvider),
                  child: ListView.separated(
                    padding: const EdgeInsets.fromLTRB(
                      AppTheme.space4,
                      0,
                      AppTheme.space4,
                      AppTheme.space4,
                    ),
                    itemCount: page.items.length,
                    separatorBuilder: (BuildContext context, int index) =>
                        const SizedBox(height: AppTheme.space3 - 2),
                    itemBuilder: (BuildContext context, int index) =>
                        _driverCard(page.items[index]),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _driverCard(AdminDriverRow driver) {
    final ThemeData theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.space4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                StatusChip.driver(driver.status, context),
                const SizedBox(width: AppTheme.space3 - 2),
                Text(driver.taxiType.labelZh, style: theme.textTheme.bodyMedium),
              ],
            ),
            const SizedBox(height: AppTheme.space3),
            DetailRow(label: '的士司機證', value: driver.taxiDriverPlateNo),
            DetailRow(label: '車輛登記', value: driver.vehicleRegMark),
            DetailRow(label: 'Profile ID', value: driver.id),
            const SizedBox(height: AppTheme.space3),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: <Widget>[
                if (driver.status == DriverStatus.pendingKyc) ...<Widget>[
                  FilledButton(
                    onPressed: _busy ? null : () => _review(driver, 'approve'),
                    child: const Text('通過審核'),
                  ),
                  OutlinedButton(
                    onPressed: _busy ? null : () => _review(driver, 'reject'),
                    child: const Text('拒絕'),
                  ),
                ],
                if (driver.status == DriverStatus.depositRequired ||
                    driver.status == DriverStatus.active)
                  FilledButton.tonal(
                    onPressed: _busy ? null : () => _grant(driver),
                    child: const Text('按金入帳'),
                  ),
                if (driver.status == DriverStatus.active)
                  OutlinedButton(
                    onPressed: _busy ? null : () => _review(driver, 'suspend'),
                    child: const Text('停權'),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
