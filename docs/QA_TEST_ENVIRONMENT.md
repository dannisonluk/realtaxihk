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
> `SMTP_HOST`／`SMTP_FROM` 必須設定、`TRUSTED_PROXY_COUNT` ≥ 1、
> `TURNSTILE_SECRET_KEY` 必須設定）。
> 想驗正式啟動行為，用 `scripts/verify/prod_boot_drill.py`，它逐條驗這 12 種情形。
>
> `TURNSTILE_SECRET_KEY` 是這一組裡**唯一「執行期會 fail open」**的設定：沒設的話
> `DisabledHumanVerifier` 只印一條警告就放行全部請求，所以部署漏填的服務看起來
> 完全健康。這正是啟動時必須直接拒絕、而不能只靠執行期行為判定的原因。

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
cd .. && python serve.py             # → http://127.0.0.1:3000
```

`serve.py` 預設埠 **3000**（`CORS_ORIGINS` 裡本來就有它），而且**預設服務新版**
React build（`web/dist`）；要服務舊版 console 才加 `--legacy`。

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

## 6. 手機 App 首次登入：登入與叫車解鎖是兩件事

### 6.1 登入用電郵加密碼，電話只用來解鎖「call車」

> **這一節在 2026-10-03 改寫。** 舊版是「電話驗證**就是**登入」——`POST /auth/otp/verify`
> 同時負責註冊與登入，電話號碼既是身分也是唯一的秘密。掉了號碼就等於掉了帳號，而且
> 新使用者遇到的第一件事，是被要求證明一個沒人問過他的號碼。那個模型已經拆掉。

現在是幾個獨立的動作：

| 動作 | 端點 | 需要什麼 |
|---|---|---|
| **有帳號** | `POST /auth/register` | email + 密碼 + 一個**聲稱**的電話（不驗證） |
| **登入** | `POST /auth/login` | email + 密碼 |
| **解鎖 call車** | `POST /identity/phone/request` → `/identity/phone/confirm` | 已登入 + 能收到那個號碼的 OTP |
| 次要登入 | `POST /auth/otp/verify` | 一個**已經被某帳號驗證過**的號碼 + OTP |

所以「一開始就要電話 verify」不再是事實：新註冊的帳號**可以立刻登入、看行程、改個人
資料**，只有建立訂單／接單／上線會被擋，理由是 `403 PHONE_NOT_VERIFIED`，客戶端據此
顯示「驗證號碼以解鎖叫車」。

`/auth/otp/verify` 還在，但它**不能**註冊，也**不能**登入一個只是「聲稱」該號碼的
帳號 —— 它要求 `phone_verified_at IS NOT NULL`。這是留著這個端點唯一的理由：一個
被偷走的 OTP 只能到達 SIM 卡本來就到的帳號。

電話要真號碼嗎？不用：

| 你想要 | 做法 |
|---|---|
| 一個固定的驗證碼 | `.env` 設 `ALLOW_DEV_OTP=true` → 永遠是 `123456` |
| 用真號碼但不想等 WhatsApp | 讀 API 日誌的 `[WHATSAPP:dev] OTP <code> -> +852****4567`（需 `LOG_LEVEL=INFO`） |
| 一個號碼 | 任何 `+852` + 8 位都可以，但 `+8520000xxxx` 除外 —— 那是審查者帳號的保留區（見 6.6） |
| 完全跳過解鎖 | 用審查者帳號，見 **6.6**：它建立時就是已驗證的 |

### 6.2 第一道牆：真機連不到你的 API

`scripts/dev/serve_and_probe.py` 預設綁 `127.0.0.1`。要畀真機經 Wi-Fi 連，直接用
`--host 0.0.0.0`：`scripts/dev/serve_and_probe.py --host 0.0.0.0`，然後 APK 用
`--dart-define=API_BASE_URL=http://<區網 IP>:8000`。`APP_HOST` 對呢個腳本無效，個
flag 先係唯一接口。

