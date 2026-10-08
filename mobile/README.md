# hkfastdc — mobile client

Flutter client for the hkfastdc backend (`../app`). One app, three roles,
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

`flutter build apk --debug` succeeded on this machine on 2026-10-07 and produced
`build/app/outputs/flutter-apk/app-debug.apk` (about 183 MB, universal
arm64-v8a / armeabi-v7a / x86_64). The older `ERROR_PIPE_BUSY` symptom was
resource exhaustion, not a policy refusal; when it returns, retry once before
blaming the toolchain and use `command flutter` rather than the shell alias.

The debug build proves the Flutter plugin set, `webview_flutter`,
`geolocator_android`, `google_maps_flutter` and the merged Android manifest on
this codebase. It does not prove release signing or a real Google Maps key;
those remain deployment-time decisions.

```bash
cd mobile/android
./gradlew :app:assembleDebug    # → mobile/build/app/outputs/flutter-apk/app-debug.apk
```

One setting does help, and is worth knowing: `FLUTTER_SUPPRESS_ANALYTICS=true`.
The analytics path on Windows runs `cmd.exe /c ver`, and it runs it *while the
tool is already unwinding* — so it replaces the real error with a crash report.
Without the variable, the run above reported only `flutter.bat finished with
non-zero exit value 1` plus a `flutter_02.log`; with it set, the same build named
the two processes that actually failed. That is the difference between a crash
report and a diagnostic.

**CI still adds value for portability.** A Linux CI `flutter build apk --debug`
remains the check that the same plugin/resource set assembles on the reference
runner; the local 2026-10-07 success is evidence, not a replacement for it.

Two things to settle before a release build means anything:

* **Release signing is wired up, but there is no keystore yet.**
  `android/app/build.gradle.kts` reads `android/key.properties` (gitignored) and
  creates a `release` signing config from it. While that file is absent a release
  build **fails** rather than falling back to the debug keys, because a
  debug-signed APK cannot be uploaded and cannot update a published build — the
  signature would not match, and a build that "succeeds" while producing one is
  worse than no build. A debug-only build (`assembleDebug`) still runs without a
  keystore; only the release path is refused. The file it expects:

  ```properties
  storeFile=/path/to/upload-keystore.jks
  storePassword=…
  keyAlias=upload
  keyPassword=…
  ```

  Generate the keystore once (keep it out of the repo):

  ```bash
  keytool -genkey -v -keystore upload-keystore.jks -keyalg RSA \
    -keysize 2048 -validity 10000 -alias upload
  ```

  Verified with a throwaway keystore: `./gradlew :app:signingReport` then reports
  `Variant: release → Config: release → Store: …/upload-keystore.jks → Alias: upload`.
  `storeFile` is resolved against `mobile/android/`, so a relative path is fine.
* **`GOOGLE_MAPS_API_KEY` is absent from `android/local.properties`.** The build
  still succeeds — the manifest placeholder resolves to empty and the map
  surfaces render their labelled placeholder (`AppConfig.mapsConfigured`) — but
  no map will draw.

### Release hardening

Three things make the release artifact different from the debug one, and all
three are stated in the build files rather than left to a default:

* **R8 (shrink + obfuscate) and resource shrinking** are set explicitly in the
  `release` build type (`isMinifyEnabled` / `isShrinkResources` /
  `proguardFiles`). Flutter 3.44's Gradle plugin already enables them — but only
  while `shouldShrinkResources` is true, which `-Pshrink=false` turns off. Writing
  them in `android/app/build.gradle.kts` means a release is shrunk however Gradle
  is invoked. The app-level keeps live in `android/app/proguard-rules.pro`, each
  with the reason it exists.
* **The Dart side is obfuscated by `tool/build_release.sh`**, which pins
  `flutter build apk --release --obfuscate --split-debug-info=build/symbols`. R8
  only covers the Java/Kotlin side; without `--obfuscate` every Dart class, symbol
  and string literal in `libapp.so` is readable. Note the limit: `--obfuscate`
  renames symbols but **keeps string literals in plain text**, so it is not a
  place to hide secrets — nothing in this app treats a string literal as one.
* **The symbol files must be archived.** `--split-debug-info=build/symbols` writes
  the mapping that turns an obfuscated crash stack back into named frames. It is
  the only copy: lose it and that build's crashes can never be symbolicated.
  Archive `build/symbols/` alongside each release, the way the keystore is kept.

