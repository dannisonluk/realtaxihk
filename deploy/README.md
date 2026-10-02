# 部署（選項 A：單台 VPS + Docker Compose）

本目錄放置**部署目標決策**（`docs/DEPLOY_TARGET_DECISION.md`）選定後的產物。
決策已定為 **選項 A**，反向代理用 **nginx**（該文件原本建議 Caddy；Caddy 能自動
簽發憑證、設定更短，但 nginx 更普遍、可調性更高，且維運人員通常更熟悉）。

## 這裡有什麼

| 檔案 | 用途 |
|---|---|
| `nginx/realtaxihk.conf` | TLS 終結與反向代理。放進 nginx 的 `http {}` 區塊（即 `/etc/nginx/conf.d/`）。 |

## 使用方式

1. 把 `nginx/realtaxihk.conf` 複製到 nginx 容器的 `/etc/nginx/conf.d/`。
2. 將檔案內的 `api.realtaxihk.com` 換成真實主機名。
   **注意**：repo 內目前有兩種拼法——文檔寫 `realtaxihk.com`，
   `mobile/lib/core/config/app_config.dart` 的註釋寫 `realtaxi.hk`。
   兩者都還不是決定，請擇一並保持一致。
3. nginx 必須與 `api` 服務位於**同一個 Docker 網路**，因為 upstream 用的是
   compose 的服務名 `api:8000`，而不是主機連接埠。
   `docker-compose.yml` 刻意把 API 綁在 loopback，不對外曝露——這是對的，
   請不要為了讓 nginx 連上而把它改成 `0.0.0.0`。
4. 簽發憑證（webroot 模式，設定檔已預留 `/.well-known/acme-challenge/`）：

   ```bash
   certbot certonly --webroot -w /var/www/certbot -d api.realtaxihk.com
   ```

5. 驗證並重載：

   ```bash
   nginx -t && nginx -s reload
   ```

## 三個必須對齊的設定（否則會靜默失效）

### 1. `TRUSTED_PROXY_COUNT=1`

`app/api/auth.py::_client_ip` 的 SEC-07 邏輯**從 X-Forwarded-For 的右邊**取第
`trusted_proxy_count` 個 hop。nginx 的 `$proxy_add_x_forwarded_for` 會把真實對端
**附加在最右邊**，所以單一 nginx 對應的值就是 **1**。

- 設 `0`：完全不讀 XFF，所有請求看起來都來自 nginx 的 IP → **IP 速率限制形同
  只有一個桶**，全站共用。
- 設 `2`（沒有第二層代理）：會取到客戶端可偽造的那一格 → **速率限制可被繞過**。

這是「設定錯了不會報錯、只會悄悄失效」的那一類，務必確認。

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
> （`app/services/admin_refresh_service.py::cookies_are_secure`，prod 恆為 true），
> 不依賴請求的 scheme，所以這一項不會因為代理設定而失效。

## 這裡還沒有什麼

- **`docker-compose.prod.yml`**：加入 nginx 服務、certbot 續期、以及備份 cron。
  尚未撰寫，因為要先確認 VPS 與主機名。
- **備份 destination（P1-4）**：`scripts/ops/db_backup.py --via auto` 已可上傳
  Cloudflare R2（憑證已為頭像與的士證而設，可直接複用），只差把 cron 寫進 compose。
- **PgBouncer**：`docs/REALTIME_POSITION_COST.md` §3.5 指出多行程會超出 Postgres
  的 `max_connections`。這是**上線前必須處理的正確性問題**，不是優化。
