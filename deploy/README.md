# 部署（選項 A：單台 VPS + Docker Compose）

本目錄放置**部署目標決策**（`docs/DEPLOY_TARGET_DECISION.md`）選定後的產物。
決策已定為 **選項 A**，反向代理用 **nginx**（該文件原本建議 Caddy；Caddy 能自動
簽發憑證、設定更短，但 nginx 更普遍、可調性更高，且維運人員通常更熟悉）。

## 這裡有什麼

| 檔案 | 用途 |
|---|---|
| `nginx/hkfastdc.conf` | TLS 終結與反向代理。放進 nginx 的 `http {}` 區塊（即 `/etc/nginx/conf.d/`）。 |
| `../docker-compose.prod.yml` | 正式環境的 **overlay**。加入 nginx、certbot 續期，並把連線池的三個數字寫明。 |

## 正式部署怎麼跑

`docker-compose.prod.yml` 是 **overlay（疊加檔）**，不是獨立檔案：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**為何不寫成獨立檔案**：`docker-compose.yml` 承載了全部安全加固
（SEC-06 的 loopback 綁定、SEC-19 的密碼必填、SEC-31 的 `--no-proxy-headers`）。
獨立檔案等於把它們複製一份，而下一次修安全問題時**被漏掉的永遠是第二份**。
這與 `.dockerignore` 是同一類判斷。

### 第一次啟動前：憑證必須先存在

nginx 讀不到憑證會**直接啟動失敗並進入無限重啟**（`restart: unless-stopped`）。
所以第一次簽發要在 nginx 還沒起來時做，用 `--standalone`：

