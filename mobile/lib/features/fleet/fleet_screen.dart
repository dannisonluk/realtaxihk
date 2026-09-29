import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/format/money.dart';
import '../../core/theme/app_theme.dart';
import '../../models/enums.dart';
import '../../models/fleet.dart';
import '../../state/data_providers.dart';
import '../shared/widgets.dart';

/// The driver's own fleet (的士車隊): who else is on the roster, and what the
/// fleet has been charged each week.
///
/// Read-only, and deliberately so. A fleet is a **licensed operator** — the
/// Transport Department grants the licence — so it is created by an admin and
/// drivers are added to a roster. There is nothing here for a driver to create
/// or edit; the screen exists so a member can see the roster they are on and
/// check that their discounted fee matches what the operator told them.
///
/// The fee shown is the *fleet* rate: the platform's flat weekly fee less the
/// fleet's volume discount. A member is charged this **instead of** the flat
/// fee, never both — the platform-wide run skips anyone on an active roster.
class FleetScreen extends ConsumerWidget {
  const FleetScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<MyFleet> mine = ref.watch(myFleetProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('我的車隊'),
        actions: <Widget>[
          IconButton(
            onPressed: () => ref.invalidate(myFleetProvider),
            tooltip: '重新整理',
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncValueView<MyFleet>(
        value: mine,
        onRetry: () => ref.invalidate(myFleetProvider),
        builder: (MyFleet data) {
          final Fleet? fleet = data.fleet;
          if (fleet == null) {
            return const EmptyView(
              icon: Icons.groups_outlined,
              title: '你目前未加入任何車隊',
              subtitle:
                  '車隊由平台管理員為持牌的士營運商開設，加入後由車隊把你列入名單。'
                  '如你所屬車隊尚未在平台上，請聯絡車隊負責人。',
            );
          }
          return _FleetBody(fleet: fleet, membership: data.membership);
        },
      ),
    );
  }
}

class _FleetBody extends ConsumerWidget {
  const _FleetBody({required this.fleet, this.membership});

  final Fleet fleet;
  final FleetMembershipInfo? membership;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ThemeData theme = Theme.of(context);
    final AsyncValue<List<FleetMember>> roster = ref.watch(fleetMembersProvider(fleet.id));
    final AsyncValue<List<FleetSettlementRun>> history = ref.watch(
      fleetSettlementProvider(fleet.id),
    );

    return ListView(
      padding: const EdgeInsets.all(16),
      children: <Widget>[
        _FleetCard(fleet: fleet, membership: membership),
        const SizedBox(height: 16),
        Text('每週車隊收費', style: theme.textTheme.titleSmall),
        const SizedBox(height: 8),
        Text(
          fleet.isFullyDiscounted ? '此車隊每週服務費全免，系統仍會記錄每週結算。' : '車隊成員每週按折扣後的車隊費用收費，不會另外收取平台劃一費用。',
          style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
        ),
        const SizedBox(height: 8),
        Card(
          child: AsyncValueView<List<FleetSettlementRun>>(
            value: history,
            onRetry: () => ref.invalidate(fleetSettlementProvider(fleet.id)),
            builder: (List<FleetSettlementRun> runs) => runs.isEmpty
                ? const Padding(
                    padding: EdgeInsets.all(24),
                    child: Center(child: Text('還沒有結算紀錄')),
                  )
                : Column(
                    children: <Widget>[
                      for (int i = 0; i < runs.length; i++) ...<Widget>[
                        _SettlementTile(run: runs[i]),
                        if (i != runs.length - 1) const Divider(height: 1),
                      ],
                    ],
                  ),
          ),
        ),
        const SizedBox(height: 16),
        Text('車隊成員', style: theme.textTheme.titleSmall),
        const SizedBox(height: 8),
        Card(
          child: AsyncValueView<List<FleetMember>>(
            value: roster,
            onRetry: () => ref.invalidate(fleetMembersProvider(fleet.id)),
            builder: (List<FleetMember> members) => members.isEmpty
                ? const Padding(
                    padding: EdgeInsets.all(24),
                    child: Center(child: Text('名單上沒有其他成員')),
                  )
                : Column(
                    children: <Widget>[
                      for (int i = 0; i < members.length; i++) ...<Widget>[
                        _MemberTile(member: members[i]),
                        if (i != members.length - 1) const Divider(height: 1),
                      ],
                    ],
                  ),
          ),
        ),
      ],
    );
  }
}

