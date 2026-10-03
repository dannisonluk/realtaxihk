# 開發者指南 / Development Guide

> **這份文檔的定位**：給**要改這個 repo 的人**。它回答三個問題：
> ①新增代碼要遵守什麼規範；②有哪些**非顯而易見的後端行為**會令你寫錯；
> ③這個沙盒有哪些限制。
>
> 系統**怎樣運作**（一程車的流程、錢在哪裡被改動、不變式）請讀
> [`ARCHITECTURE.md`](ARCHITECTURE.md)。規模與交付物清單見
> [`../README.md`](../README.md)。未做項與憑證阻塞見
> [`WORK_SUMMARY.md`](WORK_SUMMARY.md) §4。
>
> 本文所有數字皆為**實跑得出**，非沿用舊值。

---

## 1. 分層與規範（新增代碼必須遵循）

### 後端（`app/`）

```
app/api/           HTTP 層——只做 I/O、驗證、錯誤映射。業務規則不寫在這裡。
app/api/admin/     管理後台路由，一個資源一個模組（drivers / settlement / refunds /
                   audit / accounts / orders / disputes / search / live；
                   `_roles.py` 放角色閘門，`_shared.py` 放跨資源的 helper）
app/api/schemas/   Pydantic 響應模型（見 §3.1 的警告）
app/services/      領域邏輯——所有業務不變式住在這裡，按 bounded context 分組
                   （auth / licence / order / ledger / fleet / admin / infra）
app/models/        SQLAlchemy 2.0 declarative，按 bounded context 拆包
app/core/          橫切關注點——config、money、deps（RBAC）、rate_limit、client_ip、
                   hk_bounds、exceptions、logging、security、totp
app/main.py        App 組裝、lifespan 背景任務、Sentry
```

- 錯誤信封一律 `{code, message, details}`（**不是** FastAPI 預設的 `{"detail": …}`）。
- 金錢一律 `Decimal` 或**字串**（`money_str` 2dp / `meter_str` 1dp，
  全 `ROUND_HALF_UP`），**永不 float**。每張單凍結 `tariff_version` 快照。
- **命名**：`app/api/` 一律 `admin_x`（沒有 `x_admin` 後綴）。

### Flutter（`mobile/`）

```
models/     純資料 + fromJson（嚴格型別，經 core/network/wire.dart）
data/       Repository：一對一映射 API，回傳 typed model
state/      providers.dart（依賴圖）· data_providers.dart（讀寫分離）· auth_controller.dart
features/   UI，按角色分 admin/ driver/ passenger/ fleet/ shared/
router/     routing_rules.dart（純函數，可獨立測試）· app_router.dart
```

1. **讀寫分離**：畫面讀 = `watch(FutureProvider)`；改動 = repository 呼叫 +
   `ref.invalidate(...)`。只有一條寫入路徑、一條讀回路徑。
2. **嚴格解碼**：所有 `fromJson` 經 `wire.dart` 的 `asString/asMap/asBool/asEnumList/…`，
   欄位名必須對應 server 實際輸出；每個 model 的 docstring **引用後端檔案**。
3. **`Decimal` 在 JSON 是字串**（`"12.5"` 非 `12.5`）→ 用 `Money.parse`。
   **這是最容易踩的坑。** 絕不用 double 相減（`500.00 - 499.70` 會渲染成
   `HK$0.30000000000001137`），一律走 `Money.minus` / `Money.fromCents`。
4. **註釋寫「為什麼」**，引用 SEC-xx / P0-x / P1-x 編號與後端檔名。
5. UI 文案用**繁體中文**。

### admin-web（React）

- 元件：`Shell.tsx` 的 `PageHead`；`primitives.tsx` 的 `Chip`/`Money`/`Row`/`DetailRow`/`Empty`；
  文案取自 `lib/labels.ts`（**不要**在 page 裡硬寫中文標籤）。
- 資料載入一律用 `useLoad`（**循序，永不並行** —— 本環境偶爾接受連線後永不回應）。
- 對話框用 `useFormDialog` / `useConfirmDialog`；狀態經 `AppProvider` / `useApp()`。
- **`#/` 路由表在 `App.tsx` 的 `buildRouter()` 內**，不可搬去 module scope：
  `createHashRouter` 會即時讀 `window.location` 並訂閱 `hashchange`，
  而 `history.replaceState` 不 fire `hashchange`，module scope 會綁死錯 hash。
- **金錢在 wire 上一律字串** —— 不要「好心」轉 `number`，否則運算後畫面會出 `NaN`。
- `AdminUser` 是 `AdminIdentity` 的 alias（`api/session.ts`），**不要開第二個宣告**。

---

## 2. 後端業務模組對照

