import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/format/money.dart';
import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../models/enums.dart';

/// Renders an [AsyncValue] with a spinner, a retryable error, and a body.
///
/// Every screen uses this so a failure looks the same everywhere and always
/// offers the same way out. [onRetry] is what the retry button calls — pass
/// `ref.invalidate(provider)` for a `FutureProvider`, or a controller method for
/// an action.
class AsyncValueView<T> extends StatelessWidget {
  const AsyncValueView({
    required this.value,
    required this.builder,
    this.onRetry,
    this.loading,
    super.key,
  });

  final AsyncValue<T> value;
  final Widget Function(T data) builder;
  final VoidCallback? onRetry;
  final Widget? loading;

  @override
  Widget build(BuildContext context) {
    return value.when(
      skipLoadingOnRefresh: false,
      data: builder,
      loading: () => loading ?? const Center(child: CircularProgressIndicator()),
      error: (Object error, StackTrace stack) => ErrorView(error: error, onRetry: onRetry),
    );
  }
}

/// A failure with the server's own message and, where it helps, a hint about
/// what to do next.
class ErrorView extends StatelessWidget {
  const ErrorView({required this.error, this.onRetry, super.key});

  final Object error;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final String hint = switch (error) {
      final ApiException e when e.code == ApiException.rateLimited =>
        e.retryAfter == null
            ? 'Please wait a moment and try again.'
            : 'Please wait ${(e.retryAfter!.inSeconds / 60).ceil()} minutes and try again.',
      final ApiException e when e.code == ApiException.serviceUnavailable =>
        'The service is busy right now. Please try again shortly.',
      final ApiException e when e.code == ApiException.network =>
        'Check your connection. Nothing was sent to the server.',
      _ => '',
    };

    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(Icons.error_outline, size: 44, color: theme.colorScheme.error),
            const SizedBox(height: 16),
            Text(error.userMessage, textAlign: TextAlign.center, style: theme.textTheme.bodyLarge),
            if (hint.isNotEmpty) ...<Widget>[
              const SizedBox(height: 8),
              Text(
                hint,
                textAlign: TextAlign.center,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ],
            if (onRetry != null) ...<Widget>[
              const SizedBox(height: 24),
              FilledButton.tonal(onPressed: onRetry, child: const Text('Try again')),
            ],
          ],
        ),
      ),
    );
  }
}

/// An empty state with an optional call to action.
class EmptyView extends StatelessWidget {
  const EmptyView({required this.icon, required this.title, this.subtitle, this.action, super.key});

  final IconData icon;
  final String title;
  final String? subtitle;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(icon, size: 44, color: theme.colorScheme.onSurfaceVariant),
            const SizedBox(height: 16),
            Text(title, style: theme.textTheme.titleMedium),
            if (subtitle != null) ...<Widget>[
              const SizedBox(height: 6),
              Text(
                subtitle!,
                textAlign: TextAlign.center,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ],
            if (action != null) ...<Widget>[const SizedBox(height: 20), action!],
          ],
        ),
      ),
    );
  }
}

/// An amount, coloured by sign using the Hong Kong convention (red up, green
/// down) — see [AppTheme.moneyColor].
class MoneyText extends StatelessWidget {
  const MoneyText(this.money, {this.signed = false, this.style, this.showSymbol = true, super.key});

  final Money money;

  /// Prefix with an explicit `+`/`-`, for a ledger row.
  final bool signed;

  final TextStyle? style;
  final bool showSymbol;

  @override
  Widget build(BuildContext context) {
    final String text = signed ? money.signedHkd : (showSymbol ? money.hkd : money.display);
    return Text(
      text,
      style: (style ?? Theme.of(context).textTheme.titleMedium)?.copyWith(
        color: signed ? AppTheme.moneyColor(context, money.asDouble) : null,
        fontWeight: FontWeight.w600,
        fontFeatures: const <FontFeature>[FontFeature.tabularFigures()],
      ),
    );
  }
}

/// A small status pill.
class StatusChip extends StatelessWidget {
  const StatusChip({required this.label, required this.color, super.key});

  factory StatusChip.order(OrderStatus status, BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return StatusChip(
      label: status.labelZh,
      color: switch (status) {
        OrderStatus.created => scheme.onSurfaceVariant,
        OrderStatus.broadcasting => AppTheme.pending,
        OrderStatus.accepted || OrderStatus.driverArrived => scheme.primary,
        OrderStatus.inTrip => AppTheme.gain,
        OrderStatus.completed => AppTheme.loss,
        OrderStatus.cancelled => scheme.error,
      },
    );
  }

  factory StatusChip.driver(DriverStatus status, BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return StatusChip(
      label: status.labelZh,
      color: switch (status) {
        DriverStatus.pendingKyc => AppTheme.pending,
        DriverStatus.depositRequired => scheme.primary,
        DriverStatus.active => AppTheme.loss,
        DriverStatus.suspended => scheme.error,
        DriverStatus.terminated => scheme.onSurfaceVariant,
      },
    );
  }

  factory StatusChip.refund(RefundStatus status, BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return StatusChip(
      label: status.labelZh,
      color: switch (status) {
        RefundStatus.pending => AppTheme.pending,
        RefundStatus.approved => AppTheme.loss,
        RefundStatus.rejected => scheme.error,
      },
    );
  }

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: color.withValues(alpha: 0.5)),
      ),
      child: Text(
        label,
        style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600),
      ),
    );
  }
}

/// A label/value row used across the detail and receipt screens.
/// Supply either [value] or [valueWidget], whichever fits.
class DetailRow extends StatelessWidget {
  const DetailRow({required this.label, this.value, this.valueWidget, super.key});

  final String label;
  final String? value;
  final Widget? valueWidget;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          SizedBox(
            width: 132,
            child: Text(
              label,
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ),
          Expanded(
            child:
                valueWidget ??
                Text(
                  value ?? '—',
                  style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w500),
                ),
          ),
        ],
      ),
    );
  }
}

/// Shorthand for a transient failure toast.
void showError(BuildContext context, Object error) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(error.userMessage)));
}

/// Shorthand for a success toast.
void showInfo(BuildContext context, String message) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(message)));
}
