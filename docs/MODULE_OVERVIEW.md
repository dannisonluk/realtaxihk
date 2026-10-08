# realtaxihk — 系統模組總覽（Module Overview）

> 目的：畀人用紙筆逐模組核對「呢個系統有咩、點解咁樣互動」。
> 最後更新：2026-10-09。會隨架構改動而更新；舊數字留喺 archive，唔改呢度以外嘅歷史記錄。

一個 repo，三件交付物，Cap. 374D 合規的士資訊中介（非承運人）：

| 交付物 | 目錄 | 技術 | 量度 |
|---|---|---|---|
| 後端 API | `app/` | FastAPI (async) + SQLAlchemy 2.0 async + PostgreSQL/PostGIS + Redis + Alembic | 141 檔 · 115 paths / 129 operations · pytest 1304 passed |
| Flutter App | `mobile/` | Flutter + Riverpod + Dio + go_router | 34 個畫面 · 161 VM tests · contract 64 fixtures |
| Web 管理後台 | `admin-web/web/` | React + Vite + React Router (hash) | 24 pages · 97 vitest · `tsc --noEmit` clean |

---

## 1. 後端 API（`app/`）

### 1.1 `app/api` — HTTP 路由層（57 py）
每個 module 一個 router，全部經 `app/api/router.py` 掛入 `app/main.py`：

| Router | 功能 |
|---|---|
| `auth.py` | 註冊、登入、密碼 change/forgot/reset、refresh |
| `identity.py` | 電話 OTP、驗證、unlock、re-verify |
| `orders.py` | 叫車主流程：request/grab/accept/arrival/start/complete/cancel/interrupt/change-destination |
| `prebooking.py` | 預約（pre-booking）行程 |
| `recurring.py` | 週期性行程（from history） |
| `trips.py` | trip 查詢、歷史、狀態 |
| `tracking.py` | live tracking（同 WS 配合） |
| `receipts.py` | 收據 freeze 產生／讀取 |
| `drivers.py` | 司機註冊、onboarding、job list、earnings、refund request、environment |
| `driver_notifications.py` | 司機通知（push 目標，provider 未接） |
| `driver_attributes.py` | 司機屬性／標籤 |
| `fixed_offers.py` | 固定 offer |
| `fleets.py` | 車隊（fleet）CRUD |
| `destinations.py` | 目的地／premium destination |
| `service_area_route.py` | 服務區域／路線限制 |
| `fare.py` | 車資規則 |
| `licence.py` | 牌照審查 |
| `admin_auth.py` | admin 登入／session（cookie） |
| `admin_analytics.py` | admin 分析 |
| `admin_licence.py` | admin 牌照管理 |
| `ws.py` | WebSocket live 更新（Origin allowlist、native `Authorization: Bearer` / web `?token=` 雙通道、4401/4403/4404/4408 close codes） |
| `web.py` | web/e2e 入口 |

### 1.2 `app/core` — 基礎設施（21 py）
`config.py`（fail-closed env）、`db.py`（async session）、`security.py`、`passwords.py`、`phone.py`、`totp.py`、`token_revocation.py`、`admin_cookies.py`、`rate_limit.py`、`cooldown.py`、`middleware.py`（TrustedHost→CORS→SecurityHeaders→BodySize）、`client_ip.py`、`hk_bounds.py`、`region.py`、`service_area.py`、`money.py`、`logging.py`、`masking.py`、`exceptions.py`、`deps.py`。

### 1.3 `app/models` — DB 表（13 py）
`user.py`（乘客／司機／admin）、`admin.py`、`fleet.py`、`licence.py`、`order_event.py`、`dispute.py`、`fixed_offer.py`、`prebooking.py`、`recurring.py`、`premium.py`、`driver_notification.py`。

