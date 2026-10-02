# 深度程式碼審查報告

> **歷史文件**：本文檔是 2026-10-01 的程式碼審查快照。其中的檔案數、行數與測試數量都是當時的數字，刻意不隨程式碼演進而更新；如需目前數字，請看 `docs/WORK_SUMMARY.md`。

> **📌 這是一份有日期的快照。** 底下所有數字描述的是**審查當下**（2026-10-01
> 早段，`667 tests` / `64 paths` / `69 operations`）的狀態，**不是** repo 現況。
> 現況見 `docs/WORK_SUMMARY.md`（**887 tests / 82 paths / 89 operations /
> 31 vitest**）。`response_model=` 一節（下方 §H-4 的部分）在該次審查後又擴展到
> 全部 89 個 operation。
>
> **範圍**：後端 `app/`（59 個 Python 檔、13,158 行）、Console `admin-web/web/src/`
> （29 個 TS/TSX 檔）。
> **方法**：逐檔閱讀 + 實跑驗證。所有發現都附「如何驗證」，未經實跑的
> 一律標為推測。
> **日期**：2026-10-01

---

## 摘要

| 嚴重度 | 數量 | 狀態 |
|---|---|---|
| **高（真實缺陷，會產生錯誤行為）** | 6 | 全部已修 + 補測試 |
| **中（一致性／可維護性）** | 4 | 全部已修 |
| **低（註釋／文件缺失）** | 6 | 已補註釋 |

**測試**：656 → **667 passed / 0 failed / 0 error / 0 skipped**。
**Lint**：`ruff check` clean、`ruff format --check` clean（102 files）。

---

## 一、高嚴重度：真實缺陷

### H-1 · `code_map` 缺 423，P4 的保證金閘門會回傳無意義的錯誤碼

**檔案**：`app/core/exceptions.py`

`StarletteHTTPException` handler 用一個 `code_map` 把 HTTP status 翻譯成
client 可判讀的 `code`。但該 map **沒有 423**，而 P4 的
`DECISION-3`（保證金負數 → 不能接單）計劃回傳 **423 `DEPOSIT_INSUFFICIENT`**。

**實測結果（修復前）**：

```
/probe423 -> 423 {"code": "HTTP_ERROR", "message": "Request failed.",
                  "details": {"reason": "DEPOSIT_INSUFFICIENT"}}
```

**為甚麼這是真缺陷**（不是理論問題）：`admin-web/web/src/api/client.ts` 的
錯誤處理是**按 `code` 分支**，不是按 `status`。所以 423 會退化為通用的
`HTTP_ERROR`，而 **423 與 403 的區分正是整個設計的重點**：

- `403` = 你無權（重試無用）
- `423` = 「在這個條件為真時不行」（司機補款即可恢復）

一個把兩者混為一談的 console，會叫管理員放棄一件他其實可以解決的事。

**修法**：加入 `HTTP_423_LOCKED → "LOCKED"`。

---

### H-2 · 結構化 `detail` 沒有 `message` 時，真正的原因讀不到

**檔案**：`app/core/exceptions.py`

```python
message = payload.pop("message", "Request failed.")   # 修復前
```

但所有 guard 送出的都是 `{"reason": "..."}` 而**沒有 `message`**。
結果：423 的 `message` 欄位是字面字串 `"Request failed."`，
而 `DEPOSIT_INSUFFICIENT` 靜靜躺在 `details` 裡。

**影響**：`ACCOUNT_UNVERIFIED`、`PHONE_REVERIFY_DUE`、`OUTSIDE_HK` ——
每一個「需要向使用者解釋」的拒絕，在 UI 上都顯示為同一句無用的話。
`exceptions.py` 原本的註釋已經在處理這個問題（把 dict 搬去 `details`
而不是 `str()` 它），但**只做了一半**：機器可讀的部分修好了，
人可讀的部分沒有。

