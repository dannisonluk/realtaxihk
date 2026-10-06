# Fresh Mobile Audit — hkfastdc_mobile (realtaxihk/mobile)

Audit based on actual `mobile/lib`, `mobile/test/fixtures`, `mobile/tool`, `pubspec.yaml`, and `mobile/android` source. No prior audit artifacts, deliverables, AGENT/HANDOFF notes, or docs claims were used as evidence. Findings below are source-verifiable from the files read in this audit.

## A. Executive Summary (5 lines)

1. The app is unusually well-structured for a small Flutter codebase: the wire decoders, secure token store, auth/session controller, and contract harness are disciplined, and live-trip screens explicitly cancel subscriptions/timers and re-read the order instead of trusting socket payloads as state.
2. The most important unresolved issue is in the `ApiClient` refresh/interceptor layer: refresh is single-flighted (`_inFlight`), but a failed refresh unconditionally clears the token store and fires `sessionExpired` with no epoch/version guard, so a late failure can race a successful rotation/replay on another request; this is a High-severity finding because it can strand or sign out a session that was still valid.
3. The location flow does distinguish `denied`/`deniedForever`/`serviceDisabled` and offers a settings CTA for the permanent cases, but the ride-request screen has no direct “re-ask” button for a transient `denied` result, and the driver/passenger screens only surface the refusal as a message.
4. The app has a strong theme/accessibility baseline (custom `Semantics`, explicit tooltips, fixed text-scale handling in `DetailRow`), but it ships only Chinese hardcoded strings and no `FlutterGen`/l10n resource bundle, so every screen payload is Chinese-only and the `BrandLogo` English asset branch is dead/contradictory with `app.dart`.
5. Contract tests cover decoders, enums, money, pagination, and trip events, but there are no widget/integration tests exercising the live-trip socket reconnect, location refusal UI, double-submit guard, or token refresh race.

## B. Findings

