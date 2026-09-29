import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';

import '../../core/config/app_config.dart';
import '../../core/location/location_service.dart';
import '../../core/network/api_exception.dart';
import '../../data/order_repository.dart';
import '../../data/trip_repository.dart';
import '../../models/enums.dart';
import '../../models/order.dart';
import '../../models/trip.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/map_panel.dart';
import '../shared/widgets.dart';

/// The driver's active trip: live position push plus the lifecycle buttons.
///
/// The socket is the push channel. The server allows `ws_tick_burst` (5) at once
/// and `ws_ticks_per_second` (2) sustained, and each accepted tick is a PostGIS
/// UPDATE plus a Redis publish — so the position stream is sampled on a timer at
/// [AppConfig.locationTickInterval] rather than forwarded as it arrives. A
/// `RATE_LIMITED` error would otherwise be the normal case.
class DriverActiveTripScreen extends ConsumerStatefulWidget {
  const DriverActiveTripScreen({required this.orderId, super.key});

  final String orderId;

  @override
  ConsumerState<DriverActiveTripScreen> createState() => _DriverActiveTripScreenState();
}

class _DriverActiveTripScreenState extends ConsumerState<DriverActiveTripScreen> {
  final LocationService _location = const LocationService();

  TripChannel? _channel;
  StreamSubscription<TripEvent>? _events;
  StreamSubscription<Position>? _positions;
  Timer? _pushTimer;

  Position? _latest;
  MapPoint? _me;
  String? _socketNote;
  bool _busy = false;
  bool _closing = false;

  @override
  void initState() {
    super.initState();
    unawaited(_connect());
    _startTracking();
  }

  @override
  void dispose() {
    _closing = true;
    _pushTimer?.cancel();
    unawaited(_events?.cancel());
    unawaited(_positions?.cancel());
    unawaited(_channel?.close());
    super.dispose();
  }

