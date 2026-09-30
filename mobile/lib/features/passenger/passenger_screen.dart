import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../router/app_router.dart';

/// Passenger shell: a bottom bar over an `IndexedStack` of three branches, so
/// each tab keeps its own scroll position and map camera.
class PassengerScreen extends StatelessWidget {
  const PassengerScreen({required this.shell, super.key});

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
            icon: Icon(Icons.local_taxi_outlined),
            selectedIcon: Icon(Icons.local_taxi),
            label: '叫車',
          ),
          NavigationDestination(
            icon: Icon(Icons.receipt_long_outlined),
            selectedIcon: Icon(Icons.receipt_long),
            label: '行程',
          ),
          NavigationDestination(
            icon: Icon(Icons.person_outline),
            selectedIcon: Icon(Icons.person),
            label: '帳戶',
          ),
        ],
      ),
      // A live trip is one tap away from any tab.
      //
      // Deliberately not `FloatingActionButton.small`: that is 40dp, under the
      // 44pt minimum `layout.md` sets for a touch target, and this button is
      // the primary action for the whole app.
      floatingActionButton: shell.currentIndex == 0
          ? null
          : FloatingActionButton(
              onPressed: () => context.go(Routes.request),
              tooltip: '叫車',
              child: const Icon(Icons.add),
            ),
    );
  }
}
