import '../core/network/wire.dart';
import 'enums.dart';

/// A named landing/dropoff landmark from `GET /api/v1/landmarks`.
class Landmark {
  const Landmark({
    required this.id,
    required this.code,
    required this.nameEn,
    required this.nameZh,
    required this.category,
    required this.lat,
    required this.lng,
    required this.radiusM,
    required this.sortOrder,
    required this.isActive,
  });

  factory Landmark.fromJson(Map<String, dynamic> json) => Landmark(
    id: asString(json['id'], 'landmark.id'),
    code: asString(json['code'], 'landmark.code'),
    nameEn: asString(json['name_en'], 'landmark.name_en'),
    nameZh: asString(json['name_zh'], 'landmark.name_zh'),
    category: LandmarkCategory.fromWire(asString(json['category'], 'landmark.category')),
    lat: asDouble(json['lat'], 'landmark.lat'),
    lng: asDouble(json['lng'], 'landmark.lng'),
    radiusM: asInt(json['radius_m'], 'landmark.radius_m'),
    sortOrder: asInt(json['sort_order'], 'landmark.sort_order'),
    isActive: asBool(json['is_active'], 'landmark.is_active'),
  );

  final String id;
  final String code;
  final String nameEn;
  final String nameZh;
  final LandmarkCategory category;
  final double lat;
  final double lng;
  final int radiusM;
  final int sortOrder;
  final bool isActive;

  String get labelZh => nameZh.isEmpty ? nameEn : nameZh;

  String get labelEn => nameEn.isEmpty ? nameZh : nameEn;

  Map<String, dynamic> toJson() => <String, dynamic>{
    'id': id,
    'code': code,
    'name_en': nameEn,
    'name_zh': nameZh,
    'category': category.wire,
    'lat': lat,
    'lng': lng,
    'radius_m': radiusM,
    'sort_order': sortOrder,
    'is_active': isActive,
  };
}

/// `GET /api/v1/landmarks` — `{items: [...]}`.
class LandmarkPage {
  const LandmarkPage({required this.items});

  factory LandmarkPage.fromJson(Map<String, dynamic> json) =>
      LandmarkPage(items: asObjectList(json['items'], 'landmark.items', Landmark.fromJson));

  final List<Landmark> items;
}
