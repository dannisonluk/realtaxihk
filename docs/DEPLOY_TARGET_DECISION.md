# 部署目標決策簡報（P-5）

- **日期**：2026-09-30
- **狀態**：等待決策 —— 這是**根阻塞**，§4A 的 7 項有 5 項下游於此
- **用途**：一份可以直接讀完、然後做決定的文件。不是教學，是有數字的取捨。

> **為什麼這份文件存在**：`docs/WORK_SUMMARY.md` §5b 指出，「未定部署目標」
> 表面看是 7 項等 credentials，實際 5 項都是它的下游 —— 備份去邊儲、TLS 在哪
> 跑、打款用誰，全部要等這個決定。逐項啃是浪費時間，所以要先把這一項定下來。

---

## 1. 先講硬約束：PostGIS（這一項決定一半選項）

**這是全份簡報最重要的一段。**

`app/models/user.py` 用 `geoalchemy2.Geography`；migration
`9307e944a592` 建的是真正的 **PostGIS `geography(POINT, 4326)`** 欄位
（`drivers.current_location`、trips 的 `pickup_location` / `dropoff_location`），
而且 `app/api/trips.py` 與 `trip_service.py` 在 SQL 層直接呼叫 **`ST_AsText`**。

**這不是「用 float 存經緯度」可以替代的設計**，是已經寫進 schema 與查詢的
硬依賴。直接後果：

| 選項 | PostGIS 支援 | 判斷 |
|---|---|---|
| 自架 `postgis/postgis:16-3.4`（compose 現況） | ✅ 原生 | 可行 |
| AWS RDS for PostgreSQL | ✅ 支援（需選對版本） | 可行，但要收費 |
| GCP Cloud SQL for PostgreSQL | ✅ 支援 | 可行 |
| **Neon** | ⚠️ 有 PostGIS 擴展，但 version 與 extension set 常變 | 需實測，別假設 |
| **Supabase** | ✅ PostGIS 開箱可用 | 可行 |
| **Railway / Render 免費層** | ⚠️ 免費 Postgres 通常**無 PostGIS** | 高風險 |
| **Vercel / Netlify** | ❌ 無持久 Postgres | **不可行** |
| **Cloudflare（D1 / Workers）** | ❌ D1 是 SQLite，無 PostGIS；且 stack 是 Python | **不可行** |

> 教訓：直接**假設任何「Serverless Postgres」都有 PostGIS** 是最容易踩的坑。
> 免費層尤其常把 PostGIS 拿掉。任何選項在動手前要先跑一句
> `CREATE EXTENSION IF NOT EXISTS postgis; SELECT PostGIS_Version();`。

---

## 2. 這個系統實際需要什麼（不是猜，是從 repo 讀出來的）

| 需求 | 證據 | 對部署的意義 |
|---|---|---|
| **PostgreSQL + PostGIS** | 上述 | 不能用 D1／純 SQLite |
| **Redis 7（有狀態，非單純 cache）** | `docker-compose.yml` SEC-06 註解：rate-limit counters、grab locks、**Pub/Sub channels** | 需要真 Redis；不能只當 cache，**且 Pub/Sub 意味著多 instance 需要同一個 Redis** |
| **WebSocket，跨 instance Pub/Sub** | `app/api/ws.py` 的 `hub.subscribe` / `publish`；`trip_service.py:43` | 若要多 instance，**必須共用 Redis Pub/Sub**；單 instance 則無此問題 |
| **長連線（WS 常駐）** | 同上 | 排除純 request/response 的 serverless function 模型 |
| **背景／定時工作** | `scripts/db_backup.py`（P1-4 nightly pg_dump） | 需要能跑 cron 的地方 |
| **持久檔案**：的士證、頭像 | Cloudflare R2（presigned upload） | 這項**已經獨立於主機**，是加分項 |
| **HTTPS / TLS 終結** | compose 註解假設「TLS 是 reverse proxy 的責任」 | 需要 nginx/Caddy，或用平台的 LB |
| **54 個 route decorators** | `grep -c` | 中等規模，非微服務 |

**一句話**：這是一個**有狀態、長連線、需要 PostGIS** 的單體服務，
不是可以拆成 serverless functions 的東西。

---

## 3. 三個現實選項

### 選項 A — 單台 VPS + Docker Compose（**推薦**）

用 repo 現有的 `docker-compose.yml`，幾乎零改動。一台 VPS 跑 db + redis + api，
前面 Caddy／nginx 做 TLS。

- **改動量**：最小。compose 已寫好、已標註「this file IS the production deploy path」、
  migrations 已在 `command` 裡 `alembic upgrade head`。加一個 Caddy service 即可。
- **成本**：一台 4 vCPU / 8 GB VPS，約 **US$24–48/月**（Hetzner / DigitalOcean / Vultr）。
  香港或新加坡機房可壓低延遲（目標用戶在香港）。
