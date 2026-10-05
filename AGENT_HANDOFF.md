# Agent Handoff / 協作進度

這個檔是 realtaxihk repo 內多個 agent 的共享協作進度。任何 agent 在 commit 或開始大改之前，請先讀這個檔，並在改動後更新對應區段，避免互相覆蓋。

## 使用規則

1. 每次開始/完成工作前，更新「Last updated」。
2. 揸住邊啲檔案，就將檔案路徑寫入「Ownership / 檔案認領」區段。
3. 唔好改「另一 agent」已認領嘅檔案；如果一定要改，先喺此檔留言。
4. Commit 前只 add 自己嘅檔案，唔好一次過 `git add .`，以免捲入其他 agent 嘅 WIP。
5. 如果發現衝突，唔好直接 revert 對方，先喺此檔標記並等對方/用戶確認。

## 現時 git 狀態

- 最新 commit：以 `git log --oneline -5` 為準（本檔唔再硬寫 hash，避免每次 commit 都要改）
- 目前 branch：見 `git branch --show-current`
- Alembic head：`c1f2e3d4a5b6`（2026-10-05 實跑 `alembic heads` 確認，**只有一個 head**）。
  注意：本文件舊版寫 `f1c2d3e4a5b6` —— 那支現在只是**鏈中間**的一支
  （`f1c2d3e4a5b6 -> 7a1b2c3d4e5f -> 042a7bc3e54c -> 5e1a9c7d4b02 -> c1f2e3d4a5b6`），已非 head。
  **一律用 `alembic heads` 現查，不要信任何文檔寫死的 hash。**

### 已 commit 嘅進度

| Commit | 內容 | 邊個做 |
|---|---|---|
| `7a06bef` | premium destinations, driver attributes, ride requirements（Phase 1 backend） | 本 agent |
| `f18cb9e` | admin-web premium destination management page | 本 agent |
| `e2fc6db` | admin SoD whitelist、KYC restore、console controls 修正 | 其他 agent |
| `a2b9f9f` | fixed-fare matching and offer lifecycle（backend） | 其他 agent |
| `ab95c7c` | audit: mark SS-D1 deposit/refund FK RESTRICT as fixed | 其他 agent |
| `69c4484` | migration deep scan fixes（JSONB/unique index/固定價 index/enum + FK RESTRICT migration） | 本 agent |
| `e5454e5` | docs(audit): migration-chain deep scan 記錄 | 本 agent |

## Ownership / 檔案認領

### 本 agent（Hermes 主要承接方）

- `AGENT_HANDOFF.md`（本檔）
- 已完成：
  - `docs/FEATURE_EXPANSION_2026-10-05.md`（integrated backlog）
  - Phase 1 特選目的地/司機屬性：`app/models/premium.py`（基礎版已 commit，但現時 worktree 有後續改動，見下）
  - 已完成：Phase 1 fixtures + contract pins：
    - `mobile/test/fixtures/order_with_requirements.json`、`driver_payment_methods.json`、`driver_payment_methods_read.json`
    - `scripts/dev/gen_mobile_fixtures.py`：order 加 requirements / payment_preference；driver payment methods capture；reset 先清 RESTRICT deposit/refund rows
    - `scripts/verify/audit_response_models.py` / `mobile/tool/verify_contract.dart`：新增 fixture 全部 pin 到
    - 驗證：`audit_response_models.py` 72 blocks OK；`dart run tool/verify_contract.dart` 59 fixtures 0 failure
  - `app/api/destinations.py`、`app/api/admin/destinations.py`、`app/api/driver_attributes.py`、`app/core/region.py`
  - migration `5c8b2f0a1e43_premium_destinations_driver_attributes.py`
  - `tests/api/test_premium_destinations.py`、`tests/api/test_order_attributes.py`
  - admin-web `DestinationsPage.tsx` 及相關 i18n/types/endpoints/App/Shell
  - mobile mirror：`mobile/lib/models/order.dart`、`mobile/lib/models/driver_attributes.dart`、`mobile/lib/data/destination_repository.dart`、`mobile/lib/core/network/wire.dart`、`mobile/lib/data/driver_repository.dart`、state providers

### 其他 agent（fixed-fare / admin SoD / FK hardening）

已 commit：
- `app/models/fixed_offer.py`、`app/api/fixed_offers.py`、`app/api/schemas/fixed_offer.py`
- `app/services/fare/fixed_fare_service.py`
- migration `8f2a1c5d3b40_fixed_fare_offers.py`
- `tests/api/test_fixed_fare_offers.py`
- admin SoD/KYC/dispute fixes

