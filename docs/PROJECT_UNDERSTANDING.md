# realtaxihk — 專案架構理解摘要

- **日期**：2026-10-01（**本版全面重寫**。上一版停在 `4333f2a`，之後落後 30+
  個 commit；本文所有數字皆為**實跑得出**，非沿用舊值）。
- **方法**：實際讀取全部源碼 + 實跑驗證（pytest、alembic、OpenAPI、
  response-model 審計、ruff、Dart contract/tests/analyzer、瀏覽器 UI verifier）。
- **當前狀態**：本地 HEAD 是 `main` 上最新 commit（**不寫死 hash** —— 之前寫死
  過三次，其後每一次 commit 都會令它變成錯的。要查：`git log --oneline -1`）；
  `origin/main..HEAD` = **有未推 commit**（查：`git rev-list --count origin/main..HEAD`）
  （push 被 PAT 權限擋住，已由用戶豁免；見 `docs/WORK_SUMMARY.md` §5）。
  working tree clean。
- **總覽索引**：`docs/WORK_SUMMARY.md`（每次改動後同步）。

---

## 1. 這是什麼

香港的士配對平台 **realtaxihk.com**，定位是 **Cap. 374D 合規的資訊中介**（不是的士營運商）。
一個 repo 內含**三件完整交付物**：

| 交付物 | 位置 | 規模 | 狀態 |
|---|---|---|---|
| 後端 API | `app/` | **82 paths / 89 operations** · **887 tests** | ✅ 生產就緒 |
| Flutter App | `mobile/` | 58 files, 11,558 LOC | ✅ 三角色完整 |
| Web 管理後台 | `admin-web/web/`（React + Vite）＋ `admin-web/js/`（legacy） | src **12,328 LOC**（ts/tsx，不含測試）· **57 vitest** | ✅ 全部路由通過 |

**重要**：題目要求的「任務 1」與「任務 2」**已經存在且已完成**。以下計畫是「識別餘下可延伸的缺口」，而非從零開發。

---

## 2. 技術棧

### 後端
- FastAPI (async) + SQLAlchemy 2.0 async + asyncpg + PostgreSQL 16/PostGIS + Redis 7 + Alembic
- 金錢一律 `Decimal` 或**字串**（`money_str` 2dp / `meter_str` 1dp `ROUND_HALF_UP`），永不 float；每張單凍結 `tariff_version` 快照
- 錯誤信封 `{code, message, details}`（非 FastAPI 預設 `{"detail": ...}`）
- **`/openapi.json` 已完整**：82 paths，**全部 89 個 operation 都有 `response_model=`**
  （之前只有 1 個）。`response_model=` 是**過濾器**不是註解 —— FastAPI 會靜默丟棄
  模型未聲明的鍵，所以模型必須**由捕獲的 fixture 反推**，不能靠讀 handler。
  驗證：`scripts/verify/audit_response_models.py`（應印 `fixture blocks checked: 68` + `OK`）。

### 前端（Flutter）
- **Riverpod 3** + **Dio** + **go_router 17** + `flutter_secure_storage` + `geolocator` + `google_maps_flutter` + `web_socket_channel`
- **只有 Android**（無 web/ios/linux/macos/windows target）
- 平台限制：本機 `flutter` / `dart run` **無法執行**（Dart native-assets build hook 要
  spawn `cmd.exe`，沙盒具名管道耗盡 → `CreateFile failed 231` / `ERROR_PIPE_BUSY`）。
  這是**環境限制不是契約失敗**。替代路徑：`tool/dart_check.py`（Python 經 LSP 驅動
  analyzer）+ `dart --packages=...` 繞過 dartdev。

### 管理後台（React + Vite）
- `admin-web/web/`：React 18 + Vite 5 + TypeScript + hash routing（`createHashRouter`）
- **`admin-web/js/` 是 legacy**（vanilla JS、phone-OTP 登入），React 版已是
  username/password + TOTP。新功能一律寫 React 版。
