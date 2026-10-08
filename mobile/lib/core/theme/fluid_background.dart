import 'dart:ui' as ui;

import 'package:flutter/material.dart';

/// A soft, full-bleed fluid gradient used behind the whole app.
///
/// The page background is transparent in [ThemeData], so this layer shows
/// through every `Scaffold` without each screen needing its own decoration.
/// Cards and other material surfaces remain opaque on top, keeping reading
/// areas clean while the page edges keep the AI-fluid blue/teal/lavender tone.
class FluidBackground extends StatelessWidget {
  const FluidBackground({super.key, required this.child});

  final Widget child;

  static const List<Color> _lightBase = <Color>[
    Color(0xFFFFFFFF),
    Color(0xFFF4FAFF),
    Color(0xFFF2FBF9),
    Color(0xFFFAF7FF),
  ];

  static const List<Color> _darkBase = <Color>[
    Color(0xFF080808),
    Color(0xFF0B1014),
    Color(0xFF10131B),
    Color(0xFF0A0D0F),
  ];

  static const List<_Wave> _lightWaves = <_Wave>[
    _Wave(
      top: 0.04,
      control1: 0.34,
      control2: 0.08,
      bottom: 0.42,
      color: Color(0xFF86DAFF),
      alpha: 0.18,
      blur: 46,
    ),
    _Wave(
      top: 0.28,
      control1: 0.72,
      control2: 0.22,
      bottom: 0.62,
      color: Color(0xFF5EDCE5),
      alpha: 0.15,
      blur: 48,
    ),
    _Wave(
      top: 0.58,
      control1: 0.18,
      control2: 0.82,
      bottom: 0.9,
      color: Color(0xFFC5BFEE),
      alpha: 0.16,
      blur: 46,
    ),
    _Wave(
      top: 0.76,
      control1: 0.5,
      control2: 0.0,
      bottom: 1.05,
      color: Color(0xFFF4D35E),
      alpha: 0.1,
      blur: 36,
    ),
  ];

  static const List<_Wave> _darkWaves = <_Wave>[
    _Wave(
      top: 0.02,
      control1: 0.34,
      control2: 0.08,
      bottom: 0.42,
      color: Color(0xFF126DA8),
      alpha: 0.36,
      blur: 46,
    ),
    _Wave(
      top: 0.28,
      control1: 0.72,
      control2: 0.22,
      bottom: 0.62,
      color: Color(0xFF0E7E8A),
      alpha: 0.34,
      blur: 48,
    ),
    _Wave(
      top: 0.58,
      control1: 0.18,
      control2: 0.82,
      bottom: 0.9,
      color: Color(0xFF5A5FA8),
      alpha: 0.38,
      blur: 46,
    ),
    _Wave(
      top: 0.76,
      control1: 0.5,
      control2: 0.0,
      bottom: 1.05,
      color: Color(0xFFD8A93C),
      alpha: 0.2,
      blur: 36,
    ),
  ];

  @override
  Widget build(BuildContext context) {
    final bool isDark = Theme.of(context).brightness == Brightness.dark;

    return DecoratedBox(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: isDark ? _darkBase : _lightBase,
        ),
      ),
      child: CustomPaint(
        painter: _FluidPainter(isDark: isDark),
        child: child,
      ),
    );
  }
}

class _FluidPainter extends CustomPainter {
  const _FluidPainter({required this.isDark});

  final bool isDark;

  @override
  void paint(Canvas canvas, Size size) {
    if (size.isEmpty) {
      return;
    }

    final List<_Wave> waves = isDark ? FluidBackground._darkWaves : FluidBackground._lightWaves;
    for (final _Wave wave in waves) {
      final Path path = Path()
        ..moveTo(-size.width * 0.1, size.height * wave.top)
        ..cubicTo(
          size.width * 0.25,
          size.height * wave.control1,
          size.width * 0.72,
          size.height * wave.control2,
          size.width * 1.1,
          size.height * wave.bottom,
        )
        ..lineTo(size.width * 1.1, size.height * wave.bottom + size.height * 0.24)
        ..cubicTo(
          size.width * 0.72,
          size.height * wave.control2 + size.height * 0.24,
          size.width * 0.25,
          size.height * wave.control1 + size.height * 0.24,
          -size.width * 0.1,
          size.height * wave.top + size.height * 0.24,
        )
        ..close();

      final Paint paint = Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: <Color>[
            wave.color.withValues(alpha: wave.alpha),
            wave.color.withValues(alpha: 0),
          ],
        ).createShader(Offset.zero & size)
        ..maskFilter = ui.MaskFilter.blur(ui.BlurStyle.normal, wave.blur);

      canvas.drawPath(path, paint);
    }
  }

  @override
  bool shouldRepaint(covariant _FluidPainter oldDelegate) => oldDelegate.isDark != isDark;
}

class _Wave {
  const _Wave({
    required this.top,
    required this.control1,
    required this.control2,
    required this.bottom,
    required this.color,
    required this.alpha,
    required this.blur,
  });

  final double top;
  final double control1;
  final double control2;
  final double bottom;
  final Color color;
  final double alpha;
  final double blur;
}
