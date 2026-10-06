# realtaxihk 上線前完整 Final Check 報告

**日期**：2026-10-06
**狀態**：**已過時快照（superseded）** —— 基準係 local `main @ ed7e89f`；內文數字（pytest 1219、vitest 81、mobile 153、反逆向項目）屬當日當時，之後已更新。現況以 [`AUDIT_2026-10-06.md`](AUDIT_2026-10-06.md) 為準。
**場景**：上線前檢查（Pre-launch Check）—— 執行/編譯、功能完整、代碼質量、安全、反逆向、UI/UX 六維度
**參與成員**：產品評審員 + 安全官 + QA 與發布 + 設計顧問 + 調查員（5 位全上）
**檢查基準**：local `main` @ `ed7e89f`（領先 `origin/main` **8 個未推 commit**）
**報告人**：主理人（Software Workshop CEO）彙編；關鍵主張已由主理人獨立覆核（見 §2 來源欄「⊕」標記）

---

## 📌 TL;DR（執行摘要）

- 整體結論：🟡 **條件 Go** —— 代碼層已達生產就緒，但**有 5 條阻塞項**未清，未清不能正式上線。
- **執行／編譯零報錯**：9 條 gate 全綠，pytest **1219 passed / 0 failed / 0 error / 0 skipped**，migration 漂移 = 9 項 baseline **無新增**。
- **後端安全成熟度極高**：實跑 79 條安全測試全過，**找不到阻塞級後端問題**；JWT／cookie／argon2id／OTP／IDOR／安全標頭全部到位。
- **最大系統性風險是「反逆向保護幾乎為零」**：release APK 無程式碼混淆、無憑證綁定 —— 而這是傳送 GPS + 手機號 + 長效 refresh token 的應用。
- **前端 i18n 名不副實**：`supportedLocales` 列了 `en`，但全 app **零英文資源**，錯誤訊息還中英混雜。
- 下一步：清 5 條阻塞項 → **跑一次 CI**（8 個未推 commit 從未上過 CI，release APK 從未建置）→ 可上線。

---

## 🎯 核心結論卡片

| 項目 | 內容 |
|------|------|
| **Go / No-Go** | 🟡 **條件 Go**（清完 §5 的 5 條阻塞項即可上線） |
| **嚴重度分布** | 🔴 5 / 🟠 4 / 🟡 11 / 🟢 6（共 26 條） |
| **關鍵行動項** | 13 條（P0 ×5、P1 ×6、P2 ×2） |
| **建議負責人** | 運維／部署 ×3、後端 ×2、Mobile ×3、前端 ×2、全員 ×1 |
| **可交付性** | 代碼 ✅ / 部署配置 ⚠️（後台未 mount、回滾未驗）/ 交付基準 ⚠️（未推 commit 未過 CI） |
| **回滾預案** | ⚠️ **目前不存在**，屬阻塞項（見 §5 B4） |

---

## 1. 各成員核心結論

### 🔍 產品評審員（功能完整性 + 可交付性）
- **核心判斷**：🟡 條件 Go。P4 狀態機、五個後端端點、四個前端畫面、密碼流程 API 與遷移**全部齊備且與設計文件一致**；部署骨架（Dockerfile／prod compose／nginx／certbot）也齊。但找到 **3 個用戶可觸及的功能斷點**：後台 console 未 mount（上線即 404）、網頁版重設密碼／驗證電郵頁面不存在（收信後斷路）、P4 通知未即時化。
- **關鍵建議**：交付基準必須先定 —— `origin/main` 落後 8 個 commit，而這 8 個 commit **從未在 CI 上跑過**；應先 push 並確認 CI 綠燈，再定交付 commit。

### 🛡️ 安全官（OWASP Top 10 + STRIDE + 反逆向）
- **核心判斷**：🟡 條件 Go，**後端無阻塞項**。實跑 79 條安全測試全過；JWT 演算法 pinned、refresh cookie `HttpOnly+Secure+SameSite=Strict+__Host-`、argon2id（m=64MiB 高於 OWASP）、OTP 雙閘+限流+單次、無 SQL 拼接、`/orders/{id}` 逐一驗 IDOR、`role` 與 `admin_role` 完全無混用（讀 live row 不信任 token claim）、安全標頭齊全。本地 `.env` **無法**把 prod 降級（base compose 硬編碼 `APP_ENV: prod`）。
- **關鍵建議**：唯一上線前缺口集中在 mobile 反逆向 —— ① release 開 R8 + `--obfuscate --split-debug-info` 並寫進 CI；② dio 加 SPKI 憑證綁定；③ manifest 設 `allowBackup="false"`。反逆向的**實際價值判斷為中等偏低**（核心資產在伺服器端），故混淆+pinning 值得做（成本低），root 檢測／Play Integrity 屬加分非阻塞。