```bash
# 1. 先讓 certbot 自己佔用 :80 完成首次簽發（此時 nginx 尚未啟動）
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm -p 80:80 certbot certonly --standalone \
  --preferred-challenges http \
  -d hkfastdc.com \
  --email ops@hkfastdc.com --agree-tos --no-eff-email

# 2. 再啟動整個堆疊
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

之後的**續期**由 `certbot` 服務每 12 小時跑一次 `certbot renew --webroot`
（`--webroot` 而非 `--standalone`，因為此時 :80 已被 nginx 佔用；設定檔已為此
保留 `/.well-known/acme-challenge/`）。

nginx 與 certbot 是兩個容器，**之間沒有訊號通道**，而 nginx 只在啟動或 reload 時
讀取憑證。因此 nginx 容器內有一個每 6 小時 `nginx -s reload` 的迴圈——以 90 天的
憑證有效期而言，這把「續期後憑證閒置」的上限壓到 6 小時，換來的是不必在兩個容器
之間做訊號轉發。

### 主機名：`hkfastdc.com`，唯一一個（2026-10-04 已定）

以前 repo 內有**三種**拼法並存而沒有任何一個是決定。現在決定了：**單一
hostname `hkfastdc.com`，沒有 `api.` 也沒有 `console.` 前綴。** 所有東西用
**路徑**分開，都在同一個 origin 上：

| 路徑 | 服務 |
|---|---|
| `/` | API（`location /` 保持原樣，所以既有路由一行沒改） |
| `/ws/` | API，WebSocket upgrade |
| `/console/` | 管理後台（靜態檔，`/var/www/console`） |

同 origin 不只是整齊：console 用**相對** `/api/*` 呼叫（見
`admin-web/web/vite.config.ts`），所以**沒有 CORS preflight**，也只需要簽一張憑證。
`CORS_ORIGINS` 仍然填這個 origin，以備日後有別的 host 的呼叫者。

`PUBLIC_BASE_URL` 必須是同一台主機的 `https://` 來源，否則驗證信會把使用者帶到
別的地方。

`ssl_certificate` 的路徑把主機名寫死在裡面（`/etc/letsencrypt/live/hkfastdc.com/…`），
所以**改漏一處**的後果不是警告，是 nginx 找不到 cert 而**啟動失敗**
（`docker-compose.prod.yml` 的 api 註釋說明它會 restart-loop）。conf 內共**五處**
寫死主機名，要同步改。

> 先前這裡建議用 `envsubst` 模板把主機名參數化。**主機名定了之後就不需要了**
> —— 那個做法存在的理由是「三種拼法無法收斂」，而現在收斂了。多一層模板只會多
> 一個要對齊的地方。

## Console build：`/console/` 的靜態檔從哪來

`nginx/hkfastdc.conf` 的 `location /console/` 是**啟用中**的，但 nginx 本身不
建置前端——它只是把 `/var/www/console/` 當靜態根目錄讀。所以上線前**必須先建置
一次**，否則 `/console/` 會回 404。

```bash
cd admin-web/web
npm ci          # 用 lockfile 精準安裝，不要用 npm install
npm run build   # 產物在 admin-web/web/dist/
```

`docker-compose.prod.yml` 的 nginx service 已把該目錄掛進容器：

```yaml
volumes:
  - ./admin-web/web/dist:/var/www/console:ro
```

**路徑對應關係**（`base: './'` 是關鍵，它令產物的資源路徑保持相對）：

| nginx `location` | `root` + `location` 解析後 | 對應 `dist/` 內 |
|---|---|---|
| `/console/` | `/var/www/console/` | `dist/index.html` |
| `/console/assets/` | `/var/www/console/assets/` | `dist/assets/*`（含 hash 檔名） |

兩個 `location` 都用 `root /var/www`，所以**掛一個 volume 就同時覆蓋兩者**，
不需要為 `assets` 再掛一次。

> ⚠️ **未 build 就先 `up` 的後果**：bind mount 的來源目錄不存在時，Docker 會
> **靜靜地建立一個空的 root 擁有的目錄**，不會報錯。於是 nginx 起來、TLS 正常、
> `/` 的 API 照常運作，唯獨 `/console/` 每一條路由都回 **404**——KYC 審批、爭議
> 裁決、退款、車隊結算全部沒有後台可用，而**任何日誌都不會說原因**。這是本專案
> 反覆記錄的「看起來對、其實無聲失效」那一類。**先把 `dist/` build 出來再 `up`。**

驗證（在宿主機，`dist/` 存在即可先確認內容）：

```bash
ls admin-web/web/dist/index.html admin-web/web/dist/assets
```

若容器已起來但 console 仍 404，確認容器內真的看到檔案：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec nginx ls /var/www/console/
```

## 三個必須對齊的設定（否則會靜默失效）

### 1. `TRUSTED_PROXY_COUNT=1`

`app/core/client_ip.py::client_ip` 的 SEC-07 邏輯**從 X-Forwarded-For 的右邊**取第
`trusted_proxy_count` 個 hop。nginx 的 `$proxy_add_x_forwarded_for` 會把真實對端
**附加在最右邊**，所以單一 nginx 對應的值就是 **1**。

- 設 `0`：完全不讀 XFF，所有請求看起來都來自 nginx 的 IP → **IP 速率限制形同
  只有一個桶**，全站共用。
- 設 `2`（沒有第二層代理）：會取到客戶端可偽造的那一格 → **速率限制可被繞過**。

這是「設定錯了不會報錯、只會悄悄失效」的那一類，務必確認。
（`docker-compose.prod.yml` 已設為 `"1"`，不必再改。）

### 2. `X-Forwarded-For` 用 `$proxy_add_x_forwarded_for`

不要改成 `$http_x_forwarded_for`。前者會附加真實對端，後者會把客戶端送來的值
原封不動轉發——那正是 SEC-07 修掉的漏洞。

### 3. access log 用 `$uri`，不要用 `$request_uri`

**這一項與 P1-1 直接相關。** 瀏覽器端的 WebSocket 沒有 header 通道，所以實時行程
的 access token 只能走查詢字串（`/ws/trip/<order_id>?token=...`，見
`app/api/ws.py`）。`$request_uri` 會把 token **以明文寫進日誌**，每次重連寫一次。

設定檔內自訂的 `log_format realtaxihk` 用的是 `$uri`（只有路徑），查詢字串不會
落地。**改動該 format 等於重新引入這個洩漏。**

> 附帶說明：cookie 的 `Secure` 旗標由**環境**決定
> （`app/services/admin/admin_refresh_service.py::cookies_are_secure`；實際
> cookie 設定喺 `app/core/admin_cookies.py`，prod 恆為 true），
> 不依賴請求的 scheme，所以這一項不會因為代理設定而失效。

## 連線池：三個數字是同一個決定

`DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`API_WORKERS` **不是三個獨立旋鈕**。SQLAlchemy
的連線池是 **per-process** 建立的，所以 api 對 Postgres 的實際需求是

```
(DB_POOL_SIZE + DB_MAX_OVERFLOW) × API_WORKERS
```

`docker-compose.prod.yml` 目前是 `(10 + 20) × 1 = 30`，對 `PG_MAX_CONNECTIONS=100`
留了 70 條餘量給 `alembic upgrade head`、psql、每晚的 `pg_dump` 與監控。

`tests/infra/test_prod_compose_pool_arithmetic.py` 會在這個算式不成立時失敗，並且會
檢查 `--workers` 確實取自 `${API_WORKERS}`（否則那個算式守的是一個容器根本沒在用的
數字）。

### `API_WORKERS` 預設為 1，這不是保守，是**正確性**

`ConnectionRegistry`（WebSocket 連線計數）與速率限制計數器**都存在行程記憶體內**。
跑 N 個行程時，`WS_MAX_CONNECTIONS_TOTAL` 與各項速率上限會被**各別執行 N 次**——
全域上限形同虛設，而且不會有任何錯誤訊息。這是**功能退化，不是設定微調**。

要真正加寬，是三個步驟的組合，缺一不可：

1. 重算上面的連線池算式（池要按比例縮小）；
2. 把 `ConnectionRegistry` 與速率限制計數搬到 Redis；
3. 最後才提高 `API_WORKERS`。

## 這裡還沒有什麼

### PgBouncer（擴容路徑，**尚未撰寫**）

**先講結論**：`docs/REALTIME_POSITION_COST.md` §3.5 指出的**正確性問題已經解決**——
把池大小改成可由環境變數設定、並在 overlay 內明確寫成 `(10+20)×1=30 ≤ 100`，
就已經讓 api 不會超出 `max_connections`。PgBouncer 現在是**擴容手段**（解除
「行程數 × 池大小」這個硬上限），不再是上線前的阻礙。

**為何不直接附上 compose 檔**：PgBouncer 的設定無法在本機驗證（本環境起不到
Docker），而設定錯的表現是**連線失敗**，不是啟動時的錯誤訊息——那正是本專案反覆
記錄的「看起來對、其實無聲失效」那一類。與其附上一份未經驗證、看起來可以用的
基礎設施設定，不如把內容與檢查點寫在這裡。

要加入時，它是**第三個 overlay**，因為啟用連線池不是一個改動而是三個，其中兩個
忘了不會報錯：

```yaml
# deploy/pgbouncer/docker-compose.pgbouncer.yml（尚未建立）
services:
  pgbouncer:
    image: edoburu/pgbouncer:v1.26.0-p0
    environment:
      DB_HOST: db
      DB_USER: ${POSTGRES_USER:-realtaxi}
      DB_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      DB_NAME: ${POSTGRES_DB:-realtaxihk}
      POOL_MODE: transaction
      AUTH_TYPE: scram-sha-256
      MAX_CLIENT_CONN: "200"
      DEFAULT_POOL_SIZE: "20"

  api:
    environment:
      POSTGRES_HOST: pgbouncer        # ← 忘了就等於沒啟用
      DB_STATEMENT_CACHE_SIZE: "0"    # ← 忘了會在執行期爆
```

**`DB_STATEMENT_CACHE_SIZE=0` 不是可選項。** asyncpg 預設為每條連線快取 100 條
prepared statement；transaction pooling 下同一筆交易的前後兩個語句可能落在
**不同的伺服器連線**上，後者會以
`prepared statement "__asyncpg_stmt_N__" does not exist` 失敗——看起來像資料庫
故障，其實是設定問題。

加入後至少要驗證這四件事：

1. `psql "postgres://realtaxi:<pw>@127.0.0.1:<port>/realtaxihk"` 經 PgBouncer 連得上；
2. 進入 admin console（`psql .../pgbouncer`）看 `SHOW POOLS;`，確認
   `sv_active` / `sv_idle` 有在動，而不是 0；
3. 連續跑一批含 prepared statement 的請求（例如 `GET /api/v1/admin/live/drivers`），
   確認沒有 `does not exist`；
4. 重新確認連線池算式——此時 `PG_MAX_CONNECTIONS` 要對的是 **PgBouncer 的
   `DEFAULT_POOL_SIZE`**，不再是 api 的池大小。

### 備份 cron（P1-4）

`scripts/ops/db_backup.py backup --via docker` 已經可以直接用，並且
`--upload-cmd` / `--upload-verify-cmd` 已可上傳 Cloudflare R2
（憑證已為頭像與的士證而設，可直接複用）。

**它應該跑在宿主的 cron，不是容器裡**，原因是 `.dockerignore` 排除了 `scripts/`，
映像內根本沒有這支腳本。這支腳本只用標準庫，不需要映像，所以宿主 cron 是它正確的
位置：

```cron
# 每晚 03:17（避開整點）備份，並要求遠端確認檔案落地
17 3 * * * cd /srv/realtaxihk && \
  ./scripts/ops/db_backup.py backup --via docker \
    --upload-cmd '...' --upload-verify-cmd '...' >> /var/log/realtaxi-backup.log 2>&1
```

**`--upload-verify-cmd` 建議一定要給**：沒有它的話，「上傳指令 exit 0 但什麼都沒做」
與「真的上傳成功」無法區分。另外還缺**異地目的地**（P1-4 的另一半）——
R2 與 VPS 若在同一個帳號下，一次帳號事故會同時帶走兩者。

### 其他

- **主機名**：已定為 `hkfastdc.com`，單一 origin（見上面「主機名」一節）。
- **後台主控台（`admin-web/web`）**：`nginx/hkfastdc.conf` 內已有**啟用中**的
  `location /console/`，與 API 同一個 origin，**不需要另簽憑證**。
  `docker-compose.prod.yml` 的 nginx service 已掛上
  `./admin-web/web/dist:/var/www/console:ro`。剩下的只有**建置**：跑一次
  `cd admin-web/web && npm ci && npm run build`（詳見上面「Console build」一節）。
  **未 build 就 `up` 會令 `/console/` 回 404**，其餘一切照常。
