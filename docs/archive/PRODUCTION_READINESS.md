# realtaxihk Backend — MVP → Production Readiness Audit

> **EN — Summary.** The audit that moved this codebase from MVP to
> production-hardened: **4 bugs, 7 P0, 10 P1 and 10 P2 findings, all closed**.
> Each entry carries its evidence and its fix. This is a **dated snapshot of a
> past state** — it records what was wrong *then*, and much of the referenced
> code has since been rewritten.
>
> **Why line numbers below are stale, and deliberately not renumbered.** The
> file paths still resolve; the `file:line` references do not. Renumbering them
> would make old findings *look* like descriptions of the current code, when
> they describe code that no longer exists in that form. Leaving them stale is
> the honest option — see `docs/WORK_SUMMARY.md` §4D for the same reasoning
> applied elsewhere.
>
> **中文摘要**：本文件是把這個 codebase 由 MVP 推到 production-hardened 的審計記錄——
> **4 bugs + 7 P0 + 10 P1 + 10 P2，全部已結案**。這是**帶日期的過去狀態快照**：
> 它記錄的是「當時」有什麼問題，而其中很多代碼其後已被改寫。
> 行號刻意**不重新編號**——重新編號會令舊發現假裝在描述現況（同 `WORK_SUMMARY.md` §4D）。

> **⚠️ 行號引用已失效（2026-10-01 註記）**：本文所有結論附 `file:line` 證據，
> 但 `app/models/` 已由單一 `__init__.py` 拆成 5 個 bounded-context 模組
> （`user.py` / `admin.py` / `fleet.py` / `licence.py` / `_base.py`），
> 且多個 API／service 檔在此後有大幅改動。**檔案層級路徑仍有效，行號不再準確。**
> 審計的**結論**（4 bugs + 7 P0 + 10 P1 + 10 P2 全部修復）仍然成立。

> **⚠️ 更正（2026-10-02）**：下文聲稱 `prod_boot_drill.py` 為 PASS，但實測當時是
> **6/7**。「prod with proper secrets」一例自 P-2 加入 SMTP／`PUBLIC_BASE_URL` 的
> prod 開機要求之後就**靜靜地失效** —— 該例只設了三個 secret，未設其餘必填項，
> 於是 validator 依設計拒絕啟動，drill 便報 BAD。CI 只跑 ruff + pytest，**不會跑這支
> drill**，所以沒有人看見。已補齊該例的設定並把相關變數加入 `MANAGED`（避免環境
> 繼承令案例以錯誤理由通過），現為 **7/7**。教訓：沒有進 CI 的驗證腳本會腐爛。

- **掃描日期**：2026-09-29
- **方法**：不靠任何 session 記憶 — 逐檔讀取 `app/`（api 8 檔、core 8 檔、services 9 檔、models）＋ `tests/conftest.py`、`alembic/env.py`、`Dockerfile`、`docker-compose.yml`、`pyproject.toml`、`.env.example`、`README.md`，再用 grep 驗證每個「有沒有」判斷（for_update、lifespan、is_active 使用、lockfile、CI、logging）。所有結論附 file:line 證據。
- **基準**：commit `88a09ee`（main），106/106 tests，3 commits 歷史。

---

## 修復記錄（2026-09-29，同日完成）

**全部 4 bugs + 7 P0 + 10 P1 + 10 P2 已修復並驗證**。最終狀態：**120/120 tests GREEN**（106 原有 + 14 新 hardening regression suite `tests/test_hardening.py`）、live smoke 9/9、prod fail-fast drill PASS（`scripts/verify/prod_boot_drill.py`）。