### ✅ QA 與發布（執行 + 編譯 + 覆蓋）
- **核心判斷**：🟢 通過。9 條 gate **全部綠燈**：ruff check／ruff format／mypy（128 檔 0 issue）／pytest **1219-0-0-0**（708 秒）／admin-web typecheck + vitest **81 tests** + build／mobile dart_check **84 檔 0 diagnostics** + format + **153 斷言** + **64 fixtures**／alembic check **9 項 = baseline 無新增**。119 個端點**全部**至少被一條測試引用，無未覆蓋模組。
- **關鍵建議**：兩條「CI 觀測盲區」—— `alembic check` 冇入 CI（漂移要人手跑才睇到）；mypy 只掃 `app/`，`tests/` 與 `scripts/` 冇型別閘。另**更正**：本專案 CI **確實有** mypy job（先前認知有誤）。

### 🎨 設計顧問（UI/UX）
- **核心判斷**：🟡 條件通過。**React 管理台達生產級**（狀態色語意合理、四態齊、a11y 有焦點陷阱/`prefers-contrast`/44px target、響應式 860px 收合、i18n 有 key parity 測試）。Flutter 客戶端設計 token 高度集中（features 內零硬編碼顏色/字級），**行程中畫面（P4）是做得最好的一塊**（socket 斷線有 banner + 輪詢降級）。真實缺口在 i18n、定位引導、a11y。
- **關鍵建議**：i18n 一致性優先 —— 要麼引入 `.arb` 補英文，要麼從 `supportedLocales` 移除 `en` 以免誤導；並修正 `ErrorView` 的硬編碼英文（彈在純中文介面中間）。其次為定位權限被拒補「去設定」引導。

### 🔧 調查員（代碼質量 + 健康檢查）
- **核心判斷**：🟡 條件通過，**核心 runtime 全綠**。真 uvicorn boot 成功、`GET /health` → 200（db:true、redis:true）；API smoke 全綠；契約審計 `OK`（78 blocks、119 ops、97/101 schemas reachable）；migration parity 3 passed、enum check constraints 4 passed；fixture 三處同步 **64/64/64** 零 mismatch；建置產物**零污染**（`git ls-files` 無 `__pycache__`/`build`/`dist`）；13 處 TODO/NotImplementedError **全部合法**（抽象基底或刻意哨兵）；19 處 `except Exception` 全部有理由註釋；0 bare except、0 靜默吞錯。
- **關鍵建議**：唯一可直接修的真缺陷是**契約審計名冊雙向漂移**（漏檢 3 個存在 fixture + 1 個死項），它影響「response_model 契約審計」這條 CI gate 的可信度。另有 10 條後端端點無任何客戶端呼叫且未記入文件。

---

## 2. 綜合審查發現（去重合併後按嚴重度排序）

> 「來源」欄標記：產品 = 產品評審員｜安全 = 安全官｜QA = QA 與發布｜設計 = 設計顧問｜調查 = 調查員｜**⊕ = 主理人已獨立覆核確認**

### 🔴 阻塞項（5 條）

