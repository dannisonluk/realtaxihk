# realtaxihk — 企業級全量審計報告

**日期**：2026-10-04 · **HEAD**：`be9e758`（+ 本輪未提交修復）· **修訂**：v4（修復輪）
**範圍**：FastAPI 後端（`app/`，105 py 檔）＋ Flutter mobile（`mobile/lib` 68 檔 / 12,913 行）＋ React admin-web（16,494 行 / 49 檔）
**性質**：v1–v3 為 read-only 審計；**v4 起進入修復輪**（改動見 §7，全部附證據）

---

## 0. 修訂記錄

| 版本 | 觸發 | 修正 |
|---|---|---|
| v1 → v2 | 用戶追問「process 有冇真係完成」 | 第一波代理 **6/6 全滅**（非只有 Task 5）；回收 task-1 報告 → 補 AC-01/03/05 |
| v2 → v3 | 批次完成通知 `deleg_a22edbc1` | **v2 誤判「mobile 未交付」是錯的** —— task-0 其實 `completed`，31KB 報告。補入全部 mobile findings |
| **v3 → v4** | 用戶指示繼續 | **① 撤回 M-H-3（子代理誤讀，見 §3.1）**；② 修復 AC-01/02/03/04 + F-03 + AC-08，並補 9 個 SoD 回歸測試；③ **新增 N-1**（rank 制令 FINANCE ⊇ OPERATIONS，與 `AdminRole` docstring 自述的 SoD 相矛盾）；④ 閘門由 2 紅轉全綠 |

**兩次方法錯誤（已修正並寫入 skill）**：
1. 用「log 冇 `final` 行 + mtime 靜止」判代理未完成 → 錯（真相：log 格式不同）。**判斷代理產出必須以「報告檔案存在且內容完整」為準。**
2. **v3 的 M-H-3 我把子代理的結論標為「已核實」—— 實際上我只 grep 到行號，冇檢查該行屬於哪個 class。** 子代理把 `FareEstimateRequest`（請求類）當成 `FareEstimate`（回應類）→ 整條 finding 不成立。**教訓：核實必須確認符號的歸屬（class/scope），唔止確認字符串存在。**

---

## 1. 零報錯驗證 — 閘門實測（v4 為全綠）

| # | 閘門 | v3 | **v4（修復後）** | 證據 |
|---|---|---|---|---|
| 1 | 後端 `pytest -q`（全量） | ✅ 1062 passed / 0 failed（559.15s） | ✅ **1071 passed / 0 failed（701.18s, RC=0）** | junit XML 獨立解析（非靠進程自報）→ **零回歸** |
| 2 | 後端 `pytest`（相關檔） | — | ✅ **149 passed**（152.13s） | `test_fleets` / `test_licence_api` / `test_security_hardening` / `test_admin_settlement_preview` |
| 3 | 新增 SoD 測試 | — | ✅ **9 passed**（6.02s） | `tests/api/test_admin_role_separation.py`（本輪新增） |
| 4 | `ruff check app/ tests/` | ❌ 2 errors（僅 WIP） | ✅ **All checks passed!** | WIP 的 I001 + F401 已一併清理 |
| 5 | `ruff format --check app/ tests/` | ✅ | ✅ **151 files already formatted** | — |
| 6 | `mypy`（2.4.0 == uv.lock 釘版） | ❌ 1 error（F-02） | ✅ **Success: no issues found in 105 source files** | F-02 已修 |
| 7 | Console `tsc --noEmit` | ✅ | ✅ **RC=0** | — |
| 8 | Console `vitest run` | ⚠️ 9 次中 1 次 flake | ✅ **9 files / 69 tests passed**（2.53s） | F-03 已修，無回歸 |
| 9 | Flutter `analyze` | ✅ No issues | ✅ 未變（本輪未改 mobile） | `rt_mobile.log` |
| 10 | Dart `run_tests.dart` / `verify_contract.dart` | ✅ 133 / 56 fixtures | ✅ 未變（本輪未改 mobile） | — |

