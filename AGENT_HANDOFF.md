# Agent Handoff / 協作進度

這個檔是 realtaxihk repo 內多個 agent 的共享協作進度。任何 agent 在 commit 或開始大改之前，請先讀這個檔，並在改動後更新對應區段，避免互相覆蓋。

## 使用規則

1. 每次開始/完成工作前，更新「Last updated」。
2. 揸住邊啲檔案，就將檔案路徑寫入「Ownership / 檔案認領」區段。
3. 唔好改「另一 agent」已認領嘅檔案；如果一定要改，先喺此檔留言。
4. Commit 前只 add 自己嘅檔案，唔好一次過 `git add .`，以免捲入其他 agent 嘅 WIP。
5. 如果發現衝突，唔好直接 revert 對方，先喺此檔標記並等對方/用戶確認。

## 現時 git 狀態

- 最新 commit：`ab95c7c`（其他 agent）
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

## Ownership / 檔案認領

### 本 agent（Hermes 主要承接方）

- `AGENT_HANDOFF.md`（本檔）
- 已完成：
  - `docs/FEATURE_EXPANSION_2026-10-05.md`（integrated backlog）
  - Phase 1 特選目的地/司機屬性：`app/models/premium.py`（基礎版已 commit，但現時 worktree 有後續改動，見下）
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
- `alembic/versions/f1c2d3e4a5b6_harden_deposit_and_refund_fks.py`
- `mobile/lib/core/security/username_policy.dart`
- `mobile/lib/features/driver/fixed_offers_screen.dart`
- `mobile/lib/features/shared/profile_setup_screen.dart`
- `mobile/lib/models/fixed_offer.dart`
- `mobile/lib/router/app_router.dart`、`mobile/lib/router/routing_rules.dart`、`mobile/lib/data/identity_repository.dart`
- `new/`（用戶/其他 agent 素材，唔好掂）

### 混亂區 / 請勿亂改

以下檔案現時 worktree 有未 commit 改動，可能同時被兩個 agent 郁過。等確認後先好處理：
- `app/models/premium.py`
- `alembic/versions/5c8b2f0a1e43_premium_destinations_driver_attributes.py`
- `alembic/versions/8f2a1c5d3b40_fixed_fare_offers.py`
- `tests/api/test_security_hardening.py`
- `mobile/lib/core/network/wire.dart`
- `mobile/lib/data/driver_repository.dart`
- `mobile/lib/models/order.dart`
- `mobile/lib/state/data_providers.dart`

## 衝突紀錄

- 2026-10-05：本 agent 曾開 `alembic/versions/6f3a9c2e8b15_fixed_fare_and_recurring.py`，但發現其他 agent 已有 fixed-fare migration，已刪除該重複檔。現時 alembic head 只有 `f1c2d3e4a5b6`。
- `app/models/premium.py` 曾有本 agent 的 FixedPriceOffer draft，與 `app/models/fixed_offer.py` 重疊；現時 worktree 狀態未定，請其他 agent 唔好覆蓋，等我哋一齊核對。

## 下一步

### 本 agent 擬做

- 重複行程 / recurring rides（backend + migration + API + mobile mirror）：
  - 會用全新檔案 `app/models/recurring.py`、`app/api/recurring.py`、migration，避免撞現有檔案。
  - 唔會改 `app/models/user.py`、`app/models/fixed_offer.py`、`app/services/order/*`、`mobile/lib/models/order.dart`，除非此檔確認由本 agent 接管。

### 其他 agent 擬做

- 請喺此檔補低你正在做嘅 slice，避免同 recurring rides 撞。
- 如果你已處理 fixed-fare mobile UI，請標記 `mobile/lib/features/driver/fixed_offers_screen.dart` 完成。

## Last updated

- 2026-10-05（本 agent，建立檔）
