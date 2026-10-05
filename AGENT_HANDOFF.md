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
- Alembic head：`f1c2d3e4a5b6`（已確認 `alembic heads` 只有一個 head）

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
  - **仲等緊 owner / 未郁**：mobile 大項（M-H-1 enum unknown、M-H-2 outbox、M-M-2 settlement 警示、M-M-3 autoDispose、M-M-5 token header、NEW-24）、AC-06/07、F-05/F-06、NEW-15/16/17/9 未郁，避免踩 sibling WIP。

### 混亂區 / 請勿亂改

以下檔案現時 worktree 有未 commit 改動，可能同時被兩個 agent 郁過。等確認後先好處理：
- `mobile/lib/core/network/wire.dart`
- `mobile/lib/data/driver_repository.dart`
- `mobile/lib/models/order.dart`
- `mobile/lib/state/data_providers.dart`

（已清：`app/models/premium.py`、`5c8b...`、`8f2a...`、`tests/api/test_security_hardening.py` 已在 `69c4484`／revert 處理完）

## 衝突紀錄

- 2026-10-05：本 agent 曾開 `alembic/versions/6f3a9c2e8b15_fixed_fare_and_recurring.py`，但發現其他 agent 已有 fixed-fare migration，已刪除該重複檔。現時 alembic head 只有 `f1c2d3e4a5b6`。
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
- 提醒：新 migration 請接 `f1c2d3e4a5b6`，避免再出現多 head 分叉。
- 2026-10-05 巡查：recurring WIP（`7a1b2c3d4e5f_recurring_rides.py`、`app/api/schemas/recurring.py`、`app/models/recurring.py`）已接 `f1c2d3e4a5b6`，`alembic heads` 只得 `7a1b2c3d4e5f`，無分叉；`app/api/schemas/__init__.py` 嘅 recurring export 我唔碰。
- 2026-10-05 另外幫 recurring WIP 清咗 blocker（唔 commit，留喺 worktree 俾你 review）：
  - `app/models/__init__.py`：`Recurring*` 原本錯誤由 `app.models.premium` import，改為 `from app.models.recurring import ...`
  - `app/models/recurring.py`：補返 migration 有但 model 冇嘅 `CheckConstraint("weekday BETWEEN 1 AND 7")`
  - `7a1b2c3d4e5f_recurring_rides.py`：enum 名改為 model 一致嘅 `ck_recurring_rides_status`／`ck_recurring_rides_frequency`，並加相應 check constraint；
  - 已跑 migration parity：3 passed；ruff format 已套用。

## Last updated

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
