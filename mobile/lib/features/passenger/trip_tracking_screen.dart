import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/config/app_config.dart';
import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../data/trip_repository.dart';
import '../../models/enums.dart';
import '../../models/order.dart';
import '../../models/trip.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/map_panel.dart';
import '../shared/widgets.dart';

/// The passenger's live map.
///
/// Two channels are needed, and the second one is not optional:
///
/// 1. `/ws/trip/{order_id}` for the driver's position ticks. The server pings on
///    its own timer (`SEC-30`) and the transport's WebSocket ping reaps genuinely
///    dead peers; there is no app-level idle watchdog that could race a healthy,
///    silent passenger socket (NEW-10).
/// 2. A slow poll of `GET /orders/{id}`. `app/services/trip_service.py` and
///    `app/api/ws.py` both document that "lifecycle events (grab/cancel) publish
///    on the same channel", but **no call site ever publishes one** — `publish()`
///    is only reached from `record_tick`. So a grab does not arrive over the
///    socket, and without this poll the passenger would sit on "waiting for a
///    driver" forever after being matched. See mobile/README.md.
class TripTrackingScreen extends ConsumerStatefulWidget {
  const TripTrackingScreen({required this.orderId, super.key});

  final String orderId;

  @override
  ConsumerState<TripTrackingScreen> createState() => _TripTrackingScreenState();
}

class _TripTrackingScreenState extends ConsumerState<TripTrackingScreen> {
  TripChannel? _channel;
  StreamSubscription<TripEvent>? _events;
  Timer? _poll;

  MapPoint? _driver;
  String? _socketNote;
  bool _socketAlive = false;
  bool _closing = false;

  @override
  void initState() {
    super.initState();
    unawaited(_connect());
    _poll = Timer.periodic(AppConfig.locationPollInterval, (Timer _) => _refresh());
  }

  @override
  void dispose() {
    _closing = true;
    _poll?.cancel();
    unawaited(_events?.cancel());
    unawaited(_channel?.close());
    super.dispose();
  }

  Future<void> _connect() async {
    if (_closing) {
      return;
    }
    try {
      final TripChannel channel = await ref
          .read(tripRepositoryProvider)
          .openTripChannel(widget.orderId);
      if (_closing) {
        await channel.close();
        return;
      }
      setState(() {
        _channel = channel;
        _socketAlive = true;
        _socketNote = null;
      });
      _events = channel.events.listen(
        _onEvent,
        onError: (Object error) => _onSocketDown(error.userMessage),
        onDone: () => _onSocketDown(_closeReason(channel)),
      );
    } on ApiException catch (e) {
      _onSocketDown(e.message);
    } on TripSocketClosed catch (e) {
      _onSocketDown(e.reason);
    }
  }

  String _closeReason(TripChannel channel) => switch (channel.closeCode) {
    AppConfig.wsUnauthenticated => '登入狀態已失效，請重新登入',
    AppConfig.wsForbidden => '你不是此行程的參與者',
    AppConfig.wsUnknownOrder => '找不到此行程',
    AppConfig.wsCapacity => '連線數目已達上限，稍後重試',
    final int? code when code == null => '連線中斷',
    final int? code => '連線已關閉（$code）',
  };

  void _onEvent(TripEvent event) {
    switch (event) {
      case TripLocationEvent(:final double lat, :final double lng):
        setState(() => _driver = MapPoint(lat: lat, lng: lng, label: '司機位置'));
      case TripErrorEvent():
        // A passenger socket is read-only, so the only error it can provoke is
        // its own; nothing to do beyond leaving the map up.
        break;
      case TripPingEvent():
      case TripAckEvent():
      case TripUnknownEvent():
        break;
    }
  }

  void _onSocketDown(String reason) {
    if (_closing || !mounted) {
      return;
    }
    setState(() {
      _socketAlive = false;
      _socketNote = reason;
    });
    // The REST snapshot below keeps the map useful while the socket is down.
    unawaited(_refresh());
  }