| 層 | 修復後狀態 |
|---|---|
| Domain logic / 測試 | ✅ 120/120（+14 hardening） |
| 資料完整性 | ✅ B2/P0-1 ledger `with_for_update` 行級鎖 + 並發測試（asyncio.gather 2×grant → 餘額精確） |
| Fail-safe | ✅ P0-2 `model_validator` fail-fast（prod+dev secret 拒絕開機，實機 drill 驗證） |
| 封禁 | ✅ P0-3 `require_active_user`／`require_admin` DB re-check；WS handshake 亦檢查（403） |
| Observability | ✅ P0-4 JSON stdout logging＋request-ID middleware；`/health` 真檢 DB+Redis（503 on failure） |
| Geo ghosts | ✅ P0-5 `MaintenanceService.sweep_geo_index()`：訂單離開 BROADCASTING→ZREM；60s 週期 sweeper（實戰清了 1 條 ghost） |
| 部署管道 | ✅ P1-3 `uv.lock`；P1-4 GitHub Actions CI（PostGIS+Redis services，`uv sync --frozen` + pytest）；P0-6/7 compose restart: unless-stopped＋`alembic upgrade head` 前置 |
| 合規 | ✅ P1-6 PDPO purge job（otp_codes 24h、refresh_tokens 30d、rate keys 7d） |
| Auth | ✅ P1-5 refresh token 輪換（SHA-256 hash at rest、single-use、`/auth/refresh` `/auth/logout`） |
| Notify | ✅ P2-9 真 WhatsApp Cloud API provider（httpx，template message，fail-closed on prod OTP） |

**新增檔案**：`app/core/logging.py`、`app/services/infra/maintenance.py`、`app/services/auth/refresh_service.py`、`alembic/versions/b4f1c2a7d901_*.py`（refresh_tokens 表＋ledger idempotency index）、`.github/workflows/ci.yml`、`uv.lock`、`tests/test_hardening.py`、`scripts/verify/prod_boot_drill.py`。

**過程中修復的 regression**：Redis client singleton 跨 event-loop 污染（TestClient portal 每 test 新 loop → pooled connection 綁死舊 loop → WS suite hang）。P2-7 一度改為 **per-call client**，但這樣每次 request 都開一條新 Redis socket（SEC-18 的 revocation check 就是熱路徑）。現已改為 **per-loop cache**（`db.py`，`WeakKeyDictionary` keyed on the running loop）：production 單 loop 只有一個 client，測試每個 TestClient loop 各自一個，兩邊都正確，也沒有 socket churn。

**上線日仍然要做（人手/環境項，代碼之外）**：
1. 產生強 JWT secret（`openssl rand -hex 32`）＋強 POSTGRES_PASSWORD 入 prod .env — fail-fast 會把關
2. Nginx/Caddy TLS 反代 + `X-Forwarded-For`（rate limit 按 IP 才有效）＋ CORS lockdown（`CORS_ORIGINS` env）
3. DB 備份 cron（`pg_dump` nightly + off-host retention）
4. WhatsApp Cloud API token／template（`WHATSAPP_*` env）＋ FCM credentials
5. 首次 `docker compose up` 部署演練 + `/health` 輪詢驗收
6. 用 `scripts/ops/create_admin.py --phone +852XXXXXXXX` 建立第一個 ADMIN（見 P2-11）

---

## 0. Verdict

> **目前狀態（2026-09-29，第三輪後）：沒有任何上線阻塞項。**
> §2–§4 以下保留**原始審計內容**作記錄；逐項的修復位置見 `docs/SECURITY_AUDIT.md` §0.1，
> 以及本檔上方的「新增檔案 / 修復的 regression / 上線日仍然要做」段。
> 未做的只剩 §4 的 P2 backlog（產品演進）與 §0.1 兩項刻意延後（JWT `iss`/`aud`、部署環境覆寫 `CORS_ORIGINS`）。

**原始判定（2026-09-28，保留作對照）**：MVP 核心域邏輯紮實（計費、搶單、ledger、WS 全有 TDD 背書），但有 7 個 P0 項未過關 — 其中 2 個是掃描中發現的真 bug。錢相關（ledger 並發）與 fail-safe（JWT/OTP 誤設定）必須先修復才能上線。

| 層 | 2026-09-28 | 現在 |
|---|---|---|
| Domain logic / 測試 | ✅ 106/106 | ✅ 186/186 |
| 資料完整性 | ⚠️ ledger 無行級鎖 → 並發 lost update（P0-1） | ✅ `FOR UPDATE` + 條件式 UPDATE 仲裁 |
| Fail-safe | ❌ prod 誤設定無攔截（P0-2） | ✅ fail-closed validator，7/7 開機情境實測 |
| Observability | ❌ 零 logging config、`/health` 假健康（P0-4） | ✅ structured JSON log + request id；`/health` 真查 DB/Redis，不 OK 時回 503 |
| 部署管道 | ⚠️ 無 lockfile、無 CI、container 不會自動 migrate | ✅ `uv.lock` + GitHub Actions + `alembic upgrade head` 前置 |
| 合規 | ⚠️ PDPO purge job 未實現 | ✅ `pdpo_purge` job |
| Auth | ⚠️ 2 小時 JWT、無 refresh | ✅ refresh rotation + per-user revocation epoch |

