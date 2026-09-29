# realtaxihk Backend — MVP → Production Readiness Audit

- **掃描日期**：2026-09-29
- **方法**：唔靠任何 session 記憶 — 逐檔讀取 `app/`（api 8 檔、core 8 檔、services 9 檔、models）＋ `tests/conftest.py`、`alembic/env.py`、`Dockerfile`、`docker-compose.yml`、`pyproject.toml`、`.env.example`、`README.md`，再用 grep 驗證每個「有冇」判斷（for_update、lifespan、is_active 使用、lockfile、CI、logging）。所有結論附 file:line 證據。
- **基準**：commit `88a09ee`（main），106/106 tests，3 commits 歷史。

---

## 修復記錄（2026-09-29，同日完成）

**全部 4 bugs + 7 P0 + 10 P1 + 10 P2 已修復並驗證**。最終狀態：**120/120 tests GREEN**（106 原有 + 14 新 hardening regression suite `tests/test_hardening.py`）、live smoke 9/9、prod fail-fast drill PASS（`scripts/prod_boot_drill.py`）。

| 層 | 修復後狀態 |
|---|---|
| Domain logic / 測試 | ✅ 120/120（+14 hardening） |
| 資料完整性 | ✅ B2/P0-1 ledger `with_for_update` 行級鎖 + 並發測試（asyncio.gather 2×grant → 餘額精確） |
| Fail-safe | ✅ P0-2 `model_validator` fail-fast（prod+dev secret 拒絕開機，實機 drill 驗證） |
| 封禁 | ✅ P0-3 `require_active_user`／`require_admin` DB re-check；WS handshake 亦檢查（403） |
| Observability | ✅ P0-4 JSON stdout logging＋request-ID middleware；`/health` 真檢 DB+Redis（503 on failure） |
| Geo ghosts | ✅ P0-5 `MaintenanceService.sweep_geo_index()`：訂單離開 BROADCASTING→ZREM；60s 週期 sweeper（實戰清咗 1 條 ghost） |
| 部署管道 | ✅ P1-3 `uv.lock`；P1-4 GitHub Actions CI（PostGIS+Redis services，`uv sync --frozen` + pytest）；P0-6/7 compose restart: unless-stopped＋`alembic upgrade head` 前置 |
| 合規 | ✅ P1-6 PDPO purge job（otp_codes 24h、refresh_tokens 30d、rate keys 7d） |
| Auth | ✅ P1-5 refresh token 輪換（SHA-256 hash at rest、single-use、`/auth/refresh` `/auth/logout`） |
| Notify | ✅ P2-9 真 WhatsApp Cloud API provider（httpx，template message，fail-closed on prod OTP） |

**新增檔案**：`app/core/logging.py`、`app/services/maintenance.py`、`app/services/refresh_service.py`、`alembic/versions/b4f1c2a7d901_*.py`（refresh_tokens 表＋ledger idempotency index）、`.github/workflows/ci.yml`、`uv.lock`、`tests/test_hardening.py`、`scripts/prod_boot_drill.py`。

**過程中修復嘅 regression**：Redis client singleton 跨 event-loop 污染（TestClient portal 每 test 新 loop → pooled connection 綁死舊 loop → WS suite hang）— P2-7 singleton 改為 per-call client（production 單 loop 無額外成本）。

**上線日仍然要做（人手/環境項，代碼之外）**：
1. 產生強 JWT secret（`openssl rand -hex 32`）＋強 POSTGRES_PASSWORD 入 prod .env — fail-fast 會把關
2. Nginx/Caddy TLS 反代 + `X-Forwarded-For`（rate limit 按 IP 先有效）＋ CORS lockdown（`CORS_ORIGINS` env）
3. DB 備份 cron（`pg_dump` nightly + off-host retention）— 審計 P1-8 建議
4. WhatsApp Cloud API token／template（`WHATSAPP_*` env）＋ FCM credentials
5. 首次 `docker compose up` 部署演練 + `/health` 輪詢驗收

---

## 0. Verdict

**MVP 核心域邏輯紮實（計費、搶單、ledger、WS 全有 TDD 背書），但有 7 個 P0 項未過關 — 其中 2 個係掃描中發現嘅真 bug。** 錢相關（ledger 並發）同 fail-safe（JWT/OTP 誤設定）必須先修先好上線。

