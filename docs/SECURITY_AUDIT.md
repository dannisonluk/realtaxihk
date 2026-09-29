# realtaxihk.com 網絡安全審計報告

**審計日期**：2026-09-29
**審計範圍**：`app/`（FastAPI 後端）、`docker-compose.yml`、`Dockerfile`、依賴、Redis/Postgres 暴露面
**方法**：靜態審閱 + **對真實運行的 uvicorn + Postgres + Redis 實測攻擊**（非只讀代碼）
**驗證腳本**：`scripts/security_probe.py`（找出漏洞，可重跑）、`scripts/security_verify.py`（修復後重跑攻擊）、`scripts/prod_boot_drill.py`、`tests/test_security_hardening.py`
**審計輪次**：第一輪只讀審計（未改任何應用代碼）；第二輪修復 30 項，見 §0.1。

---

## 0. 風險總表

| ID | 嚴重度 | 問題 | 實測 |
|---|---|---|---|
| SEC-01~03 | 🔴 **Critical** | `APP_ENV` 未設時 OTP 固定為 `123456` 且直接在 response 回傳 → 任意電話號碼登入 | ✅ 已證明 |
| SEC-04~05 | 🔴 **Critical** | `APP_ENV=production`（拼寫/大寫/其他值）繞過 prod 檢查，用 repo 內 hardcode JWT secret 開機 → 可離線偽造 ADMIN token | ✅ 已證明 |
| SEC-06 | 🔴 **Critical** | Redis **無密碼**且 `0.0.0.0:16379` 對外 → 可改 rate-limit key、篡改 GEO 派單索引、**向乘客實時地圖注入假位置**、預佔 grab lock 令訂單永遠無法被接 | ✅ 已證明 |
| SEC-13 | 🔴 **Critical** | Ledger `reference` 命名空間共用且無校驗 → 預先佔用 `weekly:{driver}:{period}` 令週費靜默不收；佔用 `refund:{id}` 令退款「已批准但無出款」 | ✅ 已證明 |
| SEC-07 | 🟠 **High** | `X-Forwarded-For` 取**最左**（客戶端可偽造）→ 完全繞過所有 IP rate limit（連 nginx 在後都無效） | ✅ 已證明 |
| SEC-08 | 🟠 **High** | 全域 OTP 上限係**單一共用 key**（500/hour）→ 一個攻擊者用 500 個請求即可令**全平台無法登入** | ✅ 代碼確認 |
| SEC-12 | 🟠 **High** | P0-3 未真正落實：6 條路由用 JWT-only 依賴，**已停用帳號仍可讀帳本/資料並持續上傳 GPS**，長達 120 分鐘 | ✅ 已證明 |
| SEC-09~11 | 🟠 **High** | 無 request body 上限 + `tunnels` 無 `max_length` → 63MB body 被接受；10 萬非法值回吐 32.8MB；20 萬項寫入 3.4MB JSONB 並在每次列表回傳 | ✅ 已證明 |
| SEC-14 | 🟠 **High** | WebSocket **先 `accept()` 後認證**，且每條 socket 一條 Redis 連線、無 per-user 上限、無 idle timeout | ✅ 已證明 |
| SEC-15 | 🟡 Medium | `/fare/estimate`、`/driver/location` 完全無 rate limit | ✅ 已證明 |
| SEC-16 | 🟡 Medium | WS tick 無節流：每個 tick = 1 次 DB UPDATE + commit + 1 次 Redis publish | 代碼確認 |
| SEC-17 | 🟡 Medium | Refresh token **無 reuse detection**：被盜 token 被輪換後不會撤銷整個 family，亦無告警 | 代碼確認 |
| SEC-18 | 🟡 Medium | Access token 120 分鐘且 logout 不撤銷 → 登出後仍可用最多 2 小時 | 代碼確認 |
| SEC-19 | 🟡 Medium | `docker-compose.yml` 把 db/redis 發佈到 `0.0.0.0`；且 `api` service **未注入 `POSTGRES_PASSWORD`** → 文件記載的部署路徑無法啟動 | ✅ 已證明 |
| SEC-20 | 🟡 Medium | 無 `.dockerignore` → build context 包含 `.env`（真 JWT secret + DB 密碼）與 `.venv` | ✅ 已證明 |
| SEC-21 | 🟡 Medium | Dockerfile 用 `pip install .` 完全忽略 `uv.lock` + 依賴只有 `>=` 下限 → 建置不可重現 | 代碼確認 |
| SEC-22 | 🟡 Medium | `/metrics` 啟用時無認證掛載 | 代碼確認 |
| SEC-23 | 🟡 Medium | `/health` 回傳 `env` → 直接告訴攻擊者「試 123456」 | ✅ 已證明 |
| SEC-24 | 🟢 Low | 完全沒有安全 headers（HSTS / X-Content-Type-Options / CSP …） | ✅ 已證明 |
| SEC-25 | 🟢 Low | `geo:drivers:online` 只寫不讀、永不清理 → Redis 無上限增長（實測已累積 37 筆） | ✅ 已證明 |
| SEC-26 | 🟢 Low | `GET /orders?before_id=` 接受**任意** order id 作 cursor → 跨租戶存在性/時間戳 oracle | 代碼確認 |
| SEC-27 | 🟢 Low | `GET /trips/{id}/location` 把司機的 **user UUID** 回給乘客 | 代碼確認 |
| SEC-28 | 🟢 Low | OTP 比對用 `!=` 非 constant-time（有 5 次上限，實際難利用，但應改 `hmac.compare_digest`） | 代碼確認 |
| SEC-29 | 🟢 Low | `DevFcmProvider` 把 device token 前 8 字寫入 log | 代碼確認 |
| SEC-30 | 🟢 Low | WS `pump()` 的心跳只在收到訊息時才發 → 文件寫的 idle keep-alive 實際無效 | 代碼確認 |
| **SEC-31** | 🟠 **High** | **修完 SEC-07 之後才發現**：uvicorn 的 `ProxyHeadersMiddleware` 預設開啟且 `forwarded_allow_ips=127.0.0.1`，會用客戶端自己送的 `X-Forwarded-For` 改寫 `scope["client"]` —— 在**應用層之下**把 SEC-07 整個還原 | ✅ 已證明（Redis bucket 實測） |

