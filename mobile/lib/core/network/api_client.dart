import 'dart:async';

import 'package:dio/dio.dart';

import '../config/app_config.dart';
import '../storage/token_store.dart';
import 'api_exception.dart';

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
  Future<String?>? _inFlight;

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

    final String? token = await _refreshOnce();
    if (token == null) {
      onRefreshFailed();
      handler.next(err);
      return;
    }

    request.extra[_kRetried] = true;
    request.headers['Authorization'] = 'Bearer $token';
    try {
      final Response<dynamic> replay = await dio.fetch<dynamic>(request);
      handler.resolve(replay);
    } on DioException catch (e) {
      handler.next(e);
    }
  }

  Future<String?> _refreshOnce() {
    final Future<String?>? existing = _inFlight;
    if (existing != null) {
      return existing;
    }
    final Future<String?> attempt = _refresh();
    _inFlight = attempt;
    return attempt.whenComplete(() {
      _inFlight = null;
    });
  }

  Future<String?> _refresh() async {
    final String? refreshToken = await tokenStore.refreshToken();
    if (refreshToken == null) {
      return null;
    }
    try {
      final Response<dynamic> response = await refreshDio.post<dynamic>(
        '/api/v1/auth/refresh',
        data: <String, dynamic>{'refresh_token': refreshToken},
        options: Options(extra: <String, dynamic>{_kBareClient: true}),
      );
      final Object? body = response.data;
      if (body is! Map<String, dynamic>) {
        return null;
      }
      final Object? access = body['access_token'];
      final Object? rotated = body['refresh_token'];
      if (access is! String || rotated is! String) {
        return null;
      }
      await tokenStore.updateTokens(accessToken: access, refreshToken: rotated);
      return access;
    } on DioException {
      // 401 here means the token was replayed or expired; the server has
      // already revoked everything. 5xx/network means we simply cannot refresh
      // right now — either way the caller has no usable token.
      await tokenStore.clear();
      return null;
    }
  }
}
