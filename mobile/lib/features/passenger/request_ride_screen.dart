import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/location/location_service.dart';
import '../../core/network/api_exception.dart';
import '../../models/enums.dart';
import '../../models/fare.dart';
import '../../models/order.dart';
import '../../router/app_router.dart';
import '../../state/providers.dart';
import '../shared/map_panel.dart';
import '../shared/widgets.dart';

/// Which endpoint the next map tap sets.
enum _Target { pickup, dropoff }

/// Request a taxi: pick two points, get a Cap. 374D quote, place the order.
///
/// The form deliberately offers **only the fields `OrderCreateIn` accepts**.
/// `FareEstimateRequest` additionally takes `baggage_count`, `animals` and
/// `advance_booking`, but `OrderService.create()` calls `calculate_fare()`
/// without them, so an estimate that included a baggage surcharge would not be
/// reproducible in the order snapshot — the passenger would be quoted a total the
/// order never carries. Those three belong on a standalone calculator, not here.
class RequestRideScreen extends ConsumerStatefulWidget {
  const RequestRideScreen({super.key});

  @override
  ConsumerState<RequestRideScreen> createState() => _RequestRideScreenState();
}

class _RequestRideScreenState extends ConsumerState<RequestRideScreen> {
  final LocationService _location = const LocationService();

  MapPoint? _pickup;
  MapPoint? _dropoff;
  _Target _target = _Target.dropoff;

  TaxiType _taxiType = TaxiType.urban;
  final Set<Tunnel> _tunnels = <Tunnel>{};
  bool _crossesHarbour = false;
  bool _atCrossHarbourStand = false;
  final TextEditingController _distance = TextEditingController();
  final TextEditingController _tip = TextEditingController(text: '0');

  FareEstimate? _estimate;
  bool _busy = false;
  bool _locating = false;

  @override
  void initState() {
    super.initState();
    _locate();
  }

  @override
  void dispose() {
    _distance.dispose();
    _tip.dispose();
    super.dispose();
  }

  Future<void> _locate() async {
    setState(() => _locating = true);
    final position = await _location.current();
    if (!mounted) {
      return;
    }
    setState(() {
      _locating = false;
      if (position != null) {
        _pickup = MapPoint(lat: position.latitude, lng: position.longitude, label: '上車點');
        _recomputeDistance();
      }
    });
    if (position == null && mounted) {
      showInfo(context, '未能取得位置，可在地圖上點選上車點');
    }
  }

  /// The server does not derive `distance_km` from the coordinates — the client
  /// supplies it — so it is recomputed on every change and left editable, since
  /// a straight line under-reports a real road distance.
  void _recomputeDistance() {
    final MapPoint? a = _pickup;
    final MapPoint? b = _dropoff;
    if (a == null || b == null) {
      return;
    }
    _distance.text = GeoMath.distanceKm(a, b).toStringAsFixed(1);
    _estimate = null;
  }

  void _onMapTap(MapPoint point) {
    setState(() {
      if (_target == _Target.pickup) {
        _pickup = MapPoint(lat: point.lat, lng: point.lng, label: '上車點');
      } else {
        _dropoff = MapPoint(lat: point.lat, lng: point.lng, label: '落車點');
      }
      _recomputeDistance();
    });
  }

  double get _distanceKm => double.tryParse(_distance.text) ?? 0;

  double get _tipValue => double.tryParse(_tip.text) ?? 0;

  bool get _ready => _pickup != null && _dropoff != null && _distanceKm > 0 && _distanceKm <= 100;

  FareEstimateRequest _fareRequest() => FareEstimateRequest(
    taxiType: _taxiType,
    distanceKm: _distanceKm,
    tunnels: _tunnels.toList(growable: false),
    crossesHarbour: _crossesHarbour,
    pickupAtCrossHarbourStand: _atCrossHarbourStand,
    tip: _tipValue,
  );

