import 'dart:async';
import 'dart:io' show X509Certificate;

import 'package:dio/dio.dart';
import 'package:dio/io.dart';
import 'package:flutter/foundation.dart' show debugPrint, kReleaseMode;

import '../config/app_config.dart';
import '../storage/token_store.dart';
import 'api_exception.dart';
import 'cert_pinning.dart';
import 'wire.dart';

/// Request-level flags. `extra` survives a `dio.fetch()` replay, which is what
/// makes the retry-once guard work.
const String _kSkipAuth = 'realtaxi.skip_auth';
const String _kRetried = 'realtaxi.retried';
const String _kBareClient = 'realtaxi.bare';

/// The single HTTP entry point.
///
/// Responsibilities kept here so no repository has to remember them:
/// * attaches the bearer token;
/// * on a 401, refreshes **once** and replays the original request;
/// * maps every failure to [ApiException] using the server's own
///   `{code, message, details}` envelope;
/// * emits [sessionExpired] when the refresh token is gone, so the app can sign
///   the user out from a background failure rather than at the next tap.
class ApiClient {
  ApiClient({required this.tokenStore, String? baseUrl, Dio? httpClient})
    : baseUrl = baseUrl ?? AppConfig.apiBaseUrl,
      _dio =
          httpClient ??
          Dio(
            BaseOptions(
              baseUrl: baseUrl ?? AppConfig.apiBaseUrl,
              connectTimeout: const Duration(seconds: 15),
              receiveTimeout: const Duration(seconds: 30),
              sendTimeout: const Duration(seconds: 30),
              contentType: Headers.jsonContentType,
              responseType: ResponseType.json,
              headers: <String, dynamic>{'Accept': 'application/json'},
            ),
          ) {
    // A second, interceptor-free client for the refresh call itself. Routing
    // `/auth/refresh` through the auth interceptor would recurse on a 401 and
    // make the failure path untestable.
    _refreshDio = Dio(
      BaseOptions(
        baseUrl: baseUrl ?? AppConfig.apiBaseUrl,
        connectTimeout: const Duration(seconds: 15),
        receiveTimeout: const Duration(seconds: 15),
        contentType: Headers.jsonContentType,
        responseType: ResponseType.json,
      ),
    );

    // TLS pinning. The pins are a build-time input; the decision about what an
    // absent value means is a pure function so it can be tested (see
    // `cert_pinning.dart`).
    final Set<String> pins = parsePinnedFingerprints(AppConfig.apiCertSha256);
    final CertPinMode pinMode = resolveCertPinMode(
      pinsConfigured: pins.isNotEmpty,
      releaseMode: kReleaseMode,
    );
    if (pinMode == CertPinMode.misconfigured) {
      // Fail-closed, exactly like `AppConfig.apiBaseUrl` does for a missing
      // `API_BASE_URL`: a release that cannot pin must not connect at all.
      throw StateError(
        'API_CERT_SHA256 must be set with --dart-define in release builds; '
        'refusing to connect without certificate pinning.',
      );
    }
    if (pinMode == CertPinMode.warnOnly) {
      // Logged once per client, not per request.
      debugPrint(
        'certificate pinning disabled: no API_CERT_SHA256 was supplied. '
        'This is allowed in development only.',
      );
    }
    // An injected `httpClient` is a test double and keeps its own adapter.
    if (httpClient == null) {
      _dio.httpClientAdapter = _pinnedAdapter(pins);
    }
    _refreshDio.httpClientAdapter = _pinnedAdapter(pins);

    _dio.interceptors.add(
      _AuthInterceptor(
        dio: _dio,
        refreshDio: _refreshDio,
        tokenStore: tokenStore,
        onRefreshFailed: () => _sessionExpired.add(null),
      ),
    );
  }

  final TokenStore tokenStore;
  final String baseUrl;

  late final Dio _dio;
  late final Dio _refreshDio;

  final StreamController<void> _sessionExpired = StreamController<void>.broadcast();

