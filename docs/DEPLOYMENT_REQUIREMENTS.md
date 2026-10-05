# 部署需求清單 / Deployment Requirements

> **EN — Summary.** The actionable checklist for a deploy: what must be
> provisioned, which secrets must exist before the first boot, and the order to
> do it in. Companion to [`DEPLOY_TARGET_DECISION.md`](DEPLOY_TARGET_DECISION.md)
> — that one says **which** target, this one says **what to prepare once it is
> chosen**.
>
> **Two things that bite in the wrong order:**
> - Credentials must exist **before** the first `docker compose up`, or nginx
>   restart-loops. Get the certificate with `certbot certonly --standalone`
>   first.
> - Most blockers here are **not** code and **not** credentials — they are
>   undecided choices (a hostname, a backup destination). Those are cheap to
>   resolve and expensive to defer, because everything downstream waits on them.
>
> **中文摘要**：可執行的部署清單——要準備什麼、哪些秘密必須在第一次啟動前就存在、
> 以及次序。與選型文件互補。兩個容易做錯次序的地方：憑證必須在第一次 `up` **之前**
> 存在（否則 nginx 無限重啟）；而這裡多數阻塞項**既不是程式問題也不是憑證問題**，
> 而是**未做的選擇**（主機名、備份目的地）。

- **日期**：2026-10-01
- **狀態**：可執行清單。與 `DEPLOY_TARGET_DECISION.md`（選型取捨）互補 ——
  該份說明「選擇哪一個」，本份說明「選定之後要準備什麼」。
- **權威來源**：全部由 repo 讀出（`app/core/config.py`、`docker-compose.yml`、
  `pyproject.toml`、`admin-web/web/package.json`、`mobile/pubspec.yaml`），
  非估算。

> 所有版本號、預設值、變數名都可在對應檔案核對。凡「必填」者，缺失會令
> 進程**啟動即失敗**（`config.py` 的 `_fail_closed`），不是執行期才出錯。

---

## 1. 硬性約束（選錯就裝不起來）

| # | 約束 | 證據 | 後果 |
|---|---|---|---|
| 1 | **PostgreSQL + PostGIS 擴展** | `app/models/`（`user.py` / `order` 相關）用 `geoalchemy2.Geography`；migration `9307e944a592` 建真 `geography(POINT,4326)`；`app/api/trips.py` SQL 層呼叫 `ST_AsText` | 純 Postgres / SQLite **不能**替代。啟動時 migration 會失敗 |
| 2 | **Redis ≥ 7** | `docker-compose.yml` SEC-06 註解：rate-limit counters、grab locks、**Pub/Sub** | Redis 5 不支援 `HELLO 3`，`redis-py` 8.x 連不上。**已實測失敗** |
| 3 | **Redis 為有狀態，非快取** | 同上；Pub/Sub 用於多 instance WS | 多 instance 必須**共用同一 Redis**；清空 Redis 會令 rate-limit 與 lock 失效 |
| 4 | **常駐 WebSocket** | `app/api/ws.py` `hub.publish/subscribe` | 排除純 request/response 的 serverless（Lambda、Cloud Run 預設） |
| 5 | **可跑 cron / 定時工作** | `scripts/ops/db_backup.py`（nightly）、`app/main.py` `_job_loop` | 需要長時間存活進程，或外部 scheduler |
| 6 | **HTTPS 終結點** | compose 註解「TLS 是 reverse proxy 的責任」；`PUBLIC_BASE_URL` prod 強制 `https://` | 需 nginx / Caddy / 平台 LB |

---

## 2. 執行環境

### 2.1 後端（API）

| 項目 | 要求 | 來源 |
|---|---|---|
| Python | **3.11+**（本地實測 3.13.12 / 3.11.5） | `pyproject.toml` `requires-python` |
| 套件管理 | `uv`（`uv.lock` 已鎖）；`uv pip install -r` 或 `uv sync` | `uv.lock` |
| 關鍵套件 | FastAPI、SQLAlchemy 2.0 **async**、asyncpg、geoalchemy2、alembic、redis-py ≥ 8、pydantic-settings、python-jose/pyjwt、passlib/bcrypt | `pyproject.toml` |
| Ruff | **0.16.9** 實測 | `pyproject.toml` `line-length = 100` |
| 進程模型 | Uvicorn workers ≥ 2（若多 worker，Redis Pub/Sub 必須共用） | `app/main.py` |

> **注意**：`uv` 建的 venv **沒有 pip**。裝套件要用 `uv pip install`，
> `python -m pip` 會直接失敗。

### 2.2 資料庫

