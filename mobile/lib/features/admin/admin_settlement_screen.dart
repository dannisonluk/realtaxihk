import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../models/admin.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// The weekly service fee, run by hand.
///
/// The scheduled job fires once at boot then every 7 days; this is the ops lever
/// to re-run a specific ISO week after a failed batch. It is idempotent per week,
/// so re-running a settled period charges nobody twice.
///
/// **Why this is two buttons and not one.** The platform-wide run charges every
/// eligible driver at once, and idempotency-per-week means the first accidental
/// press is not something you get to undo: the money is gone and the reference is
/// spent. The server therefore refuses a run that would charge anyone unless it
/// is handed a token only `POST /settlement/preview` can mint, bound to that
/// preview's period *and* fee. This screen mirrors that: [preview] first, then
/// [run] with the token it returned.
///
/// The preview is also genuinely useful on its own — `wouldGoNegative` is the
/// count of drivers who will be charged into **arrears**. Arrears are legal by
/// design, so that number is a decision, not arithmetic, and it is the reason to
/// look before leaping rather than a formality.
class AdminSettlementScreen extends ConsumerStatefulWidget {
  const AdminSettlementScreen({super.key});

  @override
  ConsumerState<AdminSettlementScreen> createState() => _AdminSettlementScreenState();
}

class _AdminSettlementScreenState extends ConsumerState<AdminSettlementScreen> {
  final TextEditingController _period = TextEditingController();
  SettlementPreview? _previewResult;
  SettlementRun? _result;
  bool _busy = false;

  @override
  void dispose() {
    _period.dispose();
    super.dispose();
  }

  /// The validated period, or `null` for "current week", or `false` if the
  /// operator typed something malformed (in which case the message is shown and
  /// the caller must stop). Returning a sentinel rather than throwing keeps the
  /// two callers readable.
  ///
  /// The server validates `^\d{4}-W\d{2}$`; an empty string means current week.
  String? _validatedPeriod() {
    final String period = _period.text.trim();
    if (period.isEmpty) return null;
    if (!RegExp(r'^\d{4}-W\d{2}$').hasMatch(period)) {
      showInfo(context, '期間格式應為 YYYY-Www，例如 2026-W38');
      return _invalidPeriod;
    }
    return period;
  }

  /// Sentinel returned by [_validatedPeriod] when the text is malformed. A plain
  /// `''` cannot serve, because `''` is the legitimate "current week" spelling.
  static const String _invalidPeriod = '\u0000invalid';