| ID | Severity | Title | file:line | Evidence | Concrete fix |
|----|----------|-------|-----------|----------|--------------|
| F1 | High | Concurrent refresh / session-expiry race in ApiClient | `lib/core/network/api_client.dart:320-347` | A refresh error fires `_sessionExpired.add(null)` (`api_client.dart:322`); `AuthController` listens and clears the store (`lib/state/auth_controller.dart:29-35`). `_refreshOnce()` single-flights refresh (`api_client.dart:337-347`), but a refresh failure always clears the store (`api_client.dart:369`) and can race a replay/update on another request; there is no version/epoch check before sessionExpired is emitted. | Emit `sessionExpired` only after the refresh future settles, with a version check so a successful rotation from another request is not overwritten by a failed one. Add a regression test with two concurrent 401s. |
| F2 | High | `MalformedResponseException` is not a parse error subtype, so storage/parse paths cannot catch it as a malformed-payload error | `lib/core/network/api_exception.dart:157-164` | The class is a plain `Exception`; `TokenStore.read` only handles `FormatException`/`ArgumentError` (`lib/core/storage/token_store.dart:93-99`), so a malformed stored JSON doesn’t surface through the strict decoder path. | Make `MalformedResponseException` implement `FormatException` (or add a decoder-level type) so storage/parse paths and tests can unify on a catchable malformed-payload type. |
| F3 | High | Stale comment claims “single-flight refresh” but the implementation is the standard single-flight and the risk is the failure path | `lib/core/network/api_client.dart:293-296` and `api_client.dart:349-371` | The comment describes “At most one refresh is in flight” and the code does implement `_inFlight` (`api_client.dart:337-347`), so this is not a reentrant-refresh bug; the real issue is that a refresh failure clears the store immediately (`api_client.dart:369`) even while another request may be replaying, and `sessionExpired` has no epoch/version guard. | Replace the comment with a description of the actual failure/race handling; keep the single-flight, add a refresh-generation token so a stale failure cannot clear a newer successful rotation. |
| F4 | Medium | Location refusal is surfaced, but no in-app retry for a transient denied permission on the request screen | `lib/features/passenger/request_ride_screen.dart:94-115` and `lib/features/shared/widgets.dart:625-640` | `_locate()` shows `showLocationUnavailable` with a settings CTA only for `deniedForever`/`serviceDisabled`; a `denied` result closes with “再按一次即可授權” but the UI offers no button to invoke `ensureAccess()` again without navigating away and back. | Add a “重新授權” action for `LocationAccess.denied` in `showLocationUnavailable`/`_locate` that calls `_locate()` again; keep the existing settings path for the permanent cases. |
| F5 | Low | No test coverage for socket reconnect or location-refusal UI | `lib/features/driver/driver_active_trip_screen.dart:153-167`, `lib/features/passenger/trip_tracking_screen.dart:125-140`, `lib/features/shared/widgets.dart:590-640` | The screens implement bounded reconnect (`[2,5,10,30]`) and settings CTAs, but `tool/run_tests.dart` only exercises pure Dart units; no widget/integration tests exercise these paths. | Add widget tests for: reconnect timer scheduling, non-retryable close codes, and the three location-refusal branches. |
| F6 | Medium | Hardcoded Chinese strings and no app-level i18n | `lib/app.dart:23-33` and `lib/features/shared/widgets.dart:150-188` | `App` sets `supportedLocales: [zh_HK, zh]`, yet all UI copy is literal Chinese (e.g. `'請稍候片刻再試。'` at `widgets.dart:153`). No `.arb`, no `FlutterGen`, no `intl` localization bundle exists under `lib`. | Introduce a `zh_HK`/`zh` `.arb` bundle and `AppLocalizations`; route copy through it before adding any new string. If Chinese-only is intentional, document it and remove the English dead branch below. |
| F7 | Medium | Contradictory bilingual branding logic vs Chinese-only support | `lib/features/shared/widgets.dart:84-88` and `lib/app.dart:23-28` | `BrandLogo._asset` returns `logo-en.webp` when `languageCode == 'en'`; `App` only supports `zh`/`zh_HK`. `logo-en.webp` exists in `assets/branding`. The comment in `app.dart:24-27` says there is “no English resource anywhere in the tree.” Either the asset is dead or the claim is stale. | Either remove the English asset branch and the `logo-en.webp`, or add English locale support and `AppLocalizations` before advertising English. |
| F8 | Medium | User-facing English strings are inconsistent with Chinese-only product copy | `lib/features/shared/widgets.dart:188` | The retry button label is hardcoded `'Try Again'` while the surrounding copy is Chinese. | Move the retry label into the Chinese strings (`請再試`) or into the `AppLocalizations` bundle. |
| F9 | Low | `BrandLogo` semantics label default is English | `lib/features/shared/widgets.dart:71-87` | Default `semanticLabel = 'hkfastdc'`, but the product is Chinese-only and screens using it pass Chinese semantic labels; default is dead/English for most users. | Provide a Chinese default (`香港Call的士`) or require callers to pass it. |
| F10 | Low | No visible “sign in again” recovery after network error on cold start | `lib/state/auth_controller.dart:74-75` and `lib/router/routing_rules.dart:140-144` | On restore offline, `rethrow` keeps tokens but `resolveRedirect` sends the user to `splash` indefinitely with no obvious retry affordance in this source path. | Add a retry button on the splash error view and a timeout/backoff for restore; keep tokens until explicit failure. |
| F11 | Low | No Flutter widget/integration tests for high-risk screens | `tool/run_tests.dart:155-557` | The harness covers pure Dart units (money, format, wire, enums, `ApiException`, trip events, pagination), but no `flutter test` widget tests exist in `test/` for `RequestRideScreen`, `DriverActiveTripScreen`, `TripTrackingScreen`, or `AuthController`. | Add widget tests for: double-tap order submit, location-denied UI, socket reconnect, and auth-expired redirect. |
| F12 | Low | `sessionExpired` broadcast stream is closed only in `ApiClient.close()`, which nothing calls | `lib/core/network/api_client.dart:190-194` | `close()` closes `_sessionExpired`, but as a provider singleton there is no caller of `close()` in the source; the broadcast controller stays open for the app lifetime. | Tie `close()` to a provider `ref.onDispose` or rely on the process lifetime and document it; add a unit test that verifies no listener leak after sign-out. |
| F13 | Low | `_connectAttempts` never resets on success in the passenger screen | `lib/features/passenger/trip_tracking_screen.dart:125-140` | `_connectAttempts` increments on each reconnect and is only used to index `delays`; it is never reset when a connection succeeds, so the backoff stays at the last delay forever even after a successful reconnect. | Reset `_connectAttempts = 0` after a successful `_connect()` (like the driver screen does at `driver_active_trip_screen.dart:102`). |

