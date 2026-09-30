import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../models/driver.dart';
import '../../models/enums.dart';
import '../../router/app_router.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import 'widgets.dart';

/// Shared account screen.
///
/// Also the entry point into driver mode. Because `UserRole.DRIVER` is never
/// assigned by the backend, this screen is where an account discovers whether it
/// has a driver profile at all — `GET /drivers/me` 404s for one that does not.
class AccountScreen extends ConsumerWidget {
  const AccountScreen({this.driverMode = false, super.key});

  /// True when shown from inside the driver shell, which flips the mode switch
  /// to point back at the passenger app.
  final bool driverMode;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeData theme = Theme.of(context);
    final AsyncValue<DriverProfile?> profile = ref.watch(driverProfileProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('帳戶')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    ref.watch(currentUserProvider)?.phoneMasked ?? '—',
                    style: theme.textTheme.titleLarge,
                  ),
                  const SizedBox(height: 10),
                  StatusChip(
                    label: switch (ref.watch(currentUserProvider)?.role) {
                      UserRole.admin => '管理員',
                      UserRole.driver => '司機',
                      _ => '乘客',
                    },
                    color: theme.colorScheme.primary,
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          Text('司機', style: theme.textTheme.titleSmall),
          const SizedBox(height: 8),
          Card(
            child: AsyncValueView<DriverProfile?>(
              value: profile,
              onRetry: () => ref.invalidate(driverProfileProvider),
              builder: (DriverProfile? data) =>
                  _DriverSection(profile: data, driverMode: driverMode),
            ),
          ),
          const SizedBox(height: 16),
          Text('帳戶操作', style: theme.textTheme.titleSmall),
          const SizedBox(height: 8),
          Card(
            child: Column(
              children: <Widget>[
                ListTile(
                  leading: const Icon(Icons.logout),
                  title: const Text('登出'),
                  subtitle: const Text('會撤銷此帳戶所有有效憑證'),
                  onTap: () => _confirmSignOut(context, ref),
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),
          Center(
            child: Text(
              'RealTaxi HK  ·  車費為估算，實際以錶收費為準',
              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _confirmSignOut(BuildContext context, WidgetRef ref) async {
    final bool confirmed = await confirmDestructive(
      context,
      title: '登出？',
      message: '伺服器會撤銷所有 access token 及 refresh token，需要重新以驗證碼登入。',
      confirmLabel: '登出',
      cancelLabel: '取消',
    );
    if (confirmed) {
      await ref.read(authControllerProvider.notifier).signOut();
    }
  }
}

class _DriverSection extends ConsumerWidget {
  const _DriverSection({required this.profile, required this.driverMode});

  final DriverProfile? profile;
  final bool driverMode;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeData theme = Theme.of(context);

    // No profile: offer to become a driver.
    if (profile == null) {
      return Column(
        children: <Widget>[
          ListTile(
            leading: const Icon(Icons.badge_outlined),
            title: const Text('成為司機'),
            subtitle: const Text('需要香港身份證末四位、的士證號及車牌'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => context.push(Routes.driverOnboarding),
          ),
        ],
      );
    }

    final DriverProfile driver = profile!;
    final bool canDrive = driver.status.canDrive;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
          child: Row(
            children: <Widget>[
              StatusChip.driver(driver.status, context),
              const SizedBox(width: 10),
              Text(driver.taxiType.labelZh, style: theme.textTheme.bodyMedium),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Column(
            children: <Widget>[
              DetailRow(label: '的士證號', value: driver.taxiDriverPlateNo),
              DetailRow(label: '車輛登記', value: driver.vehicleRegMark),
              if (driver.deposit != null)
                DetailRow(label: '按金', valueWidget: MoneyText(driver.deposit!.balanceHkd)),
              if (driver.deposit != null && !driver.deposit!.isFulfilled)
                DetailRow(label: '尚欠', valueWidget: MoneyText(driver.deposit!.shortfall)),
            ],
          ),
        ),
        const Divider(height: 24),
        // Fleet membership is separate from driver status: a driver can be on a
        // roster while their own profile is not yet ACTIVE (they are rostered but
        // not billable). So this is offered whenever a profile exists, not only
        // when they can drive.
        ListTile(
          leading: const Icon(Icons.groups_outlined),
          title: const Text('我的車隊'),
          subtitle: const Text('名單、每週車隊收費與結算紀錄'),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => context.push(Routes.driverFleet),
        ),
        if (canDrive)
          ListTile(
            leading: const Icon(Icons.drive_eta_outlined),
            title: Text(driverMode ? '返回乘客模式' : '司機模式'),
            subtitle: Text(driverMode ? '回到叫車介面' : '接單、上線、查看收入'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => context.go(driverMode ? Routes.request : Routes.driverJobs),
          )
        else
          ListTile(
            leading: const Icon(Icons.info_outline),
            title: const Text('司機帳戶尚未啟用'),
            subtitle: Text(switch (driver.status) {
              DriverStatus.pendingKyc => '身份審核中，通過後需繳交按金',
              DriverStatus.depositRequired => '按金未達要求，完成後即可接單',
              DriverStatus.suspended => '帳戶已停權，請聯絡平台',
              DriverStatus.terminated => '帳戶已終止',
              DriverStatus.active => '',
            }),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => context.push(Routes.driverOnboarding),
          ),
      ],
    );
  }
}