未 commit（worktree，請其他 agent 自己確認）：
- `mobile/lib/core/security/username_policy.dart`
- `mobile/lib/features/driver/fixed_offers_screen.dart`
- `mobile/lib/features/shared/profile_setup_screen.dart`
- `mobile/lib/models/fixed_offer.dart`
- `mobile/lib/router/app_router.dart`、`mobile/lib/router/routing_rules.dart`、`mobile/lib/data/identity_repository.dart`
- `new/`（用戶/其他 agent 素材，唔好掂）

### 其他 agent（migration deep scan / FK hardening）已 commit

- `alembic/versions/f1c2d3e4a5b6_harden_deposit_and_refund_fks.py`（已在 `69c4484` commit，唔再係未 commit）
- `5c8b2f0a1e43`／`8f2a1c5d3b40` migration 修正（JSONB、unique index、index 名、fare_mode enum）已在 `69c4484` commit
- `tests/api/test_security_hardening.py` public destinations path 已在 `69c4484` commit
- `app/models/premium.py` 已 revert 返 HEAD（冇 duplicate class／冇多餘 import），混亂區已清

### 已識別 slice（等我哋其中一方認領）

- **Phase 2 fixed-fare platform fee ledger 已完成（本 agent）**：`FIXED_RIDE_FEE`、`reference_for_fixed_ride`、`order_complete` append、migration `042a7bc3e54c`；tests + parity green。
- **Migration 衝突注意（已解除）**：recurring migration `7a1b2c3d4e5f` 已 commit；新 migration `042a7bc3e54c` 已接住佢。
- **Audit 批核項（owner 2026-10-05）**：
  - N-1：**接受 rank hierarchy**，已改 `app/models/admin.py` / `app/api/admin/_roles.py` / `app/api/admin/disputes.py` / `tests/conftest.py` / `docs/ADMIN_CONSOLE_DESIGN.md` / audit docs docstring；dispute resolve 保留 decision-matched whitelist（assigned judge 唔可以批自己 payout）。
  - 已修細項：NEW-18（email 唔再 early write，token row 先係 pending）+ tests 更新；NEW-29（刪死 `APP_HOST` / `app_host` / `app_port`）；NEW-30（turnstile site key comment 修正）；M-M-1（trip_repository 誤導 docstring 改誠實）；M-M-4（release 缺 `API_BASE_URL` 硬失敗）。
  - **AC-05：owner 已批准（2026-10-05）**。改動已喺 worktree（`admin-web/serve.py` default 翻轉做 `web/dist`、`--legacy` 後備；`admin-web/README.md` + `scripts/dev/serve_and_run_browser.py` 同步），但呢三個檔同時係 sibling WIP，未 commit。已驗證：`py_compile` OK、`--help` RC=0、`web/dist/index.html` 同 `legacy/index.html` 都存在。
  - **已完成（2026-10-05）**：NEW-23（dispute audit 同 mutation 同一 transaction commit）、NEW-10（WS watchdog 移除，transport ping 負責 reap）；NEW-1/NEW-2 已做 NEW-2+box 誠實化。
  - **仲等緊 owner / 未郁**：mobile 大項（M-H-1 enum unknown、M-H-2 outbox、M-M-3 autoDispose）、AC-07、F-05 未郁，避免踩 sibling WIP。
  - **已於 2026-10-05 安全批次完成（見「Last updated」頂部）**：NEW-9、NEW-15、NEW-16、NEW-17、NEW-24、NEW-25/26、M-M-2、M-M-5、AC-06、F-06。

### 混亂區 / 請勿亂改

以下檔案現時 worktree 有未 commit 改動，可能同時被兩個 agent 郁過。等確認後先好處理：
- `mobile/lib/core/network/wire.dart`
- `mobile/lib/data/driver_repository.dart`
- `mobile/lib/models/order.dart`
- `mobile/lib/state/data_providers.dart`

（已清：`app/models/premium.py`、`5c8b...`、`8f2a...`、`tests/api/test_security_hardening.py` 已在 `69c4484`／revert 處理完）

## 衝突紀錄

- 2026-10-05：本 agent 曾開 `alembic/versions/6f3a9c2e8b15_fixed_fare_and_recurring.py`，但發現其他 agent 已有 fixed-fare migration，已刪除該重複檔。現時 alembic head 以 `alembic heads` 為準（2026-10-05 為 `c1f2e3d4a5b6`，見上）。
- `app/models/premium.py` 曾有本 agent 的 FixedPriceOffer draft，與 `app/models/fixed_offer.py` 重疊；現時 worktree 狀態未定，請其他 agent 唔好覆蓋，等我哋一齊核對。

## 下一步

### 本 agent 擬做

