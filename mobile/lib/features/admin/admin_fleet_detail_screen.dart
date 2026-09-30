import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/format/money.dart';
import '../../core/network/api_exception.dart';
import '../../models/admin.dart';
import '../../models/enums.dart';
import '../../models/fleet.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// One fleet: the roster, the settlement lever, and the weekly history.
///
/// Two things here need care.
///
/// **The roster is the billing boundary.** Adding a member takes them off the
/// platform-wide weekly run from the next settlement and puts them on this
/// fleet's discounted rate instead; removing them does the reverse. A driver may
/// be on at most one ACTIVE roster, so a second add is refused by the server
/// rather than silently double-billing.
///
/// **Settlement is idempotent per (fleet, ISO week).** Re-running a week charges
/// nobody twice and updates the stored aggregate instead of appending, so the
/// button is safe to press twice. `tampered` above zero means the ledger
/// reference for that week is held by a different entry and the fee was
/// deliberately *not* collected — that needs a human, not a retry.
class AdminFleetDetailScreen extends ConsumerStatefulWidget {
  const AdminFleetDetailScreen({required this.fleetId, super.key});

  final String fleetId;

  @override
  ConsumerState<AdminFleetDetailScreen> createState() => _AdminFleetDetailScreenState();
}

class _AdminFleetDetailScreenState extends ConsumerState<AdminFleetDetailScreen> {
  final TextEditingController _period = TextEditingController();
  FleetSettlementRun? _lastRun;
  bool _running = false;

  @override
  void dispose() {
    _period.dispose();
    super.dispose();
  }

  void _refreshAll() {
    ref.invalidate(adminFleetsProvider);
    ref.invalidate(fleetMembersProvider(widget.fleetId));
    ref.invalidate(fleetSettlementProvider(widget.fleetId));
  }

  Future<void> _runSettlement() async {
    final String period = _period.text.trim();
    if (period.isNotEmpty && !RegExp(r'^\d{4}-W\d{2}$').hasMatch(period)) {
      showInfo(context, '期間格式應為 YYYY-Www，例如 2026-W38');
      return;
    }

    setState(() => _running = true);
    try {
      final FleetSettlementRun run = await ref
          .read(fleetRepositoryProvider)
          .runSettlement(widget.fleetId, period: period.isEmpty ? null : period);
      if (mounted) {
        setState(() => _lastRun = run);
        _refreshAll();
      }
    } on ApiException catch (e) {
      if (mounted) {
        showError(context, e);
      }
    } finally {
      if (mounted) {
        setState(() => _running = false);
      }
    }
  }

  Future<void> _edit(Fleet fleet) async {
    final _EditFleetResult? result = await showDialog<_EditFleetResult>(
      context: context,
      builder: (BuildContext context) => _EditFleetDialog(fleet: fleet),
    );
    if (result == null || !mounted) {
      return;
    }

    try {
      await ref
          .read(fleetRepositoryProvider)
          .updateFleet(
            fleet.id,
            name: result.name,
            status: result.status,
            discountPercent: result.discountPercent,
          );
      _refreshAll();
      if (mounted) {
        showInfo(context, '已更新車隊設定');
      }
    } on ApiException catch (e) {
      if (mounted) {
        showError(context, e);
      }
    }
  }

  Future<void> _addMember() async {
    final AdminDriverRow? driver = await showDialog<AdminDriverRow>(
      context: context,
      builder: (BuildContext context) => const _PickDriverDialog(),
    );
    if (driver == null || !mounted) {
      return;
    }

    try {
      await ref.read(fleetRepositoryProvider).addMember(widget.fleetId, driverProfileId: driver.id);
      _refreshAll();
      if (mounted) {
        showInfo(context, '已加入名單');
      }
    } on ApiException catch (e) {
      if (mounted) {
        showError(context, e);
      }
    }
  }