| 做法 | 步驟 | 適用 |
|---|---|---|
| **A（建議）** | `adb reverse tcp:8000 tcp:8000`，APK 用 `--dart-define=API_BASE_URL=http://127.0.0.1:8000` | USB 連著的實機。`adb reverse` 令手機上的 `127.0.0.1:8000` 轉到電腦，**完全不用改綁定** |
| **B** | 自己起 `.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers`，APK 用 `--dart-define=API_BASE_URL=http://<區網 IP>:8000` | Wi-Fi、不想插線。要自己確認防火牆放行 8000 |

`adb reverse` 在每次重新插線（或 adb 重啟）之後都要再下一次。

> `--no-proxy-headers` 不是可選的裝飾：少了它，uvicorn 會相信來自 `127.0.0.1` 的
> `X-Forwarded-For`，每一個 IP 速率限制就變成可偽造（SEC-31）。

### 6.3 第二道牆：登入了，但還沒驗證電話

這一處**最容易被誤判成 bug**。

登入成功不等於可以叫車。`POST /orders`、`/orders/{id}/grab`、`/drivers/location` 掛的是
`require_phone_current`，而它底下還有一層 `require_phone_verified`：

| 缺什麼 | 403 的 `reason` | 客戶端該做什麼 |
|---|---|---|
| 沒驗證過電話 | `PHONE_NOT_VERIFIED` | 顯示「驗證號碼以解鎖叫車」，導去 `/identity/phone/*`（App 是 `/phone/unlock`） |
| 驗證過但逾期（P-4 月檢） | `PHONE_REVERIFY_DUE` | 顯示「重新驗證」，附上 `phone_reverify_due_at`；App 走同一畫面但用 `reverify` 端點 |

```json
{"code": "FORBIDDEN",
 "message": "verify a phone number to call a taxi",
 "details": {"reason": "PHONE_NOT_VERIFIED"}}
```

> **`account_status` 不再是閘。** 它現在只是「資料齊不齊」的旗標（console 會顯示它），
> `require_active_user` 從不讀它。舊版的 `require_verified_account` 與
> `ACCOUNT_UNVERIFIED` 已經刪除 —— 所以「登入之後什麼都被擋」這個現象不會再出現。

### 6.4 用「支援的路徑」解鎖（三步）

`/identity/*` 全部由 `require_active_user` 守門 —— 未驗證電話的帳號**可以**呼叫它們。
這條路就是正式流程本身，不是測試後門。

```bash
API=http://127.0.0.1:8000/api/v1
PHONE=+85290000001          # 任何 +852 + 8 位都可以（+8520000xxxx 除外）
EMAIL=qa@example.com
PASS='Qa-Test-Passw0rd-9'   # 至少 12 字元，見 app/core/passwords.py

# 1. 註冊 —— 電話只是聲稱，不驗證
TOKEN=$(curl -s $API/auth/register -H 'content-type: application/json' \
     -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\",\"phone_e164\":\"$PHONE\"}" \
     | .venv/Scripts/python -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

# 2. 要一個 OTP 給那個號碼（ALLOW_DEV_OTP=true 時固定是 123456）
curl -s $API/identity/phone/request -H "authorization: Bearer $TOKEN" \
     -H 'content-type: application/json' -d "{\"phone_e164\":\"$PHONE\"}"

# 3. 確認 —— 這一步才把號碼綁上，並開始 P-4 的月檢時鐘
curl -s $API/identity/phone/confirm -H "authorization: Bearer $TOKEN" \
     -H 'content-type: application/json' \
     -d "{\"phone_e164\":\"$PHONE\",\"code\":\"123456\"}"
# → "verified": true, "phone_verified": true, "phone_reverify_due_at": "..."
```

> 之後叫車就通了。驗一下：

```bash
curl -s $API/identity/me -H "authorization: Bearer $TOKEN"
# → "phone_verified": true, "phone_reverify_blocked": false
```

