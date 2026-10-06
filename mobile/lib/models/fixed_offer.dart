import '../core/format/money.dart';
import '../core/network/wire.dart';

/// A driver's standing fixed-fare offer (一口價) from
/// `/api/v1/drivers/me/fixed-offers`.
class FixedOffer {
  const FixedOffer({
    required this.id,
    required this.driverProfileId,
    required this.priceHkd,
    required this.status,
    this.destinationArea,
    this.premiumDestinationId,
    this.pickupArea,
    this.createdAt,
    this.updatedAt,
  });

  factory FixedOffer.fromJson(Map<String, dynamic> json) => FixedOffer(
    id: asString(json['id'], 'fixed_offer.id'),
    driverProfileId: asString(json['driver_profile_id'], 'fixed_offer.driver_profile_id'),
    destinationArea: asStringOrNull(json['destination_area'], 'fixed_offer.destination_area'),
    premiumDestinationId: asStringOrNull(
      json['premium_destination_id'],
      'fixed_offer.premium_destination_id',
    ),
    pickupArea: asStringOrNull(json['pickup_area'], 'fixed_offer.pickup_area'),
    priceHkd: Money.parse(json['price_hkd']),
    status: asString(json['status'], 'fixed_offer.status'),
    createdAt: asDateOrNull(json['created_at'], 'fixed_offer.created_at'),
    updatedAt: asDateOrNull(json['updated_at'], 'fixed_offer.updated_at'),
  );

  final String id;
  final String driverProfileId;
  final String? destinationArea;
  final String? premiumDestinationId;
  final String? pickupArea;
  final Money priceHkd;
  final String status;
  final DateTime? createdAt;
  final DateTime? updatedAt;

  bool get isActive => status == 'ACTIVE';
}

/// `GET/POST /api/v1/drivers/me/fixed-offers` — the list envelope.
class FixedOfferPage {
  const FixedOfferPage({required this.items});

  factory FixedOfferPage.fromJson(Map<String, dynamic> json) =>
      FixedOfferPage(items: asObjectList(json['items'], 'items', FixedOffer.fromJson));

  final List<FixedOffer> items;
}

/// Create body for a fixed-fare offer.
class FixedOfferCreateRequest {
  const FixedOfferCreateRequest({
    this.destinationArea,
    this.premiumDestinationId,
    this.pickupArea,
    required this.priceHkd,
  });

  final String? destinationArea;
  final String? premiumDestinationId;
  final String? pickupArea;
  final Money priceHkd;

  Map<String, dynamic> toJson() => <String, dynamic>{
    'destination_area': destinationArea,
    'premium_destination_id': premiumDestinationId,
    'pickup_area': pickupArea,
    'price_hkd': priceHkd.canonical,
  };
}

/// Update body for a fixed-fare offer.
class FixedOfferUpdateRequest {
  const FixedOfferUpdateRequest({this.priceHkd, this.status});

  final Money? priceHkd;
  final String? status;

  Map<String, dynamic> toJson() => <String, dynamic>{
    'price_hkd': priceHkd?.canonical,
    'status': status,
  };
}
