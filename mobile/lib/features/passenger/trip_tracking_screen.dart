import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/config/app_config.dart';
import '../../core/format/money.dart';
import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../data/trip_repository.dart';
import '../../models/enums.dart';
import '../../models/order.dart';
import '../../models/trip.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/interrupt_sheet.dart';
import '../shared/map_panel.dart';
import '../shared/widgets.dart';
import 'change_destination_sheet.dart';

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
///
/// **P4 adds a third case the poll cannot cover well.** The arrival-confirmation
/// request must appear the moment the driver claims arrival (§7) — a 10-second
/// delay in the wrong place. Until lifecycle events are actually published on
/// the socket this screen still leans on the poll, so the confirm panel is
/// rendered from the *order status* rather than from a push: whenever the next
/// poll lands on `PENDING_ARRIVAL_CONFIRM` the panel is there. It is correct,
/// just not instant; the notification gap is tracked in `docs/WORK_SUMMARY.md`.
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
  bool _busy = false;
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
      case TripOrderEvent():
        // The driver claimed arrival, started the trip, or someone ended it.
        // Re-reading beats waiting for the next poll tick — the poll is still
        // there as the fallback for a dropped socket, not as the main path.
        ref.invalidate(orderDetailProvider(widget.orderId));
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
                    Text(
                      order.fare.isDestinationChange ? '車費（已改目的地，重新估算）' : '車費（已凍結）',
                      style: theme.textTheme.titleSmall,
                    ),
                    MoneyText(order.estimatedTotalHkd, style: theme.textTheme.headlineSmall),
                  ],
                ),
                const SizedBox(height: AppTheme.space1),
                DetailRow(label: '的士種類', value: order.taxiType.labelZh),
                DetailRow(label: '下單時間', value: _createdAt(order)),
                if (order.destinationChangeCount > 0)
                  DetailRow(label: '改目的地次數', value: '${order.destinationChangeCount} 次'),
                if (driver != null)
                  DetailRow(
                    label: '司機位置',
                    value: '${driver.lat.toStringAsFixed(5)}, ${driver.lng.toStringAsFixed(5)}',
                  ),
                const SizedBox(height: AppTheme.space3),
                ..._actions(context, order),
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

  /// The bottom action area, chosen by status.
  ///
  /// The P4 rules that shape this (§5.2, §6.1):
  ///
  /// * No cancel button once arrival is proven — the server would answer 409
  ///   `CANCEL_LOCKED`, and a button that can only fail is an invitation to
  ///   press it and then be punished.
  /// * Interrupt is never hidden behind a menu. A crash is not a moment for
  ///   navigation.
  List<Widget> _actions(BuildContext context, Order order) {
    if (order.status == OrderStatus.pendingArrivalConfirm) {
      return <Widget>[_ArrivalConfirmPanel(order: order, onConfirmed: _afterAction)];
    }
    if (order.status.canInterrupt) {
      return <Widget>[
        Row(
          children: <Widget>[
            Expanded(
              child: OutlinedButton.icon(
                onPressed: _busy ? null : () => _changeDestination(order),
                icon: const Icon(Icons.edit_location_alt),
                label: const Text('改目的地'),
              ),
            ),
            const SizedBox(width: AppTheme.space3),
            Expanded(
              child: OutlinedButton.icon(
                onPressed: _busy ? null : () => _interrupt(order),
                icon: const Icon(Icons.report),
                style: OutlinedButton.styleFrom(
                  foregroundColor: Theme.of(context).colorScheme.error,
                ),
                label: const Text('中斷行程'),
              ),
            ),
          ],
        ),
        if (order.status == OrderStatus.driverArrived)
          Padding(
            padding: const EdgeInsets.only(top: AppTheme.space3),
            child: Text(
              '⚠️ 已確認到達，無法取消。如無法乘車，請與司機溝通或中斷行程。',
              style: Theme.of(
                context,
              ).textTheme.bodySmall?.copyWith(color: Theme.of(context).colorScheme.error),
            ),
          ),
      ];
    }
    if (order.status == OrderStatus.interrupted) {
      return <Widget>[
        Card(
          child: Padding(
            padding: const EdgeInsets.all(AppTheme.space3),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text('行程已結束。', style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: AppTheme.space1),
                Text(
                  '如對費用有疑問，可提出申訴。平台已為此行程開立爭議個案，客服會盡快聯絡你。',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: AppTheme.space2),
        SizedBox(
          width: double.infinity,
          child: OutlinedButton(onPressed: () => _appeal(order), child: const Text('提出申訴')),
        ),
      ];
    }
    if (order.status.canCancel) {
      return <Widget>[
        OutlinedButton.icon(
          onPressed: _busy ? null : () => _confirmCancel(order),
          icon: const Icon(Icons.close),
          label: Text(order.status.cancelNeedsReason ? '取消行程（可能被扣違約罰款）' : '取消行程'),
        ),
      ];
    }
    return const <Widget>[];
  }

  static String _createdAt(Order order) => order.createdAt == null ? '—' : _fmt(order.createdAt!);

  static String _fmt(DateTime at) =>
      '${at.hour.toString().padLeft(2, '0')}:${at.minute.toString().padLeft(2, '0')}';

  String _statusHint(OrderStatus status, bool live) => switch (status) {
    OrderStatus.broadcasting => live ? '正在為你尋找司機…' : '正在為你尋找司機…（即時連線中斷）',
    OrderStatus.accepted => '司機已接單，正在前往上車點',
    OrderStatus.pendingArrivalConfirm => '司機聲稱已到達，請確認你已上車',
    OrderStatus.driverArrived => '司機已到達上車點（已確認）',
    OrderStatus.inTrip => '行程進行中',
    OrderStatus.destinationChanged => '行程進行中（目的地已更改）',
    OrderStatus.interrupted => '行程已中斷',
    OrderStatus.completed => '行程已完成',
    OrderStatus.cancelled => '行程已取消',
    OrderStatus.created => '訂單已建立',
  };

  void _afterAction() {
    ref.invalidate(orderDetailProvider(widget.orderId));
  }

  /// A failed call keeps the screen honest: the server's message is shown and
  /// the order is re-fetched, because a 409 usually means the status moved
  /// underneath us (the poll will catch up either way).
  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
    } on ApiException catch (e) {
      if (mounted) {
        showError(context, e);
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
      _afterAction();
    }
  }

  /// P4 §4.1. Pick a new dropoff, confirm, then let the server re-price.
  Future<void> _changeDestination(Order order) async {
    final String current = order.premiumDestination?.nameZh ?? order.destinationArea ?? '原本目的地';
    final MapPoint? destination = await pickNewDestination(context, currentAddress: current);
    if (destination == null || !mounted) {
      return;
    }
    final bool confirmed = await confirmDestructive(
      context,
      title: '確認更改目的地？',
      message: '更改後會以新路線重新估價，行程繼續。實際車資仍由你與司機協商（法例第 374D 章）。',
      confirmLabel: '確認更改',
    );
    if (!confirmed || !mounted) {
      return;
    }
    final Money previous = order.estimatedTotalHkd;
    await _run(() async {
      final Order updated = await ref
          .read(orderRepositoryProvider)
          .changeDestination(
            order.id,
            dropoffLat: destination.lat,
            dropoffLng: destination.lng,
            dropoffAddress: destination.label,
          );
      if (mounted) {
        await _showNewEstimate(updated, previous: previous);
      }
    });
  }

  /// P4 §4.1 step 7 — the new estimate must be shown immediately, and it must be
  /// labelled an estimate rather than a price.
  Future<void> _showNewEstimate(Order updated, {required Money previous}) async {
    final Money now = updated.estimatedTotalHkd;
    // `minus` works in integer cents — never subtract `asDouble`s, which is how
    // `HK$0.30000000000001137` reaches the screen.
    final Money delta = now.minus(previous);
    final Money magnitude = Money(
      delta.canonical.startsWith('-') ? delta.canonical.substring(1) : delta.canonical,
    );
    final String deltaText = delta.isZero
        ? '與原本相同'
        : '（${delta.isNegative ? '−' : '+'}${magnitude.hkd}）';
    final StringBuffer body = StringBuffer('新估價 ${now.hkd} $deltaText。實際車資仍由你與司機協商。');
    if (updated.fare.distanceIsStraightLine) {
      body.write('\n\n註：此估價以直線距離計算，實際道路距離可能更遠。');
    }
    await showDialog<void>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('已更改目的地'),
        content: Text(body.toString()),
        actions: <Widget>[
          FilledButton(onPressed: () => Navigator.of(context).pop(), child: const Text('知道了')),
        ],
      ),
    );
  }

  /// P4 §4.2. Instant, so the confirmation copy has to say so plainly.
  Future<void> _interrupt(Order order) async {
    final InterruptChoice? choice = await askInterruptReason(
      context,
      options: InterruptionReason.forPassenger,
      counterparty: '司機',
    );
    if (choice == null || !mounted) {
      return;
    }
    final bool confirmed = await confirmDestructive(
      context,
      title: '確認中斷行程？',
      message: '中斷後行程即時結束，不能復原。平台會稍後處理費用安排，並已為此行程開立爭議個案。',
      confirmLabel: '確認中斷',
    );
    if (!confirmed || !mounted) {
      return;
    }
    await _run(() async {
      await ref
          .read(orderRepositoryProvider)
          .interrupt(order.id, reasonCode: choice.reason, note: choice.note);
      if (mounted) {
        showInfo(context, '行程已中斷');
      }
    });
  }

  /// There is no passenger-facing dispute endpoint yet, so this explains what
  /// actually happens rather than pretending to file something. The interrupt
  /// path already opened a case in the same transaction; anything else has to go
  /// through support. (Tracked as a gap in `docs/WORK_SUMMARY.md`.)
  Future<void> _appeal(Order order) async {
    await showDialog<void>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('提出申訴'),
        content: Text(
          '行程編號：${order.id}\n\n'
          '中斷行程時，平台已自動開立爭議個案，客服會依序處理並與你聯絡。'
          '如屬其他情況，請以此編號聯絡客服。',
        ),
        actions: <Widget>[
          FilledButton(onPressed: () => Navigator.of(context).pop(), child: const Text('知道了')),
        ],
      ),
    );
  }

  Future<void> _confirmCancel(Order order) async {
    // From ACCEPTED onward a cancel is a default: money and a cool-down. The
    // reason becomes mandatory exactly there (P4 §5.2.1), so the picker is
    // shown instead of a free-text box.
    InterruptionReason? reasonCode;
    if (order.status.cancelNeedsReason) {
      final InterruptChoice? choice = await askInterruptReason(
        context,
        options: InterruptionReason.forPassenger,
        counterparty: '司機',
      );
      if (choice == null || !mounted) {
        return;
      }
      reasonCode = choice.reason;
    }

    final bool confirmed = await confirmDestructive(
      context,
      title: '取消行程？',
      message: order.status.cancelNeedsReason
          ? '司機已接單並可能已在前往途中，取消會構成違約：'
                '平台會即時收取相當於本次估價全額的違約罰款，並在 15 分鐘內限制你開新單。'
                '如對收費有疑問，可於事後提出申訴。'
          : '取消後無法復原，需要重新叫車。',
      confirmLabel: order.status.cancelNeedsReason ? '確認取消（將被罰款）' : '確認取消',
    );
    if (!confirmed || !mounted) {
      return;
    }
    await _run(() async {
      await ref
          .read(orderRepositoryProvider)
          .cancel(
            order.id,
            reason: reasonCode == null ? 'passenger cancelled' : 'passenger defaulted',
            reasonCode: reasonCode,
          );
      if (mounted) {
        showInfo(context, '行程已取消');
      }
    });
  }
}