| # | 類別 | 位置 | 問題 | 建議 | 來源 |
|---|------|------|------|------|------|
| B1 | 部署 | `deploy/nginx/hkfastdc.conf` + `docker-compose.prod.yml` | nginx `location /console/` 的 `root /var/www`，但 prod compose 的 nginx volumes 只有 conf／letsencrypt／certbot-webroot／nginx-logs，**冇 mount console 靜態檔** → `up` 後 `/console/` 回 404。上線後 KYC 審批、爭議裁決、退款、車隊結算**全部無後台可用** | 在 prod compose 加 `./admin-web/web/dist:/var/www/console:ro`（或具名 volume），並在 deploy/README 寫明 build 步驟 | 產品 ⊕ |
| B2 | 功能缺失 | `app/services/auth/password_service.py:188`、`app/services/auth/identity_service.py:206` | 忘記密碼／電郵驗證的兩封郵件連結指向 `{PUBLIC_BASE_URL}/reset-password` 與 `/verify-email`，但**全 repo 冇呢兩個頁面**（無 HTML、admin-web 無 route、FastAPI 無 HTMLResponse/StaticFiles/Jinja2）→ 連結 404；App 亦冇手動輸入 token 的畫面 → 用戶收信後**無法完成重設或驗證** | 補兩頁靜態頁（可放 `admin-web/web/` 或獨立 static），或證明由 repo 外托管並補文件；否則應在 App 內提供 token 手動輸入路徑 | 產品 ⊕ |
| B3 | 功能缺失 | `app/api/orders.py`（五個 P4 端點） | `arrival-claim`／`arrival-confirm`／`interrupt`／`change-destination`／`dispute/resolve` **完全冇呼叫 `TripHub.publish()`**（全 app 唯一的 `.publish(` 在 `trip_service.py`，無 API route 觸達）。前端靠 **10 秒輪詢**（`app_config.dart:108 locationPollInterval = 10s`）→ 到達核對畫面最遲 10 秒才彈。`docs/IN_TRIP_REDESIGN.md:932` **明文**：「這令 P2 的『publish 從未被呼叫』由已知但無害的缺陷，變成**必須修的阻斷項**」 | 五個端點補 `TripHub.publish()`；若短期做不到，最低限度落實 §7 的臨時措施（`arrival-claim`／`interrupt` 後即時觸發對方端 `GET /orders/{id}`，且邊界處輪詢縮至 2 秒） | 產品 ⊕ |
| B4 | 運維 | `docker-compose.prod.yml`、`Dockerfile`、`.env.example` | prod 啟動硬依賴憑證未備：**Turnstile secret（缺則 prod 直接拒啟動）**、SMTP（密碼重設／電郵驗證無此則功能不可用）、`POSTGRES_PASSWORD`、`JWT_SECRET_KEY`。且**回滾 runbook 不存在**：compose 啟動即自動 `alembic upgrade head`，`alembic downgrade` 演練未做，遷移出事無程序可依 | 上線前備齊憑證並寫入部署 runbook；補一份回滾程序（含 downgrade 演練、DB 快照還原點、nginx 切回舊 build 的步驟） | 產品 + 安全 |
| B5 | 交付基準 | `.github/workflows/ci.yml`、git state | local `main` 領先 `origin/main` **8 個 commit**（147 files / +8945 / −746），**從未在 CI 上跑過**；`ci.yml` 的 mobile job 只跑 `flutter build apk --debug`，**release APK 從未建置過** → 交付物係邊個 commit 未定，且 release 路徑完全未驗 | 先 push 並確認 origin CI 綠燈，定交付 commit；另補一條 release build 閘（至少 `flutter build apk --release` 能過，keystore 用 CI secret） | 產品 + QA ⊕ |

### 🟠 高（4 條）

| # | 類別 | 位置 | 問題 | 建議 | 來源 |
|---|------|------|------|------|------|
| H1 | 反逆向 | `mobile/android/app/build.gradle.kts:116-125` | release buildType **完全無** `isMinifyEnabled`／`isShrinkResources`／ProGuard 規則（亦無任何 `.pro` 檔）；全 repo 無 `--obfuscate --split-debug-info`。後果：`blutter`／`strings`／`jadx` 可還原 Dart 符號、類名與**全部字串字面值** → API 路徑、錯誤訊息、客戶端業務常數（取消冷卻秒數、目的地變更上限=3、到達半徑=150m）全可讀。**另發現**：無 keystore 時 release 會 fallback 用 **debug 簽名** | 開 R8（minify+shrink）+ 固定 `--obfuscate --split-debug-info=build/symbols` 並寫進 CI／發佈腳本；release 缺 keystore 應**直接失敗**而非靜默用 debug 簽名 | 安全 ⊕ |
| H2 | 反逆向 | `mobile/lib/core/network/api_client.dart:26-51` | Dio **無** pinning、**無** `badCertificateCallback`、無 SPKI 校驗（僅一條 `badCertificate` 錯誤訊息字串）。此 app 傳 GPS + 手機號 + 長效 refresh token → 任何能安裝流氓 CA 的裝置可 MITM 讀取全部流量 | 加 SHA-256 SPKI pinning（或 `badCertificateCallback` 白名單），失敗即斷線；備妥憑證輪替流程 | 安全 ⊕ |
| H3 | i18n | `mobile/lib/app.dart:23-24`、`mobile/lib/features/shared/widgets.dart:135-163` | `supportedLocales` 列了 `Locale('en')`，但**全 app 零英文資源**（無 `.arb`、無 l10n 目錄、無 `AppLocalizations`），所有文案硬編碼中文 → 宣稱雙語實際單語。**反向問題**：`ErrorView` 的提示與「Try Again」按鈕、網絡錯誤句**硬編碼英文**，彈在純中文介面中間 | 要英文就引入 `flutter gen-l10n` + `.arb`；否則從 `supportedLocales` 移除 `en`。並統一 `ErrorView` 語言 | 設計 ⊕ |
| H4 | 定位 UX | `mobile/lib/core/location/location_service.dart:81`、`features/passenger/request_ride_screen.dart:103-105`、`features/driver/driver_jobs_screen.dart:55-57` | `openSettings()` **已定義但從未被呼叫**；權限 `deniedForever`／定位服務關閉時只彈 toast，無「去設定」按鈕。`ensureAccess()` 已分四種 `LocationAccess` 但 UI 無差別處理 → 用戶被永久卡住且無自助出路 | 依 `LocationAccess` 分支：`deniedForever`／`serviceDisabled` → 帶 action 的 SnackBar 呼叫 `openSettings()` | 設計 |