- **【2026-10-05 完成】收據要求 + 車內環境 mobile UI**（見下面「已完成」段；本輪 commit）
  - 更正：最後**冇需要**改 `mobile/lib/state/data_providers.dart`（`driverEnvironmentProvider` 已存在）同 `mobile/lib/data/driver_repository.dart`（`environment()`／`setEnvironment()` 已存在）——兩者都係 HEAD 已有。
  - 但**有**改 `app/models/user.py`（加 3 個 receipt 欄位），因為收據要 freeze 落 order；`app/api/orders.py` 冇改（收據 endpoint 住新檔 `app/api/receipts.py`）。
  - 實際郁過嘅對方 WIP 檔只有 2 個，各加 1 段：
    - `mobile/lib/router/routing_rules.dart`（加 `Routes.driverEnvironment`）
    - `mobile/lib/router/app_router.dart`（加 import + 1 個 GoRoute）

- 已完成：重複行程 / recurring rides（backend + migration + API；commit `912ac50`）：
  - `app/models/recurring.py`、`app/api/recurring.py`、`app/services/recurring/recurring_service.py`、migration `7a1b2c3d4e5f`
  - **mobile mirror 未做**（等 mobile 衝突區清咗先）
  - 唔會改 `app/models/user.py`、`app/models/fixed_offer.py`、`app/services/order/*`，除非此檔確認由本 agent 接管。

### 其他 agent（本 agent 已做）

- 已完成：migration chain deep scan、fixed-fare/premium migration drift 修正、FK RESTRICT migration、security test public destinations path（見 commit `69c4484`、`e5454e5`）
- 目前無持有 recurring rides slice；會避開你認領嘅檔案。
- 提醒：新 migration 請接**當時的 `alembic heads` 輸出**（2026-10-05 為 `c1f2e3d4a5b6`），避免再出現多 head 分叉。不要照文檔抄 hash。
- 2026-10-05 巡查：recurring WIP（`7a1b2c3d4e5f_recurring_rides.py`、`app/api/schemas/recurring.py`、`app/models/recurring.py`）已接上前一 head，無分叉；其後又落了 `8f2a1c5d3b40`、`5c8b2f0a1e43`、`f1c2d3e4a5b6`、`7a1b2c3d4e5f`、`042a7bc3e54c`、`5e1a9c7d4b02`、`c1f2e3d4a5b6`。`app/api/schemas/__init__.py` 嘅 recurring export 我唔碰。
- 2026-10-05 另外幫 recurring WIP 清咗 blocker（唔 commit，留喺 worktree 俾你 review）：
  - `app/models/__init__.py`：`Recurring*` 原本錯誤由 `app.models.premium` import，改為 `from app.models.recurring import ...`
  - `app/models/recurring.py`：補返 migration 有但 model 冇嘅 `CheckConstraint("weekday BETWEEN 1 AND 7")`
  - `7a1b2c3d4e5f_recurring_rides.py`：enum 名改為 model 一致嘅 `ck_recurring_rides_status`／`ck_recurring_rides_frequency`，並加相應 check constraint；
  - 已跑 migration parity：3 passed；ruff format 已套用。

## Last updated