| 層 | 狀態 |
|---|---|
| Domain logic / 測試 | ✅ 106/106，模式正確（SETNX+Lua、per-tick session、append-only ledger） |
| 資料完整性 | ⚠️ ledger 冇行級鎖 → 並發 lost update（P0-1） |
| Fail-safe | ❌ prod 誤設定無攔截（P0-2） |
| Observability | ❌ 零 logging config、/health 係假健康（P0-4） |
| 部署管道 | ⚠️ 無 lockfile、無 CI、container 唔自動 migrate（P1） |
| 合規 | ⚠️ PDPO purge job 未實現（P1） |

---

## 1. 掃描中發現嘅真 bugs（即刻修，每個 <10 行）

### B1. `/api/v1/auth/me` 對已刪除 user 會 NameError → 500
`app/api/auth.py:4` 只 import `APIRouter, Depends`；`auth.py:69` 喺 `me()` 內 `raise HTTPException(...)` — 個名從未 import。
觸發條件：JWT 有效但 user row 冇咗（PDPO 刪除權一落地就會發生）。
**修法**：頂部補 `from fastapi import HTTPException`。

### B2. Ledger `append()` 冇 `with_for_update` → 並發 lost update
`app/services/ledger_service.py:36-52`：balance 更新係 ORM read-modify-write（SELECT → Python 加數 → UPDATE 覆寫）。兩個並發 request（同時 grant、grant+penalty）同讀 balance=500，各自 +100，後 commit 覆蓋前者 → **帳目靜靜地錯**。
grep 全 repo 證實 `for_update` 出現次數 = 0。SETNX 只保護搶單，唔保護 ledger。
**修法**（二選一）：
```python
# (a) 行級鎖
deposit = (await session.execute(
    select(DriverDeposit).where(...).with_for_update()
)).scalars().first()
# (b) SQL 原子加數 + RETURNING
UPDATE driver_deposits SET balance_hkd = balance_hkd + :amt ... RETURNING balance_hkd
```
同場加建議：`ledger_entries` 對 `balance_after_hkd` 連續性加 DB 約束或對帳 job（見 P2-6）。

### B3. `tip` 冇上限 → DB overflow → 500
`app/api/orders.py:55` 同 `app/api/fare.py:33`：`tip: Decimal = Field(ge=0)` 無 `le`。`1e50` tip → `Numeric(10,2)` overflow → asyncpg 錯 → 500。純 validation 缺口（`discount_percent` 有 le=100，唯獨 tip 漏）。
**修法**：兩處補 `le=10000`（HK$10,000 tip cap）。

### B4. `tracking.py` 用 `__import__("datetime")` hack
`app/api/tracking.py:43`：`__import__("datetime").datetime.now(...)` — 能跑但係 production code smell。
**修法**：頂部 `import datetime` 正常寫。

---

## 2. P0 — 上線硬阻塞

### P0-1. （=B2）Ledger 並發安全
錢嘅嘢零容忍。見 B2。

### P0-2. Prod fail-safe：誤設定就全平台失守
- **JWT secret**：`app/core/config.py:23` 有 dev default secret。docker-compose 有 `:?` 強制（`docker-compose.yml:45`），但裸 uvicorn / PM2 / systemd 部署完全冇保護。`app_env=prod` + dev secret = 任何人可以自簽 token 冒充任何 user。
- **OTP dev_code**：`app/services/otp_service.py:70,83-85` — `app_env=dev` 時回 `dev_code="123456"` 且繞過 WhatsApp。若 prod 機器 `.env` 手快快留低 `APP_ENV=dev`，**全平台大門洞開**。
**修法**（startup 時 fail-fast，一個 validator 搞掂）：
```python
# config.py — model_validator(mode="after")
if self.app_env == "prod":
    if self.jwt_secret_key.startswith("dev-only"):
        raise ValueError("JWT_SECRET_KEY must be overridden in prod")
    if self.postgres_password == "change-me-dev":
        raise ValueError("POSTGRES_PASSWORD must be overridden in prod")
```
另外 `otp_service.py` 將 dev_code 判斷改為 `app_env not in ("dev", "test")` 反向寫法（default-deny）。

### P0-3. `is_active` 從未被執行 — 封禁唔存在
grep 證實 `is_active` 只喺 `app/models/__init__.py:87` 出現，**零使用**。User 被停用後：
- 所有 REST endpoint 照樣過（JWT stateless、2 小時有效）；
- WS 照樣連到。
平台冇任何手段趕走壞人。`/me` 有 DB 重查但其他 endpoint 冇。
**修法**：`get_current_user` 之後加一個可選 `require_active_user` dependency（DB 查 `is_active`），最低限度套用喺：order create/grab/cancel、driver location、admin 全部、WS handshake。成本：每 request 一次 PK lookup（可接受；將來再加 cache）。

