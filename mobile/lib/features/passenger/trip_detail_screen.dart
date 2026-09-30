import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/format/money.dart';
import '../../core/theme/app_theme.dart';
import '../../models/enums.dart';
import '../../models/fare.dart';
import '../../models/order.dart';
import '../../router/app_router.dart';
import '../../state/data_providers.dart';
import '../shared/widgets.dart';

/// The full receipt for one order, from either side of the trip.
///
/// `GET /orders/{id}` answers a participant or an admin, so this screen serves
/// passenger and driver alike and needs no role check — a 403 is the server's
/// answer, not a client-side decision.
///
/// Why it exists separately from [TripTrackingScreen]: the tracking screen is
/// the *live* view (a socket, a map, a moving driver) and is meaningless once an
/// order is terminal. This one is the record — the frozen fare breakdown, the
/// tariff version the quote was priced against, and the Cap. 374D disclaimer.
/// Historical orders are precisely the ones a user wants to look up later, and
/// before this screen existed the only route to an order was the map.
class TripDetailScreen extends ConsumerWidget {
  const TripDetailScreen({required this.orderId, super.key});

  final String orderId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<Order> order = ref.watch(orderDetailProvider(orderId));

    return Scaffold(
      appBar: AppBar(
        title: const Text('行程詳情'),
        actions: <Widget>[
          IconButton(
            onPressed: () => ref.invalidate(orderDetailProvider(orderId)),
            tooltip: '重新整理',
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncValueView<Order>(
        value: order,
        onRetry: () => ref.invalidate(orderDetailProvider(orderId)),
        builder: (Order o) => RefreshIndicator(
          onRefresh: () async => ref.invalidate(orderDetailProvider(orderId)),
          child: ListView(
            padding: const EdgeInsets.all(AppTheme.space4),
            children: <Widget>[
              _Headline(order: o),
              // Only an in-flight order has a live channel to open. A terminal
              // one would land on a map that never updates, which reads as a
              // broken screen rather than an old order.
              if (o.status.hasDriver && !o.status.isTerminal) ...<Widget>[
                const SizedBox(height: AppTheme.space3),
                FilledButton.icon(
                  onPressed: () => context.push('${Routes.trackTrip}/${o.id}'),
                  icon: const Icon(Icons.map_outlined),
                  label: const Text('查看即時位置'),
                ),
              ],
              const SizedBox(height: AppTheme.space6 - 4),
              _StatusTimeline(status: o.status),
              const SizedBox(height: AppTheme.space6 - 4),
              _FareBreakdown(order: o),
            ],
          ),
        ),
      ),
    );
  }
}

/// Status pill, total, and the two timestamps.
class _Headline extends StatelessWidget {
  const _Headline({required this.order});

  final Order order;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.space4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                StatusChip.order(order.status, context),
                const Spacer(),
                MoneyText(order.estimatedTotalHkd, style: theme.textTheme.headlineSmall),
              ],
            ),
            const SizedBox(height: AppTheme.space3),
            DetailRow(label: '的士類型', value: order.taxiType.labelZh),
            DetailRow(label: '建立時間', value: Format.dateTime(order.createdAt)),
            // Absent until the trip actually completes, which is a normal state
            // rather than a missing value.
            if (order.completedAt != null)
              DetailRow(label: '完成時間', value: Format.dateTime(order.completedAt)),
            DetailRow(label: '訂單編號', value: order.id),
          ],
        ),
      ),
    );
  }
}

/// The lifecycle as a vertical list, with the current step marked.
///
/// The order is notional rather than an event log: the backend stores only the
/// current `status`, so this shows where the order sits in the sequence, not
/// when each step happened. `CANCELLED` is a branch off the main line rather
/// than a step in it, so it replaces the remaining steps instead of advancing
/// past them.
class _StatusTimeline extends StatelessWidget {
  const _StatusTimeline({required this.status});

  final OrderStatus status;

  /// The happy path, in order. `BROADCASTING` is the server's initial state for
  /// a created order, so `CREATED` is not on the walk.
  static const List<OrderStatus> _walk = <OrderStatus>[
    OrderStatus.broadcasting,
    OrderStatus.accepted,
    OrderStatus.driverArrived,
    OrderStatus.inTrip,
    OrderStatus.completed,
  ];

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool cancelled = status == OrderStatus.cancelled;
    final int currentIndex = _walk.indexOf(status);

    // A cancelled order has no position on the walk, so nothing is "done" —
    // render the whole line as inactive and add the cancellation separately.
    final List<OrderStatus> steps = cancelled
        ? <OrderStatus>[OrderStatus.broadcasting, OrderStatus.cancelled]
        : _walk;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.space4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('狀態', style: theme.textTheme.titleMedium),
            const SizedBox(height: AppTheme.space3),
            for (int i = 0; i < steps.length; i++)
              _Step(
                label: steps[i].labelZh,
                // In the cancelled branch only the last step (cancellation) is
                // highlighted; otherwise everything up to `currentIndex`.
                done: cancelled ? i == steps.length - 1 : i < currentIndex,
                current: !cancelled && i == currentIndex,
                last: i == steps.length - 1,
                isError: cancelled && i == steps.length - 1,
              ),
          ],
        ),
      ),
    );
  }
}

