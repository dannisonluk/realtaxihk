import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../models/enums.dart';
import '../../models/fleet.dart';
import '../../router/app_router.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// The fleet register (的士車隊), for the admin and management teams.
///
/// A fleet is a **licensed operator**: the Transport Department grants the
/// licence, so fleets are created here and never self-service. This screen is
/// therefore the platform-side onboarding step — the operator supplies a name
/// and licence number, and the platform sets the weekly fee discount they are
/// entitled to.
///
/// The discount is the whole commercial point. A member of a fleet is charged
/// the platform's flat weekly fee **less this percentage**, and is excluded from
/// the platform-wide run so they are never charged both. See
/// `AdminFleetDetailScreen` for the roster and settlement levers.
class AdminFleetsScreen extends ConsumerStatefulWidget {
  const AdminFleetsScreen({super.key});

  @override
  ConsumerState<AdminFleetsScreen> createState() => _AdminFleetsScreenState();
}

class _AdminFleetsScreenState extends ConsumerState<AdminFleetsScreen> {
  FleetStatus? _filter;

  Future<void> _create() async {
    final _FleetFormResult? form = await showDialog<_FleetFormResult>(
      context: context,
      builder: (BuildContext context) => const _CreateFleetDialog(),
    );
    if (form == null || !mounted) {
      return;
    }

    try {
      await ref
          .read(fleetRepositoryProvider)
          .createFleet(
            name: form.name,
            licenseNo: form.licenseNo,
            discountPercent: form.discountPercent,
            contactName: form.contactName,
            contactPhone: form.contactPhone,
          );
      ref.invalidate(adminFleetsProvider);
      if (mounted) {
        showInfo(context, '已新增車隊 ${form.name}');
      }
    } on ApiException catch (e) {
      if (mounted) {
        showError(context, e);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final AsyncValue<List<Fleet>> fleets = ref.watch(adminFleetsProvider(_filter));

    return Scaffold(
      appBar: AppBar(
        title: const Text('車隊'),
        actions: <Widget>[
          IconButton(
            onPressed: _create,
            tooltip: '新增車隊',
            icon: const Icon(Icons.add_business_outlined),
          ),
          IconButton(
            onPressed: () => ref.invalidate(adminFleetsProvider),
            tooltip: '重新整理',
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Column(
        children: <Widget>[
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(
              horizontal: AppTheme.space4,
              vertical: AppTheme.space3,
            ),
            child: Row(
              children: <Widget>[
                ChoiceChip(
                  label: const Text('全部'),
                  selected: _filter == null,
                  onSelected: (bool _) => setState(() => _filter = null),
                ),
                for (final FleetStatus status in FleetStatus.values) ...<Widget>[
                  const SizedBox(width: AppTheme.space2),
                  ChoiceChip(
                    label: Text(status.labelZh),
                    selected: _filter == status,
                    onSelected: (bool _) => setState(() => _filter = status),
                  ),
                ],
              ],
            ),
          ),
          Expanded(
            child: AsyncValueView<List<Fleet>>(
              value: fleets,
              onRetry: () => ref.invalidate(adminFleetsProvider),
              builder: (List<Fleet> items) => items.isEmpty
                  ? EmptyView(
                      icon: Icons.groups_outlined,
                      title: _filter == null ? '還沒有任何車隊' : '沒有符合的車隊',
                      subtitle: '車隊由平台為持牌的士營運商開設。',
                      action: FilledButton.tonal(onPressed: _create, child: const Text('新增車隊')),
                    )
                  : ListView.separated(
                      padding: const EdgeInsets.fromLTRB(
                        AppTheme.space4,
                        0,
                        AppTheme.space4,
                        AppTheme.space6,
                      ),
                      itemCount: items.length,
                      separatorBuilder: (BuildContext context, int index) =>
                          const SizedBox(height: AppTheme.space3 - 2),
                      itemBuilder: (BuildContext context, int index) =>
                          _FleetTile(fleet: items[index]),
                    ),
            ),
          ),
        ],
      ),
    );
  }
}

class _FleetTile extends StatelessWidget {
  const _FleetTile({required this.fleet});

  final Fleet fleet;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      child: ListTile(
        title: Text(fleet.name),
        subtitle: Text(
          <String>[
            fleet.licenseNo,
            '折扣 ${fleet.discountLabel}',
            if (fleet.memberCount != null) '${fleet.memberCount} 位成員',
          ].join('  ·  '),
          style: theme.textTheme.bodySmall,
        ),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            StatusChip(label: fleet.status.labelZh, color: _statusColor(fleet.status, context)),
            const Icon(Icons.chevron_right),
          ],
        ),
        onTap: () => context.push('${Routes.adminFleets}/${fleet.id}'),
      ),
    );
  }
}

