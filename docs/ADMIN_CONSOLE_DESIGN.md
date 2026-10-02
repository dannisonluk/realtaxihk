# 管理後台功能設計

> **⚠️ 狀態（2026-10-01 更新）**：本文件是**設計提案**，其中**大部分已經實作**。
> 已落地的部分：四級 RBAC（`AdminRole` rank 比較、`require_role` 閘）、審計覆蓋
> 金錢／狀態改動、帳戶管理（建立／改角色／重設密碼）、訂單監控、結算預覽 +
> confirm token + CSV 匯出、爭議實體與裁決流程、主體搜尋、頭像上傳，
> 以及六個對應的後台畫面（`admin-web/web/src/pages/`）。
> 尚**未**實作：工單（ticket）系統、部分批量操作。
> 實作總覽見 `docs/WORK_SUMMARY.md` §2.11。
>
> **行號引用已失效**：`app/models/` 已由單一 `__init__.py`（886 行）拆成 5 個
> bounded-context 模組，本文中指向 `app/models/__init__.py:<line>` 的行號不再準確；
> 檔案層級的路徑（`app/api/admin.py` 等）仍然有效。
>
> 本文針對「加強 admin 後台功能設計」的需求，對照市場及跨行業通用做法，
> 逐模組說明**現狀（HAVE）**、**缺口（NEED）**、**設計方案**與**落地成本**。
>
> 撰寫原則：每一項都對應到真實的 table / API / 檔案；沒有實作的模組會明確
> 標示為「設計提案」，不會假裝已經存在。

---

## 0. 全域結論：現狀與缺口的落差在哪裡

後台目前有 **9 個頁面**（`admin-web/web/src/app/Shell.tsx` 的 `NAV` +
`App.tsx` 的 `buildRouter()`）：

| 頁面 | 路由 | 已接的 API |
|---|---|---|
| 總覽 | `/` | 由多個端點組合的 badge 計數 |
| 司機審核 | `/kyc` | `GET /admin/drivers`, `POST /admin/drivers/{id}/review` |
| 的士證審核 | `/licences` | `GET/POST /admin/licences*` |
| 退款 | `/refunds` | `GET /admin/refunds`, `POST /admin/refunds/{id}/decision` |
| 每週結算 | `/settlement` | `POST /admin/settlement/weekly/run` |
| 車隊 | `/fleets`, `/fleets/:id` | `GET/POST /admin/fleets*` |
| 表現分析 | `/analytics` | `GET /admin/analytics`, `GET /admin/analytics/heatmap` |
| 司機詳情 | `/drivers/:id` | `GET /admin/drivers/{id}` |

**三個決定性的架構缺口**，它們不是「功能少」，而是「地基缺」：

> **更正（2026-10-02）**：以下三個缺口（A 稽核覆蓋、B RBAC、C 爭議載體）**均已修復**。
> 本節保留為當時的落差分析；實作現況如下：
>
> - **A 已修復** —— `app/api/admin.py` 現有 **17 個 `record_audit` 調用點**，涵蓋 13 個
>   事件：`EV_KYC_DECISION`、`EV_DEPOSIT_GRANT`、`EV_DEPOSIT_ADJUST`、`EV_REFUND_DECISION`、
>   `EV_SETTLEMENT_RUN`、`EV_SETTLEMENT_PREVIEW`、`EV_DISPUTE_CREATE`／`_MESSAGE`／
>   `_ASSIGN`／`_RESOLVE`、`EV_ADMIN_ACCOUNT_CREATE`／`EV_ADMIN_ROLE_CHANGE`／
>   `EV_ADMIN_PASSWORD_RESET`。動錢與動狀態的動作都會留下稽核列（`payload` 欄見
>   migration `a1c4e8b7f209`）。高頻**搜尋**刻意不逐次審計，以免淹沒金錢事件。
> - **B 已修復** —— `AdminAccount.role`（`app/models/admin.py:108`）儲存粗粒度 RBAC 等級；
>   四級 `AdminRole`（`SUPPORT < OPERATIONS < FINANCE < SUPER_ADMIN`）以 **rank 比較**
>   作 gate（`require_role`，`app/core/deps.py:324`），每 request 重讀 live row，
>   不信任 token 的 `admin_role` claim。
> - **C 已修復** —— `OrderDispute` + `DisputeMessage`（`app/models/dispute.py`）已建表，
>   `app/api/admin.py` 有 11 處爭議相關處理，含 SLA、指派、狀態流與裁決。
>
> 下文的行號引用已按現行代碼更新（`app/models/` 已由單一檔案拆為 package）。