  Future<void> _removeMember(FleetMember member) async {
    final bool confirmed = await confirmDestructive(
      context,
      title: '移出車隊名單？',
      message: '${member.shortId} 將由下一次結算起，回復按平台劃一費用收費。紀錄會保留，不會刪除。',
      confirmLabel: '移出',
      cancelLabel: '取消',
    );
    if (!confirmed || !mounted) {
      return;
    }

    try {
      await ref.read(fleetRepositoryProvider).removeMember(widget.fleetId, member.driverProfileId);
      _refreshAll();
      if (mounted) {
        showInfo(context, '已移出車隊名單');
      }
    } on ApiException catch (e) {
      if (mounted) {
        showError(context, e);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final AsyncValue<List<Fleet>> fleets = ref.watch(adminFleetsProvider(null));

    return Scaffold(
      appBar: AppBar(
        title: const Text('車隊詳情'),
        actions: <Widget>[
          IconButton(onPressed: _refreshAll, tooltip: '重新整理', icon: const Icon(Icons.refresh)),
        ],
      ),
      body: AsyncValueView<List<Fleet>>(
        value: fleets,
        onRetry: () => ref.invalidate(adminFleetsProvider),
        builder: (List<Fleet> items) {
          final Fleet? fleet = _findById(items, widget.fleetId);
          if (fleet == null) {
            // The register was refreshed and this fleet is gone from the page —
            // a filter or a page boundary, not a deletion, but either way there
            // is nothing to render.
            return const EmptyView(icon: Icons.search_off, title: '找不到此車隊', subtitle: '請返回列表重新整理。');
          }
          return ListView(
            padding: const EdgeInsets.all(16),
            children: <Widget>[
              _FleetHeader(fleet: fleet, onEdit: () => _edit(fleet)),
              const SizedBox(height: 20),
              Text('每週結算', style: theme.textTheme.titleSmall),
              const SizedBox(height: 8),
              Text(
                '同一週重複執行不會重複收費；已收過的成員會計入「略過」。'
                '車隊必須為營運中才會收費。',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _period,
                decoration: const InputDecoration(labelText: 'ISO 週次（留空為本週）', hintText: '2026-W38'),
              ),
              const SizedBox(height: 12),
              FilledButton(
                onPressed: _running ? null : _runSettlement,
                child: _running
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('執行本週車隊結算'),
              ),
              if (_lastRun != null) ...<Widget>[
                const SizedBox(height: 12),
                _RunResultCard(run: _lastRun!),
              ],
              const SizedBox(height: 20),
              Text('結算紀錄', style: theme.textTheme.titleSmall),
              const SizedBox(height: 8),
              Card(
                child: AsyncValueView<List<FleetSettlementRun>>(
                  value: ref.watch(fleetSettlementProvider(fleet.id)),
                  onRetry: () => ref.invalidate(fleetSettlementProvider(fleet.id)),
                  builder: (List<FleetSettlementRun> runs) => runs.isEmpty
                      ? const Padding(
                          padding: EdgeInsets.all(24),
                          child: Center(child: Text('還沒有結算紀錄')),
                        )
                      : Column(
                          children: <Widget>[
                            for (int i = 0; i < runs.length; i++) ...<Widget>[
                              _HistoryTile(run: runs[i]),
                              if (i != runs.length - 1) const Divider(height: 1),
                            ],
                          ],
                        ),
                ),
              ),
              const SizedBox(height: 20),
              Row(
                children: <Widget>[
                  Text('成員名單', style: theme.textTheme.titleSmall),
                  const Spacer(),
                  TextButton.icon(
                    onPressed: _addMember,
                    icon: const Icon(Icons.person_add_alt),
                    label: const Text('加入成員'),
                  ),
                ],
              ),
              Card(
                child: AsyncValueView<List<FleetMember>>(
                  value: ref.watch(fleetMembersProvider(fleet.id)),
                  onRetry: () => ref.invalidate(fleetMembersProvider(fleet.id)),
                  builder: (List<FleetMember> members) => members.isEmpty
                      ? const Padding(
                          padding: EdgeInsets.all(24),
                          child: Center(child: Text('名單上還沒有成員')),
                        )
                      : Column(
                          children: <Widget>[
                            for (int i = 0; i < members.length; i++) ...<Widget>[
                              _RosterTile(
                                member: members[i],
                                onRemove: () => _removeMember(members[i]),
                              ),
                              if (i != members.length - 1) const Divider(height: 1),
                            ],
                          ],
                        ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  static Fleet? _findById(List<Fleet> fleets, String id) {
    for (final Fleet fleet in fleets) {
      if (fleet.id == id) {
        return fleet;
      }
    }
    return null;
  }
}

class _FleetHeader extends StatelessWidget {
  const _FleetHeader({required this.fleet, required this.onEdit});

  final Fleet fleet;
  final VoidCallback onEdit;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(child: Text(fleet.name, style: theme.textTheme.titleLarge)),
                IconButton(
                  onPressed: onEdit,
                  tooltip: '編輯車隊',
                  icon: const Icon(Icons.edit_outlined),
                ),
              ],
            ),
            const SizedBox(height: 8),
            DetailRow(label: '狀態', value: fleet.status.labelZh),
            DetailRow(label: '車隊牌照', value: fleet.licenseNo),
            DetailRow(label: '每週費用折扣', value: fleet.discountLabel),
            if (fleet.memberCount != null) DetailRow(label: '成員人數', value: '${fleet.memberCount}'),
            if (fleet.contactName != null && fleet.contactName!.isNotEmpty)
              DetailRow(label: '聯絡人', value: fleet.contactName),
            if (fleet.contactPhone != null && fleet.contactPhone!.isNotEmpty)
              DetailRow(label: '聯絡電話', value: fleet.contactPhone),
            if (!fleet.status.isBillable) ...<Widget>[
              const SizedBox(height: 12),
              Text(
                '車隊非營運中，結算會被拒絕。',
                style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _RunResultCard extends StatelessWidget {
  const _RunResultCard({required this.run});

  final FleetSettlementRun run;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Money? gross = run.grossFeeHkd;
    final Money? saving = run.discountSaving;

    return Card(
      color: run.hasAnomaly ? theme.colorScheme.errorContainer : null,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('結果  ·  ${run.period}', style: theme.textTheme.titleMedium),
            const SizedBox(height: 12),
            if (gross != null) DetailRow(label: '平台劃一費用', valueWidget: MoneyText(gross)),
            DetailRow(label: '車隊每位費用', valueWidget: MoneyText(run.feeHkd)),
            if (saving != null && !saving.isZero)
              DetailRow(label: '每位節省', value: '${saving.hkd}  ·  折扣 ${run.discountPercent}%'),
            DetailRow(label: '計費成員', value: '${run.memberCount}'),
            DetailRow(label: '已收費', value: '${run.charged}'),
            DetailRow(label: '略過（已收過）', value: '${run.skipped}'),
            DetailRow(label: '失敗', value: '${run.failed}'),
            DetailRow(label: '帳目異常', value: '${run.tampered}'),
            DetailRow(label: '實收總額', valueWidget: MoneyText(run.collectedHkd)),
            if (run.hasAnomaly) ...<Widget>[
              const SizedBox(height: 12),
              Text(
                '帳目異常代表該週的 ledger reference 被其他帳目佔用，系統刻意未收費，'
                '需要人手核對；重試不會解決。',
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

class _HistoryTile extends StatelessWidget {
  const _HistoryTile({required this.run});

  final FleetSettlementRun run;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return ListTile(
      title: Text(run.period),
      subtitle: Text(
        '${run.memberCount} 位成員 · 已收 ${run.charged} · 略過 ${run.skipped}'
        '${run.tampered > 0 ? '  ·  異常 ${run.tampered}' : ''}',
        style: theme.textTheme.bodySmall,
      ),
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: <Widget>[
          MoneyText(run.collectedHkd, style: theme.textTheme.titleSmall),
          Text(
            '實收',
            style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ],
      ),
    );
  }
}

class _RosterTile extends StatelessWidget {
  const _RosterTile({required this.member, required this.onRemove});

  final FleetMember member;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return ListTile(
      leading: const Icon(Icons.person_outline),
      title: Text(member.shortId),
      subtitle: Text(
        '${member.taxiType.labelZh}  ·  ${member.memberRole.labelZh}  ·  '
        '${member.driverStatus.labelZh}${member.isBillable ? '' : '（未計費）'}',
        style: theme.textTheme.bodySmall,
      ),
      trailing: IconButton(
        onPressed: onRemove,
        tooltip: '移出車隊',
        icon: Icon(Icons.person_remove_outlined, color: theme.colorScheme.error),
      ),
    );
  }
}

// --------------------------------------------------------------------------- //
// edit
// --------------------------------------------------------------------------- //

class _EditFleetResult {
  const _EditFleetResult({required this.name, required this.status, required this.discountPercent});

  final String name;
  final FleetStatus status;
  final String discountPercent;
}

class _EditFleetDialog extends StatefulWidget {
  const _EditFleetDialog({required this.fleet});

  final Fleet fleet;

  @override
  State<_EditFleetDialog> createState() => _EditFleetDialogState();
}

class _EditFleetDialogState extends State<_EditFleetDialog> {
  late final TextEditingController _name = TextEditingController(text: widget.fleet.name);
  late final TextEditingController _discount = TextEditingController(
    text: widget.fleet.weeklyFeeDiscountPercent,
  );
  late FleetStatus _status = widget.fleet.status;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    _discount.dispose();
    super.dispose();
  }

  void _submit() {
    final String name = _name.text.trim();
    final String discount = _discount.text.trim();
    if (name.isEmpty) {
      setState(() => _error = '車隊名稱不可留空。');
      return;
    }
    final double? parsed = double.tryParse(discount);
    if (parsed == null || parsed < 0 || parsed > 100) {
      setState(() => _error = '折扣須為 0 至 100 之間的數字。');
      return;
    }
    Navigator.of(
      context,
    ).pop(_EditFleetResult(name: name, status: _status, discountPercent: discount));
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('編輯車隊'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            TextField(
              controller: _name,
              decoration: const InputDecoration(labelText: '車隊名稱'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _discount,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              decoration: const InputDecoration(labelText: '每週費用折扣（%）', helperText: '由下一次結算起生效。'),
            ),
            const SizedBox(height: 16),
            Align(
              alignment: Alignment.centerLeft,
              child: Text('營運狀態', style: Theme.of(context).textTheme.labelLarge),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              children: <Widget>[
                for (final FleetStatus status in FleetStatus.values)
                  ChoiceChip(
                    label: Text(status.labelZh),
                    selected: _status == status,
                    onSelected: (bool _) => setState(() => _status = status),
                  ),
              ],
            ),
            if (_error != null) ...<Widget>[
              const SizedBox(height: 12),
              Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ],
          ],
        ),
      ),
      actions: <Widget>[
        TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('取消')),
        FilledButton(onPressed: _submit, child: const Text('儲存')),
      ],
    );
  }
}

// --------------------------------------------------------------------------- //
// driver picker
// --------------------------------------------------------------------------- //

/// Picks a driver profile from the KYC register to add to the roster.
///
/// The picker reads the admin driver list, which is the only place the platform
/// exposes driver profile ids to an operator. It is filtered to ACTIVE profiles:
/// a driver still in KYC can be rostered, but they are not billable (there is no
/// deposit account to debit), so offering them here would invite a settlement
/// that silently collects nothing.
class _PickDriverDialog extends ConsumerWidget {
  const _PickDriverDialog();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<Paged<AdminDriverRow>> drivers = ref.watch(
      adminDriversProvider(DriverStatus.active),
    );

    return AlertDialog(
      title: const Text('選擇司機'),
      content: SizedBox(
        width: 420,
        height: 380,
        child: AsyncValueView<Paged<AdminDriverRow>>(
          value: drivers,
          onRetry: () => ref.invalidate(adminDriversProvider),
          builder: (Paged<AdminDriverRow> page) => page.items.isEmpty
              ? const EmptyView(
                  icon: Icons.person_search_outlined,
                  title: '沒有已啟用的司機',
                  subtitle: '司機需先通過審核並繳足按金，才會出現在此清單。',
                )
              : ListView.builder(
                  itemCount: page.items.length,
                  itemBuilder: (BuildContext context, int index) {
                    final AdminDriverRow row = page.items[index];
                    return ListTile(
                      leading: const Icon(Icons.person_outline),
                      title: Text(row.vehicleRegMark),
                      subtitle: Text('${row.taxiType.labelZh}  ·  ${row.taxiDriverPlateNo}'),
                      onTap: () => Navigator.of(context).pop(row),
                    );
                  },
                ),
        ),
      ),
      actions: <Widget>[
        TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('取消')),
      ],
    );
  }
}
