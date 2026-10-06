/// The ride requirements a passenger freezes onto an order.
///
/// This mirrors `RideRequirementsIn` in `app/api/orders.py` and is the **single
/// place** the four in-car environment keys and their labels are declared:
/// [NearbyFilter] reads them from here rather than restating them, so a key that
/// gains a label on one side cannot drift from the other. `tests/api/
/// test_nearby_filters.py` pins the server half of the same contract.
///
/// A requirement is a *statement by the passenger*, visible on the job card
/// before a driver accepts. It is not a platform guarantee: the platform is an
/// information intermediary (Cap. 374D), which is why the UI that offers these
/// carries a disclaimer.
library;

/// The one requirement that is an object rather than a flag. Kept here as well
/// as on [NearbyFilter] because the animal *presence* rule is the same on both
/// sides — the server matches it by `IS NOT NULL`, never by truthiness.
const String animalRequirementKey = 'animal';

/// A small animal travelling with the passenger.
///
/// `kind` is deliberately an open string on the wire (the server takes any
/// value up to 32 characters), but the client offers a short closed set so the
/// driver's job card never renders free-form text. Bounds mirror
/// `AnimalDetailIn`: they are checked here as well as server-side because a
/// field with no `keyboardType`-visible limit should still refuse `0 kg` before
/// spending a round trip on it.
class AnimalDetail {
  const AnimalDetail({required this.kind, required this.heightCm, required this.weightKg});

  final String kind;
  final double heightCm;
  final double weightKg;

  /// The kinds the picker offers. `DOG`/`CAT` cover the overwhelming majority
  /// of Hong Kong taxi pet requests; `OTHER` exists so a rabbit is not forced
  /// into either.
  static const List<String> kinds = <String>['DOG', 'CAT', 'OTHER'];

  static const Map<String, String> kindLabelsZh = <String, String>{
    'DOG': '狗',
    'CAT': '貓',
    'OTHER': '其他',
  };

  /// Server bounds (`AnimalDetailIn`), restated so the form can refuse before
  /// the request is sent.
  static const double minHeightCm = 1;
  static const double maxHeightCm = 200;
  static const double minWeightKg = 0.1;
  static const double maxWeightKg = 100;

  static bool isValidHeight(double cm) => cm >= minHeightCm && cm <= maxHeightCm;

  static bool isValidWeight(double kg) => kg >= minWeightKg && kg <= maxWeightKg;

  bool get isValid => isValidHeight(heightCm) && isValidWeight(weightKg) && kind.isNotEmpty;

  Map<String, dynamic> toJson() => <String, dynamic>{
    'kind': kind,
    // Numbers, not strings: the server field is `Decimal` and accepts either,
    // but a number is what an estimate request would send.
    'height_cm': heightCm,
    'weight_kg': weightKg,
  };

  /// Tolerant decode: the server stores `animal: null` on every order whose
  /// `requirements_json` was written by `model_dump()`, so a JSON null must read
  /// as "no animal" rather than as a half-filled one.
  static AnimalDetail? fromJson(Object? value) {
    if (value is! Map) {
      return null;
    }
    final Map<String, dynamic> map = value.cast<String, dynamic>();
    final Object? kind = map['kind'];
    final Object? height = map['height_cm'];
    final Object? weight = map['weight_kg'];
    if (kind is! String || kind.isEmpty || height == null || weight == null) {
      return null;
    }
    return AnimalDetail(kind: kind, heightCm: _toDouble(height), weightKg: _toDouble(weight));
  }

  static double _toDouble(Object value) =>
      value is num ? value.toDouble() : double.tryParse(value.toString()) ?? 0;
}

/// What the passenger needs the assigned driver to see before accepting.
///
/// An order with no requirements sends `requirements: null` rather than an
/// all-false object, so the stored JSONB matches what the passenger actually
/// asked for and `legacy` orders (created before the field existed) stay
/// indistinguishable from "asked for nothing".
class RideRequirements {
  const RideRequirements({
    this.silentRide = false,
    this.noRadioMusic = false,
    this.noSmoke = false,
    this.noPerfume = false,
    this.animal,
  });

  final bool silentRide;
  final bool noRadioMusic;
  final bool noSmoke;
  final bool noPerfume;
  final AnimalDetail? animal;

  /// The four boolean flags, in display order. `animal` is deliberately absent:
  /// it is an object, and the server matches it by presence.
  static const List<String> flagKeys = <String>[
    'silent_ride',
    'no_radio_music',
    'no_smoke',
    'no_perfume',
  ];

  static const Map<String, String> flagLabelsZh = <String, String>{
    'silent_ride': '全程靜音',
    'no_radio_music': '不播收音機／音樂',
    'no_smoke': '無煙車',
    'no_perfume': '無香水',
  };

  static const Map<String, String> flagSubtitleZh = <String, String>{
    'silent_ride': '司機全程不交談、不播放聲音',
    'no_radio_music': '收音機與音響全程關閉',
    'no_smoke': '車廂內不吸煙',
    'no_perfume': '不使用香水、香薰或空氣清新劑',
  };

  bool get isEmpty => !silentRide && !noRadioMusic && !noSmoke && !noPerfume && animal == null;

  /// The enabled flags as wire keys — used to render "what this order asks for"
  /// badges on the driver's job card.
  List<String> get enabledFlags => <String>[
    if (silentRide) 'silent_ride',
    if (noRadioMusic) 'no_radio_music',
    if (noSmoke) 'no_smoke',
    if (noPerfume) 'no_perfume',
  ];

  /// `POST /orders` payload. Only the flags the passenger actually asked for are
  /// sent; the server's own defaults cover the rest, and the animal object is
  /// included only when one was described.
  Map<String, dynamic> toJson() => <String, dynamic>{
    for (final String key in enabledFlags) key: true,
    if (animal != null) animalRequirementKey: animal!.toJson(),
  };

  RideRequirements copyWith({
    bool? silentRide,
    bool? noRadioMusic,
    bool? noSmoke,
    bool? noPerfume,
    Object? animal = _unset,
  }) => RideRequirements(
    silentRide: silentRide ?? this.silentRide,
    noRadioMusic: noRadioMusic ?? this.noRadioMusic,
    noSmoke: noSmoke ?? this.noSmoke,
    noPerfume: noPerfume ?? this.noPerfume,
    animal: identical(animal, _unset) ? this.animal : animal as AnimalDetail?,
  );

  /// Decode the order's frozen `requirements_json`.
  ///
  /// Tolerant by design: it takes the raw JSONB map straight off the wire and
  /// treats an absent key exactly like an explicit `false`, because the server
  /// writes both spellings depending on which client created the order.
  static RideRequirements fromJson(Map<String, dynamic>? json) {
    if (json == null) {
      return const RideRequirements();
    }
    bool flag(String key) => json[key] == true;
    return RideRequirements(
      silentRide: flag('silent_ride'),
      noRadioMusic: flag('no_radio_music'),
      noSmoke: flag('no_smoke'),
      noPerfume: flag('no_perfume'),
      animal: AnimalDetail.fromJson(json[animalRequirementKey]),
    );
  }
}

/// A sentinel so `copyWith(animal: null)` clears the animal while
/// `copyWith()` leaves it alone — `null` is a meaningful value here.
const Object _unset = Object();