> **不要自己去 `UPDATE users SET phone_verified_at = now()`。**
> `PhoneBindingService.confirm` 是唯一同時寫 `phone_verified_at` 與
> `phone_reverify_due_at` 的地方，而 `evaluate()` 把 NULL 期限讀成「已逾期」。只改一半
> 會造出一列「電話已驗證、但立刻被判定逾期」的資料，症狀看起來毫不相關。

### 6.4a 一鍵造乘客＋司機測試帳號（service layer）

`scripts/ops/create_booking_account.py` 用 auth service layer 做上面三歩，不是 SQL UPDATE：

```bash
# .env 或環境變數
ALLOW_DEV_OTP=true
# 本機 QA 自行設定、最少 12 字元；腳本不會把它寫入 repo 或印出來
BOOKING_TEST_PASSWORD='<dev-only-password-12-plus>'

.venv/Scripts/python.exe scripts/ops/create_booking_account.py
# 預設：PASSENGER_PHONE=+85291230001 DRIVER_PHONE=+85291230002
# 可改用 --passenger-phone / --driver-phone（亦接受 PASSENGER_PHONE / DRIVER_PHONE env）
```

已註冊且已驗證的號碼會印「already registered and verified — skipping」，所以可重跑。
Log 只印遮罩（例如 `+852****0001`）；密碼不放 argv，也不會印。**這只是兩個已驗證的
user 帳號**，不是已通過 KYC 的司機 —— 司機檔案仍由 App `/drivers/register` ＋ admin KYC
建立。部署側唯一未做的是在實際 QA env 跑一次證明可用。

### 6.4b Android 密碼重設 App Links（部署側驗證）

App 端已收 `/reset-password` 與 `/magic` 的 Android `intent-filter`
（AUTO_VERIFY，`https/http://hkfastdc.com`），Flutter cold-start parser 會把
`https://hkfastdc.com/reset-password?token=abc` 送到 `/password/reset` 並預填 token。
**仍要做**的是部署時取得 APK signing `SHA256_CERT_FINGERPRINT`，執行：

```bash
PACKAGE_NAME=com.hkfastdc.mobile \
SHA256_CERT_FINGERPRINT=AA:BB:... \
.venv/Scripts/python.exe scripts/ops/render_assetlinks.py --site-host hkfastdc.com
```

並把輸出的 `assetlinks.json` 放到 `https://hkfastdc.com/.well-known/assetlinks.json`，
然後在真機／Play Console 驗證 App Links。指紋不會寫入 repo。

### 6.5 App 的覆蓋範圍（2026-10-04 起：三個入口已分開）

登入與電話驗證在 App 裡是分開的，電話驗證只是「解鎖叫車」的條件：

| 流程 | App 路由 | 狀態 |
|---|---|---|
| 註冊（email + 密碼 + **聲稱**電話） | `/login/register` | ✅ |
| 主要登入（email + 密碼） | `/login` | ✅ |
| 次要登入（已驗證號碼 + OTP） | `/login/phone` | ✅ |
| 解鎖叫車（綁定並驗證電話） | `/phone/unlock` | ✅ |
| 電郵重設密碼（deep link 預填 token） | `/password/reset` | ✅（App Link 待部署側驗證，見 6.4b） |

`/phone/unlock` **刻意不放在 `/login` 之下**：`resolveRedirect` 會把已登入的使用者趕離
所有 `/login` 路徑，而需要解鎖的正是「已登入但未驗證」的帳號 —— 放在那裡等於對唯一需要
它的帳號隱形。它掛在 root navigator 上，可從乘客帳戶頁、司機帳戶頁，以及流程中途的 403
抵達。

App 只在**真的會被擋的兩個動作**之後主動提議解鎖：乘客叫車（`POST /orders`）與司機的
工作畫面。走同一條 guard 鏈的其他動作（例如司機牌照）目前只顯示伺服器回傳的訊息。**估價
沒有被擋**（`GET /fare/estimate` 不在 guard 鏈上），所以未驗證的帳號照樣看得到價錢 ——
App 不會在那裡假裝有一道牆。