---

## 0.1 修復狀態（2026-09-29 第二輪）

30 項全部修復。驗證方式：`scripts/prod_boot_drill.py`（7 個真實開機情境）、
`tests/test_security_hardening.py`（27 個回歸測試）、`scripts/security_verify.py`
（對真實運行的 server 重跑攻擊）。**下表每一項都有對應測試或實測。**

| ID | 狀態 | 修復位置 |
|---|---|---|
| SEC-01~03 | ✅ | `core/config.py`（`app_env` 必填 + 白名單）、`otp_service.py`（`dev_otp_enabled`）、`ALLOW_DEV_OTP` 開關 |
| SEC-04~05 | ✅ | `core/config.py`（移除 hardcode secret；必填 + 熵檢查；`iss/aud` 待辦見下） |
| SEC-06 | ✅ | `docker-compose.yml`（`127.0.0.1` + `requirepass`）、`grab_service.py`（DB 條件式 UPDATE 仲裁） |
| SEC-07 | ✅ | `api/auth.py::_client_ip`（取最右可信跳數）、`api/fare.py`、`TRUSTED_PROXY_COUNT` |
| SEC-08 | ✅ | `api/auth.py`（soft cap → 降級；hard cap → 503）、per-phone 限流 |
| SEC-09~11 | ✅ | `api/orders.py`、`api/fare.py`（`before` validator + `max_length`）、`core/middleware.py`、`order_service.py`（去重） |
| SEC-12 | ✅ | `drivers.py`／`tracking.py`／`trips.py` 改用 `require_active_user` + 反射式回歸測試 |
| SEC-13 | ✅ | `ledger_service.py`（比對 entry_type/amount + 命名空間 helper）、`admin.py`、`settlement_service.py`（`tampered` 計數）、`refund_service.py`（出款斷言） |
| SEC-14 | ✅ | `ws.py`（`accept()` 前完成授權）、`trip_service.py`（共用 hub + `ConnectionRegistry`） |
| SEC-15 | ✅ | `api/fare.py`、`api/tracking.py` 加限流 |
| SEC-16 | ✅ | `ws.py` token bucket（`ws_ticks_per_second` + `ws_tick_burst`） |
| SEC-17 | ✅ | `refresh_service.py`（`RotateOutcome.reused` + family 撤銷）、`api/auth.py` |
| SEC-18 | ✅ | `core/token_revocation.py`（per-user epoch）、`api/auth.py` logout、access token 縮至 15 分鐘 |
| SEC-19 | ✅ | `docker-compose.yml`（補 `POSTGRES_PASSWORD`；`APP_ENV` 硬編 `prod`） |
| SEC-20 | ✅ | `.dockerignore`（新增） |
| SEC-21 | ✅ | `Dockerfile`（`uv sync --frozen --no-dev`） |
| SEC-22 | ✅ | `main.py` + `core/middleware.py::TokenGuardMiddleware`（無 token 則不掛載） |
| SEC-23 | ✅ | `main.py` `/health` 移除 `env` |
| SEC-24 | ✅ | `core/middleware.py::SecurityHeadersMiddleware` |
| SEC-25 | ✅ | `geo_service.py`（移除只寫索引）、`maintenance.py`（清理遺留 key）、`tracking.py` |
| SEC-26 | ✅ | `api/orders.py`（`before_id` 限定在呼叫者自己的訂單範圍） |
| SEC-27 | ✅ | `api/trips.py` 改回 `driver_profile_id`；`trip_service.trip_snapshot` 同步 |
| SEC-28 | ✅ | `otp_service.py` 改用 `hmac.compare_digest` |
| SEC-29 | ✅ | `notify.py`（只記錄長度，不記錄內容） |
| SEC-30 | ✅ | `ws.py` 心跳改為獨立 task（不再只在收到訊息時才 ping） |
| **SEC-31** | ✅ | `Dockerfile` / `docker-compose.yml` / `scripts/*.py` 全部加 `--no-proxy-headers`；`tests/test_security_hardening.py::TestProxyHeaderTrust` 鎖住啟動設定 |

### 兩處刻意偏離原建議（附理由）

1. **SEC-13 第 3 點（`UNIQUE(reference, entry_type)`）沒有照做。**
   現有索引是 `UNIQUE(reference) WHERE reference IS NOT NULL`，**比建議的更嚴格**：
   它連「同一 reference、不同 entry_type」都禁止。改成複合索引反而會放寬。真正的
   漏洞核心（`append()` 命中時不比對就直接回傳舊 entry）已在服務層修好，加上
   reference 改由伺服器分命名空間產生，跨用途碰撞已無法表達。保留原索引。
2. **SEC-18 的 deny-list 用 per-user epoch 而非 per-token `jti` 黑名單，且 Redis
   故障時 fail-open。** 理由：epoch 一個 key 就能撤銷該用戶所有 access token，
   不需逐 token 記帳或清理；而 fail-closed 會令一次 Redis 抖動變成全平台登入中斷
   ——比「已撤銷的 token 多活最多 15 分鐘」更差。短到期時間是後備。