### 🟡 中（11 條）

| # | 類別 | 位置 | 問題 | 建議 | 來源 |
|---|------|------|------|------|------|
| M1 | 反逆向 | `mobile/android/app/src/main/AndroidManifest.xml:12-16` | `<application>` 未宣告 `android:allowBackup` → Android 預設 `true`。現時 token 走 `flutter_secure_storage`（Keystore 包金鑰不進備份），實際外洩風險有限；但預設值等於「未來任何寫進普通儲存的資料都會被備份走」 | 明確設 `android:allowBackup="false"` + `android:fullBackupContent` | 安全 ⊕ |
| M2 | 運維 | `docs/DEPLOYMENT_REQUIREMENTS.md`、`docs/SECURITY.md` | 文件自認「`alembic downgrade` 演練未測、回滾路徑未驗證」；compose 啟動即自動 upgrade head，無 rollback runbook（與 B4 同根） | 補回滾 runbook 並實地演練一次 downgrade | 產品 |
| M3 | 契約審計 | `scripts/verify/audit_response_models.py:95` 及 `MODEL_OF` | **雙向漂移**：① `auth_verify_new_user` 在 `MODEL_OF:95` 但該 fixture 已退役、磁碟不存在（`check_fixtures()` 用 `if not path.exists(): continue` 靜默跳過）→ 死項；② `identity_me`／`identity_profile`／`auth_register` **存在且 `verify_contract.dart` 有解碼器**，但唔在 `MODEL_OF` → 其 `response_model` 的 key 存活**未被審計**。根因：腳本 docstring 聲稱「walks manifest.json」，實際只走硬編碼名冊。**實跑 rc=0**（容忍漂移、唔會紅）→ CI 捉唔到 | 刪死項、補 3 個 entry，或改為由 `manifest.json` 驅動；並在腳本加「名冊 vs 磁碟」一致性斷言 | 調查 ⊕ |
| M4 | 功能缺口（未記錄） | `app/api/`（10 條端點） | 10 條後端端點冇任何 mobile/admin-web 呼叫，且 `docs/WORK_SUMMARY.md §4.C` **未列**：`POST /identity/email/request`、`POST /identity/email/confirm`、`POST /drivers/licence/uploads`、`GET/POST /drivers/licence/submissions`、`GET /drivers/licence/submissions/{id}`、`POST .../withdraw`、`POST/GET/PATCH /recurring-rides[/{id}]`、`GET /service-area/check`、`GET /service-area/bounds` | 確認是否刻意 backend-first；若否，記入 §4.C 或標為「待接 UI」，以免「聲稱已交付但 App 打唔出」 | 調查 |
| M5 | CI | `.github/workflows/ci.yml` | `alembic check`（autogenerate 漂移偵測）**冇入 CI**；只有 `test_migration_schema_parity.py` 做 schema parity → 漂移要人手跑才睇到 | 在 test job 加 `uv run alembic check`（配 baseline 白名單斷言） | QA ⊕ |
| M6 | CI | `pyproject.toml [tool.mypy] files=["app"]` | mypy **只掃 `app/`**；`tests/` 與 `scripts/` 冇型別閘（`scripts/` 內有 AST 守門腳本，改壞了冇人知） | 加一條擴展 job（mypy/pyright 覆蓋 tests + scripts），或明確接受此範圍並記錄 | QA ⊕ |
| M7 | a11y | `mobile/lib/features/shared/widgets.dart:389`（`DetailRow` 標籤 `SizedBox(width:132)`）及多處固定高度 Row | 系統大字體（`textScaleFactor`）下固定寬標籤會被截斷／溢出；全 app 無 `textScaler` 處理或 clamp | 標籤改 `minWidth` + `Flexible`，或對 >1.3 的縮放加保護 | 設計 |
| M8 | a11y | 全 `mobile/lib`（`Semantics` 僅用於 `BrandLogo`） | 自訂互動元件（狀態 chip、費率卡）無語意 label | 為非文字資訊元件補 `Semantics(label:)` | 設計 |
| M9 | 設計一致性 | `mobile/lib/features/shared/widgets.dart:297-312`（`StatusChip.order`） | 行程狀態**借用「金額」色**：`inTrip`/`destinationChanged`→`gain`（紅）、`completed`→`loss`（綠），與 admin 台語意相反（admin：in-trip=ok 綠、completed=neutral）→ 同一狀態在兩個介面顏色相反 | 為狀態定義獨立 tone token，別重用 `gain`/`loss` | 設計 |
| M10 | 前端 bug | `admin-web/web/src/pages/OrderDetailPage.tsx:164` | 用 `className="btn btn--small"`，但 CSS 只定義 `.btn--sm`（`styles.css:678`）→ 該「查看收據」按鈕失去小尺寸樣式 | 改 `btn--sm` | 設計 ⊕ |
| M11 | 互動一致性 | `admin-web/web/src/pages/FleetDetailPage.tsx:91` | 動錢的車隊結算執行用原生 `window.confirm`，其餘破壞性操作全用樣式化 `useConfirmDialog` | 改用 `useConfirmDialog(danger)` 統一 | 設計 |