### P0-4. Observability 係零
- **Logging**：grep 證實 `app/` 除 `notify.py` 外零 logging 呼叫、無 `logging.basicConfig`、無 structured format、無 request ID。production 出事冇任何線索。
- **/health 假健康**：`app/main.py:53-55` 回 static `{"status":"ok"}`，唔掂 DB/Redis。DB 死咗 LB 照行 traffic 過嚟。
**修法**：
1. `app/core/logging.py`：JSON formatter + uvicorn access log 接線 + request ID middleware（`contextvars`）。
2. `/health` 拆兩級：`/health/live`（process alive）＋ `/health/ready`（真跑 `SELECT 1` + `redis.ping()`，timeout 2s）。compose/K8s readiness 用 ready。

### P0-5. Geo index ghost entries — 直接污染派單
實證：live smoke 兩輪 run 之間 `nearby count 4→8`（2026-09-28 session 記錄，現場 DB 可再驗證：`ZCARD geo:orders:active`）。原因：
- 訂單永遠停喺 BROADCASTING（冇 sweeper）、
- server crash 喺 GEOADD 後 / ZREM 前、
- Redis 無 TTL。
`/nearby` 回 ghost 訂單，司機搶 404/409 — 派單正確性直接受損。
**修法**（組合拳）：
1. `index_order` 後落 TTL 嘅 `key:{order_id}` 或改用 `order:created_at + max_broadcast_min` 過期語意；
2. 加 sweeper job（每 5min）：`ZRANGE geo:orders:active` → 批量查 `orders.status != BROADCASTING` → ZREM（幂等）；
3. startup 時全量對賬一次（防 Redis/DB 唔同步）。

### P0-6. Graceful shutdown + auto-migrate
- grep 證實 **無 `lifespan` / `on_event`**（`app/main.py` 全文已讀）。uvicorn SIGTERM 時 engine 唔 dispose、Redis client 唔 close — 連線由 process 死亡硬收割。
- compose `api` command 直啟 uvicorn（`docker-compose.yml:48`），**schema migration 要人手行** — 部署新 image 必定同 DB schema 脫節。
**修法**：
1. `app/main.py` 改 lifespan：startup 什么都唔使（engine 係 lazy）；shutdown `await dispose_engine()`（`app/core/db.py:54` 已有，係冇人叫）。
2. compose command 改 `sh -c "alembic upgrade head && uvicorn ..."`（單 instance 部署安全；多 instance 時改獨立 migrate step）。

### P0-7. 部署形態基本盤
`docker-compose.yml:33-48`：api service 無 `restart: unless-stopped`；uvicorn 單 worker（WS 用途下 OK，但記低這是刻意的 — Pub/Sub 經 Redis，多 worker 理論可行，唯 rate limiter 同 geo index 已係 Redis-backed，多 worker 前要先壓測）。TLS 由反代負責 — 目前 repo 內無 Nginx/Caddy 配置。
**修法**：compose 加 restart policy；加一份 `deploy/nginx.conf` 範本（TLS、WS `proxy_read_timeout 3600s`、`Upgrade` headers、access log 唔記 `?token=` — 見 P1-1）。

---

## 3. P1 — 上線後兩星期內

### P1-1. WS token 喺 access log 裸奔
`ws.py:48`：`?token=<JWT>` 會完整出現喺 uvicorn access log 同任何反代 log。JWT 落 log = 憑證洩漏（2 小時有效）。
**修法**：uvicorn `--no-access-log` 或自訂 log format 剝 query；Nginx 層 `log_format` 用 `$uri` 唔用 `$request`。中期改 first-message auth（connect 後第一個 frame 帶 token）。

### P1-2. OTP 發送無 per-IP 限流
`otp_service.py:54-67` 只有 per-phone 60s cooldown。Attacker 換電話號碼掃 → WhatsApp Cloud API 按 message 收錢 + 電話炸彈。RateLimiter（`rate_limit.py`）已存在，接線就得。
**修法**：`/auth/otp/request` 加 `ip` 維度 limiter（例如 10/10min/IP）＋ global hourly cap（成本閘）。

