# 測試環境 — QA 交接清單

> **EN — QA test environment.** Everything a tester needs to bring the stack up
> and log in: the four environment variables that actually matter, how to get an
> OTP, how to create an admin, and the addresses the three clients dial. **§6 is
> the one to read before testing the APK** — the two walls a first login hits
> (a phone-only login that still leaves the account `UNVERIFIED`, and a dev
> server that binds `127.0.0.1` so a physical device cannot reach it). The
> traps section is the part worth reading twice — every entry there has cost
> somebody an hour.
>
> **中文摘要**：這份文檔只講「QA 要怎麼把環境跑起來並登入」。**四個一定要改的
> 環境變數**、OTP 怎麼拿、管理員怎麼建、三個客戶端各連哪個位址，以及**手機 App
> 第一次登入會撞到的兩道牆**（第 6 節）。第 8 節「常見陷阱」每一條都真的令人卡過。

---

## 0. 這份文檔的範圍

本文檔講**測試環境**：在本機（或一台 QA 機）把後端、管理後台、手機 App 跑起來，
並且能登入。它**不**講正式部署 —— 那是
[`DEPLOYMENT_REQUIREMENTS.md`](DEPLOYMENT_REQUIREMENTS.md) 與
[`../deploy/README.md`](../deploy/README.md)。

環境變數的**完整**清單在 [`../.env.example`](../.env.example)（每個變數都有註釋）
與 [`DEPLOYMENT_REQUIREMENTS.md`](DEPLOYMENT_REQUIREMENTS.md) §2.4。本文檔只列
QA 必須關心的那幾個，並說明**不設會怎樣**。

---

## 1. 一次性設定

```bash
# 1. 基礎設施：PostGIS :15433、Redis :16379
docker compose up -d db redis

# 2. Python 環境（uv 管理 venv 與依賴）
uv sync --frozen --all-extras

# 3. 環境變數 —— 見第 2 節，至少要先填 JWT_SECRET_KEY
cp .env.example .env

# 4. schema
.venv/Scripts/python -m alembic upgrade head

# 5. 起 API（背景 uvicorn + 等 health check 通過）
.venv/Scripts/python scripts/dev/serve_and_probe.py

# 6. 驗一下
.venv/Scripts/python scripts/verify/verify_api.py
```

Linux／macOS 把 `.venv/Scripts/python` 換成 `.venv/bin/python`。

停止：`.venv/Scripts/python scripts/dev/stop_server.py`。

---

## 2. 環境變數：QA 一定要改的四個

`.env.example` 是可直接用的 dev 基線，但有 **四個**值 QA 一定要動，
其中前兩個**不動就登入不了**。

| 變數 | 測試環境請填 | 不填／填錯會怎樣 |
|---|---|---|
| `JWT_SECRET_KEY` | `openssl rand -hex 32` 的輸出 | **留空則啟動直接失敗**。此值無預設、且強制檢查熵（≥ 32 字元、≥ 8 個不同字元）；`"x" * 64` 也會被拒 |
| `ALLOW_DEV_OTP` | `true` | **收不到 OTP**。見第 3 節 —— 驗證碼**不會**出現在回應裡，這個開關令它固定為 `123456` |
| `APP_ENV` | `dev` | 無預設值。留空、或填 `production`／`PROD`，都是啟動錯誤（只接受 `dev` \| `test` \| `prod`） |
| `LOG_LEVEL` | `INFO` | 想從日誌讀 OTP 的話必須是 `INFO`，否則看不到 `[WHATSAPP:dev] OTP ...` 那行 |

以下幾個**不用改，但要知道為什麼是這個值**：