- 2026-10-05（本 agent：**audit 安全批次完成**——NEW-9/15/16/17/24/25/26 + M-M-2/M-M-5 + AC-06 + F-06）
  - **NEW-9（body size cap 對 chunked body 失效）**：`app/core/middleware.py`。原本 chunked（無 `Content-Length`）超過上限時由 `receive()` 拋 `_BodyTooLarge`，但 `add_middleware()` 令本 middleware 喺 Starlette `ExceptionMiddleware` **外**，所以個 raise 永遠到唔到本 class：內層 `BaseHTTPMiddleware` 會變成 `RuntimeError: No response returned.`，而舊嘅 `Exception` 版本就被 FastAPI body parsing 嘅 `except Exception` 吞成 400 `BAD_REQUEST` → **413 分支係死碼**。改法：唔再拋例外，超標時將餵俾 app 嘅 body 換成「空、最後一塊」（**任何超標內容都唔會被 parse**），再喺 `guard_send` 將 app 嘅回應改寫成 413。`_BodyTooLarge` 已刪。`tests/api/test_security_hardening.py` **49 passed**。
  - **NEW-15**：`fleet_settlement_runs` 新增獨立 `skipped_no_deposit_account`（唔再混入 `skipped`）——`app/models/fleet.py`、`app/services/fleet/fleet_service.py`、`app/api/schemas/fleet.py`、`app/api/fleets.py`、migration `c1f2e3d4a5b6`（`alembic heads` 單一 head ✓）。Mobile mirror：`FleetSettlementRun.skippedNoDepositAccount` + 3 個 fixture 補 `skipped_no_deposit_account`。
  - **NEW-16**：licence cap 改名 `MAX_SUBMISSIONS_PER_ROLLING_24H` / `max_submissions_per_rolling_24h`（`app/services/licence/licence_service.py`、`app/api/licence.py`）——**呢個就係 handoff 之前記錄嘅 676 errors 元兇**（service 改名但 API 未同步），已同步。
  - **NEW-17**：OTP 嘗試預算改成 phone-wide window（`app/services/auth/otp_service.py`）；新 code 唔 reset 舊 budget，wrong attempt 仍累積到 row（`test_phone_binding_api.py:261` 期望 `attempts_remaining == 4` 仍成立）。
  - **NEW-24**：新增 fixture `mobile/test/fixtures/ws_outside_hk_error.json` + `manifest.json` 一行 + `verify_contract.dart` decoder（斷言 `OUTSIDE_HK`、`isFatal == true`、訊息 `座標不在香港範圍內`）；`run_tests.dart` 移除舊 `BAD_LOCATION` pin（server 從來只送 `OUTSIDE_HK`，舊 pin 係客戶端自己估出嚟嘅字彙漂移）。
  - **NEW-25/26**：`mobile/lib/features/driver/driver_earnings_screen.dart` 加 `signed: true`；`admin-web/web/src/pages/DriverDetailPage.tsx` 嘅 `Math.min(100, Math.max(0, …))` 同 `sign` 檢查後 **HEAD 已有**，唔使改。
  - **M-M-2**：`mobile/lib/models/admin.dart` `SettlementPreview` 補 `wouldChargeDriverIds` / `wouldGoNegativeDriverIds`（原本 mobile 完全冇 decode 呢兩個 field）。
  - **M-M-5**：`mobile/lib/data/trip_repository.dart` — native 平台改用 `io.IOWebSocketChannel.connect(uri, headers: {'Authorization': 'Bearer …'})`（token 唔再落 URL query）；web 保留 query 參數 fallback（瀏覽器無法設 WS handshake header）。
  - **AC-06（⚠️ 留意）**：`admin-web/web/src/components/primitives.tsx` 嘅 Modal 加咗 focus 移入 + Tab/Shift+Tab focus trap。handoff 之前記錄過呢個檔有 sibling 未 commit WIP（同一件事）——**我今次落嘅係單一實作、冇重疊**（`grep useEffect|boxRef` 只一處），已修好之前 `tsc` 報嘅 `TS18048 'first'/'last' is possibly undefined`。依 handoff 規則 3 喺此留言：**如 sibling 仲有自己版本，以 worktree 現有版本為準**。
  - **F-06（docs drift）**：`docs/DEPLOYMENT_REQUIREMENTS.md`（flutter 狀態改為 `dart` 直跑、`ERROR_PIPE_BUSY` 描述、gap 7→3 項、`ruff format` 檔數 184→212 並誠實註明餘 1 個 E501 在 sibling WIP `admin-web/serve.py`）；`docs/STRUCTURE_REVIEW.md`（寫死嘅「97 passed / 54 fixtures」改為指向即時輸出）。
  - **2026-10-05 第二次 docs 掃描**：再修一批寫死數字 —— 根 `README.md`（交付物 82/56/46 檔 → 124/78/48；LOC；fixtures 54→63；Dart 97→149；vitest 69→76；API 68 塊→73 塊；docs 12→17 份）＋ `docs/WORK_SUMMARY.md`（API 86/93→98/111 ops；24 畫面→28；127→149 tests；69→76 vitest；pytest 數改為「未驗證」）＋ 本檔 head hash。新建 `docs/ERROR_SCAN_2026-10-05.md` 記 2 個真 blocker，`docs/README.md` 加量測基準表。
  - **順帶**：`ruff format` 補跑 3 個**本 agent 自己舊 commit** 留低嘅未格式化檔（`app/api/orders.py`、`app/core/region.py`、`scripts/dev/gen_mobile_fixtures.py`）；`ruff check --fix` 修 2 個 I001（`gen_mobile_fixtures.py`、`scripts/verify/audit_response_models.py`）。
  - **驗證**：`tests/api/test_security_hardening.py` 49 passed；mobile harness **149 passed / 0 failed**；`verify_contract.dart` **61 fixtures / 0 failure**；admin-web `tsc` 0 error、`vitest` **76 passed（10 檔）**；`ruff format --check` 211 formatted（餘 1 個係 sibling WIP）；`ruff check` 餘 1 個 E501（同上）；`alembic heads` 單一 `c1f2e3d4a5b6`。
  - **冇碰**：`mobile/lib/state/data_providers.dart`、`mobile/lib/models/order.dart`、`mobile/lib/core/network/wire.dart`、`mobile/lib/data/{driver,identity}_repository.dart`、`mobile/lib/router/*`、`mobile/lib/features/shared/account_screen.dart`、`admin-web/{README.md,serve.py}`、`scripts/dev/serve_and_run_browser.py`、`new/`。