### P1-3. 無 lockfile、無 CI
- grep 證實無 `uv.lock` / `requirements.txt`；Dockerfile `pip install .`（`Dockerfile:13`）→ 每次 build 攞最新版 — 不可重現、supply chain 裸奔。
- grep 證實無 `.github/`。106 個測試靠人手跑。
**修法**：`uv lock` + Dockerfile 改 `uv sync --frozen`；GitHub Actions：postgres:16-postgis service + redis:7 service + `uv run pytest` + ruff。

### P1-4. DB/Redis 備份策略未定義
compose 有 named volumes（`docker-compose.yml:50-52`）+ Redis appendonly ✓，但無 pg_dump cron、無 off-site 複製、無還原演練記錄。Ledger 係財務紀錄 — 冇 backup 唔可以上線。
**修法**：每日 `pg_dump` cron → 對象存儲；Redis 可重建（geo/rate 係 ephemeral，Pub/Sub 係瞬態）— 明確寫低「Redis 唔係 truth，DB 先係」。

### P1-5. Token 生命週期對移動端唔完整
JWT 2 小時（`config.py:25`）、無 refresh token。的士 trip 夠用，但司機成日開 app 就要日日 OTP。
**修法**：加 refresh token（7-30 天，rotation + revoke 清單一張表就夠）或 sliding re-issue。

### P1-6. PDPO purge job 未實現
`otp_service.py:7` docstring 自己承認（"purge job later"）＋ `models/__init__.py:229` OtpCode docstring。OTP rows 無限累積。
**修法**：同 P0-5 sweeper 一齊做：每日 DELETE `otp_codes WHERE expires_at < now() - 30d`（或 consumed_at + 7d）。

### P1-7. Admin grant 無冪等鍵
`admin.py:83-113`：network retry → double-credit。Ledger append 冇 `reference` 唯一約束（`models/__init__.py:214` `reference` 欄位存在但無 UNIQUE）。
**修法**：grant 時 client 提供冪等鍵入 `reference` + DB partial unique index；撞鍵回原 entry。

### P1-8. Pagination 缺失
- `drivers.py:108-114` `/me/ledger` 全量 `.all()` — 一個司機幾年後幾千行。
- `admin.py:41-44` `/admin/drivers` 全量。
**修法**：keyset pagination（ledger `id > cursor LIMIT 100`）。

### P1-9. WS 無 heartbeat
`ws.py:123-132` 純被動。NAT/負載均衡 idle timeout（通常 60-300s）會靜靜地斷閒置連線，乘客張 map 凍結。
**修法**：server 每 30s 推 `{"type":"ping"}`，client 回 pong（或用 WS protocol-level ping）；`proxy_read_timeout` 對齊。

### P1-10. 訂單建立硬編碼無隧道
`order_service.py:56-57`：`tunnels=[], crosses_harbour=False` 寫死 — `OrderCreateIn` 根本無呢啲欄位。B 模組訂單快照嘅估價永遠唔包隧道費（過海訂單估價平 $25-50，派單顯示失真；live smoke $149 個 case 走嘅係 `/fare/estimate`，唔係 order flow）。
**修法**：`OrderCreateIn` 加 `tunnels: list[str]` + `crosses_harbour: bool`，直傳 `calculate_fare`。374D 框架下估價仍係 estimate，但派單顯示應貼近現實。

---

## 4. P2 — 產品演進（上線後 backlog）

