import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'core/theme/app_theme.dart';
import 'core/theme/fluid_background.dart';
import 'router/app_router.dart';

class HkfastdcApp extends ConsumerWidget {
  const HkfastdcApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final GoRouter router = ref.watch(routerProvider);

    return MaterialApp.router(
      title: 'hkfastdc',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      themeMode: ThemeMode.system,
      builder: (BuildContext context, Widget? child) =>
          FluidBackground(child: child ?? const SizedBox.shrink()),
      routerConfig: router,
      locale: const Locale('zh', 'HK'),
      // Only the locales the app actually has copy for. `Locale('en')` used to
      // be listed here, but there is no English resource anywhere in the tree —
      // no `.arb`, no `l10n` directory — so listing it advertised a language the
      // app cannot render. The product ships in Chinese only.
      supportedLocales: const <Locale>[Locale('zh', 'HK'), Locale('zh')],
      localizationsDelegates: const <LocalizationsDelegate<Object>>[
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
    );
  }
}