- 設計理由：持有支付平台的管理 session，不想引入數百個 install-time 執行 script 的依賴樹。
- 驗證：`npm run typecheck`（`tsc --noEmit`）+ `npx vitest run`（57 tests）+
  `npm run build`。真瀏覽器另有 `admin-web/tool/verify_ui.mjs`。

---

## 3. 分層與規範（新增代碼必須遵循）

### Flutter 分層
```
models/     純資料 + fromJson（嚴格型別，經 core/network/wire.dart）
data/       Repository：一對一映射 API，回傳 typed model
state/      providers.dart（依賴圖）· data_providers.dart（讀寫分離）· auth_controller.dart
features/   UI，按角色分 admin/ driver/ passenger/ fleet/ shared/
router/     routing_rules.dart（純函數，可獨立測試）· app_router.dart
```

**關鍵規範**：
1. **讀寫分離**：畫面讀 = `watch(FutureProvider)`；改動 = repository 呼叫 + `ref.invalidate(...)`。只有一條寫入路徑、一條讀回路徑。
2. **嚴格解碼**：所有 `fromJson` 經 `wire.dart` 的 `asString/asMap/asBool/asEnumList/...`，欄位名必須對應 server 實際輸出；每個 model 的 docstring **引用後端檔案**。
3. **`Decimal` 在 JSON 是字串**（`"12.5"` 非 `12.5`）→ 用 `Money.parse`。這是最容易踩的坑。
4. **註釋寫「為什麼」**，引用 SEC-xx / P0-x / P1-x 編號與後端檔名。
5. UI 文案用**繁體中文**。

### admin-web（React）規範
- 元件：`Shell.tsx` 的 `PageHead`；`primitives.tsx` 的 `Chip`/`Money`/`Row`/`DetailRow`/`Empty`；
  文案取自 `lib/labels.ts`（**不要**在 page 裡硬寫中文標籤）
- 資料載入一律用 `useLoad`（**循序，永不並行** —— 本環境偶爾接受連線後永不回應）
- 對話框用 `useFormDialog` / `useConfirmDialog`；狀態經 `AppProvider` / `useApp()`
- **`#/` 路由表在 `App.tsx` 的 `buildRouter()` 內**，不可搬去 module scope：
  `createHashRouter` 會即時讀 `window.location` 並訂閱 `hashchange`，
  而 `history.replaceState` 不 fire `hashchange`，module scope 會綁死錯 hash
- **金錢在 wire 上一律字串** —— 不要「好心」轉 `number`，否則運算後畫面會出 `NaN`
- `AdminUser` 是 `AdminIdentity` 的 alias（`api/session.ts`），**不要開第二個宣告**

---

## 4. 後端業務模組（App 需對應）

| 模組 | 內容 | 對應前端 |
|---|---|---|
| **A 認證 & KYC** | WhatsApp OTP（sha256、TTL、5 次上限、per-IP+global 限流）、JWT HS256 + 輪換 refresh、司機生命週期 `PENDING_KYC → DEPOSIT_REQUIRED → ACTIVE → SUSPENDED/TERMINATED`、停用即時生效（每 request 查 DB） | login / otp / onboarding |
| **B 訂單 & 派單** | fare 快照凍結入 `fare_json`、Redis `GEOSEARCH` 廣播、`SETNX`+Lua 原子搶單、生命週期 `BROADCASTING → ACCEPTED → DRIVER_ARRIVED → IN_TRIP → COMPLETED/CANCELLED`、$50 失約罰款 | request / jobs / active trip |
| **C-mini 帳本** | append-only `ledger_entries` + `balance_after` 鏈、`with_for_update` 行鎖、reference 冪等 | earnings / ledger |
| **D 實時追蹤** | WS `realtaxi:trip:{order_id}` Pub/Sub、tick 存 PostGIS、server 心跳 | tracking / map |
| **E 車隊 & 結算** | 車隊 = **持牌營運商**（管理員開設，不可自助）、roster 為收費邊界、partial unique 保證一人一 ACTIVE roster、每 (fleet, ISO week) 冪等 | admin fleets + driver fleet |
| **F 管理後台（RBAC）** | 四級 **`AdminRole`**：`SUPPORT < OPERATIONS < FINANCE < SUPER_ADMIN`，以 **rank 比較**（非 set 成員）作 gate、live row 為權威、審計涵蓋金錢／狀態改動 | admin console |
| **G 爭議** | `disputes` + `dispute_messages`、SLA 由 severity 導出、`is_internal` 內部備註、裁決可否動錢 | admin disputes |
| 背景任務 | geo ghost-order sweeper、PDPO 保留清除 | — |

