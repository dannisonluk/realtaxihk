import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/format/money.dart';
import '../../core/network/api_exception.dart';
import '../../models/enums.dart';
import '../../models/ledger.dart';
import '../../models/refund.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// The driver's statement and deposit withdrawal.
///
/// Requesting a refund **holds** the whole remaining balance and suspends the
/// driver — it stops dispatch and pauses the weekly service fee. No money moves
/// until an admin approves, and at most one request can be open at a time.
class DriverEarningsScreen extends ConsumerWidget {
  const DriverEarningsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<List<LedgerEntry>> ledger = ref.watch(ledgerProvider);
    final AsyncValue<RefundRequest?> refund = ref.watch(myRefundProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('收入'),
        actions: <Widget>[
          IconButton(
            onPressed: () {
              ref.invalidate(ledgerProvider);
              ref.invalidate(myRefundProvider);
            },
            tooltip: '重新整理',
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncValueView<List<LedgerEntry>>(
        value: ledger,
        onRetry: () => ref.invalidate(ledgerProvider),
        builder: (List<LedgerEntry> entries) => ListView(
          padding: const EdgeInsets.all(16),
          children: <Widget>[
            _BalanceCard(entries: entries),
            const SizedBox(height: 16),
            Text('按金退回', style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 8),
            Card(
              child: AsyncValueView<RefundRequest?>(
                value: refund,
                onRetry: () => ref.invalidate(myRefundProvider),
                builder: (RefundRequest? data) => _RefundSection(refund: data),
              ),
            ),
            const SizedBox(height: 16),
            Text('帳目紀錄', style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 8),
            if (entries.isEmpty)
              const Card(
                child: Padding(
                  padding: EdgeInsets.all(24),
                  child: Center(child: Text('還沒有任何帳目紀錄')),
                ),
              )
            else
              Card(
                child: Column(
                  children: <Widget>[
                    for (int i = 0; i < entries.length; i++) ...<Widget>[
                      _LedgerTile(entry: entries[i]),
                      if (i != entries.length - 1) const Divider(height: 1),
                    ],
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _BalanceCard extends StatelessWidget {
  const _BalanceCard({required this.entries});

  final List<LedgerEntry> entries;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    // The newest entry carries the authoritative running balance the server
    // computed when it was written, so it is not re-derived here.
    final LedgerEntry? latest = entries.isEmpty ? null : entries.first;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              '按金餘額',
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
            const SizedBox(height: 6),
            MoneyText(
              latest?.balanceAfterHkd ?? const Money('0.0'),
              style: theme.textTheme.displaySmall,
            ),
            const SizedBox(height: 8),
            Text(
              '每週服務費會自動由此餘額扣除。',
              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
          ],
        ),
      ),
    );
  }
}

class _RefundSection extends ConsumerStatefulWidget {
  const _RefundSection({required this.refund});

  final RefundRequest? refund;

  @override
  ConsumerState<_RefundSection> createState() => _RefundSectionState();
}

class _RefundSectionState extends ConsumerState<_RefundSection> {
  bool _busy = false;

  Future<void> _request() async {
    final bool confirmed = await confirmDestructive(
      context,
      title: '申請退回按金？',
      message:
          '申請後會凍結整筆按金並暫停接單，每週服務費亦會暫停。'
          '實際退款需要平台管理員批核，批核後帳戶會終止。',
      confirmLabel: '確認申請',
      cancelLabel: '取消',
    );
    if (!confirmed || !mounted) {
      return;
    }

    setState(() => _busy = true);
    try {
      await ref.read(driverRepositoryProvider).requestRefund();
      ref.invalidate(myRefundProvider);
      ref.invalidate(driverProfileProvider);
      if (mounted) {
        showInfo(context, '已提交退款申請');
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
    final RefundRequest? refund = widget.refund;

    if (refund == null) {
      return Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('尚未申請過退款。', style: theme.textTheme.bodyMedium),
            const SizedBox(height: 12),
            OutlinedButton(onPressed: _busy ? null : _request, child: const Text('申請退回按金')),
          ],
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              StatusChip.refund(refund.status, context),
              const Spacer(),
              MoneyText(refund.amountHkd),
            ],
          ),
          const SizedBox(height: 12),
          DetailRow(label: '申請時間', value: _format(refund.createdAt)),
          if (refund.decidedAt != null) DetailRow(label: '批核時間', value: _format(refund.decidedAt)),
          if (refund.note != null && refund.note!.isNotEmpty)
            DetailRow(label: '申請備註', value: refund.note),
          if (refund.decisionNote != null && refund.decisionNote!.isNotEmpty)
            DetailRow(label: '批核備註', value: refund.decisionNote),
          const SizedBox(height: 8),
          Text(switch (refund.status) {
            RefundStatus.pending => '等待平台批核。批核前不會有任何款項變動。',
            RefundStatus.approved => '已批核並完成退款，帳戶已終止。',
            RefundStatus.rejected => '申請被拒絕，按金已解除凍結，帳戶回復啟用。',
          }, style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
        ],
      ),
    );
  }

  static String _format(DateTime? at) => at == null
      ? '—'
      : '${at.year}-${at.month.toString().padLeft(2, '0')}-'
            '${at.day.toString().padLeft(2, '0')} '
            '${at.hour.toString().padLeft(2, '0')}:${at.minute.toString().padLeft(2, '0')}';
}

class _LedgerTile extends StatelessWidget {
  const _LedgerTile({required this.entry});

  final LedgerEntry entry;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return ListTile(
      title: Text(entry.entryType.labelZh),
      subtitle: Text(
        <String>[
          if (entry.note != null && entry.note!.isNotEmpty) entry.note!,
          if (entry.createdAt != null)
            '${entry.createdAt!.year}-${entry.createdAt!.month.toString().padLeft(2, '0')}-'
                '${entry.createdAt!.day.toString().padLeft(2, '0')} '
                '${entry.createdAt!.hour.toString().padLeft(2, '0')}:'
                '${entry.createdAt!.minute.toString().padLeft(2, '0')}',
        ].join('  ·  '),
        style: theme.textTheme.bodySmall,
      ),
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: <Widget>[
          MoneyText(entry.amountHkd, signed: true, style: theme.textTheme.titleSmall),
          Text(
            '餘 ${entry.balanceAfterHkd.display}',
            style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ],
      ),
    );
  }
}