### 1.1 全量 pytest 補跑（已完成，獨立核實）
本輪改動涉及 `fleets.py` / `admin_licence.py` 的守衛與路由，故重新跑**全量** `pytest -q --junitxml=.tmp/junit_full2.xml`（背景 `proc_5cc1092be74d`，exit 0）。
**結果：1071 passed / 0 failed（701.18s, RC=0）** —— 即 v3 基線 **1062 + 本輪新增 9 個 SoD 測試 = 1071**，**零回歸**。數字由 junit XML 獨立解析確認（非靠進程自報輸出）。

**評級：A− → 修復後 A**（v3 的 B+ 扣分主因是代理執行與 2 個紅閘門，兩者均已閉環）

---

## 2. 代理執行狀態（誠實核對）

**第一波 `deleg_7ede4789`（6 agents）：6/6 FAILED** — 每個 log 含 `HTTP 429: 您已达到请求数限制：1分钟内最多请求15次`。**零有效產出**。

**第二波 `deleg_a22edbc1`（2 agents）：2/2 completed** —— task-0（mobile，`.tmp/audit_mobile_contract.md` 31,277 B）、task-1（admin console，`.tmp/audit_admin_console.md` 25,071 B）。

**回收方法（已修正）**：等 batch-complete 通知 → 讀 `manifest.json` → **再驗報告檔案完整性（size + 行數 + sha256 + 尾段）**。真實 log 格式係 `HH:MM:SS final    | end status=...`，唔可以用 grep `final status=` 推斷。

### 2.1 子代理 finding 的核實狀態（v4 校正）

| 來源 | 數量 | 我親自核實 | 結果 |
|---|---|---|---|
| 我自己讀 code | 後端全部、F-01~F-06、10 閘門 | 100% | ✅ |
| task-0（mobile） | 3 HIGH + 5 MED + 5 LOW | **3 HIGH + 5 MED 全核** | **1 個（M-H-3）核實後推翻；其餘全部成立** |
| task-1（admin） | AC-01/03/05 + AC-06~13 | AC-01/02/03/05/06/07/08/09/10~13 全核 | ✅ 全部成立（AC-07 屬**故意且有註釋**，非疏漏） |

---

## 3. 發現清單（v4 最終狀態）

### 3.1 ⛔ 撤回：M-H-3（子代理誤讀）

原 v3 記為 HIGH：「`FareEstimateOut.discount_percent` / `is_estimate` 從未被讀取；`discountPercent` 每次 decode 靜默變 0，`toJson` 寫返錯值。」

**核實結果：不成立。**
- `mobile/lib/models/fare.dart:95,108,121` 的 `discountPercent` 屬於 **`FareEstimateRequest`（請求類）**，係 client → server 的 body 欄位，唔係回應欄位。`FareEstimate`（回應類，:31-80）**冇呢個欄位，亦冇 `toJson`** → 「靜默變 0 + 寫返錯值」的描述無從發生。
- 回應側嘅兩個欄位**確實有被讀取**：`mobile/lib/models/order.dart:35` `discountPercent: asDouble(json['discount_percent'], …)`、`:39` `isEstimate: asBool(json['is_estimate'], …)`（`FareSnapshot`），並由 `trip_detail_screen.dart:265` 使用（`if (fare.isEstimate)`，註釋明言「driven by the server's own `is_estimate`」）。

**真實殘餘（降為 LOW，新 ID M-L-6）**：`/fare/estimate` 回應由 `FareEstimate`（`fare.dart`）解析，而**該類唔帶** `discount_percent` / `is_estimate` → **報價畫面**（request-ride）無法顯示「此為估價」徽章或折扣率；只有**訂單快照**路徑（`FareSnapshot`）有。屬一致性/UX 小缺口，非資料丟失。

### 3.2 ✅ 本輪已修（附證據）

