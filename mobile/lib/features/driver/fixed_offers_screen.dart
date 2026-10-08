import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/format/money.dart';
import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../models/fixed_offer.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// Driver-published fixed-fare offers (一口價).
///
/// A driver names a route (destination area or premium destination, optionally
/// with a pickup area) and the flat price they will do it for. The platform
/// shows the offer to matching passengers and keeps the disclosed service fee
/// as the spread; the price here is what the driver receives.
class FixedOffersScreen extends ConsumerStatefulWidget {
  const FixedOffersScreen({super.key});

  @override
  ConsumerState<FixedOffersScreen> createState() => _FixedOffersScreenState();
}

class _FixedOffersScreenState extends ConsumerState<FixedOffersScreen> {
  bool _busy = false;

  @override
  Widget build(BuildContext context) {
    final AsyncValue<List<FixedOffer>> offers = ref.watch(fixedOffersProvider);
    final ThemeData theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(
        title: const Text('一口價'),
        actions: <Widget>[
          IconButton(
            tooltip: '新增一口價',
            onPressed: _busy ? null : _create,
            icon: const Icon(Icons.add),
          ),
        ],
      ),
      body: offers.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (Object e, StackTrace _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(AppTheme.space4),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text('載入失敗：$e', style: theme.textTheme.bodyMedium),
                const SizedBox(height: AppTheme.space3),
                FilledButton(
                  onPressed: () => ref.invalidate(fixedOffersProvider),
                  child: const Text('重試'),
                ),
              ],
            ),
          ),
        ),
        data: (List<FixedOffer> items) => items.isEmpty
            ? const Center(child: Text('尚未設定一口價'))
            : ListView.separated(
                padding: const EdgeInsets.all(AppTheme.space4),
                itemCount: items.length,
                separatorBuilder: (BuildContext context, int index) =>
                    const SizedBox(height: AppTheme.space3),
                itemBuilder: (BuildContext context, int index) {
                  final FixedOffer offer = items[index];
                  return Card(
                    child: Padding(
                      padding: const EdgeInsets.all(AppTheme.space4),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Row(
                            children: <Widget>[
                              Expanded(
                                child: Text(_routeLabel(offer), style: theme.textTheme.titleMedium),
                              ),
                              const SizedBox(width: AppTheme.space2),
                              MoneyText(offer.priceHkd),
                            ],
                          ),
                          const SizedBox(height: AppTheme.space2),
                          Text(
                            offer.isActive ? '啟用中' : '已暫停',
                            style: theme.textTheme.bodySmall?.copyWith(
                              color: offer.isActive
                                  ? theme.colorScheme.primary
                                  : theme.colorScheme.onSurfaceVariant,
                            ),
                          ),
                          const SizedBox(height: AppTheme.space3),
                          Align(
                            alignment: Alignment.centerLeft,
                            child: OutlinedButton(
                              onPressed: _busy ? null : () => _toggle(offer),
                              child: Text(offer.isActive ? '暫停' : '啟用'),
                            ),
                          ),
                        ],
                      ),
                    ),
                  );
                },
              ),
      ),
    );
  }

  String _routeLabel(FixedOffer offer) {
    final String destination = offer.premiumDestinationId != null
        ? '特選目的地'
        : (offer.destinationArea ?? '所有目的地');
    final String pickup = offer.pickupArea ?? '任何上車區';
    return '$pickup → $destination';
  }

  Future<void> _create() async {
    final TextEditingController price = TextEditingController();
    final String? selectedArea = await showModalBottomSheet<String>(
      context: context,
      builder: (BuildContext context) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(AppTheme.space4),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text('目的地', style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: AppTheme.space2),
              for (final String area in _areas) ...[
                ListTile(title: Text(area), onTap: () => Navigator.pop(context, area)),
              ],
            ],
          ),
        ),
      ),
    );
    if (selectedArea == null) return;

    if (!mounted) return;
    final String? entered = await showDialog<String>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('設定一口價'),
        content: TextField(
          controller: price,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: const InputDecoration(labelText: '司機實收（HK\$）'),
        ),
        actions: <Widget>[
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('取消')),
          FilledButton(
            onPressed: () => Navigator.pop(context, price.text),
            child: const Text('建立'),
          ),
        ],
      ),
    );
    if (entered == null || entered.trim().isEmpty) return;
    price.dispose();

    final Money? amount = Money.tryParse(entered.trim());
    if (amount == null || amount.isNegative || amount.isZero) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('請輸入有效的 HK\$ 一口價（大於 0）')));
      }
      return;
    }

    setState(() => _busy = true);
    try {
      await ref
          .read(driverRepositoryProvider)
          .createFixedOffer(
            FixedOfferCreateRequest(destinationArea: selectedArea, priceHkd: amount),
          );
      ref.invalidate(fixedOffersProvider);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已建立一口價')));
      }
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _toggle(FixedOffer offer) async {
    setState(() => _busy = true);
    try {
      await ref
          .read(driverRepositoryProvider)
          .updateFixedOffer(
            offer.id,
            FixedOfferUpdateRequest(status: offer.isActive ? 'PAUSED' : 'ACTIVE'),
          );
      ref.invalidate(fixedOffersProvider);
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  static const List<String> _areas = <String>['AIRPORT', 'KOWLOON', 'NT', 'HK_ISLAND', 'LANTAU'];
}