| 變數 | 預設 | 說明 |
|---|---|---|
| `POSTGRES_HOST` / `REDIS_URL` | `127.0.0.1` | **刻意不用 `localhost`**。Windows 上 `localhost` 會先解析到 `::1`，而 Docker 發佈的埠只有 IPv4，該次連線是被丟棄而非拒絕 —— 每個新連線白等約 2 秒（實測 2034ms vs 1ms） |
| `POSTGRES_PORT` / `REDIS_PORT` | `15433` / `16379` | 避開同機另一個專案 `ineedajob`（用 15432／6379） |
| `CORS_ORIGINS` | 四個 loopback origin（3000、8081 各兩種寫法） | 只在**瀏覽器直接跨域**時才生效。Vite dev server 與 `serve.py` 都會把 `/api` 反向代理到 API，所以正常情況下不會觸發 CORS。要從別的埠開後台才需擴充此清單 |
| `OTP_IP_RATE_LIMIT` | `10`（每 600 秒） | **QA 最常撞到的限制**，見第 8 節 |
| `APP_PORT` | `8000` | 三個客戶端都假設這個埠 |
| `TRUSTED_PROXY_COUNT` | `0` | 本機直連，沒有代理，所以 `X-Forwarded-For` 完全被忽略（正確行為） |

> `APP_ENV=prod` **不是**「假裝正式環境」的開關。它會令 app 在啟動時拒絕一整套
> dev 預設值（JWT 密鑰、`POSTGRES_PASSWORD`、`ALLOW_DEV_OTP`、
> `PUBLIC_BASE_URL` 必須 https、`CORS_ORIGINS` 不得含純 http、
> `SMTP_HOST`／`SMTP_FROM` 必須設定、`TRUSTED_PROXY_COUNT` ≥ 1）。
> 想驗正式啟動行為，用 `scripts/verify/prod_boot_drill.py`，它逐條驗這 11 種情形。

---

## 3. 測試電話號碼與 OTP

### 3.1 電話號碼格式

只接受 **E.164 香港號碼**：`^\+852\d{8}$`，即 `+852` 後接**剛好 8 位數字**。

| 輸入 | 結果 |
|---|---|
| `+85291234567` | 接受 |
| `85291234567`（缺 `+`） | 422 拒絕 |
| `+852 9123 4567`（含空格） | 422 拒絕 |
| `+852912345`（7 位） | 422 拒絕 |

**沒有預先種好的測試帳號。** 任何合規格的號碼在第一次 `OTP verify` 時自動建立
帳號，所以 QA 可以自由編號，建議用**不屬於真人的區段**（例如 `+8529` 開頭）避免
誤發訊息。手機 App 上直接用鍵盤輸入即可。

### 3.2 取得 OTP —— 這是最大的誤解

**驗證碼永遠不會出現在 API 回應裡**（任何環境都不會）。
`/api/v1/auth/otp/request` 只回 `{"sent": true, "expires_in": 300}`。

歷史原因值得知道：它曾經在 `ALLOW_DEV_OTP=true` 時以 `dev_code` 欄位回傳，等於把
一組可用的 OTP 交給呼叫方 —— 回應是送到攻擊者手上的，所以 OTP 就不再證明任何事。
現在只剩**確定性**：`ALLOW_DEV_OTP=true`（僅 dev／test，`APP_ENV=prod` 下直接被拒）
令驗證碼固定為一個常數。

所以 QA 有兩條路：

| 做法 | 步驟 |
|---|---|
| **A（建議）** | `.env` 設 `ALLOW_DEV_OTP=true`，驗證碼**一律**是 `123456` |
| **B** | 保持 `ALLOW_DEV_OTP=false`，看 API 日誌：`[WHATSAPP:dev] OTP <code> -> +852****4567`（需 `LOG_LEVEL=INFO`） |

路徑 B 是 dev 通知供應器 `DevWhatsAppProvider` 印出來的，不是真的發了 WhatsApp。

### 3.3 OTP 的時序限制（QA 反覆測試時會撞到）

| 限制 | 值 | 說明 |
|---|---|---|
| 驗證碼有效期 | 300 秒 | `request` 後 5 分鐘內要驗 |
| 重發冷卻 | 60 秒 | 同一號碼 60 秒內再 `request` 會被拒，**不會**產生新碼 |
| 單碼嘗試次數 | 5 次 | 超過後即使輸入正確也失效 |
| 單次使用 | 是 | 驗過一次即標記 `consumed_at`，重驗會被拒（「OTP already used」） |

> **反覆登入同一個號碼的 QA 情境**：因為「單次使用 + 60 秒冷卻」同時生效，連續
> 登入會失敗。測試腳本的做法是把 `otp_codes` 的時間戳往回推，繞過冷卻 —— 這是
> **安排**而不是繞過安全控制（見 `tests/api/test_licence_api.py::_sign_in`）。
> 手動 QA 時，換一個號碼比等 60 秒快。