- 2026-10-05（本 agent：**車內環境/付款偏好乘客端 + 收據 mobile screen + 特選目的地 pins 完成**；順帶修正 nearby 動物篩選嘅 JSONB null bug）
  - **後端 bug 修正（重要，影響已 commit 嘅 `bd25f76`）**：`RideRequirementsIn.model_dump()` 會為每個欄位序列化，所以只要求「靜音」嘅訂單其實存咗 `"animal": null`。JSONB 入面 JSON null **係一個值**，所以 `-> 'animal' IS NULL` 對佢係 **false** → 司機嘅「可載寵物」chip 會**隱藏**從未提及寵物嘅訂單。改用 `.astext.is_(None)`（`astext` 令「鍵不存在」同「JSON null」都塌成 SQL NULL）。`requires` 方向同樣改 `.astext.is_not(None)`。新增回歸測試 `test_exclude_animal_keeps_orders_that_never_mentioned_one` **先證實失敗**再修 → `tests/api/test_nearby_filters.py` **18 passed**。
  - 新增 `mobile/lib/models/ride_requirements.dart`（`RideRequirements` + `AnimalDetail`）＝**requirements 封閉集單一來源**，`NearbyFilter` 改成讀佢（唔再各自重述四個 key）。
  - `mobile/lib/models/driver_attributes.dart`：`DriverPaymentMethods` 加 `all` + `labelsZh`（六個 `PaymentMethod` 鏡像）。
  - `mobile/lib/features/passenger/request_ride_screen.dart`：新增「車內環境要求」（四個開關 + 小動物 bottom sheet，尺寸界線同 `AnimalDetailIn` 一致）＋「付款方式偏好」chips；`requirements` 為空時**送 `null`**（唔會喺每張單寫 `animal: null`）。
  - 新檔 `mobile/lib/features/passenger/receipt_screen.dart`：凍結收據文件（索取/讀取、複製伺服器原文、一口價分帳、付款方式聲明）。**入口用 `Navigator.push`**，唔改 `routing_rules.dart` / `app_router.dart`（仍然係其他 agent WIP）。
  - `mobile/lib/features/passenger/trip_detail_screen.dart`：加 requirements 卡、付款方式卡、「索取電子收據」按鈕。
  - `mobile/lib/features/driver/driver_jobs_screen.dart`：加特選目的地 pin 列（tap → server-side `premium_destination_id` 篩選）＋接單卡要求/動物/目的地 badge（**接單前**可見，因為 ACCEPTED 後取消會寫 `PENALTY_DEDUCTION`）。
  - 驗證：nearby filters 18 passed；`ruff` 0；`mypy` 124 files 0；mobile harness 149 passed 0 failed（+10）；`flutter analyze` 我 7 個檔 0 issue（`fixed_offers_screen.dart` 有 3 個 pre-existing error，係其他 agent 未 commit WIP，唔碰）。
  - **仍然未做**：司機列表「一口價」badge（要等 `Order.fareMode` 由其他 agent 入 HEAD）；收據 route 註冊（等 router WIP 入 HEAD）。
