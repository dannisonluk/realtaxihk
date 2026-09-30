import 'dart:math';

import 'package:flutter/material.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';

import '../../core/config/app_config.dart';
import '../../core/location/location_service.dart';
import '../../core/theme/app_theme.dart';

/// A map marker, or a plain coordinate, in one type.
///
/// Kept free of `LatLng` so callers that never touch a map (the trip history
/// list, a test) can build one without pulling in the plugin.
class MapPoint {
  const MapPoint({required this.lat, required this.lng, required this.label});

  final double lat;
  final double lng;
  final String label;

  LatLng get latLng => LatLng(lat, lng);
}

/// The app's map, with a graceful fallback.
///
/// `google_maps_flutter` needs an API key that is baked into the Android
/// manifest at build time. A build without one renders a grey grid and logs to
/// logcat, which is a miserable thing to debug, so this widget detects the
/// missing key ([AppConfig.mapsConfigured]) and shows the coordinates plus a
/// short explanation instead. The screens above it are unaffected either way.
class MapPanel extends StatefulWidget {
  const MapPanel({
    this.centre,
    this.markers = const <MapPoint>[],
    this.route,
    this.zoom = 14,
    this.follow,
    this.showMyLocation = true,
    this.onTap,
    super.key,
  });

  /// Where to point the camera. Falls back to central Hong Kong.
  final MapPoint? centre;

  final List<MapPoint> markers;

  /// An optional polyline — a straight line between the points. There is no
  /// routing service in this backend, so anything more would be a lie.
  final List<MapPoint>? route;

  final double zoom;

  /// When set, the camera follows this point as it changes (the driver in a live
  /// trip).
  final MapPoint? follow;

  final bool showMyLocation;

  /// Called when the user taps the map. Absent on read-only panels.
  final void Function(MapPoint point)? onTap;

  @override
  State<MapPanel> createState() => _MapPanelState();
}

class _MapPanelState extends State<MapPanel> {
  /// Central Hong Kong — inside the server's `lat 22.1–22.6, lng 113.8–114.5`
  /// bounds, so it is never a position the API would reject.
  static const MapPoint _fallback = MapPoint(lat: 22.3193, lng: 114.1694, label: 'Hong Kong');

  GoogleMapController? _controller;

  @override
  void didUpdateWidget(MapPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    final MapPoint? follow = widget.follow;
    final MapPoint? previous = oldWidget.follow;
    if (follow != null &&
        (previous == null || previous.lat != follow.lat || previous.lng != follow.lng)) {
      _moveTo(follow);
    }
  }

  void _moveTo(MapPoint point) {
    _controller?.animateCamera(CameraUpdate.newLatLngZoom(point.latLng, widget.zoom));
  }

  @override
  Widget build(BuildContext context) {
    if (!AppConfig.mapsConfigured) {
      return _MapUnavailable(centre: widget.centre ?? _fallback, markers: widget.markers);
    }

    final MapPoint centre = widget.centre ?? widget.markers.firstOrNull ?? _fallback;

    return GoogleMap(
      initialCameraPosition: CameraPosition(target: centre.latLng, zoom: widget.zoom),
      onMapCreated: (GoogleMapController controller) => _controller = controller,
      onTap: widget.onTap == null
          ? null
          : (LatLng point) =>
                widget.onTap!(MapPoint(lat: point.latitude, lng: point.longitude, label: 'Pinned')),
      markers: <Marker>{
        for (final MapPoint point in widget.markers)
          Marker(
            markerId: MarkerId('${point.lat},${point.lng},${point.label}'),
            position: point.latLng,
            infoWindow: InfoWindow(title: point.label),
          ),
      },
      polylines: <Polyline>{
        if (widget.route != null && widget.route!.length >= 2)
          Polyline(
            polylineId: const PolylineId('route'),
            points: widget.route!.map((MapPoint p) => p.latLng).toList(growable: false),
            width: 4,
            color: Theme.of(context).colorScheme.primary,
          ),
      },
      myLocationEnabled: widget.showMyLocation,
      myLocationButtonEnabled: widget.showMyLocation,
      compassEnabled: true,
      mapToolbarEnabled: false,
      zoomControlsEnabled: false,
    );
  }
}

/// Shown when no Maps key was supplied at build time.
class _MapUnavailable extends StatelessWidget {
  const _MapUnavailable({required this.centre, required this.markers});

  final MapPoint centre;
  final List<MapPoint> markers;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Container(
      color: theme.colorScheme.surfaceContainerHighest,
      padding: const EdgeInsets.all(AppTheme.space6),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(
              Icons.map_outlined,
              size: 44,
              color: theme.colorScheme.onSurfaceVariant,
            ),
            const SizedBox(height: AppTheme.space3),
            Text('未設定 Google Maps 金鑰', style: theme.textTheme.titleSmall),
            const SizedBox(height: AppTheme.space2),
            Text(
              '以 --dart-define=GOOGLE_MAPS_API_KEY=… 重新建置即可顯示地圖。',
              textAlign: TextAlign.center,
              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
            const SizedBox(height: AppTheme.space4),
            _coord(context, '中心', centre),
            for (final MapPoint marker in markers) _coord(context, marker.label, marker),
          ],
        ),
      ),
    );
  }

  Widget _coord(BuildContext context, String label, MapPoint point) {
    final ThemeData theme = Theme.of(context);
    final bool outside = !LocationService.isInHongKong(point.lat, point.lng);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppTheme.space1 / 2),
      child: Text(
        '$label  ${point.lat.toStringAsFixed(5)}, ${point.lng.toStringAsFixed(5)}'
        '${outside ? '  (香港範圍外，API 會拒絕)' : ''}',
        // Labels are 12pt on Apple's scale; `labelMedium` is that slot, so this
        // stays in step if the scale is ever retuned.
        style: theme.textTheme.labelMedium?.copyWith(
          fontFeatures: const <FontFeature>[FontFeature.tabularFigures()],
          color: outside ? theme.colorScheme.error : null,
        ),
      ),
    );
  }
}

/// Straight-line distance in kilometres, for the fare request.
///
/// This is the same value the client must send as `distance_km` on
/// `POST /orders` — the server does **not** recompute it from the coordinates.
/// A straight line under-reports a real road distance, so the request screen
/// lets the user correct it before the order is placed.
abstract final class GeoMath {
  static const double _earthRadiusKm = 6371.0088;

  /// Haversine. Accurate to well under a metre at Hong Kong's scale, which is
  /// far finer than the straight-line approximation itself.
  static double distanceKm(MapPoint a, MapPoint b) {
    final double dLat = _rad(b.lat - a.lat);
    final double dLng = _rad(b.lng - a.lng);
    final double h =
        pow(sin(dLat / 2), 2) + cos(_rad(a.lat)) * cos(_rad(b.lat)) * pow(sin(dLng / 2), 2);
    return 2 * _earthRadiusKm * asin(sqrt(h.clamp(0.0, 1.0)));
  }

  static double _rad(double degrees) => degrees * pi / 180;
}
