import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../models/admin.dart';
import '../../models/enums.dart';
import '../../models/refund.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// The refund queue.
///
/// Approving is the only path in the whole system that moves money **out** of the
/// platform: it writes a `REFUND` ledger entry keyed `refund:{id}`, so a
/// double-tap cannot pay twice, and it terminates the driver. Rejecting releases
/// the hold and returns them to `ACTIVE`.
class AdminRefundsScreen extends ConsumerStatefulWidget {
  const AdminRefundsScreen({super.key});

  @override
  ConsumerState<AdminRefundsScreen> createState() => _AdminRefundsScreenState();
}

class _AdminRefundsScreenState extends ConsumerState<AdminRefundsScreen> {
  RefundStatus? _filter = RefundStatus.pending;
  bool _busy = false;

  Future<void> _decide(RefundRequest refund, bool approve) async {
    final bool confirmed = await confirmDestructive(
      context,
      title: approve ? '批准退款？' : '拒絕退款？',
      message: approve
          ? refund.isPartial
                ? '會即時付出 ${refund.amountHkd.hkd}，解除其餘凍結資金，司機帳戶回復啟用。'
                : '會即時付出 ${refund.amountHkd.hkd}，司機帳戶將終止，無法復原。'
          : '會解除按金凍結，司機帳戶回復啟用。',
      confirmLabel: approve ? '確認批准' : '確認拒絕',
      cancelLabel: '取消',
    );
    if (!confirmed || !mounted) {
      return;
    }

    setState(() => _busy = true);
    try {
      await ref.read(adminRepositoryProvider).decideRefund(refund.id, approve: approve);
      ref.invalidate(adminRefundsProvider);
      if (mounted) {
        showInfo(context, approve ? '已批准退款' : '已拒絕退款');
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
    final AsyncValue<Paged<RefundRequest>> refunds = ref.watch(adminRefundsProvider(_filter));

    return Scaffold(
      appBar: AppBar(
        title: const Text('退款申請'),
        actions: <Widget>[
          IconButton(
            onPressed: () => ref.invalidate(adminRefundsProvider),
            tooltip: '重新整理',
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Column(
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppTheme.space4,
              AppTheme.space3,
              AppTheme.space4,
              AppTheme.space3,
            ),
            child: SegmentedButton<RefundStatus?>(
              segments: const <ButtonSegment<RefundStatus?>>[
                ButtonSegment<RefundStatus?>(value: null, label: Text('全部')),
                ButtonSegment<RefundStatus?>(value: RefundStatus.pending, label: Text('待審批')),
                ButtonSegment<RefundStatus?>(value: RefundStatus.approved, label: Text('已批准')),
                ButtonSegment<RefundStatus?>(value: RefundStatus.rejected, label: Text('已拒絕')),
              ],
              selected: <RefundStatus?>{_filter},
              onSelectionChanged: (Set<RefundStatus?> value) =>
                  setState(() => _filter = value.first),
            ),
          ),
          Expanded(
            child: AsyncValueView<Paged<RefundRequest>>(
              value: refunds,
              onRetry: () => ref.invalidate(adminRefundsProvider),
              builder: (Paged<RefundRequest> page) {
                if (page.items.isEmpty) {
                  return const EmptyView(icon: Icons.inbox_outlined, title: '沒有符合條件的退款申請');
                }
                return RefreshIndicator(
                  onRefresh: () async => ref.invalidate(adminRefundsProvider),
                  child: ListView.separated(
                    padding: const EdgeInsets.fromLTRB(
                      AppTheme.space4,
                      0,
                      AppTheme.space4,
                      AppTheme.space4,
                    ),
                    itemCount: page.items.length,
                    separatorBuilder: (BuildContext context, int index) =>
                        const SizedBox(height: AppTheme.space3 - 2),
                    itemBuilder: (BuildContext context, int index) =>
                        _refundCard(page.items[index]),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _refundCard(RefundRequest refund) {
    final ThemeData theme = Theme.of(context);
    final bool pending = refund.status == RefundStatus.pending;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.space4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                StatusChip.refund(refund.status, context),
                const Spacer(),
                MoneyText(refund.amountHkd, style: theme.textTheme.titleLarge),
              ],
            ),
            const SizedBox(height: AppTheme.space3),
            DetailRow(label: '申請時間', value: _format(refund.createdAt)),
            DetailRow(label: '司機 Profile', value: refund.driverProfileId),
            if (refund.note != null && refund.note!.isNotEmpty)
              DetailRow(label: '申請備註', value: refund.note),
            if (refund.decidedAt != null)
              DetailRow(label: '批核時間', value: _format(refund.decidedAt)),
            if (refund.decisionNote != null && refund.decisionNote!.isNotEmpty)
              DetailRow(label: '批核備註', value: refund.decisionNote),
            if (pending) ...<Widget>[
              const SizedBox(height: AppTheme.space3),
              Row(
                children: <Widget>[
                  Expanded(
                    child: OutlinedButton(
                      onPressed: _busy ? null : () => _decide(refund, false),
                      child: const Text('拒絕'),
                    ),
                  ),
                  const SizedBox(width: AppTheme.space3),
                  Expanded(
                    child: FilledButton(
                      onPressed: _busy ? null : () => _decide(refund, true),
                      child: const Text('批准並付款'),
                    ),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  static String _format(DateTime? at) => at == null
      ? '—'
      : '${at.year}-${at.month.toString().padLeft(2, '0')}-'
            '${at.day.toString().padLeft(2, '0')} '
            '${at.hour.toString().padLeft(2, '0')}:${at.minute.toString().padLeft(2, '0')}';
}