### 缺口 A — 稽核日誌只覆蓋登入，不覆蓋動錢

`AdminAuditLog`（`app/models/admin.py:281`）**已經存在，而且設計得不錯**：
append-only、`admin_id` 可為 NULL（令不存在的帳號嘗試登入都記錄得到）、
記 IP / user-agent、`created_at` 有索引、應用層無 UPDATE/DELETE 路徑。表頭
註釋甚至自己寫明它要回答的是 *"who logged in, from where, and did they move
money"*。

但當時實際上**只有一個寫入點**（見上方更正）：`app/services/admin_auth_service.py:177` 的
`AdminAuthService.audit()`。而**呼叫它的 event 常數只有登入類**：

```
EV_LOGIN, EV_LOGIN_FAILED, EV_LOCKED,
EV_TOTP_VERIFY, EV_TOTP_FAILED, EV_TOTP_ENROLLED,
EV_RECOVERY_USED, EV_RECOVERY_REGENERATED
```

結果是：**動錢與動狀態的動作一行稽核都沒有**。

| 動作 | 端點 | 有稽核記錄？ |
|---|---|---|
| KYC 核准 / 拒絕 / 停權 | `POST /admin/drivers/{id}/review` | ❌ |
| 保證金補款 | `POST /admin/drivers/{id}/deposit/grant` | ❌（只有 ledger `created_by`） |
| 保證金人工調整 | `POST /admin/drivers/{id}/deposit/adjust` | ❌（只有 ledger `note`） |
| 退款決定（唯一出金路徑） | `POST /admin/refunds/{id}/decision` | ❌（只有 `decided_by`） |
| 每週結算執行 | `POST /admin/settlement/weekly/run` | ❌ |
| 車隊建立 / 修改 | `POST /admin/fleets*` | ❌ |

`ledger_entries.created_by` 補不回這個缺口 —— 它只在**金額真的動了**的時候才
有值，而「拒絕一個退款」「否決一張 KYC」同樣是需要問責的決定，卻不留痕。
一個實習生駁回 200 張 KYC，今天在系統裡是**完全隱形**的。

### 缺口 B — 沒有權限分級（RBAC 不存在）

- `AdminAccount`（`app/models/admin.py:108`）**當時沒有 role / permission 欄位**（見上方更正，`role` 已補上）。
- `require_admin`（`app/core/deps.py:324`）是**二元判斷**：有真身 active
  `admin_accounts` row + `totp_enrolled_at` 已設。通過就是 admin，一視同仁。
- `_admin_out`（`app/api/admin_auth.py:414`）回傳 id / username / email /
  full_name / totp_enrolled，**沒有角色**。
- `Shell.tsx` 的 `NAV` 是**靜態陣列，沒有任何 role 條件**。

後果：客服、財務、暑期實習生拿到的是**完全一樣、無限制**的控制台 —— 包括
「核准退款」按鈕，而是唯一真正把錢匯出平台的路徑。

### 缺口 C — 沒有工單 / 爭議 / 申訴的任何載體

全庫沒有 support ticket、dispute、complaint 表。今日一個乘客投訴司機態度，唯一
的記錄方式是口頭 / 電郵，系統內零足跡；亦沒有 SLA、沒有指派、沒有狀態流。

---

## 1. 使用者與司機管理

### HAVE

- `GET /api/v1/admin/drivers` — 分頁列表，可依 `status` 過濾。
- `GET /api/v1/admin/drivers/{id}` — **單次來回取齊**：檔案、`hk_id_last4`
  （刻意遮罩，不出完整號碼）、保證金（連 `shortfall_hkd` 差額）、ledger
  近 50 筆、退款近 20 筆、所屬車隊。這個是「決定前必須看的東西」視圖。