class _Step extends StatelessWidget {
  const _Step({
    required this.label,
    required this.done,
    required this.current,
    required this.last,
    this.isError = false,
  });

  final String label;
  final bool done;
  final bool current;
  final bool last;
  final bool isError;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final Color accent = isError
        ? scheme.error
        : (done || current ? scheme.primary : scheme.onSurfaceVariant);

    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Column(
            children: <Widget>[
              Container(
                width: 12,
                height: 12,
                margin: const EdgeInsets.only(top: AppTheme.space1),
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: done || current ? accent : Colors.transparent,
                  border: Border.all(color: accent, width: 2),
                ),
              ),
              if (!last)
                Expanded(
                  child: Container(width: 2, color: accent.withValues(alpha: 0.35)),
                ),
            ],
          ),
          const SizedBox(width: AppTheme.space3),
          Expanded(
            child: Padding(
              padding: EdgeInsets.only(bottom: last ? 0 : AppTheme.space4 + 2),
              child: Text(
                label,
                style: TextStyle(
                  color: accent,
                  fontWeight: current || isError ? FontWeight.w600 : FontWeight.w400,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// The frozen fare breakdown — `orders.fare_json`.
///
/// Every figure here was computed once, at order creation, and stored
/// (`order_service.fare_snapshot`). It is deliberately not recomputed: a tariff
/// change or a new toll must not silently rewrite what a passenger was quoted
/// last month. [FareSnapshot.tariffVersion] is shown for the same reason — it is
/// the version the numbers belong to.
class _FareBreakdown extends StatelessWidget {
  const _FareBreakdown({required this.order});

  final Order order;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final FareSnapshot fare = order.fare;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.space4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Text('車費明細', style: theme.textTheme.titleMedium),
                const Spacer(),
                // Cap. 374D: the platform is an intermediary, so this is an
                // estimate, not a binding quote. The badge is driven by the
                // server's own `is_estimate` rather than hardcoded, so it
                // cannot outlive the field.
                if (fare.isEstimate)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: AppTheme.space2,
                      vertical: AppTheme.space1 - 2,
                    ),
                    decoration: BoxDecoration(
                      color: theme.colorScheme.secondaryContainer,
                      borderRadius: BorderRadius.circular(AppTheme.radiusField - 4),
                    ),
                    child: Text(
                      '估價',
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: theme.colorScheme.onSecondaryContainer,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: AppTheme.space2),

            DetailRow(label: '錶費', valueWidget: MoneyText(fare.meterFare)),
            if (fare.hasDiscount)
              DetailRow(
                label: '折扣 ${fare.discountPercent.toStringAsFixed(0)}%',
                valueWidget: MoneyText(fare.meterAfterDiscount),
              ),

            // One line per surcharge. `code` is the stable identifier; the
            // localised name is display-only.
            for (final FareSurcharge s in fare.surcharges)
              DetailRow(
                label: s.nameZh,
                valueWidget: MoneyText(s.amount),
              ),

            if (!fare.tip.isZero)
              DetailRow(label: '貼士', valueWidget: MoneyText(fare.tip)),

            const Divider(height: 24),
            DetailRow(
              label: '總額',
              valueWidget: MoneyText(
                fare.totalFare,
                style: theme.textTheme.titleLarge,
              ),
            ),

            const SizedBox(height: AppTheme.space4),
            _Disclaimer(fare: fare),
          ],
        ),
      ),
    );
  }
}

/// The bilingual Cap. 374D disclaimer, plus the tariff version.
///
/// Both languages are rendered rather than picking by locale: the legal notice
/// is part of the record, and a user reading it in a second language should not
/// have to change their phone's language to see the wording the platform is
/// held to.
class _Disclaimer extends StatelessWidget {
  const _Disclaimer({required this.fare});

  final FareSnapshot fare;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final TextStyle? style = theme.textTheme.bodySmall?.copyWith(
      color: theme.colorScheme.onSurfaceVariant,
    );

    return Container(
      padding: const EdgeInsets.all(AppTheme.space3),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(AppTheme.radiusField - 2),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(fare.disclaimerZh, style: style),
          const SizedBox(height: AppTheme.space2),
          Text(fare.disclaimerEn, style: style),
          const SizedBox(height: AppTheme.space2),
          Text(
            '收費標準版本：${fare.tariffVersion}',
            style: style?.copyWith(fontFeatures: const <FontFeature>[FontFeature.tabularFigures()]),
          ),
        ],
      ),
    );
  }
}
