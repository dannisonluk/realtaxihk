import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/nearby_filter.dart';
import '../models/order.dart';
import 'providers.dart';

/// Holds the driver's current job-list filter.
///
/// A [Notifier] rather than a legacy `StateProvider`: Riverpod 3 moved the
/// latter behind `legacy.dart`, and this value has real behaviour worth
/// naming — see [NearbyFilterController.clear], which the empty-state button
/// needs.
class NearbyFilterController extends Notifier<NearbyFilter> {
  @override
  NearbyFilter build() => const NearbyFilter();

  void set(NearbyFilter next) => state = next;

  /// Replace one field, leaving the rest alone.
  void patch(NearbyFilter Function(NearbyFilter current) update) =>
      state = update(state);

  void toggleRequires(String key) => state = state.copyWith(
    requires: _toggled(state.requires, key),
  );

  void toggleExcludes(String key) =>
      state = state.copyWith(excludes: _toggled(state.excludes, key));

  /// Drop every filter. The driver asked to see everything again, so the next
  /// fetch is the unfiltered one — not a filter of nothing.
  void clear() => state = const NearbyFilter();

  static Set<String> _toggled(Set<String> current, String key) {
    final Set<String> next = Set<String>.of(current);
    if (!next.remove(key)) {
      next.add(key);
    }
    return next;
  }
}

final NotifierProvider<NearbyFilterController, NearbyFilter> nearbyFilterProvider =
    NotifierProvider<NearbyFilterController, NearbyFilter>(
      NearbyFilterController.new,
    );

/// `GET /orders/nearby` with [NearbyFilter] applied.
///
/// The family key is a record holding the centre *and* the filter, so a filter
/// change is a new cache entry rather than a stale list that keeps showing
/// orders the driver just filtered out. Records compare structurally and
/// [NearbyFilter] implements `==`, so identical queries share one entry.
final filteredNearbyOrdersProvider = FutureProvider.family<
  NearbyOrders,
  ({double lat, double lng, double radiusKm, NearbyFilter filter})
>(
  (Ref ref, ({double lat, double lng, double radiusKm, NearbyFilter filter}) q) =>
      ref
          .watch(orderRepositoryProvider)
          .nearby(lat: q.lat, lng: q.lng, radiusKm: q.radiusKm, filter: q.filter),
);