- `POST /api/v1/admin/drivers/{id}/review` — approve / reject / suspend /
  terminate，經 `assert_driver_transition` 把關。

### NEED

| 缺口 | 說明 |
|---|---|
| 乘客側完全缺席 | 後台**只管理司機**，沒有乘客列表 / 詳情 / 停權。乘客濫用（假單、騷擾司機）今日無法處理。 |
| 列表無搜尋 | `list_drivers` 只支援 `status_filter` + 分頁。沒有電話 / 車牌 / 姓名搜尋。客服接電話時無法定位帳號。 |
| 決定無稽核 | 見缺口 A。 |
| 無批次操作 | 500 張 KYC 要逐張點。 |

### 設計方案

1. **統一「主體」搜尋**：新增 `GET /admin/search?q=`，對 `users.phone_e164`、
   `users.display_name`、`driver_profiles.vehicle_reg_mark`、
   `driver_profiles.taxi_driver_plate_no` 做前綴匹配，回傳統一結果型別。
   這是客服最常用的一個入口。
2. **乘客管理頁**：`GET /admin/passengers`、`GET /admin/passengers/{id}`。
   詳情頁展示行程史、取消率、投訴記錄。停權走 `users.is_active`（**已存在**，
   而 `get_current_user` / `require_active_user` 已經會**逐個 request** 讀 live
   row，所以停權即刻生效，不需要新機制）。
3. **批次 KYC**：`POST /admin/drivers/review/bulk`，body 為 `{ids: [], decision}`。
   必須逐筆寫稽核，不可以一筆 aggregate。

---

## 2. 訂單監控

### HAVE

**幾乎沒有。** 後台**沒有任何訂單頁面、沒有任何訂單 API**。
`app/api/orders.py` 全部是乘客 / 司機視角，沒有 `/admin/orders`。

`Order` model 有齊 `accepted_at`、`driver_arrived_at`、`completed_at`、
`cancelled_at`、`cancellation_reason`，狀態機 `ORDER_TRANSITIONS` 完整。

### NEED

運營現場最痛的是「**有個乘客打電話來說司機無出現**」，而後台答不到：
這張單現在是什麼狀態？哪個司機？車去到哪裡？多久之前接單？

### 設計方案

1. **`GET /admin/orders`** — 過濾 `status` / `date range` / `driver_id` /
   `passenger_id`，分頁。回傳一行所需：id、狀態、乘客、司機、上落客點、
   各時間戳、車資。
2. **`GET /admin/orders/{id}`** — 詳情：完整時間軸 + 車資明細（直接重用
   `FareBreakdown` 欄位）+ 該單的 ledger entries + 若有的中斷 / 爭議記錄。
3. **實時地圖頁（Live Ops）** — 這個是 ROI 最高的一頁。資料**已經有**：
   `driver_profiles.current_location`（`geography(POINT,4326)`）＋
   `is_online`。只需一條 `GET /admin/orders/active` 回傳全部 in-flight 單的
   司機位置，前端用 Leaflet 畫。
   > 注意：不要為了這頁另開 WebSocket。`TripHub` 已經有 channel，但
   > **lifecycle event 從未被 publish**（見 `docs/PROJECT_UNDERSTANDING.md`
   > 與 `mobile/README.md:135`）。營運地圖用 5 秒輪詢即可，成本可忽略
   > （見 `docs/REALTIME_POSITION_COST.md` 的 scaling 表）。
4. **SLA 告警** — 「接單超過 10 分鐘未到達」「in-trip 超過 90 分鐘」等規則
   以 badge 形式呈現，做成 `Shell.tsx` 可以顯示的 `badges` 計數（**機制已存在**，
   `badges.pendingKyc` / `pendingRefunds` 已是先例）。

---

## 3. 財務結算

### HAVE

- `POST /admin/settlement/weekly/run?period=YYYY-Wnn` — 人手重跑 ISO 週結算，
  **冪等**（`reference = weekly:<period>`，重跑不會重複收費）。
- `POST /admin/drivers/{id}/deposit/grant` — 補款，`reference_for_grant`
  做 server-side namespace，令 client key 無法碰撞到 weekly / refund。