The command, from `mobile/`:

```bash
tool/build_release.sh
```

### One trap in `android/app/build.gradle.kts`

`Properties` is **imported** rather than written as `java.util.Properties`. In a
*project* script the `java` extension accessor (`JavaPluginExtension`,
contributed by AGP) shadows the `java` package name, so the qualified form does
not compile:

```
e: app/build.gradle.kts:23:31: Unresolved reference 'util'.
e: app/build.gradle.kts:26:32: Cannot infer type for this parameter. Specify it explicitly.
```

The second message is a knock-on — `properties` never acquired a type. The
identical expression in `settings.gradle.kts` is fine because a `Settings` script
has no `java` accessor, which is why the Flutter template gets away with it
there.

**Do not read Gradle's script-compilation footer as a failure count.** It reports
these two errors plus the `android { }` deprecation and prints "3 errors". Only
two are errors: AGP 9 deprecates the old DSL, but Flutter's template pins
`android.newDsl=false`, so the old `android { }` block is still the supported
path and the notice is a warning. The footer counts warnings and errors together.

The Dart source is verified too: the four commands above. As of **2026-10-06** the
numbers are `tool/dart_check.py` → **84 files opened, 0 with diagnostics**;
`tool/run_tests.dart` → **153 cases, 0 failed**; `tool/verify_contract.dart`
→ **64 fixtures decoded, 0 failure** (65 fixture files on disk).

What none of the four can see: `res/`, the `assets:` block, and the Flutter plugin
set. The Dart analyzer does not read Android resources, and the two scripts never
load an image or a Gradle project. A Gradle build is the only thing that resolves
them — which is why `./gradlew :app:assembleDebug` is a gate rather than just a
packaging step, and why adding a plugin (`webview_flutter`, for the Turnstile
challenge) is verified there and nowhere else. Since that gate cannot run here,
`webview_flutter` is currently **resolved but not proven**.

There is a second, quieter trap in the same area. `.flutter-plugins-dependencies`
is what the Flutter Gradle plugin reads to decide which plugin subprojects to
include, and **only `flutter pub get` regenerates it — `dart pub get` does not.**
The file is gitignored, and after adding `webview_flutter` it still lists no
`webview_flutter_android`, so a Gradle build on this machine would produce an APK
with no WebView implementation inside it: a failure that surfaces at runtime, on
the login screen, rather than at build time. CI is fine (its `flutter pub get`
writes the file), but if you build locally after touching `pubspec.yaml`, check
that file before believing the APK.

## Branding

The artwork is two 2048x2048 posters in `branding/source/` (`logo-zh.jfif`,
`logo-en.jfif`) with the wordmark **baked into the pixels**. There is no vector
mark to compose from, so every size is derived:

```bash
python tool/gen_branding_assets.py           # writes everything below
python tool/gen_branding_assets.py --check   # verifies, writes nothing
```

| Output | Where | Why that size |
|---|---|---|
| In-app poster | `assets/branding/logo-{zh,en}.webp`, 1024px | `BrandLogo` renders it at 160-240dp, so 1024 is ~4x headroom |
| Legacy launcher icon | `mipmap-*dpi/ic_launcher.png`, 48-192px | full bleed |
| Adaptive launcher icon | `drawable-*dpi/ic_launcher_foreground.png` + `mipmap-anydpi-v26/ic_launcher.xml` | a 108dp canvas with the poster inset to 72dp |

The generator is the single source of truth for all of them. The generated files
are committed, and CI runs `--check` so they cannot silently drift from the
artwork. Pillow is required for this and is deliberately **not** a project
dependency — CI resolves it ephemerally.

### Why the adaptive icon is inset to 72dp

An `adaptive-icon` foreground is drawn on a 108dp canvas of which only the
central 66-72dp is guaranteed visible. The launcher crops to that viewport and
*then* applies a mask — circle, squircle, rounded square or square. Dropping the
full-bleed poster in would put "HKFASTDC" and the bottom banner outside the safe
zone, and they would be cut off on every modern launcher.

The poster's own background is a flat `#D9DDE6`, so filling the canvas with that
same colour and insetting the poster to 72dp makes the mask edge land on
identical colour. The artwork reads as full-bleed while every element stays
inside the safe zone.

