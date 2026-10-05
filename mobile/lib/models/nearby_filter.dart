import 'ride_requirements.dart';

/// Driver-side filters for `GET /orders/nearby`.
///
/// This mirrors the query-param contract of `app/api/orders.py::nearby_orders`.
/// The two closed sets below are *mirrors*, not authorities: the server answers
/// 422 for a key outside them, so drift fails loudly instead of degrading into a
/// filter that silently matches nothing. `tests/api/test_nearby_filters.py`
/// pins the server side of that contract.
class NearbyFilter {
  const NearbyFilter({
    this.fareMode,
    this.destinationArea,
    this.premiumDestinationId,
    this.requires = const <String>{},
    this.excludes = const <String>{},
  });

  /// `METER` (按錶) or `FIXED` (一口價); `null` means both.
  final String? fareMode;

  static const Map<String, String> fareModeLabelsZh = <String, String>{
    'METER': '按錶收費',
    'FIXED': '一口價',
  };

  /// Rough destination region — one of [areaCodes]; `null` means anywhere.
  ///
  /// This is the drop-off area, not the pick-up: a driver already filters by
  /// proximity through their own reported position.
  final String? destinationArea;

  /// A `premium_destinations.id`; `null` means any drop-off.
  final String? premiumDestinationId;

  /// Requirement keys the order must carry (the passenger asked for them).
  final Set<String> requires;

  /// Requirement keys the order must *not* carry — a driver without a pet
  /// carrier excludes [animalKey] rather than filtering out every order that
  /// asks for nothing.
  final Set<String> excludes;

  /// The closed set of destination areas, matching `app/core/region.py`.
  static const List<String> areaCodes = <String>[
    'HK_ISLAND',
    'KOWLOON',
    'NT',
    'AIRPORT',
    'LANTAU',
  ];

  static const Map<String, String> areaLabelsZh = <String, String>{
    'HK_ISLAND': '港島',
    'KOWLOON': '九龍',
    'NT': '新界',
    'AIRPORT': '機場',
    'LANTAU': '大嶼山',
  };

  /// The four in-car environment flags a passenger can ask for, matching
  /// `RideRequirementsIn` minus [animalKey].
  ///
  /// Read from [RideRequirements] rather than restated: the booking screen, the
  /// driver's job card and this filter all name the same four flags, and three
  /// copies of a closed set is three chances to drift.
  static const List<String> environmentKeys = RideRequirements.flagKeys;

  static const Map<String, String> environmentLabelsZh = RideRequirements.flagLabelsZh;

  /// `animal` is the one requirement that is an object, not a flag, so it is
  /// matched by presence rather than truthiness on both sides.
  static const String animalKey = animalRequirementKey;

  bool get isEmpty =>
      fareMode == null &&
      destinationArea == null &&
      premiumDestinationId == null &&
      requires.isEmpty &&
      excludes.isEmpty;

  NearbyFilter copyWith({
    Object? fareMode = _unset,
    Object? destinationArea = _unset,
    Object? premiumDestinationId = _unset,
    Set<String>? requires,
    Set<String>? excludes,
  }) => NearbyFilter(
    fareMode: identical(fareMode, _unset) ? this.fareMode : fareMode as String?,
    destinationArea: identical(destinationArea, _unset)
        ? this.destinationArea
        : destinationArea as String?,
    premiumDestinationId: identical(premiumDestinationId, _unset)
        ? this.premiumDestinationId
        : premiumDestinationId as String?,
    requires: requires ?? this.requires,
    excludes: excludes ?? this.excludes,
  );

  /// The query parameters for this filter. Empty collections are omitted so the
  /// unfiltered request stays byte-identical to the pre-filter client.
  ///
  /// Keys are sent comma-joined rather than repeated: the server accepts both,
  /// and one string is stable across HTTP clients.
  Map<String, dynamic> toQuery() => <String, dynamic>{
    if (fareMode != null) 'fare_mode': fareMode,
    if (destinationArea != null) 'destination_area': destinationArea,
    if (premiumDestinationId != null)
      'premium_destination_id': premiumDestinationId,
    if (requires.isNotEmpty) 'requires': requires.join(','),
    if (excludes.isNotEmpty) 'excludes': excludes.join(','),
  };

  @override
  bool operator ==(Object other) =>
      other is NearbyFilter &&
      other.fareMode == fareMode &&
      other.destinationArea == destinationArea &&
      other.premiumDestinationId == premiumDestinationId &&
      _sameSet(other.requires, requires) &&
      _sameSet(other.excludes, excludes);

  @override
  int get hashCode => Object.hash(
    fareMode,
    destinationArea,
    premiumDestinationId,
    Object.hashAllUnordered(requires),
    Object.hashAllUnordered(excludes),
  );

  static bool _sameSet(Set<String> a, Set<String> b) =>
      a.length == b.length && a.containsAll(b);
}

const Object _unset = Object();
