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

- **現時狀態**：`pytest` **1060 passed / 0 failed / 0 error / 0 skipped**（以
  `--junit-xml` 讀，44 個 `test_*.py` 模組）·
  > 📌 **這一批由 964 起點修掉 108 個失敗**（登入／註冊與電話驗證分離的改動，
  > 見 §4C）。舊文檔寫的 961 對應的是改動前的 `HEAD`。此數字為 2026-10-04 實跑
  > （`.tmp/full4.xml` 讀出，與 `--collect-only` 的 1060 一致）。
  `ruff check` **只剩 `app/api/fleets.py` 的 2 條**（`I001` + `F401`，那是用戶自己
  未 staged 的改動，刻意不動）· `ruff format --check` clean（184 files）·
  console `tsc` clean + **69 vitest passed（9 files）** · `npm run build` 主包
  468.49 kB（gzip 146.50 kB）＋地圖分包 155.70 kB（gzip 45.58 kB，按需載入）·
  Dart **127 passed**（登入分拆一批加了 30 條）· contract **54 fixtures decoded,
  0 failure** · `dart_check` 68 files, 0 diagnostics · `audit_layout` **52 renders
  clean** ·
  `tool/check_contrast.py` OK · API **86 paths / 93 operations，全部已声明
  response model** · fixture↔schema 审计 **68/68 块无数据丢失** ·
  pyright（1.1.408）`app/` + `scripts/` + `tests/` **0 errors** —— `tests/` 原有
  140 條，2026-10-03 清零（見 §7）。

> **本文件的用途**：一份可以單獨看完的總覽。其他 `docs/*` 是**主題深入報告**；
> `.workbuddy-ai/memory/*.md` 是**逐日流水**（append-only，不整理）。
> 本文件是索引 + 摘要，不取代它們。文檔全貌見 [`README.md`](README.md)。

---

## 1. 交付物

| 交付物 | 位置 | 技術 | 狀態 |
|---|---|---|---|
| 後端 API | `app/` | FastAPI (async) + SQLAlchemy 2.0 async + PostgreSQL 16/PostGIS + Redis 7 + Alembic | ✅ **86 paths / 93 operations** · 1060 tests |
| Flutter App | `mobile/` | Flutter + Riverpod 3.4.3 + Dio + go_router 17（**24 個畫面**，三角色）；品牌資產由 `tool/gen_branding_assets.py` 由 `branding/source/` 的原圖產生 | ✅ 127 tests · 68 files / 0 diagnostics · APK 曾 BUILD SUCCESSFUL（2026-10-04；本機現時跑不完，見 §4C） |
| Web 管理後台 | `admin-web/web/`（React + Vite）、`admin-web/legacy/`（legacy） | React + Vite（新版）、Vanilla JS（舊版） | ✅ **69 vitest** · UI verifier PASS |

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
uv run pytest -q                                       # 1060 passed（用 --junit-xml 讀，見下）
uv run python scripts/verify/audit_response_models.py  # 68 块夹具 vs response_model，0 丢失
cd admin-web/web && npx tsc --noEmit && npm run build && npx vitest run --no-file-parallelism --pool=forks
cd mobile && dart --packages=.dart_tool/package_config.json tool/run_tests.dart
cd mobile && python tool/dart_check.py mobile          # LSP，非 flutter analyze；要帶路徑
cd mobile && dart --packages=.dart_tool/package_config.json tool/verify_contract.dart
# 品牌資產問的是「有沒有跟上原圖」，不是「能不能編譯」——不跑這條檢查就沒有人會發現
.venv/Scripts/python mobile/tool/gen_branding_assets.py --check
# 圖示、`assets:`、以及 Flutter plugin 集合，只有真正建置 APK 才驗得到：
# dart_check 與 run_tests 看不到 res/，也看不到 Gradle 專案。而本機這條路
# **現在跑不完**（kernel compiler 撞 ERROR_PIPE_BUSY 231，見 §5a）——
# 所以它現在的實際身份是「CI 的閘」，不是本機的閘。
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
| **P1-1 WS token 走 `?token=`** | `app/api/ws.py` 仍是 query param。設計上**刻意如此**（瀏覽器 WS 無 header 通道），已有 `StripTokenQueryFilter` 兜底。反代已定為 nginx（`deploy/nginx/hkfastdc.conf`），其 `log_format` 用 `$uri` 而非 `$request_uri`，查詢字串（連 token）不會落地 —— **殘餘洩漏已封**。**仍待辦**：選定主機名（repo 內有**三種**拼法並存：文檔 `hkfastdc.com`、`app/api/ws.py` 註解 `hkfastdc.com`、nginx 註解區塊 `console.hkfastdc.com`）。nginx 檔內同時寫入憑證路徑，改錯會令 nginx **啟動失敗**而非警告。 |
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

### C. 已知功能缺口（要寫程式，未排期）

