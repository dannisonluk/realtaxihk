import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../models/driver_booking_preferences.dart';
import '../../models/enums.dart';
import '../../state/data_providers.dart';
import '../../state/providers.dart';
import '../shared/widgets.dart';

/// The driver's standing pre-booking preferences.
///
/// `PUT /drivers/me/booking-preferences` replaces the whole set, so this screen
/// holds local state and writes all selected values on save.
class DriverBookingPreferencesScreen extends ConsumerStatefulWidget {
  const DriverBookingPreferencesScreen({super.key});

  @override
  ConsumerState<DriverBookingPreferencesScreen> createState() =>
      _DriverBookingPreferencesScreenState();
}

class _DriverBookingPreferencesScreenState extends ConsumerState<DriverBookingPreferencesScreen> {
  DriverBookingPreferences? _draft;
  final TextEditingController _originController = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _originController.dispose();
    super.dispose();
  }

  Future<void> _pickTime({required bool from}) async {
    final DriverBookingPreferences draft = _draft!;
    final String value = from ? draft.availableFrom : draft.availableUntil;
    final TimeOfDay? picked = await showTimePicker(
      context: context,
      initialTime: _timeOfDay(value),
      helpText: from ? '可開始時間' : '可接單截止時間',
    );
    if (picked == null || !mounted) {
      return;
    }
    final String wire = DriverBookingPreferences.formatTime(picked.hour, picked.minute);
    setState(() {
      _draft = from ? draft.copyWith(availableFrom: wire) : draft.copyWith(availableUntil: wire);
    });
  }

  TimeOfDay _timeOfDay(String value) {
    final String normalized = DriverBookingPreferences.normalizeTime(value);
    if (normalized.isEmpty) {
      return const TimeOfDay(hour: 6, minute: 0);
    }
    return TimeOfDay(
      hour: int.parse(normalized.substring(0, 2)),
      minute: int.parse(normalized.substring(3, 5)),
    );
  }

  String _timeLabel(String value) {
    if (!DriverBookingPreferences.isValidTime(value)) {
      return '未設定';
    }
    return MaterialLocalizations.of(
      context,
    ).formatTimeOfDay(_timeOfDay(value), alwaysUse24HourFormat: true);
  }

  void _toggleCategory(LandmarkCategory category) {
    final DriverBookingPreferences draft = _draft!;
    final List<LandmarkCategory> categories = <LandmarkCategory>[...draft.categories];
    if (categories.contains(category)) {
      categories.remove(category);
    } else {
      categories.add(category);
    }
    setState(() => _draft = draft.copyWith(categories: categories));
  }

  Future<void> _save() async {
    final DriverBookingPreferences? draft = _draft;
    if (draft == null) {
      return;
    }
    setState(() => _busy = true);
    try {
      await ref
          .read(orderRepositoryProvider)
          .updateDriverBookingPreferences(
            draft.copyWith(preferredOriginArea: _originController.text.trim()),
          );
      ref.invalidate(driverBookingPreferencesProvider);
      if (mounted) {
        showInfo(context, '已更新預約接單設定');
        await Navigator.of(context).maybePop();
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
    final AsyncValue<DriverBookingPreferences> prefs = ref.watch(driverBookingPreferencesProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('預約接單設定')),
      body: AsyncValueView<DriverBookingPreferences>(
        value: prefs,
        onRetry: () => ref.invalidate(driverBookingPreferencesProvider),
        builder: (DriverBookingPreferences loaded) {
          final DriverBookingPreferences draft = _draft ??= loaded;
          if (_originController.text.isEmpty && draft.preferredOriginArea.isNotEmpty) {
            _originController.text = draft.preferredOriginArea;
          }
          return ListView(
            padding: const EdgeInsets.all(AppTheme.space4),
            children: <Widget>[
              GroupedSection(
                title: '預約接單偏好',
                footnote: '只有符合所選類別、上車地區及時間範圍的預約 Call 才會向你顯示。',
                children: <Widget>[
                  Text('類別', style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: AppTheme.space2),
                  SingleChildScrollView(
                    scrollDirection: Axis.horizontal,
                    child: Row(
                      children: <Widget>[
                        for (final LandmarkCategory category in LandmarkCategory.values)
                          Padding(
                            padding: const EdgeInsets.only(right: AppTheme.space2),
                            child: FilterChip(
                              label: Text(category.labelZh),
                              selected: draft.categories.contains(category),
                              onSelected: (_) => _toggleCategory(category),
                            ),
                          ),
                      ],
                    ),
                  ),
                  const SizedBox(height: AppTheme.space4),
                  TextField(
                    controller: _originController,
                    textInputAction: TextInputAction.done,
                    decoration: const InputDecoration(
                      labelText: '首選上車地區',
                      hintText: '例如：中西區',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: AppTheme.space4),
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.play_arrow_outlined),
                    title: const Text('可開始時間'),
                    subtitle: Text(_timeLabel(draft.availableFrom)),
                    trailing: const Icon(Icons.schedule),
                    onTap: () => _pickTime(from: true),
                  ),
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.stop_circle_outlined),
                    title: const Text('可接單截止時間'),
                    subtitle: Text(_timeLabel(draft.availableUntil)),
                    trailing: const Icon(Icons.schedule),
                    onTap: () => _pickTime(from: false),
                  ),
                ],
              ),
              const SizedBox(height: AppTheme.space6),
              FilledButton(
                onPressed: _busy ? null : _save,
                child: _busy
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('儲存'),
              ),
            ],
          );
        },
      ),
    );
  }
}