### 1.4 `app/services` — 業務邏輯（48 py）
| 子目錄 | 功能 |
|---|---|
| `auth/` | account、identity、OTP、password、phone binding/reverify、refresh |
| `admin/` | admin auth/refresh、analytics、audit、dispute、search、account 管理 |
| `order/` | order service、state machine、fare calculator、geo service（Redis GEO）、grab service、event/trip service、prebooking |
| `ledger/` | ledger、refund、settlement、settlement confirm |
| `licence/` | licence review、storage |
| `fleet/` | fleet service |
| `driver/` | driver notification |
| `fare/` | fixed fare |
| `receipt/` | receipt freeze |
| `recurring/` | recurring service |
| `infra/` | human、maintenance、notify |

### 1.5 `app/main.py`
App 組合：middleware 順序、router 掛載、lifespan（db/redis）、Sentry init（有 DSN 先 init）、WS 路由。

---

## 2. Flutter App（`mobile/`）

### 2.1 `lib/core`（15 dart）
`app_config.dart`（release fail-closed）、`network/`（cert pinning、Dio、WS custom client）、`theme/`、`ws/`、`errors/` 等。

### 2.2 `lib/data` — repositories（11 dart）
`auth_repository`、`identity_repository`、`order_repository`、`trip_repository`、`driver_repository`、`fleet_repository`、`destination_repository`、`fare_repository`、`admin_repository`、`recurring_ride_repository`、`driver_notification_repository`。全部經 `lib/models` 的 typed DTO 同 API 對接。

### 2.3 `lib/models`（20 dart）
`auth.dart`、`order.dart`、`driver.dart`、`fleet.dart`、`receipt.dart`、`recurring.dart` 等。

### 2.4 `lib/state`（5 dart）
Riverpod providers／controllers（auth、order、nearby filter 等）。

### 2.5 `lib/router`（2 dart）
`app_router.dart`（go_router 組裝 + `_AuthListenable`）、`routing_rules.dart`（Routes + redirect 規則，可獨立測試）。Splash→login/register/phone/otp/forgot/password reset/phone unlock→（passenger 3-tab shell / driver / fleet / admin）。

### 2.6 `lib/features`（39 dart，34 個畫面）
| 子目錄 | 畫面 |
|---|---|
| `auth/` | login、register、phone login、otp、forgot、password reset、change password、phone unlock |
| `passenger/` | passenger shell、request ride、trip history、trip detail、trip tracking、receipt、recurring rides、change destination sheet |
| `driver/` | driver shell、jobs、active trip、earnings、onboarding、preferences、environment、notifications、fixed offers |
| `fleet/` | fleet screen |
| `admin/` | admin shell、fleets、fleet detail、kyc、refunds、settlement |
| `shared/` | account、profile setup、map panel、interrupt sheet、widgets |

---

## 3. Web 管理後台（`admin-web/web/`）

### 3.1 `src/api`（5）
`client.ts`（ApiClient）、`session.ts`、`endpoints.ts`（後台用到的 API paths）。

### 3.2 `src/app`（7）
`App.tsx`（hash router、boot gate、角色 gate）、`AppContext.tsx`、`Shell.tsx`、`RequireRole`。

### 3.3 `src/pages`（24，含測試檔）
Dashboard、Search、Orders、OrderDetail、LiveMap（lazy Leaflet）、Disputes、Kyc、Licences、Analytics、Refunds、Settlement、Fleets、Destinations、FleetDetail、DriverDetail、Audit、Accounts、Login。

### 3.4 `src/components`、`src/i18n`
共用 UI、繁中/英文翻譯。

---

## 4. 模組間互動

### 4.1 三端對 API 的依賴
- **Mobile** → `https://api…`（REST）+ `wss://…`（WS）→ FastAPI → PostgreSQL／Redis。Release build 強制 HTTPS/WSS + SHA-256 pin（fail-closed）。
- **Admin console** → 同一 API，用 admin cookie session；角色 gate 喺 route 層，真安全邊界喺 server（403）。
- **Web/e2e** → `web.py` 提供 browser 入口。