### 4.1 RBAC 的關鍵設計（新增 route 前必讀）

- **rank 比較，不是 set 成員**。`AdminRole` 有 `.rank` 與 `.at_least()`。
  用 set 意味著每加一個角色都要重讀每個列出角色的 tuple，而漏掉一個就是一條開著的 route。
  安全方向（default-deny）必須是**結構性**的。
- **claim 只是建議；live row 才是權威**。`require_role` 每 request 重讀
  `admin_accounts`。信 claim 的話，一個剛被降權的管理員最多可繼續動錢 15 分鐘 ——
  「正是他被降權後最可能行動的那個窗口」。
- **`require_role` 必須是 `def` 不是 `async def`**：它是 **dependency factory**，
  回傳 dependency。寫成協程函數時 `Depends(require_role(R))` 會 evaluate 成協程物件，
  FastAPI 在註冊 route 時就拒絕。內層 guard 命名為 `require_role_guard`，
  令 `tests/test_security_hardening.py`（靠 `dependency.call.__name__` 找 guard）看得見它。
- **`SUPER_ADMIN` 是唯一可改角色者**。「RBAC 唯一的死穴，必須由架構而非紀律守住」。
- **角色需求取決於 request body 時**（爭議裁決），`require_role` 用不上 ——
  用 `deps.py` 的 `live_admin_role(session, principal)`，讀 row 並在 row 缺失時 fail closed。
- **爭議裁決是 per-request 不是 per-route**：`POST /disputes/{id}/resolve` OPERATIONS 可達
  （判斷行為對錯），但**帶 `moves_money` 的裁決額外要求 FINANCE**（職責分離）。

---

## 5. 必須知道的「後端行為怪癖」（前端已 work around，新增代碼勿踩）

1. **`UserRole.DRIVER` 從未被指派**。註冊一律 `PASSENGER`，`POST /drivers/register` 只建 `DriverProfile`。**司機能力完全由 `DriverProfile.status` 決定**。
2. **行程生命週期事件從不發佈**。`TripHub.publish()` 只由 location tick 觸達 → 乘客 socket 學不到「司機搶單了」。所以 `TripTrackingScreen` 在 socket 之外**同時 poll `GET /orders/{id}`**。
3. **`GET /orders?before_id=` 越權回 404 而非 403**（SEC-26，刻意不可區分）。
4. **`/orders/nearby` Redis 故障時 fail-open** 回 `degraded: true` + 空陣列 → UI 應顯示「搜尋中」而非「沒有訂單」。
5. **取消罰款是真錢**：指派司機在 `ACCEPTED`/`DRIVER_ARRIVED` 取消 → 寫 `PENALTY_DEDUCTION`。
6. **`GET /auth/me` 是兩個形狀**。乘客/司機 token → `AuthMeOut`（`role` + `phone_masked`）；
   管理員 token → `AdminMeOut`（**`role`（永遠 `'ADMIN'`）+ `admin_role`（rank）** + `username` + `email_masked`）。
   **`role` 是 principal kind，`admin_role` 才是 rank** —— 兩者混用會令後台在有效登入下
   導覽全空、每頁顯示「沒有存取權限」。`POST /admin/auth/login` 的 `admin` block
   **只有 `admin_role`，沒有 `role`**。
7. **`BusinessRuleError.details["reason"]`** 帶機器可讀的判別碼；**HTTPException 形狀的 403
   會被全域 handler 重組成 `{"code","message","details"}`** → 斷言 `r.json()["details"]["reason"]`，
   永遠不要 `["detail"]`。