### 3.4 速率限制

`OTP_IP_RATE_LIMIT=10`（每 600 秒）、`OTP_PHONE_RATE_LIMIT=5`（每 3600 秒）。
**從同一台機器反覆測 OTP 登入，第 11 次就會拿到 429。**
單機 QA 建議在 `.env` 調高 `OTP_IP_RATE_LIMIT`（例如 1000）與
`OTP_PHONE_RATE_LIMIT`，測完再改回來。

`X-Forwarded-For` 在本機（`TRUSTED_PROXY_COUNT=0`）**完全被忽略**，所以偽造這個
標頭**不會**取得新的速率限制桶 —— 這是 SEC-07 的修復，不是 bug。

---

## 4. 管理員帳號

這裡有**兩個不同的東西**，別混用：

| 腳本 | 建的是什麼 | 用在哪 |
|---|---|---|
| `scripts/ops/create_admin_account.py` | `admin_accounts` 的一筆帳號，**帶密碼與 TOTP 第二因素** | **管理後台（console）**，也就是現在的主線 |
| `scripts/ops/create_admin.py` | `users.role = ADMIN` | 舊版 console 身分，**不是**帶憑證的帳號 |

### 4.1 建一個後台管理員

```bash
export ADMIN_PASSWORD='<符合密碼政策的密碼>'
.venv/Scripts/python scripts/ops/create_admin_account.py \
    --username qa-admin --email qa-admin@example.com --name "QA Admin"
```

- 密碼**不從命令列參數取**（會留在 shell history 與 process table），只讀
  `ADMIN_PASSWORD` 或互動提示。
- **TOTP 不在這裡產生。** 管理員在**第一次登入**時才註冊，因為那是唯一能在信任
  這個秘密之前證明它可用的方式。用腳本寫入秘密，會做出一個「沒人生得出驗證碼」
  的帳號。
- 密碼政策會擋掉含 `admin` 子字串的密碼（`_WEAK_FRAGMENTS`）—— 用自己的帳號類型
  命名的密碼會被拒。

### 4.2 非互動式註冊 TOTP（給自動化／QA 重跑）

```bash
export ADMIN_PASSWORD='<同上>'
.venv/Scripts/python scripts/ops/enrol_admin_totp.py --username qa-admin --super-admin
```

`--super-admin` 會在註冊完成後升級為 `SUPER_ADMIN`；console 的驗證工具需要這個
等級。

### 4.3 管理員角色

階層由低到高，權限檢查用 `rank >=` 比較（不是集合成員）：

| 角色 | rank | 大致職責 |
|---|---|---|
| `SUPPORT` | 0 | 記錄問題（工單），**不能**審 KYC |
| `OPERATIONS` | 1 | 決定問題（審批 KYC、派單） |
| `FINANCE` | 2 | 動錢（退款、結算） |
| `SUPER_ADMIN` | 3 | 全部，且**唯一**能改別人的角色 |

`SUPPORT`／`OPERATIONS` 分開、`OPERATIONS`／`FINANCE` 分開，都是刻意的職責分離：
審 KYC 的人不該同時決定放不放行，批文件的人不該同時動錢。

---

## 5. 三個客戶端各連哪裡

### 5.1 後端 API

`http://127.0.0.1:8000`（`APP_PORT` 預設 8000）。健康檢查與 API 都在 `/api/v1`。

### 5.2 管理後台

```bash
cd admin-web/web
npm install
npm run dev            # → http://localhost:5174，並把 /api 代理到 127.0.0.1:8000
```

Vite dev server 自己代理 `/api`，所以**不需要** CORS，也用不到 `CORS_ORIGINS`。

要驗**正式建置產物**：

```bash
npm run build          # → admin-web/web/dist
cd .. && python serve.py --dist      # → http://127.0.0.1:3000
```

`serve.py` 預設埠 **3000**（`CORS_ORIGINS` 裡本來就有它）；不加 `--dist` 時它服務
的是**舊版** console。

### 5.3 手機 App

`API_BASE_URL` 由 `--dart-define` 決定，沒給時用一個 dev fallback：

