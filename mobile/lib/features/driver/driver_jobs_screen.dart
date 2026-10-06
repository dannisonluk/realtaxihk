import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/location/location_service.dart';
import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../models/enums.dart';
import '../../models/nearby_filter.dart';
import '../../models/order.dart';
import '../../models/ride_requirements.dart';
import '../../router/app_router.dart';
import '../../state/data_providers.dart';
import '../../state/nearby_filter.dart';
import '../../state/providers.dart';
import '../auth/phone_unlock_screen.dart';
import '../shared/map_panel.dart';
import '../shared/widgets.dart';

/// The driver's job list.
///
/// `GET /orders/nearby` reads a Redis geo index, so the driver must be reporting
/// a position for anything to be within range. Going online is therefore not
/// cosmetic: it is what makes the list non-empty, and `POST /drivers/location`
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
      ref.invalidate(filteredNearbyOrdersProvider);
      if (mounted) {
        await context.push('${Routes.driverActiveTrip}/${grabbed.id}');
      }
    } on ApiException catch (e) {
      // A 409 means another driver won the race — the lock plus the conditional
      // UPDATE guarantee exactly one winner, so this is not retryable.
      if (mounted) {
        // `POST /orders/{id}/grab` runs `require_phone_current`. A driver is not
        // exempt: a proven number is what makes the account accountable for the
        // orders it takes on.
        if (!offerPhoneUnlockIfNeeded(context, e)) {
          showError(context, e);
        }
        ref.invalidate(filteredNearbyOrdersProvider);
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
          SafeArea(
            bottom: false,
            child: Column(
              children: <Widget>[
                Card(
                  margin: const EdgeInsets.fromLTRB(
                    AppTheme.space4,
                    AppTheme.space3,
                    AppTheme.space4,
                    0,
                  ),
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
                      color: _online
                          ? theme.colorScheme.primary
                          : theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ),
                if (_locationNote != null)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(
                      AppTheme.space4,
                      AppTheme.space3,
                      AppTheme.space4,
                      0,
                    ),
                    child: Row(
                      children: <Widget>[
                        Icon(Icons.location_off, size: 16, color: theme.colorScheme.error),
                        const SizedBox(width: AppTheme.space2),
                        Expanded(child: Text(_locationNote!, style: theme.textTheme.bodySmall)),
                      ],
                    ),
                  ),
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
    final NearbyFilter filter = ref.watch(nearbyFilterProvider);
    final AsyncValue<NearbyOrders> nearby = ref.watch(
      filteredNearbyOrdersProvider((lat: me.lat, lng: me.lng, radiusKm: 3, filter: filter)),
    );

    return Column(
      children: <Widget>[
        _premiumPins(),
        _filterBar(filter),
        Expanded(
          child: AsyncValueView<NearbyOrders>(
            value: nearby,
            onRetry: () => ref.invalidate(filteredNearbyOrdersProvider),
            builder: (NearbyOrders data) {
              // P2-10: the server fails open with an empty page when Redis is
              // down. "Searching" is the honest message — there may well be
              // orders. A filter that matches nothing is a different story,
              // and says so below.
              if (data.degraded) {
                return const EmptyView(
                  icon: Icons.cloud_off,
                  title: '暫時無法搜尋附近訂單',
                  subtitle: '定位服務暫時不可用，請稍後重新整理。',
                );
              }
              if (data.items.isEmpty) {
                final bool filtered = !filter.isEmpty;
                return RefreshIndicator(
                  onRefresh: () async => ref.invalidate(filteredNearbyOrdersProvider),
                  child: ListView(
                    children: <Widget>[
                      const SizedBox(height: 120),
                      EmptyView(
                        icon: filtered ? Icons.filter_alt_off : Icons.search_off,
                        title: filtered ? '沒有符合條件的訂單' : '附近沒有待接訂單',
                        subtitle: filtered ? '條件太窄，試下放寬。' : '下拉重新整理。',
                      ),
                      if (filtered)
                        Center(
                          child: TextButton.icon(
                            onPressed: () => ref.read(nearbyFilterProvider.notifier).clear(),
                            icon: const Icon(Icons.clear_all),
                            label: const Text('清除篩選'),
                          ),
                        ),
                    ],
                  ),
                );
              }
              return RefreshIndicator(
                onRefresh: () async => ref.invalidate(filteredNearbyOrdersProvider),
                child: ListView.separated(
                  padding: const EdgeInsets.all(AppTheme.space4),
                  itemCount: data.items.length,
                  separatorBuilder: (BuildContext context, int index) =>
                      const SizedBox(height: AppTheme.space3),
                  itemBuilder: (BuildContext context, int index) {
                    final Order order = data.items[index];
                    return Card(
                      child: Padding(
                        padding: const EdgeInsets.all(AppTheme.space4),
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
                            const SizedBox(height: AppTheme.space2),
                            if (order.fare.tunnels.isNotEmpty)
                              Text(
                                '經 ${order.fare.tunnels.map((Tunnel t) => t.labelZh).join('、')}',
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                            _jobBadges(order),
                            const SizedBox(height: AppTheme.space3),
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
          ),
        ),
      ],
    );
  }

  /// What the passenger asked for, plus the destination pin, shown **before**
  /// the driver accepts.
  ///
  /// This is the point of freezing requirements onto the order: a driver without
  /// a pet carrier, or who will not take a silent trip, must be able to pass on
  /// the job without first claiming it and then cancelling — a cancellation after
  /// `ACCEPTED` writes a real `PENALTY_DEDUCTION`. Rendering these after the grab
  /// button would make each of them cost the driver money.
  Widget _jobBadges(Order order) {
    final RideRequirements requirements = RideRequirements.fromJson(order.requirements);
    final PremiumDestination? premium = order.premiumDestination;
    if (requirements.isEmpty && premium == null) {
      return const SizedBox.shrink();
    }

    return Padding(
      padding: const EdgeInsets.only(top: AppTheme.space2),
      child: Wrap(
        spacing: AppTheme.space2,
        runSpacing: AppTheme.space1,
        children: <Widget>[
          if (premium != null)
            Chip(avatar: const Icon(Icons.flight_takeoff, size: 18), label: Text(premium.nameZh)),
          for (final String key in requirements.enabledFlags)
            Chip(
              avatar: Icon(_requirementIcon(key), size: 18),
              label: Text(RideRequirements.flagLabelsZh[key] ?? key),
            ),
          if (requirements.animal != null)
            Chip(
              avatar: const Icon(Icons.pets, size: 18),
              // Species and size together: "has a pet" alone tells a driver
              // nothing about whether their carrier fits.
              label: Text(
                '${AnimalDetail.kindLabelsZh[requirements.animal!.kind] ?? requirements.animal!.kind}'
                '　${requirements.animal!.heightCm.toStringAsFixed(0)} cm'
                '／${requirements.animal!.weightKg.toStringAsFixed(0)} kg',
              ),
            ),
        ],
      ),
    );
  }

  static IconData _requirementIcon(String key) => switch (key) {
    'silent_ride' => Icons.volume_off,
    'no_radio_music' => Icons.music_off,
    'no_smoke' => Icons.smoke_free,
    'no_perfume' => Icons.air,
    _ => Icons.check_circle_outline,
  };

  /// The premium destinations a driver may recognise — airport terminals,
  /// airline bases, the CAD — shown as pins above the job list.
  ///
  /// These are *places*, and the list is public metadata, so this card answers
  /// "where is the work likely to be", not "who is the passenger". The curated
  /// list is what makes such a trip predictable, which is the whole reason a
  /// driver is willing to price it flat (Phase 2). Tapping one narrows the
  /// list to that destination — server-side, via the same filter the chips use.
  ///
  /// Renders nothing when the list is empty or still loading: a spinner for
  /// optional metadata above a working job list would be noise.
  Widget _premiumPins() {
    final AsyncValue<PremiumDestinationPage> destinations = ref.watch(premiumDestinationsProvider);
    final List<PremiumDestination> items =
        destinations.value?.items ?? const <PremiumDestination>[];
    if (items.isEmpty) {
      return const SizedBox.shrink();
    }
    final String? selected = ref.watch(nearbyFilterProvider).premiumDestinationId;

    return SizedBox(
      height: 44,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.fromLTRB(AppTheme.space4, AppTheme.space3, AppTheme.space4, 0),
        children: <Widget>[
          for (final PremiumDestination d in items) ...<Widget>[
            FilterChip(
              avatar: const Icon(Icons.flight_takeoff, size: 18),
              label: Text(d.nameZh),
              selected: selected == d.id,
              onSelected: (bool nowSelected) => ref
                  .read(nearbyFilterProvider.notifier)
                  .patch(
                    (NearbyFilter f) => f.copyWith(premiumDestinationId: nowSelected ? d.id : null),
                  ),
            ),
            const SizedBox(width: AppTheme.space2),
          ],
        ],
      ),
    );
  }

  /// The filter row. Every control writes into [nearbyFilterProvider], whose
  /// value is part of the family key of [filteredNearbyOrdersProvider] — so a
  /// change refetches from the server instead of filtering on the device.
  ///
  /// Server-side is the only correct place for this: the geo index is capped at
  /// a fixed number of candidates, and filtering a capped list on the device
  /// would show the driver fewer orders than exist.
  Widget _filterBar(NearbyFilter filter) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.fromLTRB(AppTheme.space4, AppTheme.space2, AppTheme.space4, 0),
      child: Row(
        children: <Widget>[
          _filterMenu(
            label: '收費模式',
            icon: Icons.receipt_long,
            current: filter.fareMode,
            options: NearbyFilter.fareModeLabelsZh,
            onPick: (String? value) => ref
                .read(nearbyFilterProvider.notifier)
                .patch((NearbyFilter f) => f.copyWith(fareMode: value)),
          ),
          const SizedBox(width: AppTheme.space2),
          _filterMenu(
            label: '目的地地區',
            icon: Icons.place_outlined,
            current: filter.destinationArea,
            options: NearbyFilter.areaLabelsZh,
            onPick: (String? value) => ref
                .read(nearbyFilterProvider.notifier)
                .patch((NearbyFilter f) => f.copyWith(destinationArea: value)),
          ),
          const SizedBox(width: AppTheme.space2),
          FilterChip(
            avatar: const Icon(Icons.volume_off, size: 18),
            label: const Text('靜音'),
            selected: filter.requires.contains('silent_ride'),
            onSelected: (bool _) =>
                ref.read(nearbyFilterProvider.notifier).toggleRequires('silent_ride'),
          ),
          const SizedBox(width: AppTheme.space2),
          FilterChip(
            avatar: const Icon(Icons.smoke_free, size: 18),
            label: const Text('無煙'),
            selected: filter.requires.contains('no_smoke'),
            onSelected: (bool _) =>
                ref.read(nearbyFilterProvider.notifier).toggleRequires('no_smoke'),
          ),
          const SizedBox(width: AppTheme.space2),
          FilterChip(
            avatar: const Icon(Icons.pets, size: 18),
            label: const Text('可載寵物'),
            selected: filter.excludes.contains(NearbyFilter.animalKey),
            onSelected: (bool _) =>
                ref.read(nearbyFilterProvider.notifier).toggleExcludes(NearbyFilter.animalKey),
          ),
          if (!filter.isEmpty) ...<Widget>[
            const SizedBox(width: AppTheme.space2),
            ActionChip(
              avatar: const Icon(Icons.clear, size: 18),
              label: const Text('清除'),
              onPressed: () => ref.read(nearbyFilterProvider.notifier).clear(),
            ),
          ],
        ],
      ),
    );
  }

  /// A tappable chip that opens a `不限` + options menu. Used for the two
  /// single-valued filters; the boolean requirements are plain [FilterChip]s.
  Widget _filterMenu({
    required String label,
    required IconData icon,
    required String? current,
    required Map<String, String> options,
    required ValueChanged<String?> onPick,
  }) {
    return PopupMenuButton<String?>(
      onSelected: onPick,
      itemBuilder: (BuildContext context) => <PopupMenuEntry<String?>>[
        const PopupMenuItem<String?>(value: null, child: Text('不限')),
        for (final MapEntry<String, String> entry in options.entries)
          PopupMenuItem<String?>(value: entry.key, child: Text(entry.value)),
      ],
      child: Chip(
        avatar: Icon(icon, size: 18),
        label: Text(current == null ? label : '$label：${options[current] ?? current}'),
        deleteIcon: current == null ? null : const Icon(Icons.close, size: 16),
        onDeleted: current == null ? null : () => onPick(null),
      ),
    );
  }
}