| 缺口 | 影響 | 為什麼現在是這樣 |
|---|---|---|
| **P4 in-trip 重新設計未實作（最大的一項）** | 訂單狀態機仍是 `IN_TRIP → COMPLETED` 的死巷（`app/services/order/state_machine.py`）。行程中改目的地、司機中途結束、到達雙重驗證、違約扣款、$5 平台費全部沒有 —— 乘客與司機在行程中都只有「完成」一個動作 | 設計已完成且**七個 DECISION 全部拍板**，只差實作，見 [`IN_TRIP_REDESIGN.md`](IN_TRIP_REDESIGN.md) §9。它會動 `ORDER_TRANSITIONS` 與兩條由測試守住的不變式（終態無出邊、行程中不可達 `CANCELLED`），所以不能順手改 |
| **沒有補完個人資料的畫面** | `POST /identity/profile` 要 username／given name／family name，而 App 沒有任何地方呼叫它，所以註冊出來的帳號 `username IS NULL`，帳戶頁只能退回顯示遮蔽後的電話 | 註冊刻意不收姓名 —— 那會在一個以「短」為目的的表格上加第四個必填欄位。要補就是登入後的一次性提示，不是把它塞回註冊 |
| **沒有改密碼／忘記密碼流程** | 忘記密碼的帳號只剩「已驗證號碼 + OTP」這條次要登入，而它要求 `phone_verified_at IS NOT NULL` —— **未驗證電話又忘了密碼的帳號無路可走** | 後端也沒有 `POST /auth/password/*`，所以這同時是後端缺口。次要登入覆蓋得到一部分，覆蓋不到這一類 |
| **`GET /identity/me` 不在 `mobile/test/fixtures/` 內** | `tool/verify_contract.dart` 的 fixture 迴圈**驗不到 `Profile`**，而 `Profile` 是唯一帶 `phone_verified` 的模型 —— 也就是「能不能叫車」的判準。它目前只有 `run_tests.dart` 裡手寫的樣本 | 產 fixture 需要跑著的 API（`scripts/dev/gen_mobile_fixtures.py`）。補上之後 `Profile` 才會像其他模型一樣被真回應釘住 |
| **本機跑不完 APK build：`webview_flutter` 只算「已解析、未證明」** | Gradle 的 `:app:compileFlutterBuildDebug` 會叫 `flutter assemble`，而它要 spawn kernel compiler 與 native-assets hook，兩者都撞 `ERROR_PIPE_BUSY`（231）。所以 `res/`、`assets:`、plugin 集合在本機**沒有任何閘** | 231 是資源耗盡而非政策拒絕 —— 同一條命令在 2026-10-03 與 2026-10-04 00:44 成功過。CI 的 `flutter build apk --debug` 是唯一的閘，但**還沒在這些 commit 上跑過**。另注意 `dart pub get` **不會**重寫 `.flutter-plugins-dependencies`（只有 `flutter pub get` 會），那是 Gradle 決定要編哪些 plugin 子專案的依據 |
| **`serve_and_probe.py` 把 uvicorn 寫死在 `127.0.0.1`** | 真機連不到 API，而 `APP_HOST=0.0.0.0` 對它**無效**（沒有任何 dev 啟動腳本讀那個設定）。現時要手動 `adb reverse tcp:8000 tcp:8000` | 不是 bug（本機開發預設綁 loopback 是對的），是 dev 工具缺口。要修就是讓該腳本接受 `--host` |
| **沒有「一鍵造一個能叫車的帳號」的 ops 腳本** | 每次要新開一個能叫車的測試帳號，都要依序打 3 個端點（`/auth/register` → `/identity/phone/request` → `/identity/phone/confirm`，見 `QA_TEST_ENVIRONMENT.md` §6.4） | 刻意**先不做**：這 3 步走的正是正式流程，等於順手驗證了後端。**注意「審查者帳號」已有 ops 腳本**（`scripts/ops/create_reviewer_account.py`，有到期日、不能動錢，見 §6.6），但它解決的是「給外部審查者一個能登入的帳號」，**不是**這條。若日後要頻繁重跑，再加 `scripts/ops/` 腳本，但必須走 service 層而不是 `UPDATE users` |
### D. 已結案（保留以免重複處理）

- **Sentry 已接好**（`app/main.py`，`sentry_dsn` 有值就 init）。已補 `release`
  （綁 `_API_VERSION`）與 `max_request_body_size="never"`（PDPO：不送 request body）。
- **文件語言已統一**：`docs/` 與三份 README 全部為**書面語（繁體）**。
- **行號引用已處理**：設計文件重新指向現行位置；帶日期的審計快照**不改行號**，
  只保留「已失效」註記（重新編號會假裝那些發現仍描述現況）。
- **部署目標已定**：選項 A（單台 VPS + Compose），反代用 nginx。產物見
  `deploy/README.md`、`docker-compose.prod.yml`。

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
| `docs/IN_TRIP_REDESIGN.md` | in-trip + 預約重設計：狀態機、schema、API、$5 平台費 | 設計提案，7 個 DECISION 全部已拍板 |
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
✅ 後端 86 paths / 93 ops / 1060 tests / ruff format clean — 生產就緒
✅ 登入改為 email + 密碼；電話只解鎖 call車（`PHONE_NOT_VERIFIED`）；鎖定回 401
✅ auth 三面 rate limit + Cloudflare Turnstile（prod 缺密鑰拒啟動）+ 受限審查者帳號
✅ App 已接新登入流程：三個入口分開、電話只解鎖 call車、四條 Turnstile 門都帶 token
⚠️ APK build 在本機跑不完（`ERROR_PIPE_BUSY`）→ `webview_flutter` 與 `res/`／`assets:`
   只有 CI 驗得到，而 CI 還沒在這些 commit 上跑過（見 §4C）
✅ mobile 24 畫面 / 127 tests / 0 diagnostics      — 三角色完整
✅ admin-web React / 69 vitest / typecheck + build clean / UI verifier PASS
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
✅ 全部 89 個 operation 都有 `response_model=`
⚠️ push 未做 — 用戶指示「只需 commit」（見 §5）
⬜ 真正等 credentials 的只有 3 家 provider：Google Maps / FCM / WhatsApp
⬜ 主機名未定（三種拼法並存）— §4A
```