| ID | 位置 | 修復 | 驗證 |
|---|---|---|---|
| **F-02** | `app/core/phone.py:34` | `bool(value) and …` → `value is not None and …` | `mypy` → **0 error** |
| **AC-01** | `app/api/admin_licence.py:134` | `require_admin` → **`_require_operations`**（牌照批／拒＝合規判斷，與 `admin/drivers.py:266` 的 KYC review 同類） | 新增 3 測試；`test_licence_api` 全過 |
| **AC-02** | `app/api/fleets.py:388` | `require_admin` → **`_require_finance`**（向成員收週費＝動錢，與平台結算 `admin/settlement.py` 一致） | 新增 3 測試 |
| **AC-03** | `app/api/fleets.py:356` | `require_admin` → **`_require_finance`**（與 `:335` 加入成員對稱） | 新增 3 測試 |
| **AC-04** | `app/api/fleets.py:21,43` | 移除 unused `_require_operations` import、修正 I001 → **ruff 由 2 錯轉 0 錯** | `ruff check app/ tests/` → All checks passed |
| **F-03** | `admin-web/web/src/app/App.boot.test.tsx:422-427` | 固定 `setTimeout(30ms)` → **`settleUntil()` 輪詢**（2s 上限，10ms 間隔，失敗時 rethrow 原斷言錯誤） | `vitest` 69/69；新增 helper 有完整 why 註釋 |
| **AC-08** | `admin-web/web/src/pages/AnalyticsPage.tsx:42-56` | 瀏覽器本地時區 → **`Asia/Hong_Kong` 固定 +8**（`hkDay()`），與後端分桶一致 | `tsc` RC=0 |

**依據**：AC-01/02/03/04 的角色語意**並非我自創** —— `app/api/admin/_roles.py` 的 docstring 已明文規定「KYC / driver state → OPERATIONS」「money movement → FINANCE（grant, adjust, **settlement**, refund）」，且 `AdminRole` enum docstring 說明該分層正是為 SoD 而設。原程式碼只係未落實。

**新增回歸測試** `tests/api/test_admin_role_separation.py`（9 passed）—— v3 指出此三個 guard **零測試覆蓋**：
- 每個 class 都assert **拒絕（403）+ 正向對照**（該角色的 403 必須可歸因於「角色」，而非路徑打錯或 fixture 缺失）。
- 守衛在 handler 之前執行，故無需任何 fixture row。
- `test_add_and_remove_share_a_guard` 專門 pin 住 AC-03 的對稱性。

### 3.3 🔴 新增發現 N-1（HIGH，架構級）

**Rank 制令 FINANCE 成為 OPERATIONS 的超集 → `AdminRole` docstring 自述的職責分離失效。**

- `app/core/deps.py:449`：`if not row.admin_role.at_least(minimum)` —— **rank 比較**。
- `app/models/admin.py:94-99`：`SUPPORT: 0, OPERATIONS: 1, FINANCE: 2, SUPER_ADMIN: 3`。
- 後果：`_require_operations` 對 **FINANCE 亦放行** → 一個 FINANCE 帳號可以**既批出司機專業牌照（KYC），又啟動收費／搬錢**。
- 但 `app/models/admin.py:61-63` 明文寫：「`OPERATIONS` and `FINANCE` are apart because KYC must not be loosened by whoever benefits from more drivers being online, **and money must not be moved by whoever approved the paperwork**. Standard separation of duties.」
- **實作與自述意圖直接矛盾。** rank 制的防護方向（「新角色加在最底層就上唔到去」）是對的，但它同時把**同層的橫向分離**吃掉。
- **已 pin 住**：`test_finance_outranks_operations_on_a_licence_decision`（現時通過，即矛盾存在）。
- **修法選項**（需 owner 拍板）：(a) 接受階層、修正 docstring（放棄 KYC/財務分離）；(b) 對「互相排斥」的兩個角色改用**集合**（`at_least` 只保留給 SUPER_ADMIN）；(c) 對 licence/KYC 與動錢路由改為明確 `role in {…}` 白名單。

### 3.4 🔴 HIGH（未修，需 owner 決策）

