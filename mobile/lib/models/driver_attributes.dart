import '../core/network/wire.dart';

/// `GET/PUT /api/v1/drivers/me/payment-methods`.
class DriverPaymentMethods {
  const DriverPaymentMethods({required this.methods});

  factory DriverPaymentMethods.fromJson(Map<String, dynamic> json) =>
      DriverPaymentMethods(
        methods: asStringListOrEmpty(json['methods'], 'payment_methods.methods'),
      );

  final List<String> methods;

  /// The closed set, mirroring `PaymentMethod` in `app/models/premium.py`.
  ///
  /// Declared here — next to the model both the driver's settings screen and
  /// the passenger's preference chips read — rather than restated per screen, so
  /// a method can never have a chip on one side and no label on the other. The
  /// server validates the same six values and answers 422 for anything else.
  static const List<String> all = <String>[
    'CASH',
    'OCTOPUS',
    'CARD',
    'ALIPAY',
    'WECHAT_PAY',
    'TAP_AND_GO',
  ];

  static const Map<String, String> labelsZh = <String, String>{
    'CASH': '現金',
    'OCTOPUS': '八達通',
    'CARD': '信用卡',
    'ALIPAY': '支付寶',
    'WECHAT_PAY': '微信支付',
    'TAP_AND_GO': '掃碼易',
  };

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