### 尚未做（非本次 30 項範圍，建議下一輪）

- JWT 加 `iss` / `aud` claim 並在 `decode` 時驗證（SEC-04 修復清單第 3 點）——
  需要簽發端與驗證端同步改，且現時只有自家簽發，無跨服務驗證需求。
- `docker-compose.yml` 的 `CORS_ORIGINS` 預設已改為真域名，但仍應由部署環境明確覆寫。

---

## 0.2 修復期間發現的效能／可用性問題（已一併修好）

這兩項不在原本 30 項之內，是在「跑真實測試」時撞出來的——正好說明只讀代碼審計的極限。

### P-01 `localhost` 的 IPv6 回退：每個新連線 2 秒

`POSTGRES_HOST=localhost` / `REDIS_URL=redis://localhost:...`。在 Windows 上
`localhost` 同時解析出 `::1` 與 `127.0.0.1`，而 Docker 發佈的埠**只有 IPv4**。
連 `::1:15433` 時 SYN 被**靜默丟棄**（不是 refuse），要等 ~2 秒逾時才回退 IPv4。

實測（用 `socket.getaddrinfo` / 原生 `connect` 逐個 family 計時，再對 asyncpg 與
redis-py 量連接建立成本）：

| 目標 | 結果 |
|---|---|
| `connect 127.0.0.1:15433` | **1ms** |
| `connect [::1]:15433` | `ConnectionRefusedError` 但**耗時 2034ms** |
| `asyncpg connect + SELECT 1` | **2095ms → 25ms** |
| `redis PING` | **2058ms → 3ms** |
| `app get_session_factory()` 首次 | **2131ms → 60ms** |

因為多條代碼路徑都是「每次呼叫開一個新連線」，這變成**每個 HTTP 請求 4~6 秒**。

**修法**：`core/config.py` 預設改 `127.0.0.1`；`.env` / `.env.example` / CI 同步。
`docker-compose.yml` 不受影響（服務間用 service name `db` / `redis`）。

**連帶效果**：`tests/test_geo_and_limits.py::TestRateLimiter::test_order_creation_rate_limited`
原本是**會間歇性失敗的**（見下），修好後穩定通過。

### P-02 SEC-18 把「每次請求開一個 Redis 連線」放上了熱路徑

第一輪修 SEC-18 時，`assert_not_revoked()` 用了 `request.app.state.redis_factory()`——
而 `get_redis()` 的契約就是「每次呼叫回一個新 client」。結果**每一個已認證請求**
都要開／關一條 Redis socket。修好 P-01 之後單次成本由 2s 降到 3ms，但仍是純浪費，
而且在高併發下是 fd／連線數耗盡的放大器（可用性風險，不只效能）。

**修法**：`app.state.auth_redis` 一個 app 生命週期的 client，與 `rate_limiter` /
`maintenance` / `trip_hub` 一同在 lifespan 關閉；`deps.py` 改用它。

### P-03（既有，非本次引入）rate-limit 測試對「牆上時鐘」有依賴

`test_order_creation_rate_limited` 用**固定視窗**限流（`int(time.time()) // 60`）。
原本每個請求 4~6 秒 → 6 個請求橫跨 25~37 秒 → 有 ~40~60% 機率跨過分鐘邊界，
**counter 歸零 → 第 6 個請求仍然 201 → 斷言失敗**。

已用 `git worktree` 在**未修復的 HEAD（64dacce）**上重跑確認：同樣失敗（4.1s/請求）。
所以這是**既有的 flaky test**，不是本次改動造成。P-01 修好後 6 個請求只需 0.42 秒，
跨視窗機率降到可忽略。

---

## 0.3 SEC-31：SEC-07 在應用層之下被還原（修完 30 項之後才發現）

這一項是**重跑修復後攻擊探針時撞出來的**——如果只信「測試全綠 + 代碼已改」就會漏掉。

### 現象

`SEC-07` 的檢查在探針裡一直 FAIL：16 個請求、16 個不同 XFF → **0 × 429**。

直查 Redis 見到：

```
rl:realtaxi:otp:ip:203.0.113.0  = 2
rl:realtaxi:otp:ip:203.0.113.1  = 2
...
rl:realtaxi:otp:ip:127.0.0.1    = 3
```

**XFF 的值直接變成了限流 key。** 但 `_client_ip()` 明明有 `TRUSTED_PROXY_COUNT > 0` 的
護欄，而該 server 的 `trusted_proxy_count` 確認是 `0`（用 `env -i` 重現子進程環境核實）。

### 根因：uvicorn 自己就在改寫 client

`uvicorn 0.54.0` 的 `ProxyHeadersMiddleware` **預設啟用**，`forwarded_allow_ips` 預設
`127.0.0.1`。它在 ASGI 層做：

```python
if client_host in self.trusted_hosts:          # 127.0.0.1 在預設信任名單內
    ...
    host, port = self.trusted_hosts.get_trusted_client_address(x_forwarded_for)
    scope["client"] = (host, port)             # ← 覆寫成客戶端送的值
```

而 `get_trusted_client_address()` 對單一值的 XFF 是**原樣回傳**（多值時由右往左找第一個
不在信任名單的）。所以任何 TCP peer 是 `127.0.0.1` 的呼叫者（本機反代、同機任何進程、
SSRF、以及 Docker 下 `FORWARDED_ALLOW_IPS` 被設成 `*` 的常見誤配）都可以自選 IP。

