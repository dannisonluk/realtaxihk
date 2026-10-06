import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/format/money.dart';
import '../../core/theme/app_theme.dart';
import '../../models/order.dart';
import '../../models/recurring_ride.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';

const List<String> _dayLabels = <String>[
  '星期一',
  '星期二',
  '星期三',
  '星期四',
  '星期五',
  '星期六',
  '星期日',
];

String _dayLabel(int weekday) => _dayLabels[(weekday - 1) % 7];

/// Passenger screen for recurring weekly rides.
///
/// Literature: the server owns scheduling and minting; this screen only keeps
/// templates in sync and lets the passenger pause/resume/cancel. It also
/// accepts an optional `OrderCreateRequest` payload passed from history, so
/// a fresh template can be created here without forcing the user to re-tap the
/// map.
class RecurringRidePrefill {
  const RecurringRidePrefill({
    required this.order,
    required this.sourceOrderId,
  });

  /// Route template from a past trip, when opened from trip history.
  final OrderCreateRequest order;

  /// The past trip's id, kept for audit and server-side lineage.
  final String sourceOrderId;
}

class RecurringRideScreen extends ConsumerStatefulWidget {
  const RecurringRideScreen({super.key, this.prefill, this.sourceOrderId});

  /// Route template from a past trip, when opened from trip history.
  final OrderCreateRequest? prefill;

  /// The past trip the template was built from, when one exists.
  final String? sourceOrderId;

  @override
  ConsumerState<RecurringRideScreen> createState() =>
      _RecurringRideScreenState();
}

class _RecurringRideScreenState extends ConsumerState<RecurringRideScreen> {
  int _weekday = 1;
  TimeOfDay _time = const TimeOfDay(hour: 8, minute: 0);
  bool _saving = false;

  String _scheduledTime() =>
      '${_time.hour.toString().padLeft(2, '0')}:${_time.minute.toString().padLeft(2, '0')}';

  @override
  Widget build(BuildContext context) {
    final AsyncValue<List<RecurringRide>> rides = ref.watch(
      recurringRidesProvider,
    );
    final ThemeData theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(title: const Text('每週重複行程')),
      body: ListView(
        padding: const EdgeInsets.all(AppTheme.space4),
        children: <Widget>[
          if (widget.prefill != null)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(AppTheme.space4),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text('建立新行程', style: theme.textTheme.titleMedium),
                    const SizedBox(height: AppTheme.space2),
                    Text(
                      '由歷史行程預填，每週同一時間叫車。',
                      style: theme.textTheme.bodyMedium?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                    const SizedBox(height: AppTheme.space3),
                    _DayPicker(
                      labels: _dayLabels,
                      selected: _weekday,
                      onChanged: (int day) => setState(() => _weekday = day),
                    ),
                    const SizedBox(height: AppTheme.space3),
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.schedule),
                      title: const Text('叫車時間'),
                      subtitle: Text(_scheduledTime()),
                      onTap: () async {
                        final TimeOfDay? picked = await showTimePicker(
                          context: context,
                          initialTime: _time,
                        );
                        if (picked != null && mounted) {
                          setState(() => _time = picked);
                        }
                      },
                    ),
                    const SizedBox(height: AppTheme.space3),
                    FilledButton.icon(
                      onPressed: _saving ? null : _saveTemplate,
                      icon: _saving
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.add_alarm),
                      label: const Text('建立每週行程'),
                    ),
                  ],
                ),
              ),
            ),
          const SizedBox(height: AppTheme.space4),
          Text('我的定期行程', style: theme.textTheme.titleMedium),
          const SizedBox(height: AppTheme.space2),
          rides.when(
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (Object e, StackTrace st) => Text('載入失敗：$e'),
            data: (List<RecurringRide> items) {
              if (items.isEmpty) {
                return const Text('未有定期行程。');
              }
              return Column(
                children: <Widget>[
                  for (final RecurringRide ride in items)
                    _RideTile(ride: ride, onChanged: _handleStatusChanged),
                ],
              );
            },
          ),
        ],
      ),
    );
  }

  Future<void> _saveTemplate() async {
    setState(() => _saving = true);
    try {
      await ref
          .read(recurringRideRepositoryProvider)
          .create(
            weekday: _weekday,
            scheduledTime: _scheduledTime(),
            orderPayload: widget.prefill!,
            sourceOrderId: widget.sourceOrderId,
          );
      if (!mounted) {
        return;
      }
      ref.invalidate(recurringRidesProvider);
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('已建立每週行程')));
    } on Exception catch (e) {
      if (!mounted) {
        return;
      }
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('建立失敗：$e')));
    } finally {
      if (mounted) {
        setState(() => _saving = false);
      }
    }
  }

  Future<void> _handleStatusChanged(RecurringRide ride, String status) async {
    try {
      await ref
          .read(recurringRideRepositoryProvider)
          .setStatus(ride.id, status);
      if (!mounted) {
        return;
      }
      ref.invalidate(recurringRidesProvider);
    } on Exception catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('更新失敗：$e')));
      }
    }
  }
}

class _DayPicker extends StatelessWidget {
  const _DayPicker({
    required this.labels,
    required this.selected,
    required this.onChanged,
  });

  final List<String> labels;
  final int selected;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: AppTheme.space2,
      runSpacing: AppTheme.space2,
      children: <Widget>[
        for (int i = 0; i < labels.length; i++)
          FilterChip(
            label: Text(labels[i]),
            selected: selected == i + 1,
            onSelected: (bool _) => onChanged(i + 1),
          ),
      ],
    );
  }
}

class _RideTile extends StatelessWidget {
  const _RideTile({required this.ride, required this.onChanged});

  final RecurringRide ride;
  final void Function(RecurringRide ride, String status) onChanged;

  @override
  Widget build(BuildContext context) {
    final bool active = ride.isActive;
    return Card(
      child: ListTile(
        leading: Icon(
          active ? Icons.event_repeat : Icons.pause_circle_outline,
          color: active ? null : Theme.of(context).colorScheme.outline,
        ),
        title: Text(_dayLabel(ride.weekday)),
        subtitle: Text(
          '${ride.scheduledTime}   下次：${Format.dateTime(ride.nextRunAt)}',
        ),
        trailing: TextButton(
          onPressed: () => onChanged(ride, active ? 'PAUSED' : 'ACTIVE'),
          child: Text(active ? '暫停' : '恢復'),
        ),
      ),
    );
  }
}