| 模組 | 內容 | 對應前端 |
|---|---|---|
| **A 認證 & KYC** | WhatsApp OTP（sha256、TTL、5 次上限、per-IP+global 限流）、JWT HS256 + 輪換 refresh、司機生命週期 `PENDING_KYC → DEPOSIT_REQUIRED → ACTIVE → SUSPENDED/TERMINATED`、停用即時生效（每 request 查 DB） | login / otp / onboarding |
| **B 訂單 & 派單** | fare 快照凍結入 `fare_json`、Redis `GEOSEARCH` 廣播、`SETNX`+Lua 原子搶單、生命週期 `BROADCASTING → ACCEPTED → DRIVER_ARRIVED → IN_TRIP → COMPLETED/CANCELLED`、$50 失約罰款 | request / jobs / active trip |
| **C-mini 帳本** | append-only `ledger_entries` + `balance_after` 鏈、`with_for_update` 行鎖、reference 冪等（**五種 entry type 各有 prefix**） | earnings / ledger |
| **D 實時追蹤** | WS `realtaxi:trip:{order_id}` Pub/Sub、tick 存 PostGIS、server 心跳 | tracking / map |
| **E 車隊 & 結算** | 車隊 = **持牌營運商**（管理員開設，不可自助）、roster 為收費邊界、partial unique 保證一人一 ACTIVE roster、每 (fleet, ISO week) 冪等 | admin fleets + driver fleet |
| **F 管理後台（RBAC）** | 四級 `AdminRole`：`SUPPORT < OPERATIONS < FINANCE < SUPER_ADMIN`，以 **rank 比較**（非 set 成員）作 gate、live row 為權威、審計涵蓋金錢／狀態改動 | admin console |
| **G 爭議** | `disputes` + `dispute_messages`、SLA 由 severity 導出、`is_internal` 內部備註、裁決可否動錢 | admin disputes |
| 背景任務 | geo ghost-order sweeper、PDPO 保留清除 | — |

RBAC 的設計理由（為什麼是四級、為什麼排名比較、為什麼必須 `def` 而非 `async def`）
見 [`ARCHITECTURE.md`](ARCHITECTURE.md) §6。

---

## 3. 必須知道的「後端行為怪癖」

前端已 work around；**新增代碼勿踩**。

1. **`UserRole.DRIVER` 從未被指派**。註冊一律 `PASSENGER`，`POST /drivers/register`
   只建 `DriverProfile`。**司機能力完全由 `DriverProfile.status` 決定**。
2. **行程生命週期事件從不發佈**。`TripHub.publish()` 只由 location tick 觸達 →
   乘客 socket 學不到「司機搶單了」。所以 `TripTrackingScreen` 在 socket 之外
   **同時 poll `GET /orders/{id}`**。
3. **`GET /orders?before_id=` 越權回 404 而非 403**（SEC-26，刻意不可區分）。
4. **`/orders/nearby` Redis 故障時 fail-open** 回 `degraded: true` + 空陣列 →
   UI 應顯示「搜尋中」而非「沒有訂單」。
5. **取消罰款是真錢**：指派司機在 `ACCEPTED`/`DRIVER_ARRIVED` 取消 →
   寫 `PENALTY_DEDUCTION`。
6. **`GET /auth/me` 是兩個形狀**。乘客/司機 token → `AuthMeOut`
   （`role` + `phone_masked`）；管理員 token → `AdminMeOut`（**`role`（永遠 `'ADMIN'`）
   + `admin_role`（rank）** + `username` + `email_masked`）。
   **`role` 是 principal kind，`admin_role` 才是 rank** —— 兩者混用會令後台在有效
   登入下導覽全空、每頁顯示「沒有存取權限」。`POST /admin/auth/login` 的 `admin`
   block **只有 `admin_role`，沒有 `role`**。
7. **`BusinessRuleError.details["reason"]`** 帶機器可讀的判別碼；**HTTPException
   形狀的 403 會被全域 handler 重組成 `{"code","message","details"}`** →
   斷言 `r.json()["details"]["reason"]`，永遠不要 `["detail"]`。
8. **`StarletteHTTPException` handler 會建全新 `JSONResponse`，只搬 `exc.headers`**。
   所以 `clear_session_cookies(response)` 之後 `raise HTTPException(...)` 是 **no-op**：
   要令 refusal 同時清 cookie，必須經 `HTTPException(headers=...)` 用
   `app/core/admin_cookies.py` 的 `clear_session_cookie_headers()`。
   而 `set-cookie` 一定要用 **list** 逐條 append，**不可 comma-fold**
   （`expires=Thu, 01 Jan ...` 自己含 comma，client 一 split 兩條都爛）。