**`_client_ip()` 收到的是已經被污染的 `request.client.host`**，護欄根本沒機會執行。
`TRUSTED_PROXY_COUNT=0` 在這一刻是裝飾品。

### 實測（啟動一次 server、送一個從未用過的 XFF 值，再直查 Redis 的 bucket 名）

| 啟動方式 | 送 `X-Forwarded-For: 198.51.100.78` 之後 |
|---|---|
| 預設（proxy headers **開**） | 新增 bucket `rl:realtaxi:otp:ip:198.51.100.78` → **每個請求一個新 bucket = 完全無限流** |
| 加 `--no-proxy-headers` | **沒有**新 bucket，請求落在 `127.0.0.1` |

修好後探針：`16 個請求、16 個不同 XFF -> 8 × 429`（前 8 個通過、之後全 429），
且**與 XFF 完全無關**。

### 修法

1. 所有 uvicorn 啟動點加 `--no-proxy-headers`：`Dockerfile`、`docker-compose.yml`
   （`api` 的 command）、`scripts/live_smoke.py`、`scripts/serve_and_probe.py`
   （`uvicorn.Config(..., proxy_headers=False)` 與 CLI 兩處）。
2. 信任決策**只留在應用層** `_client_ip()` + `TRUSTED_PROXY_COUNT` 一處。兩層都改寫
   client IP 正是這個漏洞能藏住的原因。
3. 回歸測試：`tests/test_security_hardening.py::TestProxyHeaderTrust`
   - `test_every_uvicorn_launch_point_disables_proxy_headers` —— 掃 Dockerfile /
     compose / `scripts/*.py`，任何啟動 `app.main:app` 的檔案都必須關掉 proxy headers；
   - `test_premise_uvicorn_trusts_the_header_from_loopback` —— 用 uvicorn 的
     `_TrustedHosts` 釘住「前提」，免得理由變成傳說（uvicorn 改了內部實作就會跳過並提醒重驗）。
   
   **注意**：這件事用 in-process `TestClient` **測不出來** —— TestClient 不會經過
   uvicorn 的中介層。所以回歸測試必須驗啟動設定。

### 順帶修正的文件錯誤

本報告 SEC-07 的「修復」第 3 點原本寫「最好在 uvicorn 用 `--proxy-headers
--forwarded-allow-ips=<nginx IP>`，讓框架自己處理」——**方向相反，已撤回並更正**。
那正是令 SEC-31 存在的配置。

### 順帶修好的殘留：422 回應體回吐請求內容

驗證 SEC-09 時發現，pydantic v2 會在每個 error 物件上附 `input`（原始輸入），
`exceptions.py` 原樣 `jsonable_encoder` 回吐。結果：

- 20,000 個非法 `tunnels`（120 KB 請求）→ **100,219 bytes** 回應；
- 不只是放大（1:1），還會把**呼叫者送的內容原樣彈回**——一個含憑證的壞 payload
  會出現在回應裡，以及任何記錄 response body 的日誌裡。

修法：`RequestValidationError` handler 丟棄 `input` 欄位（保留 `type` / `loc` / `msg`）。
修後同一個請求 → **209 bytes**。100k 版本本來就已由 1 MB body cap 以 413 擋下（113 bytes）。

---

## 1. Critical — 可直接完全接管平台

### SEC-01~03：`APP_ENV` 的 fail-safe 方向反了 → 任意帳號登入

**根因**：`app/core/config.py:16` `app_env: str = "dev"`（預設 dev），而 `otp_service.py:75` 與 `88`：

```python
code = _DEV_CODE if settings.app_env == "dev" else f"{secrets.randbelow(10**6):06d}"
...
if settings.app_env == "dev":
    result["dev_code"] = code          # ← 直接把驗證碼回給請求者
```

`notify.py:119` 亦係 `app_env in ("dev","test")` → 走 `DevWhatsAppProvider`（只寫 log，不發訊息）。

**實測**（無 `.env`、無 `APP_ENV`，等同 Docker image 的實際情況——Dockerfile 從不 COPY `.env`）：

```
/health reports env='dev' (no .env, no APP_ENV set)
POST /otp/request -> 200, response contains dev_code='123456'
兩個不同號碼都係 dev_code='123456'
POST /otp/verify code=123456 -> 200，回傳可用 access_token + refresh_token
```

**影響**：任何人對任意 `+852XXXXXXXX` 請求 OTP，拿回 `123456`，即可登入該帳號。而 `verify_otp` 在帳號不存在時會**自動建立帳號**，所以連不存在的號碼也能憑空取得 token。完全繞過認證。

**修復**（必須做，且要 fail-closed）：
1. 反轉預設：`app_env` 必須**明確**設定才接受；未設或非白名單值 → **拒絕啟動**。
2. 白名單化而非等值比較：`if self.app_env not in ("dev","test","prod"): raise`。
3. `dev_code` 的洩漏條件由 `app_env == "dev"` 改為**同時**要求一個獨立的顯式開關（如 `ALLOW_DEV_OTP=true`），且該開關在 `app_env == "prod"` 時強制為 False。
4. `_DEV_CODE` 常數應只存在於 test 路徑（例如由 fixture 注入），不要放在生產 import 的模組裡。

### SEC-04~05：prod 檢查太窄 → 可偽造 ADMIN JWT

**根因**：`config.py:70-80` 只檢查 `if self.app_env == "prod"`。

```python
if self.app_env == "prod":
    if self.jwt_secret_key.startswith("dev-only"): raise ...
```