---

## 1. 掃描中發現的真 bugs（即刻修，每個 <10 行）

### B1. `/api/v1/auth/me` 對已刪除 user 會 NameError → 500
`app/api/auth.py:4` 只 import `APIRouter, Depends`；`auth.py:69` 在 `me()` 內 `raise HTTPException(...)` — 該名稱從未 import。
觸發條件：JWT 有效但 user row 消失（PDPO 刪除權一落地就會發生）。
**修法**：頂部補 `from fastapi import HTTPException`。

### B2. Ledger `append()` 沒有 `with_for_update` → 並發 lost update
`app/services/ledger/ledger_service.py:36-52`：balance 更新是 ORM read-modify-write（SELECT → Python 相加 → UPDATE 覆寫）。兩個並發 request（同時 grant、grant+penalty）均讀取 balance=500，各自 +100，後 commit 覆蓋前者 → **帳目靜靜地錯**。
grep 全 repo 證實 `for_update` 出現次數 = 0。SETNX 只保護搶單，不保護 ledger。
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

### B3. `tip` 沒有上限 → DB overflow → 500
`app/api/orders.py:55` 與 `app/api/fare.py:33`：`tip: Decimal = Field(ge=0)` 無 `le`。`1e50` tip → `Numeric(10,2)` overflow → asyncpg 錯 → 500。純 validation 缺口（`discount_percent` 有 le=100，唯獨 tip 漏）。
**修法**：兩處補 `le=10000`（HK$10,000 tip cap）。

### B4. `tracking.py` 用 `__import__("datetime")` hack
`app/api/tracking.py:43`：`__import__("datetime").datetime.now(...)` — 能跑但是 production code smell。
**修法**：頂部 `import datetime` 正常寫。

---

## 2. P0 — 上線硬阻塞

### P0-1. （=B2）Ledger 並發安全
錢的事情零容忍。見 B2。

### P0-2. Prod fail-safe：誤設定就全平台失守
- **JWT secret**：`app/core/config.py:23` 有 dev default secret。docker-compose 有 `:?` 強制（`docker-compose.yml:45`），但裸 uvicorn / PM2 / systemd 部署完全沒有保護。`app_env=prod` + dev secret = 任何人可以自簽 token 冒充任何 user。
- **OTP dev_code**：`app/services/auth/otp_service.py:70,83-85` — `app_env=dev` 時回 `dev_code="123456"` 且繞過 WhatsApp。若 prod 機器 `.env` 匆忙留下 `APP_ENV=dev`，**全平台大門洞開**。
**修法**（startup 時 fail-fast，一個 validator 即可完成）：
```python
# config.py — model_validator(mode="after")
if self.app_env == "prod":
    if self.jwt_secret_key.startswith("dev-only"):
        raise ValueError("JWT_SECRET_KEY must be overridden in prod")
    if self.postgres_password == "change-me-dev":
        raise ValueError("POSTGRES_PASSWORD must be overridden in prod")
```
另外 `otp_service.py` 將 dev_code 判斷改為 `app_env not in ("dev", "test")` 反向寫法（default-deny）。

### P0-3. `is_active` 從未被執行 — 封禁不存在
grep 證實 `is_active` 只在 `app/models/user.py:147` 出現，**零使用**。User 被停用後：
- 所有 REST endpoint 照樣過（JWT stateless、2 小時有效）；
- WS 照樣連到。
平台沒有任何手段趕走壞人。`/me` 有 DB 重查但其他 endpoint 沒有。
**修法**：`get_current_user` 之後加一個可選 `require_active_user` dependency（DB 查 `is_active`），最低限度套用於：order create/grab/cancel、driver location、admin 全部、WS handshake。成本：每 request 一次 PK lookup（可接受；將來再加 cache）。