// --------------------------------------------------------------------------- //
// create
// --------------------------------------------------------------------------- //

class _FleetFormResult {
  const _FleetFormResult({
    required this.name,
    required this.licenseNo,
    required this.discountPercent,
    this.contactName,
    this.contactPhone,
  });

  final String name;
  final String licenseNo;
  final String discountPercent;
  final String? contactName;
  final String? contactPhone;
}

class _CreateFleetDialog extends StatefulWidget {
  const _CreateFleetDialog();

  @override
  State<_CreateFleetDialog> createState() => _CreateFleetDialogState();
}

class _CreateFleetDialogState extends State<_CreateFleetDialog> {
  final TextEditingController _name = TextEditingController();
  final TextEditingController _license = TextEditingController();
  final TextEditingController _discount = TextEditingController(text: '0');
  final TextEditingController _contactName = TextEditingController();
  final TextEditingController _contactPhone = TextEditingController();

  String? _error;

  @override
  void dispose() {
    _name.dispose();
    _license.dispose();
    _discount.dispose();
    _contactName.dispose();
    _contactPhone.dispose();
    super.dispose();
  }

  void _submit() {
    final String name = _name.text.trim();
    final String license = _license.text.trim();
    final String discount = _discount.text.trim().isEmpty ? '0' : _discount.text.trim();

    if (name.isEmpty || license.isEmpty) {
      setState(() => _error = '車隊名稱及牌照號碼為必填。');
      return;
    }
    // The server constrains this to 0–100; checking here turns a 422 into a
    // sentence.
    final double? parsed = double.tryParse(discount);
    if (parsed == null || parsed < 0 || parsed > 100) {
      setState(() => _error = '折扣須為 0 至 100 之間的數字。');
      return;
    }

    Navigator.of(context).pop(
      _FleetFormResult(
        name: name,
        licenseNo: license,
        discountPercent: discount,
        contactName: _contactName.text.trim().isEmpty ? null : _contactName.text.trim(),
        contactPhone: _contactPhone.text.trim().isEmpty ? null : _contactPhone.text.trim(),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('新增車隊'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            TextField(
              controller: _name,
              decoration: const InputDecoration(labelText: '車隊名稱', hintText: '星群的士'),
            ),
            const SizedBox(height: AppTheme.space3),
            TextField(
              controller: _license,
              decoration: const InputDecoration(labelText: '車隊牌照號碼', hintText: '由運輸署發出'),
            ),
            const SizedBox(height: AppTheme.space3),
            TextField(
              controller: _discount,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              decoration: const InputDecoration(
                labelText: '每週費用折扣（%）',
                helperText: '0–100。成員按折扣後的車隊費用收費。',
              ),
            ),
            const SizedBox(height: AppTheme.space3),
            TextField(
              controller: _contactName,
              decoration: const InputDecoration(labelText: '聯絡人（選填）'),
            ),
            const SizedBox(height: AppTheme.space3),
            TextField(
              controller: _contactPhone,
              decoration: const InputDecoration(labelText: '聯絡電話（選填）'),
            ),
            if (_error != null) ...<Widget>[
              const SizedBox(height: AppTheme.space3),
              Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ],
          ],
        ),
      ),
      actions: <Widget>[
        TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('取消')),
        FilledButton(onPressed: _submit, child: const Text('建立')),
      ],
    );
  }
}

Color _statusColor(FleetStatus status, BuildContext context) {
  final ColorScheme scheme = Theme.of(context).colorScheme;
  return switch (status) {
    FleetStatus.active => scheme.primary,
    FleetStatus.suspended => scheme.error,
    FleetStatus.dissolved => scheme.onSurfaceVariant,
  };
}
