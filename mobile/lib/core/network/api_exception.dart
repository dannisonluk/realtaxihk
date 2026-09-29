import 'dart:async';

/// A failure returned by the API, normalised from the server's error envelope.
///
/// The backend does NOT use FastAPI's default `{"detail": ...}`. Every error is
/// rendered by `app/core/exceptions.py` as:
///
///   { "code": "...", "message": "...", "details": { ... } }
///
/// with `code` drawn from a fixed set. Matching on [code] rather than on the
/// status text is the only stable way to branch on a failure.
class ApiException implements Exception {
  const ApiException({
    required this.code,
    required this.message,
    this.statusCode,
    this.details = const <String, dynamic>{},
    this.retryAfter,
  });

  /// One of the codes below, or `NETWORK` / `MALFORMED` for client-side faults.
  final String code;
  final String message;
  final int? statusCode;
  final Map<String, dynamic> details;

  /// Parsed from `Retry-After` on 429/503. The OTP endpoint sets 600s on the
  /// hard ceiling, so the UI can say how long to wait instead of guessing.
  final Duration? retryAfter;

  // Server-defined codes (app/core/exceptions.py).
  static const String validationError = 'VALIDATION_ERROR';
  static const String businessRule = 'BUSINESS_RULE_VIOLATION';
  static const String badRequest = 'BAD_REQUEST';
  static const String notFound = 'NOT_FOUND';
  static const String unauthorized = 'UNAUTHORIZED';
  static const String forbidden = 'FORBIDDEN';
  static const String conflict = 'CONFLICT';
  static const String payloadTooLarge = 'PAYLOAD_TOO_LARGE';
  static const String rateLimited = 'RATE_LIMITED';
  static const String serviceUnavailable = 'SERVICE_UNAVAILABLE';
  static const String httpError = 'HTTP_ERROR';
  static const String internalError = 'INTERNAL_ERROR';

  // Client-side codes.
  static const String network = 'NETWORK';
  static const String malformed = 'MALFORMED_RESPONSE';

  /// True when retrying the exact same request could plausibly succeed.
  bool get isTransient =>
      code == rateLimited || code == serviceUnavailable || code == internalError || code == network;

  /// True when the session is gone and the user has to sign in again.
  bool get isAuthFailure => statusCode == 401 || code == unauthorized;

  /// Parses the server's `{code, message, details}` envelope.
  ///
  /// This is the single place the envelope is decoded. It lives here rather than
  /// inside [ApiClient] so the contract can be checked directly against captured
  /// responses — see `tool/verify_contract.dart` — instead of only through a
  /// mocked HTTP layer.
  ///
  /// A body that is not an envelope (a proxy's HTML error page, an empty body)
  /// becomes [httpError] rather than [malformed]: the status code is still
  /// meaningful, and a 502 from a load balancer is not a contract change.
  static ApiException fromEnvelope(Object? data, {int? statusCode, Duration? retryAfter}) {
    if (data is Map<String, dynamic>) {
      final Object? code = data['code'];
      final Object? message = data['message'];
      if (code is String && message is String) {
        final Object? details = data['details'];
        return ApiException(
          code: code,
          message: message,
          statusCode: statusCode,
          details: details is Map<String, dynamic> ? details : const <String, dynamic>{},
          retryAfter: retryAfter,
        );
      }
    }
    return ApiException(
      code: httpError,
      message: 'Request failed${statusCode == null ? '' : ' (HTTP $statusCode)'}.',
      statusCode: statusCode,
      retryAfter: retryAfter,
    );
  }

  /// `Retry-After` is sent as whole seconds by the backend; anything else is
  /// ignored rather than guessed at.
  static Duration? parseRetryAfter(String? raw) {
    if (raw == null) {
      return null;
    }
    final int? seconds = int.tryParse(raw.trim());
    return seconds == null ? null : Duration(seconds: seconds);
  }

  @override
  String toString() => 'ApiException($code${statusCode == null ? '' : ' $statusCode'}): $message';
}

/// Thrown when a response body is not the shape the client expects. Kept
/// separate from [ApiException] so a server contract change is loud rather than
/// silently producing nulls.
class MalformedResponseException implements Exception {
  const MalformedResponseException(this.message);

  final String message;

  @override
  String toString() => 'MalformedResponseException: $message';
}

/// Thrown when the trip socket closes with one of the server's application
/// close codes (4401/4403/4404/4408). [retryable] distinguishes a transient
/// capacity rejection from an authoritative refusal.
class TripSocketClosed implements Exception {
  const TripSocketClosed(this.code, this.reason);

  final int code;
  final String reason;

  bool get retryable => code == 4408 || code == 1011 || code == 1001;

  @override
  String toString() => 'TripSocketClosed($code): $reason';
}

/// Convenience for `catch` blocks that want the user-facing text.
extension ApiExceptionMessage on Object {
  String get userMessage {
    final Object self = this;
    if (self is ApiException) {
      return self.message;
    }
    if (self is TripSocketClosed) {
      return self.reason;
    }
    if (self is TimeoutException) {
      return 'The request timed out. Check your connection and try again.';
    }
    return 'Something went wrong. Please try again.';
  }
}