## C. What Is Already Well Done

- Strict wire decoders in `lib/models/enums.dart`, `lib/models/auth.dart`, `lib/models/order.dart`, `lib/models/trip.dart` with named malformed-payload errors and unknown-event tolerance.
- Secure token storage via `flutter_secure_storage` with Android Keystore options and iOS `first_unlock_this_device`, single JSON key per session to avoid partial writes (`token_store.dart:37-116`).
- `AuthController` correctly keeps cached session on network failure and signs out on auth failure (`auth_controller.dart:45-76`).
- Live-trip screens cancel subscriptions/timers in `dispose`, check `mounted`, and re-read server state on order events instead of mutating from socket payloads (`driver_active_trip_screen.dart:70-90`, `trip_tracking_screen.dart:77-83`).
- Good contract harness (`tool/verify_contract.dart`) covering many pure Dart units.
- Strong theme documentation and consistent use of theme tokens (`app_theme.dart`), 44pt minimum targets, and custom `Semantics` in status pills/empty views (`widgets.dart`).

## D. Model/Contract Drift

- Dart models and fixture payloads are aligned on `OrderDetail`, `FareEstimate`, `TripEvent`, and admin responses; `verify_contract.dart` exercises these with fixture JSON via `tool/run_tests.dart`.
- Enum mirrors: `OrderStatus`, `DriverStatus`, `RefundStatus`, `TaxiType`, `Tunnel`, `TripEvent` decode every known backend token and throw on unknown order/driver status (`enums.dart`, tested in `run_tests.dart:335-410`).
- Date/time: parse helpers tolerate null and reject garbage (`run_tests.dart:326`); `Money` keeps server strings and uses cents (`run_tests.dart:155-231`).
- Nullable fields: wire helpers distinguish null from absent (`run_tests.dart:302-308`); `Order.fromJson` uses nullable parsing for `arrival_confirmed_at`, `completed_at`, etc.
- Potential drift: `mobile/lib/models/order.dart` `OrderDetail` local `disclaimer_en`/`disclaimer_zh` fields exist in `order_detail.json` fixture but are not part of any visible business type; if the backend returns `disclaimer_en` only in a review context this is intentional, but there is no cross-check test against the exact backend schema.
- `OrderCreateRequest` omits baggage/advance booking with a justified comment (`request_ride_screen.dart:16-25`); this is deliberate and worth a contract test that backend `OrderService.create()` really ignores them.

## E. Auth/Session

- Tokens are stored in secure storage with Android Keystore and iOS keychain options; refresh and access tokens are written as a single JSON object (`token_store.dart:65-116`).
- Logout: `AuthRepository.logout` calls `/auth/logout` and clears local store in `AuthController`; it is best-effort server-side (`auth_controller.dart:187-198`). This is correct, but if `/auth/logout` hangs (network), local clear waits for the request; there is no timeout in `ApiClient`.
- Refresh: the interceptor single-flights refresh via `_inFlight` (`api_client.dart:337-347`), but failure still clears the store and emits `sessionExpired` with no epoch/version guard (F1/F3).
- Race: `sessionExpired` listener clears the store and sets state null (`auth_controller.dart:29-35`); a late refresh failure can race a successful rotation, as described above.
- Stale state: `AuthController` restores cached user only to keep the home screen painted; `/auth/me` revalidates on cold start (`auth_controller.dart:40-57`), good.
- No `retry` count/backoff on restore; offline path keeps tokens but may leave splash indefinitely (F10).

## F. Realtime/Location

