# realtaxihk — 工作總覽 / Work Summary

> **EN — Summary.** The index for this project: what is built, what has been
> verified by actually running it, and — the part most documents omit —
> **what is still not done and why**.
>
> **Read §4 first if you are picking this up cold.** It splits the outstanding
> work into two categories that need different handling: **§4A** blocked on
> credentials or a deployment target (not a code problem), **§4B** deliberate
> product trade-offs, recorded so they are not "fixed" later, **§4C** known
> functional gaps that need code, and **§4D** closed items kept so they are not
> re-opened.
>
> **中文摘要**：本專案的總索引 —— 已建了什麼、已實跑驗證了什麼，以及**還有什麼
> 未做、為什麼**。**剛接手請先讀 §4**：它把未完成項分成四類 —— 卡在憑證／部署
> 目標（不是程式問題）、刻意的產品取捨（看似未完成，其實已決定）、**已知功能缺口
> （要寫程式，見 §4C）**，以及已結案項。
>
> 逐輪工作的完整記錄（問題 → 修法 → 證據，§2.1…§2.21）已抽到
> **[`archive/WORK_LOG.md`](archive/WORK_LOG.md)**，內容刻意不更新。

**兩個適用於全份文件的警告**（都是踩過才知道）：

- **HEAD hash 刻意不寫下來。** 寫死過三次，每次之後的 commit 都令它變錯。
  要查：`git log --oneline -1`。
- **「有沒有未推的 commit」不是本文件回答的問題。** 用
  `git rev-list --count origin/main..HEAD` —— 而 **0 的意思是 HEAD 等於
  origin/main**（即所有改動都未 commit），**不是**「都推上去了」。

- **現時狀態**（2026-10-07 重新量測）：
  > ✅ **後端 `pytest` 全套一次過實跑全綠**（Docker Desktop 開住、`realtaxi-db` ＋
  > `realtaxi-redis` 兩隻 container 都 healthy）：
  > `pytest tests -q --junit-xml=.tmp/final4.xml` = **1283 passed / 0 failed / 0 error /
  > 0 skipped**（2026-10-07）。先前「一次過跑會中途中止、唔敢宣稱 full-suite 全綠」
  > 嘅情況**已經消失** —— 現在單一 process 順序跑就穩定全綠。
  > ```
  > docker compose up -d db redis
  > .venv/Scripts/python.exe -m pytest tests -q --junit-xml=.tmp/final.xml
  > ```
  > ⚠️ 並行跑兩隻 pytest 仍**不建議**（兩隻 heavy run 爭同一 DB/Redis 資源），
  > 但「互相污染出假 failed」嘅根因已消除：GEO index 已納入
  > `REDIS_KEY_NAMESPACE`（per-process），同 rate-limit key 一樣。見
  > [`README.md`](../README.md) §7.2。
  >
  > 計數器的量法統一記在 [`README.md`](README.md) 的「量測基準」表 —— 改架構後先重跑量法再改這裡。
  - **實跑得到**：`ruff check .` **All checks passed!**（全樹）· `ruff format --check .`
    **238 files already formatted** · `mypy app` **141 files / 0 errors** · `compileall app` rc=0 ·
    console `tsc --noEmit` **exit 0（乾淨）** · console vitest **94 passed（14 檔）** ·
    Dart harness **161 passed / 0 failed** · `dart_check.py` **95 files / 0 diagnostics** ·
    contract **64 fixtures decoded, 0 failure**（共 65 個 fixture json）·
    `audit_response_models.py` **OK（81 fixture blocks / 129 operations 全有 `response_model`）** ·
    API **115 paths / 129 operations** · `alembic heads` **單一 head `e6f7c3d9e5a9`**（22 個 migration）。
  - ✅ **現時無未修項。** 先前列為「未提交 WIP、未經同意去改」嘅 mobile
    `fixed_offers_screen.dart` `$` escape 問題，**已隨 WIP 收斂修好**：`dart_check.py`
    對 `mobile/lib` 現報 **0 diagnostics**。逐條歷史見
    [`archive/ERROR_SCAN_2026-10-05.md`](archive/ERROR_SCAN_2026-10-05.md)。
  - ✅ **`dart format` 排版閘（2026-10-06 修）。** CI 的 mobile job 一直有
    `dart format --line-length 100 --output=none --set-exit-if-changed lib tool`
    （`.github/workflows/ci.yml`），但**加閘時只修了 8 個檔，之後再度漂移** ——
    實測 `HEAD`／`origin/main` 都係 **19 檔唔過**（工作區連 WIP 共 24 檔），
    即係呢個閘由頭到尾都係「聲明咗但未真正綠過」。已用**同 CI 完全一致**嘅
    SDK（本機 Flutter **3.44.0** / bundled Dart **3.12.0** == CI 釘嘅版本）重排
    全樹；非空白／非尾逗號內容逐檔核對**零改動**。現況：
    `dart format --line-length 100 --output=none --set-exit-if-changed lib tool`
    = **0 changed**，而 assertions 155、contract 64、`dart_check.py` 85/0 全部照過。

