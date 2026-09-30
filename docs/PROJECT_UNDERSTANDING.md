# realtaxihk — 專案架構理解摘要

- **日期**：2026-09-30
- **方法**：實際讀取全部源碼 + 實跑驗證（pytest、alembic、prod_boot_drill、Dart contract/tests/analyzer、瀏覽器 UI verifier），非只讀文檔。
- **當前狀態**：`origin/main` 已同步，working tree clean，HEAD = `446b7ee`。

---

## 1. 這是什麼

香港的士配對平台 **realtaxihk.com**，定位是 **Cap. 374D 合規的資訊中介**（不是的士營運商）。
一個 repo 內含**三件完整交付物**：

| 交付物 | 位置 | 規模 | 狀態 |
|---|---|---|---|
| 後端 API | `app/` | 38 endpoints, 221 tests | ✅ 生產就緒 |
| Flutter App | `mobile/` | 54 files, 8,645 LOC | ✅ 三角色完整 |
| Web 管理後台 | `admin-web/` | 10 modules, 2,513 LOC | ✅ 全部路由通過 |

**重要**：題目要求的「任務 1」與「任務 2」**已經存在且已完成**。以下計畫是「識別餘下可延伸的缺口」，而非從零開發。

---

## 2. 技術棧

### 後端
- FastAPI (async) + SQLAlchemy 2.0 async + PostgreSQL 16/PostGIS + Redis 7 + Alembic
- 金錢一律 `Decimal`，永不 float；每張單凍結 `tariff_version` 快照
- 錯誤信封 `{code, message, details}`（非 FastAPI 預設 `{"detail": ...}`）
- 38 條 path，`/openapi.json` 幾乎不帶 schema（response 全是手砌 dict）→ **前端 model 是「假設」，必須用真 fixture 驗證**

### 前端（Flutter）
- **Riverpod 3** + **Dio** + **go_router 17** + `flutter_secure_storage` + `geolocator` + `google_maps_flutter` + `web_socket_channel`
- **只有 Android**（無 web/ios/linux/macos/windows target）
- 平台限制：本機 `flutter` / `dart analyze` **無法執行**（Dart VM spawn pipe 失敗，Windows `ERROR_PIPE_BUSY` 231）→ 靠 `tool/dart_check.py`（Python 經 LSP 驅動 analyzer）+ `dart --packages=...` 繞過 dartdev

### 管理後台（零建置 SPA）
- **無 bundler / 無 package.json / 無 node_modules**。ES module + hash routing
- 唯一依賴是開發用的 playwright（跑 UI verifier）
- 設計理由：持有支付平台的管理 session，不想引入數百個 install-time 執行 script 的依賴樹

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
2. **嚴格解碼**：所有 `fromJson` 經 `wire.dart` 的 `asString/asMap/asBool/asEnumList/...`，欄位名必須對應 server 實際輸出；每個 model 的 docstring **引用後端檔案**（例如 `order_out() in app/services/order_service.py`）。
3. **`Decimal` 在 JSON 是字串**（`"12.5"` 非 `12.5`）→ 用 `Money.parse`。這是最容易踩的坑。
4. **註釋寫「為什麼」**，引用 SEC-xx / P0-x / P1-x 編號與後端檔名。
5. UI 文案用**繁體中文**。

### admin-web 規範
- `dom.js` 提供 `el/mount/toast`；**刻意沒有 `innerHTML` 出口**，伺服器字串一律 `textContent`
- hash routing + render token（慢回應不可覆蓋新導覽）
- `api.js` 是 endpoint wrapper 集中層；view 只組 DOM
- boot 順序：無 session → login；有 session → **先 `GET /auth/me` 驗證**才渲染 shell（不信快取的 role）

---

## 4. 後端業務模組（App 需對應）