- `POST /admin/drivers/{id}/deposit/adjust` — 有號調整（±HK$5,000），
  `reason` **必填**（`min_length=3`），設計註釋寫得好清楚：*"an adjustment has
  no upstream event to point at, so the reason is the audit trail"*。
- `LedgerEntry` append-only，有 `UniqueConstraint("id","created_at")`。

### NEED

| 缺口 | 說明 |
|---|---|
| 無對帳視圖 | 沒有「今週收了多少、應收多少、差額從哪裡來」。 |
| 結算無試算 | `run_weekly` 一按即扣錢，沒有 dry-run。這是最容易出事的按鈕。 |
| 無匯出 | 財務要交數，今日只能靠 SQL。 |
| 無發票 / 收據 | 車隊客戶需要單據。 |
| 結算執行無稽核 | 見缺口 A。 |

### 設計方案

1. **`GET /admin/settlement/preview?period=`** — 對指定週跑同一套計算但
   **不寫任何 ledger**，回傳「將會有 N 個司機被扣、合計 HK$X、其中 M 個會導致
   負結餘」。**這個應該是 `run_weekly` 之前的必經步驟** —— 一個動錢的按鈕，
   不應該是一次盲按。
2. **`POST /admin/settlement/weekly/run` 加 `confirm_token`** ——
   preview 回傳一個簽名 token，run 需要帶回。防誤觸 + 令「看過再按」變成
   結構性約束，而不是口頭規矩。
3. **`GET /admin/settlement/export.csv?period=`** — 逐筆 entry 匯出，OTel 友善。
4. **車隊對帳**：`GET /admin/fleets/{id}/statement?period=`，因為
   `FleetMembership` 已有 `weekly_fee_discount_percent`，車隊是一個真實的
   計費主體，它應該看得到自己的帳。

---

## 4. 爭議處理

### HAVE

- `RefundRequest` + `POST /admin/refunds/{id}/decision` —— 但這個是
  **司機申請退保證金**，不是乘客投訴司機。兩者是不同的東西。
- `orders.cancellation_reason` 是自由文字。

### NEED

**沒有 dispute / complaint 實體。** 全庫 search 沒有 ticket、dispute、complaint
表。這是九個模組中最空白的一個。

### 設計方案

> **與 P4 協調（重要）**：`docs/IN_TRIP_REDESIGN.md` §4.4 為 in-trip 設計了
> `order_disputes`。**兩者是同一張表，不可以開兩張。** 最終 schema 以
> `order_disputes` 為基礎，`order_id` 可為 NULL（for 非訂單類投訴）。
> 下表是完整版；P4 的版本是其訂單相關子集。

```
order_disputes
  id                uuid pk
  order_id          uuid fk orders(id) nullable   -- 未有單都可能投訴
  raised_by_kind    enum('passenger','driver','admin','system')
  raised_by_id      uuid nullable                 -- system 自動開單時為 NULL
  source            varchar(32)   -- AUTO_INTERRUPTED | PARTY_REPORT
                                  -- | ADMIN_CREATED | PARTY_CANCELLED_IN_LOCK_WINDOW
  against_kind      enum('passenger','driver','platform')
  against_id        uuid nullable
  category          enum('FARE','CONDUCT','SAFETY','LOST_ITEM','APP_ISSUE','OTHER')
  severity          enum('LOW','NORMAL','HIGH','SAFETY_CRITICAL')
  status            enum('OPEN','INVESTIGATING','AWAITING_PARTY','RESOLVED','ESCALATED','CLOSED')
  assigned_admin_id uuid fk admin_accounts(id) nullable
  resolution        varchar(24) nullable   -- NONE | CHARGE_PASSENGER | CHARGE_DRIVER
                                           -- | REFUND_PLATFORM_FEE | WAIVED_PLATFORM_FEE
  resolved_by       uuid fk admin_accounts(id) nullable
  resolved_at       timestamptz nullable
  resolution_note   text nullable
  sla_due_at        timestamptz
  created_at / updated_at
```

配套：
- `dispute_message` 子表（雙方 + 客服都往上寫，形成可稽核的往來記錄；
  加 `is_internal` 旗標 —— 客服自己人之間的討論不應該讓當事人看到）。
