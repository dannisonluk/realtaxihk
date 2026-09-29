import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_exception.dart';
import '../../models/admin.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// The weekly service fee, run by hand.
///
/// The scheduled job fires once at boot then every 7 days; this is the ops lever
/// to re-run a specific ISO week after a failed batch. It is idempotent per week,
/// so re-running a settled period charges nobody twice.
class AdminSettlementScreen extends ConsumerStatefulWidget {
  const AdminSettlementScreen({super.key});

  @override
  ConsumerState<AdminSettlementScreen> createState() => _AdminSettlementScreenState();
}

class _AdminSettlementScreenState extends ConsumerState<AdminSettlementScreen> {
  final TextEditingController _period = TextEditingController();
  SettlementRun? _result;
  bool _busy = false;

  @override
  void dispose() {
    _period.dispose();
    super.dispose();
  }

  Future<void> _run() async {
    final String period = _period.text.trim();
    // The server validates `^\d{4}-W\d{2}$`; an empty string means "current week".
    if (period.isNotEmpty && !RegExp(r'^\d{4}-W\d{2}$').hasMatch(period)) {
      showInfo(context, '期間格式應為 YYYY-Www，例如 2026-W38');
      return;
    }

    setState(() => _busy = true);
    try {
      final SettlementRun run = await ref
          .read(adminRepositoryProvider)
          .runWeeklySettlement(period: period.isEmpty ? null : period);
      if (mounted) {
        setState(() => _result = run);
      }
    } on ApiException catch (e) {
      if (mounted) {
        showError(context, e);
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
    final SettlementRun? run = _result;

    return Scaffold(
      appBar: AppBar(title: const Text('每週結算')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          Text('手動執行每週服務費', style: theme.textTheme.titleLarge),
          const SizedBox(height: 8),
          Text(
            '系統每 7 天自動執行一次。此處可補跑指定週次；同一週重複執行不會重複收費。',
            style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
          const SizedBox(height: 24),
          TextField(
            controller: _period,
            decoration: const InputDecoration(labelText: 'ISO 週次（留空為本週）', hintText: '2026-W38'),
          ),
          const SizedBox(height: 20),
          FilledButton(
            onPressed: _busy ? null : _run,
            child: _busy
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('執行結算'),
          ),
          if (run != null) ...<Widget>[
            const SizedBox(height: 24),
            Card(
              color: run.hasAnomaly ? theme.colorScheme.errorContainer : null,
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text('結果  ·  ${run.period}', style: theme.textTheme.titleMedium),
                    const SizedBox(height: 12),
                    DetailRow(label: '服務費', valueWidget: MoneyText(run.feeHkd)),
                    DetailRow(label: '合資格司機', value: '${run.eligibleDrivers}'),
                    DetailRow(label: '已收費', value: '${run.charged}'),
                    DetailRow(label: '略過（已收過）', value: '${run.skipped}'),
                    DetailRow(label: '失敗', value: '${run.failed}'),
                    DetailRow(label: '帳目異常', value: '${run.tampered}'),
                    if (run.tampered > 0) ...<Widget>[
                      const SizedBox(height: 12),
                      Text(
                        '帳目異常代表該週的 ledger reference 被其他帳目佔用，'
                        '系統刻意未收費，需要人手核對。',
                        style: theme.textTheme.bodySmall?.copyWith(
                          color: theme.colorScheme.onErrorContainer,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}