**Previewing this is where it is easy to fool yourself.** Looking at the 108dp
canvas proves nothing — you have to crop the central 72/108 *first*, then apply
each mask. Measured on this artwork the content bounding box is
`x 0.104-0.865 / y 0.137-0.873` of the poster, and all four mask shapes clip
**0.00%** of it: the poster's own margins are exactly what the masks remove.

### Why the in-app assets are WebP and the launcher icons are not

The source is a **JPEG**, so it already carries compression noise. Storing that
noise in a PNG preserves it perfectly and expensively. Measured at 1024px:

| PNG (optimize) | PNG (256 colours) | WebP q90 | JPEG q90 |
|---|---|---|---|
| 904 KB | 382 KB | **92 KB** | 138 KB |

PNG is the worst column, and it is the one this started with. 92 KB is a 10x
saving on art that is flat fills and hard type, which is exactly what lossy WebP
handles without visible damage. WebP is safe here because this app ships Android
only — there is no `ios/` directory, and Android has decoded WebP since API 14.

The launcher icons stay PNG: they total about 290 KB, they are the first thing
the OS decodes, and 48px is where a lossy encode *would* show.

**The legacy icon is a deliberate trade-off.** At 48px the full poster is an
unreadable red blob; a car-only crop would stay legible. Android 8 and above uses
the adaptive icon instead, which is essentially every device in service, so the
blob is only ever seen on Android 7.1 and below.

---

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

The generator writes **63** raw responses to `test/fixtures/`, each with the
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
  (`app/services/auth/account_service.py`), and `POST /drivers/register` only
  creates a `DriverProfile`. Driver capability is gated entirely on
  `DriverProfile.status` — there are 19 `require_active_user` driver routes and
  zero role-gated ones. So routing keys off admin-vs-not
  (`lib/router/app_router.dart`), and driver mode is entered from the account
  screen (`lib/features/shared/account_screen.dart`).
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

**Turnstile site key.** Registration, login, the OTP send and the phone-bind send
are all behind Cloudflare Turnstile, and **no endpoint serves the site key**:
`turnstile_site_key` exists in `app/core/config.py` but is exposed by no route, so
the app can only learn it at build time.

```bash
--dart-define=TURNSTILE_SITE_KEY=0x4AAAAAAA...
--dart-define=TURNSTILE_BASE_URL=https://hkfastdc.com/   # optional, this is the default
```

`TURNSTILE_BASE_URL` is the origin the widget is loaded under, and it decides
which hostname Cloudflare mints the token for. It must be on the site key's
allowed-domain list, and it is **not** the API host.

Three behaviours worth knowing:

* **No site key in a debug build** → the challenge is not rendered at all, and no
  `human_token` is sent. That is the normal development state, because
  `DisabledHumanVerifier` allows everything server-side.
* **No site key in a release build** → a visible red panel naming the missing
  define, plus a reported error. A release must not ship a sign-in form that
  cannot work. `resolveTurnstileMode` is the entire rule and is unit-tested.
* **A token is single-use**, so every form resets the challenge after a submit
  (`TurnstileChallengeState.reset`). Without that the box still reads "Success"
  while the form holds a spent token, and the retry is refused for a reason the
  user cannot see.

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

Three doors, and only the first is the primary one:

| Door | Route | Credential |
|---|---|---|
| Log in | `/login` | email + password |
| Register | `/login/register` | email + password + a phone number (**claimed**, not proven) |
| Phone login | `/login/phone` → `/login/otp` | an **already-proven** number + a code |

A phone number is no longer a login credential in its own right. It is a claim at
registration, a secondary login once proven, and the thing that unlocks calling a
taxi. Proving it is a separate step in the account area — `/phone/unlock`, reached
from the account screen — and it is **not** a precondition for having an account.

The code is never echoed in a response (`SEC-02`), so for local work the dev rail
is what makes the OTP screens usable at all:

```bash
ALLOW_DEV_OTP=true uvicorn app.main:app --port 8000 --no-access-log
```

then use any `+852` number and the code `123456`. Registration and the email +
password login need no such switch, but they do need a Turnstile token once
`APP_ENV=prod` — see Configuration.

## Tests

```bash
dart --packages=.dart_tool/package_config.json tool/run_tests.dart
```