  /// Fires when the refresh token is rejected or absent — the user must sign in
  /// again. Already broadcast, so multiple listeners are fine.
  Stream<void> get sessionExpired => _sessionExpired.stream;

  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, dynamic>? query,
    bool authenticated = true,
  }) => _send<Map<String, dynamic>>(
    () => _dio.get<dynamic>(
      path,
      queryParameters: query,
      options: Options(extra: <String, dynamic>{_kSkipAuth: !authenticated}),
    ),
  );

  Future<Map<String, dynamic>> post(
    String path, {
    Object? data,
    Map<String, dynamic>? query,
    bool authenticated = true,
  }) => _send<Map<String, dynamic>>(
    () => _dio.post<dynamic>(
      path,
      data: data,
      queryParameters: query,
      options: Options(extra: <String, dynamic>{_kSkipAuth: !authenticated}),
    ),
  );

  /// `PUT` — the full-replace verb used by driver attribute routes.
  ///
  /// Distinct from [patch] because the server treats these as replacement of
  /// the whole set (payment methods / environment flags), not partial updates.
  Future<Map<String, dynamic>> put(
    String path, {
    Object? data,
    Map<String, dynamic>? query,
    bool authenticated = true,
  }) => _send<Map<String, dynamic>>(
    () => _dio.put<dynamic>(
      path,
      data: data,
      queryParameters: query,
      options: Options(extra: <String, dynamic>{_kSkipAuth: !authenticated}),
    ),
  );

  /// `PATCH` — the partial-update verb the admin routes use.
  ///
  /// Distinct from [post] because the server does: `PATCH /admin/fleets/{id}`
  /// only touches the fields present in the body, so sending the full object
  /// would be a different (and destructive) operation.
  Future<Map<String, dynamic>> patch(
    String path, {
    Object? data,
    Map<String, dynamic>? query,
    bool authenticated = true,
  }) => _send<Map<String, dynamic>>(
    () => _dio.patch<dynamic>(
      path,
      data: data,
      queryParameters: query,
      options: Options(extra: <String, dynamic>{_kSkipAuth: !authenticated}),
    ),
  );

  /// `DELETE`. Returns a body, because the routes that use it do: removing a
  /// fleet member answers with the membership row it just marked `REMOVED`
  /// rather than a 204, so the caller can render the new state without a
  /// follow-up read.
  Future<Map<String, dynamic>> delete(
    String path, {
    Object? data,
    Map<String, dynamic>? query,
    bool authenticated = true,
  }) => _send<Map<String, dynamic>>(
    () => _dio.delete<dynamic>(
      path,
      data: data,
      queryParameters: query,
      options: Options(extra: <String, dynamic>{_kSkipAuth: !authenticated}),
    ),
  );

  Future<void> close() async {
    _dio.close(force: true);
    _refreshDio.close(force: true);
    await _sessionExpired.close();
  }

  /// An adapter that pins the leaf certificate's SHA-256 against [pins].
  ///
  /// Dio's `validateCertificate` is the leaf-level hook: it runs on **every**
  /// connection, once the platform has accepted the chain, which is what makes it
  /// real pinning. (`badCertificateCallback`, by contrast, only fires for a
  /// certificate the platform *already* rejected — a valid CA-signed certificate
  /// would never be checked against the pin, which is a false sense of security.)
  ///
  /// With no pins (a development build) every certificate is allowed; the
  /// release-mode refusal happens in the constructor, before this is ever built.
  IOHttpClientAdapter _pinnedAdapter(Set<String> pins) => IOHttpClientAdapter(
    validateCertificate: (X509Certificate? certificate, String host, int port) {
      if (pins.isEmpty) {
        return true;
      }
      if (certificate == null) {
        return false;
      }
      final bool matches = matchesAnyPin(certificate.der, pins);
      if (!matches) {
        final String presented = fingerprintOfDer(certificate.der);
        debugPrint('certificate pin mismatch for $host: SHA-256 $presented');
      }
      return matches;
    },
  );

  /// Runs [call], decodes the body, and normalises every failure.
  Future<T> _send<T>(Future<Response<dynamic>> Function() call) async {
    final Response<dynamic> response;
    try {
      response = await call();
    } on DioException catch (e) {
      throw _toApiException(e);
    }

    final Object? data = response.data;
    if (data is T) {
      return data;
    }
    if (data == null) {
      // Every documented endpoint returns an object; an empty body is a
      // contract change, not an empty success.
      throw MalformedResponseException(
        'expected $T from ${response.requestOptions.path}, got an empty body',
      );
    }
    throw MalformedResponseException(
      'expected $T from ${response.requestOptions.path}, got ${data.runtimeType}',
    );
  }

  ApiException _toApiException(DioException e) {
    final Response<dynamic>? response = e.response;
    final Duration? retryAfter = ApiException.parseRetryAfter(
      response?.headers.value('retry-after'),
    );

    if (response == null) {
      return ApiException(
        code: ApiException.network,
        message: _networkMessage(e),
        retryAfter: retryAfter,
      );
    }

    return ApiException.fromEnvelope(
      response.data,
      statusCode: response.statusCode,
      retryAfter: retryAfter,
    );
  }

  static String _networkMessage(DioException e) => switch (e.type) {
    DioExceptionType.connectionTimeout => 'Cannot reach the server — connection timed out.',
    DioExceptionType.sendTimeout => 'Uploading the request timed out.',
    DioExceptionType.receiveTimeout => 'The server took too long to respond.',
    DioExceptionType.badCertificate => 'The server certificate could not be verified.',
    DioExceptionType.cancel => 'The request was cancelled.',
    _ => 'No connection to the server. Check your network and try again.',
  };
}

