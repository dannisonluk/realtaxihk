import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../models/enums.dart';
import '../../models/order.dart';
import '../../router/app_router.dart';
import '../../state/data_providers.dart';
import '../shared/widgets.dart';

/// The passenger's own order history.
///
/// `GET /orders` resolves the scope from the caller, so `role: 'passenger'`
/// here means "orders I booked" — it is not a permission the client can assert.
class TripHistoryScreen extends ConsumerWidget {
  const TripHistoryScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<OrderPage> history = ref.watch(orderHistoryProvider('passenger'));

    return Scaffold(
      appBar: AppBar(
        title: const Text('我的行程'),
        actions: <Widget>[
          IconButton(
            onPressed: () => ref.invalidate(orderHistoryProvider('passenger')),
            tooltip: '重新整理',
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncValueView<OrderPage>(
        value: history,
        onRetry: () => ref.invalidate(orderHistoryProvider('passenger')),
        builder: (OrderPage page) {
          if (page.items.isEmpty) {
            return const EmptyView(
              icon: Icons.receipt_long_outlined,
              title: '還沒有行程',
              subtitle: '完成第一次叫車後，紀錄會顯示在這裡。',
            );
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(orderHistoryProvider('passenger')),
            child: ListView.separated(
              padding: const EdgeInsets.all(16),
              itemCount: page.items.length,
              separatorBuilder: (BuildContext context, int index) => const SizedBox(height: 10),
              itemBuilder: (BuildContext context, int index) =>
                  _OrderTile(order: page.items[index]),
            ),
          );
        },
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
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        title: Row(
          children: <Widget>[
            StatusChip.order(order.status, context),
            const Spacer(),
            MoneyText(order.estimatedTotalHkd),
          ],
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 8),
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
        // A terminal order has no live channel to open, so only in-flight ones
        // are tappable through to the map.
        onTap: order.status.isTerminal
            ? null
            : () => context.push('${Routes.trackTrip}/${order.id}'),
      ),
    );
  }
}