**修法**：`payload.pop("message", None) or payload.get("reason") or "Request failed."`

**驗證**：

```
/probe423 -> 423 {"code": "LOCKED", "message": "DEPOSIT_INSUFFICIENT", ...}
```

---

### H-3 · 結算：一種「錢沒收到」的失敗被誤計為「正常跳過」

**檔案**：`app/services/settlement_service.py`

```python
except BusinessRuleError:
    # Lost a same-reference race — the unique index did its job.
    skipped += 1
```

`LedgerService.append` 會 raise **兩種不同的** `BusinessRuleError`：

1. `"duplicate ledger reference"` —— 真的輸了競態，費用**已收**（冪等）。
2. `"driver deposit account not found"` —— deposit row 在 pre-check 與
   append 之間被刪除。**這張單沒有收到錢。**

兩者都被計入 `skipped`，而 `skipped` 在維運上的讀法是「本來就不用收」
（例如「已收過」或「從未入金」）。所以**平台靜靜漏收費用，而結算報告
看起來完全健康** —— 這正是模組 docstring 裡 SEC-13 想防的那種事，
只是從另一個門進來了。

**修法**：用 `exc.message` 分流；非 duplicate 者計入 `failed` 並記
`logger.exception`（保留 traceback）。

> **這個修法本身有味道**：用字串比對 message 當判別條件是脆弱的。
> 已加註釋說明，並指出任何一方改 message 都要重看這個分支。
> 正解是讓 `LedgerService` raise 一個帶 `code` 的子類別，
> 但那是跨檔案的 API 變更，不應該混在這一輪。

**補充**：`append()` 裡的 `IntegrityError → BusinessRuleError("duplicate ...")`
轉換**在目前呼叫路徑上不可達** —— pre-check 之後沒有 `flush()` 點，
任何並發 insert 會令 pre-check 的 SELECT 直接看到已提交的 row。
它是一個**安全網**而非流程的一部分，已加註釋說明。

---

### H-4 · Console 有三處違反專案自己訂下的「禁止並行請求」規則

**檔案**：`admin-web/web/src/app/AppContext.tsx`、`pages/FleetDetailPage.tsx`、
`api/endpoints.ts`

`app/useLoad.ts` 的 docstring 寫得非常明確：

> Firing a page's independent requests together is the obvious shape, and it is
> what the console did — until it ran on a machine that intermittently accepts a
> connection to the API and then never answers it. Every parallel call is another
> connection that can land on that path... **`Promise.all` is deliberately not
> offered, because using it is the bug described at the top of this file.**

但仍然有三處在用 `Promise.all`：

| 檔案 | 行 | 並行數 |
|---|---|---|
| `app/AppContext.tsx` | 101 | 2（sidebar 徽章計數，**每次載入 shell 都跑**） |
| `pages/FleetDetailPage.tsx` | 69 | 3 |
| `api/endpoints.ts` | 314 | 3（`fleets.detail`） |

**為甚麼是缺陷**：`refreshBadges` 是最嚴重的一個 —— 它在每次掛載 shell
時執行，所以「一個連線被接受但永不回應」的機率被乘以 2，而且它是
**best-effort、失敗被吞掉**，所以症狀會是「側邊欄數字不對」而不是
看得見的錯誤。這正是最難診斷的那種。

**修法**：全部改為 sequential。`FleetDetailPage` 自己的 loader 與
`endpoints.fleets.detail` 是同一組呼叫，兩處都改了。

---

### H-5 · 兩個並行的測試行程會互相刪除對方的 template DB

**檔案**：`tests/conftest.py`

**這是這一輪最難診斷的一個** —— 因為症狀看起來完全像產品缺陷。

`TEMPLATE_DB` 原本是**固定名** `realtaxihk_test_tpl`，而
`_ensure_template()` 的第一個動作是：

```python
await conn.execute(f'DROP DATABASE "{TEMPLATE_DB}" WITH (FORCE)')   # 修復前
```

