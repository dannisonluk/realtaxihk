import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/location/location_service.dart';
import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../models/driver_attributes.dart';
import '../../models/enums.dart';
import '../../models/fare.dart';
import '../../models/identity.dart';
import '../../models/landmark.dart';
import '../../models/order.dart';
import '../../models/ride_requirements.dart';
import '../../router/app_router.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../auth/phone_unlock_screen.dart';
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
  const RequestRideScreen({super.key, this.prefill});

  /// Route/requirements template from a history row, for "book again".
  final OrderCreateRequest? prefill;

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

  /// What the passenger needs the driver to see before accepting.
  ///
  /// Held as one immutable value rather than five fields, so "is anything
  /// asked for" and "what goes on the wire" are answered in one place —
  /// `RideRequirements.toJson` omits an absent animal instead of sending a
  /// `null` the server would store as a JSON null.
  RideRequirements _requirements = const RideRequirements();

  /// The methods the passenger would prefer. Informational, not a guarantee —
  /// the driver's own declared methods are what the order is matched against.
  final Set<String> _paymentPreference = <String>{};

  OrderKind _orderKind = OrderKind.onDemand;
  DateTime? _scheduledPickupAt;
  Landmark? _selectedLandmark;

  /// Landmark id from a history order before the landmark list loads.
  String? _prefillLandmarkId;

  /// Draft for the animal detail sheet. Kept off [_requirements] until the
  /// passenger confirms, so opening and closing the sheet changes nothing.
  String _animalKind = AnimalDetail.kinds.first;
  final TextEditingController _animalHeight = TextEditingController(text: '35');
  final TextEditingController _animalWeight = TextEditingController(text: '8');

  @override
  void initState() {
    super.initState();
    final OrderCreateRequest? prefill = widget.prefill;
    if (prefill != null) {
      _pickup = MapPoint(
        lat: prefill.pickupLat,
        lng: prefill.pickupLng,
        label: prefill.pickupAddress.isEmpty ? '上車點' : prefill.pickupAddress,
      );
      _dropoff = MapPoint(
        lat: prefill.dropoffLat,
        lng: prefill.dropoffLng,
        label: prefill.dropoffAddress.isEmpty ? '落車點' : prefill.dropoffAddress,
      );
      _taxiType = prefill.taxiType;
      _tunnels
        ..clear()
        ..addAll(prefill.tunnels);
      _crossesHarbour = prefill.crossesHarbour;
      _atCrossHarbourStand = prefill.pickupAtCrossHarbourStand;
      _distance.text = prefill.distanceKm.toStringAsFixed(1);
      _tip.text = prefill.tip.toStringAsFixed(1);
      _requirements = RideRequirements.fromJson(prefill.requirements);
      _paymentPreference
        ..clear()
        ..addAll(prefill.paymentPreference);
      _orderKind = prefill.orderKind ?? OrderKind.onDemand;
      _scheduledPickupAt = prefill.scheduledPickupAt;
      _prefillLandmarkId = prefill.dropoffLandmarkId;
      return;
    }
    _locate();
  }

  @override
  void dispose() {
    _distance.dispose();
    _tip.dispose();
    _animalHeight.dispose();
    _animalWeight.dispose();
    super.dispose();
  }

  Future<void> _locate() async {
    setState(() => _locating = true);
    // Resolve permission once and reuse the answer in `current()` — calling
    // `ensureAccess()` again would raise the system dialog twice for an
    // initially-denied permission.
    final LocationAccess access = await _location.ensureAccess();
    final Position? position = access == LocationAccess.granted
        ? await _location.current(access: access)
        : null;
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
      showLocationUnavailable(
        context,
        access,
        onOpenSettings: () => _location.openSettings(access),
        onRetry: _locate,
        alternative: '可在地圖上點選上車點',
      );
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

  /// One environment flag. Toggling re-quotes nothing: a requirement is not a
  /// price input, so it must not clear the estimate the way a tunnel does.
  void _toggleRequirement(String key) {
    setState(() {
      _requirements = switch (key) {
        'silent_ride' => _requirements.copyWith(silentRide: !_requirements.silentRide),
        'no_radio_music' => _requirements.copyWith(noRadioMusic: !_requirements.noRadioMusic),
        'no_smoke' => _requirements.copyWith(noSmoke: !_requirements.noSmoke),
        'no_perfume' => _requirements.copyWith(noPerfume: !_requirements.noPerfume),
        _ => _requirements,
      };
    });
  }

  void _togglePayment(String method) {
    setState(() {
      if (!_paymentPreference.remove(method)) {
        _paymentPreference.add(method);
      }
    });
  }

  Future<void> _pickScheduledTime() async {
    final DateTime now = DateTime.now();
    final DateTime earliest = now.add(const Duration(hours: 2));
    final DateTime latest = now.add(const Duration(days: 3));
    final DateTime existing = _scheduledPickupAt ?? earliest;
    final DateTime initialDate = existing.isBefore(earliest)
        ? earliest
        : (existing.isAfter(latest) ? latest : existing);
    final DateTime? date = await showDatePicker(
      context: context,
      initialDate: initialDate,
      firstDate: earliest,
      lastDate: latest,
      helpText: '選擇預約日期',
    );
    if (date == null || !mounted) {
      return;
    }
    final TimeOfDay? time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(existing.isBefore(earliest) ? earliest : existing),
      helpText: '選擇預約時間',
    );
    if (time == null || !mounted) {
      return;
    }
    DateTime scheduled = DateTime(date.year, date.month, date.day, time.hour, time.minute);
    if (scheduled.isBefore(earliest)) {
      scheduled = earliest;
    }
    if (scheduled.isAfter(latest)) {
      scheduled = latest;
    }
    setState(() {
      _scheduledPickupAt = scheduled;
      _estimate = null;
    });
  }

  String _scheduledLabel() {
    final DateTime? value = _scheduledPickupAt;
    if (value == null) {
      return '未設定';
    }
    final String date = MaterialLocalizations.of(context).formatMediumDate(value);
    final String time = MaterialLocalizations.of(
      context,
    ).formatTimeOfDay(TimeOfDay.fromDateTime(value), alwaysUse24HourFormat: false);
    return '$date $time';
  }

  String? get _landmarkId => _selectedLandmark?.id ?? _prefillLandmarkId;

  /// Collect the animal description a driver needs *before* accepting.
  ///
  /// Refuses the submission rather than trusting the server to: the bounds are
  /// the server's own (`AnimalDetailIn`), and a driver reading the job card
  /// needs a plausible size, not `0 kg`.
  Future<void> _editAnimal() async {
    final AnimalDetail? existing = _requirements.animal;
    _animalKind = existing?.kind ?? AnimalDetail.kinds.first;
    _animalHeight.text = (existing?.heightCm ?? 35).toStringAsFixed(0);
    _animalWeight.text = (existing?.weightKg ?? 8).toStringAsFixed(0);

    final AnimalDetail? result = await showModalBottomSheet<AnimalDetail>(
      context: context,
      isScrollControlled: true,
      builder: (BuildContext sheetContext) => _AnimalSheet(
        kind: _animalKind,
        heightController: _animalHeight,
        weightController: _animalWeight,
        onKindChanged: (String kind) => setState(() => _animalKind = kind),
      ),
    );
    if (result == null || !mounted) {
      return;
    }
    setState(() => _requirements = _requirements.copyWith(animal: result));
  }

  bool get _ready =>
      _pickup != null &&
      _dropoff != null &&
      _distanceKm > 0 &&
      _distanceKm <= 100 &&
      (_orderKind != OrderKind.scheduled || _scheduledPickupAt != null);

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
      showInfo(context, '請先設定上車點、落車點及距離${_orderKind == OrderKind.scheduled ? '、預約時間' : ''}');
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
              // Omitted entirely when empty, so an order that asks for nothing
              // stores no `requirements_json` — which is what the driver's
              // nearby filters read as "never mentioned an animal".
              requirements: _requirements.isEmpty ? null : _requirements.toJson(),
              paymentPreference: _paymentPreference.toList(growable: false),
              orderKind: _orderKind,
              scheduledPickupAt: _orderKind == OrderKind.scheduled ? _scheduledPickupAt : null,
              dropoffLandmarkId: _landmarkId,
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
      if (!mounted) {
        return;
      }
      // `POST /orders` runs `require_phone_current`, so this is the one place a
      // passenger can meet a 403 they cannot resolve from this screen. The quote
      // above is **not** gated, so it must keep the plain toast: an unverified
      // passenger is allowed to see prices, and offering the unlock there would
      // imply otherwise.
      if (!offerPhoneUnlockIfNeeded(context, e)) {
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
    final PremiumDestinationPage premium =
        ref.watch(premiumDestinationsProvider).value ??
        const PremiumDestinationPage(items: <PremiumDestination>[]);
    // Null while the profile is still loading, which is deliberately treated as
    // "no prompt" rather than "no username" — a card that appears and then
    // vanishes on every cold start reads as a bug.
    final Profile? profile = ref.watch(profileProvider).value;
    final List<Landmark> landmarks = ref.watch(landmarksProvider(null)).value ?? const <Landmark>[];

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
              markers: <MapPoint>[
                ?pickup,
                ?dropoff,
                for (final PremiumDestination pin in premium.items)
                  if (pin.lat != pickup?.lat || pin.lng != pickup?.lng)
                    if (pin.lat != dropoff?.lat || pin.lng != dropoff?.lng)
                      MapPoint(lat: pin.lat, lng: pin.lng, label: '★ ${pin.nameZh}'),
              ],
              route: (pickup != null && dropoff != null) ? <MapPoint>[pickup, dropoff] : null,
              onTap: _onMapTap,
              zoom: 13,
            ),
          ),
          Expanded(
            child: SafeArea(
              top: false,
              child: ListView(
                padding: const EdgeInsets.all(AppTheme.space4),
                children: <Widget>[
                  // A brand-new account has no username — `POST /auth/register`
                  // does not ask for one — and every receipt, order row and
                  // roster line that names the passenger would then render "—".
                  // Offered here, on the screen a fresh account lands on,
                  // because this is the one place the gap is otherwise invisible.
                  //
                  // Offered, **not gated**: the server never refuses a booking
                  // for an incomplete profile (`AccountStatus` is a completeness
                  // flag, and no guard in `app/core/deps.py` reads it), so this
                  // must not be the only way to reach the booking button either.
                  if (profile != null && profile.username == null) ...<Widget>[
                    Card(
                      child: ListTile(
                        leading: const Icon(Icons.badge_outlined),
                        title: const Text('補完個人資料'),
                        subtitle: const Text('設定姓名後，訂單與收據會顯示你的名字'),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => context.push(Routes.profileSetup),
                      ),
                    ),
                    const SizedBox(height: AppTheme.space4),
                  ],
                  SegmentedButton<OrderKind>(
                    segments: const <ButtonSegment<OrderKind>>[
                      ButtonSegment<OrderKind>(
                        value: OrderKind.onDemand,
                        label: Text('現在 Call'),
                        icon: Icon(Icons.bolt),
                      ),
                      ButtonSegment<OrderKind>(
                        value: OrderKind.scheduled,
                        label: Text('預約 Call'),
                        icon: Icon(Icons.event_available_outlined),
                      ),
                    ],
                    selected: <OrderKind>{_orderKind},
                    onSelectionChanged: (Set<OrderKind> value) => setState(() {
                      _orderKind = value.first;
                      _estimate = null;
                    }),
                  ),
                  if (_orderKind == OrderKind.scheduled) ...<Widget>[
                    const SizedBox(height: AppTheme.space2),
                    Text(
                      '預約時間需早於叫車最少 2 小時，最多可預約 3 日內。',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                    const SizedBox(height: AppTheme.space2),
                    Card(
                      child: ListTile(
                        leading: const Icon(Icons.calendar_month_outlined),
                        title: const Text('預約上車時間'),
                        subtitle: Text(_scheduledLabel()),
                        trailing: const Icon(Icons.schedule),
                        onTap: _pickScheduledTime,
                      ),
                    ),
                  ],
                  if (landmarks.isNotEmpty) ...<Widget>[
                    const SizedBox(height: AppTheme.space4),
                    Text('目的地地標（選填）', style: theme.textTheme.titleSmall),
                    const SizedBox(height: AppTheme.space2),
                    SizedBox(
                      height: 40,
                      child: ListView.separated(
                        scrollDirection: Axis.horizontal,
                        itemCount: landmarks.length,
                        separatorBuilder: (BuildContext context, int index) =>
                            const SizedBox(width: AppTheme.space2),
                        itemBuilder: (BuildContext context, int index) {
                          final Landmark landmark = landmarks[index];
                          return FilterChip(
                            label: Text(landmark.labelZh),
                            selected:
                                _selectedLandmark?.id == landmark.id ||
                                (_prefillLandmarkId == landmark.id && _selectedLandmark == null),
                            onSelected: (bool selected) => setState(() {
                              _selectedLandmark = selected ? landmark : null;
                              if (selected) {
                                _prefillLandmarkId = null;
                              }
                            }),
                          );
                        },
                      ),
                    ),
                  ],
                  const SizedBox(height: AppTheme.space4),
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
                    onSelectionChanged: (Set<_Target> value) =>
                        setState(() => _target = value.first),
                  ),
                  const SizedBox(height: AppTheme.space2),
                  Text(
                    '點選地圖設定${_target == _Target.pickup ? '上車' : '落車'}位置',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                  const SizedBox(height: AppTheme.space4),

                  Text('的士種類', style: theme.textTheme.titleSmall),
                  const SizedBox(height: AppTheme.space2),
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
                  const SizedBox(height: AppTheme.space4),

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
                      const SizedBox(width: AppTheme.space3),
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
                  const SizedBox(height: AppTheme.space4),

                  Text('隧道', style: theme.textTheme.titleSmall),
                  const SizedBox(height: AppTheme.space2),
                  Wrap(
                    spacing: AppTheme.space2,
                    runSpacing: AppTheme.space1,
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
                  const SizedBox(height: AppTheme.space2),
                  // The two surcharge toggles are one grouped card, which is how
                  // iOS presents a run of related switches.
                  GroupedSection(
                    title: '附加費',
                    footnote: '過海附加費與回程隧道費會即時反映在報價內。',
                    children: <Widget>[
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
                    ],
                  ),

                  // What the driver must be able to provide. Every control here
                  // is a *statement the passenger makes*, visible on the job
                  // card before anyone accepts — the platform is an information
                  // intermediary (Cap. 374D) and does not verify the car.
                  GroupedSection(
                    title: '車內環境要求',
                    footnote:
                        '這些要求會顯示給司機，讓對方在接單前決定是否合適；'
                        '平台僅屬資訊中介，不會代司機保證。',
                    children: <Widget>[
                      for (final MapEntry<String, String> entry
                          in RideRequirements.flagLabelsZh.entries)
                        SwitchListTile(
                          contentPadding: EdgeInsets.zero,
                          value: _requirements.enabledFlags.contains(entry.key),
                          onChanged: (bool _) => _toggleRequirement(entry.key),
                          title: Text(entry.value),
                          subtitle: Text(RideRequirements.flagSubtitleZh[entry.key] ?? ''),
                        ),
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.pets),
                        title: const Text('攜帶小動物'),
                        // The driver must know the species and rough size
                        // *before* accepting; an order that says only "pet"
                        // leaves them guessing at a carrier they may not have.
                        subtitle: Text(
                          _requirements.animal == null
                              ? '未填寫'
                              : '${AnimalDetail.kindLabelsZh[_requirements.animal!.kind] ?? _requirements.animal!.kind}'
                                    '　高約 ${_requirements.animal!.heightCm.toStringAsFixed(0)} cm'
                                    '、約 ${_requirements.animal!.weightKg.toStringAsFixed(0)} kg',
                        ),
                        trailing: _requirements.animal == null
                            ? null
                            : IconButton(
                                tooltip: '移除',
                                icon: const Icon(Icons.clear),
                                onPressed: () => setState(
                                  () => _requirements = _requirements.copyWith(animal: null),
                                ),
                              ),
                        onTap: _editAnimal,
                      ),
                    ],
                  ),
                  const SizedBox(height: AppTheme.space4),

                  // A preference, not a promise: the server matches the
                  // *driver's* declared methods onto the order at grab time, and
                  // this list only tells them what the passenger would like.
                  GroupedSection(
                    title: '付款方式偏好',
                    footnote: '只屬偏好。司機實際接受的方式會在下單後顯示。',
                    children: <Widget>[
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: AppTheme.space2),
                        child: Wrap(
                          spacing: AppTheme.space2,
                          runSpacing: AppTheme.space1,
                          children: <Widget>[
                            for (final String method in DriverPaymentMethods.all)
                              FilterChip(
                                label: Text(DriverPaymentMethods.labelsZh[method] ?? method),
                                selected: _paymentPreference.contains(method),
                                onSelected: (bool _) => _togglePayment(method),
                              ),
                          ],
                        ),
                      ),
                    ],
                  ),

                  if (_estimate != null) ...<Widget>[
                    const SizedBox(height: AppTheme.space4),
                    _FareBreakdownCard(estimate: _estimate!),
                  ],
                  const SizedBox(height: AppTheme.space6),
                  Row(
                    children: <Widget>[
                      Expanded(
                        child: OutlinedButton(
                          onPressed: (_busy || !_ready) ? null : _quote,
                          child: const Text('取得報價'),
                        ),
                      ),
                      const SizedBox(width: AppTheme.space3),
                      Expanded(
                        child: FilledButton(
                          onPressed: (_busy || !_ready) ? null : _placeOrder,
                          child: _busy
                              ? const SizedBox(
                                  width: 20,
                                  height: 20,
                                  child: CircularProgressIndicator(strokeWidth: 2),
                                )
                              : Text(_orderKind == OrderKind.scheduled ? '確認預約' : '確認叫車'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: AppTheme.space3),
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
        padding: const EdgeInsets.all(AppTheme.space4),
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
            const Divider(height: AppTheme.space6),
            DetailRow(label: '起錶', valueWidget: MoneyText(estimate.meterFare, showSymbol: false)),
            if (!estimate.discountPercent.isZero)
              DetailRow(
                label: '折扣率',
                valueWidget: MoneyText(estimate.discountPercent, showSymbol: false),
              ),
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
            const SizedBox(height: AppTheme.space2),
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

/// The animal description a driver reads before accepting.
///
/// A bottom sheet rather than a screen: it is three fields belonging to the
/// booking form, and the passenger must be able to dismiss it without losing
/// the ride they were half-way through setting up.
///
/// The bounds it enforces are the server's own (`AnimalDetailIn`): 1–200 cm and
/// 0.1–100 kg. Checking here is not belt-and-braces — the server would answer
/// 422 *after* the passenger had left the form, and by then the species and size
/// they typed are gone.
class _AnimalSheet extends StatelessWidget {
  const _AnimalSheet({
    required this.kind,
    required this.heightController,
    required this.weightController,
    required this.onKindChanged,
  });

  final String kind;
  final TextEditingController heightController;
  final TextEditingController weightController;
  final ValueChanged<String> onKindChanged;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);

    return Padding(
      // Lifts the sheet above the keyboard, which otherwise covers the field
      // being typed into.
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(AppTheme.space4),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text('小動物資料', style: theme.textTheme.titleMedium),
              const SizedBox(height: AppTheme.space2),
              Text(
                '司機接單前會看到種類與大約尺寸。',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: AppTheme.space4),
              SegmentedButton<String>(
                segments: <ButtonSegment<String>>[
                  for (final String k in AnimalDetail.kinds)
                    ButtonSegment<String>(value: k, label: Text(AnimalDetail.kindLabelsZh[k] ?? k)),
                ],
                selected: <String>{kind},
                onSelectionChanged: (Set<String> value) => onKindChanged(value.first),
              ),
              const SizedBox(height: AppTheme.space4),
              Row(
                children: <Widget>[
                  Expanded(
                    child: TextField(
                      controller: heightController,
                      keyboardType: const TextInputType.numberWithOptions(decimal: true),
                      decoration: const InputDecoration(labelText: '高度', suffixText: 'cm'),
                    ),
                  ),
                  const SizedBox(width: AppTheme.space3),
                  Expanded(
                    child: TextField(
                      controller: weightController,
                      keyboardType: const TextInputType.numberWithOptions(decimal: true),
                      decoration: const InputDecoration(labelText: '重量', suffixText: 'kg'),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: AppTheme.space4),
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  onPressed: () {
                    final AnimalDetail detail = AnimalDetail(
                      kind: kind,
                      heightCm: double.tryParse(heightController.text) ?? 0,
                      weightKg: double.tryParse(weightController.text) ?? 0,
                    );
                    if (!detail.isValid) {
                      showInfo(
                        context,
                        '請輸入合理尺寸：高度 ${AnimalDetail.minHeightCm.toStringAsFixed(0)}–'
                        '${AnimalDetail.maxHeightCm.toStringAsFixed(0)} cm、'
                        '重量 ${AnimalDetail.minWeightKg}–'
                        '${AnimalDetail.maxWeightKg.toStringAsFixed(0)} kg。',
                      );
                      return;
                    }
                    Navigator.of(context).pop(detail);
                  },
                  child: const Text('確定'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
