import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../models/enums.dart';
import '../../models/order.dart';
import '../../router/app_router.dart';
import '../../state/order_history_controller.dart';
import '../shared/widgets.dart';

/// The passenger's own order history, one page at a time.
///
/// `GET /orders` resolves the scope from the caller, so `role: 'passenger'`
/// here means "orders I booked" — it is not a permission the client can assert.
///
/// Paging is a notifier (`OrderHistoryController`) rather than a `FutureProvider`
/// because the list accumulates: the next page is appended to what is already
/// read. The screen only drives it — a scroll near the end asks for more, and
/// the footer reflects whichever of "loading" / "failed, retry" / "end" applies.
class TripHistoryScreen extends ConsumerStatefulWidget {
  const TripHistoryScreen({super.key});

  @override
  ConsumerState<TripHistoryScreen> createState() => _TripHistoryScreenState();
}

class _TripHistoryScreenState extends ConsumerState<TripHistoryScreen> {
  static const String _role = 'passenger';

  final ScrollController _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
  }

  @override
  void dispose() {
    _scroll
      ..removeListener(_onScroll)
      ..dispose();
    super.dispose();
  }

  /// Ask for the next page a little before the bottom, so the append usually
  /// lands before the user reaches the footer. The controller ignores the call
  /// when a page is already in flight or the list is exhausted, so firing on
  /// every scroll event is harmless.
  void _onScroll() {
    if (!_scroll.hasClients) {
      return;
    }
    final double remaining = _scroll.position.maxScrollExtent - _scroll.position.pixels;
    if (remaining < 400) {
      unawaited(ref.read(orderHistoryControllerProvider(_role).notifier).loadMore());
    }
  }

  @override
  Widget build(BuildContext context) {
    final AsyncValue<OrderHistoryState> history = ref.watch(orderHistoryControllerProvider(_role));

    return Scaffold(
      appBar: AppBar(
        title: const Text('我的行程'),
        actions: <Widget>[
          IconButton(
            onPressed: () =>
                unawaited(ref.read(orderHistoryControllerProvider(_role).notifier).refresh()),
            tooltip: '重新整理',
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncValueView<OrderHistoryState>(
        value: history,
        onRetry: () =>
            unawaited(ref.read(orderHistoryControllerProvider(_role).notifier).refresh()),
        builder: (OrderHistoryState page) {
          if (page.items.isEmpty) {
            return const EmptyView(
              icon: Icons.receipt_long_outlined,
              title: '還沒有行程',
              subtitle: '完成第一次叫車後，紀錄會顯示在這裡。',
            );
          }
          return RefreshIndicator(
            onRefresh: () => ref.read(orderHistoryControllerProvider(_role).notifier).refresh(),
            child: ListView.separated(
              controller: _scroll,
              padding: const EdgeInsets.all(AppTheme.space4),
              // One extra row for the footer: the spinner, the retry, or the
              // end-of-list marker.
              itemCount: page.items.length + 1,
              separatorBuilder: (BuildContext context, int index) =>
                  const SizedBox(height: AppTheme.space3 - 2),
              itemBuilder: (BuildContext context, int index) {
                if (index == page.items.length) {
                  return _Footer(state: page);
                }
                return _OrderTile(order: page.items[index]);
              },
            ),
          );
        },
      ),
    );
  }
}

/// The list footer. Exactly one of the three states is ever true, so this is a
/// switch rather than a stack of conditionals.
class _Footer extends ConsumerWidget {
  const _Footer({required this.state});

  final OrderHistoryState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeData theme = Theme.of(context);

    if (state.loadingMore) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: AppTheme.space6),
        child: Center(child: CircularProgressIndicator()),
      );
    }

    if (state.error != null) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: AppTheme.space4),
        child: Center(
          child: Column(
            children: <Widget>[
              Text(
                state.error!.userMessage,
                textAlign: TextAlign.center,
                style: theme.textTheme.bodySmall,
              ),
              const SizedBox(height: AppTheme.space2),
              // Retrying the failed page keeps the pages already loaded — the
              // alternative (a full refresh) would throw away the user's scroll
              // position for a transient network blip.
              TextButton(
                onPressed: () => unawaited(
                  ref.read(orderHistoryControllerProvider('passenger').notifier).loadMore(),
                ),
                child: const Text('重試'),
              ),
            ],
          ),
        ),
      );
    }

    if (!state.exhausted) {
      // Nothing to say yet — more rows exist and nothing is in flight, which is
      // the normal state while scrolling.
      return const SizedBox(height: AppTheme.space2);
    }

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppTheme.space6 - 4),
      child: Center(
        child: Text(
          '沒有更多行程了',
          style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
        ),
      ),
    );
  }
}

class _OrderTile extends StatelessWidget {
  const _OrderTile({required this.order});

  final Order order;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final DateTime? at = order.createdAt;

    return Card(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: AppTheme.space4,
          vertical: AppTheme.space2,
        ),
        title: Row(
          children: <Widget>[
            StatusChip.order(order.status, context),
            const Spacer(),
            MoneyText(order.estimatedTotalHkd),
          ],
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: AppTheme.space2),
          child: Text(
            <String>[
              order.taxiType.labelZh,
              if (at != null)
                '${at.year}-${at.month.toString().padLeft(2, '0')}-'
                    '${at.day.toString().padLeft(2, '0')} '
                    '${at.hour.toString().padLeft(2, '0')}:'
                    '${at.minute.toString().padLeft(2, '0')}',
              if (order.fare.tunnels.isNotEmpty)
                order.fare.tunnels.map((Tunnel t) => t.labelZh).join('、'),
            ].join('  ·  '),
            style: theme.textTheme.bodySmall,
          ),
        ),
        trailing: const Icon(Icons.chevron_right),
        // Every order has a receipt, so every row opens the detail page. An
        // in-flight one additionally has a live map, which the detail page
        // offers as a further step — a terminal order stops there, because a
        // socket for a finished trip would never update.
        onTap: () => context.push('${Routes.tripDetail}/${order.id}'),
      ),
    );
  }
}
