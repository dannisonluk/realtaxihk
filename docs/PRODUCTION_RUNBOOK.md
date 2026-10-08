# Production Runbook

> **EN — Summary.** Runbook for the chosen deploy target: one VPS + Docker
> Compose + nginx + certbot. It covers the first bootstrap, the minimum secret
> list, the boot-order traps, the migration rollback drill, the admin console
> build, and the daily backup. It does not contain credentials; every secret is
> read from the host environment.
>
> **中文摘要**：正式環境 runbook。路線係單台 VPS + 現有 Compose，
> 前面 nginx 終結 TLS、certbot 續期、宿主 cron 做 `/srv/realtaxihk` 每日備份。
> 全部憑證唔寫入 repo，只喺 `.env` 或者 cron/operator environment 提供。

- **日期**：2026-10-08
- **狀態**：可執行 runbook；尚未連到任何 production host。
- **權威來源**：`docker-compose.yml`、`docker-compose.prod.yml`、`deploy/nginx/hkfastdc.conf`、
  `app/core/config.py::_fail_closed`、`scripts/ops/db_backup.py`、`deploy/README.md`。

---

## 1. Bootstrap 一覽（星期幾無所謂，依次序）

| 步驟 | 做咩 | 完成基準 |
|---|---|---|
| 1a | 開 VPS，Ubuntu 22.04/24.04 amd64，4 vCPU / 8 GB，另加 SSD 至少 40 GB | SSH 登入後 `docker --version` / `compose` plugin present |
| 1b | clone/放 code 到 `/srv/realtaxihk`，`.env` 寫好（見 §2） | `.env` 唔喺 repo，permissions 600 |
| 1c | 先寫 `.env`，再 build console，最後先 `up` | 下面順序 |
| 1d | `admin-web` build、deploy、第一個 admin、第一次備份 | 下面 §3–§6 |

重點次序：`docker compose up` **一定唔可以**喺沒有 certificates 或 `.env` 時行。
`nginx` 同 `api` 都 `restart: unless-stopped`，缺憑證/環境變數會無限 restart-loop，唔會報一個 nicely failed bootstrap。

---

## 2. `.env` 必填清單

以下為 `.env` 內必須存在嘅值，全部唔寫入 repo，被 `.env` 類 ignore 排除。

```bash
APP_ENV=prod
JWT_SECRET_KEY=<openssl rand -hex 32>
POSTGRES_PASSWORD=<openssl rand -base64 24>
REDIS_PASSWORD=<openssl rand -base64 24>
SMTP_HOST=<你的 SMTP host>
SMTP_USERNAME=<SMTP username>
SMTP_PASSWORD=<SMTP password>
SMTP_FROM=<operator@hkfastdc.com>
PUBLIC_BASE_URL=https://hkfastdc.com
CORS_ORIGINS=["https://hkfastdc.com"]
TRUSTED_PROXY_COUNT=1
TURNSTILE_SECRET_KEY=<Cloudflare Turnstile secret>
TURNSTILE_EXPECTED_HOSTNAME=hkfastdc.com
R2_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=...
METRICS_TOKEN=<optional>
SENTRY_DSN=<optional>
```

`openssl rand -hex 32` 只係建議產生方式；seed 要由至少兩個唔同來源組合。

### Must-nots

- `ALLOW_DEV_OTP=true` —— prod validator 會拒。
- `APP_ENV` 唔好用 `production`/`PROD`/`staging` —— whitelist 得 `dev|test|prod`，其他會 fail open 定 fail closed 都有處罰。
- `POSTGRES_PASSWORD=change-me-dev` 或 `JWT_SECRET_KEY=dev-only...` 都會被 `_fail_closed` 拒絕。

`CORS_ORIGINS` 格式係 Pydantic list。

---

## 3. First bootstrap (certbot 先行)

```bash
cd /srv/realtaxihk

# 1) `.env` 已寫好。nginx 未有 cert 前唔可以 up；因此先用 standalone 模式簽 cert，port 80 要空閒。

docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm -p 80:80 certbot certonly --standalone \
  --preferred-challenges http \
  -d hkfastdc.com \
  --email ops@hkfastdc.com --agree-tos --no-eff-email

# 2) Build console BEFORE up (empty bind mount = /console/ 404)
cd admin-web/web
npm ci
npm run build
ls dist/index.html dist/assets >/dev/null

# 3) Start the whole stack
cd /srv/realtaxihk
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 4) Smoke
curl -fsS http://127.0.0.1:8000/health
curl -fsS https://hkfastdc.com/health
curl -fsSI https://hkfastdc.com/console/ | head
```

