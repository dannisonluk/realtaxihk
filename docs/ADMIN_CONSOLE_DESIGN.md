# 管理後台功能設計 / Admin Console Design

> **摘要**：管理後台功能設計——四級 `AdminRole`、各畫面職責、審計要求、RBAC 理由。
> **大部分已實作**，行號已指向現行位置。核心設計決定：**角色用 rank 比較
> （`role.rank >= FINANCE.rank`），不用集合成員**——集合下每新增角色都要重讀每組
> 枚舉，漏一組即一條開放路由；rank 令 default-deny 自動成立。

> **⚠️ 狀態（2026-10-01）**：大部分已實作——四級 RBAC、審計覆蓋金錢／狀態改動、
> 帳戶管理、訂單監控、結算預覽 + confirm token + CSV 匯出、爭議裁決、主體搜尋、
> 頭像上傳、六個後台畫面（`admin-web/web/src/pages/`）；未實作：工單系統、部分批量
> 操作。總覽見 `docs/WORK_SUMMARY.md` §2.11。**行號引用已失效**：`app/models/` 已由
> 單一 `__init__.py`（886 行）拆成 5 個 bounded-context 模組，
> `app/models/__init__.py:<line>` 不再準確；檔案層級路徑（`app/api/admin.py` 等）仍有效 ——
> 惟 `app/api/admin.py` 已於 2026-10-03 拆成 `app/api/admin/` 套件，見
> `docs/archive/STRUCTURE_REVIEW.md` R1。

---

## 0. 全域結論：現狀與缺口的落差

9 個頁面（`admin-web/web/src/app/Shell.tsx` 的 `NAV` + `App.tsx` 的 `buildRouter()`）：

| 頁面 | 路由 | 已接的 API |
|---|---|---|
| 總覽 | `/` | 由多個端點組合的 badge 計數 |
| 司機審核 | `/kyc` | `GET /admin/drivers`, `POST /admin/drivers/{id}/review` |
| 的士證審核 | `/licences` | `GET/POST /admin/licences*` |
| 退款 | `/refunds` | `GET /admin/refunds`, `POST /admin/refunds/{id}/decision` |
| 每週結算 | `/settlement` | `POST /admin/settlement/weekly/run` |
| 車隊 | `/fleets`, `/fleets/:id` | `GET/POST /admin/fleets*` |
| 表現分析 | `/analytics` | `GET /admin/analytics`, `GET /admin/analytics/heatmap`, `GET /admin/analytics/operations`, `GET /admin/analytics/supply` |
| 司機詳情 | `/drivers/:id` | `GET /admin/drivers/{id}` |

**缺口與現狀**：三個「地基缺」現已**全部修復**（更正 2026-10-02）；下表保留當時落差。

| 缺口 | 當時問題 | 現狀（已實作） |
|---|---|---|
| A 稽核覆蓋 | 只有登入類 event（`EV_LOGIN`、`EV_LOGIN_FAILED`、`EV_LOCKED`、`EV_TOTP_VERIFY`、`EV_TOTP_FAILED`、`EV_TOTP_ENROLLED`、`EV_RECOVERY_USED`、`EV_RECOVERY_REGENERATED`）；KYC／保證金 grant／adjust／退款／結算／車隊等動作零稽核。 | `app/api/admin/`（原 `admin.py`，已拆包）有 **17 個 `record_audit` 調用點**、13 個事件（見 §7）；動錢與動狀態動作皆留稽核列（`payload` 欄見 migration `a1c4e8b7f209`）；搜尋刻意不逐次審計。 |
| B 無 RBAC | `AdminAccount`（`app/models/admin.py:108`）無 role；`require_admin`（`app/core/deps.py:324`）為二元判斷；`_admin_out`（`app/api/admin_auth.py:414`）不回角色；`Shell.tsx` 的 `NAV` 無 role 條件。 | `AdminAccount.role`（`app/models/admin.py:108`）儲存等級；四級 `AdminRole`（`SUPPORT < OPERATIONS < FINANCE < SUPER_ADMIN`）以 **rank 比較**作 gate（`require_role`，`app/core/deps.py:324`），每 request 重讀 live row，不信 token 的 `admin_role` claim。 |
| C 無爭議載體 | 全庫無 support ticket / dispute / complaint 表；投訴無 SLA、指派、狀態流。 | `OrderDispute` + `DisputeMessage`（`app/models/dispute.py`）已建表；`app/api/admin/`（原 `admin.py`）有 11 處爭議處理，含 SLA、指派、狀態流與裁決。 |