> **本文件的用途**：一份可以單獨看完的總覽。其他 `docs/*` 是**主題深入報告**；
> `.workbuddy-ai/memory/*.md` 是**逐日流水**（append-only，不整理）。
> 本文件是索引 + 摘要，不取代它們。文檔全貌見 [`README.md`](README.md)。

---

## 1. 交付物

| 交付物 | 位置 | 技術 | 狀態 |
|---|---|---|---|
| 後端 API | `app/` | FastAPI (async) + SQLAlchemy 2.0 async + PostgreSQL 16/PostGIS + Redis 7 + Alembic | ✅ **115 paths / 129 operations，全部有 `response_model`** · 141 檔 / 28,933 LOC · pytest 全套 **1283 / 0**（2026-10-07 單一 process 實跑） |
| Flutter App | `mobile/` | Flutter + Riverpod 3.4.3 + Dio + go_router 17（**34 個畫面**，三角色）；品牌資產由 `tool/gen_branding_assets.py` 由 `branding/source/` 的原圖產生 | ✅ 161 tests · fixture contract OK · APK debug 已成功 build（2026-10-07，~183 MB） |
| Web 管理後台 | `admin-web/web/`（React + Vite）、`admin-web/legacy/`（legacy） | React + Vite（新版）、Vanilla JS（舊版） | ✅ **94 vitest passed（14 檔）** · `tsc --noEmit` **exit 0** |

一個 repo、三件完整交付物。定位：**Cap. 374D 合規的士資訊中介**（非的士營運商）。

---

## 2. 做過的事 → [`archive/WORK_LOG.md`](archive/WORK_LOG.md)

按主題記錄每一輪的問題、修法與證據（§2.1 後端上線阻塞修復 … §2.21 收尾批次
commit）。**該文件是歷史記錄，刻意不更新** —— 裡面的測試數、行號、檔案佈局都
反映當時狀態。本文件只保留現況與未做項。

---

## 3. 驗證標準：「全部實跑」

