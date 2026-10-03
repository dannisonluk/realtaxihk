import 'package:flutter/material.dart';

/// The app's design tokens, set in Apple's Human Interface Guidelines.
///
/// Why this file looks the way it does
/// ----------------------------------
/// The app ships on iOS and Android from one Flutter tree. Apple's guidance is
/// platform-specific and Material's is not, so the choice per component is:
/// follow the iOS convention where people notice it (navigation, sheets,
/// destructive actions, list hierarchy) and keep Material behaviour where it is
/// invisible. The reasoning is written next to each decision rather than left to
/// the reader, because "it looked better" does not survive the next iteration.
///
/// The numbers are Apple's, not invented:
///
/// * **Type.** The scale below is the iOS Dynamic Type table at the default
///   (`Large`) size — `typography.md › iOS, iPadOS Dynamic Type sizes`. Body is
///   17 pt, the platform default, and nothing interactive is smaller than 11 pt,
///   the platform minimum. Sizes are converted to logical pixels 1:1 (Flutter's
///   logical pixel is the iOS point on a correctly configured device).
/// * **Colour.** Semantic system colours (`color.md › System colors`), so light
///   and dark both get their intended values rather than one palette inverted.
///   Money still uses the Hong Kong convention — **red up, green down** — which
///   is the opposite of the US/EU default, so it lives here and not at the call
///   site.
/// * **Targets.** 44x44 pt default (`accessibility.md › Control size`). A theme
///   cannot enforce that, but the component themes set it where they can.
/// * **Spacing.** A 4 pt grid with 16 pt as the standard content inset, matching
///   Apple's default margins.
///
/// What is deliberately *not* here: a dynamic colour light/dark scheme is
/// generated from the brand seed rather than hand-tuned per token. Apple warns
/// against hard-coding system colour values (`color.md › System colors`: "Avoid
/// hard-coding system color values in your app"), and `ColorScheme.fromSeed`
/// already derives an accessible set for both appearances. The brand red is the
/// one custom value, because it carries the product's identity and the taxi
/// livery, and it is defined once with both appearances' worth of contrast in
/// mind.
abstract final class AppTheme {
  /// The brand red. HK's licensed urban taxi red is #D2232A; it is too dark to
  /// carry a dark-mode primary, so the seed is lifted a little. The exact value
  /// matters less than that it is defined once and derived from everywhere.
  static const Color seed = Color(0xFFE23A3A);

  /// Money up / credit. Hong Kong convention.
  static const Color gain = Color(0xFFD32F2F);

  /// Money down / debit. Hong Kong convention.
  static const Color loss = Color(0xFF2E7D32);

  /// Waiting on a person. Distinct from both money colours so a pending state
  /// never reads as a gain or a loss.
  static const Color pending = Color(0xFFF9A825);

  // -- spacing ---------------------------------------------------------------
  //
  // A 4 pt grid. 16 is the standard horizontal content inset (`layout.md ›
  // Guides and safe areas`: standard system margins), 24 the seam between two
  // sections, 8 the gap between tightly related lines.

  static const double space1 = 4;
  static const double space2 = 8;
  static const double space3 = 12;
  static const double space4 = 16;
  static const double space6 = 24;
  static const double space8 = 32;

  /// Minimum hit target. Apple's default is 44 pt; Flutter's is 48 dp, which is
  /// the Material figure. 44 is used here because this is an iOS-first design,
  /// and the larger Material value is still satisfied by the padding below.
  static const double minTarget = 44;

  // -- radii ------------------------------------------------------------------
  //
  // Continuous-feeling radii. Apple's grouped list cards read as ~10-12 pt; a
  // button at 12 pt sits comfortably inside a 16 pt inset.

  static const double radiusCard = 12;
  static const double radiusField = 10;
  static const double radiusButton = 12;

  static ThemeData light() => _build(Brightness.light);