`AdminAuditLog`（`app/models/admin.py:281`）：append-only、`admin_id` 可為 NULL、
記 IP / user-agent、`created_at` 有索引、應用層無 UPDATE/DELETE 路徑。

---

## 1. 使用者與司機管理

**現況**：`GET /api/v1/admin/drivers`（分頁，`status` 過濾）；`GET
/api/v1/admin/drivers/{id}`（單次取齊：檔案、`hk_id_last4` 遮罩、保證金連
`shortfall_hkd`、ledger 近 50 筆、退款近 20 筆、車隊）；`POST
/api/v1/admin/drivers/{id}/review`（approve / reject / suspend / terminate，經 `assert_driver_transition`）。**缺口**：乘客側缺席、列表無搜尋、決定無稽核、無批次操作。

### 設計方案

1. 主體搜尋 `GET /admin/search?q=`：前綴匹配 `users.phone_e164`、
   `users.display_name`、`driver_profiles.vehicle_reg_mark`、
   `driver_profiles.taxi_driver_plate_no`。
2. 乘客頁 `GET /admin/passengers`、`GET /admin/passengers/{id}`（行程史、取消率、
   投訴）；停權用 `users.is_active`（已存在；`get_current_user` / `require_active_user` 逐 request 讀 live row）。
3. 批次 KYC `POST /admin/drivers/review/bulk`，body `{ids: [], decision}`；逐筆
   稽核，不可 aggregate。

---

## 2. 訂單監控

**現況**：**幾乎沒有**——無訂單頁面、無訂單 API（`app/api/orders.py` 全乘客／司機視角）。`Order` 有 `accepted_at`、`driver_arrived_at`、`completed_at`、
`cancelled_at`、`cancellation_reason`，狀態機 `ORDER_TRANSITIONS` 完整。**缺口**：
乘客來電說司機無出現，後台答不出狀態、司機、車位置、接單時間。

### 設計方案

1. `GET /admin/orders`：過濾 `status` / `date range` / `driver_id` /
   `passenger_id`，分頁；回 id、狀態、乘客、司機、上落客點、時間戳、車資。
2. `GET /admin/orders/{id}`：時間軸 + 車資明細（重用 `FareBreakdown`）+ ledger
   entries + 中斷／爭議。
3. Live Ops 地圖：`driver_profiles.current_location`（`geography(POINT,4326)`）＋
   `is_online` 已有 → `GET /admin/orders/active` 回 in-flight 單司機位置，Leaflet。**不開 WebSocket**：`TripHub` 有 channel 但 lifecycle event 從未 publish（`docs/DEVELOPMENT.md` §3、`mobile/README.md:135`）；5s 輪詢（`docs/REALTIME_POSITION_COST.md`）。
4. SLA 告警（接單超 10 分鐘未到達、in-trip 超 90 分鐘）用 badge
   （`badges.pendingKyc` / `pendingRefunds` 先例）。

---

## 3. 財務結算

**現況**：`POST /admin/settlement/weekly/run?period=YYYY-Wnn`（ISO 週結算，
**冪等**：`reference = weekly:<period>`）；`POST /admin/drivers/{id}/deposit/grant`
（`reference_for_grant` server-side namespace，令 client key 無法碰撞 weekly /
refund）；`POST /admin/drivers/{id}/deposit/adjust`（±HK$5,000，`reason` **必填**
`min_length=3`：*"an adjustment has no upstream event to point at, so the reason is
the audit trail"*）；`LedgerEntry` append-only，有 `UniqueConstraint("id","created_at")`。
**缺口**：無對帳視圖；結算無試算；無匯出；無發票／收據；結算執行無稽核。