不接受「讀源碼覺得無問題」。每次改動都跑齊：

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy                                            # types; no DB needed（只掃 app/）
# pyright 不是 gate（mypy 才是，而 mypy 的 files 刻意只有 app/）。要交叉檢查
# tests/ 或 scripts/ 時，需要臨時 pyrightconfig.json：
#   {"venvPath":".","venv":".venv","pythonVersion":"3.12"}   ← 用完即刪，不要入 repo
npx --yes pyright@1.1.408 app/ scripts/ tests/         # 現為 0 errors
uv run python -m pytest -q --junit-xml=.tmp/final4.xml            # 全套 2026-10-07 實跑：1283 passed / 0 failed（需 Docker，見 §0）
uv run python scripts/verify/audit_response_models.py  # 81 塊 fixture vs response_model，0 遺失
cd admin-web/web && npx tsc --noEmit && npm run build && npx vitest run --no-file-parallelism --pool=forks
cd mobile && dart --packages=.dart_tool/package_config.json tool/run_tests.dart
cd mobile && dart format --line-length 100 --output=none --set-exit-if-changed lib tool  # CI 排版閘；必須 0 changed
cd mobile && python tool/dart_check.py mobile          # LSP，非 flutter analyze；要帶路徑
cd mobile && dart --packages=.dart_tool/package_config.json tool/verify_contract.dart
# 品牌資產問的是「有沒有跟上原圖」，不是「能不能編譯」——不跑這條檢查就沒有人會發現
.venv/Scripts/python mobile/tool/gen_branding_assets.py --check
# 圖示、`assets:`、以及 Flutter plugin 集合，只有真正建置 APK 才驗得到：
# 本機這條路 2026-10-07 已成功跑完（`flutter build apk --debug`，約 183 MB）。
cd mobile/android && FLUTTER_SUPPRESS_ANALYTICS=true ./gradlew :app:assembleDebug
# 備份：不止跑 backup，還要跑 drill
.venv/Scripts/python scripts/ops/db_backup.py backup
.venv/Scripts/python scripts/ops/db_backup.py verify   # 還原 + 逐表核對 row count
```

**關鍵**：改動 money 格式後，要對**真 server + 真 Postgres** 做 live 驗證，不可以
只信 TestClient。當年的 `"0.0"` bug 與 4 個備份 bug 都是「讀源碼看不到、真跑才爆」。

> `--junit-xml=` 不是可選的：`[safe-delete]` marker 會注入 stdout 並截斷摘要，
> 令印出的總數與 exit code 都不可信。**要讀 XML。**

---

## 4. 仍未做

### A. 需要你提供憑證／部署目標（我無法代做）

| 項目 | 阻塞原因 |
|---|---|
| **P1-1 WS token 走 `?token=`** | `app/api/ws.py` 仍是 query param。設計上**刻意如此**（瀏覽器 WS 無 header 通道），已有 `StripTokenQueryFilter` 兜底。反代已定為 nginx（`deploy/nginx/hkfastdc.conf`），其 `log_format` 用 `$uri` 而非 `$request_uri`，查詢字串（連 token）不會落地 —— **殘餘洩漏已封**。**主機名已定（2026-10-04）：`hkfastdc.com`，單一 origin。** console 掛 `/console/`（靜態檔），API 保持 `location /`，所以既有路由一行沒改。原本三種拼法已全部統一，`api.` 與 `console.` 前綴移除。nginx 檔內**憑證路徑仍寫死主機名（共五處）**，改漏一處的後果是 nginx **啟動失敗並 restart-loop**，不是警告。 |
| **P1-4 備份 — off-host destination 未選擇** | script 已完成並實跑 PASS（`scripts/ops/db_backup.py`，27 tests，還原演練 49 tables / 17,627 rows 全對）。只剩**選擇 destination**。 |
| **WhatsApp / FCM / Google Maps 未接** | config 欄位存在、env 空。需要三家 provider 的憑證。 |
| **P2-2 遺留：部分退款** | 現時只做全額退還。 |
| **P2-2 遺留：實際打款渠道** | 只寫 ledger，轉帳仍線下人手。需要真實支付渠道。 |
| **P2-3 `distance_km` 由 client 自報** | 乘客可亂報。374D 下估價僅供參考、風險可控，但廣播排序會被 gaming。需 `GOOGLE_MAPS_API_KEY`。 |

### B. 已知產品層取捨（非 bug，記錄在案）

- **調整不會 activate 司機** —— 刻意。更正是記帳、不是付款；維持 `DEPOSIT_REQUIRED`。
- **負餘額合法（arrears）** —— 罰款／費用可以超過按金，司機欠平台。
- **`ADJUSTMENT` 單一管理員直接寫入** —— 由你選擇。無雙人覆核，靠 `created_by` 審計。
  若日後要防內部舞弊，需改成申請→批准流程。爭議裁決的 `moves_money` 路徑已加
  FINANCE 角色閘（職責分離第一步），`ADJUSTMENT` 本身仍為單人。
- **搜尋不逐次審計** —— 高頻讀取，寫滿審計表會淹沒真正的金錢事件。刻意。
- **`ws_max_connections_total` 與限流計數器都是 per-process** —— `ConnectionRegistry`
  把計數放在 process 記憶體，所以 N 個 uvicorn worker 會把上限執行 N 次。
  `docker-compose.prod.yml` 因此把 `--workers` 釘在 1（`API_WORKERS=1`），而**調高
  它是一個三步走的決定**（重算連線池 → registry 搬去 Redis → 才調 worker），
  不是一行改動。消費點只有 `app/main.py:254` 一處，該處有註解。

### C. 已知功能缺口（要寫程式，未排期）

| 缺口 | 影響 | 為什麼現在是這樣 |
|---|---|---|
| **乘客違約罰款「有記錄、未收錢」** | 乘客在 `ACCEPTED` 之後取消，會寫 `PENALTY_CHARGED` 事件（`settled: false`）並設 15 分鐘冷靜期，但**錢沒有實際扣到** | `ledger_entries.driver_profile_id` 是 NOT NULL，而乘客沒有錢包 —— 收乘客的錢要先有乘客錢包／預授權。在沒有支付渠道之前，記錄 + 冷靜期是能做到的全部；admin 事後裁決仍可依事件記錄處理 |
| **App 收得到 `reset-password` deep link，但 Android App Links 尚未驗證（部署側）** | 已加 Android `intent-filter`（`/reset-password`、`/magic`）、Flutter cold-start parser 與 token 預填畫面。**仍未做：把含真實簽署指紋的 `assetlinks.json` 放到 `https://hkfastdc.com/.well-known/assetlinks.json` 並等 Android 驗證** | 指紋只存在於 deployment 的 APK signing 產物，所以 repo 刻意不印／不提交。可用 `scripts/ops/render_assetlinks.py` 在部署時產生 |
| **本機 APK build 已驗證（2026-10-07）** | `flutter build apk --debug` 成功，產出 `mobile/build/app/outputs/flutter-apk/app-debug.apk`；因此 `res/`、`assets:` 與 Flutter plugin 集合已由本機實跑證明，唔再只靠 CI 或舊成功紀錄 | Release signing／Google Maps API key 仍然係部署決定，唔影響 debug build；`webview_flutter` 已隨 plugin 集合同時進入 APK |
| **「一鍵造能叫車的測試帳號」已收斂，剩下的是跑在 QA env** | `scripts/ops/create_booking_account.py` 已用 service layer 走 `/auth/register` → `/identity/phone/request` → `/identity/phone/confirm` 對應的 auth services，**不是 SQL UPDATE**；預設 `+85291230001`（乘客）＋`+85291230002`（司機），已驗證的號碼會跳過。仍需在 `ALLOW_DEV_OTP=true` 的實際 QA env 跑一次證明可用 | 刻意走正式流程而非直接改 DB；driver onboarding（牌照／按金）仍由 App + `/drivers/register` + admin KYC 完成，腳本不偽造司機檔案 |
### D. 已結案（保留以免重複處理）