**149** cases over the code with no Flutter dependency: money and date formatting, the
wire decoders, the enums, the error envelope, websocket frames, pagination, the
models (including the fleet and profile shapes), the router redirect rules, the
password policy, the HK phone format, and the Turnstile widget protocol.

Formatting is `dart format --line-length 100` — the flag matters, the repo is
written at 100 columns and the tool defaults to 80, which would reformat every
file in the tree. CI enforces it with `--set-exit-if-changed lib tool`, so a
non-conforming file fails the build rather than merely being reported.

The pinned SDK is Flutter **3.44.0** (bundled Dart **3.12.0**), and the format
output is version-specific: the Dart 3.12 formatter joins lines the older one
split, so a tree formatted by a different SDK is not conforming here. When the
gate was first added only eight files were fixed; drift accumulated afterwards
(nineteen files at `HEAD`), which is why the whole tree was reformatted. If you
touch `lib/` or `tool/`, run the formatter before committing.

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
              storage, formatting, theme, location, security (password policy),
              human (the Turnstile seam), phone (the HK number format)
  models/     one file per response shape, decoded from the wire
  data/       repositories — one per API area, thin over ApiClient
  state/      Riverpod providers and the auth controller
  router/     go_router configuration and the role redirect
  features/   screens, grouped by role (`passenger/`, `driver/`, `admin/`,
              `fleet/`, `auth/`), plus shared widgets
branding/
  source/     the two 2048px posters as supplied — the only hand-edited input
assets/
  branding/   generated: the in-app posters, WebP (see Branding above)
tool/
  dart_check.py             type-check via LSP (see above)
  gen_branding_assets.py    regenerate every icon and in-app asset
  run_tests.dart            unit tests for the pure code
  verify_contract.dart      decode every fixture with the real models
```

Two files are split purely so the tests can reach them: `core/human/` is a
protocol file with no Flutter import plus a widget file that has one, and
`core/security/password_policy.dart` mirrors `app/core/passwords.py` without
touching a `BuildContext`. `tool/run_tests.dart` runs on the plain VM, so anything
it imports must not reach `package:flutter`.

## Known gaps

* No **widget** tests — the harness that would run them (`flutter test`) does not
  work here, and there is no headless alternative without the Flutter tool.
  Everything testable without a widget tree is covered by `tool/run_tests.dart`.
* **The Turnstile WebView is unverified at runtime — and so is the plugin behind
  it.** The challenge is rendered by `WebViewWidget`
  (`lib/core/human/turnstile.dart`), which needs a platform view and a real
  Cloudflare key; neither exists in this sandbox. What *is* verified: the message
  protocol (`parseTurnstileSignal`), the build-mode decision
  (`resolveTurnstileMode`), and the rendered page. What is **not**: that
  `webview_flutter` actually reaches an APK — that needs the Gradle build, which
  cannot run here (see Building an APK). `dart pub get` did put
  `webview_flutter` and `webview_flutter_android` into `pubspec.lock`, but the
  Android plugin list Gradle reads is a separate file that only `flutter pub get`
  writes. Whether Cloudflare **accepts** the token additionally depends on the
  site key's allowed-domain list containing the host in `TURNSTILE_BASE_URL`.
* Push notifications are not wired up; the trip screen polls instead.
* **No change-password or forgotten-password flow.** There is no
  `POST /auth/password/*` on the server either, so this is a backend gap as much
  as a client one.
* ~~No profile-completion screen~~ — **closed.** `lib/features/shared/profile_setup_screen.dart`
  calls `POST /identity/profile` (username + given/family name + optional gender), offered
  from the account screen and the booking screen. It is deliberately **not** a gate: the
  server never refuses anything for an incomplete profile, so a client-side redirect would be
  stricter than the API. Registration still does not collect a name.
* ~~`GET /identity/me` is not in `test/fixtures/`~~ — **closed.** `identity_me.json` and
  `identity_profile.json` are captured by `scripts/dev/gen_mobile_fixtures.py` and decoded by
  the real `Profile` in `tool/verify_contract.dart`, so the fixture loop now covers it.
* **A reviewer account that has expired still gets a session from `/auth/login`
  if it was signed in before the expiry.** Cold start is handled — a 403
  `REVIEWER_ACCOUNT_EXPIRED` from `/auth/me` clears the stored session rather than
  leaving the splash on a retry that can never work — but the login route itself
  is the authoritative check and this is the client's half of it.