真機跑 APK 前要處理的一件事：

| 事項 | 做法 |
|---|---|
| **Turnstile site key** | **沒有任何端點提供它**（`app/core/config.py` 定義了 `turnstile_site_key`，但沒有 route 回傳），所以只能用 build-time define：`--dart-define=TURNSTILE_SITE_KEY=0x4AAAAAAA...`，必要時配 `--dart-define=TURNSTILE_BASE_URL=https://<site key 允許的網域>/` |
| **release build 沒帶 key** | App 刻意顯示錯誤面板而不是登入表單。正式環境 `TURNSTILE_SECRET_KEY` fail-closed，每個請求都會 403 —— 讓使用者對著一個永遠失敗的按鈕，比當場說明更糟 |

> **`TURNSTILE_BASE_URL` 決定 token 是為哪個網域簽發的。** Cloudflare 驗的是 origin，
> 所以它必須在 site key 的 allowed-domain 清單上 —— 不一定是 API 的網域。

#### 還缺什麼

* **沒有補完個人資料的畫面。** `POST /identity/profile` 要 username／given name／family
  name，而 App 沒有任何地方呼叫它，所以註冊出來的帳號 `username IS NULL`。註冊刻意不收
  姓名 —— 那會在一個以「短」為目的的表格上加第四個必填欄位。
* **Android App Links 尚未在已部署網域驗證。** App 端 scaffold 已加，但
  `assetlinks.json` 需要 deployment 的 APK signing 指紋，見 6.4b。
* **Turnstile 的 WebView 沒有在真機驗證過**，`webview_flutter` 是否真的進得了 APK 也
  沒有在本機驗證過（本機的 Gradle build 跑不完，見
  [`../mobile/README.md`](../mobile/README.md) 的 Building an APK）。CI 的
  `flutter build apk --debug` 是唯一會驗到這件事的地方。
* 這幾條連同其他記在 [`WORK_SUMMARY.md`](WORK_SUMMARY.md) §4C。

要測叫車流程仍然可以用 6.4 的 curl（推薦 —— 它同時驗證了後端的正式流程），或者用 6.6 的
審查者帳號（建立時就已驗證，完全不用解鎖）。

### 6.6 審查者帳號：已驗證、會自己過期、動不了錢

App Store / Play 審查，或者外部審計，需要一個能用的帳號 —— 但不該是一個能做其他任何事
的帳號。用 `scripts/ops/create_reviewer_account.py` 建一個：

```bash
# .env 或環境變數
ALLOW_REVIEWER_ACCOUNT=true
REVIEWER_PASSWORD='...'      # 不放在 argv：那會進 shell history 與 process table

.venv/Scripts/python.exe scripts/ops/create_reviewer_account.py \
    --email reviewer@example.com --days 14 --yes

# 列出全部，或提早撤銷
.venv/Scripts/python.exe scripts/ops/create_reviewer_account.py --list
.venv/Scripts/python.exe scripts/ops/create_reviewer_account.py --revoke reviewer@example.com
```

它拿到什麼、拿不到什麼：

| | |
|---|---|
| **可以** | 用 `POST /auth/login`（email + 密碼）登入，看行程，**建立訂單** |
| **不可以動錢** | 結構上不行，不是政策上不行。價值只經 `driver_profiles.deposit` 與 append-only 的 `ledger_entries` 流動，兩者都掛在司機檔案上，而司機檔案需要**管理員**的 KYC 決定與**管理員**的入金 |
| **不可以開後台** | 後台認的是 `admin_accounts`（另一張表、強制 TOTP）。`users` 的一列無論 `role` 寫什麼都不是管理員 |
| **會自己失效** | `reviewer_expires_at` 在**每一個**已認證請求上由 `require_active_user` 檢查，過期即 `403 REVIEWER_ACCOUNT_EXPIRED`，不需要任何人記得去撤銷 |