| 情境 | 位址 | 說明 |
|---|---|---|
| Android **模擬器** | `http://10.0.2.2:8000` | fallback 的預設值。模擬器裡的 `127.0.0.1` 是**模擬器自己**的 loopback，不是你的機器 |
| 桌面／iOS 模擬器 | `http://127.0.0.1:8000` | fallback 的預設值 |
| **真機（USB）** | `http://127.0.0.1:8000` + `adb reverse tcp:8000 tcp:8000` | **建議**。`adb reverse` 令手機上的 `127.0.0.1:8000` 轉到電腦，不必改任何綁定 |
| **真機**（同一 Wi-Fi） | `http://<你的區網 IP>:8000` | 例如 `--dart-define=API_BASE_URL=http://192.168.0.103:8000`。**API 需綁到 `0.0.0.0`，但 `APP_HOST` 做不到** —— 見第 6.2 節 |

```bash
cd mobile/android && ./gradlew :app:assembleDebug
# → mobile/build/app/outputs/apk/debug/app-debug.apk
```

- 產物識別：`applicationId = com.hkfastdc.mobile`，顯示名稱 `hkfastdc`。
- **地圖金鑰**：不給 `GOOGLE_MAPS_API_KEY`（`--dart-define` 或
  `mobile/android/local.properties`）時，App 會顯示座標面板而不是地圖 ——
  這是刻意的降級，不是壞掉。此金鑰不是機密（Android 金鑰由套件名與簽章憑證限制）。
- 沙盒／CI 上 `flutter` CLI 會掛（見 [`DEVELOPMENT.md`](DEVELOPMENT.md) §5），
  但 `./gradlew` 正常，所以用上面的指令建 APK。

---

## 6. 手機 App 首次登入：兩道牆

### 6.1 電話驗證**就是**登入，沒有「跳過」這回事

App 沒有密碼 —— 整個後端都沒有密碼登入。`/api/v1/auth/otp/verify` 是唯一的入口，
所以「一開始就要電話 verify」不是一道額外的閘，**它就是門口本身**，沒有測試開關可以
繞過，這是刻意的。

但「要電話驗證」不等於「要真電話」：

| 你想要 | 做法 |
|---|---|
| 一個固定的驗證碼 | `.env` 設 `ALLOW_DEV_OTP=true` → 永遠是 `123456` |
| 用真號碼但不想等 WhatsApp | 讀 API 日誌的 `[WHATSAPP:dev] OTP <code> -> +852****4567`（需 `LOG_LEVEL=INFO`） |
| 一個號碼 | 任何 `+852` + 8 位都可以，第一次驗證時自動註冊（見 3.1） |

換句話說：**登入這一關不需要繞，因為它本來就不是真的在發訊息。**

### 6.2 第一道牆：真機連不到你的 API

`scripts/dev/serve_and_probe.py` 把 uvicorn 寫死在 **`127.0.0.1`**（第 24 行與第 34
行都是硬編碼），所以 `APP_HOST=0.0.0.0` **不會生效** —— 那個設定沒有任何 dev 啟動
腳本讀它。結果是 APK 裝在真機上時，`10.0.2.2`（那是模擬器專用的別名）與區網 IP 都
連不到。

| 做法 | 步驟 | 適用 |
|---|---|---|
| **A（建議）** | `adb reverse tcp:8000 tcp:8000`，APK 用 `--dart-define=API_BASE_URL=http://127.0.0.1:8000` | USB 連著的實機。`adb reverse` 令手機上的 `127.0.0.1:8000` 轉到電腦，**完全不用改綁定** |
| **B** | 自己起 `.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers`，APK 用 `--dart-define=API_BASE_URL=http://<區網 IP>:8000` | Wi-Fi、不想插線。要自己確認防火牆放行 8000 |

`adb reverse` 在每次重新插線（或 adb 重啟）之後都要再下一次。

> `--no-proxy-headers` 不是可選的裝飾：少了它，uvicorn 會相信來自 `127.0.0.1` 的
> `X-Forwarded-For`，每一個 IP 速率限制就變成可偽造（SEC-31）。

### 6.3 第二道牆：登入之後帳號仍然是 `UNVERIFIED`

這一處**最容易被誤判成 bug**。

OTP 只證明電話。剛註冊的帳號 `account_status` 是 `UNVERIFIED`，而
`require_verified_account`（`app/core/deps.py:243`）要求**三樣齊全**才放行：