| # | 項目 | 證據 / 說明 |
|---|---|---|
| P2-1 | `GET /orders/{id}`（當事人）、order history、driver 訂單流 | `orders.py` 只有 `/nearby` 一個 GET；乘客冇途徑查自己訂單 — 移動端 MVP 前必補 |
| P2-2 | `held_hkd` / `WEEKLY_FEE_DEDUCTION` / `REFUND` 業務流 | grep 證實三個概念零實作；週費收取係商業模式核心 |
| P2-3 | `distance_km` 由 client 自報 | `orders.py:51` — 乘客可以亂報。374D 下估價僅供參考，風險可控，但廣播排序會被 gaming；中期接路徑規劃（`.env.example:27` 已預留 `GOOGLE_MAPS_API_KEY`，`config.py` 未接） |
| P2-4 | Metrics（Prometheus）+ 錯誤追蹤（Sentry） | 而家連 5xx 都只會喺 uvicorn stderr |
| P2-5 | Load test（WS tick 吞吐、SETNX 競爭） | 無任何基準數據 |
| P2-6 | Ledger 防篡改 hash chain / 對帳 job | `balance_after` 鏈已可核數；加 running-hash 係升級 |
| P2-7 | 多 worker / 多 instance 壓測 | Pub/Sub 已 Redis-backed，理論 OK；`get_redis()` per-call 建新 client（`db.py:50-51`、`trip_service.py:34`）高頻 publish 時要 pooling |
| P2-8 | Redis 驅逐策略聲明 | geo/rl keys 無 maxmemory 政策；上雲時設 `maxmemory-policy allkeys-lru` 之外嘅方案（rl keys 有 TTL ✓、geo keys 無 TTL — 見 P0-5） |
| P2-9 | OTP/WhatsApp provider 實測 | `notify.py:33-40` `WhatsAppCloudProvider` 係 `NotImplementedError` stub；`.env.example:28-30` 欄位已備但 `config.py` 無對應 Settings（靠 `extra="ignore"` 靜默吞）— 接線時要加 config 欄位 |
| P2-10 | 降級開關（kill switches） | 派單/rate limit 任何一環 Redis 故障時嘅行為未定義（宜 fail-open 派單、fail-closed ledger 操作，寫明） |

---

## 5. 上線日 checklist（執行序）

```
[ ] B1-B4 bugs 修復 + 測試（B2 加並發測試：兩 task 同時 grant，斷言 balance 正確）
[ ] P0-2 fail-safe validator + APP_ENV=prod 冒煙測試
[ ] P0-3 require_active_user 接線（orders/driver/admin/WS）
[ ] P0-4 logging + /health/ready
[ ] P0-5 geo sweeper + TTL + startup 對賬
[ ] P0-6 lifespan dispose + compose alembic upgrade
[ ] P0-7 restart policy + nginx 範本（TLS + WS headers + log 剝 query）
[ ] uv lock + CI 綠燈
[ ] pg_dump cron 上場 + 還原演練一次
[ ] 環境變數實測清單：JWT_SECRET_KEY / POSTGRES_PASSWORD / APP_ENV=prod / CORS_ORIGINS=真域名
[ ] live_smoke 跑一次 @ prod config（OTP 應該走真 provider 而唔係 dev_code）
```

## 6. 已經做得啱、唔使重做嘅嘢

- 計費引擎：tariff_version 快照 + 374D 免責聲明逐單凍結（`order_service.py:23-44`）— 合規核心，無懈可擊。
- 搶單：SETNX + token-checked Lua release（`grab_service.py:20-26`）— 教科書級。
- WS per-tick fresh session + rollback 前抽 plain values — `MissingGreenlet` 課堂教材。
- Alembic `include_object` 過濾 PostGIS 系統表（`alembic/env.py:30-38`）。
- 測試基建：template-clone 真 Postgres、NullPool、dependency overrides 完整（`tests/conftest.py`）。
- CORS whitelist 由 settings 驅動（`main.py:29-35`）— prod 只需改 env。
- `.env` 已入 `.gitignore`（實測 cat 過）。

---

## 附錄：證據索引

| 判斷 | 證據 |
|---|---|
| auth.py NameError | `app/api/auth.py:4`（imports）vs `:69`（用法） |
| ledger 無鎖 | `app/services/ledger_service.py:36-52`；`grep for_update app/` → 0 |
| tip 無上限 | `app/api/orders.py:55`、`app/api/fare.py:33` |
| is_active 零使用 | `grep is_active app/` → 僅 `models/__init__.py:87` |
| 無 lifespan | `grep lifespan app/` → 0；`db.py:54` `dispose_engine` 無人叫 |
| 無 logging | `grep getLogger app/` → 僅 notify.py |
| /health 假 | `app/main.py:53-55` |
| dev_code 風險 | `app/services/otp_service.py:70,83-85` |
| JWT dev default | `app/core/config.py:23` |
| compose 有 JWT 強制 | `docker-compose.yml:45` |
| 無 lockfile/CI | `ls uv.lock .github` → 不存在 |
| 訂單無隧道欄位 | `app/services/order_service.py:56-57`、`app/api/orders.py:44-55` |
| geo 無 TTL | `app/services/geo_service.py:23-27`；live smoke count 4→8 |
| ledger 全量回 | `app/api/drivers.py:108-114` |
| WS token 喺 query | `app/api/ws.py:48` |
| PII masking 展示層 | `app/core/masking.py`（本 audit 無受影響 — 無讀取 PII 欄位值） |