- `GET/POST /admin/disputes*`、`POST /admin/disputes/{id}/assign`、
  `POST /admin/disputes/{id}/resolve`。
- **自動開單**：`orders.status` 變 `INTERRUPTED` 時，在**同一 transaction**
  內開一張 dispute（見 IN_TRIP_REDESIGN §3.3 的觸發規則表）。
  > 若靠當事人自己開單，出事那一刻人人都忙，之後就沒有人記得。
- **`resolution` 必填、無 default** —— 動錢的裁決不可以「跟預設」。
- **`SAFETY_CRITICAL` 應自動觸發司機停權候選**，但**不可以自動停** ——
  要有真人確認，否則一個假投訴就足以令司機停工。這個是刻意的。
- 前端：`/disputes` 頁，預設排序 = SLA 快到期優先（不是最新優先）。


---

## 5. 權限分級（RBAC）

### HAVE

**沒有。** 見缺口 B。

### 設計方案

這個是所有其他模組的前置條件 —— 有了工單、對帳、稽核，如果沒有分級，等於把
所有新權力都發給所有人。

**角色模型（四級，最高級可管理所有）**：

> **修訂（依用戶 2026-10-01）**：**沒有實習生角色**；新增一個**最高級別，
> 可以管理所有**。

| 角色 | 定位 | 可以看 | 可以改 |
|---|---|---|---|
| `SUPPORT`（客服） | 前線 | 工單、訂單、司機唯讀 | 回覆工單、改工單指派與狀態；**不可**動錢、不可批 KYC、不可改他人帳號 |
| `OPERATIONS`（營運） | 審核 + 處理爭議 | 全部 | KYC 裁決、司機停權、處理 dispute 裁決；**不可**動錢額度（grant / adjust / settlement） |
| `FINANCE`（財務） | 金流 | 全部 | settlement run、grant、adjust、refund decision、dispute 的財務結果；**不可**批 KYC、不可改角色 |
| `SUPER_ADMIN`（最高管理員） | **可以管理所有** | 全部 | **全部**，外加：建立 / 修改 / 停用 admin 帳號、指派與變更任何人的角色、管理系統設定（費率、週費、罰款比例） |

**為什麼是這四級而不是三級：**

- `SUPPORT`（客服）與 `OPERATIONS`（營運）**必須分開**。客服的日常是「回答
  問題、記錄問題」，營運的日常是「做決定」。若合併，客服就會拿到 KYC 核准權
  —— 而 KYC 核准是司機能否上線營運的門檻，是一個實質的合規判斷，不應該由
  第一線客服承擔。
- `FINANCE` 與 `OPERATIONS` **必須分開**。KYC 是合規判斷，不應該由管錢的人
  因為「想快點上線多點生意」而放寬；反過來，動錢的權力也不應該給負責審核
  文件的人。這是標準的職責分離（separation of duties）。
- **`SUPER_ADMIN` 是唯一可以改角色的人。** 若 `OPERATIONS` 或 `FINANCE`
  也能改角色，那角色分級可以被自我提權繞過 —— 一個 `OPERATIONS` 帳號把自己
  改成 `SUPER_ADMIN`，所有分級就形同虛設。**這是 RBAC 唯一的死穴，
  必須由架構而非紀律守住。**

**實作方式**：

1. **Schema** — `admin_accounts` 加 `role` 欄位（`String(16)`，預設 `SUPPORT`，
   **不是** `SUPER_ADMIN` —— 新增帳號要預設最小權限，不可以靠人記得改低）。
   Alembic migration + backfill：**現有帳號全部設為 `SUPER_ADMIN`**
   （否則升級即鎖死，沒有人有權限去授予權限）。
   > ⚠️ 這次 backfill 一定是 `SUPER_ADMIN` 而非其他 —— 因為必須有一個
   > 帳號能改角色，否則系統進入死鎖。但**完成後應立即人手把多餘的帳號調低**，
   > 這一點要寫進 migration 的註釋與部署步驟。
2. **Token** — `issue_admin_access_token` 把 `admin_role` 放入 claim。
   但 **`require_admin` 一定要讀 live DB row 而不是信 claim** ——
   `deps.py` 已經有這個原則（*"trust the DB"*，見 `require_active_user`
   對 `row.role != user.role` 的處理）。降權必須即刻生效。