`"production"`、`"PROD"`、`"staging"`、`"prod "`（尾隨空格）全部**不等於** `"prod"` → 所有 prod 安全檢查被跳過。

**實測**：以 `APP_ENV=production` 開機 → 成功啟動；再用 repo 內 hardcode 的 secret（`app/core/config.py:30`）離線簽一個 ADMIN token：

```
forged token -> GET /api/v1/admin/refunds = 200
```

**影響**：離線偽造 ADMIN JWT（無需任何憑證），可讀退款隊列、批 KYC、發放押金、觸發週費結算——即完整平台接管。而且這個 secret 就寫在 git 歷史裡。

**修復**：
1. 用白名單 + fail-closed（見上）。
2. 移除 `jwt_secret_key` 的 hardcode 預設；改為必填（`Field(...)`），並在啟動時檢查**熵**（長度 ≥ 32 bytes、非全部相同字元）。
3. 加 `iss` / `aud` claim 並在 `decode` 時驗證。
4. **立刻輪換任何曾用此 secret 簽發的 token**，並在 prod 環境變數中設定新 secret。

### SEC-06：Redis 無認證且對外 → 可注入假位置、否決派單、繞過限流

**實測**：

```
docker ps  -> realtaxi-redis  0.0.0.0:16379->6379/tcp
netstat    -> 0.0.0.0:16379 LISTENING
PING       -> True
requirepass-> {'requirepass': ''}      # 空 = 無密碼
redis_version -> 7.4.8
```

Redis 在這個架構裡不只是 cache，而是**授權/一致性基礎設施**，所以無認證的 Redis 等於無認證的業務邏輯：

| 攻擊 | 實測結果 |
|---|---|
| 讀寫 rate-limit key | 48 個 `rl:*` key 可讀；`SET rl:realtaxi:otp:global:hourly:<window> = 1e9` 成功 → 令全平台登入被鎖 |
| **向乘客注入假司機位置** | `PUBLISH realtaxi:trip:<order_id>` → subscriber 收到 `{"type":"location","lat":22.3193,...}`。`TripHub.subscribe` 原樣 fan-out，所以攻擊者可**任意偽造司機實時位置**給乘客看 |
| 否決派單 | `SET lock:grab:<order_id> attacker NX PX 15000` 成功 → `GrabService.grab()` 對所有真司機回 False，且可每 15 秒續期 → 該訂單永遠無法被接 |
| 篡改派單索引 | `geo:orders:active` 可任意 ZADD/ZREM → 令訂單對司機隱形，或塞入幽靈訂單 |

**修復**（優先次序）：
1. **不要發佈 db/redis 端口到 host**：改用 `expose:` 或綁 `127.0.0.1:16379:6379`，只讓 compose 內部網絡可達。
2. Redis 設 `requirepass`（高熵）並在 `REDIS_URL` 帶密碼；啟用 `--protected-mode yes`。
3. 若必須對外，加 TLS + ACL 分權（限制 `PUBLISH`/`KEYS`/`CONFIG`）。
4. 長期：不要把 Pub/Sub 當信任邊界——WS hub 應只接受**經伺服器驗證過的** tick（現在是由 `handle_push` 寫入，但任何能 publish 的人都能繞過）。
5. `lock:grab` 的 key 應加上隨機 salt 或改用 DB 唯一約束做真正的仲裁，避免外部可預測的鎖。

### SEC-13：Ledger `reference` 命名空間共用 → 靜默漏收週費／假出款

**根因**：`admin.py:113` 接受**客戶端提供**的 `reference`，而 `ledger_service.append()` 對已存在的 reference 直接**原樣回傳舊 entry**（不套用本次金額）。同時 `settlement_service.py:79` 用 `weekly:{driver_id}:{period}`、`refund_service.py` 用 `refund:{refund_id}`——**同一個命名空間，無前綴保護、無所有權校驗**。

**實測 A：週費靜默不收**

```
driver ACTIVE, balance: 500.0
planted ledger entry with reference 'weekly:<driver>:2099-W03' -> 200
settlement run: {'period':'2099-W03','charged':0,'skipped':2,...}
driver balance after settlement = 501.0     # 應為 301.0
ledger: [('DEPOSIT_TOPUP','500.0'), ('DEPOSIT_TOPUP','1.0')]   # 無 WEEKLY_FEE_DEDUCTION
```

即 HK$200 週費**從未收取**，而結算報告把它算成正常的 `skipped`，運營完全看不出來。

**實測 B：退款「已批准但無出款」**

```
planted ledger entry with reference 'refund:<refund_id>' -> 200
approve decision -> 200 APPROVED
balance=502.0  held=0.0  status=TERMINATED
ledger: [('DEPOSIT_TOPUP','500.0'), ('DEPOSIT_TOPUP','2.0')]   # 無 REFUND entry
```

凍結被釋放、退款被標記 APPROVED、司機被 TERMINATED，但**沒有寫入任何 REFUND 借方**。結果：審計上「已批准退款」與「REFUND 帳目」對不上，司機則帶著一筆無法提領的餘額（退款要求 ACTIVE 身份）被終止。

**與 SEC-04 串連**：攻擊者用偽造 ADMIN token 就能執行上述操作，無需內部人員。

**修復**：
1. `reference` 分命名空間並**由伺服器產生**：不接受客戶端傳入的 reference，或強制加上服務端前綴（`admin:{uuid}` / `weekly:` / `refund:`），並在 `append()` 內校驗前綴與呼叫者匹配。
2. `append()` 命中既有 reference 時，應**比對 entry_type 與 amount**，不一致就 raise（現在是靜默回傳舊 entry——這正是漏洞核心）。
3. 加 `entry_type` 到唯一性條件（`UNIQUE(reference, entry_type)`），令跨類型碰撞不可能。
4. 退款流程改為：先寫 ledger、再改狀態，或在同一交易內驗證 ledger entry 真的被建立。