### 🟢 低（6 條）

| # | 類別 | 位置 | 問題 | 建議 | 來源 |
|---|------|------|------|------|------|
| L1 | 配置 | `app/main.py:336-342` | CORS `allow_headers` 缺 `X-CSRF-Token`。文件化部署是同源（nginx 把 console 放同 host `/console/`），故**現行不是 bug**；但若日後 console 搬到獨立 origin，refresh 的 preflight 會靜默失敗（同源 `TestClient` 捉不到） | 補上 `"X-CSRF-Token"` | 安全 |
| L2 | 反逆向 | `mobile/` 全樹 | 無 root/jailbreak 檢測、無模擬器檢測、無反調試、無 Play Integrity／簽名校驗 | 視風險取捨（成本高、可繞過，屬加分項） | 安全 |
| L3 | Info | `admin-web/web/src/api/session.ts:27` | access token 存 `sessionStorage`（XSS 可讀）。屬**已文件化的刻意取捨**：refresh token 在 HttpOnly cookie、SECURITY.md 實測 XSS sink 為零、access token 15 分鐘 | 僅記錄，非缺陷 | 安全 |
| L4 | 死代碼 | `app/models/dispute.py:95` | `DisputeSource.PARTY_CANCELLED_IN_LOCK_WINDOW` 全庫唯一一行（除 DB CHECK migration 與設計文件提及），**無任何 producer 傳得入**。後台若做 source filter 揀佢永遠 0 行（不 crash） | 保留或移除（低優先） | 調查 |
| L5 | 清潔 | `deploy/nginx/hkfastdc.conf;C` | 遺留空目錄（未追蹤、空）—— 疑似 `;` 誤輸入的產物 | 清理 | 產品 |
| L6 | 設計 | `mobile/lib/features/passenger/request_ride_screen.dart:302`（`height*0.32`）、`zoom:13/15` | 少量散落魔法數字（地圖高度比例、縮放級別）。**建議非缺陷** | 可選：收進 `AppTheme` | 設計 |

---

## 3. 六維度逐項裁決（對應用戶六項要求）