### 設計方案

1. `GET /admin/settlement/preview?period=`：同計算但**不寫 ledger**，回「N 司機
   被扣、合計 HK$X、M 個負結餘」；**`run_weekly` 前必經**。
2. `POST /admin/settlement/weekly/run` 加 `confirm_token`（preview 回簽名 token，
   run 帶回）。
3. `GET /admin/settlement/export.csv?period=`：逐筆匯出，OTel 友善。
4. 車隊對帳 `GET /admin/fleets/{id}/statement?period=`；`FleetMembership` 有
   `weekly_fee_discount_percent`。

---

## 4. 爭議處理

**現況**：`RefundRequest` + `POST /admin/refunds/{id}/decision` 是**司機退保證金**，
非乘客投訴；`orders.cancellation_reason` 自由文字。**當時無 dispute / complaint
實體**（已修復，見缺口 C）。

### 設計方案

> **與 P4 協調**：`docs/IN_TRIP_REDESIGN.md` §4.4 的 `order_disputes` 與此**是同一
> 張表，不可開兩張**；以 `order_disputes` 為基礎，`order_id` 可為 NULL（非訂單類
> 投訴），P4 版本是其訂單相關子集。

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

配套：`dispute_message`（`is_internal` 旗標——客服內部討論不讓當事人見）；`GET/POST /admin/disputes*`、`POST /admin/disputes/{id}/assign`、
`POST /admin/disputes/{id}/resolve`；**自動開單**：`orders.status` 變 `INTERRUPTED`
時同一 transaction 內開 dispute（見 IN_TRIP_REDESIGN §3.3）；**`resolution` 必填、
無 default**；**`SAFETY_CRITICAL` 自動觸發停權候選、不自動停**（須真人確認）；前端 `/disputes` 預設 SLA 快到期優先。

---

## 5. 權限分級（RBAC）

**現況**：**沒有**（見缺口 B，已修復）。為其他模組前置條件。

### 設計方案

**角色模型（四級，最高級可管理所有）**。**修訂（依用戶 2026-10-01）：無實習生
角色；新增最高級別，可管理所有。**

| 角色 | 定位 | 可以看 | 可以改 |
|---|---|---|---|
| `SUPPORT`（客服） | 前線 | 工單、訂單、司機唯讀 | 回覆工單、改工單指派與狀態；**不可**動錢、不可批 KYC、不可改他人帳號 |
| `OPERATIONS`（營運） | 審核 + 處理爭議 | 全部 | KYC 裁決、司機停權、處理 dispute 裁決；**不可**動錢額度（grant / adjust / settlement） |
| `FINANCE`（財務） | 金流 | 全部 | settlement run、grant、adjust、refund decision、dispute 的財務結果；**可**做 OPERATIONS 職責（rank 制：FINANCE ⊇ OPERATIONS），不可改角色 |
| `SUPER_ADMIN`（最高管理員） | **可以管理所有** | 全部 | **全部**，外加：建立 / 修改 / 停用 admin 帳號、指派與變更任何人的角色、管理系統設定（費率、週費、罰款比例） |

**為何四級**：`SUPPORT` / `OPERATIONS` 必須分開（客服回答 vs 營運決定；KYC 是上線合規判斷）；`FINANCE` / `OPERATIONS` 用 **rank 階層**（2026-10-05 owner 決定：接受 hierarchy，FINANCE ⊇ OPERATIONS，唔再當佢哋係橫向互斥）；只有 dispute-resolution 呢一個 endpoint 用 decision-matched whitelist 防止 assigned judge 同時批 payout。**只有 `SUPER_ADMIN` 可改角色**，否則可自我提權（**RBAC 唯一死穴，須由架構守住**）。

**實作**：

