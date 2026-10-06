import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme/app_theme.dart';
import '../../models/driver_notification.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// The driver's in-app notification inbox.
///
/// These are durable rows from `GET /drivers/me/notifications`, so the list
/// keeps working after a disconnect; external push (WhatsApp / FCM) is
/// deliberately out of scope. Rows show both high-value order kinds — premium
/// destinations and fixed fares — with a plain 8pt list rhythm and no floating
/// shadows.
class DriverNotificationsScreen extends ConsumerStatefulWidget {
  const DriverNotificationsScreen({super.key});

  @override
  ConsumerState<DriverNotificationsScreen> createState() =>
      _DriverNotificationsScreenState();
}

class _DriverNotificationsScreenState
    extends ConsumerState<DriverNotificationsScreen> {
  Future<void> _refresh() async {
    ref.invalidate(driverNotificationsProvider);
    await ref.read(driverNotificationsProvider.future);
  }

  Future<void> _markOne(DriverNotification notification) async {
    if (notification.read) {
      return;
    }
    try {
      await ref
          .read(driverNotificationRepositoryProvider)
          .read(notification.id);
      ref.invalidate(driverNotificationsProvider);
    } on Exception {
      // A failed mark-read must not block scrolling the inbox; the next
      // refresh retries it. The API client maps failures to ApiException.
    }
  }

  Future<void> _markAll() async {
    try {
      await ref.read(driverNotificationRepositoryProvider).readAll();
      ref.invalidate(driverNotificationsProvider);
    } on Exception {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('暫時無法更新已讀狀態')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final AsyncValue<DriverNotificationPage> inbox = ref.watch(
      driverNotificationsProvider,
    );

    return Scaffold(
      appBar: AppBar(
        title: const Text('通知'),
        actions: <Widget>[
          IconButton(
            onPressed: () => unawaited(_refresh()),
            tooltip: '重新整理',
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncValueView<DriverNotificationPage>(
        value: inbox,
        onRetry: () => ref.invalidate(driverNotificationsProvider),
        builder: (DriverNotificationPage page) {
          if (page.items.isEmpty) {
            return const EmptyView(
              icon: Icons.notifications_none,
              title: '暫時沒有通知',
              subtitle: '一口價或 premium 目的地訂單出現時會顯示喺呢度。',
            );
          }
          return ListView.separated(
            padding: const EdgeInsets.all(AppTheme.space4),
            itemCount: page.items.length,
            separatorBuilder: (BuildContext context, int index) =>
                const SizedBox(height: AppTheme.space2),
            itemBuilder: (BuildContext context, int index) => _NotificationTile(
              notification: page.items[index],
              onTap: () => unawaited(_markOne(page.items[index])),
            ),
          );
        },
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(
            AppTheme.space4,
            AppTheme.space2,
            AppTheme.space4,
            AppTheme.space3,
          ),
          child: SizedBox(
            width: double.infinity,
            child: OutlinedButton.icon(
              onPressed: () => unawaited(_markAll()),
              icon: const Icon(Icons.done_all),
              label: const Text('全部標為已讀'),
            ),
          ),
        ),
      ),
    );
  }
}

class _NotificationTile extends StatelessWidget {
  const _NotificationTile({required this.notification, required this.onTap});

  final DriverNotification notification;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool unread = !notification.read;
    final Color accent = notification.isPremium
        ? theme.colorScheme.primary
        : theme.colorScheme.secondary;

    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(AppTheme.radiusCard),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(
            AppTheme.space3,
            AppTheme.space3,
            AppTheme.space4,
            AppTheme.space3,
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Padding(
                padding: const EdgeInsets.only(top: AppTheme.space1),
                child: Icon(
                  notification.isPremium
                      ? Icons.flight_takeoff
                      : Icons.local_offer_outlined,
                  size: 22,
                  color: accent,
                ),
              ),
              const SizedBox(width: AppTheme.space3),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Expanded(
                          child: Text(
                            Localizations.localeOf(context).languageCode == 'en'
                                ? notification.headlineEn
                                : notification.headlineZh,
                            style: unread
                                ? theme.textTheme.titleMedium
                                : theme.textTheme.titleSmall?.copyWith(
                                    color: theme.colorScheme.onSurfaceVariant,
                                  ),
                          ),
                        ),
                        if (unread)
                          Container(
                            width: 8,
                            height: 8,
                            margin: const EdgeInsets.only(
                              top: AppTheme.space2,
                              left: AppTheme.space2,
                            ),
                            decoration: BoxDecoration(
                              color: theme.colorScheme.primary,
                              shape: BoxShape.circle,
                            ),
                          ),
                      ],
                    ),
                    const SizedBox(height: AppTheme.space1),
                    Text(
                      Localizations.localeOf(context).languageCode == 'en'
                          ? notification.bodyEn
                          : notification.bodyZh,
                      style: theme.textTheme.bodySmall,
                    ),
                    const SizedBox(height: AppTheme.space2),
                    if (notification.createdAt != null)
                      Text(
                        _formatTime(notification.createdAt!),
                        style: theme.textTheme.labelMedium,
                      ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  String _formatTime(DateTime time) {
    final DateTime local = time.toLocal();
    final String date = '${local.month}/${local.day}';
    final String minute = local.minute.toString().padLeft(2, '0');
    return '$date ${local.hour}:$minute';
  }
}