3. **依賴** — 新增 `require_role(*roles)` 依賴工廠，掛在動錢端點上：

   ```python
   # 概念示意。注意 SUPER_ADMIN 不一定出現在每個 tuple —— 用一個
   # 「高於」判斷（rank >= required）比逐個列舉更難漏。
   require_finance     = require_role(AdminRole.FINANCE)       # rank >= FINANCE
   require_operations  = require_role(AdminRole.OPERATIONS)
   require_super       = require_role(AdminRole.SUPER_ADMIN)
   ```

   **建議用 rank 比較而不是 set 成員測試**：`if user.admin_rank < required_rank:
   reject`。用 set 的話，每次加一個新角色都要回頭檢查所有 tuple，
   而漏掉一個就是一個開放的路由。
   現有全部 `Depends(require_admin)` 保持不變（= 全部角色可讀），
   只在**寫入端點**加上更嚴的依賴。這個令改動是**加法**，不會一次過
   漏掉某條路由就變成開放。
4. **改角色端點本身要特別小心** —— `PATCH /admin/accounts/{id}/role`：
   - 只有 `SUPER_ADMIN` 可呼叫。
   - **不可以改自己的角色**（防止降級自己之後無法復原，或更壞的自我提權路徑）。
   - 不可以改到令系統沒有 `SUPER_ADMIN`（最後一個不可以降級）。
   - 每次改動寫 `AdminAuditLog`，`detail` 記 `from` / `to`。
   > 這四條是**同一個約束的四個面**：令「管理權限」本身不會被意外或惡意清空。
5. **雙人覆核（two-person rule）** — 對最高風險動作（`refund/decision`
   approve、`settlement/weekly/run`），要求第二個 admin 以**不同帳號**確認。
   實作：`pending_approval` 表 + `approval_request`，或者最簡版本 ——
   run 需要帶上 `approved_by: <other_admin_id>`，並校驗兩者不同。
   > **建議**：先做 role，再做雙人覆核。雙人覆核會改變 UI 流程（需要
   > 「待我覆核」收件匣），是一個獨立的 feature，不應該塞入 role 這一步。
6. **前端** — `Shell.tsx` 的 `NAV` 由靜態陣列變成 `NAV.filter(item =>
   canSee(item, role))`；`_admin_out` 加 `role`；`AppContext` 記住 role，
   並提供 `<RequireRole>` 包裹動錢按鈕（**UI 隱藏不等於授權** —— 後端依賴
   才是權威，前端只是不要引誘人按）。


---

## 6. 數據分析儀表板

### HAVE

- `GET /admin/analytics` — 按時間分桶的收入表（`day`/`week`/`month`），
  可過濾 `taxi_type`、可排序。範圍上限 1096 日（三年），超出回 422。
- `GET /admin/analytics/heatmap` — 按一日 24 小時的收入分佈。
- 兩者**全部由 `orders` 派生**，沒有新表 —— 所以沒有「統計數字同訂單漂移」的問題。
  這個設計是對的，應該沿用。
- 邊界是**香港時間的日界**（`_hk_day_bounds`），不是 UTC。

### NEED

| 缺口 | 說明 |
|---|---|
| 無營運指標 | 只有錢。沒有接單率、取消率、平均等待時間、司機上線率。 |
| 無供應側 | 沒有「多少司機在線 / 多少有單」的供需圖。 |
| 無地理分佈 | 只有 heatmap（時間），無「哪區最多單」。 |

### 設計方案

1. **`GET /admin/analytics/operations`** — 同一套派生原則：從 `orders` 計
   - 接單率 = `accepted_at is not null` / `created`
   - 取消率（分乘客取消 / 司機取消 / 超時未接）
   - 平均接單耗時 = `avg(accepted_at - created_at)`
   - 平均到達耗時 = `avg(driver_arrived_at - accepted_at)`
   全部是 SQL 聚合，零新表。
2. **`GET /admin/analytics/supply`** — 由 `driver_profiles.is_online` +
   `current_location` 派生：在線司機數、有單司機數、供需比。
