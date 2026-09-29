import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/location/location_service.dart';
import '../../core/network/api_exception.dart';
import '../../models/enums.dart';
import '../../models/order.dart';
import '../../router/app_router.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/map_panel.dart';
import '../shared/widgets.dart';

/// The driver's job list.
///
/// `GET /orders/nearby` reads a Redis geo index, so the driver must be reporting
/// a position for anything to be within range. Going online is therefore not
/// cosmetic: it is what makes the list non-empty, and `POST /driver/location`
/// refuses a driver whose profile is not `ACTIVE`.
class DriverJobsScreen extends ConsumerStatefulWidget {
  const DriverJobsScreen({super.key});

  @override
  ConsumerState<DriverJobsScreen> createState() => _DriverJobsScreenState();
}

class _DriverJobsScreenState extends ConsumerState<DriverJobsScreen> {
  final LocationService _location = const LocationService();

  MapPoint? _me;
  bool _online = false;
  bool _busy = false;
  String? _locationNote;

  @override
  void initState() {
    super.initState();
    unawaited(_locate());
  }

  Future<void> _locate() async {
    final position = await _location.current();
    if (!mounted) {
      return;
    }
    setState(() {
      if (position == null) {
        _locationNote = '未能取得位置，附近訂單需要位置才可顯示。';
        return;
      }
      _me = MapPoint(lat: position.latitude, lng: position.longitude, label: '我的位置');
      _locationNote = null;
    });
    if (_online) {
      await _push();
    }
  }

  Future<void> _push() async {
    final MapPoint? me = _me;
    if (me == null) {
      return;
    }
    try {
      await ref
          .read(driverRepositoryProvider)
          .pushLocation(lat: me.lat, lng: me.lng, online: _online);
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _online = false);
        showError(context, e);
      }
    }
  }

  Future<void> _toggleOnline(bool value) async {
    setState(() => _online = value);
    if (_me == null) {
      await _locate();
    }
    await _push();
  }

  Future<void> _grab(Order order) async {
    setState(() => _busy = true);
    try {
      final Order grabbed = await ref.read(orderRepositoryProvider).grab(order.id);
      ref.invalidate(nearbyOrdersProvider);
      if (mounted) {
        await context.push('${Routes.driverActiveTrip}/${grabbed.id}');
      }
    } on ApiException catch (e) {
      // A 409 means another driver won the race — the lock plus the conditional
      // UPDATE guarantee exactly one winner, so this is not retryable.
      if (mounted) {
        showError(context, e);
        ref.invalidate(nearbyOrdersProvider);
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final MapPoint? me = _me;

    return Scaffold(
      appBar: AppBar(
        title: const Text('接單'),
        actions: <Widget>[
          IconButton(
            onPressed: _busy ? null : _locate,
            tooltip: '更新位置',
            icon: const Icon(Icons.my_location),
          ),
        ],
      ),
      body: Column(
        children: <Widget>[
          Card(
            margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: SwitchListTile(
              value: _online,
              onChanged: _busy ? null : (bool value) => unawaited(_toggleOnline(value)),
              title: Text(_online ? '已上線' : '已離線'),
              subtitle: Text(
                _online ? '正在接收附近訂單' : '上線後才會顯示附近訂單',
                style: theme.textTheme.bodySmall,
              ),
              secondary: Icon(
                _online ? Icons.wifi_tethering : Icons.wifi_tethering_off,
                color: _online ? theme.colorScheme.primary : theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ),
          if (_locationNote != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
              child: Row(
                children: <Widget>[
                  Icon(Icons.location_off, size: 16, color: theme.colorScheme.error),
                  const SizedBox(width: 8),
                  Expanded(child: Text(_locationNote!, style: theme.textTheme.bodySmall)),
                ],
              ),
            ),
          Expanded(
            child: !_online
                ? const EmptyView(
                    icon: Icons.wifi_tethering_off,
                    title: '尚未上線',
                    subtitle: '開啟上線後，系統會顯示附近的待接訂單。',
                  )
                : me == null
                ? const Center(child: CircularProgressIndicator())
                : _nearbyList(me),
          ),
        ],
      ),
    );
  }

  Widget _nearbyList(MapPoint me) {
    final AsyncValue<NearbyOrders> nearby = ref.watch(
      nearbyOrdersProvider((lat: me.lat, lng: me.lng, radiusKm: 3)),
    );

    return AsyncValueView<NearbyOrders>(
      value: nearby,
      onRetry: () => ref.invalidate(nearbyOrdersProvider),
      builder: (NearbyOrders data) {
        // P2-10: the server fails open with an empty page when Redis is down.
        // "Searching" is the honest message — there may well be orders.
        if (data.degraded) {
          return const EmptyView(
            icon: Icons.cloud_off,
            title: '暫時無法搜尋附近訂單',
            subtitle: '定位服務暫時不可用，請稍後重新整理。',
          );
        }
        if (data.items.isEmpty) {
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(nearbyOrdersProvider),
            child: ListView(
              children: const <Widget>[
                SizedBox(height: 120),
                EmptyView(icon: Icons.search_off, title: '附近沒有待接訂單', subtitle: '下拉重新整理。'),
              ],
            ),
          );
        }
        return RefreshIndicator(
          onRefresh: () async => ref.invalidate(nearbyOrdersProvider),
          child: ListView.separated(
            padding: const EdgeInsets.all(16),
            itemCount: data.items.length,
            separatorBuilder: (BuildContext context, int index) => const SizedBox(height: 10),
            itemBuilder: (BuildContext context, int index) {
              final Order order = data.items[index];
              return Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Row(
                        children: <Widget>[
                          Text(
                            order.taxiType.labelZh,
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          const Spacer(),
                          MoneyText(order.estimatedTotalHkd),
                        ],
                      ),
                      const SizedBox(height: 8),
                      if (order.fare.tunnels.isNotEmpty)
                        Text(
                          '經 ${order.fare.tunnels.map((Tunnel t) => t.labelZh).join('、')}',
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      const SizedBox(height: 12),
                      FilledButton(
                        onPressed: _busy ? null : () => _grab(order),
                        child: const Text('接單'),
                      ),
                    ],
                  ),
                ),
              );
            },
          ),
        );
      },
    );
  }
}
