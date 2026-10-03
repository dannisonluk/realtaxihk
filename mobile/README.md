# RealTaxi HK — mobile client

Flutter client for the RealTaxi HK backend (`../app`). One app, three roles,
routed by the authenticated account.

| Role | Shell | Entry point |
|---|---|---|
| Passenger | 3 tabs | `lib/features/passenger/` |
| Driver | 3 tabs | `lib/features/driver/` |
| Admin | 4 tabs | `lib/features/admin/` |

Target: Android first (minSdk 24), iOS-compatible source. Flutter 3.44.0 /
Dart 3.12.0.

## Before you run anything

**`flutter` and `dart analyze` do not work on this machine.** The Dart VM cannot
spawn subprocesses that use pipes — the spawn fails with Windows
`ERROR_PIPE_BUSY` (231) at `process_win.cc:742`. Everything that shells out dies
at startup:

```
flutter create / flutter run / flutter test   ->  CreateFile failed 231
dart analyze                                  ->  same
dart run / dart compile                       ->  same (native-assets build hook)
```

What *does* work:

```bash
dart format --line-length 100 lib tool        # formatting
python tool/dart_check.py .                   # type-check (see below)
dart --packages=.dart_tool/package_config.json tool/run_tests.dart
dart --packages=.dart_tool/package_config.json tool/verify_contract.dart
```

The last one bypasses `dartdev` entirely. `dart <file>` goes through `dartdev`,
which is what runs the native-assets build hook for `objective_c` (pulled in
transitively by `flutter_secure_storage_darwin`) and blows up; passing
`--packages` explicitly runs the VM straight on the script.

`tool/dart_check.py` drives the analysis server over LSP from Python, which
*can* spawn it with pipes. It is the same engine `dart analyze` would have used,
with the same `analysis_options.yaml`. Its docstring records the three
non-obvious things about driving it — each of which silently produces "0
diagnostics" rather than an error. Read it before debugging it.

None of the above applies to CI. The `mobile` job in `.github/workflows/ci.yml`
runs on a Linux runner, where the pipe bug does not exist, so it uses the
standard `flutter analyze` rather than the LSP driver. The two agree on the
verdict — same engine, same options file.

`android/` was materialised from the Flutter SDK's own templates
(`packages/flutter_tools/templates/app/`) rather than by `flutter create`, for
the same reason. It is a normal Android project; nothing about it is special.

## Building an APK

**Cannot be done on this machine** — it is the same pipe bug, and it fails
before Gradle is ever reached. `flutter build apk` starts by running
`git log` to check version freshness, which is a subprocess:

```
ProcessPackageException: ProcessException: 所有的管道例項都在使用中。
  Command: ...\git.EXE -c log.showSignature=false log HEAD -n 1 --pretty=format:%ad --date=iso
      at _DefaultProcessUtils.runSync (package:flutter_tools/src/base/process.dart:484)
```

Run it on the host or in CI instead:

```bash
cd mobile
flutter build apk --debug     # or --release, once signing is configured
```

Two things to settle before a release build means anything:

* **The release build type currently signs with the debug keys.**
  `android/app/build.gradle.kts` has
  `signingConfig = signingConfigs.getByName("debug")` and a
  `TODO: Add your own signing config for the release build.` A debug APK is
  fine; a release APK built this way is not shippable.
* **`GOOGLE_MAPS_API_KEY` is absent from `android/local.properties`.** The build
  still succeeds — the manifest placeholder resolves to empty and the map
  surfaces render their labelled placeholder (`AppConfig.mapsConfigured`) — but
  no map will draw.

What *can* be verified here is the Dart source, and all of it passes: the four
commands above (`tool/dart_check.py` reports 58 files / 0 diagnostics),
`tool/run_tests.dart` (97 assertions) and `tool/verify_contract.dart`
(54 fixtures, 0 failures).

## The contract is verified, not assumed