---

## 2. High

### SEC-07：`X-Forwarded-For` 偽造繞過所有 IP 限流

**根因**：`auth.py:48-53`

```python
fwd = request.headers.get("x-forwarded-for")
if fwd:
    return fwd.split(",")[0].strip()      # ← 最左 = 客戶端自己填的那一段
```

`split(",")[0]` 取的是**最左**元素。而 nginx 用 `$proxy_add_x_forwarded_for` 是**附加**在後面：`<客戶端偽造值>, <真實 IP>`。所以就算正確部署在 nginx 後面，第一個元素依然由攻擊者控制。

**實測**：

```
固定 XFF      : [200×10, 429, 429, 429]        # 對照組：限流有效
輪換 XFF      : 40 個請求 -> 0 × 429           # 完全繞過
```

**影響**：OTP 成本上限（唯一的成本剎車）失效；配合 SEC-08 可低成本打爆全域 OTP 額度；亦可用來栽贓其他 IP。

**修復**：
1. 由**最右**往左數，跳過已知可信代理層數（例如 `TRUSTED_PROXY_COUNT=1` → 取 `parts[-1]`）。
2. 或用 nginx 的 `X-Real-IP` / `Forwarded` header，並在應用層只信任來自已知代理網段的來源。
3. ~~在 uvicorn 用 `--proxy-headers --forwarded-allow-ips=<nginx IP>`，讓框架自己處理。~~
   **這一條第一輪寫錯了，已撤回** —— 見 §0.3 / SEC-31：uvicorn 的 proxy headers **預設就是開的**
   （`forwarded_allow_ips=127.0.0.1`），它會在**應用之下**改寫 `scope["client"]`，
   令上面第 1 點完全失效。正確做法是**關掉 uvicorn 的 proxy headers**（`--no-proxy-headers`），
   只由應用層的 `TRUSTED_PROXY_COUNT` 一處決定信不信 XFF。
4. 加 per-phone 限流（現在只有 per-IP + 全域），避免單一號碼被 OTP 轟炸。

### SEC-08：全域 OTP 上限 = 全平台登入 DoS

**根因**：`auth.py:69` `limiter.allow("otp:global:hourly", 500, 3600)` —— 一個**所有用戶共用**的 key。

**影響**：攻擊者（配合 SEC-07 繞過 IP 限流）只需 500 個請求，就能令**所有**用戶在該小時內無法登入（全部 429）。這是一個成本極低的平台級可用性攻擊。

**修復**：全域上限改為**告警 + 降級**而非硬性 429；或按號碼/裝置分桶；或把全域 cap 提高並改為對異常來源做精準封鎖。切勿讓單一共享計數器成為全站單點。

### SEC-12：P0-3 未真正落實 —— 已停用帳號仍保有存取

**根因**：`docs/PRODUCTION_READINESS.md` 與 `orders.py` docstring 都聲稱「P0-3 all user-facing routes run `require_active_user`」，但實際只有 `orders.py`、`auth.py` 與 `drivers.py:168` 用了它；以下 **6 條路由只用 JWT-only 的 `get_current_user`**（只驗簽名，不查 DB）：

| 檔案 | 路由 |
|---|---|
| `drivers.py:63` | `POST /api/v1/drivers/register` |
| `drivers.py:83` | `GET /api/v1/drivers/me` |
| `drivers.py:109` | `GET /api/v1/drivers/me/ledger` |
| `drivers.py:190` | `GET /api/v1/drivers/me/refund` |
| `tracking.py:34` | `POST /api/v1/driver/location` |
| `trips.py:25` | `GET /api/v1/trips/{id}/location` |

**實測**（`users.is_active=false` 之後）：

```
200  GET   /api/v1/drivers/me                 OPEN
200  GET   /api/v1/drivers/me/ledger          OPEN
200  GET   /api/v1/drivers/me/refund          OPEN
200  POST  /api/v1/driver/location            OPEN   ← 持續上傳 GPS
200  GET   /api/v1/trips/{id}/location        OPEN
403  GET   /api/v1/orders                     control（正確）
```

**影響**：被停權（封鎖）的用戶在 `ACCESS_TOKEN_EXPIRE_MINUTES=120` 內仍可讀取帳務資料、持續上傳位置（維持自己在 GEO 索引中），並讀取行程位置。停權即時失效的設計意圖沒有達成。

**修復**：把這 6 條全部改為 `Depends(require_active_user)`；並加一個**測試**斷言「所有 `/api/v1` 路由的依賴集合都包含 `require_active_user` 或 `require_admin`」，用反射列舉路由，防止將來再漏。

### SEC-09~11：無 body 上限 + `tunnels` 無 `max_length`

**根因**：`OrderCreateIn.tunnels: list[str]`（`orders.py:63`）與 `FareEstimateRequest.tunnels: list[Tunnel]`（`fare.py:26`）都沒有 `max_length`；同時全項目沒有任何 request body size 限制。

**實測**：

```
/fare/estimate 無 token -> 200（無 rate limit）
100,000 個非法 tunnels -> HTTP 422，response body 32.8 MB，1.56s（100,000 個 error 物件）
63 MB JSON body -> HTTP 200（不是 413）
200,000 個 tunnels 建單 -> HTTP 201；fare_json 在 Postgres 佔 3.4 MB，且每次 GET /orders 都重發
```