9. **`_PUBLIC_PATHS`／route-table 審計只讀宣告式 dependency**。handler 內部做的
   guard 它看不見 —— 要加一個 no-op dependency 並補行為 test 證明 guard 真在。
10. **`orders.driver_id` 指向 `driver_profiles.id`，不是 `users.id`**。
11. **`app.routes` 抽不到 `response_model`**：此 FastAPI 版本把 `include_router`
    包成 `_IncludedRouter` 不攤平 —— 行 `app.routes` 只見 21 條且全部 `MISSING`。
    **要用 `app.openapi()`**。
12. **`app/api/service_area_route.py` 是 HTTP 端點，`app/core/service_area.py` 是
    強制執行的閘門** —— 兩層不同，名字刻意區分（兩者曾經同名，grep 時極易搞混）。
    `tracking.py` 掛 `/api/v1/drivers`（複數）。

### 3.1 響應模型：`response_model=` 是過濾器，不是註解

FastAPI 會**靜默丟棄**模型沒有宣告的鍵。所以模型一定要**由真實響應的夹具反推**，
不可以靠讀 handler 或類比另一個 model。三個實際踩過的坑：同名 key ≠ 同型別
（`/auth/logout` 的 `revoked` 是 **int**，`/admin/auth/logout` 是 **bool**，共用會令
pydantic 靜默把 `False` 轉 `1`）；handler 有時不出某 key（用
`response_model_exclude_unset=True`，**不要**補 default）；`FareEstimateOut` 是唯一
「金額不是 str」的例外（`distance_km` / `waiting_min` 回顯呼叫方輸入，傳 `Decimal`，
改成 `str` 會令 route 拒絕自己 handler 的輸出 → 500）。

驗證：`scripts/verify/audit_response_models.py` → `fixture blocks checked: 68` + `OK`。

---

## 4. Lint gate

Ruff 是本 repo 唯一的 linter + formatter（取代 flake8/isort/black）。設定在
`pyproject.toml` 的 `[tool.ruff]` —— **明確選規則**而非用預設，所以 ruff 升級
不會靜默改變 gate。

```bash
uv run ruff check .            # lint
uv run ruff check . --fix      # 套用安全自動修復
uv run ruff format .           # 格式化（CI 用 --check）
```

CI 在每次 push/PR 都跑 `ruff check` + `ruff format --check`，**在測試套件之前** ——
風格問題要快速、便宜地失敗。

| 規則組 | 理由 |
|---|---|
| `E` `W` | pycodestyle，基線衛生 |
| `F` | pyflakes —— 抓到過真的重複 import bug（F811） |
| `I` | isort，決定性的 import 順序 |
| `UP` | pyupgrade，現代 py311 慣用法（`datetime.UTC`、f-string） |
| `B` | bugbear，真實 bug 模式（loop var、mutable default、`zip` strictness） |
| `C4` | comprehensions |
| `SIM` | simplify，更平的控制流 |
| `S` | bandit，安全異味 —— subprocess、assert、硬編碼秘密 |
| `PLW` | pylint warnings（例：`subprocess.run` 缺 `check=`） |
| `TRY` | 例外衛生 —— 無意義的 try、被吞掉的例外 |
| `RUF` | ruff 專有 —— unused noqa、zip strict |

刻意的 ignore（在 `pyproject.toml` 有行內註釋）：`B008`（`Depends()` 放 default 就是
FastAPI 慣用法）、`RUF001/002/003`（雙語 CJK 註釋的 ambiguous-unicode 噪音）、
`PLW0603`（`app/core/db.py` 的 lazy Redis singleton 用 `global`，刻意）、
`TRY003`（長 `HTTPException(detail=…)` 是 API 的錯誤風格）、
`UP042`（保留 `(str, Enum)`：`StrEnum` 會改 `str()` 語義）、
`FURB157`（money 保留 `Decimal("…")` 字串建構子以維持精度一致）。

> **`per-file-ignores` 要寫 `"scripts/**"`，不可以寫 `"scripts/*"`** —— glob 的 `*`
> **不跨 `/`**，`scripts/` 分組之後會**靜靜失效**。

> **`ruff check` 乾淨不代表 `ruff format --check` 乾淨** —— 兩者是獨立的 gate，
> lint 規則（E501 等）抓不到引號風格、行合併這類純格式差異。`admin-web/`
> **不在** `extend-exclude` 內，所以前端目錄下的 `.py` 工具腳本同樣受格式門禁約束。

### 沒有型別檢查器（已知缺口）

CI 只跑 `ruff check` + `ruff format --check` + `pytest`。**Ruff 不做型別推論**，
所以參數註解寫錯不會被任何 gate 攔下 —— 只有編輯器（Pylance / pyright）會報。

