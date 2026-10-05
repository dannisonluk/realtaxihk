import '../core/network/wire.dart';

/// `GET/PUT /api/v1/drivers/me/payment-methods`.
class DriverPaymentMethods {
  const DriverPaymentMethods({required this.methods});

  factory DriverPaymentMethods.fromJson(Map<String, dynamic> json) =>
      DriverPaymentMethods(
        methods: asStringListOrEmpty(json['methods'], 'payment_methods.methods'),
      );

  final List<String> methods;

  Map<String, dynamic> toJson() => <String, dynamic>{'methods': methods};
}

/// `GET/PUT /api/v1/drivers/me/environment` — in-car capability flags.
class DriverEnvironment {
  const DriverEnvironment({
    required this.silentRide,
    required this.noRadioMusic,
    required this.noSmoke,
    required this.noPerfume,
  });

  factory DriverEnvironment.fromJson(Map<String, dynamic> json) =>
      DriverEnvironment(
        silentRide: json['silent_ride'] as bool? ?? false,
        noRadioMusic: json['no_radio_music'] as bool? ?? false,
        noSmoke: json['no_smoke'] as bool? ?? false,
        noPerfume: json['no_perfume'] as bool? ?? false,
      );

  final bool silentRide;
  final bool noRadioMusic;
  final bool noSmoke;
  final bool noPerfume;

  Map<String, dynamic> toJson() => <String, dynamic>{
    'silent_ride': silentRide,
    'no_radio_music': noRadioMusic,
    'no_smoke': noSmoke,
    'no_perfume': noPerfume,
  };
}