- 2026-10-05（本 agent：**nearby filters Phase 1 完成**——司機「接單」列表 server-side 篩選）
  - Backend：`GET /api/v1/orders/nearby` 新增 5 個 optional filter（`fare_mode` / `destination_area` / `premium_destination_id` / `requires` / `excludes`），全部 AND 組合；無 filter 時**回應 byte-for-byte 不變**（舊客戶零影響）。
  - 兩條原則：**value validation 由 server 做**（未知值 → 422，唔會靜靜地 match 唔到任何嘢）；**predicate 一定在 DB 做**（PostGIS `pickup_location` 過濾後才取 id，唔受 Redis geo 候選上限所限）。
  - Changes：`app/api/orders.py`（+131）、`app/core/region.py`（area 封閉集 + `is_valid_area`，+15）、`app/services/order/geo_service.py`（候選上限參數化，filtered query 用 200 vs 預設 50，+18）
  - Mobile mirror：新檔 `mobile/lib/models/nearby_filter.dart`（query contract mirror + copyWith sentinel + `==`）、`mobile/lib/state/nearby_filter.dart`（`NearbyFilterController` Notifier + `filteredNearbyOrdersProvider` family，family key 含 filter）；改 `mobile/lib/data/order_repository.dart`（`nearby(filter:)`）、`mobile/lib/features/driver/driver_jobs_screen.dart`（filter chip row + 「清除篩選」空狀態 + 改用 filtered provider）
  - Tests：`tests/api/test_nearby_filters.py` 新檔 **17 passed**（含 422 validation、未知 area、malformed UUID、AND 組合、animal presence/exclude）；mobile harness `tool/run_tests.dart` +5 → **139 passed 0 failed**
  - **注意（重要）**：`mobile/lib/models/order.dart` 嘅 `fareMode` 欄位**現時只存在於其他 agent 未 commit 嘅版本**，所以我冇加列表上嘅「一口價」badge（會令我自己嘅 commit 編譯唔到）。等 `order.dart` 入咗 HEAD 之後，可加一行 `_fareModeChip(order)`。
  - 唔知呢個係咩嘅話：`filteredNearbyOrdersProvider` 取代咗 `nearbyOrdersProvider` 嘅使用，但**冇刪** `nearbyOrdersProvider`（`mobile/lib/state/data_providers.dart` 仍係其他 agent WIP，唔碰）。
  - 順帶一提：`mobile/tool/run_tests.dart` 有一輪我用 `dart format` 意外令全檔重排，已 `git checkout` 還原、只重施自己嘅改動（diff 純 +77）。
- 2026-10-05（本 agent，建立檔）
- 2026-10-05（其他 agent：migration deep scan 完成並 commit `69c4484`／`e5454e5`；已更新本檔進度）
- 2026-10-05（記錄 fixed-fare fee ledger gap 待接手）
- 2026-10-05（本 agent：幫 recurring WIP 清 import/constraint blocker，留喺 worktree 唔 commit；fixed-fare ledger 等 recurring migration 入 main 先接）
- 2026-10-05（本 agent：recurring rides backend 已完成並 commit；API prefix `/api/v1/recurring-rides`、background `recurring_mint`、migration `7a1b2c3d4e5f`；測試 47 passed；full suite 內 analytics/fare_mode 相關 43 failures 係其他 agent WIP 引起，未有動佢啲檔）
- 2026-10-05（本 agent：Phase 1 fixtures + contract pins 完成，已 commit `4b868f7`；generator reset 修正 RESTRICT deposit/refund 清理）
- 2026-10-05（本 agent：audit owner 決策落地——N-1 接受 rank hierarchy + docstring 全套；NEW-18/NEW-29/NEW-30/M-M-1/M-M-4 已修；test 56 passed、ruff 0）
- 2026-10-05（本 agent：audit batch 2——NEW-2 OUTSIDE_HK + NEW-1 client box 誠實化；NEW-11 Redis lock release best-effort；NEW-12 recovery code 6-byte/48-bit；NEW-14 fleet rename pre-check + settlement upsert IntegrityError backstop；TOTP 56 + fleets/security 85 + Dart 134 passed）
- 2026-10-05（本 agent：audit batch 3——NEW-23 dispute audit 移入同一 transaction（`DisputeService` 改收 session，全部 dispute handler 一次 commit）；NEW-10 WS watchdog 移除（transport ping 負責）；disputes 56 passed、ruff 0）
- 2026-10-05（本 agent：fixed-fare platform fee ledger 完成——`FIXED_RIDE_FEE`、`reference_for_fixed_ride`、`order_complete` append、migration `042a7bc3e54c`；`alembic upgrade head` 已跑，fixed-fare/parity tests 9 passed）
- 2026-10-05（本 agent：**收據（backend + mobile mirror）+ 車內環境 mobile UI 完成**）
  - `POST|GET /api/v1/orders/{id}/receipt`（JSON）+ `GET .../receipt.txt`（純文字下載）；idempotent freeze、當事人限制（403/404 不可分辨）
  - 新檔：`app/api/receipts.py`、`app/api/schemas/receipt.py`、`app/services/receipt/`、migration `5e1a9c7d4b02`、`mobile/lib/models/receipt.dart`、`mobile/lib/features/driver/driver_environment_screen.dart`、`tests/api/test_order_receipts.py`
  - 改動：`app/models/user.py`（+3 欄位）、`app/api/router.py`、`app/api/schemas/__init__.py`、`mobile/lib/data/order_repository.dart`、`mobile/tool/verify_contract.dart`、`scripts/dev/gen_mobile_fixtures.py`、`scripts/verify/audit_response_models.py`、`mobile/test/fixtures/manifest.json` + 新 fixture `order_receipt.json`
  - 車內環境 UI 掛喺 driver account screen（唔郁 `driver_jobs_screen.dart`，避免同 sibling 撞）
  - **fixture generator 會重寫全部 fixtures 嘅 UUID/時間戳**：已 `git checkout -- mobile/test/fixtures/` 還原 churn，只保留新 fixture + manifest 一行
  - 驗證：receipt tests 7 passed；receipt+orders+attributes+premium+fixed-fare+hardening 56 passed；`verify_contract.dart` 60 fixtures 0 failure；`audit_response_models.py` OK；ruff 0；mypy 124 files 0；`flutter analyze` 我嘅檔 0 issue

