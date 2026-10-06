import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';
import '../shared/map_panel.dart';
import '../shared/widgets.dart';

/// Pick a new dropoff for a trip already under way (P4 §4.1 / §6.1).
///
/// Returns the new destination, or `null` if the user backs out. **This does not
/// call the API** — the caller confirms and re-prices, so the "new estimate"
/// dialog can show what the server actually returned rather than a guess.
///
/// There is deliberately no distance field. The client is never told the pickup
/// coordinates (`order_out()` does not expose them), so it cannot compute a
/// routed distance for the *whole* new route — which is what the endpoint's
/// `distance_km` means. Omitting it makes the server fall back to the PostGIS
/// straight line and label the snapshot `straight_line`, which the UI then
/// discloses. Sending a made-up number would be worse than a labelled estimate.
Future<MapPoint?> pickNewDestination(BuildContext context, {required String currentAddress}) {
  return showDialog<MapPoint>(
    context: context,
    builder: (BuildContext context) => _ChangeDestinationDialog(currentAddress: currentAddress),
  );
}

class _ChangeDestinationDialog extends StatefulWidget {
  const _ChangeDestinationDialog({required this.currentAddress});

  final String currentAddress;

  @override
  State<_ChangeDestinationDialog> createState() => _ChangeDestinationDialogState();
}

class _ChangeDestinationDialogState extends State<_ChangeDestinationDialog> {
  final TextEditingController _address = TextEditingController();
  MapPoint? _point;
  String? _error;

  @override
  void dispose() {
    _address.dispose();
    super.dispose();
  }

  void _submit() {
    final MapPoint? point = _point;
    final String address = _address.text.trim();
    if (point == null) {
      setState(() => _error = '請在地圖上點選新目的地');
      return;
    }
    if (address.isEmpty) {
      setState(() => _error = '請填寫新目的地名稱');
      return;
    }
    Navigator.of(context).pop(MapPoint(lat: point.lat, lng: point.lng, label: address));
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final MapPoint? point = _point;

    return Dialog.fullscreen(
      child: Scaffold(
        appBar: AppBar(
          title: const Text('更改目的地'),
          leading: IconButton(
            icon: const Icon(Icons.close),
            tooltip: '返回',
            onPressed: () => Navigator.of(context).pop(),
          ),
        ),
        body: Column(
          children: <Widget>[
            Expanded(
              child: Stack(
                children: <Widget>[
                  Positioned.fill(
                    child: MapPanel(
                      centre: point,
                      markers: <MapPoint>[?point],
                      zoom: 15,
                      onTap: (MapPoint tapped) {
                        setState(() {
                          _point = MapPoint(lat: tapped.lat, lng: tapped.lng, label: '新目的地');
                          _error = null;
                        });
                      },
                    ),
                  ),
                  Positioned(
                    left: 12,
                    right: 12,
                    top: 12,
                    child: Card(
                      child: Padding(
                        padding: const EdgeInsets.all(AppTheme.space3),
                        child: Text(
                          point == null
                              ? '點選地圖設定新的落車位置'
                              : '已選：${point.lat.toStringAsFixed(5)}, ${point.lng.toStringAsFixed(5)}',
                          style: theme.textTheme.bodySmall,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            SafeArea(
              top: false,
              child: Padding(
                padding: const EdgeInsets.all(AppTheme.space4),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    DetailRow(label: '原本目的地', value: widget.currentAddress),
                    const SizedBox(height: AppTheme.space3),
                    TextField(
                      controller: _address,
                      decoration: const InputDecoration(
                        labelText: '新目的地名稱',
                        border: OutlineInputBorder(),
                      ),
                      onChanged: (_) {
                        if (_error != null) {
                          setState(() => _error = null);
                        }
                      },
                    ),
                    if (_error != null)
                      Padding(
                        padding: const EdgeInsets.only(top: AppTheme.space2),
                        child: Text(
                          _error!,
                          style: theme.textTheme.bodySmall?.copyWith(
                            color: theme.colorScheme.error,
                          ),
                        ),
                      ),
                    const SizedBox(height: AppTheme.space3),
                    SizedBox(
                      width: double.infinity,
                      child: FilledButton(onPressed: _submit, child: const Text('下一步：確認新估價')),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
