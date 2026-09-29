import 'package:flutter/material.dart';

/// Material 3 theme.
///
/// The brand colour is HK taxi red (#D2232A is the licensed-urban-taxi red, but
/// it is too dark for a primary in dark mode, so the seed is a slightly lifted
/// red). Money colours follow the local convention — **red for an increase,
/// green for a decrease** — which is the opposite of the US/EU default, so they
/// are defined here rather than left to `Colors.red` / `Colors.green` at the
/// call site.
abstract final class AppTheme {
  static const Color seed = Color(0xFFE23A3A);

  /// Money up / credit.
  static const Color gain = Color(0xFFD32F2F);

  /// Money down / debit.
  static const Color loss = Color(0xFF2E7D32);

  static const Color pending = Color(0xFFF9A825);

  static ThemeData light() => _build(Brightness.light);

  static ThemeData dark() => _build(Brightness.dark);

  static ThemeData _build(Brightness brightness) {
    final ColorScheme scheme = ColorScheme.fromSeed(seedColor: seed, brightness: brightness);
    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      appBarTheme: AppBarTheme(
        centerTitle: false,
        backgroundColor: scheme.surface,
        foregroundColor: scheme.onSurface,
        elevation: 0,
        scrolledUnderElevation: 1,
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: BorderSide(color: scheme.outlineVariant),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.4),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide.none,
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size.fromHeight(50),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size.fromHeight(50),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        ),
      ),
      listTileTheme: const ListTileThemeData(
        contentPadding: EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      ),
      dividerTheme: DividerThemeData(color: scheme.outlineVariant, thickness: 1, space: 1),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    );
  }

  /// Colour for a signed amount, using the Hong Kong convention.
  static Color moneyColor(BuildContext context, num amount) {
    if (amount > 0) {
      return gain;
    }
    if (amount < 0) {
      return loss;
    }
    return Theme.of(context).colorScheme.onSurfaceVariant;
  }
}