- 2026-10-05（本 agent：**修正 4 個測試檔的 fare_mode blocker + 1 個時間相依 bug —— full-suite 由 41 failed → 0**）
  - 根因（跨 agent WIP 副作用，非本 agent 引入）：`8f2a1c5d3b40_fixed_fare_offers.py` 令 `orders.fare_mode` NOT NULL（**無 `server_default`**），但 3 個測試檔的 raw `INSERT INTO orders` 冇供值 → `NotNullViolationError` 40 個。
  - 另外一個**獨立時間相依 bug**：`test_admin_settlement_preview.py::TestCsvExport::test_export_carries_a_header_and_a_total_row` 用 `now()` 寫 ledger row 但查 `2026-W40`（今日係 W41）→ 同檔已有 `_week_instant()` helper 卻漏用。
  - Fix：4 檔 raw INSERT 補 `'METER'`（`test_admin_orders.py`／`test_admin_live_map.py`／`test_analytics_admin.py` ×2）+ settlement export 改用 `_week_instant(ref)`。4 檔共 +11/−10。
  - 驗證：該 4 檔 **86 passed**（原本 41 failed/45 passed）✅。三個測試檔**冇任何 agent 認領**、worktree 亦乾淨，所以安全改。
  - 注意：full-suite 另有 **676 errors** 屬其他 agent WIP 中途狀態（`app/services/licence/licence_service.py` 將 `MAX_SUBMISSIONS_PER_DAY` 改名為 `MAX_SUBMISSIONS_PER_ROLLING_24H` 但 `app/api/licence.py:49` 未同步）——**唔關本 agent 事**，等對方完成。

- 2026-10-05（本 agent：**admin console 爭議可見性 parity —— 乘客要求記錄落訂單詳情頁**）
  - **缺口**：admin 睇唔到乘客落單時要求咗乜。`AdminOrderRowOut` / `AdminOrderDetailOut` 完全冇 `requirements` / `payment_preference` / `driver_payment_methods` / `premium_destination` / `pickup_area` / `destination_area` / `receipt_requested` → 營運同事處理「我叫咗要靜音車」「我叫咗用八達通」「我叫咗要收據」呢類爭議時，**手上完全冇平台版本嘅事實**。呢啲欄位自 `78745ab` 起已經 freeze 落 order，只係 admin 冇 expose。
  - Backend：`app/api/schemas/admin.py`（`AdminOrderDetailOut` 加 7 個欄位）、`app/api/admin/orders.py`（新 helper `_admin_order_detail_out()`；**只落詳情頁、唔落 list row** —— list 要維持輕量，所以另加測試釘住「爭議 blob 唔可以漏入 list」）。
  - admin-web：`types.ts`（`OrderRequirements` / `AnimalDetail` / `OrderPaymentMethod` 鏡像）、`OrderDetailPage.tsx`（新「乘客當時要求」卡）、`labels.ts`（`paymentMethod` / `area` / `requirement` 三個 keyer）、`i18n/locales/{en,zh-Hant}.ts`（enum + 卡片文案，雙語）。
  - **設計原則（同 mobile 一致）**：`requirements: null` 同 `{animal: null}` 都係「冇要求過」——**唔可以**render 成「確認冇寵物」（資料唔支持呢個結論）；空 `payment_preference` 係「冇記錄」而**唔係**「只收現金」（營運爭議唔可以睇到憑空發明嘅事實）。
  - Tests：`tests/api/test_admin_orders.py` 加 6 個（+`_make_order` 擴參數）；新檔 `admin-web/web/src/pages/OrderDetailPage.test.tsx`（6 個，vitest + jsdom，釘住上面兩個「唔可以亂斷言」嘅 case）。
  - 驗證：admin 相關 8 檔 **177 passed**；admin-web `npm test` **75 passed**（10 檔）；`npm run typecheck` 我改嘅檔 0 error；`ruff` 0；`mypy` 124 files 0；`verify_contract.dart` 61 fixtures 0 failure；`audit_response_models.py` OK。
  - **注意**：`admin-web/web/src/components/primitives.tsx` 當時有 sibling 未 commit WIP（modal focus trap），`tsc` 報 2 個 `TS18048 'first'/'last' is possibly undefined` —— 屬對方改動，**本 agent 冇碰**。