- **Sentry 已接好**（`app/main.py`，`sentry_dsn` 有值就 init）。已補 `release`
  （綁 `_API_VERSION`）與 `max_request_body_size="never"`（PDPO：不送 request body）。
- **文件語言已統一**：`docs/` 與三份 README 全部為**書面語（繁體）**。
- **行號引用已處理**：設計文件重新指向現行位置；帶日期的審計快照**不改行號**，
  只保留「已失效」註記（重新編號會假裝那些發現仍描述現況）。
- **部署目標已定**：選項 A（單台 VPS + Compose），反代用 nginx。產物見
  `deploy/README.md`、`docker-compose.prod.yml`。
- **P4 in-trip 重新設計已實作**（2026-10-06）。狀態機新增
  `PENDING_ARRIVAL_CONFIRM`（到達待乘客確認）、`DESTINATION_CHANGED`（**非終態**）、
  `INTERRUPTED`（**終態**，與 `CANCELLED` 分開，因為結算與保險處理不同）；
  兩步到達驗證（`arrival-claim` 用 DB 記錄的 GPS 判定 → `arrival-confirm` 乘客
  自己手機號碼尾 4 位，3 次失敗回 `ACCEPTED` 並自動開 dispute）；
  `POST /interrupt`（**即時生效**，同一 transaction 自動開 dispute，不退款）；
  `POST /change-destination`（重新估價、只抄一次原目的地、有次數上限、FIXED 降回
  METER）；`/start` 扣 $5 `PLATFORM_TRIP_FEE`（`reference="trip:{id}"` 冪等）；
  違約扣款（乘客 100% / 司機 50% 估價）+ 15 分鐘 Redis 冷靜期；
  `grab` 兩道閘（429 `COOLDOWN` / 423 `DEPOSIT_INSUFFICIENT`）。
  測試：`tests/api/test_orders_p4.py`（32 條）；App 端見
  `trip_tracking_screen.dart`、`driver_active_trip_screen.dart`、
  `change_destination_sheet.dart`、`interrupt_sheet.dart`。
- **改密碼／忘記密碼已實作**（2026-10-06）。`POST /auth/password/change`（需目前
  密碼；成功後撤銷**全部** session，包括呼叫者自己）、
  `POST /auth/password/forgot`（對已註冊與未註冊地址回答**完全相同**，避免成為
  「這個地址有沒有帳號」的查詢工具）、`POST /auth/password/reset`（單次使用、
  只存 SHA-256 digest、所有失敗同一句 400）。測試：
  `tests/api/test_password_flow.py`（22 條）；App 端見
  `change_password_screen.dart`、`forgot_password_screen.dart`。
- **Android 密碼重設 deep-link scaffold + 一鍵 booking 帳號腳本**（2026-10-07）。
  `mobile/android/app/src/main/AndroidManifest.xml` 加 AUTO_VERIFY VIEW
  `https/http://hkfastdc.com/{reset-password,magic}`；`routing_rules.dart` 提供
  無依賴 cold-start parser；新 `password_reset_screen.dart` 會預填 `token`。
  部署時才執行 `scripts/ops/render_assetlinks.py`（需要 APK signing
  `SHA256_CERT_FINGERPRINT`）並把 `assetlinks.json` 放到
  `https://hkfastdc.com/.well-known/`。同日加
  `scripts/ops/create_booking_account.py`，透過 auth service layer 種
  `+85291230001`／`+85291230002` 已驗證帳號，見 `QA_TEST_ENVIRONMENT.md` §6.4a。
