import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/format/money.dart';
import '../../core/network/api_exception.dart';
import '../../core/network/wire.dart';
import '../../core/theme/app_theme.dart';
import '../../models/receipt.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// The frozen receipt for one order, as a document rather than a screen.
///
/// `POST /orders/{id}/receipt` issues it; `GET` returns the same bytes. The
/// server freezes the document on first issue and never recomputes it, so the
/// figures shown here cannot drift after a tariff change — a receipt that
/// re-priced itself on every read would be worthless as a record.
///
/// The rendered text comes from the server (`Receipt.text`) rather than being
/// re-laid-out here. That is deliberate: the document a passenger forwards must
/// be the document the platform issued, and a second client-side layout is a
/// second thing that can disagree with it.
class ReceiptScreen extends ConsumerStatefulWidget {
  const ReceiptScreen({required this.orderId, this.autoIssue = false, super.key});

  final String orderId;

  /// Issue on open (`POST`) rather than only reading (`GET`).
  ///
  /// The passenger tapping 「索取收據」 asked for the document, so the platform
  /// records that request — that is what `receipt_requested_at` is for. Opening
  /// an existing receipt from a history row uses the read path instead, which
  /// freezes it if it somehow was not already.
  final bool autoIssue;

  @override
  ConsumerState<ReceiptScreen> createState() => _ReceiptScreenState();
}

class _ReceiptScreenState extends ConsumerState<ReceiptScreen> {
  Receipt? _receipt;
  Object? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final receipt = widget.autoIssue
          ? await ref.read(orderRepositoryProvider).requestReceipt(widget.orderId)
          : await ref.read(orderRepositoryProvider).receipt(widget.orderId);
      if (mounted) {
        setState(() => _receipt = receipt);
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _error = e);
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  /// Copy the server's own rendered document.
  ///
  /// The clipboard, not a share sheet: the project carries no `share_plus`
  /// dependency, and adding a platform plugin for one button is a deployment
  /// decision, not a receipt one. Copying the text is what makes the document
  /// forwardable today.
  Future<void> _copy() async {
    final Receipt? receipt = _receipt;
    if (receipt == null) {
      return;
    }
    await Clipboard.setData(ClipboardData(text: receipt.text));
    if (mounted) {
      showInfo(context, '收據內容已複製');
    }
  }