/// P4 §5.1.3 — the passenger proves they are in the car by reading out the last
/// four digits of **their own** number.
///
/// Only the passenger knows it, so the driver cannot complete this step alone;
/// that is the entire second factor. Three wrong entries return the order to
/// `ACCEPTED` and open a dispute, so the panel must not keep offering a fourth
/// try — it reads the returned status and switches to the "we have notified the
/// platform" copy.
class _ArrivalConfirmPanel extends ConsumerStatefulWidget {
  const _ArrivalConfirmPanel({required this.order, required this.onConfirmed});

  final Order order;
  final VoidCallback onConfirmed;

  @override
  ConsumerState<_ArrivalConfirmPanel> createState() => _ArrivalConfirmPanelState();
}

class _ArrivalConfirmPanelState extends ConsumerState<_ArrivalConfirmPanel> {
  final TextEditingController _code = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _code.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final String value = _code.text.trim();
    if (!RegExp(r'^\d{4}$').hasMatch(value)) {
      setState(() => _error = '請輸入 4 位數字');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final Order updated = await ref
          .read(orderRepositoryProvider)
          .arrivalConfirm(widget.order.id, value);
      if (!mounted) {
        return;
      }
      if (updated.status == OrderStatus.driverArrived) {
        showInfo(context, '已確認司機在場');
      } else {
        // Out of attempts: the server returned it to ACCEPTED and opened a case.
        showInfo(context, '無法確認。我們已通知平台，客服會盡快聯絡你。');
      }
      widget.onConfirmed();
    } on ApiException catch (e) {
      if (!mounted) {
        return;
      }
      if (e.reason == 'PIN_MISMATCH') {
        final Object? remaining = e.details['attempts_remaining'];
        setState(() => _error = '號碼不符。剩餘嘗試次數：$remaining');
      } else {
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
    final ThemeData theme = Theme.of(context);

    return Card(
      color: theme.colorScheme.primaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.space3),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('司機聲稱已到達', style: theme.textTheme.titleSmall),
            const SizedBox(height: AppTheme.space1),
            Text(
              '請把您自己手機號碼的最後 4 位告訴司機，並在下面輸入以確認您已上車。',
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onPrimaryContainer,
              ),
            ),
            const SizedBox(height: AppTheme.space3),
            TextField(
              controller: _code,
              keyboardType: TextInputType.number,
              maxLength: 4,
              decoration: const InputDecoration(
                labelText: '手機號碼最後 4 位',
                border: OutlineInputBorder(),
                counterText: '',
              ),
              onChanged: (_) {
                if (_error != null) {
                  setState(() => _error = null);
                }
              },
            ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: AppTheme.space2),
                child: Text(
                  _error!,
                  style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
                ),
              ),
            const SizedBox(height: AppTheme.space2),
            SizedBox(
              width: double.infinity,
              child: FilledButton(onPressed: _busy ? null : _submit, child: const Text('確認我已上車')),
            ),
            const SizedBox(height: AppTheme.space1),
            Text(
              '司機沒有出現？請勿確認，並與司機聯絡或稍後提出申訴。',
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onPrimaryContainer,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