1. `admin_accounts` 加 `role`（`String(16)`，預設 `SUPPORT` 非 `SUPER_ADMIN`）；
   migration + backfill **全設 `SUPER_ADMIN`**（否則升級死鎖），⚠️ 完成後立即人手
   調低多餘帳號。
2. `issue_admin_access_token` 放 `admin_role` claim；但 **`require_admin` 讀 live
   DB row 不信 claim**（`deps.py` *"trust the DB"*，見 `require_active_user` 對
   `row.role != user.role`）。降權即刻生效。
3. `require_role(*roles)` 工廠掛動錢端點：

   ```python
   # 概念示意。SUPER_ADMIN 不一定出現在每個 tuple——用「高於」判斷
   # （rank >= required）比逐個列舉更難漏。
   require_finance     = require_role(AdminRole.FINANCE)       # rank >= FINANCE
   require_operations  = require_role(AdminRole.OPERATIONS)
   require_super       = require_role(AdminRole.SUPER_ADMIN)
   ```

   用 rank 比較（`if user.admin_rank < required_rank: reject`）非 set（漏 tuple 即開放路由）；現有 `Depends(require_admin)` 不變，只寫入端點加嚴（加法）。
4. `PATCH /admin/accounts/{id}/role`：僅 `SUPER_ADMIN`；不可改自己；不可令系統無
   `SUPER_ADMIN`（最後一個不可降級）；每次寫 `AdminAuditLog`，`detail` 記
   `from` / `to`。四條同一約束：令管理權限不被清空。
5. **雙人覆核**：`refund/decision` approve、`settlement/weekly/run` 需第二個 admin
   以不同帳號確認（`pending_approval` + `approval_request`，或 run 帶
   `approved_by: <other_admin_id>` 並校驗不同）。**先做 role 再做雙人覆核**（獨立 feature）。
6. 前端：`Shell.tsx` 的 `NAV` 改 `NAV.filter(item => canSee(item, role))`；
   `_admin_out` 加 `role`；`AppContext` 記 role，`<RequireRole>` 包裹動錢按鈕
   （**UI 隱藏 ≠ 授權**）。

---

## 6. 數據分析儀表板

**現況**：`GET /admin/analytics`（`day`/`week`/`month` 分桶收入，可過濾
`taxi_type`、可排序；上限 1096 日（三年），超出 422）；`GET /admin/analytics/heatmap`
（一日 24 小時收入分佈）；`GET /admin/analytics/operations`（營運漏斗：建立／已接單
／完成／取消／中斷／進行中，取消歸因乘客／司機／超時／未能歸類，平均接單與到達耗時）；
`GET /admin/analytics/supply`（實時供應快照：活躍司機、上線司機、GPS 廣播、
未完成訂單、接單中司機、可接單司機與供需比，可按 `taxi_type` 過濾）。
收入／漏斗**由 `orders` 派生**，供應側由 `driver_profiles` + `orders` 派生、無新表；
邊界是**香港時間日界**（`_hk_day_bounds`）。**剩餘缺口**：無地理分佈。

### 尚未實作（餘下）

1. 地理分佈：`orders.pickup_location`（`geography(POINT,4326)`）可 `ST_SnapToGrid`
   分格。**地圖須用合規圖源，不可拼湊邊界。**
2. 儀表板自訂：**不建議**（固定版面優於自拉 widget）。

---

## 7. 稽核日誌

**現況**：表在、設計好、**當時只有登入在用**（見缺口 A，已修復）。

### 設計方案

1. 抽 `app/services/admin/audit_service.py` 的
   `record_audit(session, event, outcome, actor, detail, request)`
   （原 `AdminAuthService.audit()`，`app/services/admin/admin_auth_service.py:177`）。