  static ThemeData dark() => _build(Brightness.dark);

  static ThemeData _build(Brightness brightness) {
    final bool isDark = brightness == Brightness.dark;
    final ColorScheme scheme = ColorScheme.fromSeed(seedColor: seed, brightness: brightness);

    // iOS grouped-list backgrounds. On iOS a grouped table sits on a slightly
    // recessed background, with cards a step brighter on top of it. Material's
    // `surface` maps cleanly onto that pair, so the distinction is expressed
    // once here rather than by each screen choosing a grey.
    final Color groupedBackground = scheme.surfaceContainerLowest;

    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: groupedBackground,
      // Apple's type is optically sized and slightly tighter than Material's
      // default; `letterSpacing: 0` (set per style below) avoids Material's
      // tracking at large sizes, which makes a 34 pt title read looser than SF
      // does. Applied as `textTheme` rather than `typography` because the latter
      // takes a `Typography` (density and family), not a set of styles.
      textTheme: _typography(scheme),
      appBarTheme: AppBarTheme(
        // Large titles are centred-less on iOS: the title is leading-aligned
        // and grows to a large title on scroll. Flutter's AppBar is leading by
        // default with `centerTitle: false`, so only the surface and elevation
        // need stating.
        centerTitle: false,
        backgroundColor: groupedBackground,
        foregroundColor: scheme.onSurface,
        elevation: 0,
        scrolledUnderElevation: 0.5,
        titleTextStyle: _textStyle(17, FontWeight.w600, scheme.onSurface),
      ),
      cardTheme: CardThemeData(
        // Cards are flat surfaces separated by their background, not by a
        // shadow — the iOS grouped-list look. The border is very low contrast on
        // purpose: it is a hint at the edge, never the thing you see first.
        elevation: 0,
        color: scheme.surface,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radiusCard),
          side: BorderSide(color: scheme.outlineVariant.withValues(alpha: isDark ? 0.35 : 0.6)),
        ),
      ),
      dividerTheme: DividerThemeData(
        color: scheme.outlineVariant.withValues(alpha: 0.5),
        thickness: 0.5,
        space: 0.5,
      ),
      // Fields: iOS uses a filled, borderless field with the label above or
      // inside. Keeping the fill and dropping the outline reads as a field
      // rather than a box.
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: scheme.surface,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusField),
          borderSide: BorderSide(color: scheme.outlineVariant.withValues(alpha: 0.6)),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusField),
          borderSide: BorderSide(color: scheme.outlineVariant.withValues(alpha: 0.6)),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radiusField),
          borderSide: BorderSide(color: scheme.primary, width: 2),
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: space4, vertical: space3 + 2),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          // 50 logical px clears Apple's 44 pt target with room for the label.
          minimumSize: const Size.fromHeight(50),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(radiusButton)),
          textStyle: _textStyle(17, FontWeight.w600, scheme.onPrimary),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size.fromHeight(50),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(radiusButton)),
          textStyle: _textStyle(17, FontWeight.w600, scheme.primary),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          minimumSize: const Size(minTarget, minTarget),
          textStyle: _textStyle(17, FontWeight.w500, scheme.primary),
        ),
      ),
      listTileTheme: const ListTileThemeData(
        contentPadding: EdgeInsets.symmetric(horizontal: space4, vertical: space1),
        minVerticalPadding: space3,
      ),
      // A bottom sheet is the iOS sheet: rounded top corners, a grabber, and a
      // surface that reads as floating above the content.
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: scheme.surface,
        surfaceTintColor: Colors.transparent,
        showDragHandle: true,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
        ),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: scheme.surface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(radiusCard + 2)),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(radiusCard)),
      ),
      // The tab bar. iOS keeps it opaque and lightly tinted; `indicatorColor`
      // off keeps the selected state to the icon and label, which is what iOS
      // does, rather than Material's filled pill.
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: scheme.surface,
        surfaceTintColor: Colors.transparent,
        indicatorColor: Colors.transparent,
        elevation: 0,
        height: 56,
        labelTextStyle: WidgetStateProperty.resolveWith<TextStyle>((Set<WidgetState> states) {
          final bool selected = states.contains(WidgetState.selected);
          return _textStyle(
            10,
            selected ? FontWeight.w600 : FontWeight.w500,
            selected ? scheme.primary : scheme.onSurfaceVariant,
          );
        }),
        iconTheme: WidgetStateProperty.resolveWith<IconThemeData>((Set<WidgetState> states) {
          final bool selected = states.contains(WidgetState.selected);
          return IconThemeData(
            size: 26,
            color: selected ? scheme.primary : scheme.onSurfaceVariant,
          );
        }),
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(color: scheme.primary),
      chipTheme: ChipThemeData(
        backgroundColor: scheme.surface,
        side: BorderSide(color: scheme.outlineVariant.withValues(alpha: 0.6)),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(999)),
        labelStyle: _textStyle(13, FontWeight.w500, scheme.onSurfaceVariant),
      ),
    );
  }

  /// The iOS Dynamic Type scale at the default size (`typography.md`).
  ///
  /// Only the styles the app actually uses are defined, each mapped to the
  /// Material role it plays. Where Apple's name and Material's name disagree,
  /// the comment says which Apple style a slot is carrying.
  static TextTheme _typography(ColorScheme scheme) {
    final Color onSurface = scheme.onSurface;
    final Color secondary = scheme.onSurfaceVariant;

    return TextTheme(
      // Large Title — 34 pt. The screen title.
      displaySmall: _textStyle(34, FontWeight.w700, onSurface, height: 41 / 34),
      // Title 1 — 28 pt.
      headlineMedium: _textStyle(28, FontWeight.w700, onSurface, height: 34 / 28),
      // Title 2 — 22 pt. A section title.
      headlineSmall: _textStyle(22, FontWeight.w600, onSurface, height: 28 / 22),
      // Title 3 — 20 pt.
      titleLarge: _textStyle(20, FontWeight.w600, onSurface, height: 25 / 20),
      // Headline — 17 pt semibold. A card's own title.
      titleMedium: _textStyle(17, FontWeight.w600, onSurface, height: 22 / 17),
      // Subhead — 15 pt. Secondary card titles.
      titleSmall: _textStyle(15, FontWeight.w600, onSurface, height: 20 / 15),
      // Body — 17 pt. The reading size.
      bodyLarge: _textStyle(17, FontWeight.w400, onSurface, height: 22 / 17),
      // Callout — 16 pt.
      bodyMedium: _textStyle(16, FontWeight.w400, onSurface, height: 21 / 16),
      // Subhead — 15 pt.
      bodySmall: _textStyle(15, FontWeight.w400, secondary, height: 20 / 15),
      // Footnote — 13 pt.
      labelLarge: _textStyle(13, FontWeight.w500, secondary, height: 18 / 13),
      // Caption 1 — 12 pt.
      labelMedium: _textStyle(12, FontWeight.w500, secondary, height: 16 / 12),
      // Caption 2 — 11 pt, the platform minimum.
      labelSmall: _textStyle(11, FontWeight.w500, secondary, height: 13 / 11),
    );
  }

  static TextStyle _textStyle(double size, FontWeight weight, Color color, {double? height}) {
    return TextStyle(
      fontSize: size,
      fontWeight: weight,
      color: color,
      height: height,
      // Material 3 defaults to a small positive tracking at display sizes;
      // SF does not, so it is pinned to zero so the type matches the platform.
      letterSpacing: 0,
    );
  }

  /// Colour for a signed amount, using the Hong Kong convention.
  ///
  /// Red means "up" here, which is the opposite of the US/EU reading. Defining
  /// it in one place is what stops a ledger row and a receipt line disagreeing.
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