| 缺什麼 | 403 的 `missing` 會列出 |
|---|---|
| `username` 未設 | `"profile"` |
| `email_verified_at` 為空 | `"email"` |
| `phone_verified_at` 為空 | `"phone"` |

OTP 登入已經設好 `phone_verified_at`，所以實際上 `missing` 是 `["profile","email"]`。

被這道閘擋住的是**開始新生意**的路由，也就是掛 `require_phone_current` 的那幾條：

| 路由 | 守門依賴 | UNVERIFIED 時 |
|---|---|---|
| `POST /api/v1/orders`（建立訂單） | `require_phone_current` | **403 `ACCOUNT_UNVERIFIED`** |
| `POST /api/v1/orders/{id}/grab`（接單） | `require_phone_current` | **403** |
| `POST /api/v1/drivers/location`（上線） | `require_phone_current` | **403** |
| 其餘（查訂單、行程、`/identity/*`、`/drivers/register`） | `require_active_user` | 正常 |

403 的 body 是機器可讀的，客戶端要靠 `reason` 分辨「去補資料」與「去收信」：

```json
{"detail": {"message": "account verification is incomplete",
            "reason": "ACCOUNT_UNVERIFIED",
            "missing": ["profile", "email"]}}
```

**而手機 App 目前沒有處理這個狀態的畫面。** `mobile/lib/` 完全沒有呼叫
`/api/v1/identity/*` —— 沒有電郵輸入畫面，也沒有使用者名稱畫面。所以在 APK 上按
「叫車」只會拿到一個 403，然後顯示一句錯誤。這是**已知缺口**，見 6.5。

### 6.4 用「支援的路徑」通過它（四步）

`/identity/*` 全部由 `require_active_user` 守門 —— 未驗證的帳號**可以**呼叫它們。
所以這條路本來就是通的，而且它就是正式流程本身，不是測試後門。

```bash
API=http://127.0.0.1:8000/api/v1
PHONE=+85290000001          # 任何 +852 + 8 位都可以

# 1. 拿 token（ALLOW_DEV_OTP=true 時驗證碼固定為 123456）
curl -s $API/auth/otp/request -H 'content-type: application/json' \
     -d "{\"phone\":\"$PHONE\"}"
TOKEN=$(curl -s $API/auth/otp/verify -H 'content-type: application/json' \
     -d "{\"phone\":\"$PHONE\",\"code\":\"123456\"}" \
     | .venv/Scripts/python -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

# 2. 設 username —— 這一步補上 missing 裡的 "profile"
curl -s $API/identity/profile -H "authorization: Bearer $TOKEN" \
     -H 'content-type: application/json' \
     -d '{"username":"qatester","given_name":"QA","family_name":"Tester"}'

# 3. 要一封驗證信，再從日誌撈連結（dev 只印日誌，不真的寄）
curl -s $API/identity/email/request -H "authorization: Bearer $TOKEN" \
     -H 'content-type: application/json' -d '{"email":"qa@example.com"}'
grep -o 'verify-email?token=[A-Za-z0-9_-]*' .tmp/uvicorn.log | tail -1

# 4. 把 ?token= 之後那串貼回去
curl -s $API/identity/email/confirm -H 'content-type: application/json' \
     -d '{"token":"<貼上>"}'
```

驗一下狀態真的翻了：

```bash
curl -s $API/identity/me -H "authorization: Bearer $TOKEN"
# → "account_status":"ACTIVE", "email_verified":true, "phone_verified":true
```

> **不要自己去 `UPDATE users SET account_status='ACTIVE'`。**
> `_promote_if_ready`（`identity_service.py:277`）是**唯一**定義「齊了沒有」的地方，
> 兩道閘共用它。手改會造出一列「狀態寫著 ACTIVE、但三樣有缺」的資料，而那一列之後
> 會在任何一道閘上以一個看起來毫不相關的錯誤炸掉。

### 6.5 已知缺口：App 做不到自己的 onboarding

後端要求三樣齊全，而 App 只做得到一樣（電話）。所以在真機上跑 APK 的實際覆蓋範圍是：