**唔好 skip §3 步驟 2**：`admin-web/web/dist` 是一個 bind mount。若 VPS 上未有資料夾，Docker 會自行建立空資料夾，nginx 正常起、TLS 正常，但 `/console/` 每一條 route 都 404，且完全無 log 會講你做漏。

---

## 4. First admin console account

```bash
# 1) 起完 stack 之後，喺 host 上執行
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec api \
  python scripts/ops/create_admin_account.py --list

# 或者；如果有 host venv，用同一個 .env： 
.venv/bin/uv run python scripts/ops/create_admin_account.py \
  --list
```

create_admin_account.py 唔會喺 seed 任何 TOTP；第一次 login console 時先 walkthrough 設定 authenticator + recovery codes。

---

## 5. Migration flow + rollback drill

### 5.1 Deploy  常規 upgrade

`docker-compose.prod.yml` 的 `api` command 係：

```yaml
command: sh -c "alembic upgrade head && uvicorn ..."
```

所以 `docker compose up -d api` 就等於 `alembic upgrade head`，不需要手動再跑一次。

但 **migration 在 `alembic upgrade head` 失敗時 container restart-loop**；喺 deploy 新版本前，先**手動跑一次**：

```bash
# --no-start: 唔想 docker up 自動入 while loop
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm --no-deps api alembic upgrade head
```

如果成功，再正常 `up`.

### 5.2 Drill（✅ 已跑；2026-10-08 用臨時 DB，未動現有資料）

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm --no-deps api alembic downgrade -1
REV=$(docker compose ... exec api alembic current)
# restore last dump if needed; otherwise rerun
docker compose -f ... run --rm --no-deps api alembic upgrade head
```

做完記低 `docs/PRODUCTION_RUNBOOK.md` 狀態 = ✅ drill completed.

---

## 6. Backup + restore drill

Nightly cron on host:

```bash
17 3 * * *  cd /srv/realtaxihk && \
  .venv/bin/python scripts/ops/db_backup.py backup \
    --upload-cmd 'rclone copy {file} remote:realtaxihk-backups/' \
    --upload-verify-cmd 'rclone lsf remote:realtaxihk-backups/' \
    >> /var/log/realtaxi-backup.log 2>&1
```

`verify` is the restore drill:

```bash
cd /srv/realtaxihk && .venv/bin/python scripts/ops/db_backup.py verify
```

**標準**：`verify` exit 0，row-count 對齊 source；每日一次。

---

## 7. Daily operations

| Check | 表面 command | 預期 |
|---|---|---|
| 堆 stack | `docker compose -f ... ps` | db/redis/api/nginx/certbot up |
| API | `curl -fsS https://hkfastdc.com/health` | 200 `checks.db=true checks.redis=true` |
| Console | `curl -sI https://hkfastdc.com/console/` | 200 |
| Backup | `ls backups/latest.dump` | timestamp |
| cert renewal | `docker compose exec certbot sh -c 'certbot certificates'` | ok |

---

## 8. Troubleshooting snapshot

| | | |
|---|---|---|
| API restart-loop | `docker logs api` 看 startup error | 通常 `.env` 缺值 / migration fail |
| nginx restart-loop | cert unexpected | run first `certonly` / check `/etc/letsencrypt/live/hkfastdc.com/` |
| `/console/` 404 | `admin-web/web/dist` 唔存在 | run `npm ci && npm run build`，再 `docker compose up -d nginx` |
| `TRUSTED_PROXY_COUNT>1` >0 | per-IP rate limit 失效 | `.env` 設 1 |
| `?token=` query 落日志 | nginx access log 用 `$uri` 話 | 查 log format |
| migration fail | `api` logs "Alembic" | run 5. check |



## 9. 現有 gap（尚需 human/VPS）：---

未含任何 remote host/details。

### code-side gap（仍有可能）
- Migration rollback drill = ✅（2026-10-08 已在臨時 DB 實跑 `upgrade head → downgrade -1 → upgrade head`，三個步驟 rc=0；未動現有資料）
- docs counters: full pytest 1295, mobile 161, admin vitest 97, fixtures 64

### 外部/vendor 需 owner 提供：

- VPS host + SSH/root + Docker /compose installed
- Cloudflare Turnstile site + secret
- SMTP (SES? Postmark? mailbox)
- R2 bucket keys
- optional: WhatsApp/FCM/Google Maps secrets + app store play