- **P4 的 `interrupt` 補回後端 `(party, reason)` 校驗**（2026-10-06）。設計文件 §6.1 明寫
  「前端過濾是禮貌，後端校驗是授權」，但端點從未校驗：`InterruptIn` 沒有 validator、
  handler 只檢查 `OTHER` + note，所以乘客可以申報 `PASSENGER_MISCONDUCT`（自我指控），
  而這個 enum 正是 admin 判決所憑的證據。現以
  `app/models/user.py::INTERRUPTION_REASONS_BY_PARTY` 由 enum 推導
  （新增成員預設兩邊都准，不會靜默變成「存在但沒人能填報」），拒絕時回
  **422 `REASON_NOT_FOR_PARTY`**，且在狀態轉換之前。文件裡寫反的示例亦已改正
  （原文說「`interrupted_by_kind='driver'` 時拒絕 `PASSENGER_MISCONDUCT`」，方向剛好相反）。
- **P4 首次真正對住「已 migrate 的資料庫」跑過**（2026-10-06）。在此之前 dev DB 停在
  `c1f2e3d4a5b6`，P4 migration 從未套用 → 任何 `POST /orders` 都是 500
  `column "arrival_claimed_at" of relation "orders" does not exist`。測試看不到，因為
  `tests/conftest.py` 用 `Base.metadata.create_all` 建 schema、從不跑 migration。
  套用 `alembic upgrade head` 後第二個遺留物即現：
  `gen_mobile_fixtures.py::reset_dev_state()` 刪 `orders` 時撞上 P4 新表
  `order_events` / `order_disputes` 的 RESTRICT 外鍵。兩者都已修；生成器亦已改走
  P4 的兩步到達 → 中途改目的地 → 完成，另加一張單走中斷，並在尾端清除過期 fixture。
  契約 fixture 由 61 增至 64，三個新狀態與 `InterruptionReason` 首次有解碼覆蓋。
- **`alembic check` 的 drift 由 11 項回到 baseline 9 項**（2026-10-06）：
  `recurring_rides.status` / `.frequency` 漏了 `SAEnum(length=)`，model 推導出的寬度
  （9 / 6）與 migration 建的 `VARCHAR(16)` 不符 → 每次都報 `modify_type`。已補
  `length=16`。這是專案自己的 `SAEnum` 規則，只是這兩處漏了。
- **乘客可自行開 dispute**（2026-10-07）。`POST /orders/{id}/disputes`，只限
  乘客本人、訂單 terminal、同一位乘客同一張單同時只准一個 open case；
  severity 由 server 按 category 決定，唔接受 party 自選 SLA。App 的
  「提出申訴」由「只顯示編號」變成真表格。測試：
  `tests/api/test_passenger_disputes.py` 6 條。
- **admin dispute 詳情顯示到達驗證證據**（2026-10-07）。`AdminDisputeDetailOut`
  加 `arrival_claimed_at` / `arrival_gps_distance_m` / `arrival_pin_attempts`，
  admin console 在關聯訂單存在時顯示。
- **`serve_and_probe.py` 接受 `--host`**（2026-10-07）。真機測試可直接
  `serve_and_probe.py --host 0.0.0.0`，唔再強制自己起 uvicorn。
- **premium / fixed-fare driver inbox**（2026-10-07）。司機 App 新增通知列表
  同 badge；後端用 Postgres durable row 做 source of truth，external push
  (WhatsApp/FCM) 保持範圍外。
- **fresh audit 收尾輪**（2026-10-07）。admin F1–F7 全部核實已修（tsc 0、
  vitest 94、build 0）；backend F2–F4 / F6–F7 收齊（`mark_explicit_commit`
  消除 redundant commit，`4a8b055`）；mobile F2 補 `FormatException` 子型態
  （`eb44777`）＋ regression test（`72756a4`，harness 160→161）。全套
  pytest 1283 / 0、Dart 95/0、contract 64/64 已複核。
- **admin TOTP setup-QR re-issue**（2026-10-07）。NEW-28 收檔：登入頁
  credentials 步驟加「遺失設定 QR？重新取得」（bilingual），call
  `POST /api/v1/admin/auth/totp/enrol` 重新攞 material 並 render QR；
  型別 `AdminEnrolmentReissue`、endpoint wrapper 加註；`LoginPage.test.tsx`
  新增 1 條 pin payload/render。admin vitest 93→94，build/tsc 全綠。

---

## 5. Push 狀態：❌ 本工具仍推送不到（token 未獲授權此 repo）— 已由用戶豁免

**已 commit，但推送不到。** 實測：`git push`（token 放 URL、`-c credential.helper=`）
回 **403** `Write access to repository not granted`；同一 token `GET /user` 回
`"login": "dannisonluk"`（帳號正確），但 `GET /repos/dannisonluk/realtaxihk` 回
**404** —— private repo 沒有權限就是回 404。