| ID | 位置 | 問題 |
|---|---|---|
| **AC-05** | `admin-web/serve.py:66`（`SERVE_ROOT = ROOT / "legacy"`） | **默認服務 legacy 舊 console**；React 強化版要 `--dist`。舊版登入（`LoginPage.tsx:8` 自述）＝ `otp/verify` + `role==='ADMIN'`、**無密碼**。兩個 console 打同一 API、較弱者為默認 = 實質出貨風險。**屬部署政策，未經 owner 同意不擅自翻轉** |
| **M-H-1** | `mobile/lib/models/enums.dart:276-283` | REST enum decoder 對未知值 **`throw ArgumentError`**，無 fallback → 後端加一個 enum member，所有渲染該 entity 的畫面硬失敗。與 WS 路徑 `TripUnknownEvent` 的安全降級**哲學相反**。（子代理自評為「deliberate and tested」，但對 mobile 而言舊版本不會即時更新 → 真部署風險） |
| **M-H-2** | `mobile/lib/features/passenger/trip_tracking_screen.dart:322-332` | **斷網時 live-trip 動作（取消）直接丟失** —— `pubspec.yaml` + `lib/` 零 `connectivity_plus` / `outbox`。讀取有 REST fallback，寫入則死 |

### 3.5 MEDIUM

| ID | 位置 | 問題 |
|---|---|---|
| **AC-06** | `admin-web/web/src/components/primitives.tsx:150-160` | Modal 有 `role="dialog"`/`aria-modal`、有 Escape 與 backdrop 關閉，但**無 focus trap、無初始 focus** |
| **M-M-1** | `mobile/lib/data/trip_repository.dart:33` + `trip_tracking_screen.dart:53` | Docstring 聲稱 `TripChannel` 有 reconnect loop，**實際冇** → 一次 socket 抖動令該畫面終身降為 10s REST 輪詢。註釋誤導維護者 |
| **M-M-2** | `app/api/schemas/admin.py:492-493` vs `mobile/lib/models/admin.dart:187-203` | Settlement preview 的 `would_charge_driver_ids` / `would_go_negative_driver_ids` 回傳但客戶端唔讀 → 確認結算前**無法指名**邊個司機會變負結餘 |
| **M-M-3** | `mobile/lib/state/providers.dart` + `data_providers.dart` | 全部 `FutureProvider.family` / `AsyncNotifierProvider.family` **零 `.autoDispose`** → 每個 key 永久佔快取且永不刷新（重訪訂單見 stale snapshot） |
| **M-M-4** | `mobile/lib/core/config/app_config.dart:68-70` | ✅已核實：`apiBaseUrl => _apiOverride.isEmpty ? _devHost : _apiOverride`，`_devHost` = 明文 `http://10.0.2.2:8000`（Android）/ `http://127.0.0.1:8000`。release build 漏 `--dart-define=API_BASE_URL` → 靜默指向 loopback（實害＝release 靜默壞掉；真機上 10.0.2.2/127.0.0.1 即手機自己，故非資料外洩）。**真正問題係不對稱**：同檔 `TURNSTILE_SITE_KEY`（`turnstile.dart:114,186`）與 `GOOGLE_MAPS_API_KEY`（`map_panel.dart:152`）缺值時都**大聲提示重 build**，唯獨 `API_BASE_URL` 靜默回退 |
| **M-M-5** | `mobile/lib/core/config/app_config.dart:82-85` | ✅已核實：`tripSocket()` 以 `base.replace(queryParameters: {'token': accessToken})` 把 access token 放入 URL → 落入 reverse-proxy / server access log。docstring 自辯「browsers and Dart's WebSocket both lack a header channel on connect」——**該限制只對 web target 成立**：Android/IO 的 `IOWebSocketChannel.connect(uri, headers: …)` 支援 header，故對 mobile 主線未必屬被迫取捨（須確認實際用哪個 constructor） |
| **N-2** | `tests/conftest.py:486-487` | `finance` fixture docstring 寫「FINANCE — money movement, **but not KYC**」，與 rank 實作不符（見 N-1）。屬文檔錯誤，但係維護者理解權限模型的入口 |

### 3.6 LOW