- **優點**：
  - PostGIS、Redis Pub/Sub、WS、cron **全部天然支援**，沒有驚喜。
  - 備份（P1-4）destination 立刻可定：同機目錄 + 異地（R2），`--via auto` 已支援。
  - TLS 反代（P1-1）就在同一台，`TRUSTED_PROXY_COUNT=1` 對得上 SEC-07 設計。
  - 除錯模型最簡單：SSH 進去 `docker compose logs`。
- **缺點**：
  - 單點故障。要自己管 OS patch、磁碟、證書續期（Caddy 自動化後其實很少）。
  - 擴展是垂直的 —— 但以香港的士中介的規模，這在相當長時間內不是問題。

### 選項 B — 容器平台 + 託管 Postgres（Railway / Render / Fly.io + Neon / Supabase）

- **改動量**：中。需要把 compose 拆成平台能理解的服務定義，且**要先實測 PostGIS**。
- **成本**：約 **US$20–70/月**，視層級；免費層**不足以**跑 Redis + WS + PostGIS。
- **優點**：自動部署、自動 HTTPS、部分平台有免費 TLS 與日誌。
- **缺點**：
  - **PostGIS 是最大不確定項** —— 免費／低階 Postgres 常無此擴展，要先驗。
  - Redis Pub/Sub 在多 instance 下需要共用 Redis（平台常按 instance 給）。
  - 成本較難預測，容易在 WS 長連線與頻寬上超支。
  - 供應商的 migration／備份匯出會綁住你。

### 選項 C — 三個雲之一（AWS／GCP）全託管

- **改動量**：大。ECS/Fargate 或 Cloud Run（**但 Cloud Run 不適合常駐 WS**）＋ RDS ＋ ElastiCache。
- **成本**：**US$80–200+/月** 起，RDS 與 ElastiCache 是主要開支。
- **優點**：合規、可用性 SLA、IAM、成熟備份。
- **缺點**：以現階段規模**明顯過度工程**；設定與維運複雜度最高；
  `TRUSTED_PROXY_COUNT` 與 SEC-07 需要重新對照 ALB 行為。

---

## 4. 建議：選項 A，並且照這個順序做

**理由**：這是一個有狀態、長連線、需要 PostGIS 的單體，而且 repo 的
`docker-compose.yml` **本來就是照生產路徑寫的**（`APP_ENV: prod` 寫死、
migrations 內建、loopback binding、SEC-06/07/19/31 註解全部齊）。
選項 A 等於**沿用已經做好的正確決定**，而不是把已驗證的配置重寫成平台專屬版。

具體步驟（每步都可獨立驗收）：

1. **開一台 VPS**（4 vCPU / 8 GB，香港或新加坡機房）。
2. **實測 PostGIS**（若走選項 B 這步更關鍵）：
   `docker run --rm postgis/postgis:16-3.4 psql -c "SELECT PostGIS_Version()"`。
3. **加一個 Caddy service** 到 compose：自動 TLS、reverse proxy 到 `api:8000`，
   然後 **把 `TRUSTED_PROXY_COUNT` 設為 1** —— 這正是 SEC-07 期待的設定。
4. **定備份 destination（解 P1-4）**：`scripts/db_backup.py --via auto`，
   本機保留 + 上傳到既有的 Cloudflare R2（R2 憑證已為頭像／的士證而設，可直接複用）。
5. **定打款渠道（解 P2-2）**：VPS 不影響這項，但決定了部署就能接著談 provider。
6. **補 3 個真 key**（Google Maps / FCM / WhatsApp）—— 這三項是**真正**的 credential 阻塞，
   與部署目標無關，可以並行申請。

> **先做 1–3**，因為它們會把 §4A 的 5 項下游一次解掉 3 項（備份、TLS、以及「在哪跑」本身）。

---

## 5. 決定了之後，我可以立刻做的事

一旦你選定（A／B／C），我可以直接動工，不需要再問：

- **A**：寫 `docker-compose.prod.yml`（加 Caddy + 備份 cron）、
  `deploy/README.md`（逐步、可複製貼上）、`Caddyfile`。
- **B**：寫平台專屬服務定義，並**先跑一個 PostGIS 驗證腳本**確認擴展可用，
  再決定託管 Postgres 用邊間。
- **C**：寫 Terraform（RDS + ElastiCache + ECS/Fargate），
  並重新稽核 `TRUSTED_PROXY_COUNT` 對 ALB 的行為。

---

## 6. 一句話總結

> **PostGIS 是硬約束，長連線 WS + Redis Pub/Sub 是第二約束。**
> 這兩項加起來，指向**選項 A（單台 VPS + 現有 compose）** ——
> 它同時是最便宜、改動最少、且最貼合這個 repo 已經寫好的生產配置。
> 選項 B 的最大風險是 PostGIS 在低階託管 Postgres 上不存在；選項 C 現階段過度工程。