### P0-4. Observability 是零
- **Logging**：grep 證實 `app/` 除 `notify.py` 外零 logging 呼叫、無 `logging.basicConfig`、無 structured format、無 request ID。production 出事沒有任何線索。
- **/health 假健康**：`app/main.py:53-55` 回 static `{"status":"ok"}`，不查 DB/Redis。DB 死了 LB 仍會將 traffic 送過來。
**修法**：
1. `app/core/logging.py`：JSON formatter + uvicorn access log 接線 + request ID middleware（`contextvars`）。
2. `/health` 拆兩級：`/health/live`（process alive）＋ `/health/ready`（真跑 `SELECT 1` + `redis.ping()`，timeout 2s）。compose/K8s readiness 用 ready。

### P0-5. Geo index ghost entries — 直接污染派單
實證：live smoke 兩輪 run 之間 `nearby count 4→8`（2026-09-28 session 記錄，現場 DB 可再驗證：`ZCARD geo:orders:active`）。原因：
- 訂單永遠停在 BROADCASTING（沒有 sweeper）、
- server crash 在 GEOADD 後 / ZREM 前、
- Redis 無 TTL。
`/nearby` 回 ghost 訂單，司機搶 404/409 — 派單正確性直接受損。
**修法**（組合拳）：
1. `index_order` 後設 TTL 的 `key:{order_id}` 或改用 `order:created_at + max_broadcast_min` 過期語意；
2. 加 sweeper job（每 5min）：`ZRANGE geo:orders:active` → 批量查 `orders.status != BROADCASTING` → ZREM（幂等）；
3. startup 時全量對賬一次（防 Redis/DB 不同步）。

### P0-6. Graceful shutdown + auto-migrate
- grep 證實 **無 `lifespan` / `on_event`**（`app/main.py` 全文已讀）。uvicorn SIGTERM 時 engine 不 dispose、Redis client 不 close — 連線由 process 死亡硬收割。
- compose `api` command 直接啟動 uvicorn（`docker-compose.yml:48`），**schema migration 需人手執行** — 部署新 image 必定與 DB schema 脫節。
**修法**：
1. `app/main.py` 改 lifespan：startup 什麼都不需要（engine 是 lazy）；shutdown `await dispose_engine()`（`app/core/db.py:54` 已有，只是沒有人呼叫）。
2. compose command 改 `sh -c "alembic upgrade head && uvicorn ..."`（單 instance 部署安全；多 instance 時改獨立 migrate step）。

### P0-7. 部署形態基本盤
`docker-compose.yml:33-48`：api service 無 `restart: unless-stopped`；uvicorn 單 worker（WS 用途下 OK，但記下這是刻意的 — Pub/Sub 經 Redis，多 worker 理論可行，唯 rate limiter 與 geo index 已是 Redis-backed，多 worker 前要先壓測）。TLS 由反代負責 — 目前 repo 內無 Nginx/Caddy 配置。
**修法**：compose 加 restart policy；加一份 `deploy/nginx.conf` 範本（TLS、WS `proxy_read_timeout 3600s`、`Upgrade` headers、access log 不記 `?token=` — 見 P1-1）。

---

## 3. P1 — 上線後兩星期內

### P1-1. WS token 在 access log 裸奔
`ws.py:48`：`?token=<JWT>` 會完整出現在 uvicorn access log 與任何反代 log。JWT 落 log = 憑證洩漏（2 小時有效）。
**修法**：uvicorn `--no-access-log` 或自訂 log format 剝 query；Nginx 層 `log_format` 用 `$uri` 不用 `$request`。中期改 first-message auth（connect 後第一個 frame 帶 token）。

### P1-2. OTP 發送無 per-IP 限流
`otp_service.py:54-67` 只有 per-phone 60s cooldown。Attacker 換電話號碼掃 → WhatsApp Cloud API 按 message 收錢 + 電話炸彈。RateLimiter（`rate_limit.py`）已存在，接線即可。
**修法**：`/auth/otp/request` 加 `ip` 維度 limiter（例如 10/10min/IP）＋ global hourly cap（成本閘）。

### P1-3. 無 lockfile、無 CI
- grep 證實無 `uv.lock` / `requirements.txt`；Dockerfile `pip install .`（`Dockerfile:13`）→ 每次 build 取得最新版 — 不可重現、supply chain 裸奔。
- grep 證實無 `.github/`。106 個測試靠人手執行。
**修法**：`uv lock` + Dockerfile 改 `uv sync --frozen`；GitHub Actions：postgres:16-postgis service + redis:7 service + `uv run pytest` + ruff。