**影響**：
- **無認證**的放大攻擊：小請求 → 32.8MB 回應，同時燒 CPU 與頻寬。
- 記憶體耗盡：無 body 上限 + JSON 全量 parse。
- 儲存/出口放大：`fare_json` 無上限，5 單/分/用戶 × N 用戶；而 `order_out()` 每次都把整個 `fare_json` 回傳。

**修復**：
1. `tunnels: list[Tunnel] = Field(default_factory=list, max_length=8)`（合法隧道只有 8 種）。
2. 加 body size 中間件（例如拒絕 `Content-Length > 1MB`），或在 nginx 設 `client_max_body_size`。
3. `fare_snapshot` 只寫入**去重後的合法枚舉**，不要寫原始 payload list。
4. 對 `/fare/estimate` 加 rate limit（見 SEC-15）。
5. 考慮用 `pydantic` 的 `max_length` 全域策略，並加測試確保所有 list 欄位都有上限。

### SEC-14：WebSocket 先 accept 後認證 + 無連線上限

**實測**：

```
無 token 的 handshake -> ACCEPTED (101)，之後才 close 4401
單一帳號開 120 條已授權 socket -> Redis connected_clients 由 4 升到 33（仍在上升）
redis maxclients = 10000
```

**根因**：`ws.py:52` 先 `await ws.accept()` 才驗 token（為了拿到 4401 close code）；而 `TripHub.subscribe()` 每條連線都 `self._redis_factory()` 建**自己的** Redis client + pubsub，且沒有 per-user 連線上數上限、沒有 idle timeout。

**影響**：未認證者即可完成 101 升級（佔用 socket/task）；已認證者可用一個帳號開大量 socket，令 Redis 連線數線性增長，逼近 `maxclients=10000` 時 **Redis 會拒絕所有 client，包括 API 自己**（grab lock、geo 索引、Pub/Sub 全部失效）→ 全站派單與追蹤停擺。

**修復**：
1. 認證失敗時直接以 HTTP 403 拒絕升級（不要先 accept）；或接受「先 accept 但立即 close」的權衡，同時加**未認證連線數上限**與極短 grace period。
2. `TripHub` 改用**單一共用 Redis 連線池**（一個 pubsub 連線可訂閱多個 channel），而非每連線一個 client。
3. 加 per-user / per-order 連線上數上限（例如每用戶 5 條）與 idle timeout（例如 120 秒無訊息即關）。
4. 修好 `pump()` 的心跳：目前只在收到訊息時才 ping，idle 連線永遠收不到 ping（與 docstring 不符），令死連線滯留。

---

## 3. Medium

- **SEC-15 無 rate limit 的端點**：`/api/v1/fare/estimate`（無認證、無限流，實測 200）、`/api/v1/driver/location`（實測無 limiter）。後者每個請求 = 1 次 PostGIS UPDATE + 1 次 Redis GEOADD，可被無限刷。
- **SEC-16 WS tick 無節流**：`ws.py:117-129` 每個 tick 開新 session、UPDATE + commit、再 publish。單一司機可高速推送，造成 DB 寫入放大。應加 per-connection token bucket（例如 1 tick/秒）。
- **SEC-17 無 refresh reuse detection**：`refresh_service.rotate()` 對已撤銷的 token 只回 `None`（401），**不撤銷整個 token family**、不告警。若 refresh token 被盜，攻擊者輪換後正當用戶只會莫名被登出，而攻擊者的新 token 繼續有效。建議：偵測到已撤銷 token 被重放 → 撤銷該用戶所有 refresh token 並記錄安全事件。
- **SEC-18 Access token 120 分鐘且 logout 不撤銷**：`logout` 只撤銷 refresh token；access token 在到期前仍可用（實測 SEC-12 就是靠這點）。建議縮短至 15~30 分鐘，或引入 `jti` deny-list / per-user epoch。
- **SEC-19 Compose 部署路徑無法啟動**：`docker compose config` 顯示 `api` service 的 environment **只有** `APP_ENV/JWT_SECRET_KEY/POSTGRES_HOST/REDIS_URL`，**沒有 `POSTGRES_PASSWORD`**；而 Dockerfile 不 COPY `.env`。所以容器內 `postgres_password` = 預設 `change-me-dev`，配上 `APP_ENV=prod` → validator raise → api 容器 crash-loop。同時 db/redis 被發佈到 `0.0.0.0`（見 SEC-06）。附帶：`.env` 的 `JWT_SECRET_KEY` 目前仍係 dev 預設值（會 fail-closed，但說明從未真正部署過）。
- **SEC-20 無 `.dockerignore`**：build context 會把 `.env`（真 JWT secret + DB 密碼）、`.git`、`.venv`、`.tmp/uvicorn.log` 一併送給 Docker daemon。目前 Dockerfile 用顯式 COPY 所以未 bake 進 image，但這是隨時會被 `COPY . .` 引爆的地雷。
- **SEC-21 建置不可重現**：`Dockerfile` 用 `pip install .`，完全忽略 `uv.lock`；`pyproject.toml` 只有 `>=` 下限 → 每次 build 可能裝到不同版本，未經審核的升級會直接上生產。
- **SEC-22 `/metrics` 無認證**：`main.py:210` `app.mount("/metrics", make_asgi_app())` 無任何依賴。應只綁內網或加認證。
- **SEC-23 `/health` 洩露 `env`**：實測 `{"status":"ok","env":"dev",...}`。攻擊者可據此判斷「用 123456」（見 SEC-01）。

---

