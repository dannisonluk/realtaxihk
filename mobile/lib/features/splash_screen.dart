import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../features/shared/widgets.dart';
import '../state/providers.dart';

/// Shown while the stored session is re-validated on cold start.
///
/// It also owns the *restore failure* path: `AuthController.build()` rethrows a
/// non-auth error (typically offline) and deliberately keeps the stored tokens,
/// so this screen offers a retry rather than signing the user out.
class SplashScreen extends ConsumerWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<Object?> auth = ref.watch(authControllerProvider);

    if (auth.hasError) {
      return Scaffold(
        body: ErrorView(error: auth.error!, onRetry: () => ref.invalidate(authControllerProvider)),
      );
    }

    return Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(Icons.local_taxi_rounded, size: 64, color: Theme.of(context).colorScheme.primary),
            const SizedBox(height: 20),
            Text('RealTaxi HK', style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 28),
            const SizedBox(
              width: 24,
              height: 24,
              child: CircularProgressIndicator(strokeWidth: 2.5),
            ),
          ],
        ),
      ),
    );
  }
}