fine-grained PAT 是**逐個 repo 授權**的，所以「token 屬於 dannisonluk」與
「token 能存取 realtaxihk」是兩件獨立的事。**要你出手**：GitHub token 設定加
`dannisonluk/realtaxihk` + `Contents: Read and write`。

> **用戶指示：「你不需要 push，只需要 commit」。**
>
> **判「有無未推 commit」永遠用 `git rev-list --count origin/main..HEAD`。**
> 「遠端有進度」≠「這支工具推得到」—— 遠端之所以有進度，是經其他憑證推的。
> 另注意 `rev-list` 只證明**已 commit 的東西**的推送狀態，完全不講工作區；
> **`git status --short` 是另一條問題**，兩個都要跑。

### 5a. 可複用教訓（同類問題會再出現）

- **`git push` 在這部機器是無聲掛起**，不是網絡問題 —— `git-credential-manager.exe`
  在等互動輸入，而工具會 timeout 殺掉它，加上輸出被 pipe 緩衝所以一行都沒有 flush。
  `git ls-remote` 照樣成功，令人誤以為 remote 通。要推就用 repo 自己的 token +
  停用 credential helper。
- **不要用一個會成功的寫入來做權限探測。** `PUT /contents/<path>` 探完不止告訴你
  結果，還會真的建立檔案並 push 一個 commit。要探就用 `POST /git/blobs`（只產生
  dangling object），或 `curl -o /dev/null -D -` 淨讀 header。
- **缺少 `workflows` scope 時，push 會整個被拒**（不只是 workflow 檔）。不要猜哪個
  permission 缺失 —— 讀 response 的 `x-accepted-github-permissions` header，它會
  直接指名。

---

## 6. 文件地圖

文檔總索引在 **[`docs/README.md`](README.md)**。以下只列最常讀的：

| 文件 | 內容 | 維護方式 |
|---|---|---|
| **`docs/WORK_SUMMARY.md`**（本文件） | 總覽 + 索引 + 未做清單 | 手動更新 |
| `README.md`（repo 根） | 快速上手、endpoint 一覽、配置 | 跟功能更新 |
| **`docs/ARCHITECTURE.md`** | **起點**：一程車的完整流程、錢在哪裡被改動、不變式清單 | 跟設計更新 |
| **`docs/DEVELOPMENT.md`** | 分層規範、後端行為怪癖、lint gate、方法論教訓 | 跟規範更新 |
| **`docs/SECURITY.md`** | 安全模型 + 已驗證控制 + SEV 分級發現 + 加固路線圖 | 跟修復更新 |
| `docs/ADMIN_CONSOLE_DESIGN.md` | 後台九大模組設計 + 四級 RBAC | ✅ 大部分已實作 |
| `docs/IN_TRIP_REDESIGN.md` | in-trip + 預約重設計：狀態機、schema、API、$5 平台費 | ✅ 已實作（外部通知／付款除外） |
| `docs/DEPLOYMENT_REQUIREMENTS.md` · `docs/DEPLOY_TARGET_DECISION.md` | 部署需求清單 / 選型取捨 | 選定後少變 |
| **`docs/QA_TEST_ENVIRONMENT.md`** | **測試環境交接**：四個必改的環境變數、OTP 怎麼拿（**不會**出現在回應裡）、管理員怎麼建、三個客戶端各連哪個位址、**手機 App 首次登入的兩道牆**（§6）、12 條實際卡過的陷阱 | 跟設定更新 |
| `docs/LANDMARK_COORDINATES.md` | 地標落客座標 + 深圳灣口岸幾何分析 | 覆核清單 |
| `docs/REALTIME_POSITION_COST.md` | 實時位置每 tick 成本實測 + 5 項優化 | 已實測 |
| `docs/ADMIN_AUTH.md` · `docs/DEVELOPMENT.md` §4 | 管理員認證模型 / ruff 規則集 | 少變 |
| **`docs/archive/`** | **歷史快照**：審計報告、逐行審閱、UI 審查、工作日誌 | **不更新** |

> 計 route 數要讀 OpenAPI（`GET /openapi.json` 數 `paths` / operations），
> **不要 grep route decorator** —— 一個 `@router.get` 加 `@router.post` 疊加於
> 同一個 function 會漏數。

---

## 7. 沙盒／環境陷阱（本機特有，值得記住）

