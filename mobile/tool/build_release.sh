#!/usr/bin/env bash
#
# Build the release APK with Dart-level obfuscation, and split out the symbols.
#
# Why this script exists
# ----------------------
# `flutter build apk --release` on its own shrinks and obfuscates the *Java /
# Kotlin* side (R8), but leaves the Dart AOT image readable. In `libapp.so` the
# Dart symbol names are gone only with `--obfuscate`, and even then the **string
# literals stay in plain text**: API paths, error messages, and the client-side
# business constants (the 150 m arrival radius, the 3-change destination cap) are
# all recoverable from a decompiled `libapp.so`. So obfuscation raises the cost of
# reverse engineering but is **not** a place to hide secrets — nothing in this app
# treats a string literal as a secret.
#
# `--split-debug-info` is what makes an obfuscated build debuggable at all:
# without it a crash stack is a list of addresses. The directory it writes is the
# ONLY way to symbolicate a crash from this build, so it must be archived per
# release — losing it makes that build's crashes permanently unreadable.
#
# This script pins the three flags so nobody has to remember them. Run it from
# anywhere; it re-roots itself at `mobile/`.
set -euo pipefail

cd "$(dirname "$0")/.."

SYMBOLS_DIR="build/symbols"

echo "Building obfuscated release APK (symbols -> ${SYMBOLS_DIR})"
flutter build apk \
  --release \
  --obfuscate \
  --split-debug-info="${SYMBOLS_DIR}"

echo
echo "Built. Archive ${SYMBOLS_DIR}/ alongside this release — without it, crash"
echo "stacks from this build cannot be symbolicated."