| ID | 位置 | 問題 |
|---|---|---|
| **AC-07** | `app/services/admin/search_service.py:135,163-164` | phone/plate 用 leading-wildcard `LIKE '%…%'`。**注意：同檔註釋已解釋為何必須如此**（phone 存 E.164 帶國碼、plate 帶分隔符）→ **屬故意取捨**，非疏漏；建議以 `pg_trgm` GIN index 緩解 |
| **AC-09** | `primitives.tsx:205-207` | 金額用 JS number `toFixed(2)`；上游若把 decimal string parse 成 Number，極大額可能帶 float 誤差 |
| **M-L-1** | `driver_active_trip_screen.dart:11-12`；`trip_tracking_screen.dart:9` | 兩個 feature 畫面直接 import `data/*_repository.dart`，繞過其餘全部 feature 使用的 `state/` 層 |
| **M-L-2** | `app/api/schemas/order.py:105` vs `mobile/lib/models/ledger.dart:45` | `next_cursor` 後端 `str \| None`、客戶端 `int?`；只因 `asIntOrNull` 寬鬆解析才可行 |
| **M-L-3** | `mobile/lib/router/app_router.dart:168-217` | `/driver/*` 無 client role guard；乘客 deep-link 可達畫面並收 403（server 端執法成立，屬 UX 缺口） |
| **M-L-4** | `mobile/lib/models/admin.dart:79` | `json['is_fulfilled'] as bool? ?? false` 繞過 `asBool` helper |
| **M-L-5** | `app/api/fleets.py` | Fleet mutation guard 跨模組 import 私有 `_` 前綴符號；mobile admin UI 不檢查 `admin_role` → 非 finance admin 見到會 403 的按鈕 |
| **M-L-6** | `mobile/lib/models/fare.dart:31-80` | **（M-H-3 撤回後的殘餘）** `/fare/estimate` 回應模型不帶 `discount_percent` / `is_estimate` → 報價畫面無法顯示估價徽章；訂單快照路徑（`order.dart:35,39`）正常 |
| **F-05** | `new/logo-en.jfif`（1,376,210 B）+ `new/logo-zh.jfif`（1,494,738 B） | 2.87MB untracked 二進位混入 repo；`.gitignore` 無 `new/` |
| **F-06** | 文檔漂移 | `DEPLOYMENT_REQUIREMENTS.md:98`（flutter 不能跑 — 反證）、`:244`（184 vs 174 files）、`:253`（§5 7 項 vs 實 3 項）；`STRUCTURE_REVIEW.md:119-120`（97 passed/54 fixtures vs 實 133/56） |

### 3.7 INFO（已核實）
- **AC-10~13**：`presign_download` 無 ownership check（靠 caller 授權）；TOTP enrol 無角色閘（設計如此）；preview 審計 best-effort；access token 在 sessionStorage（XSS 可讀，refresh 用 HttpOnly + CSRF double-submit，全 app 零 `dangerouslySetInnerHTML`）。
- **M-I-2**：mobile 測試為純 Dart harness（非 `flutter_test`）—— 133 個真行為斷言，但零 widget/golden 測試。
- **關於 F-02**：mobile 代理把它標為「behaviourally inert / clarity-only」—— runtime 角度正確，但佢唔知 CI 有 mypy job。由閘門角度該改動係必要的。
- **核實 AC-07 時發現的自我更正**：`tests/api/test_fleets.py:122` 寫「The fleet run (`/fleets/{id}/settlement/run`) is **not gated**」—— 我一度誤讀為「RBAC 無守衛」。細讀後確認**該句講的是 confirm_token 雙重確認**（只有平台全量 run 需要），與授權無關。**故 AC-02 的修復不與該註釋衝突。**

---

## 4. 五大維度評級（v4）

