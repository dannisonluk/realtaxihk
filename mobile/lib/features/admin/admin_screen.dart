import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

/// Admin console shell: KYC queue, refund queue, settlement, account.
///
/// The account tab is its own branch rather than a link to the passenger one:
/// `_redirect` in `app_router.dart` bounces an admin out of `/passenger/**`,
/// because the passenger and driver surfaces are not reachable for an ADMIN
/// account.
class AdminScreen extends StatelessWidget {
  const AdminScreen({required this.shell, super.key});

  final StatefulNavigationShell shell;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: shell,
      bottomNavigationBar: NavigationBar(
        selectedIndex: shell.currentIndex,
        onDestinationSelected: (int index) =>
            shell.goBranch(index, initialLocation: index == shell.currentIndex),
        destinations: const <NavigationDestination>[
          NavigationDestination(
            icon: Icon(Icons.badge_outlined),
            selectedIcon: Icon(Icons.badge),
            label: '司機審核',
          ),
          NavigationDestination(
            icon: Icon(Icons.assignment_return_outlined),
            selectedIcon: Icon(Icons.assignment_return),
            label: '退款',
          ),
          NavigationDestination(
            icon: Icon(Icons.calculate_outlined),
            selectedIcon: Icon(Icons.calculate),
            label: '結算',
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