8. **`StarletteHTTPException` handler 會建全新 `JSONResponse`，只搬 `exc.headers`**。
   所以 `clear_session_cookies(response)` 之後 `raise HTTPException(...)` 是 **no-op**：
   要令 refusal 同時清 cookie，必須經 `HTTPException(headers=...)` 用
   `app/core/admin_cookies.py` 的 `clear_session_cookie_headers()`。
   而 `set-cookie` 一定要用 **list** 逐條 append，**不可 comma-fold**
   （`expires=Thu, 01 Jan ...` 自己含 comma，client 一 split 兩條都爛）。
9. **`_PUBLIC_PATHS`／route-table 審計只讀宣告式 dependency**。handler 內部做的 guard 它看不見 ——
   要加一個 no-op dependency（見 `require_live_principal`）並補行為 test 證明 guard 真在。
10. **`orders.driver_id` 指向 `driver_profiles.id`，不是 `users.id`**。
11. **`app.routes` 抽不到 `response_model`**：此 FastAPI 版本把 `include_router` 包成
    `_IncludedRouter` 不攤平 —— 行 `app.routes` 只見 21 條且全部 `MISSING`。**要用 `app.openapi()`**。

---

## 6. 實跑驗證結果（2026-10-01）

| 項目 | 結果 |
|---|---|
| `pytest tests/ -q --junit-xml=...` | **887 passed / 0 failed / 0 error / 0 skipped** |
| `alembic heads` | `a1c4e8b7f209 (head)` |
| `alembic check` | 有**既有 baseline drift**（5 組 `uq_*`→`ix_*`、4 個 `VARCHAR`→`Enum`）；看**有無新增**，非「必須 FAIL」 |
| `scripts/verify/audit_response_models.py` | **68 fixture blocks + 89 operations，OK** |
| OpenAPI | **82 paths / 89 operations**，全部有 `response_model=` |
| `ruff check` / `format --check` | clean / clean（全樹） |
| console `tsc` / `vitest` / `build` | clean / **57 passed（8 files）** / 463.18 kB（gzip 142.84 kB） |
| Dart contract verifier | 54 decoded, 0 failures（`dart --packages=… tool/verify_contract.dart`） |
| Dart unit tests | 93 passed, 0 failed（`dart --packages=… tool/run_tests.dart`） |
| Dart 靜態檢查 | 58 files, 0 diagnostics（`python mobile/tool/dart_check.py`） |
| `admin-web/tool/audit_layout.mjs` | **48 renders clean**（{zh-Hant,en} × {light,dark} × 3 寬度） |

> **更正（2026-10-02）**：本文件曾寫「本沙盒跑不到 Dart」。**這是錯的** ——
> `dart <script>` 可以跑，只有 `dart analyze` / `flutter *` 這種**要 spawn 子程序**
> 的才會死在 `ERROR_PIPE_BUSY (231)`。繞過 dartdev 即可：
> `dart --packages=.dart_tool/package_config.json <script.dart>`。
> 故 mobile 的三項驗證（contract / unit / 靜態檢查）**全部實跑過**。
| **admin-web UI verifier（真瀏覽器）** | **PASS** — 全部路由零 error banner、零 console error |

> **測試輸出陷阱**：`[safe-delete]` marker 會注入 stdout 並截斷 pytest 的 summary，
> 令 `EXIT` 與結果都不可信（實測 exit=1 但 872 tests 全 pass）。
> **權威讀法**是 `--junit-xml=` + `ElementTree`（root `<testsuites>`，其
> `<testsuite>` 子節點帶 counts）。

### 環境注意
- 背景 server **無法跨 tool call 存活**（沙盒會回收）→ 跑 UI verifier 必須把
  「起 API + 起 console + 跑 verifier + 收工」放在**同一個命令**內。