- `DriverActiveTripScreen` opens a `TripChannel`, subscribes to `TripEvent`, samples a `Position` stream on a 3-second timer, pushes ticks, and cancels timers/subscriptions in `dispose` (`driver_active_trip_screen.dart:50-90,154-237`).
- `TripTrackingScreen` uses a REST poll fallback with `seq` guard (`trip_tracking_screen.dart:206-222`) and cancels in `dispose`.
- Reconnect uses a bounded delay list (`[2,5,10,30]`) with a cap at the last delay (`driver_active_trip_screen.dart:153-167`, `trip_tracking_screen.dart:125-140`); the passenger screen never resets `_connectAttempts` on success (F13).
- Location permission: `LocationService.ensureAccess` distinguishes `granted`/`denied`/`deniedForever`/`serviceDisabled` (`location_service.dart:33-49`) and `showLocationUnavailable` offers a settings CTA for the permanent cases (`widgets.dart:625-640`); the request screen still has no direct re-ask button for a transient `denied` result (F4).
- `Position` is handled only via `current()`; no background location service code exists in `mobile/android` or `lib/core/location` beyond the simple permission/current model, so background ticks are not supported by this source despite the token-store comment about driver background location.
- Memory: timers/subscriptions are disposed in both live screens, but `ApiClient._sessionExpired` and providers are long-lived; no leak observed in the screens themselves.

## G. Async Correctness

- `_locate`, `_quote`, and `_placeOrder` in `request_ride_screen.dart` check `mounted` before `setState`, and the submit button is disabled while `_busy` (`request_ride_screen.dart:595-603`), so a double tap does not launch two orders in the normal path. This is well done.
- The `_placeOrder` `finally` calls `setState` only under `mounted` (`request_ride_screen.dart:296`).
- `_connect` in the live-trip screen checks `_closing` before/after opening a channel (`driver_active_trip_screen.dart:85-99`), good.
- Potential race: `_refresh`/`sessionExpired` interceptor vs token-store writes (F1).
- No async gap in screen state beyond that; `AsyncValueView` correctly handles loading/error/retry.

## H. A11y/Theme/i18n

- Theme uses semantic tokens (`AppTheme.gain/loss/pending/statusLive`, `ColorScheme.fromSeed`) and documents the HK money convention (`app_theme.dart:43-65`).
- Accessibility: `StatusChip` factories provide semantic labels and use `ExcludeSemantics` to avoid double-reading; `DetailRow` increases label width above 1.3x text scale (`widgets.dart:477-500`); `BrandLogo` wraps image in `Semantics(image: true)` (`widgets.dart:92-103`).
- Potential a11y gaps: no text-scale max in screens using fixed-size map panels or bottom sheets; no explicit `Semantics(button: true)` for some custom `InkWell`/`GestureDetector` if any are used outside standard buttons (search showed mostly `IconButton`/`FilledButton`; custom map is interactive via `GestureDetector` and would benefit from an accessibility action).
- i18n: hardcoded Chinese strings throughout screens and shared widgets; no `.arb`/l10n (F6-F9).
- Keyboard/AT: standard Flutter text fields and buttons are used; WebView/Turnstile is not covered by app semantics.

## I. Stale Code/Comments (source-verifiable only)

- `lib/app.dart:24-27` comment claims “no English resource anywhere in the tree,” but `assets/branding/logo-en.webp` exists next to `lib/features/shared/widgets.dart:84-88` which selects it for `en`. (F7)
- `lib/features/shared/widgets.dart:65-67` says “The whole thing is one semantics node” while the code also passes a `semanticLabel`; the implementation does wrap in one `Semantics`, so this is not stale in this case.
- `token_store.dart:54-60` mentions driver background location task; no background location service exists in `mobile/lib`/`mobile/android` source (only foreground screens), so that comment describes a planned/absent capability.

## J. Observable Test Gaps

- No widget/integration tests cover location denied/forever-denied UI, live-trip socket reconnect, order double-submit, auth-expired redirect UX, or refresh-token race.
- `tool/run_tests.dart` exercises pure Dart only; it does not run `flutter test` and cannot catch `setState after dispose` or layout/a11y regressions.
- No test asserts the exact set of fields accepted by `OrderCreateRequest` against backend `OrderCreateIn`.
- No test runs the repo’s fixture files through every model decoder (`tool/verify_contract.dart` is not the same as running `flutter test` and is not imported in `run_tests.dart`).
- No test for `MalformedResponseException` being catchable as a parse error (F2).

## Z. Verification
- Source read: actual Dart source and fixture files; no prior audit material trusted.
- Finder/listing used: `find`, `ls`, `grep`, `read_file`; no full repo grep for all files due to budget, but all sections above are anchored to files read this session.
- Test harness not executed by this audit (not required by task); report is based on source inspection and the `verify_contract`/`run_tests` structure read.