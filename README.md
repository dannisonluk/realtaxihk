# hkfastdc.com

Hong Kong taxi matching platform — **information intermediary** (Cap. 374D compliant).
One repo, **three complete deliverables**: a FastAPI backend, a Flutter app for
three roles, and a web admin console.

> 香港的士配對平台，走**資訊中介**定位（非承運人）。一個 repo 內含三件完整交付物。

**Status: production-hardened.** Backend test count is not quoted here — it drifts
and this line had gone stale twice; see [`docs/README.md`](docs/README.md) for the
measurement baseline. Mobile: 161 assertions · 64 contract fixtures decoded (65 files) ·
console: 96 vitest tests · browser UI verifier PASS.

**New here? Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) first** — a guided
tour of how a trip flows from hail to settlement, where money is allowed to
change, and which invariants are load-bearing. Then read
[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) before you change anything.
Full picture: [`docs/WORK_SUMMARY.md`](docs/WORK_SUMMARY.md) — what's built,
what's verified, what still needs credentials. Security:
[`docs/SECURITY.md`](docs/SECURITY.md). Document index:
[`docs/README.md`](docs/README.md) — dated audits and reviews now live in
[`docs/archive/`](docs/archive/README.md).

---

## Contents

| | Section | What it covers |
|---|---|---|
| 1 | [Overview](#1-overview) | The product, the three deliverables, how they fit |
| 2 | [Quick start](#2-quick-start) | Get all three running |
| 3 | [Backend](#3-backend--app) | FastAPI service, domain rules, API surface |
| 4 | [Mobile](#4-mobile--mobile) | Flutter app, three roles, offline/error handling |
| 5 | [Admin console](#5-admin-console--admin-web) | React console + legacy ES-module build |
| 6 | [Shared contracts](#6-shared-contracts) | How the three stay in sync (the real risk area) |
| 7 | [Verification](#7-verification) | What is actually tested, and how |
| 8 | [Deploy & ops](#8-deploy--ops) | Compose, nginx, backups, fail-closed config |
| 9 | [Repository layout](#9-repository-layout) | Every top-level directory |

---

## 1. Overview

### 1.1 The product

A passenger hails, nearby drivers are broadcast to, one driver wins the order,
the trip runs, and the driver is billed a weekly service fee. The platform never
carries the passenger.

> 乘客叫車 → 向附近司機廣播 → 一位司機搶單 → 行程進行 → 司機每週付服務費。
> **平台不承運**，這條定位直接決定了免責聲明、估價僅供參考、以及司機自報距離
> 這三個設計。

### 1.2 The three deliverables

| # | Deliverable | Path | Scale | Tech |
|---|---|---|---|---|
| 1 | **Backend** | `app/` | 141 files · 28,933 LOC | FastAPI async · PostgreSQL 16/PostGIS · Redis 7 · SQLAlchemy 2.0 · Alembic |
| 2 | **Mobile** | `mobile/` | 93 Dart files · 19,481 LOC | Flutter 3.44 · Riverpod · go_router · Dio · flutter_secure_storage |
| 3 | **Admin console** | `admin-web/` | 52 TS/TSX files · 18,237 LOC | React 18 + Vite + TypeScript (current) · hand-written ES modules (legacy) |

Plus the glue that keeps them honest: `scripts/` (25 tools), `tests/` (61 files),
`docs/` (14 living documents + `docs/archive/` for dated snapshots),
`alembic/` (24 migrations), `deploy/`.

### 1.3 How they fit together

```
                    ┌──────────────────────────────────────┐
   Passenger ──────▶│                                      │
                    │        FastAPI  (app/)               │
   Driver ─────────▶│  REST  /api/v1/*   +   WS /ws/trip   │
                    │                                      │
   Admin ──────────▶│  + /api/v1/admin/*                   │
                    └───────┬──────────────┬───────────────┘
                            │              │
                    PostgreSQL 16     Redis 7
                    (+ PostGIS)     geo index, locks,
                    the ledger      rate limits, Pub/Sub
                            ▲
                            │  one OpenAPI schema, three consumers
              ┌─────────────┴─────────────┐
              │                           │
        mobile/ (Flutter)          admin-web/ (React)
        Dart models verified        TypeScript types
        against captured fixtures   mirror the schema
```

**The risk this diagram hides**: three consumers, one schema, no code generation.
Nothing in the build forces them to agree. That is why §6 exists and why the
fixtures in `mobile/test/fixtures/` are captured from a *running* API rather
than hand-written.

> 這張圖藏著真正的風險：**三個消費者、一份 schema、沒有 code generation**。
> 沒有任何建置步驟強制它們一致。所以 §6 是必要的，而 mobile 的 fixtures 是
> 從**真實運行中的 API** 抓下來的，不是手寫的。

---

## 2. Quick start

### 2.1 Backend

```bash
# 1. infra (PostGIS :15433, Redis :16379 — ports avoid sibling projects)
docker compose up -d db redis

# 2. app (uv manages venv + deps; --all-extras pulls dev tools: pytest, ruff)
#    --frozen is deliberate: the lockfile, not the pyproject floors, is the
#    authority. Without it a local `uv sync` can resolve versions the Dockerfile
#    and CI (`uv sync --frozen`) will never install, so you would be testing
#    something that does not ship.
uv sync --frozen --all-extras
cp .env.example .env          # adjust if needed; see §8.2 Configuration

# 3. schema
.venv/Scripts/python -m alembic upgrade head

# 4. run + verify
.venv/Scripts/python scripts/dev/serve_and_probe.py   # detached uvicorn + health wait
.venv/Scripts/python scripts/verify/verify_api.py     # one-shot API smoke
.venv/Scripts/python -m pytest -q                     # needs `docker compose up -d db redis`
uv run ruff check . && uv run ruff format --check .
```

On Linux/macOS use `.venv/bin/python` instead of `.venv/Scripts/python`.

### 2.2 Mobile

```bash
cd mobile
flutter pub get          # needs the Flutter CLI — on a host/CI machine, see §4.4
cd android && ./gradlew :app:assembleDebug   # → build/app/outputs/flutter-apk/app-debug.apk
```

> `./gradlew` works even in the sandbox; the `flutter` CLI does not. See §4.4.

### 2.3 Admin console

```bash
cd admin-web/web
npm install
npm run typecheck && npx vitest run && npm run build   # → web/dist
cd .. && python serve.py                               # serve the React build (default)
```

> `serve.py` serves the **React** build by default; `--legacy` serves the old
> console. See §5.

---

## 3. Backend — `app/`

### 3.1 Layers

```
app/api/           HTTP layer — I/O, validation, error mapping. No business rules.
                  14 modules + admin/ (one module per resource) + schemas/
                  (11 Pydantic response models)
app/services/      Domain logic — every business invariant lives here, grouped
                  by bounded context (auth/ licence/ order/ ledger/ fleet/
                  admin/ infra/)
app/models/        SQLAlchemy 2.0 declarative, split by bounded context
                  (_base, user, admin, fleet, licence, dispute)
app/core/          Cross-cutting: config, money, deps (RBAC), rate_limit,
                  client_ip, hk_bounds, exceptions, logging, security, totp
app/main.py        App wiring, lifespan background jobs, Sentry
```

**Design rule that shows up everywhere**: *correctness must not depend on the
fast component.* Redis is used for locks, geo indexing and rate limits — but
every one of them has a database-level backstop. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §2.3.

> **貫穿全 repo 的設計原則**：正確性不可以依賴那個「快」的元件。
> Redis 負責鎖、地理索引、速率限制，但每一項都有資料庫層的後備。

### 3.2 Domain rules

- **Auth & KYC** — WhatsApp OTP (sha256-hashed, TTL, resend cooldown, 5-attempt
  cap, per-IP + global rate limits); JWT HS256 with rotating refresh tokens
  (hashed at rest, single-use). Driver lifecycle
  `PENDING_KYC → DEPOSIT_REQUIRED → ACTIVE → SUSPENDED/TERMINATED`.
  Deactivation takes effect immediately: every request re-checks the DB
  (`require_active_user`), not just the JWT.

  > 停用**即時生效**——每個 request 都重讀資料庫，不是只信 JWT。

- **Orders & dispatch** — fare snapshot frozen into `fare_json` at creation;
  Redis `GEOSEARCH` nearby broadcast; `SETNX` + Lua-release lock for atomic grab
  (exactly-once, 6-way concurrency tested); lifecycle
  `BROADCASTING → ACCEPTED → DRIVER_ARRIVED → IN_TRIP → COMPLETED/CANCELLED`;
  $50 no-show penalty (negative ledger = arrears). Creation is rate-limited
  (5/60s per passenger → 429).

  > 估價在**建立訂單的一刻凍結**。tariff 每年會調，若只存距離就會用新價結算舊單。

- **Ledger** — append-only `ledger_entries` with a `balance_after` chain,
  `with_for_update` row locks, and a reference-idempotency index. Five entry
  types, each with its **own reference namespace** so they cannot collide
  (`SEC-13`): `DEPOSIT_TOPUP` (`grant:`), `WEEKLY_FEE_DEDUCTION`
  (`weekly:` / `fleet:`), `PENALTY_DEDUCTION`, `REFUND` (`refund:`),
  `ADJUSTMENT` (`adj:` — signed manual correction, ±HK$5,000, reason required,
  attributed via `created_by`).

  > **參考號命名空間必須分開**。共用會令「植入一筆週費」變成靜默漏收——
  > 報告顯示 `skipped`，而錢永遠收不到。詳見 `docs/ARCHITECTURE.md` §3.2。

- **Live tracking** — `WS /ws/trip/{order_id}` (passenger subscribes, assigned
  driver pushes, driver ACTIVE re-checked per tick); ticks persist to PostGIS and
  fan out via Redis Pub/Sub (`realtaxi:trip:{order_id}`); server pings every
  `WS_HEARTBEAT_S`. Close codes: `4401` unauthenticated, `4403` forbidden,
  `4404` unknown order.

- **Fleets & settlement** — a fleet is a **licensed operator** (the Transport
  Department grants the licence), so it is admin-created, never self-service. The
  roster is the billing boundary: an ACTIVE member leaves the platform-wide
  weekly run and is charged the fleet's discounted rate instead. A partial unique
  index keeps a driver on at most one ACTIVE roster, so a second add is a 409
  rather than a silent double-bill.

  > 平台的幂等檢查是 per (driver, week)，車隊的是 per (fleet, week)——
  > **兩者都不會保護另一邊**。所以平台結算必須主動排除已入名冊的司機。

- **Background jobs** — geo ghost-order sweeper (auto-cancels stale
  `BROADCASTING` after `MAX_BROADCAST_MINUTES`) + PDPO retention purges; both run
  in lifespan tasks and are cancelled cleanly on shutdown.

### 3.3 API surface

All routes under `/api/v1` unless noted. Auth = `Authorization: Bearer <access JWT>`.

| Area | Endpoints |
|---|---|
| **Auth — account** | `POST /auth/register` · `POST /auth/login` · `POST /auth/refresh` · `POST /auth/logout` · `GET /auth/me` |
| **Auth — phone OTP** | `POST /auth/otp/request` · `POST /auth/otp/verify` (secondary login for an *already-proven* number) |
| **Identity** | `GET /identity/me` · `POST /identity/profile` · `GET /identity/username-check` · `POST /identity/email/request` · `POST /identity/email/confirm` · `POST /identity/phone/request` · `POST /identity/phone/confirm` · `POST /identity/phone/reverify` · `POST /identity/avatar/uploads` |
| **Drivers** | `POST /drivers/register` · `GET /drivers/me` · `GET /drivers/me/ledger` |
| **Fare** | `POST /fare/estimate` |
| **Orders** | `POST /orders` · `GET /orders/nearby` · `GET /orders` · `GET /orders/{id}` · `POST /orders/{id}/grab` · `.../arrive` · `.../start` · `.../complete` · `.../cancel` |
| **Driver GPS** | `POST /drivers/location` |
| **Trips** | `GET /trips/{order_id}/location` (REST snapshot for WS reconnects) |
| **Admin — KYC** | `GET /admin/drivers` · `POST /admin/drivers/{id}/review` · `POST /admin/drivers/{id}/deposit/grant` · `POST /admin/drivers/{id}/deposit/adjust` |
| **Admin — refunds** | `GET /admin/refunds` · `POST /admin/refunds/{id}/decision` |
| **Admin — settlement** | `POST /admin/settlement/weekly/run` (idempotent per ISO week) |
| **Admin — fleets** | `GET /admin/fleets` · `POST /admin/fleets` · `PATCH /admin/fleets/{id}` · `GET\|POST /admin/fleets/{id}/members` · `DELETE /admin/fleets/{id}/members/{driver_id}` · `GET /admin/fleets/{id}/settlement` · `POST /admin/fleets/{id}/settlement/run` |
| **Fleets (driver)** | `GET /fleets/me` · `GET /fleets/{id}` · `GET /fleets/{id}/members` · `GET /fleets/{id}/settlement` |
| **Live** | `WS /ws/trip/{order_id}?token=<access JWT>` |
| **Ops** | `GET /health` (pings DB + Redis, 503 on failure) · `GET /metrics` (when `PROMETHEUS_ENABLED`) |

> `response_model=` is a **filter**, not an annotation: FastAPI silently drops any
> key a model does not declare. Models are reverse-engineered from captured
> fixtures, never from reading the handler.
> Guarded by `scripts/verify/audit_response_models.py` → `68 fixture blocks, OK`.

### 3.4 Fare engine

- Meter tariffs effective **2024-07-14** (TD press release; Cap. 374D schedule):
  Urban $29 flagfall → $2.1/200m (to $102.5) → $1.4; NT $25.5 → $1.9 (to $82.5)
  → $1.4; Lantau $24 → $1.9 (to $195) → $1.6. Waiting per minute or part.
- Tolls: cross-harbour $25 (+$25 return fee, waived at cross-harbour stands /
  same-side destination), Tai Lam $28, Tates Cairn $20, Lion Rock / Eagle's Nest
  / Shing Mun / Aberdeen $8, Lantau Link $30.
- Extras: baggage $6, animal $5, advance booking $5; discounts apply to the meter only.

`tariff_version` = `meter:2024-07-14;tolls:2025-09-21`.

**Money precision** — four functions, not one, because they answer different
questions (`app/core/money.py`):

| Function | Precision | For |
|---|---|---|
| `money_str()` | 2 dp | **stored** money (`Numeric(10,2)` columns) |
| `meter_str()` | 1 dp | **meter readings** (tariff-derived) |
| `quantize_money()` | 2 dp → `Decimal` | values that keep being arithmetic |
| `ratio_str()` | 2 dp | **derived** figures (means, rates) |

All `ROUND_HALF_UP`.

> `Decimal.quantize` 預設是 `ROUND_HALF_EVEN`（銀行家捨入），會令同一個響應內
> 的比率與金額往相反方向捨入。四個函式都固定 `ROUND_HALF_UP`。

---

## 4. Mobile — `mobile/`

### 4.1 What it is

One Flutter app, **three roles**, routed by the authenticated account.

| Role | Shell | Entry point |
|---|---|---|
| Passenger | 3 tabs | `lib/features/passenger/` |
| Driver | 3 tabs | `lib/features/driver/` |
| Admin | 4 tabs | `lib/features/admin/` |

Plus `features/fleet/` (fleet members see their own roster and settlement) and
`features/auth/` (phone → OTP → role-based redirect).

Target: **Android first** (minSdk 24, `applicationId = com.hkfastdc.mobile`),
iOS-compatible source. Flutter 3.44.0 / Dart 3.12.0, Java 17 / Kotlin JVM 17.

### 4.2 Structure

```
lib/
  core/
    network/    api_client.dart (Dio), wire.dart (JSON decode helpers), api_exception.dart
    storage/    token_store.dart — flutter_secure_storage, not SharedPreferences
    location/   geolocator wrapper
    config/     base URL + env
    theme/ format/
  state/        Riverpod providers — auth_controller, data_providers,
                providers, order_history_controller
  router/       go_router — app_router.dart, routing_rules.dart
  models/       Dart models mirroring the backend schema
  data/         repositories (one per backend area)
  features/     passenger · driver · admin · fleet · auth · shared
```

**Stack choices, and why**:

- **Riverpod** over Provider — the auth state drives a global redirect, and
  Riverpod's dependency graph makes "re-evaluate the router when auth changes"
  expressible rather than hand-wired.
- **go_router** — declarative deep-link + redirect, which is what a role-based
  redirect needs.
- **`flutter_secure_storage`** for tokens — never `SharedPreferences`. The
  access and refresh tokens are credentials.

> **Access token 存 `flutter_secure_storage`，不是 `SharedPreferences`。**
> 兩者是不同的安全等級，token 屬於後者。

### 4.3 Contracts, and why they are captured not written

`lib/models/` mirrors the backend schema — but nothing enforces that at build
time. So the wire format is **captured from a running API** into
`test/fixtures/` (63 fixtures) and every one is decoded with the real Dart models:

```bash
.venv/Scripts/python scripts/dev/gen_mobile_fixtures.py     # capture
cd mobile && dart --packages=.dart_tool/package_config.json tool/verify_contract.dart
# → 64 fixture(s) decoded, 0 failure(s)
```

A backend field rename that the Dart model does not expect fails here, before
anyone builds an APK.

> 後端改一個欄位名 → 這裡會解碼失敗。**在有人 build APK 之前就發現。**

### 4.4 The sandbox limitation (read before you try to run Flutter)

**`flutter` and `dart analyze` cannot run in this environment.** The Dart VM uses
**named pipes** for child-process stdio on Windows, so every spawn fails with
`ERROR_PIPE_BUSY (231)`: `flutter create` / `flutter run` / `flutter test` /
`dart analyze` / `dart run` all die at startup. Disabling the sandbox does not
help — it is a host limitation, not a policy one.

What does work:

```bash
dart format --line-length 100 lib tool                        # formatting
python tool/dart_check.py .                                   # type-check, over LSP
dart --packages=.dart_tool/package_config.json tool/run_tests.dart
dart --packages=.dart_tool/package_config.json tool/verify_contract.dart
```

`tool/dart_check.py` drives the analysis server over LSP **from Python** (which
uses anonymous pipes, so it works). It is the same engine `dart analyze` would
use, with the same `analysis_options.yaml`. `--packages=` bypasses `dartdev`,
which is what trips the native-assets build hook.

> **The APK does build in this environment — bypass the CLI and call Gradle:**
> `cd mobile/android && ./gradlew :app:assembleDebug`. Only the `flutter` CLI
> dies (its version check spawns `git`); the Flutter Gradle plugin reads
> `flutter.sdk` from `local.properties` and runs the same build. On a host or in
> CI, `cd mobile && flutter build apk --debug` works too.

See [`mobile/README.md`](mobile/README.md) for the full detail, including the
three ways the LSP driver silently reports "0 diagnostics" while being wrong.

---

## 5. Admin console — `admin-web/`

### 5.1 Two builds, side by side

| | Entry | Build | Status |
|---|---|---|---|
| **React (current)** | `web/` | Vite + React 18 + TypeScript → `web/dist` | supported |
| **Legacy** | `legacy/` (`index.html`, `js/`, `styles.css`) | none — hand-written ES modules | kept as reference |

`serve.py` serves the React build by default; `--legacy` serves the old one
(`legacy/`).

The legacy bundle is kept because it still works **and it is the reference the
rewrite was verified against** — remove it only once nothing depends on it.

> legacy 版刻意保留：它仍然可用，而且是 React 重寫時的**驗證參照**。

### 5.2 What it does

The management and admin surfaces: KYC queue · refund decisions · weekly
platform settlement · driver detail · dispute thread and resolution · live map ·
audit log · search · analytics · fleet register with rosters and fleet-level
settlement.

### 5.3 Stack and structure

```
admin-web/web/src/
  pages/        21 pages — Kyc, Refunds, Settlement, Disputes, LiveMap, Orders,
                DriverDetail, Fleets, FleetDetail, Licence, Accounts, Audit,
                Search, Analytics, Login, Dashboard…
  api/          typed calls into the backend
  components/   shared UI
  i18n/         zh-HK / en strings
  app/          routing + shell
  lib/          labels, formatting helpers
```

- **React 18 + Vite + TypeScript**, `react-router` for routing.
- **i18n** — the console is bilingual; the contrast guard (`check_contrast.py`)
  runs against both themes.

### 5.4 Verified in a real browser

The console is not verified by unit tests alone — it is driven in a real Chromium
by Playwright against a running backend:

```bash
# `playwright` is not a top-level package here: it is nested under the Playwright
# CLI. The path is version-specific — substitute the one under
# `~/.workbuddy-ai/binaries/node/versions/<ver>/node_modules/@playwright/cli/node_modules`.
NODE_PATH="$HOME/.workbuddy-ai/binaries/node/versions/22.22.2-3/node_modules/@playwright/cli/node_modules" \
  node admin-web/tool/verify_ui.mjs --base http://127.0.0.1:8081 \
    --username ops-admin --password "$ADMIN_PASSWORD" --totp-secret "$ADMIN_TOTP_SECRET"
```

Plus `audit_layout.mjs` (28px minimum touch targets, 52 renders clean at 1440px
and 500px) and `check_theme_tokens.py`.

> **為什麼要真瀏覽器**：unit test 看不到「按鈕太小」、「兩個主題對比度不足」、
> 「路由沒有註冊」這類問題。`audit_layout` 當初就抓到 `#/live` 從來不在
> `ROUTES` 裡——所以唯一渲染第三方地圖控件的頁面，正好是唯一沒被量到的頁面。

### 5.5 RBAC on the client

The console mirrors the backend's four-level `AdminRole`
(`SUPPORT < OPERATIONS < FINANCE < SUPER_ADMIN`), but **the client is not the
enforcement point** — it only hides what the server would refuse anyway.

One subtlety the codebase had to fix: the server compares roles by **rank**, not
set membership, so the client must send a boolean capability
(`canMoveMoney: hasRole('FINANCE')`) rather than a role string. A role string on
the wire invites the client to re-implement the ranking rule, and then the two
implementations drift.

> 前端不負責執行權限，只負責隱藏。但注意：**後端用排名比較，不是集合成員**，
> 所以前端傳布林能力（`canMoveMoney`）而不是角色字串——否則等於要前端
> 重新實作一次排名規則，然後兩份實作就會分家。

---

## 6. Shared contracts

The three deliverables share one OpenAPI schema, and **nothing in the build
enforces that they agree**. This section is the mitigation.

| Boundary | Mechanism | Failure it catches |
|---|---|---|
| API → mobile | 63 fixtures captured from a **running** API, decoded by real Dart models | A renamed/removed field, before an APK is built |
| API → console | TypeScript types in `api/`, `npm run typecheck` | A changed response shape at compile time |
| Response shape → itself | `audit_response_models.py` vs captured fixtures (81 blocks) | A `response_model` that silently drops a field |
| DB schema → models | `test_migration_schema_parity.py` — the **only** test that runs migrations | Model/migration drift |
| Enum shape → DB | `test_enum_check_constraints.py` + CHECK constraints | A value the app cannot read back |

> **這一節是整個 repo 最容易被低估的部分。**
> 三個消費者、一份 schema、零 code generation——沒有任何建置步驟會因為
> 「前端還用舊欄位名」而失敗。所以上面每一列都是**手動建立的防線**。

Two concrete traps this repo hit, both worth knowing:

1. **Same key ≠ same type.** `/auth/logout` returns `revoked` as an **int**
   (count), `/admin/auth/logout` as a **bool**. Sharing a model silently turned
   `False` into `1`.
2. **Fixtures are hand-written, so they can agree with the bug.** A missing field
   in a model and a missing field in its fixture look identical — which is why
   the models are derived from *real responses*, not from the fixtures alone.

---

## 7. Verification

### 7.1 Run everything

```bash
# backend — needs db + redis containers up
.venv/Scripts/python -m pytest -q --junit-xml=.tmp/full.xml
uv run ruff check . && uv run ruff format --check .
uv run mypy                                        # types; no database needed

# mobile (Dart, in mobile/)
python tool/dart_check.py .                        # 0 diagnostics expected
dart --packages=.dart_tool/package_config.json tool/run_tests.dart      # 155 passed
dart --packages=.dart_tool/package_config.json tool/verify_contract.dart # 64 fixtures

# console (in admin-web/web/)
npm run typecheck && npx vitest run && npm run build
```

> `--junit-xml=` is not optional here. On this dev host, read the printed
> summary or the XML, never a pipeline exit code: when pytest is wrapped in a
> shell pipeline (e.g. `| tail`), the wrapper's exit code is the last command's,
> not pytest's. The 2026-10-07 full run printed `1285 passed`; treat that as the
> source of truth.
>
> `--junit-xml=` 不是可選項。本機要**只讀 XML／summary，不要依賴 pipeline exit
> code**：pytest 被 pipe 包住時，exit code 係最尾嗰個指令（例如 `tail`）嘅值，
> 唔係 pytest 嘅值。2026-10-07 全套實跑印出 `1285 passed`；以 print 出嚟嘅 summary
> 或 XML 為準。
>
> The same shim blocks `npm run build`: Vite empties `dist/assets` before writing, and
> once that directory holds more than 50 files the sandbox refuses the bulk delete —
> the build then dies with `x Build failed in 1.20s`, which reads like a compile error
> and is not one. Move `dist/` aside first; with a clean `dist` the build is green.
>
> 同一道守衛也會擋 `npm run build`：Vite 寫入前會清空 `dist/assets`，一旦該目錄超過
> 50 個檔案，沙盒就拒絕批量刪除，build 會以 `x Build failed in 1.20s` 收場 —— 看起來
> 像編譯錯誤，其實不是。先把 `dist/` 移走再 build 就綠。

### 7.2 What is covered

| Suite | Count | Covers |
|---|---|---|
|| `tests/`（61 個 `.py`；60 個 `test_*.py` + conftest） | **全套 1285 passed / 0 failed / 0 error**（單一 process 實跑，2026-10-07） | fare unit · per-module API · WS streaming · fleets/roster/settlement · backup retention + restore drill · console contrast · hardening regressions |
| `mobile/tool/run_tests.dart` | **161** | Dart unit assertions |
| `mobile/tool/verify_contract.dart` | **64 fixtures**（共 65 個 fixture json） | every wire shape, decoded by the real models |
| `admin-web/web` (vitest) | **96** | page-level behaviour |
| `verify_ui.mjs` + `audit_layout.mjs` | PASS | real-browser E2E, layout, both themes |

> 後端 pytest 狀態（2026-10-07）：**一次過 `pytest tests` 全套實跑全綠 =
> 1285 passed / 0 failed / 0 error / 0 skipped**（印出 summary；見 §7.1 關於
> pipeline exit code 嘅警告）。
> 先前「一次過跑會中途中止、唔敢宣稱 full-suite 全綠」嘅情況**已經消失**。
> 跑法：`docker compose up -d db redis` 之後
> `.venv/Scripts/python.exe -m pytest tests -q --junit-xml=.tmp/full2.xml`。
>
> ⚠️ 並行跑兩隻 pytest 仍**不建議**（爭同一 DB/Redis 資源），但「互相污染出假
> failed」嘅根因已消除：**Redis GEO index 已納入 `REDIS_KEY_NAMESPACE`**
> （`geo_orders_key()`，per-process），同 rate-limit key 一樣。
> 見 [`docs/README.md`](docs/README.md) 的量測基準表。

Per-test isolated Postgres databases (template clone) — no cross-test state.

> **CI is `ubuntu-latest`; the dev sandbox is Windows.** Any test touching
> `Path` / `os.sep` / absolute-path resolution can be green locally and red in
> CI. `Path("C:/x").is_absolute()` is `True` on Windows and `False` on POSIX.
> Assert only platform-neutral properties, or prove it with both
> `PurePosixPath` and `PureWindowsPath`.

### 7.3 Verdict-producing scripts

These answer questions, they do not just exercise code:

```bash
.venv/Scripts/python scripts/verify/audit_response_models.py   # 81 blocks, OK
.venv/Scripts/python scripts/verify/prod_boot_drill.py         # 12 fail-fast cases
.venv/Scripts/python scripts/verify/live_smoke.py              # 9-check E2E
.venv/Scripts/python scripts/verify/security_verify.py         # re-run every finding
.venv/Scripts/python scripts/verify/security_probe.py all      # original attack probe
```

The two security scripts boot their own uvicorn on `:8000` and only create
throwaway users/orders. See
[`docs/archive/SECURITY_AUDIT.md`](docs/archive/SECURITY_AUDIT.md)
for what each check maps to.

### 7.4 CI

`.github/workflows/ci.yml` — four jobs on `ubuntu-latest`, Python 3.12,
`uv sync --frozen`:

| Job | Runs |
|---|---|
| `test` | ruff check → format check → full pytest → prod boot drill → response-model audit (PostGIS + Redis service containers) |
| `types` | `uv run mypy` over `app/` — no services; it reads source, not a database |
| `mobile` | `flutter analyze` → `run_tests.dart` → `verify_contract.dart` |
| `admin-web` | `tsc --noEmit` → vitest → `npm run build` |

The `types` job is not decoration. It found a real defect the first time it ran:
the keyset cursor in `app/api/orders.py` compared a Python tuple against two
columns, which Python evaluates as a single-column comparison, so orders sharing
the anchor's `created_at` were silently dropped from the history page. See
[`docs/archive/STRUCTURE_REVIEW.md`](docs/archive/STRUCTURE_REVIEW.md) R9.

---

## 8. Deploy & ops

### 8.1 Compose

`docker compose up -d` runs the full stack: `db` (PostGIS 16-3.4), `redis` (AOF
on), `api` (auto-runs `alembic upgrade head` before serving,
`restart: unless-stopped`).

`docker-compose.prod.yml` is an **overlay** (`-f` stacked on the base), not a
standalone file — the base carries all the hardening. Credentials must exist
**before** the first `up`, or nginx restart-loops:
first run `certbot certonly --standalone`.

> **連線池三個數字是同一個決定**：`(DB_POOL_SIZE + DB_MAX_OVERFLOW) × API_WORKERS`
> 才是對 Postgres 的總需求（池是 per-process）。prod 檔明示
> `(10+20)×1 = 30 ≤ 100`，由 `tests/infra/test_prod_compose_pool_arithmetic.py` 守住。
>
> `API_WORKERS` 預設 1 是**正確性**而不是保守：`ConnectionRegistry` 與速率限制
> 計數器都在行程記憶體內，N 個行程會把全域上限各別執行 N 次，而且不會報錯。

See [`deploy/README.md`](deploy/README.md) ·
[`docs/DEPLOYMENT_REQUIREMENTS.md`](docs/DEPLOYMENT_REQUIREMENTS.md).

### 8.2 Configuration — fail-closed

`.env.example` documents every knob. The config layer refuses to boot rather
than run insecure.

- `APP_ENV` has **no default** and is whitelisted (`dev` | `test` | `prod`).
  Unset, or `production` / `PROD`, is a startup error — the old `app_env = "dev"`
  default turned any host without a `.env` into a dev-mode server that returned a
  fixed OTP in the response body.
- `JWT_SECRET_KEY` has no default and must carry real entropy (≥ 32 chars,
  ≥ 8 distinct — `"x" * 64` is rejected). `openssl rand -hex 32`.
- With `APP_ENV=prod` the app **refuses to boot** if: the JWT secret or
  `POSTGRES_PASSWORD` still hold dev defaults · `ALLOW_DEV_OTP` is set ·
  `PUBLIC_BASE_URL` is not `https://` · `CORS_ORIGINS` is unset, `*`, or
  contains a plain-http origin · `SMTP_HOST`/`SMTP_FROM` are unset ·
  `TRUSTED_PROXY_COUNT < 1` · `TURNSTILE_SECRET_KEY` is unset.
- The deterministic dev OTP (`123456`) needs **both** a non-prod env and an
  explicit `ALLOW_DEV_OTP=true`.
- `TRUSTED_PROXY_COUNT` controls how `X-Forwarded-For` is read (`0` = ignore it
  entirely, `1` = trust one nginx hop). A client-supplied prefix can never set
  its own source address — the hops are counted **from the right**.
- `METRICS_TOKEN` must be set for `/metrics` to be mounted at all.

> `X-Forwarded-For` 由**右邊**數 hop。取最左邊等於取攻擊者控制的那一段，
> 會令所有 IP 速率限制失效（SEC-07）。

`scripts/verify/prod_boot_drill.py` verifies all 12 of these cases.

### 8.3 Backups

The ledger is the financial record, so **a dump nobody has restored is not a
backup**. `verify` is the part that matters: it restores the newest archive into
a scratch database and compares exact row counts, table by table.

```bash
.venv/Scripts/python scripts/ops/db_backup.py backup   # the nightly run
.venv/Scripts/python scripts/ops/db_backup.py verify   # restore + compare + drop
.venv/Scripts/python scripts/ops/db_backup.py list     # what's on disk
```

Off-host copying is deliberately the operator's command, not a hard-coded
provider — a backup script that assumes S3 breaks on the next host:

```bash
.venv/Scripts/python scripts/ops/db_backup.py backup \
  --upload-cmd        'rclone copy {file} remote:realtaxi-backups/' \
  --upload-verify-cmd 'rclone lsf remote:realtaxi-backups/'
```

`--upload-verify-cmd` is worth setting. Without it, "upload ok" only means the
command exited 0 — `true` passes.

Retention defaults to 7 daily + 4 weekly, counted by **calendar distance**, so
the weekly tier survives a problem that takes days to notice. **Redis is not
backed up on purpose** — rate-limit counters, grab locks and Pub/Sub are all
ephemeral; the database is the truth.

```
17 3 * * *  cd /srv/realtaxihk && .venv/bin/python scripts/ops/db_backup.py backup
```

### 8.4 Observability

**Only the backend is wired to Sentry**; neither client is.

> **未處理的 500 是經由 logging 進入 Sentry，不是經由 ASGI。**
> `app/core/exceptions.py` 有一個 catch-all handler，它吞掉 traceback 再回 500，
> 所以例外不會傳到 ASGI integration。真正送上報的是 logging integration
> （`logger.exception` 是 ERROR 級，而 SDK 的 `LoggingIntegration` 預設
> `DEFAULT_EVENT_LEVEL = logging.ERROR`）。
> **刪掉任何一個 `logger.exception`，該類錯誤就會靜靜停止上報。**

`max_request_body_size="never"` is set deliberately: the SDK default (`medium`)
sends request bodies, and an OTP request body is a phone number.

---

## 9. Repository layout

```
app/              FastAPI backend — see §3
mobile/           Flutter client (Android first) — see §4
admin-web/        Web console, React + legacy — see §5

alembic/          async migrations (postgis tables filtered via include_object)
tests/            52 pytest files, grouped by what they need — and the only place
                  migrations are actually run
  api/              35 drive the HTTP surface (they take the `client` fixture)
  domain/            7 pure logic, no database (fare, money, bounds, totp)
  infra/            10 guards over files and configuration (migration parity,
                     pool arithmetic, backup, compose, `scripts/` root, contrast)
scripts/          tooling, grouped by what you are doing
  ops/              operate a real environment — db_backup, create_admin,
                    create_admin_account, create_reviewer_account, enrol_admin_totp
  verify/           produce a pass/fail verdict — audit_response_models, live_smoke,
                    security_probe + security_verify, prod_boot_drill, verify_api,
                    bench_location_pipeline, the three connection probes
  dev/              local workflow glue — serve_and_probe, serve_and_run_browser,
                    api_supervisor, stop_server, run_against_api, gen_mobile_fixtures
  _root.py          the repo root, computed once (not fifteen times)

deploy/           nginx TLS terminator + README
docs/             14 living documents + archive/ — start with docs/README.md
docker-compose.yml
docker-compose.prod.yml   overlay, not standalone
Dockerfile        multi-stage; `uv sync --frozen`
```

### 9.1 Documentation map

Start at [`docs/README.md`](docs/README.md) — the index, with "where do I read
what" by role.

| Document | Read it for |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | **Start here** — guided tour, business flow, invariants |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | Conventions, backend quirks, lint gate, methodology |
| [`docs/WORK_SUMMARY.md`](docs/WORK_SUMMARY.md) | Current state, everything outstanding |
| [`docs/archive/AUDIT_FRESH_2026-10-06.md`](docs/archive/AUDIT_FRESH_2026-10-06.md) | **Latest fresh root-and-branch audit** — independent backend/mobile/admin reports + fixes |
| [`docs/archive/AUDIT_2026-10-06.md`](docs/archive/AUDIT_2026-10-06.md) | Earlier 2026-10-06 audit, superseded by AUDIT_FRESH_2026-10-06.md |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Security model + hardening guide |
| [`docs/ADMIN_AUTH.md`](docs/ADMIN_AUTH.md) | Admin auth model, authenticator choice |
| [`docs/ADMIN_CONSOLE_DESIGN.md`](docs/ADMIN_CONSOLE_DESIGN.md) | Console design + four-level RBAC |
| [`docs/IN_TRIP_REDESIGN.md`](docs/IN_TRIP_REDESIGN.md) | Planned in-trip + pre-booking redesign |
| [`docs/DEPLOYMENT_REQUIREMENTS.md`](docs/DEPLOYMENT_REQUIREMENTS.md) | What deploy needs |
| [`docs/DEPLOY_TARGET_DECISION.md`](docs/DEPLOY_TARGET_DECISION.md) | Target decision + rationale |
| [`docs/REALTIME_POSITION_COST.md`](docs/REALTIME_POSITION_COST.md) | Cost model for live position |
| [`docs/LANDMARK_COORDINATES.md`](docs/LANDMARK_COORDINATES.md) | Boundary coordinates + legal basis |
| [`docs/archive/`](docs/archive/README.md) | **Dated snapshots — not updated.** Audits, code reviews, UI review, work log |
| [`scripts/README.md`](scripts/README.md) · [`mobile/README.md`](mobile/README.md) · [`admin-web/README.md`](admin-web/README.md) · [`deploy/README.md`](deploy/README.md) | Per-area detail |