| 用戶要求 | 裁決 | 依據 |
|---|---|---|
| **沒有任何執行或編譯報錯** | 🟢 **通過** | 9 條 gate 全綠；pytest 1219-0-0-0；ruff/mypy/dart_check/vitest/typecheck/build 全部 rc=0 |
| **沒有缺失或未完成的功能** | 🔴 **不通過** | 3 條用戶可觸及斷點（後台 404、密碼重設斷路、P4 通知未即時化）+ 10 條端點無客戶端且未記錄。13 處 TODO 全部合法（非缺口） |
| **代碼質量過關** | 🟢 **通過** | 0 bare except、0 靜默吞錯、建置產物零污染、19 處 `except Exception` 全有理由、fixture 三處同步零 mismatch。僅契約審計名冊漂移（M3）一條中級 |
| **安全考量符合要求** | 🟢 **後端通過** / 🟠 **Mobile 未達標** | 後端實跑 79 條安全測試全過、無阻塞項；mobile 反逆向 3 缺口（H1/H2/M1） |
| **具備防止被逆向的保護措施** | 🔴 **不通過** | **保護措施實質為零**：無混淆、無憑證綁定、無完整性校驗、`allowBackup` 未關。唯一做得好的是「無硬編碼機密」與 secure storage 用法 |
| **前端 UI/UX 設計良好** | 🟡 **管理台通過 / Mobile 有條件** | 管理台生產級；Mobile 設計 token 集中、P4 行程中畫面優秀，但 i18n 名不副實、定位引導缺失、a11y 覆蓋不足 |

---

## 4. 交付清單

**代碼變更**：本輪為純檢查，**未改動任何檔案**（`git status` 僅 `?? new/` 未碰）。

**驗證覆蓋（實跑數字）**

| 層 | Gate | 結果 |
|---|---|---|
| 後端 | `ruff check app/ tests/ scripts/ alembic/` | ✅ All checks passed |
| 後端 | `ruff format --check .` | ✅ 219 files already formatted |
| 後端 | `mypy app` | ✅ 128 files, 0 issues |
| 後端 | `pytest tests/` | ✅ **1219 passed / 0 failed / 0 error / 0 skipped**（708s） |
| 後端 | `alembic check` | ✅ 9 項 = baseline（無新增漂移） |
| 後端 | 契約審計 + parity + enum 約束 | ✅ OK（78 blocks）/ 3 passed / 4 passed |
| 後端 | runtime | ✅ `/health` 200（db+redis 接得上）、API smoke 全綠 |
| 安全 | 3 條安全測試檔 | ✅ **79 passed** |
| 管理台 | typecheck + vitest + build | ✅ rc=0 / **81 tests** / built 4.25s |
| Mobile | dart_check / format / run_tests / verify_contract | ✅ 84 檔 0 diagnostics / 0 changed / **153 斷言** / **64 fixtures** |

**發布檢查清單（上線前必做）**
1. 清 §5 的 5 條 P0
2. `git push` 並確認 origin CI 四 job 全綠
3. 補一條 release APK build 閘（用 CI secret 的 keystore）並跑一次
4. 備齊 prod 憑證（Turnstile secret／SMTP／POSTGRES_PASSWORD／JWT_SECRET_KEY）並在 staging 做一次完整 boot
5. 在 staging 實跑一次 `alembic downgrade` 與還原，把步驟寫成 runbook

**回滾預案（⚠️ 目前不存在，需建立）**
- 建議三段式：① **DB**：升級前 pg_dump 快照 + 明確的 `alembic downgrade <rev>` 目標（本專案單 head `b8d1f2a3c4e5`，前一版 `c1f2e3d4a5b6`）；② **應用**：保留上一個 image tag，compose 切回即可（**注意**：compose 啟動會自動 `upgrade head`，回滾時必須同時鎖住 migration 或先 downgrade）；③ **Console**：保留上一份 `dist/` 於 nginx volume，切換目錄即可。
- 觸發條件建議：`/health` 連續失敗、5xx 率超閾、migration 後 schema parity 測試紅。

---

## ✅ 行動清單