| 流程 | 狀態 |
|---|---|
| 登入、看行程、看帳戶、看歷史 | ✅ 可用 |
| **建立訂單、接單、上線** | ❌ 403（帳號未驗證），而 App 沒有補齊的畫面 |

要測叫車流程目前只有兩條路：用 6.4 的 curl 把帳號補齊（推薦 —— 它同時驗證了後端的
正式流程），或者補上 App 的電郵與使用者名稱畫面（那是 **App 的功能缺口**，不是後端
的問題）。

---

## 7. 驗證腳本

| 腳本 | 用途 |
|---|---|
| `scripts/verify/verify_api.py` | 一次性 API 冒煙測試 |
| `scripts/verify/live_smoke.py` | 對執行中的 API 做端到端冒煙 |
| `scripts/verify/security_verify.py` | 安全控制的實跑驗證 |
| `scripts/verify/prod_boot_drill.py` | 逐條驗證 11 種「正式環境必須拒絕啟動」的情形 |
| `scripts/dev/serve_and_probe.py` | 背景起 uvicorn 並等 health 通過 |
| `scripts/dev/stop_server.py` | 停掉它 |

逐個腳本的完整說明在 [`../scripts/README.md`](../scripts/README.md)。

---

## 8. 常見陷阱

1. **OTP 不會出現在回應裡。** 沒設 `ALLOW_DEV_OTP=true` 就會一直等一個永遠不來的
   欄位。這條是本文檔最常被問的一個。
2. **`JWT_SECRET_KEY` 留空 → 啟動失敗。** `.env.example` 裡它是空的，複製之後
   一定要填。
3. **OTP 的 IP 速率限制是每 600 秒 10 次。** 反覆測登入很快就 429；單機 QA 先把
   `OTP_IP_RATE_LIMIT` 調高。
4. **Postgres／Redis 用 `127.0.0.1`，不要用 `localhost`。** Windows 上 `localhost`
   會先試 `::1`，而 Docker 埠只有 IPv4，每次新連線白等約 2 秒。
5. **Android 模擬器連不到 `127.0.0.1`。** 要用 `10.0.2.2`（模擬器對宿主機的別名）。
6. **電話號碼必須是 `+852` + 剛好 8 位。** 少了 `+`、含空格、少一位，都是 422。
7. **`APP_ENV=prod` 不是「假裝正式」的開關**，它會令 app 拒絕用 dev 預設值啟動。
   要驗正式啟動行為請用 `prod_boot_drill.py`。
8. **`APP_ENV` 沒有預設值。** 忘了設 `.env`，啟動就直接失敗 —— 這是刻意的
   （舊版預設 `dev` 會讓任何沒有 `.env` 的主機變成回傳固定 OTP 的 dev 伺服器）。
9. **管理員帳號與 `users.role = ADMIN` 是兩回事。** 後台走 `admin_accounts`；
   用 `create_admin.py` 建的 `users` 列**打不開** `/api/v1/admin/*`。
10. **管理員 TOTP 在第一次登入時才註冊。** 腳本不會產生秘密；想跳過互動流程用
    `enrol_admin_totp.py`。
11. **`serve_and_probe.py` 把 API 綁在 `127.0.0.1`，`APP_HOST` 對它無效。** 真機
    要麼 `adb reverse tcp:8000 tcp:8000`，要麼自己起 `uvicorn --host 0.0.0.0`。
    見第 6.2 節。
12. **OTP 登入成功 ≠ 帳號可用。** 新帳號是 `UNVERIFIED`，建立訂單／接單／上線會
    回 403 `ACCOUNT_UNVERIFIED`，而**手機 App 沒有補齊驗證的畫面**。見第 6 節。

---

## 9. 收尾

QA 機不建議長期開著 dev 設定：

- 把 `.env` 裡為測試放寬的值（`ALLOW_DEV_OTP`、`OTP_IP_RATE_LIMIT`）改回基線。
- `docker compose down` 會保留資料卷；要清乾淨加 `-v`。
- 若中途強制中斷過 `pytest`，Postgres 會留下孤兒測試庫
  （`realtaxihk_t_*`、`realtaxihk_v_*`），之後的測試會以
  `relation "..." does not exist` 失敗，看起來像程式碼 bug。清法：
  `DROP DATABASE "<name>" WITH (FORCE)`。