2. 補動錢 event 常數（加法，無需 migration——表設計是 string 非 enum）：

   ```
   EV_KYC_DECISION, EV_DEPOSIT_GRANT, EV_DEPOSIT_ADJUST,
   EV_REFUND_DECISION, EV_SETTLEMENT_RUN, EV_FLEET_UPSERT,
   EV_ADMIN_ACCOUNT_CREATE, EV_ADMIN_ROLE_CHANGE, EV_DISPUTE_RESOLVE
   ```
   已實作另含 `EV_SETTLEMENT_PREVIEW`、`EV_DISPUTE_CREATE`／`_MESSAGE`／`_ASSIGN`／
   `_RESOLVE`、`EV_ADMIN_PASSWORD_RESET`（共 13 事件、17 調用點）。
3. `detail` 由 `String(255)` 擴闊：加 `payload JSONB nullable` 存
   `{"before": ..., "after": ...}`；需 migration（`a1c4e8b7f209`）。
4. `GET /admin/audit`：過濾 `event` / `admin_id` / 日期；索引已備（`event`、
   `admin_id`、`created_at`）。
5. 不可變性：現靠應用層無寫路徑（對有 DB 權限者無效）；加 DB trigger 拒 UPDATE/DELETE 或 `REVOKE UPDATE, DELETE`。**縱深防禦。**

---

## 8. 風險控管

**現況**：登入節流 `LOGIN_IP_LIMIT=30/15min`、`TOTP_IP_LIMIT=20/15min`、帳號鎖定
（`AdminAccountLocked` 子類）；Token 吊銷 epoch（`token_revocation`）；退款 approve
有 `refund:{id}` 唯一 reference 防連點。**缺口**：無行為風控（同一 admin 5 分鐘改
30 筆保證金無反應）；無金額上限告警（`adjust` 有 ±5000 上限但**累計**無上限）；
無司機側（高頻取消）、乘客側（假單／重複帳號）風控。

### 設計方案

1. 輕量規則引擎：Redis 滑動窗口——`admin:<id>:money_ops`（超閾值寫 `EV_RISK_ALERT`
   稽核 row + 推 badge）、`driver:<id>:cancel_rate`、`passenger:<id>:order_rate`。
2. 審批門檻：超標**自動轉待覆核**非直接拒絕（拒絕會被繞過）。
3. 連動工單：高風險自動開 `order_disputes`（`category=OTHER`, `severity=HIGH`）。

---

## 9. 客服工單系統

**現況**：**沒有**（見缺口 C，已修復）。

### 設計方案

與 §4 **共用同一實體**（拆兩表客服不知開哪張）。

- `order_disputes` 就是工單（`category` 分性質、`severity` 分輕重）；`dispute_message`
  就是對話（含 `is_internal` 內部備註）。
- **SLA** 由 `severity` 定 `sla_due_at`：`SAFETY_CRITICAL` = 1 小時、`HIGH` = 4 小時、
  `NORMAL` = 24 小時、`LOW` = 72 小時。**逾期不阻塞任何人**（依 P4）；**不做阻塞閘**。
- 處理 dispute 需 `OPERATIONS` 或以上；裁決涉金錢需 `FINANCE` 或以上（見 §5）。
- 前端 `/disputes` 列表（預設 SLA 到期升序）+ 詳情頁；由訂單詳情可開單並帶入
  `order_id` / 雙方 id。

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

> **與 P4 的交叉依賴**：P4（`docs/IN_TRIP_REDESIGN.md`）的中斷會自動開 dispute，
> 所以 ④ 必須在 P4 之前或同步完成。表結構以 `IN_TRIP_REDESIGN.md` §3.3 為準。

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

- **`GET /admin/drivers/{id}` 的單次來回組合**——註釋已解釋「五次呼叫可以渲染出
  一個半真半假的司機」，此原則應推廣到訂單詳情與工單詳情。
- **`ADJUSTMENT` 的 `reason` 必填**——理由就是稽核軌跡，不可以變 optional。
- **`reference_for_*` 的 server-side namespace**——防止 client key 碰撞，新增任何
  動錢端點都要跟。
- **`money_str()` 統一 2 位小數**——不要在新頁面手寫 `str(amount)`。
- **analytics 全部由 `orders` 派生**——不要為了快而建 rollup 表，除非有實測證明慢。