  /// One round of the fallback: a REST position, plus a fresh order status.
  ///
  /// The status refresh is what actually detects a grab — see the class docstring.
  Future<void> _refresh() async {
    if (_closing) {
      return;
    }
    ref.invalidate(orderDetailProvider(widget.orderId));

    if (_socketAlive) {
      return;
    }
    try {
      final TripLocationSnapshot snapshot = await ref
          .read(tripRepositoryProvider)
          .snapshot(widget.orderId);
      if (!mounted || _closing) {
        return;
      }
      setState(() {
        if (snapshot.hasFix) {
          _driver = MapPoint(lat: snapshot.lat!, lng: snapshot.lng!, label: '司機位置');
        }
      });
    } on ApiException catch (e) {
      if (mounted && e.code != ApiException.network) {
        setState(() => _socketNote = e.message);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final AsyncValue<Order> order = ref.watch(orderDetailProvider(widget.orderId));

    return Scaffold(
      appBar: AppBar(
        title: const Text('行程'),
        actions: <Widget>[
          IconButton(onPressed: _refresh, tooltip: '重新整理', icon: const Icon(Icons.refresh)),
        ],
      ),
      body: AsyncValueView<Order>(
        value: order,
        onRetry: () => ref.invalidate(orderDetailProvider(widget.orderId)),
        builder: (Order data) => _body(context, data),
      ),
    );
  }

  Widget _body(BuildContext context, Order order) {
    final ThemeData theme = Theme.of(context);
    final MapPoint? driver = _driver;

    return Column(
      children: <Widget>[
        Expanded(
          child: Stack(
            children: <Widget>[
              Positioned.fill(
                child: MapPanel(
                  centre: driver,
                  markers: <MapPoint>[?driver],
                  follow: driver,
                  zoom: 15,
                ),
              ),
              Positioned(
                left: 12,
                right: 12,
                top: 12,
                child: Card(
                  child: Padding(
                    padding: const EdgeInsets.all(AppTheme.space3),
                    child: Row(
                      children: <Widget>[
                        StatusChip.order(order.status, context),
                        const SizedBox(width: AppTheme.space3),
                        Expanded(
                          child: Text(
                            _statusHint(order.status, _socketAlive),
                            style: theme.textTheme.bodySmall,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
              if (!_socketAlive)
                Positioned(
                  left: 12,
                  right: 12,
                  bottom: 12,
                  child: Card(
                    color: theme.colorScheme.errorContainer,
                    child: Padding(
                      padding: const EdgeInsets.all(AppTheme.space3),
                      child: Row(
                        children: <Widget>[
                          Icon(
                            Icons.cloud_off,
                            size: 18,
                            color: theme.colorScheme.onErrorContainer,
                          ),
                          const SizedBox(width: AppTheme.space3 - 2),
                          Expanded(
                            child: Text(
                              '即時位置連線中斷（${_socketNote ?? '未知原因'}），已改用輪詢更新。',
                              style: theme.textTheme.bodySmall?.copyWith(
                                color: theme.colorScheme.onErrorContainer,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
        SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.all(AppTheme.space4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: <Widget>[
                    Text('車費（已凍結）', style: theme.textTheme.titleSmall),
                    MoneyText(order.estimatedTotalHkd, style: theme.textTheme.headlineSmall),
                  ],
                ),
                const SizedBox(height: AppTheme.space1),
                DetailRow(label: '的士種類', value: order.taxiType.labelZh),
                DetailRow(label: '下單時間', value: _createdAt(order)),
                if (driver != null)
                  DetailRow(
                    label: '司機位置',
                    value: '${driver.lat.toStringAsFixed(5)}, ${driver.lng.toStringAsFixed(5)}',
                  ),
                const SizedBox(height: AppTheme.space3),
                if (!order.status.isTerminal)
                  OutlinedButton.icon(
                    onPressed: () => _confirmCancel(order),
                    icon: const Icon(Icons.close),
                    label: const Text('取消行程'),
                  ),
                const SizedBox(height: AppTheme.space2 - 2),
                Text(
                  order.fare.disclaimerZh,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  static String _createdAt(Order order) => order.createdAt == null ? '—' : _fmt(order.createdAt!);

  static String _fmt(DateTime at) =>
      '${at.hour.toString().padLeft(2, '0')}:${at.minute.toString().padLeft(2, '0')}';

  String _statusHint(OrderStatus status, bool live) => switch (status) {
    OrderStatus.broadcasting => live ? '正在為你尋找司機…' : '正在為你尋找司機…（即時連線中斷）',
    OrderStatus.accepted => '司機已接單，正在前往上車點',
    OrderStatus.driverArrived => '司機已到達上車點',
    OrderStatus.inTrip => '行程進行中',
    OrderStatus.completed => '行程已完成',
    OrderStatus.cancelled => '行程已取消',
    OrderStatus.created => '訂單已建立',
  };

  Future<void> _confirmCancel(Order order) async {
    final bool confirmed = await confirmDestructive(
      context,
      title: '取消行程？',
      message: '取消後無法復原，需要重新叫車。',
      confirmLabel: '確認取消',
    );
    if (!confirmed || !mounted) {
      return;
    }
    try {
      await ref.read(orderRepositoryProvider).cancel(order.id, reason: 'passenger cancelled');
      ref.invalidate(orderDetailProvider(widget.orderId));
      if (mounted) {
        showInfo(context, '行程已取消');
      }
    } on ApiException catch (e) {
      if (mounted) {
        showError(context, e);
      }
    }
  }
}
