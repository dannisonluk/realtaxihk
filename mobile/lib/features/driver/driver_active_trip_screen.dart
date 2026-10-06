import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/config/app_config.dart';
import '../../core/location/location_service.dart';
import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../data/order_repository.dart';
import '../../data/trip_repository.dart';
import '../../models/enums.dart';
import '../../models/order.dart';
import '../../models/trip.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/interrupt_sheet.dart';
import '../shared/map_panel.dart';
import '../shared/widgets.dart';

/// The driver's active trip: live position push plus the lifecycle buttons.
///
/// The socket is the push channel. The server allows `ws_tick_burst` (5) at once
/// and `ws_ticks_per_second` (2) sustained, and each accepted tick is a PostGIS
/// UPDATE plus a Redis publish — so the position stream is sampled on a timer at
/// [AppConfig.locationTickInterval] rather than forwarded as it arrives. A
/// `RATE_LIMITED` error would otherwise be the normal case.
///
/// **P4 split "arrived" into two steps.** `我已到達` is a *claim* the server
/// checks against this driver's own recorded GPS; it lands the order in
/// `PENDING_ARRIVAL_CONFIRM`, and the passenger's last-4 confirmation is what
/// makes it `DRIVER_ARRIVED` and locks cancelling. So this screen never assumes
/// the claim succeeded — it re-reads the order and renders whatever status came
/// back.
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
  Timer? _reconnectTimer;
  int _connectAttempts = 0;

  Position? _latest;
  MapPoint? _me;
  String? _socketNote;
  String? _actionNote;
  LocationAccess? _locationRefusal;
  bool _busy = false;
  bool _closing = false;
  bool _socketAlive = false;

  @override
  void initState() {
    super.initState();
    unawaited(_connect());
    unawaited(_startTracking());
  }

  @override
  void dispose() {
    _closing = true;
    _pushTimer?.cancel();
    _reconnectTimer?.cancel();
    unawaited(_events?.cancel());
    unawaited(_positions?.cancel());
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
      _connectAttempts = 0;
      _reconnectTimer?.cancel();
      setState(() {
        _channel = channel;
        _socketAlive = true;
        _socketNote = null;
      });
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
                _socketAlive = false;
              }
            case TripAckEvent():
              if (mounted && _socketNote != null) {
                setState(() => _socketNote = null);
              }
            case TripOrderEvent():
              // Something happened to the trip that this screen did not do:
              // the passenger confirmed arrival (which is what unlocks the
              // trip), cancelled, or an admin closed a dispute. The screen
              // renders from `orderDetailProvider`, so re-reading is the
              // response — never applying the payload as state.
              ref.invalidate(orderDetailProvider(widget.orderId));
            case TripLocationEvent():
            case TripPingEvent():
            case TripUnknownEvent():
              break;
          }
        },
        onError: (Object error) {
          _onSocketDown(error.userMessage);
        },
        onDone: () {
          _onSocketDown(_closeReason(channel), retryable: _mayRetry(channel));
        },
      );
    } on ApiException catch (e) {
      _onSocketDown(e.message);
    } on TripSocketClosed catch (e) {
      _onSocketDown(e.reason, retryable: e.retryable);
    }
  }

  bool _mayRetry(TripChannel channel) {
    final int? code = channel.closeCode;
    return code != AppConfig.wsUnauthenticated &&
        code != AppConfig.wsForbidden &&
        code != AppConfig.wsUnknownOrder;
  }

  void _scheduleReconnect() {
    if (_closing || !mounted || _reconnectTimer?.isActive == true) {
      return;
    }
    const List<int> delays = <int>[2, 5, 10, 30];
    final int index = _connectAttempts < delays.length ? _connectAttempts : delays.length - 1;
    _connectAttempts += 1;
    _reconnectTimer = Timer(Duration(seconds: delays[index]), () {
      if (!_closing && mounted) {
        unawaited(_connect());
      }
    });
  }

  void _onSocketDown(String reason, {bool retryable = true}) {
    if (_closing || !mounted) {
      return;
    }
    setState(() {
      _socketAlive = false;
      _socketNote = reason;
    });
    if (retryable) {
      _scheduleReconnect();
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

  Future<void> _startTracking() async {
    final LocationAccess access = await _location.ensureAccess();
    if (!mounted || _closing) {
      return;
    }
    if (access != LocationAccess.granted) {
      setState(() {
        _locationRefusal = access;
        _actionNote = locationRefusalMessage(access, alternative: '開始或完成行程都需要你的位置。');
      });
      return;
    }
    _locationRefusal = null;
    _pushTimer?.cancel();
    unawaited(_positions?.cancel());
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
    if (position == null || channel == null || _closing || !_socketAlive) {
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
    setState(() {
      _busy = true;
      _actionNote = null;
    });
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

  /// P4 §4.0.1. The driver's own fix goes along with the claim, but the server
  /// decides on the **recorded** GPS tick — so the 422s here are about the
  /// driver's real position, not about the request.
  Future<void> _arrivalClaim() async {
    final Position? position = _latest;
    setState(() {
      _busy = true;
      _actionNote = null;
    });
    try {
      await ref
          .read(orderRepositoryProvider)
          .arrivalClaim(widget.orderId, lat: position?.latitude, lng: position?.longitude);
      ref.invalidate(orderDetailProvider(widget.orderId));
      if (mounted) {
        showInfo(context, '已通知乘客確認上車');
      }
    } on ApiException catch (e) {
      if (!mounted) {
        return;
      }
      switch (e.reason) {
        case 'NO_LOCATION':
          setState(() => _actionNote = '無法取得你的位置，請確認已開啟定位權限並稍候再試。');
        case 'TOO_FAR':
          final Object? metres = e.details['distance_m'];
          setState(() => _actionNote = '你距離上車點約 $metres 米，請再接近上車點。');
        case 'GPS_MISMATCH':
          setState(() => _actionNote = '定位資料不一致，請稍候讓位置更新後再試。');
        default:
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
                    padding: const EdgeInsets.all(AppTheme.space3),
                    child: Row(
                      children: <Widget>[
                        StatusChip.order(order.status, context),
                        const SizedBox(width: AppTheme.space3),
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
            padding: const EdgeInsets.all(AppTheme.space4),
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
                const SizedBox(height: AppTheme.space2),
                DetailRow(label: '的士種類', value: order.taxiType.labelZh),
                if (order.destinationChangeCount > 0)
                  DetailRow(label: '乘客已改目的地', value: '${order.destinationChangeCount} 次'),
                if (_actionNote != null)
                  Padding(
                    padding: const EdgeInsets.only(top: AppTheme.space2),
                    child: Text(
                      _actionNote!,
                      style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
                    ),
                  ),
                if (_locationRefusal != null) ...[
                  const SizedBox(height: AppTheme.space2),
                  Row(
                    children: <Widget>[
                      if (locationNeedsSettings(_locationRefusal!))
                        TextButton(
                          onPressed: () {
                            final LocationAccess access = _locationRefusal!;
                            unawaited(_location.openSettings(access).then((_) => _startTracking()));
                          },
                          child: const Text('去設定'),
                        ),
                      TextButton(
                        onPressed: () => unawaited(_startTracking()),
                        child: const Text('重試'),
                      ),
                    ],
                  ),
                ],
                const SizedBox(height: AppTheme.space3),
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
    final ThemeData theme = Theme.of(context);

    switch (order.status) {
      case OrderStatus.accepted:
        final Position? position = _latest;
        return <Widget>[
          Text(
            position == null ? '⏳ 尚未取得你的位置，請確認已開啟定位權限。' : '✅ GPS 已定位，可以按「我已到達」。',
            style: theme.textTheme.bodySmall,
          ),
          const SizedBox(height: AppTheme.space2),
          FilledButton(onPressed: _busy ? null : _arrivalClaim, child: const Text('我已到達上車點')),
          const SizedBox(height: AppTheme.space2),
          OutlinedButton(
            onPressed: _busy ? null : () => _confirmCancel(order),
            child: const Text('取消（將被扣違約罰款）'),
          ),
        ];

      case OrderStatus.pendingArrivalConfirm:
        return <Widget>[
          Card(
            child: Padding(
              padding: const EdgeInsets.all(AppTheme.space3),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text('等候乘客確認上車', style: theme.textTheme.titleSmall),
                  const SizedBox(height: AppTheme.space1),
                  Text(
                    '請乘客說出他手機號碼的最後 4 位，並由乘客在 App 輸入確認。'
                    '確認前請勿開始行程。',
                    style: theme.textTheme.bodySmall,
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: AppTheme.space2),
          OutlinedButton(
            onPressed: _busy ? null : () => _confirmCancel(order),
            child: const Text('取消（將被扣違約罰款）'),
          ),
        ];

      case OrderStatus.driverArrived:
        return <Widget>[
          FilledButton(
            onPressed: _busy ? null : () => _transition('行程已開始', repo.start),
            child: const Text('開始行程'),
          ),
          const SizedBox(height: AppTheme.space2),
          // No cancel button: arrival is proven, so cancelling is refused
          // (409 CANCEL_LOCKED). Interrupt is the only way out (§5.2).
          OutlinedButton(
            onPressed: _busy ? null : () => _interrupt(order),
            child: const Text('中斷行程'),
          ),
        ];

      case OrderStatus.inTrip:
      case OrderStatus.destinationChanged:
        return <Widget>[
          FilledButton(
            onPressed: _busy ? null : () => _transition('行程已完成', repo.complete),
            child: const Text('完成行程'),
          ),
          const SizedBox(height: AppTheme.space2),
          OutlinedButton(
            onPressed: _busy ? null : () => _interrupt(order),
            child: const Text('中斷行程'),
          ),
        ];

      case OrderStatus.interrupted:
        return <Widget>[
          Card(
            child: Padding(
              padding: const EdgeInsets.all(AppTheme.space4),
              child: Text('行程已中斷。平台會稍後處理費用安排。', style: theme.textTheme.bodyMedium),
            ),
          ),
          const SizedBox(height: AppTheme.space2),
          FilledButton.tonal(
            onPressed: () => Navigator.of(context).maybePop(),
            child: const Text('返回接單'),
          ),
        ];

      case OrderStatus.completed:
      case OrderStatus.cancelled:
        return <Widget>[
          Card(
            child: Padding(
              padding: const EdgeInsets.all(AppTheme.space4),
              child: Text(
                order.status == OrderStatus.completed ? '行程已完成。' : '行程已取消。',
                style: theme.textTheme.bodyMedium,
              ),
            ),
          ),
          const SizedBox(height: AppTheme.space2),
          FilledButton.tonal(
            onPressed: () => Navigator.of(context).maybePop(),
            child: const Text('返回接單'),
          ),
        ];

      case OrderStatus.created:
      case OrderStatus.broadcasting:
        return <Widget>[const Text('此訂單尚未指派給你。')];
    }
  }

  /// P4 §4.2 — instant, and the only exit once arrival is proven.
  Future<void> _interrupt(Order order) async {
    final InterruptChoice? choice = await askInterruptReason(
      context,
      options: InterruptionReason.forDriver,
      counterparty: '乘客',
    );
    if (choice == null || !mounted) {
      return;
    }
    final bool confirmed = await confirmDestructive(
      context,
      title: '確認中斷行程？',
      message: '中斷後行程即時結束，不能復原。平台會為此行程開立爭議個案，並在事後判定費用安排。',
      confirmLabel: '確認中斷',
    );
    if (!confirmed || !mounted) {
      return;
    }
    await _transition(
      '行程已中斷',
      (String id) => ref
          .read(orderRepositoryProvider)
          .interrupt(id, reasonCode: choice.reason, note: choice.note),
    );
  }

  /// Cancelling from `ACCEPTED` / `PENDING_ARRIVAL_CONFIRM` is a default: 50% of
  /// the estimate, charged immediately, plus a 15-minute cool-down. The reason
  /// becomes mandatory there (P4 §5.2.1), so a picker replaces the free-text box.
  Future<void> _confirmCancel(Order order) async {
    InterruptionReason? reasonCode;
    if (order.status.cancelNeedsReason) {
      final InterruptChoice? choice = await askInterruptReason(
        context,
        options: InterruptionReason.forDriver,
        counterparty: '乘客',
      );
      if (choice == null || !mounted) {
        return;
      }
      reasonCode = choice.reason;
    }

    final bool confirmed = await confirmDestructive(
      context,
      title: '取消已接的訂單？',
      message: order.status.cancelNeedsReason
          ? '乘客已在等候。取消會構成違約：平台會即時在你的按金扣除相當於本次估價 50% 的違約罰款，'
                '並在 15 分鐘內限制你接新單。如對判定有疑問，可於事後申訴。'
          : '取消後無法復原。',
      confirmLabel: order.status.cancelNeedsReason ? '確認取消（將被罰款）' : '確認取消',
    );
    if (!confirmed || !mounted) {
      return;
    }
    await _transition(
      '訂單已取消',
      (String id) => ref
          .read(orderRepositoryProvider)
          .cancel(
            id,
            reason: reasonCode == null ? 'driver cancelled' : 'driver defaulted',
            reasonCode: reasonCode,
          ),
    );
  }
}
