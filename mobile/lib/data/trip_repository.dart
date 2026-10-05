import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

import '../core/config/app_config.dart';
import '../core/network/api_client.dart';
import '../core/network/api_exception.dart';
import '../models/trip.dart';

/// The live-trip channel and its REST fallback.
class TripRepository {
  TripRepository(this._api);

  final ApiClient _api;

  /// `GET /trips/{order_id}/location` — a single indexed read.
  ///
  /// Used on connect, after a socket drop, and on a slow poll while the socket
  /// is down. `lat`/`lng` are null until the driver has reported at least once.
  Future<TripLocationSnapshot> snapshot(String orderId) async {
    final Map<String, dynamic> json = await _api.get('/api/v1/trips/$orderId/location');
    return TripLocationSnapshot.fromJson(json);
  }

  /// Opens `/ws/trip/{order_id}`.
  ///
  /// The access token goes in the query string because a WebSocket handshake has
  /// no header channel in the browser, and the server reads it from there. The
  /// server authorises **before** accepting (`SEC-14`), so a rejected client
  /// never gets a 101 upgrade — it just closes with 4401/4403/4404.
  ///
  /// Callers own reconnection: this method opens a fresh channel and never
  /// retries internally. On a close (or a REST fallback decision) the caller
  /// should call this again rather than expecting the channel to recover.
  Future<TripChannel> openTripChannel(String orderId) async {
    final String? token = await _api.tokenStore.accessToken();
    if (token == null) {
      throw const TripSocketClosed(AppConfig.wsUnauthenticated, 'no access token');
    }
    return TripChannel.connect(orderId: orderId, accessToken: token);
  }
}

/// A live `/ws/trip/{order_id}` socket.
///
/// Protocol summary (`app/api/ws.py`):
/// * a **driver** sends `{"lat":…,"lng":…}` and receives one `{"type":"ack"}`
///   per accepted tick, or `{"type":"error","code":…}`;
/// * a **passenger** is read-only: sending anything earns
///   `{"type":"error","code":"READ_ONLY"}`;
/// * both receive `{"type":"ping"}` on the server's own timer (`SEC-30`) and
///   `{"type":"location"}` ticks — except the driver, whose own ticks are never
///   echoed back.
///
/// The server pings on its own timer and the transport's WebSocket ping reaps
/// genuinely dead peers, so a passenger socket is kept open by server pings
/// alone. There is no app-level idle watchdog that could race a healthy,
/// silent passenger socket (NEW-10).
class TripChannel {
  TripChannel._(this.orderId, this._channel);

  /// Connects without waiting for the handshake to be accepted — the server
  /// authorises first and closes on refusal, so there is nothing to await.
  static TripChannel connect({required String orderId, required String accessToken}) {
    final WebSocketChannel channel = WebSocketChannel.connect(
      AppConfig.tripSocket(orderId: orderId, accessToken: accessToken),
    );
    return TripChannel._(orderId, channel);
  }

  final String orderId;
  final WebSocketChannel _channel;

  /// Decoded events. Decoding failures are dropped rather than killing the
  /// socket — one bad frame should not cost the user their live map.
  Stream<TripEvent> get events async* {
    await for (final dynamic frame in _channel.stream) {
      if (frame is! String) {
        continue;
      }
      Object? decoded;
      try {
        decoded = jsonDecode(frame);
      } on FormatException {
        continue;
      }
      if (decoded is Map<String, dynamic>) {
        yield TripEvent.fromJson(decoded);
      }
    }
  }

  /// The server's application close code, once the socket is finished.
  /// 4401 unauthenticated, 4403 forbidden, 4404 unknown order, 4408 at capacity.
  int? get closeCode => _channel.closeCode;

  String? get closeReason => _channel.closeReason;

  /// Driver only. One tick is a PostGIS UPDATE plus a Redis publish, so the
  /// server allows a small burst (`ws_tick_burst`, default 5) and a sustained
  /// `ws_ticks_per_second` (default 2). Push at
  /// [AppConfig.locationTickInterval] and stay well inside that.
  void pushLocation({required double lat, required double lng}) {
    _channel.sink.add(jsonEncode(<String, double>{'lat': lat, 'lng': lng}));
  }

  /// Closes the socket. Safe to call more than once.
  Future<void> close([int? code, String? reason]) async {
    await _channel.sink.close(code, reason);
  }
}