3. **地理分佈** — `orders.pickup_location` 已是 `geography(POINT,4326)`，
   可直接 `ST_SnapToGrid` 分格。**但要注意地圖合規**：任何地圖呈現必須用
   合規圖源，不可以自行拼湊邊界。
4. **儀表板自訂** — **不建議**做。一個固定、經過思考的 6 格儀表板，
   好過一個要人自己拉 widget 的畫布；後者通常最後沒有人設定。

---

## 7. 稽核日誌

### HAVE

表在、設計好、**只有登入在用**。見缺口 A。

### 設計方案

1. **抽出共用寫入器** — `AdminAuthService.audit()` 是 instance method，
   令非 auth 路徑難以重用。建議抽成 `app/services/audit_service.py`
   的 `record_audit(session, event, outcome, actor, detail, request)`，令
   `admin.py` 可以直接呼叫，不需要構造一個 `AdminAuthService`。
2. **補上動錢 event 常數**（加法，不需要 migration —— 表設計本身就是
   string 而不是 enum，註釋解釋過原因）：

   ```
   EV_KYC_DECISION, EV_DEPOSIT_GRANT, EV_DEPOSIT_ADJUST,
   EV_REFUND_DECISION, EV_SETTLEMENT_RUN, EV_FLEET_UPSERT,
   EV_ADMIN_ACCOUNT_CREATE, EV_ADMIN_ROLE_CHANGE, EV_DISPUTE_RESOLVE
   ```

3. **`detail` 由 255 字擴闊** — 現在 `detail` 是 `String(255)`。
   「adjust 了 HK$-1,200，原因：…」好快爆。建議加 `payload JSONB nullable`
   存結構化前後值（`{"before": ..., "after": ...}`）。這個需要 migration。
4. **`GET /admin/audit` 查詢頁** — 過濾 `event` / `admin_id` / 日期範圍。
   索引已備（`event`、`admin_id`、`created_at` 都有 index）。
5. **不可變性** — 目前「不可變」靠「應用層沒有寫路徑」。這個對有 DB 權限的人
   無效。建議加 DB trigger 拒絕 UPDATE/DELETE，或至少一個
   `REVOKE UPDATE, DELETE` on 該表。**這個是縱深防禦，不是 bug。**

---

## 8. 風險控管

### HAVE

- 登入節流：`LOGIN_IP_LIMIT=30/15min`、`TOTP_IP_LIMIT=20/15min`、
  帳號鎖定（`AdminAccountLocked` 是可區分的子類）。
- Token 吊銷 epoch（`token_revocation`），logout 即刻令在野 access token 失效。
- 退款 approve 有 `refund:{id}` 唯一 reference，防連點。

### NEED

| 缺口 | 說明 |
|---|---|
| 無行為風控 | 「同一個 admin 5 分鐘內改 30 筆保證金」今日沒有任何反應。 |
| 無金額上限告警 | `adjust` 有 ±5000 上限，但**累計**無上限。 |
| 司機側風控 | 沒有「同一司機高頻取消」偵測。 |
| 乘客側風控 | 沒有假單 / 重複帳號偵測。 |

### 設計方案

1. **規則引擎（輕量）** — 不需要引入外部系統。用 Redis 計數器做滑動窗口：
   - `admin:<id>:money_ops` —— 動錢次數，超閾值即寫 `EV_RISK_ALERT`
     稽核 row + 推 badge 告警。
   - `driver:<id>:cancel_rate`、`passenger:<id>:order_rate` 同理。
2. **審批門檻** — 金額或頻次超標時，**自動轉為待覆核**而不是直接拒絕。
   拒絕會被繞過（叫人分幾次做），待覆核不會。
3. **與工單系統連動** — 高風險事件自動開一單 `order_disputes`
   （`category=OTHER`, `severity=HIGH`），令它有 owner、有 SLA。

---

## 9. 客服工單系統

### HAVE

**沒有。** 見缺口 C。

### 設計方案

與 §4 爭議處理**共用同一個實體**。理由：一個「乘客投訴司機繞路」既是爭議，
也是工單；如果拆成兩張表，客服會不知道應該開哪張，最後兩邊都做一半。

因此：

