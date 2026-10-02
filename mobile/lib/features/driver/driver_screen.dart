import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/theme/app_theme.dart';
import '../../router/app_router.dart';
import '../../state/data_providers.dart';

/// Driver shell.
///
/// The gate is here rather than in the router because it needs the driver
/// profile, which is an async read. A driver who is not `ACTIVE` cannot reach the
/// job list — the backend would 403 every call anyway (`grab` and
/// `POST /drivers/location` both require `DriverStatus.ACTIVE`).
class DriverScreen extends ConsumerWidget {
  const DriverScreen({required this.shell, super.key});

  final StatefulNavigationShell shell;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bool canDrive = ref.watch(driverProfileProvider).value?.status.canDrive ?? false;

    if (!canDrive) {
      return Scaffold(
        body: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              const Icon(Icons.lock_outline, size: 40),
              const SizedBox(height: AppTheme.space4),
              const Text('司機帳戶尚未啟用'),
              const SizedBox(height: AppTheme.space4),
              FilledButton.tonal(
                onPressed: () => context.push(Routes.driverOnboarding),
                child: const Text('查看狀態'),
              ),
              const SizedBox(height: AppTheme.space2),
              TextButton(onPressed: () => context.go(Routes.request), child: const Text('返回乘客模式')),
            ],
          ),
        ),
      );
    }

    return Scaffold(
      body: shell,
      bottomNavigationBar: NavigationBar(
        selectedIndex: shell.currentIndex,
        onDestinationSelected: (int index) =>
            shell.goBranch(index, initialLocation: index == shell.currentIndex),
        destinations: const <NavigationDestination>[
          NavigationDestination(
            icon: Icon(Icons.work_outline),
            selectedIcon: Icon(Icons.work),
            label: '接單',
          ),
          NavigationDestination(
            icon: Icon(Icons.account_balance_wallet_outlined),
            selectedIcon: Icon(Icons.account_balance_wallet),
            label: '收入',
          ),
          NavigationDestination(
            icon: Icon(Icons.person_outline),
            selectedIcon: Icon(Icons.person),
            label: '帳戶',
          ),
        ],
      ),
    );
  }
}