即**每次 session 開始都先刪掉 template**。當兩個 pytest 行程同時跑
（背景行程與前景行程重疊、CI matrix、分片、或開發者在 CI 跑時自己又跑一次），
後啟動的那個會把先啟動那個的 clone 來源刪掉。受害者拿到的是
**半成品的 schema**，於是出現：

```
sqlalchemy.exc.ProgrammingError: relation "otp_codes" does not exist
  [SQL: UPDATE otp_codes SET created_at = ...]
```

**為甚麼這是真缺陷而不是「不要並行跑就好」**：
- 症狀與真正的原因**沒有因果外觀上的關聯** —— 一個測試在 `otp_codes` 上
  失敗，看起來像 migration 漏了、或 `Base.metadata` 少了表，而不像
  「兩個 pytest 在搶同一個 DB」。
- 它是**間歇性**的：重跑一次就過，於是很容易被當成 flaky 而忽略。
- 代價已付出：這一輪花了不少時間去追一個**不存在**的 schema 缺陷
  （實際檢查 template 才發現 19 張表都在，只是被別的行程刪到一半）。
- **同樣的機制也解釋了 `test_repeated_logins_are_rate_limited` 的
  `[401 × 12]`** —— 那個行程拿到的是殘缺 schema，lockout 計數寫不進去，
  於是永遠只有 401，永遠沒有 429。

**修法**：`TEMPLATE_DB` 加**每個行程唯一的後綴**，並在 session 結束時
drop 自己的 template（`pytest_unconfigure` → `_drop_template`）。
這樣碰撞由「可能發生」變成「不可能發生」，代價是並行時多一個 DB。

**驗證**（這一輪實際做的）：

| 情境 | 結果 |
|---|---|
| 單一行程，乾淨跑完整 suite | **667 passed / 0 / 0 / 0** |
| 兩個行程**同時**跑完整 suite | 兩者都 667 passed / 0 / 0 / 0（見下節） |
| session 結束後檢查殘留 DB | 只剩 `realtaxihk`，無 template 殘留 |

> **順帶清掉兩個殘留 DB**：一個孤兒 per-test DB（`realtaxihk_t_*`，中斷的
> 行程留下）與舊的固定名 template。兩者都是這個缺陷的副產品。

---

### H-6 · 兩個並行的測試行程會互相清空對方的 rate-limit 計數器

**檔案**：`tests/conftest.py`、`app/core/config.py`、`app/main.py`、
`app/services/admin_auth_service.py`

修完 H-5 之後再並行跑一次，**H-5 的症狀全部消失**（沒有 `relation ... does
not exist`），但跑出**另一組**失敗 —— 而且這一組更陰險：

```
test_lockout_does_not_expose_is_active   assert 429 == 401
test_a_correct_password_during_lockout   assert 429 == 401
test_listing_counts_each_fleet_independently
    assert 429 == 200   {"code":"RATE_LIMITED","message":"too many attempts"}
test_exactly_one_service_grab_wins
    assert 429 == 200   {"code":"RATE_LIMITED","message":"too many OTP requests"}
test_rotating_xff_does_not_reset_the_ip_bucket
    assert [200 × N].count(429) >= 3
```

**注意這些失敗是「兩個方向都有」的**：有些測試**多**了 429（本應 200），
有些測試**少**了 429（本應觸發卻沒有）。一個方向有問題可以是產品 bug；
**兩個方向同時出現，只可能是「有人在動計數器」**。

**成因**：`_clear_rate_limits()` 在每個 `client` fixture 之前清空 Redis：

```python
keys = [k async for k in r.scan_iter(match="rl:*")]   # 修復前
```

`rl:*` 是**全域**的。所有 pytest 行程共用同一個 Redis（`redis_url` 指向
同一個 DB），所以**行程 A 在測試之間清計數器時，順手把行程 B 正在累積的
計數器一併刪掉**。兩種症狀由此而來：