class _FleetCard extends StatelessWidget {
  const _FleetCard({required this.fleet, this.membership});

  final Fleet fleet;
  final FleetMembershipInfo? membership;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final FleetMembershipInfo? mine = membership;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(child: Text(fleet.name, style: theme.textTheme.titleLarge)),
                StatusChip(label: fleet.status.labelZh, color: _statusColor(fleet.status, context)),
              ],
            ),
            const SizedBox(height: 12),
            DetailRow(label: '車隊牌照', value: fleet.licenseNo),
            DetailRow(label: '每週費用折扣', value: fleet.discountLabel),
            if (fleet.memberCount != null) DetailRow(label: '成員人數', value: '${fleet.memberCount}'),
            if (mine != null) DetailRow(label: '你的角色', value: mine.memberRole.labelZh),
            if (mine?.joinedAt != null) DetailRow(label: '加入日期', value: _date(mine!.joinedAt)),
            if (fleet.contactName != null && fleet.contactName!.isNotEmpty)
              DetailRow(label: '聯絡人', value: fleet.contactName),
            if (fleet.contactPhone != null && fleet.contactPhone!.isNotEmpty)
              DetailRow(label: '聯絡電話', value: fleet.contactPhone),
            if (!fleet.status.isBillable) ...<Widget>[
              const SizedBox(height: 12),
              Text(
                '此車隊已${fleet.status.labelZh}，暫時不會產生每週收費。',
                style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _SettlementTile extends StatelessWidget {
  const _SettlementTile({required this.run});

  final FleetSettlementRun run;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final Money? saving = run.discountSaving;

    return ListTile(
      title: Text(run.period),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('${run.memberCount} 位成員 · 已收 ${run.charged} 位', style: theme.textTheme.bodySmall),
          if (saving != null && !saving.isZero)
            Text(
              '較平台劃一費用省 ${saving.hkd}',
              style: theme.textTheme.bodySmall?.copyWith(color: AppTheme.gain),
            ),
          if (run.hasAnomaly)
            Text(
              '帳目異常 ${run.tampered} 宗，需要人手核對',
              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
            ),
        ],
      ),
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: <Widget>[
          MoneyText(run.feeHkd, style: theme.textTheme.titleSmall),
          Text(
            '每位 / 週',
            style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ],
      ),
      isThreeLine: true,
    );
  }
}

class _MemberTile extends StatelessWidget {
  const _MemberTile({required this.member});

  final FleetMember member;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return ListTile(
      leading: const Icon(Icons.person_outline),
      title: Text(member.shortId),
      subtitle: Text(
        '${member.taxiType.labelZh}  ·  ${member.memberRole.labelZh}'
        '${member.isBillable ? '' : '  ·  未計費'}',
        style: theme.textTheme.bodySmall,
      ),
      // `loss` is the theme's "good/healthy" green — the same colour
      // `StatusChip.driver` uses for ACTIVE.
      trailing: StatusChip(
        label: member.status.labelZh,
        color: member.isBillable ? AppTheme.loss : theme.colorScheme.onSurfaceVariant,
      ),
    );
  }
}

Color _statusColor(FleetStatus status, BuildContext context) {
  final ColorScheme scheme = Theme.of(context).colorScheme;
  return switch (status) {
    FleetStatus.active => AppTheme.loss,
    FleetStatus.suspended => scheme.error,
    FleetStatus.dissolved => scheme.onSurfaceVariant,
  };
}

String _date(DateTime? at) => at == null ? '—' : '${at.year}-${_two(at.month)}-${_two(at.day)}';

String _two(int value) => value.toString().padLeft(2, '0');