## 4. Low

- **SEC-24 無安全 headers**：實測 `Strict-Transport-Security`、`X-Content-Type-Options`、`X-Frame-Options`、`Content-Security-Policy`、`Referrer-Policy` 全部 **ABSENT**。加 TLS 後 HSTS 尤其重要。
- **SEC-25 `geo:drivers:online` 只寫不讀、永不清理**：`geo_service.py` 寫入，`GEO_DRIVERS_KEY` **全項目零讀取**，`maintenance.sweep_ghost_orders()` 只清 `geo:orders:active`。實測該 key 已累積 37 筆（我的探測留下的）。司機 app 崩潰未下線者永久殘留 → Redis 無上限增長。建議刪除該索引，或加 TTL/清理。
- **SEC-26 `before_id` 跨租戶 oracle**：`orders.py:173` `anchor = await session.get(Order, before_id)` 對**任意** order id 都查，可用來探測某 id 是否存在及其 `created_at`。
- **SEC-27 司機 user UUID 外洩**：`trips.py:69` 回傳 `driver_user_id`（司機的 user UUID）給乘客，屬不必要的識別碼暴露。
- **SEC-28 OTP 比對非 constant-time**：`otp_service.py:117` `otp.code_hash != _hash_code(...)`。有 5 次上限，實際難利用，但改成 `hmac.compare_digest` 是零成本的正確做法。
- **SEC-29 device token 入 log**：`notify.py:115` `device_token[:8]`。
- **SEC-30 WS 心跳失效**：`ws.py:138-147` 的 ping 在 `async for` 內，只有收到訊息才會執行 → idle 連線永遠不 ping，與 `ws_config.ws_heartbeat_s` 的 docstring 意圖不符。

---

## 5. 做得好、不要動的部分

避免修錯方向，以下經審閱確認**沒有**問題：

- **無 SQL injection**：`trips.py` 的原生 SQL 用 `:oid` 綁定參數；其餘全部走 SQLAlchemy ORM。
- **無 `alg=none` JWT 混淆**：`decode_access_token` 明確傳 `algorithms=[settings.jwt_algorithm]`。
- **無 mass assignment**：`Order(...)`、`DriverProfile(...)` 都是顯式逐欄建構。
- **無 CSRF / CSWSH**：認證走 `Authorization` header（非 cookie），跨站請求帶不上憑證；WS 用 query token 而非 cookie，所以跨站 WebSocket 劫持不成立。
- **CORS 正確**：顯式 origin 白名單，非 `*`；`allow_credentials=True` 配白名單是安全的（配 `*` 才危險）。
- **Refresh token 儲存正確**：sha256 at rest、`with_for_update()` 防輪換競態、single-use。
- **OTP 儲存正確**：sha256(phone:code)、5 次上限、60 秒重送冷卻。
- **Ledger 併發正確**：append-only、`FOR UPDATE` 行鎖、partial UNIQUE 冪等索引。
- **`require_admin` 有查 DB**：不信任 JWT 的 role claim。
- **WS 有 party 檢查**：handshake 驗證乘客/司機身份，且每 tick 重查司機是否 ACTIVE。
- **依賴版本新**：FastAPI 0.141 / Starlette 1.7 / SQLAlchemy 2.1 / PyJWT 2.15 / redis 8.1 等，未見已知高危 CVE。

---

## 6. 建議修復次序

**立即（上線前必須，全部會導致平台被接管）**
1. SEC-01~05：`app_env` 白名單 + fail-closed；移除 hardcode JWT secret 並**輪換**；`dev_code` 加獨立開關。
2. SEC-06：db/redis **不再發佈到 0.0.0.0**；Redis 設 `requirepass`。
3. SEC-13：`reference` 改由伺服器產生 + `append()` 比對 `entry_type`/`amount`。
4. SEC-07：`X-Forwarded-For` 改由最右取可信跳數。

**本週**
5. SEC-12：6 條路由改 `require_active_user` + 加反射式測試。
6. SEC-09~11：`tunnels` 加 `max_length=8`；加 body size 上限；`/fare/estimate` 加限流。
7. SEC-14：WS 認證前拒絕 / 共用 Redis 連線池 / per-user 上限 / idle timeout。
8. SEC-08：全域 OTP cap 改為告警或分桶，不要做成全站單點。

**接著**
9. SEC-15~18：補限流、WS tick 節流、refresh reuse detection、縮短 access token。
10. SEC-19~23：修 compose（補 `POSTGRES_PASSWORD`、內網端口）、加 `.dockerignore`、Dockerfile 改用 `uv sync --frozen`、`/metrics` 加認證、`/health` 移除 `env`。
11. SEC-24~30：安全 headers、清 `geo:drivers:online`、修心跳、constant-time 比對。

---

## 7. 附錄：如何重現

```bash
# 全部（A 認證 / B 限流 / C DoS / D 授權）
.venv/Scripts/python.exe scripts/security_probe.py all
# 只跑單一組
.venv/Scripts/python.exe scripts/security_probe.py d
```

腳本會自行以受控環境變數啟動 uvicorn（並在「無 `.env`」情境下從 `.tmp/` 啟動以模擬 Docker image），逐項輸出 `VULNERABLE` / `not reproduced`，並在最後列出已證實清單。所有測試均為非破壞性（只建立一次性測試用戶與訂單），Redis 的注入測試只發佈到不存在的 order channel 並即時清理。

**注意**：`scripts/security_probe.py` 會建立測試用戶與訂單。若要對生產環境重跑，請先改為只讀檢查（移除 B、C、D 的寫入部分）。