| 模組 | 內容 | 對應前端 |
|---|---|---|
| **A 認證 & KYC** | WhatsApp OTP（sha256、TTL、5 次上限、per-IP+global 限流）、JWT HS256 + 輪換 refresh、司機生命週期 `PENDING_KYC → DEPOSIT_REQUIRED → ACTIVE → SUSPENDED/TERMINATED`、停用即時生效（每 request 查 DB） | login / otp / onboarding |
| **B 訂單 & 派單** | fare 快照凍結入 `fare_json`、Redis `GEOSEARCH` 廣播、`SETNX`+Lua 原子搶單、生命週期 `BROADCASTING → ACCEPTED → DRIVER_ARRIVED → IN_TRIP → COMPLETED/CANCELLED`、$50 失約罰款 | request / jobs / active trip |
| **C-mini 帳本** | append-only `ledger_entries` + `balance_after` 鏈、`with_for_update` 行鎖、reference 冪等 | earnings / ledger |
| **D 實時追蹤** | WS `realtaxi:trip:{order_id}` Pub/Sub、tick 存 PostGIS、server 心跳 | tracking / map |
| **E 車隊 & 結算** | 車隊 = **持牌營運商**（管理員開設，不可自助）、roster 為收費邊界、partial unique 保證一人一 ACTIVE roster、每 (fleet, ISO week) 冪等 | admin fleets + driver fleet |
| 背景任務 | geo ghost-order sweeper、PDPO 保留清除 | — |

---

## 5. 必須知道的「後端行為怪癖」（前端已 work around，新增代碼勿踩）

1. **`UserRole.DRIVER` 從未被指派**。註冊一律 `PASSENGER`，`POST /drivers/register` 只建 `DriverProfile`。**司機能力完全由 `DriverProfile.status` 決定**。所以路由只分 admin / 非 admin，司機模式由 account 頁進入。
2. **行程生命週期事件從不發佈**。`TripHub.publish()` 只由 location tick 觸達 → 乘客 socket 學不到「司機搶單了」。所以 `TripTrackingScreen` 在 socket 之外**同時 poll `GET /orders/{id}`**。
3. **`GET /orders?before_id=` 越權回 404 而非 403**（SEC-26，刻意不可區分）。
4. **`/orders/nearby` Redis 故障時 fail-open** 回 `degraded: true` + 空陣列 → UI 應顯示「搜尋中」而非「沒有訂單」。
5. **取消罰款是真錢**：指派司機在 `ACCEPTED`/`DRIVER_ARRIVED` 取消 → 寫 `PENALTY_DEDUCTION`。

---

## 6. 實跑驗證結果（2026-09-30，Docker 運行中）

| 項目 | 結果 |
|---|---|
| `pytest -q` | **221 passed** (81.6s) |
| `alembic current` | `d5b2f8a1c430 (head)` |
| `alembic check` | **No new upgrade operations detected**（model ↔ DB 零 drift） |
| `scripts/prod_boot_drill.py` | **7/7 fail-fast 情境正確** |
| `gen_mobile_fixtures.py` | 對 live API 重抓，**55 fixtures** |
| Dart contract verifier | **54 decoded, 0 failures** |
| Dart unit tests | **92 passed, 0 failed** |
| LSP analyzer | **56 files, 0 diagnostics** |
| `ruff check` / `format --check` | clean / 69 files |
| **admin-web UI verifier（真瀏覽器）** | **PASS** — 全部 6 路由零 error banner、零 console error、零失敗 API 呼叫 |

### 環境注意
- 背景 server **無法跨 tool call 存活**（沙盒會回收）→ 跑 UI verifier 必須把「起 API + 起 console + 跑 verifier + 收工」放在**同一個命令**內。
- 沙盒擋 `wsl.exe` → Docker 需用戶自行開（本次已開）。

---

## 7. 已知缺口（真正可開發的空間）

### 高價值
1. **`GET /orders/{id}` 詳情頁在 mobile 缺 UI** — `orderDetailProvider` 已存在，但無對應詳情畫面。
2. **行程歷史無分頁載入** — `OrderPage.nextCursor` 已備好，UI 只顯示第一頁（`data_providers.dart` 的 admin 佇列同樣只取一頁）。
3. **管理後台的「司機詳情頁」缺失** — KYC 佇列可審核，但無單一司機的完整檔案檢視。
4. **Sentry 未接**（config 欄位在，`main.py:63` 有 init 分支，但 env 空）。

### 業務 backlog（docs 已列，非阻塞）
部分退款、實際打款渠道、`distance_km` 改伺服器算（需 Google Maps API key）、FCM v1 推送（需 service account RS256）、load test、ledger hash chain、多 worker 壓測。

### 部署件（docs 列為上線前必做，repo 內無檔案）
`deploy/nginx.conf`（TLS + WS `proxy_read_timeout` + access log 剝 `?token=`）、pg_dump 備份 cron。