| # | 行動 | 負責方 | 緊急度 |
|---|------|--------|--------|
| 1 | prod compose 加 console 靜態 volume（`admin-web/web/dist → /var/www/console`） | 運維 | **P0** |
| 2 | 補 `/reset-password`、`/verify-email` 兩頁（或證明外部托管並補文件） | 後端 + 前端 | **P0** |
| 3 | 五個 P4 端點補 `TripHub.publish()`（或落實 §7 的 2 秒邊界輪詢 + 即時觸發） | 後端 | **P0** |
| 4 | 備齊 prod 憑證並建立回滾 runbook（含 downgrade 演練） | 運維 | **P0** |
| 5 | push 8 個 commit → 確認 origin CI 全綠 → 定交付 commit；補 release APK build 閘 | 全員 | **P0** |
| 6 | release 開 R8 + `--obfuscate --split-debug-info` 並寫進 CI；缺 keystore 應失敗 | Mobile | P1 |
| 7 | dio 加 SPKI 憑證綁定 | Mobile | P1 |
| 8 | `AndroidManifest` 設 `allowBackup="false"` | Mobile | P1 |
| 9 | 修契約審計名冊雙向漂移（M3）—— 影響一條 CI gate 的可信度 | 後端 | P1 |
| 10 | i18n 決策：補 `.arb` 英文 **或** 移除 `en`；修 `ErrorView` 硬編碼英文 | 前端 | P1 |
| 11 | 定位權限被拒補「去設定」引導（呼叫已存在的 `openSettings()`） | Mobile | P1 |
| 12 | CI 加 `alembic check`；mypy 擴展至 tests/scripts（或明確接受） | 後端 | P2 |
| 13 | 修 `btn--small`→`btn--sm`；`FleetDetailPage` 改用 `useConfirmDialog`；清理空目錄 | 前端 | P2 |

---

## ⚠️ 待完善 / 已知局限

- **本報告未改動任何 repo 檔案**；`deliverables/` 不在 `.gitignore`，會以未追蹤狀態出現在 `git status`，請自行決定是否納入版控。
- **無法驗證的項目（環境限制）**：
  - `flutter analyze` 與 `flutter build apk --debug` 在本機沙盒跑唔到（`ERROR_PIPE_BUSY 231`），已用 `tool/dart_check.py`（同一引擎）替代 → 0 diagnostics；真 analyzer 與 APK 建置**只有 CI 驗得到，而 CI 未跑**。
  - **release APK 從未建置過** → 混淆、簽名、`res/`、plugin 集合全部未驗。
  - 反逆向結論為**靜態代碼審查**，未實際反編譯 APK 驗證（無真機／模擬器）。
  - 對比度實測（WCAG AA）與大字體爆版需真機；`ColorScheme.fromSeed` 動態生成無法純靜態量測。
  - 依賴 CVE 未跑掃描器（版本皆近期，未見明顯過期套件）。
  - 三家第三方 provider（Turnstile／Maps／SMTP）的真實申請與計費狀態不在 repo，無法核。
- **文件已記錄、屬刻意範圍外（非缺陷）**：乘客自行開 dispute 端點未做；乘客違約罰款未實收（只寫 `PENALTY_CHARGED` + 冷靜期）；預約服務（`SCHEDULED`）完全未實作；analytics 新指標未加（但 `INTERRUPTED` **未**污染現有統計，此點安全）。
- **一項認知更正**：本專案 CI **確實有** mypy job（先前專案筆記「CI 冇型別檢查器」有誤，已更正）；真正盲區是 mypy 只掃 `app/`。

---

## 📚 成員產出索引

| 成員 | 覆蓋範圍 | 核心產出 |
|------|---------|---------|
| `gstack-product-reviewer`（產品評審員） | 設計文件 vs 實作、可交付性、Go/No-Go | 設計要求落差清單（B1–B5、M2、M4）、P0/P1/P2 上線差距清單 |
| `gstack-security-officer`（安全官） | OWASP Top 10 + STRIDE + 反逆向 | 實跑 79 條安全測試全過；反逆向專節（H1/H2/M1/L1/L2/L3）；STRIDE 六層結論 |
| `gstack-qa-lead`（QA 與發布） | 執行、編譯、測試覆蓋、CI 閘 | 9 條 gate 逐條數字；pytest 1219-0-0-0；alembic baseline 9 項；CI 覆蓋更正（M5/M6） |
| `gstack-designer`（設計顧問） | Flutter + React UI/UX | 狀態完備性表（畫面 × 四態）、i18n 漏抽清單（H3）、a11y 與一致性（M7–M11） |
| `gstack-investigator`（調查員） | 代碼質量、死代碼、文件漂移、runtime | runtime/契約/parity 全綠；契約名冊漂移（M3）；10 條無客戶端端點（M4）；死 enum（L4） |

---

> 本報告由軟件工坊 AI 協作生成（主理人彙編 + 5 位成員獨立查證 + 主理人對全部 🔴 及關鍵 🟠 主張的獨立覆核）。關鍵決策請由工程負責人覆核。