- **`order_disputes` 就是工單。** `category` 區分性質，`severity` 區分輕重。
- **`dispute_message`** 就是對話記錄（含內部備註 —— 加 `is_internal` 旗標，
  因為「客服自己人之間討論」不應該讓當事人看到）。
- **SLA** 由 `severity` 決定 `sla_due_at`：`SAFETY_CRITICAL` = 1 小時、
  `HIGH` = 4 小時、`NORMAL` = 24 小時、`LOW` = 72 小時。
  > **SLA 逾期不阻塞任何人**（依 P4 的決定：中斷即時生效、行程早已結束）。
  > 它只是一個內部提醒 + 儀表板上的紅字。**不要把 SLA 做成阻塞閘** ——
  > 那會把「行政效率問題」變成「當事人被困」。
- **處理 dispute 需要 `OPERATIONS` 或以上**；若裁決涉及金錢
  （`resolution` 會寫 ledger），需要 `FINANCE` 或以上（見 §5）。
- **前端**：`/disputes` 列表（預設按 SLA 到期升序）+ 詳情頁（時間軸 + 對話）。
- **與訂單頁互相連結** —— 由訂單詳情可以直接開單，開單時自動帶入
  `order_id` / 雙方 id。客服最痛的是要手動重複輸入已知資料。

---

## 10. 落地順序（依賴關係，不是時間估計）

```
① RBAC（§5）
     └─ 是 ② 到 ⑥ 的前提：先有角色，新增的權力才有邊界
② 稽核擴充（§7）
     └─ 與 ① 同一批做：所有新端點由第一行程式碼就寫稽核
③ 訂單監控（§2）
     └─ 純讀取、零 schema 改動、ROI 最高，可獨立先上
④ 爭議 / 工單（§4 + §9）
     └─ 與 P4 的 order_disputes 是同一張表，應合併實作
     └─ P4 的中斷會自動開單，所以 ④ 是 P4 的依賴
⑤ 訂單監控的乘客側 + 使用者管理補齊（§1）
⑥ 財務試算 + 匯出（§3）
⑦ 營運 / 供應 analytics（§6）
⑧ 風控規則引擎（§8）—— 需要 ② 的稽核數據做輸入
```

> **與 P4 的交叉依賴**：P4（`docs/IN_TRIP_REDESIGN.md`）的中斷會自動開
> dispute，所以 ④ 必須在 P4 之前或同步完成。兩份文件應一併閱讀 ——
> 表結構以 `IN_TRIP_REDESIGN.md` §3.3 為準。


---

## 11. 明確不建議做的事

| 想法 | 為何不做 |
|---|---|
| 做可拖拉的自訂儀表板 | 最後沒有人設定。固定、經過設計的版面更有用。 |
| 為營運地圖另開 WebSocket | `TripHub` 已有 channel；先用 5s 輪詢，成本可忽略。 |
| 把 `AdminAuditLog` 改成 enum | 表頭註釋已解釋：append-only 表 + enum = 每個新 event 都要 migration。保持 string。 |
| 用前端隱藏按鈕當作權限 | UI 隱藏只是禮貌。權威在後端依賴。兩者都要做，但不可以只有前者。 |
| 為爭議與工單分開兩張表 | 客服會不知道開哪張。共用一個實體，用 `category` 區分。 |
| 自動停權（收投訴即停司機） | 一個假投訴就能令司機停工。自動化最多到「標記待覆核」。 |

---

## 12. 現有設計中值得保留、不要「重構掉」的部分

- **`GET /admin/drivers/{id}` 的單次來回組合** —— 註釋已解釋「五次呼叫可以
  渲染出一個半真半假的司機」，這個原則應推廣到訂單詳情與工單詳情。
- **`ADJUSTMENT` 的 `reason` 必填** —— 理由就是稽核軌跡，不可以變 optional。
- **`reference_for_*` 的 server-side namespace** —— 防止 client key 碰撞，
  新增任何動錢端點都要跟。
- **`money_str()` 統一 2 位小數** —— 不要在新頁面手寫 `str(amount)`。
- **analytics 全部由 `orders` 派生** —— 不要為了快而建 rollup 表，
  除非有實測證明慢。