  Future<void> _quote() async {
    if (!_ready) {
      showInfo(context, '請先設定上車點、落車點及距離');
      return;
    }
    setState(() => _busy = true);
    try {
      final FareEstimate estimate = await ref.read(fareRepositoryProvider).estimate(_fareRequest());
      if (mounted) {
        setState(() => _estimate = estimate);
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

  Future<void> _placeOrder() async {
    final MapPoint pickup = _pickup!;
    final MapPoint dropoff = _dropoff!;
    setState(() => _busy = true);
    try {
      final Order order = await ref
          .read(orderRepositoryProvider)
          .create(
            OrderCreateRequest(
              pickupLat: pickup.lat,
              pickupLng: pickup.lng,
              dropoffLat: dropoff.lat,
              dropoffLng: dropoff.lng,
              pickupAddress: pickup.label,
              dropoffAddress: dropoff.label,
              distanceKm: _distanceKm,
              taxiType: _taxiType,
              tip: _tipValue,
              tunnels: _tunnels.toList(growable: false),
              crossesHarbour: _crossesHarbour,
              pickupAtCrossHarbourStand: _atCrossHarbourStand,
            ),
          );
      if (!mounted) {
        return;
      }
      // The created order carries the authoritative snapshot, so drop any
      // client-side quote before leaving.
      setState(() => _estimate = null);
      await context.push('${Routes.trackTrip}/${order.id}');
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
    final ThemeData theme = Theme.of(context);
    final MapPoint? pickup = _pickup;
    final MapPoint? dropoff = _dropoff;

    return Scaffold(
      appBar: AppBar(
        title: const Text('叫車'),
        actions: <Widget>[
          IconButton(
            onPressed: _locating ? null : _locate,
            tooltip: '使用目前位置',
            icon: _locating
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.my_location),
          ),
        ],
      ),
      body: Column(
        children: <Widget>[
          SizedBox(
            height: MediaQuery.sizeOf(context).height * 0.32,
            child: MapPanel(
              centre: dropoff ?? pickup,
              markers: <MapPoint>[?pickup, ?dropoff],
              route: (pickup != null && dropoff != null) ? <MapPoint>[pickup, dropoff] : null,
              onTap: _onMapTap,
              zoom: 13,
            ),
          ),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: <Widget>[
                SegmentedButton<_Target>(
                  segments: const <ButtonSegment<_Target>>[
                    ButtonSegment<_Target>(
                      value: _Target.pickup,
                      label: Text('上車點'),
                      icon: Icon(Icons.trip_origin),
                    ),
                    ButtonSegment<_Target>(
                      value: _Target.dropoff,
                      label: Text('落車點'),
                      icon: Icon(Icons.place_outlined),
                    ),
                  ],
                  selected: <_Target>{_target},
                  onSelectionChanged: (Set<_Target> value) => setState(() => _target = value.first),
                ),
                const SizedBox(height: 6),
                Text(
                  '點選地圖設定${_target == _Target.pickup ? '上車' : '落車'}位置',
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
                const SizedBox(height: 16),

                Text('的士種類', style: theme.textTheme.titleSmall),
                const SizedBox(height: 8),
                SegmentedButton<TaxiType>(
                  segments: <ButtonSegment<TaxiType>>[
                    for (final TaxiType type in TaxiType.values)
                      ButtonSegment<TaxiType>(value: type, label: Text(type.labelZh)),
                  ],
                  selected: <TaxiType>{_taxiType},
                  onSelectionChanged: (Set<TaxiType> value) => setState(() {
                    _taxiType = value.first;
                    _estimate = null;
                  }),
                ),
                const SizedBox(height: 16),

                Row(
                  children: <Widget>[
                    Expanded(
                      child: TextField(
                        controller: _distance,
                        keyboardType: const TextInputType.numberWithOptions(decimal: true),
                        onChanged: (String _) => setState(() => _estimate = null),
                        decoration: const InputDecoration(
                          labelText: '行車距離（公里）',
                          helperText: '由座標估算，可手動修正',
                          suffixText: 'km',
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: TextField(
                        controller: _tip,
                        keyboardType: const TextInputType.numberWithOptions(decimal: true),
                        onChanged: (String _) => setState(() => _estimate = null),
                        decoration: const InputDecoration(labelText: '貼士', prefixText: 'HK\$ '),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 16),

                Text('隧道', style: theme.textTheme.titleSmall),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 4,
                  children: <Widget>[
                    for (final Tunnel tunnel in Tunnel.values)
                      FilterChip(
                        label: Text(tunnel.labelZh),
                        selected: _tunnels.contains(tunnel),
                        onSelected: (bool selected) => setState(() {
                          // SEC-09: the server rejects more than 8, and the enum
                          // has exactly 8 members, so this cannot exceed it.
                          if (selected) {
                            _tunnels.add(tunnel);
                          } else {
                            _tunnels.remove(tunnel);
                          }
                          _estimate = null;
                        }),
                      ),
                  ],
                ),
                const SizedBox(height: 8),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  value: _crossesHarbour,
                  onChanged: (bool value) => setState(() {
                    _crossesHarbour = value;
                    _estimate = null;
                  }),
                  title: const Text('過海'),
                  subtitle: const Text('加入過海隧道附加費'),
                ),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  value: _atCrossHarbourStand,
                  onChanged: (bool value) => setState(() {
                    _atCrossHarbourStand = value;
                    _estimate = null;
                  }),
                  title: const Text('於過海的士站上車'),
                  subtitle: const Text('可豁免回程隧道費'),
                ),

                if (_estimate != null) ...<Widget>[
                  const SizedBox(height: 8),
                  _FareBreakdownCard(estimate: _estimate!),
                ],

                const SizedBox(height: 20),
                Row(
                  children: <Widget>[
                    Expanded(
                      child: OutlinedButton(
                        onPressed: (_busy || !_ready) ? null : _quote,
                        child: const Text('取得報價'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: FilledButton(
                        onPressed: (_busy || !_ready) ? null : _placeOrder,
                        child: _busy
                            ? const SizedBox(
                                width: 20,
                                height: 20,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Text('確認叫車'),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Text(
                  '車費為估算，實際以錶收費為準。下單後車費會即時凍結於訂單內。',
                  textAlign: TextAlign.center,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// The Cap. 374D breakdown, including the disclaimers the server sends.
class _FareBreakdownCard extends StatelessWidget {
  const _FareBreakdownCard({required this.estimate});

  final FareEstimate estimate;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: <Widget>[
                Text('報價', style: theme.textTheme.titleSmall),
                MoneyText(estimate.totalFare, style: theme.textTheme.headlineSmall),
              ],
            ),
            const Divider(height: 20),
            DetailRow(label: '起錶', valueWidget: MoneyText(estimate.meterFare, showSymbol: false)),
            if (!estimate.meterDiscount.isZero)
              DetailRow(label: '折扣', valueWidget: MoneyText(estimate.meterDiscount, signed: true)),
            if (!estimate.surchargesTotal.isZero)
              DetailRow(
                label: '附加費',
                valueWidget: MoneyText(estimate.surchargesTotal, showSymbol: false),
              ),
            for (final FareSurcharge s in estimate.surcharges)
              DetailRow(
                label: '  ${s.nameZh}',
                valueWidget: MoneyText(s.amount, showSymbol: false),
              ),
            if (!estimate.tip.isZero)
              DetailRow(label: '貼士', valueWidget: MoneyText(estimate.tip, showSymbol: false)),
            const SizedBox(height: 8),
            Text(
              estimate.disclaimerZh,
              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
            Text(
              'Tariff ${estimate.tariffVersion}',
              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
          ],
        ),
      ),
    );
  }
}