| 項目 | 要求 |
|---|---|
| 引擎 | PostgreSQL **16.x**（compose 用 `postgis/postgis:16-3.4`）；PostGIS **3.4+** |
| 編碼 / 排序 | UTF-8；建議 `C` 或 `en_US.utf8`（本專案未依賴特定 collation） |
| 連線 | `postgresql+asyncpg://`（**async** driver，非 psycopg2） |
| Migration | `alembic upgrade head`（compose 的 `command` 已內建） |
| 擴展 | `postgis` 必須在 migration 前存在：`CREATE EXTENSION IF NOT EXISTS postgis;` |
| 驗證句 | `SELECT PostGIS_Version();` —— **動手前先跑這句** |

實測規模（本地 restore drill）：**49 張表 / 17,627 行**，屬小型資料庫。

### 2.3 Redis

| 項目 | 要求 |
|---|---|
| 版本 | **7.x**（實測 Windows 版 5.0.14.1 失敗，見約束 #2） |
| 用途 | rate-limit counter、grab lock、WS Pub/Sub、TOTP 待確認密鑰（5 分鐘 TTL） |
| 持久化 | **建議開啟 AOF**。rate-limit counter 遺失可接受；Pub/Sub 遺失只是斷線重連 |

### 2.4 管理後台（Console）

| 項目 | 要求 | 來源 |
|---|---|---|
| Node.js | **20+**（本地實測 22.22.2） | `package.json` |
| 建置 | `npm ci && npm run build` → `dist/`（**靜態檔**） | `admin-web/web/` |
| 產物大小 | 實測 `index.js` 285.6 kB（gzip **90.45 kB**）、`index.css` 12.5 kB（gzip 3.17 kB） | 實測 build |
| 路由 | **Hash routing**（`#/kyc`）→ 靜態主機**不需** rewrite rule | `App.tsx` |
| 託管 | 任何靜態伺服器；`admin-web/serve.py` 可反向代理 `/api/*` 到 :8000（同源、免 CORS preflight） | `serve.py` |

### 2.5 手機端（Mobile）

| 項目 | 要求 |
|---|---|
| Dart SDK | **3.12.0**（實測） |
| 狀態 | `flutter` CLI 需要可用 SDK；本機曾遇 `ERROR_PIPE_BUSY`，**改為直接跑 `dart`**（`dart tool/run_tests.dart`、`dart analyze`）即可，唔需要 Flutter CLI；APK 用 `cd mobile/android && ./gradlew :app:assembleDebug` |
| 部署 | 經 App Store / Play Store；**不屬後端部署範圍** |

---

## 3. 環境變數（完整清單）

### 3.1 必填 —— 缺失即啟動失敗

| 變數 | 格式 / 要求 | 產生方式 |
|---|---|---|
| `APP_ENV` | 必須是 `dev` \| `test` \| `prod` 之一。**無預設**；`production`/`PROD`/`staging` 一律拒絕 | 生產填 `prod` |
| `JWT_SECRET_KEY` | **≥ 32 字元**，且**≥ 8 個不同字元**。`dev-only*` 與兩個已提交值在 prod 會被拒 | `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | prod 不得為 `change-me-dev` | 高熵密碼 |
| `SMTP_HOST` + `SMTP_FROM` | prod 必須設定（否則註冊驗證信靜默失敗） | 郵件服務商 |
| `PUBLIC_BASE_URL` | prod 必須 `https://` 開頭（驗證連結用） | 你的公開網域 |

> `config.py` 的 `_fail_closed` 會在 **import 期**拋錯，訊息直接指名出錯的變數。
> 這是刻意設計：這些設定若跑到執行期才壞，全部是**靜默失敗**。

### 3.2 資料庫 / Redis

| 變數 | 預設 | 生產建議 |
|---|---|---|
| `POSTGRES_HOST` | `127.0.0.1` | compose 內用 service name `db` |
| `POSTGRES_PORT` | `5432` | 本地 compose 映射 `15433` |
| `POSTGRES_USER` | `realtaxi` | 保持（**非** `postgres`） |
| `POSTGRES_DB` | `realtaxihk` | 保持 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | compose 內 `redis://redis:6379/0` |

> `postgres_host` 用 **`127.0.0.1` 而非 `localhost`**：Windows 上 `localhost`
> 會先試 `::1`，而 Docker 發佈的 port 是 IPv4-only，實測每次連線慢 2 秒。

### 3.3 反向代理 / 安全（prod 必核）