/// Attaches the bearer token and performs the single-flight refresh.
class _AuthInterceptor extends Interceptor {
  _AuthInterceptor({
    required this.dio,
    required this.refreshDio,
    required this.tokenStore,
    required this.onRefreshFailed,
  });

  final Dio dio;
  final Dio refreshDio;
  final TokenStore tokenStore;
  final void Function() onRefreshFailed;

  /// At most one refresh is in flight. Without this, ten concurrent 401s would
  /// fire ten refreshes; nine of them would present an already-rotated token and
  /// the server would treat that as replay and revoke every session (`SEC-17`).
  Future<_RefreshOutcome>? _inFlight;

  @override
  Future<void> onRequest(RequestOptions options, RequestInterceptorHandler handler) async {
    if (options.extra[_kSkipAuth] != true) {
      final String? token = await tokenStore.accessToken();
      if (token != null) {
        options.headers['Authorization'] = 'Bearer $token';
      }
    }
    handler.next(options);
  }

  @override
  Future<void> onError(DioException err, ErrorInterceptorHandler handler) async {
    final RequestOptions request = err.requestOptions;
    final bool retried = request.extra[_kRetried] == true;
    final bool bare = request.extra[_kBareClient] == true;

    if (err.response?.statusCode != 401 || retried || bare || request.extra[_kSkipAuth] == true) {
      handler.next(err);
      return;
    }

    final _RefreshOutcome outcome = await _refreshOnce();
    switch (outcome) {
      case _RefreshSuccess(:final accessToken):
        request.extra[_kRetried] = true;
        request.headers['Authorization'] = 'Bearer $accessToken';
        try {
          final Response<dynamic> replay = await dio.fetch<dynamic>(request);
          handler.resolve(replay);
        } on DioException catch (e) {
          handler.next(e);
        }
      case _RefreshFatal():
        onRefreshFailed();
        handler.next(err);
      case _RefreshRetryable():
        // Network/timeout/5xx: the refresh token may still be valid, so keep
        // it and let a later 401 retry the refresh. Signing the user out here
        // would turn a transient outage into a lost session.
        handler.next(err);
    }
  }

  Future<_RefreshOutcome> _refreshOnce() {
    final Future<_RefreshOutcome>? existing = _inFlight;
    if (existing != null) {
      return existing;
    }
    final Future<_RefreshOutcome> attempt = _refresh();
    _inFlight = attempt;
    return attempt.whenComplete(() {
      _inFlight = null;
    });
  }

  Future<_RefreshOutcome> _refresh() async {
    final String? refreshToken = await tokenStore.refreshToken();
    if (refreshToken == null) {
      return const _RefreshFatal();
    }
    try {
      final Response<dynamic> response = await refreshDio.post<dynamic>(
        '/api/v1/auth/refresh',
        data: <String, dynamic>{'refresh_token': refreshToken},
        options: Options(extra: <String, dynamic>{_kBareClient: true}),
      );
      final Map<String, dynamic> body = asMap(response.data, 'refresh.body');
      final String access = asString(body['access_token'], 'refresh.access_token');
      final String rotated = asString(body['refresh_token'], 'refresh.refresh_token');
      await tokenStore.updateTokens(accessToken: access, refreshToken: rotated);
      return _RefreshSuccess(access);
    } on DioException catch (e) {
      // A 401 here means the server replayed or expired the refresh token and
      // has already revoked the whole family; clear locally and sign out.
      if (e.response?.statusCode == 401) {
        await tokenStore.clear();
        return const _RefreshFatal();
      }
      // Network, timeout, 5xx: nothing says the session is dead. Preserve the
      // tokens so the next attempt can rotate instead of forcing a new login.
      return const _RefreshRetryable();
    } on Exception {
      // A malformed success body (e.g. missing `refresh.access_token`) is a
      // contract bug, but letting MalformedResponseException escape the
      // interceptor would surface a raw decode error instead of the 401 the
      // caller was already handling. Keep the tokens; a later attempt retries.
      return const _RefreshRetryable();
    }
  }
}

/// Outcome of an access-token refresh attempt.
///
/// Split into success / fatal / retryable so a transient refresh failure does
/// not destroy a still-valid session.
sealed class _RefreshOutcome {
  const _RefreshOutcome();
}

class _RefreshSuccess extends _RefreshOutcome {
  const _RefreshSuccess(this.accessToken);
  final String accessToken;
}

/// The server explicitly rejected the refresh token (replay or expiry).
class _RefreshFatal extends _RefreshOutcome {
  const _RefreshFatal();
}

/// Network, timeout, or server error: keep the tokens for a later retry.
class _RefreshRetryable extends _RefreshOutcome {
  const _RefreshRetryable();
}