### P1-4. DB 備份策略 — **✅ 已實作（2026-09-30）**

原本：compose 有 named volumes（`docker-compose.yml:50-52`）+ Redis appendonly ✓，但無 pg_dump cron、無 off-site 複製、無還原演練記錄。Ledger 是財務紀錄 — 沒有 backup 不可以上線。

**現況**：`scripts/ops/db_backup.py`（`backup` / `verify` / `list` 三個子命令）＋
`tests/test_db_backup.py`（27 個 test）。已對真 Postgres 跑通，不是只寫了：

```
[backup] 75,225 bytes · sha256 9533d41e… · archive TOC: 112 entries
[verify]   49 table(s), 17,627 row(s)
[verify] restored 49 table(s), 17,627 row(s)
[verify] PASS — all 49 table(s) match the source exactly
```

- **`backup`**：`pg_dump -Fc --no-owner --no-privileges` → sha256 sidecar →
  `pg_restore --list` 驗 archive 可讀 → 選擇性 off-host 上載 → GFS 保留策略
  （預設 7 daily + 4 weekly，按**日曆距離**而不是數量 —— weekly 層要承受得住
  「幾日後才發現」的問題）。
- **`verify`**：就是還原演練。建立 scratch DB → `pg_restore --exit-on-error`
  → 逐表比較 `count(*)`（不用 `reltuples` 估算值）→ drop scratch。這是唯一
  能捕捉到「archive 結構正常但資料不齊」的檢查。
- **`--via auto|host|docker`**：本機無 `pg_*` 工具（只有 realtaxi-db container
  裡面有），真 server 通常有。同一個 code path，兩條路都行得通。
- **Redis 刻意不備份**：rate limit counter / grab lock / Pub-Sub 全部 ephemeral，
  DB 才是 truth。（`--upload-cmd` / `--upload-verify-cmd` 留給你填 provider —
  destination 是部署決定，不應該 hard-code 進 script。）

**仍然要你做**：選擇 off-host destination，然後上 cron：
`17 3 * * * cd /srv/realtaxihk && .venv/bin/python scripts/ops/db_backup.py backup`。
`docs/WORK_SUMMARY.md` §4A 原本當這項「卡在沒有 credentials」，實際上只是卡在
destination 未選擇 —— script 本身已經寫完與驗完。

### P1-5. Token 生命週期對移動端不完整
JWT 2 小時（`config.py:25`）、無 refresh token。的士 trip 夠用，但司機經常開 app 就要每天 OTP。
**修法**：加 refresh token（7-30 天，rotation + revoke 清單一張表就夠）或 sliding re-issue。

### P1-6. PDPO purge job 未實現
`otp_service.py:7` docstring 自己承認（"purge job later"）＋ `models/__init__.py:229` OtpCode docstring。OTP rows 無限累積。
**修法**：與 P0-5 sweeper 一起做：每日 DELETE `otp_codes WHERE expires_at < now() - 30d`（或 consumed_at + 7d）。

### P1-7. Admin grant 無冪等鍵
`admin.py:83-113`：network retry → double-credit。Ledger append 沒有 `reference` 唯一約束（`models/__init__.py:214` `reference` 欄位存在但無 UNIQUE）。
**修法**：grant 時 client 提供冪等鍵入 `reference` + DB partial unique index；撞鍵回原 entry。

### P1-8. Pagination 缺失 — ✅ 已修
- ~~`drivers.py:108-114` `/me/ledger` 全量 `.all()`~~ → `drivers.py:111-136` keyset（`after_id` + `limit`）。
- ~~`admin.py:41-44` `/admin/drivers` 全量~~ → `admin.py:58,188` `.limit(limit).offset(offset)`。

### P1-9. WS 無 heartbeat
`ws.py:123-132` 純被動。NAT/負載均衡 idle timeout（通常 60-300s）會靜靜地斷閒置連線，乘客的 map 凍結。
**修法**：server 每 30s 推 `{"type":"ping"}`，client 回 pong（或用 WS protocol-level ping）；`proxy_read_timeout` 對齊。

