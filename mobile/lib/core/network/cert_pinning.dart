/// The pure half of the certificate-pinning seam: fingerprint parsing, the
/// build-mode decision, and the leaf-certificate match.
///
/// **Why this is a separate file from `api_client.dart`.** `tool/run_tests.dart`
/// runs on the plain Dart VM (`flutter test` cannot start on this machine), so
/// anything it imports must not reach `package:flutter` — and `api_client.dart`
/// imports `package:dio` and `AppConfig`, which does. The parts worth asserting
/// on (hex normalisation, a multi-pin set, the empty-pin behaviour) live here,
/// where they can be exercised; the wiring lives next door.
library;

import 'package:crypto/crypto.dart';

/// What a build should do about certificate pinning.
enum CertPinMode {
  /// Pins are present: a leaf certificate whose SHA-256 is not in the set is
  /// refused.
  enforce,

  /// No pins, but a debug/profile build: connections are allowed and a single
  /// warning is logged. Development must not need a production certificate.
  warnOnly,

  /// No pins in a release build. Every connection would be unpinned, so the
  /// client refuses to start rather than pretend it is protected.
  misconfigured,
}

/// The decision, as a pure function so it can be tested without a `Dio`.
///
/// The asymmetry mirrors `resolveTurnstileMode`: **an absent value is tolerated
/// in development and refused in release.** [releaseMode] is passed in rather
/// than read from `kReleaseMode` so this file stays Flutter-free.
CertPinMode resolveCertPinMode({required bool pinsConfigured, required bool releaseMode}) {
  if (pinsConfigured) {
    return CertPinMode.enforce;
  }
  return releaseMode ? CertPinMode.misconfigured : CertPinMode.warnOnly;
}

/// A 32-byte (64 hex character) lowercase fingerprint.
final RegExp _hex64 = RegExp(r'^[0-9a-f]{64}$');

/// Normalises one SHA-256 fingerprint to lowercase hex with no separators.
///
/// Accepts the shapes an operator is likely to paste — `AA:BB:…`, `aa bb …`,
/// `aa-bb-…` or bare hex — and returns null when the result is not a 32-byte
/// fingerprint. Returning null (rather than throwing) lets
/// [parsePinnedFingerprints] drop a typo instead of failing the whole list,
/// which matters because the value arrives from a `--dart-define`.
String? normalizeFingerprint(String raw) {
  final String stripped = raw
      .replaceAll(':', '')
      .replaceAll('-', '')
      .replaceAll(' ', '')
      .toLowerCase();
  if (stripped.length != 64) {
    return null;
  }
  return _hex64.hasMatch(stripped) ? stripped : null;
}

/// Splits the `--dart-define=API_CERT_SHA256=…` value into a set of normalised
/// fingerprints.
///
/// Comma-separated so more than one pin can be supplied during a rotation: the
/// client accepts any of them, so a new certificate can be pinned before the old
/// one is retired. Entries that are not valid fingerprints are dropped — see
/// [normalizeFingerprint].
Set<String> parsePinnedFingerprints(String raw) {
  return raw.split(',').map(normalizeFingerprint).whereType<String>().toSet();
}

/// The SHA-256 fingerprint of a DER-encoded certificate, lowercase hex.
String fingerprintOfDer(List<int> der) => sha256.convert(der).toString();

/// Whether a leaf certificate matches any configured pin.
///
/// An empty set matches nothing: the caller decides what "no pins" means via
/// [resolveCertPinMode], so this stays a pure equality check and never quietly
/// treats an empty set as "allow everything".
bool matchesAnyPin(List<int> der, Set<String> pins) {
  return pins.isNotEmpty && pins.contains(fingerprintOfDer(der));
}