真實案例：`app/core/money.py` 的四個格式化函式參數原本註解為 `Decimal`，
但函式體一直是 `Decimal(v)`、docstring 也明說接受 int 與 str。於是
`money_str(settings.weekly_fee_hkd)`（`weekly_fee_hkd: int`）在編輯器裡報錯，
而 CI 全綠。修法是讓註解與函式體一致：統一的輸入聯集 `MoneyInput`
（見 `app/core/money.py`，`ratio_str` 與 `refund_service.request` 早就是這個形狀）。

> **寫這一類「轉換型」參數時**：如果函式體做 `Decimal(v)` / `int(v)` / `str(v)`，
> 註解就要寫成它真正接受的聯集。註解比函式體窄不會令呼叫變得不安全，
> 只會令檢查器與程式碼各說各話。建議日後把 pyright 或 mypy 納入 CI
> （見 `STRUCTURE_REVIEW.md` R9）。

---

## 5. 環境限制（本機特有）

- **`flutter` / `dart analyze` 在本沙盒跑不動**（Dart 在 Windows 用具名管道接 child
  stdio → `ERROR_PIPE_BUSY (231)`）。替代路徑：`mobile/tool/dart_check.py`（Python
  經 LSP 驅動 analyzer，同一引擎、同一 `analysis_options.yaml`）＋
  `dart --packages=…` 繞過 dartdev。**CI 是 Linux，沒有這個問題。**
- **背景 server 無法跨 tool call 存活** → 跑瀏覽器 verifier 要把「起 API + 起 console
  + 跑 verifier」放在同一個命令內。
- 其餘沙盒陷阱（proxy 攔 502、`pg_dump` 只在 container 內、`sh -c` 吞反斜線…）
  見 [`WORK_SUMMARY.md`](WORK_SUMMARY.md) §7。

---

## 6. 方法論教訓（這個 repo 反覆交學費換來的）

1. **寫「不應該 leak」的測試，一定要用一個真正會 leak 的輸入證明它會 fail。**
   曾用「naive fix」寫邊界測試，該邊界根本沒有 leak，報 0 violations —— 看似 pass，
   實際上測試無效。換成真正 leak 的邊界後立即報 violation，測試才站得住。
2. **「共用 model」前要確認型別同語義都一致。** `/auth/logout` 的 `revoked` 是 **int**，
   `/admin/auth/logout` 的是 **bool**。共用會令 pydantic 靜默把 `False` 轉 `1`。
3. **handler 有時不出某個 key 時，用 `response_model_exclude_unset=True`**，
   不要用 default「補齊」。`GET /drivers/me` 無押金路徑只出
   `{required_hkd, is_fulfilled}`，測試明確斷言 `"balance_hkd" not in <deposit>`
   （分辨「從未充值」vs「充過、現為零」）。**不可為迎合 schema 而補齊 field。**
4. **`pathlib.Path('.').rglob('*.py')` 在此沙盒會靜靜地回 0 個檔**（不 raise）。
   寫掃描腳本一定要用**明確子目錄**。
5. **不要用多個 pytest plugin／monkeypatch 同時 wrap 同一個函式**（如
   `RateLimiter`）。wrapper 會疊加，令 call 被計多次，製造假的「counter reset」。
6. **「grep 搵唔到」≠「死代碼」。** `Gender.MALE` 靜態零引用但係活的 ——
   `identity_service.py` 用 `Gender(str(value).strip().upper())` **動態構造**。
   判斷 enum 成員死活要同時考慮動態構造路徑。
7. **循環內對「使用者輸入清單」逐項做的守衛，失敗範圍是整個操作，不是該項。**
   `licence_service.submit()` 逐份餵 `attach_document` → 「交多過最低要求反而整筆 400」。
   審查這類白名單要問「誰在迴圈裡呼叫它」。
8. **型別漏一個欄位，可以令測試全綠而產品全壞。** 夹具是人手寫的，它和 bug 可以一致。
   型別必須由**真實響應**反推。
9. **「一樣快取」比「兩份一樣的程式碼」更危險。** SEC-07 的 `client_ip()` 曾有五份
   byte-identical 的副本，任何一份退回 `split(",")[0]` 就會**只讓那一個 endpoint**
   重新打開漏洞。所以它現在是一個 module。
10. **本機綠不代表 CI 綠，只要測試碰到路徑。** CI 是 ubuntu、沙盒是 Windows，
    `Path("C:/x").is_absolute()` 兩邊答案相反。要用 `PurePosixPath` /
    `PureWindowsPath` **兩邊各跑一次**證明，或只斷言平台無關的性質。