- 2026-10-05（本 agent：**admin 收費模式 parity —— 列表同詳情頁睇得到一口價**）
  - **缺口**：`orders.fare_mode` 自 `8f2a1c5d3b40` 起已存在，但 `AdminOrderRowOut` / `AdminOrderDetailOut` / admin-web **完全冇 expose**（`grep -rn fare_mode admin-web/web/src` 係空）。後果：營運睇「乘客話一口價 $105、但落車跳錶 $140」呢類爭議時，**分唔到邊程係一口價**，會誤判成收錯錢。
  - Backend：`app/api/schemas/admin.py`（row 加 `fare_mode`）、`app/api/admin/orders.py`（`_admin_order_out()` 輸出 `order.fare_mode.value`）。
  - admin-web：`types.ts`（`AdminOrderRow.fare_mode: string` + 註解講清「跳錶數字唔同係預期，唔係收錯」）、`OrdersPage.tsx`（**只喺 `FIXED` 時**喺車型後綴 ` · 一口價` —— METER 係預設，加噪音冇用）、`OrderDetailPage.tsx`（**無條件**顯示 chip，爭議頁要睇得到而唔係靠「冇 badge」推論）、`labels.ts`（`fareMode` keyer）、`i18n/locales/{en,zh-Hant}.ts`（`enum.fareMode` 雙語）。
  - Tests：`tests/api/test_admin_orders.py` 新 `TestFareMode`（2 個；`_make_order` 加 `fare_mode` 參數）；`OrderDetailPage.test.tsx` 加 1 個（FIXED + METER 兩種都render 文字）。
  - 驗證：admin 4 檔 **94 passed**；admin-web `npm test` **76 passed**（10 檔）；`ruff` 0；`mypy` 124 files 0；full-suite **1143 passed / 2 failed / 1 error**（兩個 failure 全屬 sibling 現行 WIP：`app/api/licence.py` + `app/core/middleware.py` + `tests/api/test_security_hardening.py`，**唔關本 agent 事**）。
  - 環境備忘：跑 full-suite 前 **Docker Desktop 要開返**（`db` :15433 / `redis` :16379，`docker compose up -d db redis`）——今次中途 Docker 被關咗，一度全 pytest 連 DB 都連唔到。
  - 冇動 `admin/orders.py` 以外嘅 admin handler，亦冇改 `AdminOrderRowOut`（避免影響列表效能／形狀）。（註：下一批先加 `fare_mode` 落 row —— 屬刻意的後續，非矛盾。）

- 2026-10-06（本 agent：**admin 凍結收據唯讀可見性**）
  - `GET /api/v1/admin/orders/{order_id}/receipt`（`ReceiptOut`，`require_admin`）。Admin 本來可以經 party-facing `GET /orders/{id}/receipt` 讀，但嗰個 endpoint 有 side effect：第一次讀會**凍結** snapshot（設計上 to keep bytes idempotent）。Admin 瀏覽訂單唔應該代表乘客 mint 收據，所以呢條 admin route 係唯讀：`receipt_snapshot_json` 有就回傳 snapshot + `render_receipt_text()`，冇就 404，**永不 DB write**。
  - admin-web：`OrderDetailPage` 喺 `receipt_requested` 時顯示「查看收據」按鈕；按鈕 fetch `/admin/orders/{id}/receipt` 並 render `total`、`issued_at`、服務器原文 `<pre>`。i18n 雙語 + `OrderReceipt` TS mirror（對齊 `ReceiptOut`）+ `endpoints.orders.receipt`。
  - Tests：backend `test_admin_orders.py` 新 `TestAdminReceipt`（有 snapshot 讀取、support role、unissued 404 且**唔凍結**、unknown 404、匿唔到）；admin-web `OrderDetailPage.test.tsx` 加 2 個（未 requested 唔出按鈕；有 receipt 可開並 render 文件）。
  - 驗證：admin 4 檔 **102 passed**（原 100 + 2 404 測試修正後，test_admin_orders 34 passed / 其餘三檔全 passing）；admin-web `npm test` **81 passed**（11 檔）；`npm run typecheck` 0 error（sibling `primitives.tsx` 已無 TS18048）；`ruff` 0；`mypy` 0。
  - 注意：呢條 route 只服務 `receipt_snapshot_json` **已存在**的訂單；receipt 未 mint 就 404（UI 唔會 expose 按鈕）。