| 維度 | v3 | **v4** | 依據 |
|---|---|---|---|
| ① 全量掃描與零報錯驗證 | B+ | **A** | 修復後 ruff / format / mypy / pytest（相關+新增）/ tsc / vitest **全綠**；v3 的兩個紅閘門（WIP lint、mypy）已閉環 |
| ② 系統設計／代碼質量／項目結構 | A | **A** | 分層清晰、DI 一致、無 raw SQL、i18n parity 869=869、契約測試。**新扣分＝N-1（rank 制吃掉橫向 SoD，與自身 docstring 矛盾）** |
| ③ 商業邏輯與法規防禦線 | A | **A** | Cap. 374D 資訊中介定位齊全（13 處）；車費表對照運輸署**完全正確**；金額 ROUND_HALF_UP 統一；帳本 append-only + balance chain + 命名空間隔離；下單凍結 JSON snapshot。**v3 的扣分（H-3）已證實不存在，本維度回正** |
| ④ 安全設計與數據隱私 | A− | **A−** | 無 SQL 注入、LIKE 已 escape、admin 路由全部經 DB 重驗、XSS 姿態乾淨、token 用 Keystore、零 token/PII logging、零硬編碼密鑰。**三個 SoD 缺口（AC-01/02/03）已修**；扣分＝N-1（FINANCE ⊇ OPERATIONS）+ AC-05（默認服務弱 console）+ M-M-4（明文回退）+ M-M-5（token in URL） |
| ⑤ 高併發／高頻交易／吞吐量 | A− | **A−** | Redis Lua 原子搶單、`with_for_update` 10 處、DB pool 配置、per-ISO-week 冪等、confirm_token 綁定 period+fee。扣分＝AC-07 熱端點 seq scan + M-M-3 無界快取 |

---

## 5. RBAC Matrix（v4 實測）

語意：`require_admin` = 任何已認證 admin（SUPPORT+）＝**讀取門檻**；`_require_operations` = OPERATIONS+；`_require_finance` = FINANCE+；`_require_super` = SUPER_ADMIN。層級：SUPPORT(0) < OPERATIONS(1) < FINANCE(2) < SUPER_ADMIN(3)。

**✅ v4 已修正 3 處**（v3 的錯配）：
- `POST /admin/licence/submissions/{id}/decide` → **`_require_operations`** ✅（AC-01）
- `POST /admin/fleets/{id}/settlement/run` → **`_require_finance`** ✅（AC-02）
- `DELETE /admin/fleets/{id}/members/{id}` → **`_require_finance`** ✅（AC-03，與 POST 對稱）

**⚠️ 未提交改動（AC-04，v3 誤判為「疑似非故意」）**：`POST /admin/fleets`、`PATCH /admin/fleets/{id}`、`POST /admin/fleets/{id}/members` 已改 `_require_finance`。**v4 判定：該改動方向合理**（車隊設定含 `weekly_fee` 折扣＝動錢；roster 變動決定誰被收費），與 `_roles.py` 的「money movement → FINANCE」一致，**保留**，並已把 `remove_member` 一併對齊。

**🔴 語意缺陷（N-1）**：因 rank 制，`_require_operations` 對 FINANCE 亦放行 → OPERATIONS / FINANCE 之間**冇橫向分離**。

**✅ 其餘端點全部正確**，包括最佳範式 `POST /admin/disputes/{id}/resolve`。

---

## 6. 契約 Parity Matrix

**✗ 2 個欄位在報價路徑被丟棄**：`FareEstimateOut.discount_percent`、`is_estimate`（M-L-6，**已由 HIGH 降為 LOW**；訂單快照路徑正常）；`SettlementPreviewOut.would_charge_driver_ids` + `would_go_negative_driver_ids`（M-M-2）。
**⚠ 2 個形狀不一致**：`LedgerPageOut.next_cursor`（str vs int?）、`DepositGrant.is_fulfilled`（raw cast）。
**✅ 其餘全部 parity**，包括全部 money 欄位（`Decimal` → JSON **string** 2dp → Dart `Money`，**端到端 decimal-safe，永不經 double**）。

---

## 7. 修復輪改動清單（v4）

| 檔案 | 改動 |
|---|---|
| `app/core/phone.py` | F-02：`is not None`（mypy） |
| `app/api/admin_licence.py` | AC-01：import `_require_operations`；decide guard |
| `app/api/fleets.py` | AC-02/03/04：settlement/run + remove_member → `_require_finance`；移除 unused import；I001 |
| `admin-web/web/src/app/App.boot.test.tsx` | F-03：新增 `settleUntil()` 輪詢；live-map 測試改用 |
| `admin-web/web/src/pages/AnalyticsPage.tsx` | AC-08：`hkDay()` 固定 `Asia/Hong_Kong` +8 |
| `tests/api/test_admin_role_separation.py` | **新增**（9 tests）：AC-01/02/03 的拒絕 + 正向對照；N-1 的階層 pin |