  Future<void> _loadPreview() async {
    final String? period = _validatedPeriod();
    if (period == _invalidPeriod) return;

    setState(() {
      _busy = true;
      // A new preview invalidates the old token and the old result: the token is
      // bound to *this* preview's numbers, so keeping it around would let a stale
      // figure authorise a different charge.
      _previewResult = null;
      _result = null;
    });
    try {
      final SettlementPreview preview =
          await ref.read(adminRepositoryProvider).previewWeeklySettlement(period: period);
      if (mounted) {
        setState(() => _previewResult = preview);
      }
    } on ApiException catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _run() async {
    final SettlementPreview? preview = _previewResult;
    // No preview means no token (or a no-op week, which needs none). Either way
    // this button is only meaningful once a preview exists.
    if (preview == null) return;

    final String? period = _validatedPeriod();
    if (period == _invalidPeriod) return;

    final bool confirmed = await confirmDestructive(
      context,
      title: '確認執行結算',
      message:
          '將對 ${preview.wouldCharge} 位司機收取 ${preview.feeHkd.hkd}，'
          '合計 ${preview.totalChargeHkd.hkd}。'
          '${preview.wouldGoNegative > 0 ? '\n其中 ${preview.wouldGoNegative} 位會結欠（arrears）。' : ''}'
          '\n\n此操作無法撤銷。',
      confirmLabel: '執行',
    );
    if (!confirmed || !mounted) return;

    setState(() => _busy = true);
    try {
      final SettlementRun run = await ref
          .read(adminRepositoryProvider)
          .runWeeklySettlement(period: period, confirmToken: preview.confirmToken);
      if (mounted) {
        setState(() {
          _result = run;
          // The token is single-use in effect: it authorised exactly this run,
          // and the preview it came from no longer describes the world.
          _previewResult = null;
        });
      }
      // A settlement moves real money, so anything cached from before it is
      // stale. The admin's own tabs do not show driver balances, but the driver
      // mode in the same app does, and so does the KYC queue.
      ref.invalidate(driverProfileProvider);
      ref.invalidate(ledgerProvider);
    } on ApiException catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final SettlementPreview? preview = _previewResult;
    final SettlementRun? run = _result;

    return Scaffold(
      appBar: AppBar(title: const Text('每週結算')),
      body: ListView(
        padding: const EdgeInsets.all(AppTheme.space4),
        children: <Widget>[
          Text('手動執行每週服務費', style: theme.textTheme.titleLarge),
          const SizedBox(height: AppTheme.space2),
          Text(
            '系統每 7 天自動執行一次。此處可補跑指定週次；同一週重複執行不會重複收費。\n'
            '先預覽，確認金額後才執行 —— 結算會一次過向所有合資格司機收費。',
            style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
          const SizedBox(height: AppTheme.space6),
          TextField(
            controller: _period,
            decoration: const InputDecoration(labelText: 'ISO 週次（留空為本週）', hintText: '2026-W38'),
            // Editing the period invalidates the preview: its token is bound to
            // the period it was issued for.
            onChanged: (_) {
              if (_previewResult != null || _result != null) {
                setState(() {
                  _previewResult = null;
                  _result = null;
                });
              }
            },
          ),
          const SizedBox(height: AppTheme.space6 - 4),
          FilledButton(
            onPressed: _busy ? null : _loadPreview,
            child: _busy
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('預覽（不會收費）'),
          ),
          if (preview != null) ...<Widget>[
            const SizedBox(height: AppTheme.space6),
            _PreviewCard(preview: preview),
            const SizedBox(height: AppTheme.space4),
            if (preview.isNoOp)
              Text(
                '這一週沒有可收費的司機，執行不會有任何變更。',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              )
            else
              FilledButton.tonal(
                onPressed: _busy ? null : _run,
                style: FilledButton.styleFrom(
                  backgroundColor: theme.colorScheme.errorContainer,
                  foregroundColor: theme.colorScheme.onErrorContainer,
                  minimumSize: const Size.fromHeight(AppTheme.minTarget),
                ),
                child: const Text('執行結算'),
              ),
          ],
          if (run != null) ...<Widget>[
            const SizedBox(height: AppTheme.space6),
            Card(
              color: run.hasAnomaly ? theme.colorScheme.errorContainer : null,
              child: Padding(
                padding: const EdgeInsets.all(AppTheme.space4),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text('結果  ·  ${run.period}', style: theme.textTheme.titleMedium),
                    const SizedBox(height: AppTheme.space3),
                    DetailRow(label: '服務費', valueWidget: MoneyText(run.feeHkd)),
                    DetailRow(label: '合資格司機', value: '${run.eligibleDrivers}'),
                    DetailRow(label: '車隊成員（未收費）', value: '${run.fleetManaged}'),
                    DetailRow(label: '已收費', value: '${run.charged}'),
                    DetailRow(label: '略過（已收過）', value: '${run.skipped}'),
                    DetailRow(label: '失敗', value: '${run.failed}'),
                    DetailRow(label: '帳目異常', value: '${run.tampered}'),
                    if (run.fleetManaged > 0) ...<Widget>[
                      const SizedBox(height: AppTheme.space3),
                      Text(
                        '車隊成員由所屬車隊的結算以折扣價收費，因此不在此次劃一收費之內，'
                        '避免同一週被收費兩次。',
                        style: theme.textTheme.bodySmall?.copyWith(
                          color: theme.colorScheme.onSurfaceVariant,
                        ),
                      ),
                    ],
                    if (run.tampered > 0) ...<Widget>[
                      const SizedBox(height: AppTheme.space3),
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

/// What the run *would* do. Every figure here is a count or an amount, never a
/// driver name — the server sends ids, and a preview is a screenful.
class _PreviewCard extends StatelessWidget {
  const _PreviewCard({required this.preview});

  final SettlementPreview preview;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    // Arrears are legal by design, but they are the one figure here that is a
    // judgement rather than arithmetic, so it gets the warning tint.
    final bool warn = preview.wouldGoNegative > 0 || preview.hasAnomaly;

    return Card(
      color: warn ? theme.colorScheme.errorContainer : null,
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.space4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('預覽  ·  ${preview.period}', style: theme.textTheme.titleMedium),
            const SizedBox(height: AppTheme.space3),
            DetailRow(label: '服務費', valueWidget: MoneyText(preview.feeHkd)),
            DetailRow(label: '合資格司機', value: '${preview.eligibleDrivers}'),
            DetailRow(label: '車隊成員（未收費）', value: '${preview.fleetManaged}'),
            DetailRow(label: '將收費', value: '${preview.wouldCharge}'),
            DetailRow(label: '已收過', value: '${preview.alreadyCharged}'),
            DetailRow(label: '無按金帳戶（略過）', value: '${preview.skippedNoDepositAccount}'),
            DetailRow(label: '合計收取', valueWidget: MoneyText(preview.totalChargeHkd)),
            if (preview.wouldGoNegative > 0)
              DetailRow(label: '將結欠（arrears）', value: '${preview.wouldGoNegative}'),
            if (preview.wouldGoNegative > 0) ...<Widget>[
              const SizedBox(height: AppTheme.space3),
              Text(
                '結欠司機仍會被收費，只是帳戶轉負。依設計結欠是合法的 —— '
                '司機可繼續接單，並在下次充值時清還。此數字是決定，不是算術。',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onErrorContainer,
                ),
              ),
            ],
            if (preview.tampered > 0) ...<Widget>[
              const SizedBox(height: AppTheme.space3),
              Text(
                '有 ${preview.tampered} 筆帳目異常：該週的 ledger reference 已被其他帳目佔用。',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onErrorContainer,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