### 4.2 訂單生命週期（核心 interaction chain）
1. Passenger `POST /orders`（request ride）→ Redis GEO index 揾附近司機 → 廣播（WS）。
2. Driver `grab`（兩道閘：cooldown 429 / deposit 423）→ `ACCEPTED`。
3. 兩步到達驗證：`arrival-claim`（GPS）→ `arrival-confirm`（乘客尾 4 位，3 次失敗回 ACCEPTED + dispute）。
4. `start` → 扣 platform trip fee（ledger，冪等）→ METER/FIXED fare。
5. 中途 `change-destination`（重新估價、限次、FIXED→METER）或 `interrupt`（即時終止 + dispute）。
6. `complete` → fare 結算 → ledger（driver wallet）→ settlement（admin 批核）。
7. 每個 transition 都寫 `order_event`（審計、違約罰款、dispute 證據）。

### 4.3 狀態與事件
- 狀態機：`app/services/order/state_machine.py` — PENDING→ACCEPTED→PENDING_ARRIVAL_CONFIRM→IN_PROGRESS→COMPLETED；CANCELLED/INTERRUPTED 為終態。
- 事件：`order_event` 表 + `trip_event`；WS 推送 status 變化；SMS/push 通知係 outbox fan-out（provider 未接）。
- 審計：admin `audit_service` 記金錢事件；搜尋唔逐次審計（刻意）。

### 4.4 錢（ledger）
- `ledger_service` 記所有 driver 錢（trip、fee、adjustment、refund、settlement）。
- 退款：`refund_service` — 全額（終止司機）／部分（批核後司機恢復 ACTIVE）。
- 負餘額合法（arrears）；`ADJUSTMENT` 係單一管理員直接寫入（產品取捨）。

### 4.5 收據
- `receipt_service`：訂單完成後 freeze 一份 JSON snapshot 落 DB（唔重算），idempotent；乘客/mobile/admin 各按權限讀。

### 4.6 週期行程
- `recurring_service` + `recurring.py`：由歷史 trip 建立週期預約，request 時帶 `source_order_id` prefill。

### 4.7 牌照／車隊／目的地
- `licence`（司機牌照 review + storage）、`fleet`、`destinations`（含 premium destination）— admin 管理，影響叫車排序／定價。

### 4.8 安全邊界（2026-10-08 transport hardening）
- TrustedHost（外層）→ CORS → SecurityHeaders → BodySizeLimit → routes。
- WS Origin allowlist（非 allowlist 4403 早拒）；token 可走 native `Authorization: Bearer` header 或 web `?token=` query，兩者都唔入 access log（`--no-access-log` + nginx `$uri`）。
- Admin console：CSP（theme script SHA-256 hash）、X-Frame-Options、Permissions-Policy。
- Mobile release：HTTPS/WSS 強制 + WS SHA-256 pin（pin 係唯一 trust anchor）。

---

## 5. 依賴與未接外部服務
- **未接**：WhatsApp／FCM／Google Maps／真實支付渠道（只記 unpaid／ledger，唔 fake collection）。
- **部署側未完成**：VPS + TLS + 備份 destination + `assetlinks.json` + 首次 deploy drill（見 `docs/DEPLOYMENT_REQUIREMENTS.md`）。
- **有意**：搜尋唔審計、調整單一 admin、`ws_max_connections` per-process（prod `API_WORKERS=1`）。

## 6. 驗證閘（2026-10-08）
- `pytest`（Docker + Postgres/Redis）：1304 passed
- `ruff check .` / `ruff format --check .`：全過（239 files）
- `mypy app`：141 files / 0 errors
- admin：`tsc --noEmit` clean、vitest 97 passed、build OK
- mobile：dart analyze 0 issues、161 VM tests、contract 64 fixtures、`dart format --line-length 100` 0 changed
- `git diff --check` clean；HEAD 未 push。