### P1-10. 訂單建立硬編碼無隧道
`order_service.py:56-57`：`tunnels=[], crosses_harbour=False` 硬編碼 — `OrderCreateIn` 根本無這些欄位。B 模組訂單快照的估價永遠不包隧道費（過海訂單估價便宜 $25-50，派單顯示失真；live smoke $149 的 case 走的是 `/fare/estimate`，不是 order flow）。
**修法**：`OrderCreateIn` 加 `tunnels: list[str]` + `crosses_harbour: bool`，直傳 `calculate_fare`。374D 框架下估價仍是 estimate，但派單顯示應貼近現實。

---

## 4. P2 — 產品演進（上線後 backlog）

| # | 項目 | 證據 / 說明 |
|---|---|---|
| P2-1 | ~~`GET /orders/{id}`（當事人）、order history、driver 訂單流~~ **✅ 已實作** | `GET /orders?role=passenger或driver&limit&before_id`（keyset 分頁，cursor 在 caller 自己 scope 內解析 — SEC-26）＋ `GET /orders/{order_id}`（只限當事人或 ADMIN）。前端可直接用，不需要再等 |
| P2-2 | ~~`held_hkd` / `WEEKLY_FEE_DEDUCTION` / `REFUND` 業務流~~ **✅ 已實作** | 週費結算 `SettlementService`（ISO 週冪等，ledger reference `weekly:{driver}:{period}`）＋退款流程 `RefundService`（司機申請凍結 → 管理員審批）。端點：`POST /admin/settlement/weekly/run`、`POST /drivers/me/refund/request`、`GET /admin/refunds`、`POST /admin/refunds/{id}/decision`。對帳恆等式：`balance_hkd + held_hkd == sum(ledger.amount_hkd)`。**2026-10-08：部分退款已實作**（`amount_hkd` optional，批核後司機恢復 ACTIVE，`is_partial` 標記）。**仍未做**：實際打款渠道（只寫 ledger，轉帳仍線下處理） |
| P2-2b | `ADJUSTMENT` 手動調整業務流 | ✅ **已實作**（2026-09-30）。`LedgerEntryType.ADJUSTMENT` 之前只有 enum 定義、零實作，但**三個前端都已經渲染「調整」標籤**（`admin-web/js/views/driverDetail.js`、`admin-web/web/src/lib/labels.ts`、`mobile/lib/models/enums.dart`）——即 UI 承諾了一個後端永遠產生不了的 ledger type。新端點 `POST /admin/drivers/{id}/deposit/adjust`（ADMIN）：**金額 signed**（正=credit／負=debit）、`reason` 必填（調整無上游事件，reason 就是審計軌跡）、±HK$5,000 上限（調整是更正、不是付款渠道）、`reference` 走 `adj:{driver}:{reason-slug}:{key}` 命名空間（SEC-13，不會撞 grant/weekly/fleet/refund）。順帶修好兩個相關缺陷：① `_ledger_out` 之前**從不輸出 `created_by`**，令所有管理員操作在 DB 有記名但 API 完全看不到 —— 現已加（只限 admin 視角，司機 `/me/ledger` 不會有）；② `mobile/lib/models/enums.dart` 的 `isCredit` 硬編 `adjustment == true`，但調整是 signed，一半情況會顯示錯方向 —— 已**刪除**（無 production code 用過），direction 一律讀 `amount_hkd` 正負。**仍未做**：調整不會 activate 司機（刻意 —— 更正是指記帳，不是付款；維持 `DEPOSIT_REQUIRED`） |
| P2-3 | `distance_km` 由 client 自報 | `orders.py:51` — 乘客可以亂報。374D 下估價僅供參考，風險可控，但廣播排序會被 gaming；中期接路徑規劃（`.env.example:27` 已預留 `GOOGLE_MAPS_API_KEY`，`config.py` 未接） |
| P2-4 | ~~Metrics（Prometheus）~~ **✅ 已實作**；~~錯誤追蹤（Sentry）~~ **代碼已接好（2026-10-02）** | `/metrics` 已存在且 token-gated（`PROMETHEUS_ENABLED` + `METRICS_TOKEN`；未設 token 就完全不掛載 — SEC-22）。5xx 有 structured JSON log（`core/logging.py`）。Sentry 於 `app/main.py::create_app` 接好：有 `SENTRY_DSN` 就 init，`release` 綁 `_API_VERSION`（否則錯誤無法歸因到某次部署），並設 `max_request_body_size="never"`（PDPO —— 請求內文含電話號碼，SDK 預設的 `medium` 會把它送出）。**仍未做**：只差一個 DSN 未填。 |
| P2-5 | Load test（WS tick 吞吐、SETNX 競爭） | 無任何基準數據 |
| P2-6 | Ledger 防篡改 hash chain / 對帳 job | `balance_after` 鏈已可核數；加 running-hash 是升級 |
| P2-7 | 多 worker / 多 instance 壓測 | Pub/Sub 已 Redis-backed，理論 OK。~~`get_redis()` per-call 建新 client（`db.py:50-51`、`trip_service.py:34`）高頻 publish 時要 pooling~~ → 已改為 **per-loop cache**（`db.py`），production 單 loop 共用一個 client。**仍未做**：真正的多 worker 壓測 |
| P2-8 | Redis 驅逐策略聲明 | geo/rl keys 無 maxmemory 政策；上雲時設 `maxmemory-policy allkeys-lru` 之外的方案（rl keys 有 TTL ✓、geo keys 無 TTL — 見 P0-5） |
| P2-9 | ~~OTP/WhatsApp provider 實測~~ **✅ 已實作** | `notify.py:38-102` `WhatsAppCloudProvider` 已用 httpx 實作（template message 或 plain text，4xx 會 raise）；`config.py:130-134` 已有 `whatsapp_business_token`／`whatsapp_phone_number_id`／`whatsapp_api_version`／`whatsapp_otp_template`／`fcm_credentials_json`。prod 缺憑證時 fail-closed raise。**仍未做**：FCM v1 推送（要 service-account RS256 JWT） |
| P2-10 | 降級開關（kill switches） | 派單/rate limit 任何一環 Redis 故障時的行為未定義（宜 fail-open 派單、fail-closed ledger 操作，寫明） |
| P2-11 | ~~**首個 ADMIN bootstrap 路徑（上線阻塞）**~~ **✅ 已修** | 新增 `scripts/ops/create_admin.py`（`--list` / `--phone` / `--revoke` / `--yes`）。**UUID 是即時 `uuid.uuid4()` 生成，不再用固定值** — 原本 `00000000-0000-0000-0000-0000000000aa` 只存在於 `tests/conftest.py` 的 template DB，而且被手動種入 dev DB，等於一把「萬用鎖匙」。`APP_ENV=prod` 要 `--yes` 才會執行，而且一定會先印 target DB。promote 不會追溯舊 token（`require_admin` 會比對 live row），新 admin 要重新登入 |

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
[x] pg_dump cron 上場 + 還原演練一次 — script 完成並實跑 PASS（`scripts/ops/db_backup.py`）；用戶只需選擇 off-host destination
[ ] 環境變數實測清單：JWT_SECRET_KEY / POSTGRES_PASSWORD / APP_ENV=prod / CORS_ORIGINS=真域名
[ ] live_smoke 跑一次 @ prod config（OTP 應該走真 provider 而不是 dev_code）
```

## 6. 已經做得正確、不需要重做的事項

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
| ledger 無鎖 | `app/services/ledger/ledger_service.py:36-52`；`grep for_update app/` → 0 |
| tip 無上限 | `app/api/orders.py:55`、`app/api/fare.py:33` |
| is_active 零使用 | `grep is_active app/` → 僅 `models/__init__.py:87` |
| 無 lifespan | `grep lifespan app/` → 0；`db.py:54` `dispose_engine` 無人呼叫 |
| 無 logging | `grep getLogger app/` → 僅 notify.py |
| /health 假 | `app/main.py:53-55` |
| dev_code 風險 | `app/services/auth/otp_service.py:70,83-85` |
| JWT dev default | `app/core/config.py:23` |
| compose 有 JWT 強制 | `docker-compose.yml:45` |
| 無 lockfile/CI | `ls uv.lock .github` → 不存在 |
| 訂單無隧道欄位 | `app/services/order/order_service.py:56-57`、`app/api/orders.py:44-55` |
| geo 無 TTL | `app/services/order/geo_service.py:23-27`；live smoke count 4→8 |
| ledger 全量回 | `app/api/drivers.py:108-114` |
| WS token 在 query | `app/api/ws.py:48` |
| PII masking 展示層 | `app/core/masking.py`（本 audit 無受影響 — 無讀取 PII 欄位值） |