`/openapi.json` **used to** type almost nothing: 28 paths, with all but one
response schema published as `{}`, because every response was a hand-built dict
in `app/api/*`. That has since been fixed — the API now declares a
`response_model=` on all 89 operations and the spec carries 129 schemas (see
`docs/WORK_SUMMARY.md` §2.10) — but the Dart models below were written when the
spec was empty, so they are an *assumption* about the wire format. An assumption
checked only by reading the Python is not checked at all.

Two halves:

```bash
python ../scripts/dev/gen_mobile_fixtures.py      # boots the API, captures real responses
dart --packages=.dart_tool/package_config.json tool/verify_contract.dart
```

The generator writes 54 raw responses to `test/fixtures/`, each with the
endpoint it came from in `manifest.json`. The verifier decodes every one with
the **real** models, and fails if a fixture has no decoder or a decoder has no
fixture — so a new endpoint cannot be added to the generator and quietly go
unverified. Re-run both after any backend change.

Fixtures are committed so the contract is reviewable in a diff. Credential
fields are redacted by the generator before they reach disk.

The two assumptions most likely to be wrong, both of which the verifier pins:

* **`Decimal` serialises as a JSON string**, not a number — `"distance_km":
  "12.5"`. Pydantic v2 does this for every `Decimal`, which is most money and
  distance fields. `json['distance_km'] as double` throws on every real
  response. See `lib/core/network/wire.dart`.
* **The error envelope is `{code, message, details}`**, not FastAPI's default
  `{"detail": ...}`. See `app/core/exceptions.py` and
  `ApiException.fromEnvelope`.

## Backend behaviour the UI has to work around

These are not bugs to fix in the client; they are shapes the client was built
around. Each is documented at the call site.

* **`UserRole.DRIVER` is never assigned.** Signup always creates `PASSENGER`
  (`app/services/otp_service.py`), and `POST /drivers/register` only creates a
  `DriverProfile`. Driver capability is gated entirely on `DriverProfile.status`
  — there are 19 `require_active_user` driver routes and zero role-gated ones.
  So routing keys off admin-vs-not (`lib/router/app_router.dart`), and driver
  mode is entered from the account screen (`lib/features/shared/account_screen.dart`).
* **Trip lifecycle events are never published.** `TripHub.publish()` is only
  reachable from location ticks, so a passive passenger socket never learns that
  a driver grabbed the order. `TripTrackingScreen` therefore polls
  `GET /orders/{id}` alongside the socket.
* **`GET /orders` returns no cursor** while `GET /drivers/me/ledger` does. See
  `OrderPage.nextCursor` and `LedgerPage.nextCursor`.
* **`OrderCreateIn` cannot express a baggage or animal surcharge**, so a quote
  including one is not reproducible in the order snapshot. The request screen
  offers only the fields that survive.
* **Fleet members are excluded from the platform-wide weekly run.** Two billing
  jobs can reach the same driver — `SettlementService.run_weekly` charges every
  ACTIVE driver the flat fee, `FleetSettlementService` charges a fleet's members
  the discounted rate — and they write *different* ledger references
  (`weekly:…` vs `fleet:…`), so idempotency does not protect a driver across
  them. The platform run therefore skips anyone on an active roster and reports
  how many (`fleet_managed`, surfaced on the admin settlement screen). Adding a
  driver to a fleet changes which job bills them from the next settlement, and
  that is the intended behaviour, not a bug to work around.
* **A fleet is created by an admin, never by a driver.** HK fleets are licensed
  operators, so there is no self-service "create my fleet" route and no invite
  flow. The driver-facing surface is read-only.
* **`GET /fleets/*` answers 404, not 403, for someone else's fleet** — so the
  routes cannot be used to enumerate which fleets exist. The client must treat a
  404 there as "not yours", not as "does not exist".
* **A fleet settlement has two response shapes.** The live run
  (`POST …/settlement/run`) returns `gross_fee_hkd` and no `created_at`; a stored
  history row (`GET …/settlement`) returns `created_at` and no `gross_fee_hkd`.
  `FleetSettlementRun` therefore has both nullable, and `discountSaving` is null
  on a history row — the screen hides the line rather than printing `HK$0`.