| 症狀 | 真因 | 解法 |
|---|---|---|
| `curl` 打 `127.0.0.1` 回 `502 upstream connect failed` | 沙盒 proxy 攔截，**即使該 port 根本沒有東西在聽** | 用 Python `urllib` + `ProxyHandler({})`；`NO_PROXY` 對 curl 不可靠 |
| **Dart 完全無法開啟 child process**（`where` / `git` / `adb` 全部一樣）→ `flutter --version`、`dart analyze`、`dart run` 全失敗 | Dart 在 Windows 用**具名管道**接 child 的 stdio，沙盒令 `CreatePipe`/`CreateFile` 回 `ERROR_PIPE_BUSY (231)`。**停用沙盒也不行**，是 host 限制；Python 用匿名管道所以正常 | `dart --packages=.dart_tool/package_config.json <script>`（繞過 dartdev）；靜態檢查用 `python mobile/tool/dart_check.py mobile` |
| **CLI 與 Gradle 兩條路都建不出 APK，只是撞牆的層數不同** | CLI：第一件事是跑 `git log` → spawn `git.exe` → 231，**根本還沒到 Gradle**。Gradle：`--no-version-check` 令它過得了 CLI 那一層，但 `:app:compileFlutterBuildDebug` 會叫 `flutter assemble`，而它必須 spawn kernel compiler（`frontend_server_aot.dart.snapshot`）與 native-assets hook（`dart compile kernel … objective_c/hook/build.dart`）→ 一樣 231 | **沒有任何本機路徑建得出 APK。** 但 `ERROR_PIPE_BUSY` 是**資源耗盡，不是政策拒絕** —— 同一條命令 2026-10-03 與 2026-10-04 00:44 都成功過（約 182 MB universal debug 包）。所以**失敗時先重試，不要當成壞了**。CI（Linux）沒這個問題，`flutter build apk --debug` 是唯一的閘 |
| **`FLUTTER_SUPPRESS_ANALYTICS=true` 會決定你看到的是診斷還是 crash report** | Windows 的分析路徑會跑 `cmd.exe /c ver`，而它是在工具**已經在收尾**時才跑 —— 於是它蓋掉真正的錯誤 | 設了它，`flutter assemble` 會直接說出真正失敗的兩個 process；不設，只看到 `flutter.bat finished with non-zero exit value 1` 加一個 `flutter_0N.log` |
| **加了 plugin 之後 `dart pub get` 不會令它進 APK** | Gradle 決定要編哪些 plugin 子專案，讀的是 `.flutter-plugins-dependencies`（gitignored），而**只有 `flutter pub get` 會寫它**；`dart pub get` 只更新 `pubspec.lock` | 兩個檔案都要看。症狀是 build 成功、執行時 plugin 不存在 —— 在最貴的地方才發現 |
| **自適應圖示（adaptive icon）用 108dp 畫布預覽看似無事，實機卻被裁到** | 看 108dp 畫布**不等於**看 launcher 視窗 —— launcher 只顯示中央 **72dp** 再套 circle／squircle 遮罩，外面 18dp 一律裁掉 | 預覽必須 **crop 中央 72/108 再套遮罩**，不可以整張 108dp 直接看。本專案實測：海報內文 bbox `x 0.104–0.865 / y 0.137–0.873`，四種遮罩（circle／squircle／rounded square／square）**0.00%** 內容被裁 |
| **換海報做圖示之後，兩個 in-app 資產就 1.6 MB** | 原圖是 **JPEG**，本身已經帶壓縮噪聲；用 PNG 存等於把那些噪聲「無損」保存下來 —— 兩邊都吃虧。實測 1024px：PNG-opt **904 KB** vs WebP q90 **92 KB** | in-app 用 **WebP**（本專案只有 Android，`ios/` 不存在，所以沒有相容性顧慮）。**launcher 圖示保留 PNG** —— 總共只有約 290 KB，而且是 OS 最先要解碼的東西；48px 用 lossy 會直接看到 artefact |
| **Gradle script 編譯 footer 的「N errors」會把警告一齊計入** | `ScriptCompilationException` 列出全部診斷（含 warning）再報總數 → 2 個真錯 + 1 個 `android { }` deprecation 會印成「3 errors」 | 看每行有沒有 `e:` 前綴，以及最終的 `BUILD SUCCESSFUL`／exit code，不要讀 footer 的數字 |
| Background server 無聲死 | Bash tool call 內 `cmd &` 隨 shell 退出被收割 | 用 `run_in_background=true` + `TaskStop` |
| 用 `conftest.ADMIN_ID` mint token 打 live server → 401 | 它是每個 test session 隨機 `uuid4()` | 讀真 DB：`docker exec realtaxi-db psql -U realtaxi -d realtaxihk -c "SELECT id FROM users WHERE role='ADMIN';"` |
| 要 login 但 OTP code 不在 response（SEC-02） | 刻意設計 | `ALLOW_DEV_OTP=true` + code `123456` |
| 本機完全沒有 `pg_dump` / `psql` / `createdb` | 只有 `realtaxi-db` container 裡面有 | `scripts/ops/db_backup.py --via auto` 自動 fallback 至 `docker exec` |
| `subprocess.run(cmd, shell=True)` 回 0 但 command 是失敗的 | Windows 用 `cmd.exe`，`;` 不是分隔符 | 明確 `subprocess.run(["sh","-c",cmd])` |
| 傳 Windows 路徑入 `sh -c` 會被吞掉反斜線 | `C:\Users\x` → `C:Usersx` | `.as_posix()` 傳正斜線 |
| 對檔案做 byte 級取代時，明明存在的字串卻「找不到」 | **worktree 是混合換行**：`.gitattributes` 寫 `* text=auto eol=lf`、index 全是 LF，但部分檔案實際仍是 CRLF（由 Windows 工具寫入）。git 在 add 時正規化，所以 `git status` **完全看不出來** | 取代前先偵測該檔的換行再轉換；`git ls-files --eol` 的 `w/crlf` 就是這些檔 |