  Future<void> _connect() async {
    try {
      final TripChannel channel = await ref
          .read(tripRepositoryProvider)
          .openTripChannel(widget.orderId);
      if (_closing) {
        await channel.close();
        return;
      }
      setState(() => _channel = channel);
      _events = channel.events.listen(
        (TripEvent event) {
          switch (event) {
            case TripErrorEvent():
              if (mounted) {
                setState(() => _socketNote = event.messageZh);
              }
              // A suspended driver must stop streaming; the server refuses every
              // subsequent tick, so retrying is pointless.
              if (event.isFatal) {
                _pushTimer?.cancel();
              }
            case TripAckEvent():
              if (mounted && _socketNote != null) {
                setState(() => _socketNote = null);
              }
            case TripLocationEvent():
            case TripPingEvent():
            case TripUnknownEvent():
              break;
          }
        },
        onError: (Object error) {
          if (mounted) {
            setState(() => _socketNote = error.userMessage);
          }
        },
        onDone: () {
          if (mounted && !_closing) {
            setState(() => _socketNote = '即時連線已中斷');
          }
        },
      );
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _socketNote = e.message);
      }
    } on TripSocketClosed catch (e) {
      if (mounted) {
        setState(() => _socketNote = e.reason);
      }
    }
  }

  void _startTracking() {
    _positions = _location
        .stream(distanceFilter: 15)
        .listen(
          (Position position) {
            if (!mounted) {
              return;
            }
            setState(() {
              _latest = position;
              if (LocationService.isInHongKong(position.latitude, position.longitude)) {
                _me = MapPoint(lat: position.latitude, lng: position.longitude, label: '我的位置');
              }
            });
          },
          onError: (Object _) {
            // A single dropped fix is not worth surfacing; the next one will land.
          },
        );

    _pushTimer = Timer.periodic(AppConfig.locationTickInterval, (Timer _) => _pushTick());
  }

  void _pushTick() {
    final Position? position = _latest;
    final TripChannel? channel = _channel;
    if (position == null || channel == null || _closing) {
      return;
    }
    // Guard the server's own bounds: a tick outside Hong Kong earns
    // {"type":"error","code":"BAD_LOCATION"} and burns a token for nothing.
    if (!LocationService.isInHongKong(position.latitude, position.longitude)) {
      return;
    }
    channel.pushLocation(lat: position.latitude, lng: position.longitude);
  }

  Future<void> _transition(String label, Future<Order> Function(String orderId) action) async {
    setState(() => _busy = true);
    try {
      await action(widget.orderId);
      ref.invalidate(orderDetailProvider(widget.orderId));
      if (mounted) {
        showInfo(context, label);
      }
    } on ApiException catch (e) {
      if (mounted) {
        showError(context, e);
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final AsyncValue<Order> order = ref.watch(orderDetailProvider(widget.orderId));

    return Scaffold(
      appBar: AppBar(title: const Text('進行中的行程')),
      body: AsyncValueView<Order>(
        value: order,
        onRetry: () => ref.invalidate(orderDetailProvider(widget.orderId)),
        builder: _body,
      ),
    );
  }

  Widget _body(Order order) {
    final ThemeData theme = Theme.of(context);
    final MapPoint? me = _me;

    return Column(
      children: <Widget>[
        Expanded(
          child: Stack(
            children: <Widget>[
              Positioned.fill(
                child: MapPanel(centre: me, markers: <MapPoint>[?me], follow: me),
              ),
              Positioned(
                left: 12,
                right: 12,
                top: 12,
                child: Card(
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Row(
                      children: <Widget>[
                        StatusChip.order(order.status, context),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Text(
                            _socketNote ?? (_channel == null ? '連線中…' : '位置推送中'),
                            style: theme.textTheme.bodySmall?.copyWith(
                              color: _socketNote == null
                                  ? theme.colorScheme.onSurfaceVariant
                                  : theme.colorScheme.error,
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
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: <Widget>[
                    Text('車費', style: theme.textTheme.titleSmall),
                    MoneyText(order.estimatedTotalHkd, style: theme.textTheme.headlineSmall),
                  ],
                ),
                const SizedBox(height: 8),
                DetailRow(label: '的士種類', value: order.taxiType.labelZh),
                const SizedBox(height: 12),
                ..._actions(order),
              ],
            ),
          ),
        ),
      ],
    );
  }

  List<Widget> _actions(Order order) {
    final OrderRepository repo = ref.read(orderRepositoryProvider);

    return switch (order.status) {
      OrderStatus.accepted => <Widget>[
        FilledButton(
          onPressed: _busy ? null : () => _transition('已標記到達', repo.arrive),
          child: const Text('已到達上車點'),
        ),
        const SizedBox(height: 8),
        OutlinedButton(
          onPressed: _busy ? null : () => _confirmCancel(),
          child: const Text('取消（可能被扣罰款）'),
        ),
      ],
      OrderStatus.driverArrived => <Widget>[
        FilledButton(
          onPressed: _busy ? null : () => _transition('行程已開始', repo.start),
          child: const Text('開始行程'),
        ),
        const SizedBox(height: 8),
        OutlinedButton(
          onPressed: _busy ? null : () => _confirmCancel(),
          child: const Text('取消（可能被扣罰款）'),
        ),
      ],
      OrderStatus.inTrip => <Widget>[
        FilledButton(
          onPressed: _busy ? null : () => _transition('行程已完成', repo.complete),
          child: const Text('完成行程'),
        ),
      ],
      OrderStatus.completed || OrderStatus.cancelled => <Widget>[
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Text(
              order.status == OrderStatus.completed ? '行程已完成。' : '行程已取消。',
              style: Theme.of(context).textTheme.bodyMedium,
            ),
          ),
        ),
        const SizedBox(height: 8),
        FilledButton.tonal(
          onPressed: () => Navigator.of(context).maybePop(),
          child: const Text('返回接單'),
        ),
      ],
      OrderStatus.created || OrderStatus.broadcasting => <Widget>[const Text('此訂單尚未指派給你。')],
    };
  }

  Future<void> _confirmCancel() async {
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('取消已接的訂單？'),
        content: const Text('在已接單或已到達的狀態下由司機取消，平台會在你的按金中扣除一筆違規罰款。'),
        actions: <Widget>[
          TextButton(onPressed: () => Navigator.of(context).pop(false), child: const Text('返回')),
          FilledButton(onPressed: () => Navigator.of(context).pop(true), child: const Text('確認取消')),
        ],
      ),
    );
    if (!(confirmed ?? false) || !mounted) {
      return;
    }
    await _transition(
      '訂單已取消',
      (String id) => ref.read(orderRepositoryProvider).cancel(id, reason: 'driver cancelled'),
    );
  }
}