- **少**了 429：一個測試靠「連打 `limit + 3` 次」來逼出 429
  （`test_rotating_xff_does_not_reset_the_ip_bucket`）。計數器在它數到一半時
  被別的行程清掉 → 次數歸零 → 永遠追不上 limit → 一次 429 都沒有。
- **多**了 429：反過來，一個測試在**別的測試**累積了計數之後才跑，
  於是它自己的正常請求被拒絕（`test_exactly_one_service_grab_wins`、
  `test_listing_counts_each_fleet_independently`）。

**同一個機制，兩個相反方向的症狀** —— 這正是它難診斷的原因。

**為甚麼這是真缺陷**：這**不是**「不要並行跑就好」可以打發的 ——
CI matrix、測試分片、開發者在 CI 跑時自己再跑一次，都會命中。
而且症狀（多一個 429 / 少一個 429）**與成因完全無關聯外觀**，
會讓人去查 `RateLimiter` 或 admin auth 的邏輯，那裡其實沒有 bug。

**修法**（三層，只改隔離不動產品行為）：

1. `Settings` 新增 `redis_key_namespace: str = "realtaxi:"`
   —— 生產行為**完全不變**，只是把原本硬編的 `"realtaxi:"` 變成可配置。
2. `app/main.py` 與 `AdminAuthService` 改讀 `settings.redis_key_namespace`
   （原本各自硬編 `"realtaxi:"`）。
3. `tests/conftest.py` 在 import 前把
   `REDIS_KEY_NAMESPACE` 設為 `f"test_{os.getpid()}:"`，
   而 `_clear_rate_limits()` 只掃 `rl:{APP_RL_NAMESPACE}*`。

**驗證**：見 §五「並行驗證」。

> **為甚麼是 key prefix 而不是獨立 Redis DB？** 獨立 DB（`redis://.../N`）
> 更徹底 —— 它連 limiter 以外的 key 都隔離。但 compose 只起一個 Redis，
> 而 key prefix 對「測試真正會重設的計數器」已經足夠。
> 若日後加到 4+ 分片，值得改用獨立 DB。

---

## 二、中嚴重度：一致性與可維護性

### M-1 · 已棄用的 Starlette 常數會在每次 access 產生 warning

`HTTP_413_REQUEST_ENTITY_TOO_LARGE` 已棄用，改用
`HTTP_413_CONTENT_TOO_LARGE`。同樣地 `HTTP_422_UNPROCESSABLE_ENTITY` →
`HTTP_422_UNPROCESSABLE_CONTENT`。

**影響不是「會壞」，而是「會淹沒警告」**：每次 access 都噴一條
`StarletteDeprecationWarning`，在 667 個測試的輸出裡會蓋掉真正的警告。
已改；wire 上的值（`PAYLOAD_TOO_LARGE`）刻意不變，另外加了測試斷言
值仍是 413，令 library 再改名時不會靜靜漂移。

### M-2 · `423` 在 Console 的 `CODE` map 裡不存在

H-1 修好了後端的 423，但 console 的 `CODE` 常數表也沒有它 ——
所以 view 只能硬編字串。已加 `locked: 'LOCKED'`。

### M-3 · `rate_limit.count` 的 fixed-window 取捨沒有寫下來

已補註釋說明兩件事：

- **視窗是牆鐘對齊的**（key 內嵌 `time // window_s`），所以跨邊界時
  實際可通過量最多是 `2 * limit`。這是刻意接受的成本（每個 key 一個
  sorted set 的 sliding window 太貴），而且所有使用點都是粗粒度濫用剎車，
  不是配額。
- **`expire` 只在 `count == 1` 時設**。每次呼叫都 re-EXPIRE 會不斷推後
  TTL，把一個會輪替的視窗變成永不過期的 key。