* **The roster row deliberately omits identity documents.** No HK ID fragment,
  driver's licence number or vehicle registration — an operator needs to know
  *who is on the roster*, not to read back what the driver supplied to the
  platform. `tool/verify_contract.dart` asserts the absence, so adding one to the
  model would fail the check rather than quietly widen what an operator sees.

## Configuration

**Google Maps.** The key is read from `android/local.properties`, which is
git-ignored:

```properties
GOOGLE_MAPS_API_KEY=AIza...
```

`app/build.gradle.kts` feeds it to the manifest placeholder
`${googleMapsApiKey}`. With no key the map surfaces render a labelled
placeholder instead of crashing — see `AppConfig.mapsConfigured` and
`_MapUnavailable` in `lib/features/shared/map_panel.dart`.

**API base URL.** `lib/core/config/app_config.dart` picks
`http://10.0.2.2:8000` on the Android emulator (the host loopback) and
`http://127.0.0.1:8000` elsewhere. Override at build time:

```bash
--dart-define=API_BASE_URL=https://api.example.com
```

Cleartext HTTP is permitted only for `10.0.2.2`, `localhost` and `127.0.0.1`
(`android/app/src/main/res/xml/network_security_config.xml`). Anything else must
be HTTPS — the refresh token is a bearer credential.

## Credentials

The session lives in the platform keystore, never in `SharedPreferences`:
Android Keystore (AES-GCM, RSA-OAEP-wrapped key) and the iOS keychain with
`first_unlock_this_device`. The options are pinned explicitly in
`lib/core/storage/token_store.dart` so a library default change cannot silently
downgrade where the refresh token is stored.

`first_unlock` rather than `unlocked` because the driver's background location
task has to read the token while the screen is locked.

The refresh token **rotates on every use**, and replaying a superseded one makes
the server revoke the entire token family plus every access token the user holds
(`SEC-17`). `ApiClient` therefore refreshes through a single-flight guard, so ten
concurrent 401s produce one refresh rather than ten — nine of which would present
an already-rotated token and log the user out.

## Signing in during development

The backend refuses the dev OTP rail when `APP_ENV=prod`, and the code is never
echoed in a response (`SEC-02`). For local work:

```bash
ALLOW_DEV_OTP=true uvicorn app.main:app --port 8000
```

then log in with any `+852` number and the code `123456`.

## Tests

```bash
dart --packages=.dart_tool/package_config.json tool/run_tests.dart
```

97 assertions over the code with no Flutter dependency: money and date
formatting, the wire decoders, the enums, the error envelope, websocket frames,
pagination, the models (including the fleet shapes), and the router redirect
rules.

Formatting is `dart format --line-length 100` — the flag matters, the repo is
written at 100 columns and the tool defaults to 80, which would reformat every
file in the tree.

Not `package:test`, because neither `flutter test` nor `dart test` can start
here — both go through `dartdev`, which is what trips the pipe bug. `tool/run_tests.dart`
is a self-contained harness that runs on the plain VM. When `flutter test`
works on this machine, that file is the migration list.

The routing rules live in `lib/router/routing_rules.dart` rather than inside
`app_router.dart` specifically so they can be tested without a widget tree. The
redirect is the piece most likely to strand a user on a blank screen — a
signed-in driver bounced to login, an admin bounced into the passenger shell —
so it is worth testing directly rather than by driving the UI.

## Layout

```
lib/
  core/       config, network (client, error envelope, wire decoders),
              storage, formatting, theme, location
  models/     one file per response shape, decoded from the wire
  data/       repositories — one per API area, thin over ApiClient
  state/      Riverpod providers and the auth controller
  router/     go_router configuration and the role redirect
  features/   screens, grouped by role (`passenger/`, `driver/`, `admin/`,
              `fleet/`), plus shared widgets
tool/
  dart_check.py        type-check via LSP (see above)
  run_tests.dart       unit tests for the pure code
  verify_contract.dart decode every fixture with the real models
```

## Known gaps

* No **widget** tests — the harness that would run them (`flutter test`) does not
  work here, and there is no headless alternative without the Flutter tool.
  Everything testable without a widget tree is covered by `tool/run_tests.dart`.
* Push notifications are not wired up; the trip screen polls instead.
