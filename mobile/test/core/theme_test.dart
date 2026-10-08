import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hkfastdc_mobile/core/theme/app_theme.dart';
import 'package:hkfastdc_mobile/core/theme/fluid_background.dart';

void main() {
  group('AppTheme', () {
    test('light scheme has no red family', () {
      final ThemeData light = AppTheme.light();
      final ColorScheme s = light.colorScheme;
      // Primary is bright blue, not a red hue.
      expect(s.primary, AppTheme.blue);
      // Error role is amber, not red.
      expect(s.error, AppTheme.loss);
      // Surface is white.
      expect(s.surface, AppTheme.paper);
    });

    test('dark scheme uses Cod Gray base', () {
      final ThemeData dark = AppTheme.dark();
      final ColorScheme s = dark.colorScheme;
      expect(s.surface, AppTheme.ink);
      expect(dark.scaffoldBackgroundColor, Colors.transparent);
    });

    test('money colors are green/amber, no red', () {
      expect(AppTheme.gain, const Color(0xFF0B8A4D));
      expect(AppTheme.loss, const Color(0xFFB7791F));
    });
  });

  testWidgets('FluidBackground paints and wraps child', (WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        darkTheme: AppTheme.dark(),
        home: FluidBackground(child: const Scaffold(body: Text('hi'))),
      ),
    );
    expect(find.text('hi'), findsOneWidget);
    expect(find.byType(FluidBackground), findsOneWidget);
  });
}