### M-4 · `settlement` 的 `except` 用 `logger.error` 而非 `logger.exception`

ruff 的 TRY400 抓到（在 H-3 的修改中引入）。已在修正時一併處理 ——
這個分支代表「一位司機靜靜地沒有被收費」，traceback 是唯一能說明
原因的東西。

---

## 三、已補充的註釋（不改行為）

這些地方邏輯正確，但**意圖不明顯**，容易被後人「順手簡化」而弄壞：

| 檔案 | 補充的說明 |
|---|---|
| `services/ledger_service.py` | pre-check 為甚麼**不上鎖**、靠 partial UNIQUE index 兜底；`IntegrityError` 分支是安全網 |
| `services/grab_service.py` | 為甚麼所有失敗都回 `False` 而不是 raise（避免洩漏訂單是否存在）；`ZREM` 為甚麼在 commit **之後** |
| `api/orders.py` `grab_order` | 為甚麼需要 `session.refresh`（GrabService 在**自己的** session commit，identity map 持有舊值 → 贏家會被告知他輸了） |
| `api/orders.py` `nearby_orders` | Redis geo index **不是權威來源**；SQL 的 `status == BROADCASTING` 才是真正的閘；以及為何要按 `ids` 重排 |
| `core/rate_limit.py` | 見 M-3 |
| `services/state_machine.py` | 兩個結構不變式，以及 P4 改動時必須重檢的理由 |

---

## 四、新增測試（+11）

### `tests/test_security_hardening.py` → `TestHttpErrorCodeMap`（5 個）

之所以加在這裡，是因為**這是一整類缺陷**：任何新的 status 都可能
忘記加進 `code_map`，而現有測試永遠是綠的。

1. `test_locked_maps_to_locked_not_the_generic_code` —— H-1
2. `test_structured_detail_without_message_promotes_reason` —— H-2
3. `test_explicit_message_still_wins_over_reason` —— 防止修 H-2 時改壞
4. `test_rate_limited_keeps_retry_after` —— 429 是**時間**判定，`Retry-After`
   屬於契約的一部分
5. `test_413_uses_the_non_deprecated_constant` —— M-1

### `tests/test_orders_module.py` → `TestStateMachineInvariants`（6 個）

斷言的是**表的結構**而不是某個 endpoint 的行為：

1. terminal state 沒有出邊（否則已完成的行程可以被重開，
   而所有由 `orders` 派生的收入數字會變成浮動目標）
2. `CANCELLED` 不可從 `IN_TRIP` 到達
3. 每個 status 都必須是 key（`.get(current, set())` 令遺漏的 key
   表現為「這裡甚麼都不合法」＝ 該狀態被靜靜鎖死）
4. 每個 target 都必須是已知 status
5. `COMPLETED` 只可從 `IN_TRIP` 到達（第二條入邊＝未開車就完成，
   是車費詐騙的形狀）
6. `TERMINATED` 是 terminal

---

## 五、實跑驗證

| 項目 | 結果 |
|---|---|
| backend pytest（junit XML） | **667 passed / 0 fail / 0 err / 0 skip** |
| `ruff check` | All checks passed |
| `ruff format --check` | 102 files already formatted |
| Console `tsc --noEmit` | clean |
| Console vitest | **29 passed（4 files）** |
| Console `npm run build` | 302.76 kB（gzip 97.00 kB） |
| Dart `run_tests.dart` | **93 passed / 0 failed** |
| `dart_check.py mobile` | 58 files / 0 diagnostics |
| `verify_contract.dart` | 54 fixtures / 0 failures |
| OpenAPI | 64 paths / 69 operations |

### 並行驗證（H-5 / H-6 的證據）

隔離缺陷**只能用並行跑來證明修好了**，所以做了這個測試：