限制與注意：

- **密碼只出現一次。** 腳本不儲存也不顯示它；掉了就 `--revoke` 再建一個。
- **`--days` 是硬期限。** 過了就失效，包括已經發出的 access token（最壞情況受限於
  `ACCESS_TOKEN_EXPIRE_MINUTES`）。
- **號碼取自保留區 `+8520000xxxx`**，那不是 OFCA 配發的號段，所以不會撞到真人。它不
  需要能收訊，因為帳號建立時就已驗證。
- **`APP_ENV=prod` 額外要求 `--yes`**，與 `ALLOW_DEV_OTP` 同一個形狀：一個繞過正常
  註冊流程的憑證，不該只差一個打錯的環境變數就存在。
- **`.env.example` 的 `ALLOW_REVIEWER_ACCOUNT` 預設是 `false`。**

---

## 7. 驗證腳本

| 腳本 | 用途 |
|---|---|
| `scripts/verify/verify_api.py` | 一次性 API 冒煙測試 |
| `scripts/verify/live_smoke.py` | 對執行中的 API 做端到端冒煙 |
| `scripts/verify/security_verify.py` | 安全控制的實跑驗證 |
| `scripts/verify/prod_boot_drill.py` | 逐條驗證 12 種「正式環境必須拒絕啟動」的情形（含一條正常啟動） |
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
11. **`serve_and_probe.py` 預設綁 `127.0.0.1`，`APP_HOST` 對它無效。** 真機要
    `serve_and_probe.py --host 0.0.0.0`（或 `adb reverse`）。見第 6.2 節。
12. **登入成功 ≠ 可以叫車。** 新帳號只是「聲稱」了電話；建立訂單／接單／上線會回
    403 `PHONE_NOT_VERIFIED`，要先去 `/identity/phone/*` 驗證號碼。`account_status`
    **不是閘**，它只是資料齊全度的旗標。見第 6 節。
13. **登入連續錯 5 次鎖 15 分鐘，而且回的是 401 不是 429。** 這是刻意的：429 會洩漏
    「這個帳號存在、而且被猜到觸發鎖定」。看到 `account temporarily locked` 就等
    `LOCKOUT_MINUTES`，或直接清掉該列的 `locked_until` 與 `failed_login_count`。
14. **四條路由現在要過 Turnstile：`register`、`login`、`auth/otp/request`、
    `identity/phone/request`。** `APP_ENV=dev`/`test` 走 `DevHumanVerifier`（全放行），
    所以本機不會擋；但一個沒有 `TURNSTILE_SECRET_KEY` 的**正式**部署會拒絕啟動
    （`_fail_closed`）。前端要帶 `human_token` 欄位，被擋時回 403
    `HUMAN_VERIFICATION_REQUIRED`。**注意 `otp/verify`、`phone/confirm`、
    `phone/reverify` 刻意不在名單上** —— 它們的驗證碼本身已經有五次上限與 per-IP
    限流，在人和自己的六位數字之間放一個 CAPTCHA 是最敵意的位置。App 現在會帶這個
    欄位，但 site key 是 build-time define —— 見第 6.5 節。

---

## 9. 收尾

QA 機不建議長期開著 dev 設定：

- 把 `.env` 裡為測試放寬的值（`ALLOW_DEV_OTP`、`OTP_IP_RATE_LIMIT`）改回基線。
- `docker compose down` 會保留資料卷；要清乾淨加 `-v`。
- 若中途強制中斷過 `pytest`，Postgres 會留下孤兒測試庫
  （`realtaxihk_t_*`、`realtaxihk_v_*`），之後的測試會以
  `relation "..." does not exist` 失敗，看起來像程式碼 bug。清法：
  `DROP DATABASE "<name>" WITH (FORCE)`。