| 變數 | 預設 | 生產建議 | 理由 |
|---|---|---|---|
| `TRUSTED_PROXY_COUNT` | `0` | **反代後設 1** | 只有在 > 0 時才讀 `X-Forwarded-For`，且由右往左取。設 0 = 完全忽略，客戶端偽造不了來源 IP |
| `CORS_ORIGINS` | 4 個 localhost | 你的 console 正式 origin | origin 逐字比對，`127.0.0.1` ≠ `localhost` |
| `SECURITY_HEADERS_ENABLED` | `true` | 保持 true | |
| `HSTS_MAX_AGE_S` | `31536000` | 確認已上 TLS 再加 | |
| `METRICS_TOKEN` | `""` | 設定才會掛載 `/metrics` | **空 = 完全不掛載（fail-closed）** |
| `LOG_LEVEL` | `INFO` | `INFO` 或 `WARNING` | |

### 3.4 帳號 / 認證

| 變數 | 預設 | 說明 |
|---|---|---|
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | SEC-18：由 2 小時縮短，令登出真正生效 |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `14` | |
| `REFRESH_REUSE_DETECTION` | `true` | SEC-17：重放已輪換 token → 撤銷整個 token family |
| `ALLOW_DEV_OTP` | `false` | **prod 必須 false**（否則啟動失敗）。需同時非 prod 才生效 |
| `PHONE_REVERIFY_INTERVAL_DAYS` | `30` | P-4 手機重驗 |
| `PHONE_REVERIFY_GRACE_DAYS` | `7` | 到期後的寬限，避免司機執勤中被鎖 |

### 3.5 Rate limit / DoS（照預設即可，除非有實測需求）

`OTP_IP_RATE_LIMIT=10`、`OTP_PHONE_RATE_LIMIT=5`、`OTP_GLOBAL_HOURLY_LIMIT=500`（**軟**上限）、
`OTP_GLOBAL_HOURLY_HARD_LIMIT=5000`（硬上限，429/503）、`MAX_REQUEST_BODY_BYTES=1048576`、
`FARE_ESTIMATE_IP_RATE_LIMIT=60`、`DRIVER_LOCATION_RATE_LIMIT=120`。

> 軟上限的設計意圖：跨過只會**收緊單一 phone** 的限額並告警，
> **不會**一次 429 全平台用戶。

### 3.6 WebSocket

`WS_HEARTBEAT_S=30`、`WS_MAX_CONNECTIONS_PER_USER=5`、`WS_MAX_CONNECTIONS_TOTAL=2000`、
`WS_IDLE_TIMEOUT_S=300`、`WS_TICKS_PER_SECOND=2`、`WS_TICK_BURST=5`。

> `WS_MAX_CONNECTIONS_TOTAL=2000` 是**單進程**上限。多 worker 時總容量是
> 2000 × workers，若不想要這個效果就要改。**負載測試前必看。**

### 3.7 背景工作 / 結算

| 變數 | 預設 | 說明 |
|---|---|---|
| `JOBS_ENABLED` | `true` | 關掉會同時停用 ghost-order sweep 與 PDPO purge |
| `GEO_SWEEP_INTERVAL_S` | `300` | 每 5 分鐘掃孤兒訂單 |
| `MAX_BROADCAST_MINUTES` | `30` | 無人接單自動取消 |
| `PURGE_INTERVAL_S` | `86400` | PDPO 清除週期 |
| `WEEKLY_SETTLEMENT_ENABLED` | `true` | **這是平台收入模型** |
| `WEEKLY_FEE_HKD` | `200` | 每個 ACTIVE 司機每週扣款 |
| `WEEKLY_SETTLEMENT_INTERVAL_S` | `604800` | 7 天 |
| `REFUND_MIN_HKD` | `1` | 低於此餘額不值得發退款請求 |

> **`JOBS_ENABLED` 的隱藏陷阱**：若同時跑多個 instance 且全部 `true`，
> 每個 instance 都會跑一次 sweep / settlement。結算有 idempotency（per ISO week），
> 但 sweep 與 purge 沒有。**多 instance 時只應在一個 instance 開 jobs**（現時未支援此開關，見 §5 待辦）。

### 3.8 外部服務（全部可選，未設時對應功能會拋錯）