| 情境 | 結果 |
|---|---|
| 單一行程跑完整 suite | **667 / 0 / 0 / 0** |
| **兩個行程同時**跑完整 suite | 兩者皆 **667 / 0 / 0 / 0** |
| session 結束後 `pg_database` | 只剩 `realtaxihk`，**無 template / 無孤兒 DB 殘留** |

修復前的同一測試是：一個行程 5 fail、另一個 4 fail，全部是
`relation "otp_codes" does not exist` 與各種 429 誤判。

> 這個驗證本身有價值：它把「隔離」從**沒有被測過**變成**被證明過**。
> 修 H-5 之後 H-6 才浮出來 —— 如果只跑單一行程，**兩個缺陷都不會出現**。

### 兩個環境陷阱（記下來免得下次誤判）

- **`npx vitest run` 一次跑全部 4 個 file，在這個 sandbox 會中途 abort**：
  vitest worker 寫 temp file 被 fs shim 擋（`EPERM`），輸出只顯示部分
  file。逐個 file 跑則**全部通過（7+7+5+10 = 29）**。
  **不要因為輸出比基準少就當成回歸。**
- **`dart mobile/tool/dart_check.py` 會失敗**：它是 **Python** 檔，
  但 shell 的 `dart` 會 shadow `python`。要用
  `./.venv/Scripts/python.exe mobile/tool/dart_check.py mobile`。

---

## 六、未修但有價值的觀察

以下都是**設計層面的建議**，不適合在這一輪靜靜改掉：

### O-1 · `LedgerService` 應該 raise 帶 code 的例外

H-3 的修法用字串比對 message 當判別條件。正解是
`BusinessRuleError` 加一個 `code` 欄位（它已經有 `details`），
令呼叫方不必解析自然語言。這會影響多個呼叫點，建議獨立處理。

### O-2 · `errors` 處理器的 500 分支原本不記 log —— **已修**

`register_exception_handlers` 的 `on_unexpected` 會吞掉所有例外並回
500。它是**唯一**觀察到未預期例外的地方，但原本沒有記 log ——
所以每一個 500 的根因都不存在於任何地方，除了 access log 的一個
status code。這令「app 主動 raise 的 500」與「真正的未預期例外」
無法區分。

**已加** `logger.exception`（含 method + path）。**回應內容維持通用** ——
例外訊息可能帶連接字串、檔案路徑或 SQL 片段，而這是唯一完全未經
審查的文字路徑。

### O-3 · `Order.distance_km` 由 **client 提供**

`OrderCreateIn.distance_km` 是 `Field(gt=0, le=100)`，即客戶端自報。
車費由它算出並寫入 `estimated_total_hkd`。目前沒有 server 端的
重算或比對（`pickup_location` 與 `dropoff_location` 都在 DB 裡，
用 PostGIS 算直線距離是可行的）。

**這不是漏洞，是商業決定**：HK 的士有法定跳錶，平台已聲明是
中介而非營運者（Cap. 374D），所以「乘客與司機同意里程」可能是
刻意的。但如果日後要做爭議仲裁，**server 端需要一個獨立估算**，
否則爭議時平台只能引用其中一方的數字。已在
`IN_TRIP_REDESIGN.md` 的 DECISION-2（`order_disputes`）語境下值得重看。

### O-4 · `hk_bounds._HK_MAIN` 的后海灣段精度不足（**已寫入 P4 文件**）

`is_in_hong_kong(22.500992, 113.945654)`（深圳灣口岸港方口岸區
公共運輸交匯處）回傳 `False`。原因已定位：該段多邊形只有 3 個頂點，
斜率 1.125，令邊界在該經度只有 22.488861，比交匯處低約 1.34 公里。

**港方口岸區（一地兩檢）本屬香港司法管轄**，所以這是真正的
false negative。三個選項與完整幾何分析見
`docs/IN_TRIP_REDESIGN.md` §9 DECISION-7 與 `docs/LANDMARK_COORDINATES.md`。

**待決策**，未改動。