  @override
  Widget build(BuildContext context) {
    final Receipt? receipt = _receipt;
    final ThemeData theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(
        title: const Text('電子收據'),
        actions: <Widget>[
          if (receipt != null)
            IconButton(
              onPressed: _copy,
              tooltip: '複製收據內容',
              icon: const Icon(Icons.copy_all_outlined),
            ),
        ],
      ),
      body: switch ((receipt, _error)) {
        (final Receipt r, _) => _body(context, theme, r),
        (null, final Object e) => ErrorView(error: e, onRetry: _load),
        _ => const Center(child: CircularProgressIndicator()),
      },
      bottomNavigationBar: receipt == null
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(AppTheme.space4),
                child: FilledButton.icon(
                  onPressed: _busy ? null : _copy,
                  icon: const Icon(Icons.copy_all_outlined),
                  label: const Text('複製收據內容'),
                ),
              ),
            ),
    );
  }

  Widget _body(BuildContext context, ThemeData theme, Receipt receipt) {
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(AppTheme.space4),
        children: <Widget>[
          Card(
            child: Padding(
              padding: const EdgeInsets.all(AppTheme.space4),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Row(
                    children: <Widget>[
                      Text('行程收據', style: theme.textTheme.titleMedium),
                      const Spacer(),
                      MoneyText(receipt.totalHkd, style: theme.textTheme.headlineSmall),
                    ],
                  ),
                  const SizedBox(height: AppTheme.space3),
                  // The fare mode is shown as a badge rather than a line, so a
                  // 一口價 total is never read as a metered one. `fareMode` is
                  // null on orders created before Phase 2, hence the fallback.
                  if (receipt.isFixedFare)
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: AppTheme.space2,
                        vertical: AppTheme.space1 - 2,
                      ),
                      decoration: BoxDecoration(
                        color: theme.colorScheme.primaryContainer,
                        borderRadius: BorderRadius.circular(AppTheme.radiusField - 4),
                      ),
                      child: Text(
                        '一口價',
                        style: theme.textTheme.labelSmall?.copyWith(
                          color: theme.colorScheme.onPrimaryContainer,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                  if (receipt.isFixedFare) const SizedBox(height: AppTheme.space3),
                  DetailRow(label: '的士類型', value: receipt.taxiType),
                  DetailRow(label: '發出時間', value: receipt.issuedAt),
                  if (receipt.passengerName != null)
                    DetailRow(label: '乘客', value: receipt.passengerName!),
                  DetailRow(label: '訂單編號', value: receipt.orderId),
                  if (receipt.createdAt != null)
                    DetailRow(label: '建立時間', value: Format.dateTime(receipt.createdAt)),
                  if (receipt.completedAt != null)
                    DetailRow(label: '完成時間', value: Format.dateTime(receipt.completedAt)),
                ],
              ),
            ),
          ),
          const SizedBox(height: AppTheme.space3),

          Card(
            child: Padding(
              padding: const EdgeInsets.all(AppTheme.space4),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text('行程', style: theme.textTheme.titleMedium),
                  const SizedBox(height: AppTheme.space3),
                  DetailRow(label: '上車', value: receipt.pickupAddress),
                  DetailRow(label: '落車', value: receipt.dropoffAddress),
                  DetailRow(label: '距離', value: '${receipt.distanceKm} km'),
                  if (receipt.pickupArea != null)
                    DetailRow(
                      label: '上車地區',
                      value: _areaLabel(receipt.pickupArea!),
                    ),
                  if (receipt.destinationArea != null)
                    DetailRow(
                      label: '落車地區',
                      value: _areaLabel(receipt.destinationArea!),
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(height: AppTheme.space3),

          // A fixed-fare order carries the split explicitly, because the
          // platform's own fee must never read as a hidden spread on either
          // side's total.
          if (receipt.fixedFare != null) ...<Widget>[
            _fixedFareCard(theme, receipt.fixedFare!),
            const SizedBox(height: AppTheme.space3),
          ],

          if (receipt.paymentPreference.isNotEmpty ||
              receipt.driverPaymentMethods.isNotEmpty) ...<Widget>[
            Card(
              child: Padding(
                padding: const EdgeInsets.all(AppTheme.space4),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text('付款方式', style: theme.textTheme.titleMedium),
                    const SizedBox(height: AppTheme.space2),
                    if (receipt.paymentPreference.isNotEmpty)
                      DetailRow(
                        label: '乘客偏好',
                        value: _methods(receipt.paymentPreference),
                      ),
                    if (receipt.driverPaymentMethods.isNotEmpty)
                      DetailRow(
                        label: '司機接受',
                        value: _methods(receipt.driverPaymentMethods),
                      ),
                    const SizedBox(height: AppTheme.space2),
                    Text(
                      '付款方式由司機自行聲明，平台不會代為保證。',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: AppTheme.space3),
          ],

          // The document itself. Monospace and selectable: this is the part a
          // passenger forwards, so it is presented as text rather than as more
          // rows to be re-read.
          Card(
            child: Padding(
              padding: const EdgeInsets.all(AppTheme.space4),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text('收據全文', style: theme.textTheme.titleMedium),
                  const SizedBox(height: AppTheme.space3),
                  SelectableText(
                    receipt.text,
                    style: theme.textTheme.bodySmall?.copyWith(
                      fontFamily: 'monospace',
                      height: 1.5,
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: AppTheme.space3),

          Text(
            receipt.disclaimerZh,
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
          Text(
            'Tariff ${receipt.tariffVersion}',
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
        ],
      ),
    );
  }

  /// The driver payout and the platform fee, itemised.
  Widget _fixedFareCard(ThemeData theme, Map<String, dynamic> fixedFare) {
    final Money? driverPrice = asMoneyOrNull(
      fixedFare['driver_price_hkd'],
      'receipt.fixed_fare.driver_price_hkd',
    );
    final Money? platformFee = asMoneyOrNull(
      fixedFare['platform_fee_hkd'],
      'receipt.fixed_fare.platform_fee_hkd',
    );
    final Money? passengerPrice = asMoneyOrNull(
      fixedFare['passenger_price_hkd'],
      'receipt.fixed_fare.passenger_price_hkd',
    );

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.space4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('一口價分帳', style: theme.textTheme.titleMedium),
            const SizedBox(height: AppTheme.space3),
            if (passengerPrice != null)
              DetailRow(label: '乘客應付', valueWidget: MoneyText(passengerPrice)),
            if (driverPrice != null)
              DetailRow(label: '司機收取', valueWidget: MoneyText(driverPrice)),
            if (platformFee != null)
              DetailRow(label: '平台服務費', valueWidget: MoneyText(platformFee)),
            const SizedBox(height: AppTheme.space2),
            Text(
              '服務費為乘客應付與司機收取之差額，已於收據列明。',
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
      ),
    );
  }

  static String _methods(List<String> methods) => methods.join('、');

  /// Area codes are wire values; show the Chinese label the rest of the app uses.
  static String _areaLabel(String code) => const <String, String>{
    'HK_ISLAND': '港島',
    'KOWLOON': '九龍',
    'NT': '新界',
    'AIRPORT': '機場',
    'LANTAU': '大嶼山',
  }[code] ?? code;
}