---

## 8. 一頁看完

```
✅ 後端 115 paths / 129 ops / ruff+mypy 0 / 24 migrations 單一 head — 代碼層生產就緒
   ✅ pytest 全套 1283/0/0（2026-10-07 單一 process 實跑）· 見 §0
✅ 登入改為 email + 密碼；電話只解鎖 call車（`PHONE_NOT_VERIFIED`）；鎖定回 401
✅ auth 三面 rate limit + Cloudflare Turnstile（prod 缺密鑰拒啟動）+ 受限審查者帳號
✅ App 已接新登入流程：三個入口分開、電話只解鎖 call車、四條 Turnstile 門都帶 token
✅ APK debug build 已在本機成功（2026-10-07，約 183 MB `mobile/build/.../app-debug.apk`）
✅ 現時無未修項（先前的 mobile `fixed_offers_screen.dart` `$` escape 已隨 WIP 收斂）
✅ mobile 34 畫面 / 161 tests / 19,481 LOC        — 三角色完整
✅ mobile 排版閘（`dart format --set-exit-if-changed lib tool`）已修至 0 changed
   （HEAD/origin-main 原本 19 檔唔過 — 閘聲明咗但從未綠過）
✅ P4 首次對住「已 migrate 的 dev DB」跑過（原停在 `c1f2e3d4a5b6`，2026-10-06 升到
   `b8d1f2a3c4e5`）；契約 fixture 61 → 64，三個新狀態首次有解碼覆蓋
✅ `alembic check` drift 回到 baseline 9 項（修好 `recurring_rides` 漏 `length=` 那兩項）
✅ admin-web React / 94 vitest / tsc exit 0 / UI verifier PASS
✅ 後台治理：四級 RBAC（rank 比較、live row 為權威）+ 審計覆蓋金錢／狀態改動
✅ 後台新增：帳戶管理 / 訂單監控 / 結算預覽+confirm token+CSV / 爭議 / 主體搜尋 / 頭像上傳
✅ 後台實時地圖 `#/live`：Leaflet + OpenStreetMap（免金鑰、不計費）、15 秒輪詢；路由 code-split
✅ 修好一個真 bug：AppContext 讀 `user.role` 當 rank（實為 principal kind）
   → 有效登入下導覽 0 項、每頁「沒有存取權限」；rank 在 `admin_role`
✅ location check：8 個 polygon 取代 bbox（舊 bbox 含深圳）
✅ 深圳灣口岸：港方口岸區（香港租賃、司法管轄）納入境內；蛇口 / 南山仍境外
✅ analytics：HKT 分桶 + 24 時段 heat map
✅ 4 真 bug + 7 P0 + 10 P1 + 10 P2 全數處理 · SEC-01~31 全數處理
✅ money 精度：cent 儲存、wire 2dp、meter 1dp、ratio 統一入口（ROUND_HALF_UP）
✅ P1-4 備份 script + 還原演練實跑 PASS（27 tests）
✅ TOTP 對 RFC 6238 / 4226 全部 16 條官方向量 PASS（實測）
✅ `app/models` 拆包：886 行 → 5 個 bounded-context 模組，零呼叫點改動
✅ 全部 129 個 operation 都有 `response_model=`（audit script 實跑驗證）
⚠️ push 未做 — 用戶指示「只需 commit」（見 §5）
⬜ 真正等 credentials 的只有 3 家 provider：Google Maps / FCM / WhatsApp
✅ 主機名已定：`hkfastdc.com`（單一 origin，2026-10-04）— §4A
```