**未改動任何**：`app/services/**`、`app/models/**`、`app/core/deps.py`、mobile 任何檔案（N-1 的修法需 owner 決策）。

---

## 8. 建議行動次序（v4）

**已完成（v4 + 2026-10-05）**：F-02 ✅、AC-01 ✅、AC-02 ✅、AC-03 ✅、AC-04 ✅、F-03 ✅、AC-08 ✅、N-1 ✅（owner 決定：接受 rank hierarchy，docstring 已改）、N-2 ✅（conftest docstring 已同步）、M-M-4 ✅（release fail-closed）、M-M-1 ✅（改掉誤導 docstring）、NEW-18 ✅（email 不再 early write）、NEW-29 ✅（刪死 APP_HOST / app_host/app_port fields）、NEW-30 ✅（turnstile site key comment 已修正）

**待 owner 拍板**：
1. **AC-05**：`serve.py` 默認翻轉為 `web/dist`？屬部署政策。

**可直接做**：
3. **M-H-2**：live-trip 寫入加 durable outbox（idempotency token + replay on reconnect）。
4. **M-H-1**：為 REST enum 加 `unknown` sentinel，或加 schema-enum-growth 的 CI decode-guard。
5. **M-M-4**：release build 缺 `--dart-define` 時改為**硬失敗**（fail-closed），唔好靜默回退明文。
6. **M-M-1 / M-M-3 / M-L-6 / N-2**：修 WS reconnect（或改掉誤導 docstring）；加 `.autoDispose`；`FareEstimate` 補讀兩欄位；修正 conftest docstring。
7. **F-05 / F-06**：清理 untracked 二進位；校正文檔漂移。

---

## 9. 限制聲明

- **後端**：核心模組逐行通讀（money/fare/ledger/settlement/order/trip/grab/state_machine/deps/security/config/db/middleware/rate_limit/exceptions/token_revocation/ws/orders/fare/models + admin 全線 34 檔 / 8,964 行）。第一波 6 代理全滅（429），涵蓋面由人工通讀 + 第二波補齊。
- **Admin console**：16,494 行 / 49 檔（代理完成，gate 輸出已核實；AC-06~13 由我逐項讀碼核實）。
- **Mobile**：`mobile/lib` 68 檔 / 12,913 行。**本輪逐項核實 3 個 HIGH + 3 個關鍵 MEDIUM**：M-H-3 **推翻**；M-H-1 / M-H-2 / M-M-1 / M-M-2 / M-M-3 **全部成立**。證據：`enums.dart:276-283` REST enum 硬 `throw`；`pubspec.yaml` + `lib/` **零** `connectivity_plus`/`outbox`；`trip_repository.dart:33` docstring 自稱「[TripChannel]'s own reconnect loop」而**全檔零 reconnect 實作**（grep `reconnect` 只中該註釋行本身）；`admin.dart:169-216` 只讀 `would_charge`/`would_go_negative` 兩個 **count**，唔讀 `admin.py:492-493` 的 `*_driver_ids` **列表**；`mobile/lib/state/` **零** `autoDispose`。
  另已核實 **M-M-4**（`app_config.dart:68-70` 靜默回退明文 loopback，而**同檔其他 API key 缺值時會大聲報錯** —— 屬不對稱）與 **M-M-5**（`tripSocket()` 把 token 放入 query string；docstring 的「無 header 通道」理由只對 web target 成立）。
  **仍未獨立核實（僅子代理來源，動工前須自驗）**：M-L-1~5。
- **未跑**：`npm run build`（v3 曾跑：101 modules / 1.36s）；mobile `analyze` / `run_tests.dart` / `verify_contract.dart`（本輪未改 mobile，沿用 v3 結果）。
- **未啟停任何服務**（除 `docker compose up -d db redis` 拉起審計所需 DB stack，已確認 healthy）。
- **文檔產物**：`.tmp/audit_mobile_contract.md`（31KB）、`.tmp/audit_admin_console.md`（25KB）為代理原始報告。