- 沙盒擋 `wsl.exe`、`Docker Desktop` **起不著**（已在跑就 OK）。PostGIS fallback：
  把整套 PostgreSQL + OSGeo bundle copy 落 `.tmp/pg`，以**背景 task** 跑
  （前景 `postgres.exe` 會在 Bash call 返回時被回收）。Windows Redis 5 太舊不能配
  redis-py 8.x（會送 `HELLO 3`），要 Redis 7。
- **中斷的 background pytest 會留 orphan DB**（`realtaxihk_t_*`／`realtaxihk_v_*`），
  之後的 test 撞到新 migration 的表時 PDPO purge job 會 error，表面似 code bug。
  清法：`DROP DATABASE "<name>" WITH (FORCE)`。
- **`ruff format --check .` 回報的「N files」不穩定** —— 同一棵樹實測過 140／141／143，
  且與 `ruff check --show-files` 的數目不同。因此文檔**不引用**該數字；
  判準是 **exit code**，不是那個計數。

---

## 7. 已知缺口（真正可開發的空間）

### 高價值
1. **`GET /orders/{id}` 詳情頁在 mobile 缺 UI** — `orderDetailProvider` 已存在，但無對應詳情畫面。
   （後台側已補：`OrderDetailPage`。）
2. **行程歷史無分頁載入** — `OrderPage.nextCursor` 已備好，UI 只顯示第一頁。
3. **Sentry 未接**（config 欄位在，`main.py` 有 init 分支，但 env 空）。
4. ~~**`docs/*.md` 語言未統一**~~ **（2026-10-02 已完成）** — 原本多數以**粵語**撰寫，
   與用戶偏好的書面語不一致。現已將 `docs/` 全部文檔改寫為**書面語（繁體）**，
   包括篇幅最大的 `docs/IN_TRIP_REDESIGN.md`。

> 上一版列的「管理後台的『司機詳情頁』缺失」**已完成** ——
> `admin-web/web/src/pages/DriverDetailPage.tsx` 已有 KYC、存款、車隊、牌照、訂單全貌。

### 業務 backlog（docs 已列，非阻塞）
部分退款、實際打款渠道、`distance_km` 改伺服器算（需 Google Maps API key）、
FCM v1 推送（需 service account RS256）、load test、ledger hash chain、多 worker 壓測。

### 部署件（docs 列為上線前必做，repo 內無檔案）
`deploy/nginx.conf`（TLS + WS `proxy_read_timeout` + access log 剝 `?token=`）、pg_dump 備份 cron。

---

## 8. 方法論教訓（值得記住）

1. **寫「不應該 leak」的測試，一定要用一個真正會 leak 的輸入證明它會 fail。**
   曾用「naive fix」寫邊界測試，該邊界根本沒有 leak，報 0 violations —— 看似 pass，
   實際上測試無效。換成真正 leak 的邊界後立即報 6 / 2 個 violation，測試才站得住。
2. **「共用 model」前要確認型別同語義都一致。** `/auth/logout` 的 `revoked` 是 **int**
   （吊銷數量），`/admin/auth/logout` 的是 **bool**。共用會令 pydantic 靜默把 `False` 轉 `1`。
3. **handler 有時不出某個 key 時，用 `response_model_exclude_unset=True`**，
   不要用 default「補齊」。`GET /drivers/me` 無押金路徑只出 `{required_hkd, is_fulfilled}`，
   測試明確斷言 `"balance_hkd" not in <deposit>`（分辨「從未充值」vs「充過、現為零」）。
   **不可為迎合 schema 而補齊 field —— 夹具與測試才是契約。**
4. **`pathlib.Path('.').rglob('*.py')` 在此沙盒會靜靜地回 0 個檔**（不 raise）。
   寫掃描腳本一定要用**明確子目錄**（`Path('app')`），否則你會以為專案沒有檔案。
5. **不要用多個 pytest plugin／monkeypatch 同時 wrap 同一個函式**（如 `RateLimiter`）。
   wrapper 會疊加，令 call 被計多次，製造出假的「counter reset」，浪費大量時間追不存在的 bug。
   要 instrument 就 clean-room，一次一個。