| 變數 | 用途 | 未設後果 |
|---|---|---|
| `GOOGLE_MAPS_API_KEY` | 路線 / 距離（**未接線**） | 無影響 |
| `WHATSAPP_BUSINESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` | OTP 發送 | 無法發 OTP |
| `FCM_CREDENTIALS_JSON` | 推送 | 無推送 |
| `SENTRY_DSN` | 錯誤追蹤（`release` 綁 `_API_VERSION`；請求內文一律不送出 —— PDPO） | 無上報 |
| `PROMETHEUS_ENABLED` | 掛載 `/metrics`（需同時設 `METRICS_TOKEN`） | 無 metrics |
| `R2_ENDPOINT_URL` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET` | 頭像、的士證照片 | **無法上傳執照照片** |
| `R2_PUBLIC_BASE_URL` | 公開讀取網域 | 改由 API 簽名重導 |

### 3.9 檔案上傳 / 儲存

`UPLOAD_URL_TTL_S=900`（presigned 有效期）、`MAX_AVATAR_BYTES=5242880`（5 MiB）。

> 上傳走 **presigned URL 直傳 R2**，API 不代理 5 MB 相片。這是刻意的：
> 避免 worker 被大檔案佔住。

---

## 4. 部署步驟（選項 A：單台 VPS + Compose）

前提：§1 的硬約束已滿足，§3.1 的必填變數已備。

1. **開 VPS** —— 4 vCPU / 8 GB，香港或新加坡機房（目標用戶在香港）。
2. **驗 PostGIS** —— `docker run --rm postgis/postgis:16-3.4 psql -c "SELECT PostGIS_Version();"`
3. **落 compose** —— `docker-compose.yml` 已標註「this file IS the production deploy path」，
   `APP_ENV: prod` 寫死、migration 內建、loopback binding。
4. **加 TLS 反代** —— Caddy service 自動證書 + reverse proxy → `api:8000`，
   然後 **`TRUSTED_PROXY_COUNT=1`**。
5. **設環境變數** —— §3.1 五個必填 + §3.2 連線 + §3.3 反代。
6. **跑 migration** —— `alembic upgrade head`（compose command 已含）。
7. **建管理員帳號** —— `python scripts/ops/create_admin_account.py --username ... --yes`
   然後走 TOTP 註冊（`/admin/auth/login` → `/totp/enrol/confirm`）。
8. **定備份** —— `scripts/ops/db_backup.py --via auto`：本機保留 + 上傳 R2。
   GFS 保留策略：7 日 + 4 週。**恢復演練已驗證**（49 表 / 17,627 行）。
9. **建 console 靜態檔** —— `cd admin-web/web && npm ci && npm run build`，
   把 `dist/` 交給同一個 Caddy。

---

## 5. 部署前必核（Gap list）

| # | 項目 | 現況 | 風險 |
|---|---|---|---|
| 1 | **多 instance 的 jobs 去重** | 無開關；`jobs_enabled` 是全域（`app/core/config.py:125`） | 多 instance 會重複 sweep / purge。**單台 VPS 部署不受影響**；橫向擴展前必須處理 |
| 2 | **`ws_max_connections_total` 是 per-process** | 定義在 `app/core/config.py:164`（2000）→ **全專案唯一消費點** `app/main.py:254` → `ConnectionRegistry(max_total=…)` → 執行點 `app/services/order/trip_service.py:110`（`acquire()` 在 `self._total >= self.max_total` 時回 `False`）。**prod 現時 `API_WORKERS=1`，所以有效上限就是 2000** | 只有把 `API_WORKERS` 調高才會 ×N。這是**已有記錄的刻意取捨**：`docker-compose.prod.yml:83-92` 寫明三步走（重算連線池 → registry 搬去 Redis → 才調 `API_WORKERS`），`app/main.py` 的消費點亦有註解。**單 worker 下不是缺口** |
| 3 | **`alembic downgrade` 演練** | 未測 | 回滾路徑未驗證 |

**已結案，不要再當成 gap 處理**（此表曾把這四項列為未做，實際已完成）：

| 項目 | 現況 |
|---|---|
| Admin console 無 refresh token | ✅ `/api/v1/admin/auth/refresh` 已通（HttpOnly cookie + CSRF double-submit），見 `SECURITY.md` SEV-1 |
| Console TOTP 無 QR 圖 | ✅ `qrcode.react` 在本機渲染 SVG，secret 不經第三方；已用獨立解碼器驗證，見 `ADMIN_AUTH.md` 缺口 #1 |
| `/auth/refresh` 對 admin token | ✅ 同第一項 |
| `ruff format` 未過 | ✅ 全樹已格式化（212 files；`ruff check` 只餘 1 個 E501 在 sibling WIP 的 `admin-web/serve.py`），CI 已加 `ruff format --check` gate 防復發 |

---

## 6. 一句話總結

> 這是一個**有狀態、長連線、需要 PostGIS** 的 Python 單體 +
> 一個**純靜態** React console。最小可行部署 = 一台 4 vCPU / 8 GB VPS
> （HK/SG）+ 現有 compose + 一個 Caddy。真正會咬人的不是硬體，
> 是 §3.1 那五個**缺失即啟動失敗**的變數，與 §5 的 3 項 gap。

---

## 相關文件

- `DEPLOY_TARGET_DECISION.md` —— 選型取捨（A/B/C 三方案與成本）
- `WORK_SUMMARY.md` —— 整體工作總覽
- `SECURITY.md` —— 安全模型與加固建議
- `ADMIN_AUTH.md` —— 管理員認證三步驟流程
