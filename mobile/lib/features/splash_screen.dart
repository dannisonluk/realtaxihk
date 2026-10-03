import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/theme/app_theme.dart';
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
            // The poster *is* the wordmark, so there is no `Text` beside it —
            // that would print "hkfastdc" twice, a few points apart. The
            // wordmark still reaches a screen reader; see [BrandLogo].
            const BrandLogo(size: 240),
            const SizedBox(height: AppTheme.space8),
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
