# In-Trip 業務邏輯重新設計 / In-Trip Redesign

> **EN — Summary.** A redesign proposal for the in-trip phase (P4), not yet
> implemented. It widens the order state machine beyond today's `IN_TRIP →
> COMPLETED` dead end, adding `DESTINATION_CHANGED` (non-terminal) and
> `INTERRUPTED` (terminal), plus a per-trip platform fee and the rules for
> when a driver may refuse or end a trip.
>
> **Read this before editing `ORDER_TRANSITIONS`.** The state machine has two
> invariants asserted by tests — terminal states have no outgoing edges, and
> `CANCELLED` is unreachable once a trip is under way. `INTERRUPTED` is
> terminal while `DESTINATION_CHANGED` is not, so both must be re-checked when
> this lands. The state machine's own docstring flags this.
>
> **中文摘要**：這是 **P4 in-trip 重新設計的提案，尚未實作**。它把訂單狀態機由現時
> `IN_TRIP → COMPLETED` 的死巷擴闊，加入 `DESTINATION_CHANGED`（非終態）與
> `INTERRUPTED`（終態）。**改動 `ORDER_TRANSITIONS` 之前務必先讀**——
> 狀態機有兩條由測試守住的不變式，加入這兩個狀態時必須重新檢查。

> 本文回應需求：「行程出發後即可由平台扣費（每趟 $5）；in-trip 階段的 cancel
> 與 change destination 區分狀態；司機接單後原則上不得違約，除非乘客態度惡劣、
> 嘔吐等原因；新增『中斷行程』status 按鈕，提供撞車、發生衝突等原因 options，
> 雙方皆可提出，中斷申請統一交由 admin 處理。」
>
> 涵蓋：資料庫 schema、API 設計、狀態機、前端互動流程。
>
> **本文把需要你做產品 / 法律決定的地方全部集中在 §9，明確標示為 DECISION。**
> **七個 DECISION 全部已由你拍板**（含第四輪：保證金閘門、違約即時扣款 +
> 15 分鐘冷靜期、預約 2 小時至 3 天、地標清單補至 19 個即用）。
> 其餘部分為可直接實作的設計。
>
> **第四輪新增/修訂摘要：**
> - `PENDING_ARRIVAL_CONFIRM` 取代 5 分鐘鎖定窗（到達 = GPS + 乘客電話尾 4 位）。
> - 新增預約服務（地標導向）+ 司機 filter（§3.6）。
> - 違約**即時扣款 + 15 分鐘冷靜期**（原本「待裁決」已廢）。
> - 保證金 < 0 → **不能接新單**（423，不改 `DriverStatus`）。
> - 19 個即用地標（含深圳灣口岸），座標**全部經
>   `is_in_hong_kong()` 驗證**（發現 2 個邊界陷阱，均見 §9 DECISION-7）。
>
> **第六輪新增/修訂摘要：**
> - 預約單**可見性是一條狀態規則，不是時間窗口** —— 由 `broadcast_lead_time`
>   開始一直可見，直到（a）被司機接走 或（b）到達出發時間（→ 升級即時廣播）。
>   `broadcast_lead_time` 的語意是「**多早曝光**」，不是「**曝光多久**」。
> - 保證金預警門檻確認 **$100**；**補款後不即時解鎖，需人工放行** ——
>   接單閘門改為讀「人工放行標記」而非讀餘額（見 DECISION-3）。
> - 深圳灣口岸落客點確定為**港方口岸區公共運輸交匯處**
>   （`22.500992, 113.945654`）。該座標**曾被邊界多邊形誤判為境外**
>   （真實缺陷）；**第七輪已按選項 A 修正 `_HK_MAIN` 並補測試**，
>   港方口岸區現判為境內，蛇口 / 南山 / 前海仍為境外（見 DECISION-7）。
>
> **座標覆核用的完整清單（含 Google Maps 連結）：
> `docs/LANDMARK_COORDINATES.md`。**

---

## 1. 現狀（實測，非推測）

### 1.1 狀態機

`app/services/state_machine.py`：

```python
ORDER_TRANSITIONS = {
    CREATED:          {BROADCASTING, CANCELLED},
    BROADCASTING:     {ACCEPTED, CANCELLED},
    ACCEPTED:         {DRIVER_ARRIVED, CANCELLED},
    DRIVER_ARRIVED:   {IN_TRIP, CANCELLED},
    IN_TRIP:          {COMPLETED},          # ← 只能完成
    COMPLETED:        set(),
    CANCELLED:        set(),
}
```

### 1.2 現有端點（`app/api/orders.py`）

| 端點 | 誰可呼叫 | 現在做什麼 |
|---|---|---|
| `POST /{id}/grab` | ACTIVE 司機 | 搶單（`GrabService`，Redis 原子） |
| `POST /{id}/arrive` | 受指派司機 | `→ DRIVER_ARRIVED` |
| `POST /{id}/start` | 受指派司機 | `→ IN_TRIP` |
| `POST /{id}/complete` | 受指派司機 | `→ COMPLETED` |
| `POST /{id}/cancel` | 雙方 | `→ CANCELLED`；司機在 ACCEPTED / DRIVER_ARRIVED 階段取消會被扣 `no_show_penalty_hkd`（預設 HK$50，`PENALTY_DEDUCTION`） |

取消是**單一 Payload**：`CancelIn { reason: str = "" }` —— 只有一段自由文字，
沒有結構化的原因分類，`reason` 也**不經任何驗證**就寫進
`orders.cancellation_reason`。

### 1.3 三個現存缺口（與本需求直接相關）

**(a) `IN_TRIP` 是死胡同 —— 只能向前完成。**
`IN_TRIP: {COMPLETED}`。途中撞車、乘客中途要求改目的地、乘客願意落車而司機同時
在路邊等客，全部都無法表達。實際上系統會逼營運同事「照按 complete」然後私下處理，
即是帳目與現實脫節。

**(b) 平台從未向「行程」收費。**
現行收入模型是**每週服務費**（`weekly_fee_hkd = 200`，`WEEKLY_FEE_DEDUCTION`）。
`LedgerEntryType` 只有 DEPOSIT_TOPUP / WEEKLY_FEE_DEDUCTION / PENALTY_DEDUCTION /
REFUND / ADJUSTMENT —— **沒有「按趟收費」這個 type**。

**(c) 乘客端沒有付款工具。**
`DriverDeposit` 只覆蓋司機；乘客 `User` 沒有錢包、沒有綁卡、沒有信用額。
`fare_calculator.py` 的免責聲明明確寫：

> 「車費估價僅供參考。平台僅屬資訊中介，最終車資由乘客與司機自願協商確認
> （香港法例第374D章）。」

即是**車資不經平台**，是司機收現金 / 自己收款。所以「$5 平台費」不可能是
「由車資抽成」—— 平台沒有收到過車資。這點是 §9 的 DECISION 之一。

---

## 2. 目標狀態機（重新設計）

### 2.1 新增狀態

```python
class OrderStatus(str, enum.Enum):
    CREATED = "CREATED"
    BROADCASTING = "BROADCASTING"
    ACCEPTED = "ACCEPTED"
    # 司機聲稱到達，GPS 已通過，等乘客核對電話尾 4 位。
    PENDING_ARRIVAL_CONFIRM = "PENDING_ARRIVAL_CONFIRM"
    DRIVER_ARRIVED = "DRIVER_ARRIVED"
    IN_TRIP = "IN_TRIP"
    # --- 新增 ---
    DESTINATION_CHANGED = "DESTINATION_CHANGED"  # 行程中改目的地（非終態，回落 IN_TRIP）
    INTERRUPTED = "INTERRUPTED"              # 行程提前結束（終態，即時生效，不等 admin）
    # --- 原有終態 ---
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
```

> **修訂（依用戶第二輪澄清）**：原本設計有 `INTERRUPT_PENDING`（等 admin 裁決）
> 這個中間態，**已刪除**。理由：
>
> - 改地點**不涉及 admin** —— 行程已經開始、平台已經收到費用，改地點只是重新
>   估價，行程繼續。
> - 中斷**即時生效**，雙方都**不需要等 admin**。行程真的出事（撞車、衝突），
>   要人立即停下，不可以要求他等 15 分鐘等一個 admin 上線。
> - admin 只在**事後**介入 —— 處理 dispute、判斷款項誰屬（見 §4.4）。
>
> 這個修訂令整個設計**更簡單**（少一個狀態、少一個凍結期），亦更符合現實：
> 一個乘客在撞車後需要的是「我不想繼續」，不是「我提交了申請」。

> **修訂（依用戶第三輪）**：新增 `PENDING_ARRIVAL_CONFIRM`。
> 司機按「已到達」不再直接轉 `DRIVER_ARRIVED`，而是先進這個「待乘客核對」態。
> 詳見 §5.1。

**設計取捨說明：**

| 狀態 | 為什麼需要 | 為什麼不是別的 |
|---|---|---|
| `PENDING_ARRIVAL_CONFIRM` | 「到達」必須雙重驗證（GPS + 乘客尾 4 位）。這個態就是「GPS 過了、等乘客核對」。 | **不能跳過**：若司機一按就等於到達，司機就可以在 500 米外單方面剝奪乘客的取消權。 |
| `DESTINATION_CHANGED` | 需求明確要求「cancel 與 change destination 區分狀態」。 | **非常態（non-terminal）**：改目的地之後行程繼續，所以它會轉回 `IN_TRIP`。**不涉及 admin。** |
| `INTERRUPTED` | 行程提前結束，必須與 `COMPLETED` 分開，否則收入報表會把「沒有行完的單」算成完成單。**終態，即時生效。** | 不重用 `CANCELLED`：取消是「未出發」相關，中斷是「已出發但提前結束」，理賠、結算、統計三者都不同。 |

> **核心不變式：`CANCELLED` 不再從任何已驗證到達之後的狀態可達。**
> 到達（`DRIVER_ARRIVED`）之後就沒有「取消」，只有「中斷」。
> 這個區分令一切非正常結束都留下結構化的原因（`interruption_reason`），
> 供 admin **事後**判決 —— 而不是行程中卡住等人。

### 2.2 新轉移表

```python
ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    CREATED:            {BROADCASTING, CANCELLED},
    BROADCASTING:       {ACCEPTED, CANCELLED},
    ACCEPTED: {
        PENDING_ARRIVAL_CONFIRM,   # 司機按「已到達」，GPS 驗證通過
        CANCELLED,                 # 可取消，但已構成違約（見 §5.2）
    },
    # 等乘客核對電話尾 4 位。到達未被證實 -> 用違約金而非硬鎖。
    PENDING_ARRIVAL_CONFIRM: {
        DRIVER_ARRIVED,            # 核對成功
        ACCEPTED,                  # 核對失敗 3 次 / 乘客否認 -> 回 ACCEPTED + 開 dispute
        CANCELLED,                 # 可取消，但已構成違約（見 §5.2）
    },
    # --- 到達已證實：取消權在此鎖定 ---
    DRIVER_ARRIVED: {
        IN_TRIP,
        INTERRUPTED,               # 未出發就出事（例如上車後發現乘客醉酒）
    },
    # --- in-trip ---
    IN_TRIP: {
        COMPLETED,
        DESTINATION_CHANGED,
        INTERRUPTED,          # 即時，不經 admin
    },
    # 改目的地：行程繼續，所以一定會轉回 IN_TRIP
    DESTINATION_CHANGED: {
        IN_TRIP,
        COMPLETED,               # 改完即刻到達，是可能的
        INTERRUPTED,             # 改目的地途中都可以出事
    },
    INTERRUPTED:   set(),        # 終態
    COMPLETED:     set(),        # 終態
    CANCELLED:     set(),        # 終態
}
```

**不變式（應寫成 property-based test，不只是一個 dict）：**

1. `CANCELLED` 的前驅**只能**是 `CREATED` / `BROADCASTING` / `ACCEPTED` /
   `PENDING_ARRIVAL_CONFIRM`。**沒有任何 `DRIVER_ARRIVED` 或之後的狀態可以走到
   `CANCELLED`。**
2. 三個終態 `{COMPLETED, INTERRUPTED, CANCELLED}` 的出度為零。
3. 「有 `completed_at`」的所有單，狀態必為 `COMPLETED`。
4. **`DRIVER_ARRIVED` 必定有 `arrival_confirmed_at`**（因為到達必須經雙重
   驗證）—— 測試可直接斷言這個不變式。


### 2.3 狀態圖

```
  CREATED ──> BROADCASTING ──> ACCEPTED ──> PENDING_ARRIVAL_CONFIRM ──> DRIVER_ARRIVED ──> IN_TRIP
     │             │             │                    │                     │              │
     └─────────────┴─────────────┴────────────────────┘                     │              │
                    (全部可 CANCELLED —— 到達未證實前)                       │              │
                                │                                          │              │
                    ┌───────────┘                                          │              │
                    v  核對失敗 3 次 / 乘客否認 -> 回 ACCEPTED + 開 dispute   │              │
                 ACCEPTED                                                    │              │
                                                                            │              │
                        ┌───────────────────────────────────────────────────┴──────────────┤
                        v                        v                                         v
                INTERRUPTED ●            COMPLETED ●                          DESTINATION_CHANGED
                (即時，不等 admin)                                                  │    │
                        ^                                                           │    │
                        │                                                           │    └──> COMPLETED ●
                        └───────────────────────────────────────────────────────────┘
                           （改完回到 IN_TRIP 繼續；可再改、可中斷）

  ● = 終態（出度為零）
  DRIVER_ARRIVED 之後：CANCELLED 不可達 —— 只有 COMPLETED / INTERRUPTED。
  admin 不在路徑上 —— 它只在事後處理 dispute，不阻塞任何轉移。
```

---

## 3. 資料庫 Schema 變更

### 3.1 `orders` 表新增欄位

```sql
ALTER TABLE orders
  -- 出發時間。IN_TRIP 的起點，$5 平台費的計費基準，也是「行程時長」的起點。
  ADD COLUMN started_at               timestamptz,
  -- 平台費快照。為什麼要存快照而不是查 config：一個歷史訂單的金額，
  -- 不應該因為未來改 config 而改變（與 tariff_version 的理由一樣）。
  ADD COLUMN platform_fee_hkd         numeric(10,2),
  ADD COLUMN platform_fee_charged_at  timestamptz,
  -- 改目的地：保留原始目的地，不覆蓋，令「實際行程 vs 原先行程」可審計。
  ADD COLUMN original_dropoff_address text,
  ADD COLUMN original_dropoff_location geography(POINT, 4326),
  ADD COLUMN destination_changed_at   timestamptz,
  ADD COLUMN destination_change_count smallint NOT NULL DEFAULT 0,
  -- 中斷：現行 cancellation_reason 是自由文字，中斷需要結構化原因。
  ADD COLUMN interruption_reason      varchar(32),   -- enum, 見 3.2
  ADD COLUMN interrupted_at           timestamptz,
  ADD COLUMN interrupted_by_kind      varchar(16),   -- 'passenger' | 'driver'
  -- 到達驗證（見 §5.1）：到達不再是司機單方面說了算。
  ADD COLUMN arrival_claimed_at       timestamptz,   -- 司機按「已到達」的時間
  ADD COLUMN arrival_gps_distance_m   numeric(7,1),  -- 按鈕當時與上車點的距離
  ADD COLUMN arrival_confirmed_at     timestamptz,   -- 乘客核對尾 4 位成功的時間
  ADD COLUMN arrival_pin_attempts     smallint NOT NULL DEFAULT 0;
```

> **為什麼要存 `arrival_gps_distance_m`？** 這不是用來即時判斷（即時判斷只看
> 是否 ≤ 半徑），而是**事後爭議時的證據**。「司機聲稱到達時距離 480 米」
> 是一個可以擺在 admin 面前的數字；只存一個 boolean「通過/不通過」
> 則無法回答「他當時有多遠」。
>
> **為什麼要存 `arrival_claimed_at` 而非重用 `driver_arrived_at`？**
> 兩者是不同的事件：前者是「司機說他到了」（可能被拒），
> 後者是「到達已證實」。若混為一談，「司機按了 5 次才成功」這個事實就消失了。
> `driver_arrived_at` 現在應該只在 `arrival_confirmed_at` 有值時才寫。

> **為什麼 `original_dropoff_*` 而不是建 `order_destination_changes` 子表？**
> 若只改一次（絕大多數情況），子表是殺雞用牛刀。若需要完整改動史，
> 就在同一張 migration 加子表（見 §3.3 的 `order_events`）—— 兩者不衝突：
> 欄位供報表做 `WHERE destination_change_count > 0` 的快速過濾，
> 子表供審計做完整時間軸。

### 3.2 新增 enum

```python
class InterruptionReason(str, enum.Enum):
    """中斷原因。刻意用 enum 而不是自由文字：
    這是分派給 admin 的判決依據，需要可統計、可分派規則。"""

    ACCIDENT = "ACCIDENT"                  # 撞車 / 交通意外
    CONFLICT = "CONFLICT"                  # 與對方發生衝突
    PASSENGER_MISCONDUCT = "PASSENGER_MISCONDUCT"  # 乘客態度惡劣
    PASSENGER_SICK = "PASSENGER_SICK"      # 乘客嘔吐 / 身體不適
    VEHICLE_BREAKDOWN = "VEHICLE_BREAKDOWN"    # 車輛故障
    UNSAFE_ROUTE = "UNSAFE_ROUTE"          # 路況 / 路線安全問題
    FARE_DISPUTE = "FARE_DISPUTE"          # 車資爭議
    OTHER = "OTHER"                        # 必須附文字說明
```

> **與需求的對應**：需求提到的「乘客態度惡劣」對應 `PASSENGER_MISCONDUCT`、
> 「嘔吐」對應 `PASSENGER_SICK`、「撞車」對應 `ACCIDENT`、「發生衝突」對應
> `CONFLICT`。另外補了 `VEHICLE_BREAKDOWN` / `UNSAFE_ROUTE` /
> `FARE_DISPUTE` —— 因為「司機接單後原則上不得違約」的例外，現實中還包括車
> 壞了、路真的走不通，這些不處理就會變成司機硬著頭皮開不安全的車。

### 3.3 新增 `order_disputes` 表（**事後**處理，非阻塞）

> **修訂**：原本的 `order_interrupt_requests` 表（附 partial unique index 保證
> 同時只有一個 PENDING）**已不需要** —— 因為中斷不再經審批。取而代之的是一張
> **事後** dispute 表：中斷即時生效，admin 之後才判斷錢誰屬。

```sql
CREATE TABLE order_disputes (
    id              uuid PRIMARY KEY,
    order_id        uuid NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    -- 誰提出對什麼不滿。與 interruption_reason 分開：前者是「為何結束」，
    -- 後者是「為何不服」—— 兩者可以不同（乘客因病中斷，但對車資不服）。
    raised_by_kind  varchar(16) NOT NULL,   -- 'passenger' | 'driver' | 'admin' | 'system'
    raised_by_id    uuid,
    -- 自動開單的來源：某個 reason_code，或 'AUTO_INTERRUPTED'
    source          varchar(32) NOT NULL,
    against_kind    varchar(16),            -- 'passenger' | 'driver' | 'platform'
    status          varchar(16) NOT NULL DEFAULT 'OPEN',
                    -- OPEN | INVESTIGATING | RESOLVED | ESCALATED | CLOSED
    assigned_admin_id uuid REFERENCES admin_accounts(id),   -- 可為 NULL，未指派
    -- 裁決：錢誰屬。寫成明確欄位而不是從 ledger 反推 —— 反推會令
    -- 「未裁決」與「裁決為 NONE」無法區分。
    resolution      varchar(24),   -- 'NONE' | 'CHARGE_PASSENGER' | 'CHARGE_DRIVER'
                                   -- | 'REFUND_PLATFORM_FEE' | 'WAIVED_PLATFORM_FEE'
    resolved_by     uuid REFERENCES admin_accounts(id),
    resolved_at     timestamptz,
    resolution_note text,
    sla_due_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- 一張單可以有多個 dispute（乘客 + 司機各提一個），但已解決的不應該重複開
CREATE INDEX ix_disputes_open
    ON order_disputes (status, sla_due_at)
    WHERE status IN ('OPEN', 'INVESTIGATING', 'ESCALATED');

CREATE INDEX ix_disputes_order ON order_disputes (order_id);
```

**觸發規則（自動開單，不靠人記得開）：**

| 觸發 | `source` | 預設 `against_kind` |
|---|---|---|
| 訂單變 `INTERRUPTED` | `AUTO_INTERRUPTED` | 由 `interrupted_by_kind` 的**對方**決定 |
| 訂單變 `INTERRUPTED`，`reason` 屬安全類 | `AUTO_INTERRUPTED_SAFETY` | 同上，但 `sla_due_at` = 1 小時 |
| 乘客 / 司機主動申訴 | `PARTY_REPORT` | 由申訴者指定 |
| admin 手動開單 | `ADMIN_CREATED` | 由 admin 指定 |

> **為什麼自動開單？** 中斷之後「誰是誰非」需要有人判。如果靠當事人自己
> 去開單，出事那一刻人人都忙著處理現場，之後就沒有人記得。**由系統在
> `INTERRUPTED` 的同一 transaction 內開單**，保證每一宗中斷都有一張有 owner、
> 有 SLA 的記錄。

### 3.4 新增 `order_events` 表（可選，但建議）

```sql
CREATE TABLE order_events (
    id          bigserial PRIMARY KEY,
    order_id    uuid NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    event       varchar(32) NOT NULL,   -- STATE_CHANGED | DEST_CHANGED | FEE_CHARGED | ...
    from_status varchar(24),
    to_status   varchar(24),
    actor_kind  varchar(16),            -- 'passenger' | 'driver' | 'admin' | 'system'
    actor_id    uuid,
    payload     jsonb,                  -- 前後值、舊/新目的地、金額
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_order_events_order_time ON order_events (order_id, created_at);
```

**這張表解決什麼問題？** 現在一張有爭議的行程，要重建發生過什麼，只能靠
`orders` 的最終欄位值 + 一堆 log line。有了事件表，「這張單 14:03 改過目的地、
14:20 司機按了中斷（撞車）、14:21 系統自動開 dispute」是一條可讀的時間軸。
**這與 `AdminAuditLog` 是不同層**：audit log 答「哪個 admin 做了什麼」，
order_events 答「這張單發生過什麼」。兩者都應該有。

### 3.5 Ledger 新增 entry type

```python
class LedgerEntryType(str, enum.Enum):
    DEPOSIT_TOPUP = "DEPOSIT_TOPUP"
    WEEKLY_FEE_DEDUCTION = "WEEKLY_FEE_DEDUCTION"
    PENALTY_DEDUCTION = "PENALTY_DEDUCTION"
    REFUND = "REFUND"
    ADJUSTMENT = "ADJUSTMENT"
    PLATFORM_TRIP_FEE = "PLATFORM_TRIP_FEE"        # 新增：每趟 $5，行程出發即扣
    CANCELLATION_PENALTY = "CANCELLATION_PENALTY"  # 新增：違約罰款（到達前取消 / 到達後違約）
    DISPUTE_ADJUSTMENT = "DISPUTE_ADJUSTMENT"      # 新增：dispute 裁決的加收 / 退還
```

> `native_enum=False`，所以加 enum member **不需要** migration 去改 DB type
> —— 只需確認欄位長度夠（`SAEnum` 預設 VARCHAR(30)，最長的
> `CANCELLATION_PENALTY` 是 20 字元，安全）。
>
> **`PLATFORM_TRIP_FEE` 的收款方式已拍板，見 §9 DECISION-1。**
> **`CANCELLATION_PENALTY` 是即時扣款（不等裁決），見 §9 DECISION-5。**

### 3.6 預約服務（Pre-booking）所需欄位與表

> **修訂（依用戶第三輪）**：原本的「5 分鐘取消鎖定」欄位
> （`cancellation_locked_at` / `no_show_penalty_hkd_snapshot`）**已刪除** ——
> 因為取消鎖定改為由「已驗證到達」觸發（見 §5.1），不再需要一個時間窗口。
>
> 而用戶同時提出**加入預約服務**（尤其是前往地標的訂單），
> 這正好補上了原本缺失的「出發時間」概念。**預約服務是本節的新增內容。**

#### 3.6.1 為什麼預約服務對這個業務特別有價值

> **修訂（依用戶第五輪，重要）**：**地標是「終點 / 下客點」，不是上客點。**
> 顯示方式：司機看單時，**地圖上直接標出終點地標**，令司機一眼就知
> 「這張單去機場 / 去迪士尼」，可以快速判斷接不接。
> **完全不考慮上客 —— 上客點永遠是乘客即時的實際位置。**
>
> 這一點簡化了整個設計：
> - `landmarks` **不需要 `pickup_point` 欄位**（見 §3.6.3）。
> - 邊境口岸的「港方上車點」問題**不再存在** —— 因為只做**終點顯示**，
>   而去口岸的乘客是由香港市區上車、**落客在口岸附近**。
>   所以口岸的 `location` 用**港方實際可落客的位置**（見 §9 DECISION-7）；
>   若直接用口岸大樓的中心座標，`require_in_hong_kong` 會在**建立訂單時**
>   就 422 拒絕（實測：深圳灣原座標曾被判為境外，現已修正；文錦渡原座標仍為境外，
>   故改用紅橋新村文錦渡路的港方車道路段）。
> - 司機 filter 的分類，本質是「**我想去哪類終點**」——
>   機場單的價值是「回程可能有單」，迪士尼單的價值是「閉園時段定了」，
>   兩者的**營運模式**不同，所以值得分開 filter。

即時叫車的下單高峰是「我現在就要走」。而**機場、口岸、主題公園**這類行程
有三個特性，令預約比即時更有價值：

| 特性 | 說明 |
|---|---|
| **終點高度可預測** | 去「機場」的單，乘客幾乎必然是趕航班；去「迪士尼」的，幾乎必然是閉園時段離開。 |
| **時間可預測** | 航班起飛時間、樂園閉園時間都是已知的。 |
| **司機可以提前規劃** | 一個司機可以在早上決定「我今晚 21:00 做一單去迪士尼」，而不需要一直開著 App 等。 |

而**司機可以 filter 這類單**（用戶明確要求）—— 這是預約服務真正的賣點：
即時單是「誰先看到誰搶」，預約單是「誰先規劃誰得」。這改變了司機的
工作模式，由「守株待兔」變成「排班」。

> **地標在司機端是「目的地標記」** —— 司機看到「→ 香港國際機場」
> 比看到「→ 22.3126, 113.9173」有意義得多。所以 `landmarks` 的核心價值
> 是**把座標翻譯成司機認得的目的地**，同時提供 filter 的分類維度。

#### 3.6.2 `orders` 新增欄位

```sql
ALTER TABLE orders
  -- 訂單類型。即時單是預設；SCHEDULED 表示這是一張預約單。
  ADD COLUMN order_kind            varchar(16) NOT NULL DEFAULT 'ON_DEMAND',
                                   -- 'ON_DEMAND' | 'SCHEDULED'
  -- 預約出發時間。對 ON_DEMAND 單必為 NULL。
  ADD COLUMN scheduled_pickup_at   timestamptz,
  -- 預約單的廣播窗口：提早多久開始讓司機看到並搶單。
  ADD COLUMN prebook_visible_from  timestamptz,
  -- 目的地地標（見 3.6.4）。指向 landmarks 表，可為 NULL（自由輸入的目的地）。
  ADD COLUMN dropoff_landmark_id   uuid REFERENCES landmarks(id) ON DELETE SET NULL,
  -- 預約單的狀態：'PENDING'（未開始廣播）| 'BROADCASTING' | 'MATCHED'
  -- 用獨立欄位而不是塞進 OrderStatus：預約的「未開始」是一個**時間**概念，
  -- 而 OrderStatus 是**流程**概念。混在一起會令狀態機多出一個只在預約單
  -- 才合理的狀態，污染所有即時單的邏輯。
  ADD COLUMN prebook_state         varchar(16);
```

> **為什麼 `prebook_state` 不進 `OrderStatus`？** 一個 `CREATED` 的預約單
> 與一個 `CREATED` 的即時單，在**流程上**是同一個位置（都是「未廣播」）。
> 差別只在**時間**：預約單要等到 `prebook_visible_from` 才開始廣播。
> 用一個獨立的欄位 + 一個背景 job 在到點時把 `OrderStatus` 由 `CREATED`
> 推到 `BROADCASTING`，就保留了 `OrderStatus` 的純粹性。
> 若把 `PREBOOK_PENDING` 塞進 `OrderStatus`，每一個既有的狀態檢查
> （例如 `if status == CREATED`）都要重新檢視，風險大而收益是零。

#### 3.6.3 `landmarks` 表（地標 = **終點 / 下客點**）

> **修訂（依用戶第五輪）**：`landmarks` 只服務**終點顯示**與**司機 filter**。
> 因此**移除 `pickup_point`** —— 上客點永遠是乘客即時的實際位置，
> 不會、也不需要用地標表示。詳見 §3.6.1。

```sql
CREATE TABLE landmarks (
    id           uuid PRIMARY KEY,
    code         varchar(32) NOT NULL UNIQUE,   -- 'HKIA', 'DISNEYLAND', 'ICC' …
    name_en      varchar(120) NOT NULL,
    name_zh      varchar(120) NOT NULL,
    -- 分類令司機能 filter：'AIRPORT' | 'BORDER' | 'THEME_PARK' | 'MALL'
    -- | 'OFFICE' | 'WATERFRONT' | 'VENUE' | 'HOSPITAL' | 'OTHER'
    category     varchar(24) NOT NULL,
    -- 終點座標。用「司機能實際停車落客」的位置，不一定是建築物幾何中心。
    -- 必須落在 is_in_hong_kong() 內，否則建單即 422。
    location     geography(POINT, 4326) NOT NULL,
    -- 地理圍欄半徑（米）：判斷「終點是否就是這個地標」用這個。
    -- 例如乘客把終點拖到機場附近 200 米內，就直接標成「→ 香港國際機場」。
    radius_m     integer NOT NULL DEFAULT 300,
    is_active    boolean NOT NULL DEFAULT true,   -- 座標未覆核完的一律 false
    sort_order   integer NOT NULL DEFAULT 100,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_landmarks_location ON landmarks USING gist (location);
CREATE INDEX ix_landmarks_category ON landmarks (category) WHERE is_active;
```

> **`location` 是「落客點」而不是「地標中心」。** 兩者通常很接近，
> 但對機場 / 迪士尼 / 紅館這類地方，**的士落客區**可能在幾百米外。
> 寧可填落客區，不要填建築物中心 —— 因為司機看地圖是要**開車去**，
> 不是去打卡。**具體落客座標仍需人手覆核**（見 §9 DECISION-7）。

**初始資料：19 個即用地標（含深圳灣口岸，見 §9 DECISION-7），
座標全部經 `is_in_hong_kong()` 驗證。**

| `code` | 名稱 | `category` | 座標 | `radius_m` | `inHK` |
|---|---|---|---|---|---|
| `HKIA` | 香港國際機場 | `AIRPORT` | 22.312599, 113.917300 | 800 | ✅ |
| `ASIAWORLD` | 亞洲國際博覽館 | `VENUE` | 22.321251, 113.942968 | 400 | ✅ |
| `DISNEYLAND` | 香港迪士尼樂園 | `THEME_PARK` | 22.313070, 114.040985 | 600 | ✅ |
| `OCEAN_PARK` | 海洋公園 | `THEME_PARK` | 22.234767, 114.170817 | 600 | ✅ |
| `ICC` | 環球貿易廣場 | `OFFICE` | 22.303379, 114.160226 | 200 | ✅ |
| `IFC` | 國際金融中心 | `OFFICE` | 22.285163, 114.159815 | 200 | ✅ |
| `HK_CONVENTION` | 香港會議展覽中心 | `VENUE` | 22.282625, 114.173069 | 300 | ✅ |
| `HARBOUR_CITY` | 海港城 | `MALL` | 22.297002, 114.168420 | 300 | ✅ |
| `TIMES_SQ` | 時代廣場 | `MALL` | 22.278359, 114.182106 | 200 | ✅ |
| `TST_PROMENADE` | 尖沙咀海旁 | `WATERFRONT` | 22.299419, 114.185648 | 500 | ✅ |
| `KT_PROMENADE` | 觀塘海旁 | `WATERFRONT` | 22.312288, 114.217369 | 400 | ✅ |
| `HK_COLISEUM` | 香港體育館（紅館） | `VENUE` | 22.301318, 114.181981 | 300 | ✅ |
| `HZMB_PORT` | 港珠澳大橋香港口岸 | `BORDER` | 22.317868, 113.954070 | 500 | ✅ |
| `LOK_MA_CHAU` | 落馬洲支線管制站 | `BORDER` | 22.515276, 114.065632 | 400 | ✅ |
| `LO_WU` | 羅湖管制站 | `BORDER` | 22.529713, 114.113850 | 300 | ✅ |
| `QMH` | 瑪麗醫院 | `HOSPITAL` | 22.269875, 114.131214 | 300 | ✅ |
| `PWH` | 威爾斯親王醫院 | `HOSPITAL` | 22.379609, 114.202193 | 300 | ✅ |
| `MAN_KAM_TO` | 文錦渡管制站 | `BORDER` | 22.519218, 114.124584 | 300 | ✅ |
| `SHENZHEN_BAY` | 深圳灣口岸（港方口岸區）公共運輸交匯處 | `BORDER` | 22.500992, 113.945654 | 400 | ✅ |

> **口岸類地標的定位已隨第五輪修訂而改變。** 因為地標現在是**終點**，
> 「港方上車點」的概念**不再適用** —— 去口岸的乘客是在香港市區上車、
> **落客在口岸**。所以 `location` 就是**港方口岸區的落客點**。
>
> 邊界陷阱有兩個，現況如下：
> - `SHENZHEN_BAY`（深圳灣口岸）—— 用戶已指定落客點為**港方口岸區的
>   公共運輸交匯處**（`22.500992, 113.945654`）。該座標**曾被
>   `is_in_hong_kong()` 拒絕** —— 不是座標選錯，是邊界多邊形在后海灣一段
>   精度不足。**第七輪已修正多邊形**（詳見 §9 DECISION-7 與
>   `docs/LANDMARK_COORDINATES.md` §二），現已判為境內。
> - `MAN_KAM_TO`（文錦渡）管制站在**邊界北側**，港方車道路段
>   （紅橋新村文錦渡路，22.519218, 114.124584）通過檢查，
>   可作香港境內終點。
> - **皇崗 / 落馬洲口岸完全在深圳境內**，不會也不需要成為香港終點。
>
> **結論**：口岸類地標只收錄**香港境內有實際落客點**的口岸。
> 深圳灣的落客點已確定為港方口岸區公共運輸交匯處，邊界判定**已跟上**
> （第七輪修好 `_HK_MAIN`）。

> ⚠️ **`location` 的座標仍需人手覆核落客位置（不是建築物中心）。**
> `HKIA` 應填**的士落客區**、迪士尼用**的士上落客區**、紅館用**暢運道對出**。
> 我填入的是社群地圖的地標中心，**作為開發用途足夠，上線前必須覆核** ——
> 因為司機看地圖是要**開車去落客**，座標偏幾百米就會走錯入口。

#### 3.6.4 預約單的流程

```
乘客選擇「預約」
  → 選起點（乘客的實際位置或自訂地址 —— **不是地標**）
  → 選終點（地標，或自由輸入地址）
  → 選時間（scheduled_pickup_at）
  → 建立訂單：order_kind = SCHEDULED, status = CREATED,
              prebook_state = 'PENDING'
              prebook_visible_from = scheduled_pickup_at - broadcast_lead_time

背景 job（每分鐘掃一次，或 Redis 定時）
  → 找到 prebook_visible_from <= now 且 prebook_state = 'PENDING' 的單
  → status: CREATED → BROADCASTING, prebook_state = 'BROADCASTING'
  → 寫入 GEO_ORDERS_KEY（令司機的 nearby 查詢找到它）
  → 通知符合 filter 的司機

**可見性（第六輪修正 —— 這是一條狀態規則，不是時間窗口）**
  → 由 prebook_visible_from 開始，**一直可見**
  → 直到以下任一事件發生才結束：
      (a) 有司機搶到（→ ACCEPTED, prebook_state='MATCHED'）
      (b) 到達 scheduled_pickup_at 仍無人接（→ 升級為即時廣播）
  → **不要實作「廣播 N 分鐘後收回」** —— 那會令乘客在到點前沒有車

司機看單（關鍵：地圖顯示）
  → 即時單：上車點 pin + 終點 pin
  → 預約單：**終點地標以圖示 + 名稱顯示**（「→ 香港國際機場」），
    令司機一眼辨識，不需要自己看座標猜
  → 符合司機 filter 分類的，會主動推送

司機搶單
  → 同一套 GrabService（Redis 原子）—— **不需要新機制**
  → status: BROADCASTING → ACCEPTED, prebook_state = 'MATCHED'
```

> **司機看單時見到的是「終點地標」** —— 這是整個功能的介面核心。
> 一張「→ 香港國際機場，21:30」的預約單，對一個想尋找長途單的司機，
> 價值遠高於一個座標。**地標是把座標翻譯成可判斷的資訊。**

**參數（已由用戶在 DECISION-6 拍板）：**

| 參數 | **值** | 理由 |
|---|---|---|
| `broadcast_lead_time` | **30 分鐘** | **提前量**：到 `scheduled_pickup_at - 30min` 開始對司機可見。**這不是窗口長度** —— 開始之後一直可見，直到被接走或到點 |
| 最短預約提前量 | **2 小時** | 少於 2 小時 → 422 `TOO_SOON`，引導走即時單 |
| 最長預約提前量 | **3 天** | 超過 3 天 → 422 `TOO_FAR` |
| 可見性終止條件 (a) | 有司機搶到 → 對其他司機立即消失 | 與即時單的 `GrabService` 一致（Redis 原子） |
| 可見性終止條件 (b) | 到 `scheduled_pickup_at` 仍未有人搶 → 自動轉為即時廣播（加大半徑） | 否則乘客會靜靜地沒有車 |

> **`broadcast_lead_time` 的語意（第六輪釐清）**：它是「**多早開始曝光**」，
> 不是「**曝光多久**」。我上一輪把它讀成後者，所以誤以為 30 分鐘太短
> 而建議調到 45–60 分鐘 —— 那個疑問的前提是錯的，見 §9 DECISION-6。

> **「未匹配自動升級為即時廣播」是一個必須做的設計**，不是可選項。
> 若預約單到時間沒有人接就靜靜取消，乘客會在 21:00 站在迪士尼門口沒有車。
> 這比一開始就拒絕預約更糟 —— 至少拒絕是即時知道的。

#### 3.6.5 司機端的 filter（用戶明確要求）

```
司機 App「我的偏好」
  ┌────────────────────────────────────┐
  │ 我想接的預約單                       │
  │                                     │
  │ 目的地類型（對應 landmarks.category） │
  │  ☑ 機場                              │
  │  ☑ 口岸 / 邊境                        │
  │  ☑ 主題公園                          │
  │  ☑ 場館（紅館、會展、亞博）           │
  │  ☐ 商場                              │
  │  ☐ 寫字樓                            │
  │  ☑ 海旁 / 地標                       │
  │  ☐ 醫院                              │
  │  ☐ 其他                              │
  │                                     │
  │ 我通常在哪區開始                      │
  │  [ 觀塘 ▾ ]                         │
  │                                     │
  │ 我可以接的時段                        │
  │  [ 06:00 ] 至 [ 10:00 ]             │
  │                                     │
  │  [ 儲存 ]                            │
  └────────────────────────────────────┘
```

> **Filter 的 checkbox 與 `landmarks.category` 是 1:1 對應** ——
> 不要另設一套 UI 專用分類，否則兩邊會靜靜漂移。
> UI 只是把 9 個 enum 值渲染成中文標籤。
>
> **Filter 篩選的是「終點類別」**（我想去哪類地方），
> 不是「上客位置」—— 因為地標系統中沒有上客語意（見 §3.6.1 第五輪修訂）。

**實作方式：** 新增 `driver_booking_preferences` 表：

```sql
CREATE TABLE driver_booking_preferences (
    driver_profile_id uuid PRIMARY KEY REFERENCES driver_profiles(id) ON DELETE CASCADE,
    -- 想接的「終點」地標分類（array 而不是多行，因為這是純過濾條件，沒有獨立生命週期）
    categories        varchar(24)[] NOT NULL DEFAULT '{}',
    -- 司機想「由哪一區開始」接單。這是**自由文字的地區名**，不是地標 ——
    -- 因為地標是終點，司機的起點偏好是「我通常在觀塘開工」這種區位概念。
    preferred_origin_area varchar(64),
    -- 可接的時段（每日，當地時間）
    available_from    time,
    available_until   time,
    updated_at        timestamptz NOT NULL DEFAULT now()
);
```

> **修訂（第五輪）**：原本的 `preferred_origin_landmark_id` 已改為
> `preferred_origin_area`（自由文字 / 地區 enum）。
> 原因：**地標是終點**，用終點地標做「起點偏好」在語意上是錯的。
> 「我通常在觀塘開工」的正確表達是一個**地區**，不是一個地標。
> （若日後要結構化，可改成 `districts` 對照表，但現階段自由文字足夠。）

**通知邏輯：** 背景 job 推送新預約單時，只推送給符合 filter 的司機。
**但不要把 filter 做成硬性限制** —— 司機仍然可以在「附近訂單」看到所有單。
Filter 的用途是**主動通知**，不是**封鎖可見性**。
理由：一個司機設了「只接機場單」，但今天沒有機場單，
他還是應該看到其他單 —— 否則他會兩邊都接不到。

> ⚠️ **`varchar[]` 陣列的取捨**：Postgres 原生支援，查詢用 `categories && ARRAY['AIRPORT']`
> （`&&` 是「有交集」）。若未來需要「每個分類有不同設定」，
> 就要改成子表。**現在用陣列是對的**，因為目前只需要一個布林過濾。



---

## 4. API 設計

### 4.0 到達驗證（取代既有的 `/arrive`）

既有端點 `POST /{order_id}/arrive` 只做一件事：
`transition(order, DRIVER_ARRIVED)`。**這個端點要被拆成兩步。**

#### 4.0.1 司機聲稱到達

```
POST /api/v1/orders/{order_id}/arrival-claim
     Auth: 受指派司機
     Body: { driver_lat: float, driver_lng: float }   # 可選
```

**服務邏輯：**

1. 校驗 `order.status == ACCEPTED`，否則 409。
2. **GPS 驗證**（見 §5.1.2）：
   ```sql
   SELECT ST_Distance(
       dp.current_location,
       o.pickup_location
   ) AS distance_m
   FROM driver_profiles dp, orders o
   WHERE dp.id = :driver_id AND o.id = :order_id
   ```
   - `current_location IS NULL` → 422 `{"reason": "NO_LOCATION"}`
   - `distance_m > ARRIVAL_RADIUS_M` → 422 `{"reason": "TOO_FAR", "distance_m": …}`
3. GPS 通過 → 寫 `arrival_claimed_at` / `arrival_gps_distance_m`，
   `transition(order, PENDING_ARRIVAL_CONFIRM)`。
4. 通知乘客（**必須即時推送**，見 §7）。
5. 響應回 `{"status": "PENDING_ARRIVAL_CONFIRM", "distance_m": 42.5}`。

> **`driver_lat` / `driver_lng` 為什麼是 body 而非直接用 DB 的
> `current_location`？** 兩者都應該支援：
> - 用 body 的座標 = 「此刻的位置」（最準，因為 `current_location`
>   可能是幾秒前上報的）。
> - 但 **body 的座標必須仍然通過 `require_in_hong_kong` 校驗**，
>   且**不可信**（司機可以傳任何值）。
>
> **所以建議：body 座標只用作「提升精度」，最終判斷用 DB 的
> `current_location`** —— 因為 DB 的值是經過 WebSocket 驗證路徑寫入的，
> 而 body 的值是單次請求。**若兩者差異過大（> 500 米），
> 直接拒絕並記錄** —— 這通常意味著司機在嘗試偽造。

#### 4.0.2 乘客核對尾 4 位

```
POST /api/v1/orders/{order_id}/arrival-confirm
     Auth: 乘客（必須是 order.passenger_id）
     Body: { phone_last4: "1234" }
```

**服務邏輯：**

1. 校驗 `order.status == PENDING_ARRIVAL_CONFIRM`，否則 409。
2. **比對 `order.passenger` 的 `phone_e164[-4:]`**（見 §5.1.3 的選項 A）。
   > 注意：這裡要讀**該訂單的乘客**的電話，不是呼叫者的 ——
   > 雖然通常相同，但在代叫車的情境下可能不同。
3. 成功 →
   - `transition(order, DRIVER_ARRIVED)`
   - 寫 `arrival_confirmed_at`、`driver_arrived_at`（**兩者同一次寫入**）
   - **取消權在此鎖定**
4. 失敗 → `arrival_pin_attempts += 1`
   - `< 3` → 401 `{"reason": "PIN_MISMATCH", "attempts_remaining": n}`
   - `== 3` → 回到 `ACCEPTED`，**開一張 `order_disputes`**
     （`source=ARRIVAL_CONFLICT`），通知雙方。
     > 3 次通常是司機按錯單或乘客上錯車，兩者都需要人處理。

**回應必須包含「知道了」的確認** —— 不要讓乘客提交後不知道發生什麼。
成功時應回 `{"status": "DRIVER_ARRIVED", "driver": {…}}`。

#### 4.0.3 預約單的建立

```
POST /api/v1/orders                        （既有端點，擴充 body）
     Body: {
       ...既有欄位,
       order_kind: "ON_DEMAND" | "SCHEDULED" = "ON_DEMAND",
       scheduled_pickup_at: datetime | null,      # SCHEDULED 時必填
       dropoff_landmark_id: uuid | null,
     }
```

**新增校驗（順序很重要）：**

1. `order_kind == "SCHEDULED"` 時：
   - `scheduled_pickup_at` 必填，否則 422。
   - `scheduled_pickup_at >= now() + 2h`，否則 422 `{"reason": "TOO_SOON"}`
     —— 少於 2 小時的「預約」應該走即時單（DECISION-6）。
   - `scheduled_pickup_at <= now() + 3d`，否則 422 `{"reason": "TOO_FAR"}`
     —— 更遠的預約等同無意義佔位（DECISION-6）。
2. `dropoff_landmark_id` 有值時：
   - 校驗地標存在且 `is_active`。
   - **終點以地標為準** —— 地標是結構化資料，自由文字不是。
     但要記錄差異（< 3.4 的 order_events），因為乘客可能輸入了更精確的地址。
   - **不要用地標反推上車點** —— 上車點永遠是乘客的實際位置（第五輪修訂）。
3. `prebook_visible_from = scheduled_pickup_at - broadcast_lead_time`（30 分鐘）。
4. `prebook_state = 'PENDING'`。

#### 4.0.4 地標查詢（公開，供乘客選終點與司機 filter）

```
GET /api/v1/landmarks?category=THEME_PARK,AIRPORT
     Auth: 任何已登入用戶
```

輕量端點，回傳 `code` / `name_en` / `name_zh` / `category` / `location`。
**地標是終點** —— 乘客用它選目的地，司機用它做 filter。
**應該有快取** —— 地標資料幾乎不變，每次旅程都查一次 DB 是浪費。
建議 `Cache-Control: public, max-age=3600` 加應用層 Redis 快取。

#### 4.0.5 司機預約偏好

```
GET  /api/v1/drivers/me/booking-preferences
PUT  /api/v1/drivers/me/booking-preferences
     Auth: ACTIVE 司機
     Body: {
       categories: ["AIRPORT", "THEME_PARK", "WATERFRONT"],   # 想去的終點類別
       preferred_origin_area: "觀塘",                          # 想由哪區開工（自由文字）
       available_from: "06:00",
       available_until: "10:00"
     }
```

> **這只影響主動通知，不影響可見性**（見 §3.6.5）。
> 端點名稱用 `booking-preferences` 而非 `filters`，因為它是**偏好**不是**限制**。
> `categories` 是**終點**類別 —— 地標在系統中沒有「上客」語意。

#### 4.0.6 接單 / 開單的兩道閘門（DECISION-3 + DECISION-5）

**兩個閘門是獨立的，都要通過才可接單 / 開單。順序：先冷靜期，後餘額。**

| 閘門 | 觸發 | 狀態碼 | `reason` | 誰受影響 |
|---|---|---|---|---|
| **冷靜期** | 上次違約未滿 15 分鐘 | **429** | `COOLDOWN` | 司機（`grab`）、乘客（`POST /orders`） |
| **保證金不足** | `balance + held < 0` | **423** | `DEPOSIT_INSUFFICIENT` | **只有司機**（乘客無保證金） |

```
POST /api/v1/orders/{order_id}/grab          （既有端點，加兩道前置檢查）
     Auth: ACTIVE 司機
     檢查順序：
       1. Redis GET cooldown:driver:{driver_id}
          → 存在 → 429 {"reason": "COOLDOWN", "retry_after_s": n}
                   + 響應 header `Retry-After: n`
       2. DriverDeposit.balance_hkd + held_hkd >= 0 ?
          → 否則 → 423 {"reason": "DEPOSIT_INSUFFICIENT", "balance_hkd": …}
       3. （既有）ACTIVE 狀態 + GrabService 原子搶單

POST /api/v1/orders                          （開新單，加一道前置檢查）
     Auth: 乘客
     檢查：Redis GET cooldown:passenger:{user_id}
          → 存在 → 429 {"reason": "COOLDOWN", "retry_after_s": n}
```

> **為什麼冷靜期回 429 而餘額回 423？**
> 429 是「**你現在不可以，但時間會解決**」—— 語意上是速率限制，附 `Retry-After`。
> 423 是「**資源被鎖，要你主動解鎖**」—— 司機要去補款。
> 兩者的**使用者行動完全不同**（等 vs 去補錢），所以不應共用狀態碼。
> **這與 §4.0.1 的 `NO_LOCATION` 回 422 是同一原則：狀態碼要承載語意，不只是對錯。**

> ⚠️ **檢查必須在 `GrabService` 之前。** 若放在搶單成功之後才檢查，
> 就會出現「搶到了但接不了」—— 單被鎖住而司機拿不到，其他司機也搶不到。
> **前置檢查是唯一正確的位置。**

### 4.1 改目的地

```
POST /api/v1/orders/{order_id}/change-destination
     Auth: 乘客 or 受指派司機
     Body: { dropoff_location: {lat, lng}, dropoff_address: str }
```

**服務邏輯（順序很重要）：**

1. 校驗 `order.status in {IN_TRIP, DESTINATION_CHANGED}`，否則 409。
2. 若 `destination_change_count == 0`：先把**現有** `dropoff_*` 抄到
   `original_dropoff_*`（只抄一次，不覆蓋，令「最初想去哪裡」永久可查）。
3. `assert_order_transition(status, DESTINATION_CHANGED)`。
4. 呼叫 `calculate_fare()` 用**新**距離重新估價，寫入 `orders.fare_json`
   的新版本（`is_estimate=True`），同時 `orders.estimated_total_hkd` 更新。
5. `destination_change_count += 1`，`destination_changed_at = now()`。
6. 寫 `order_events`（`DEST_CHANGED`，payload 含舊/新目的地）。
7. **響應**包含新的估價 —— 前端必須即刻顯示「新估價 HK$X，實際車資仍由你與
   司機協商」，不可以靜靜地改了這個數。

> **不變式**：`destination_change_count` 要有上限（建議 3）。
> 否則乘客可以無限次改目的地來規避取消費用或拖時間。超出即 429 並附
> 「請與司機直接溝通或提出中斷」。

### 4.2 中斷行程（**即時生效，不等 admin**）

```
POST /api/v1/orders/{order_id}/interrupt
     Auth: 乘客 or 受指派司機（雙方皆可，符合需求）
     Body: {
       reason_code: InterruptionReason,   # 必填
       note: str                          # reason_code == OTHER 時必填
     }
```

**服務邏輯（單一 transaction）：**

1. 校驗 `status in {IN_TRIP, DESTINATION_CHANGED}`，否則 409。
2. `assert_order_transition(status, INTERRUPTED)`，`orders.status = INTERRUPTED`。
3. 寫 `orders.interruption_reason` / `interrupted_at` / `interrupted_by_kind`。
4. **不再自動退 $5 —— 費用維持已收狀態。** 這是刻意的：若中斷即時退款，
   任何一方都可以用「中斷」規避平台費。**錢的調整交給事後 dispute 裁決**，
   這樣「即時結束」與「錢誰屬」兩件事被解耦 —— 前者不能等，後者必須查清。
5. **同一 transaction 內自動開一張 `order_disputes`**（`source=AUTO_INTERRUPTED`，
   或安全類原因則 `AUTO_INTERRUPTED_SAFETY`）。
6. 寫 `order_events`。
7. **發通知**：對方即時收到 + admin 佇列收到一張新 dispute。
   > 這裡必須用真實推送，不可以靠輪詢 —— 見 §7。
8. **響應即時回 `INTERRUPTED`** —— 沒有「等待中」狀態，沒有「請停靠等待」。

> **設計理由（依用戶澄清）**：行程真的出事，要人即刻停低。要求當事人
> 「提交申請然後等 admin 批」是把行政流程放在人身安全之上。
> **中斷是當事人的權利，不是需要批准的請求。**
> 而正因為即時生效，事後的裁決就必須更嚴謹 —— 這正是 dispute 表存在的理由。

**副作用：`interrupted_by_kind` 的對方會自動成為 dispute 的 `against_kind`。**
這是預設，不是定論 —— dispute 表可以再改（例如乘客因病中斷，事後才發現
真正問題是車況）。

### 4.3 完成行程（修訂既有端點）

```
POST /api/v1/orders/{order_id}/complete
     Auth: 受指派司機
```

**變更：**

1. 校驗 `status in {IN_TRIP, DESTINATION_CHANGED}`，否則 409。
2. 寫 `orders.completed_at`。
3. **$5 平台費在 `/start` 已扣**（見 §4.5），此處不再扣。
4. 寫 `order_events`。

### 4.4 Admin 事後處理 dispute

```
GET  /api/v1/admin/disputes?status=OPEN
GET  /api/v1/admin/disputes/{id}
POST /api/v1/admin/disputes/{id}/assign      # 指派給自己 / 他人
POST /api/v1/admin/disputes/{id}/resolve
     Auth: require_finance（會動錢，依 P3 的 RBAC）
     Body: {
       resolution: "NONE" | "CHARGE_PASSENGER" | "CHARGE_DRIVER"
                 | "REFUND_PLATFORM_FEE" | "WAIVED_PLATFORM_FEE",
       note: str        # 必填，理由就是稽核軌跡
     }
```

**服務邏輯：**

1. 校驗 `dispute.status in {OPEN, INVESTIGATING, ESCALATED}`。
2. `resolution` 的財務後果：

| `resolution` | 會計動作 |
|---|---|
| `NONE` | 錢不動（撞車屬正當，$5 照收） |
| `CHARGE_PASSENGER` | 向乘客收違約罰款（見 §6 的全額 / 50% 規則） |
| `CHARGE_DRIVER` | 從司機保證金扣（寫 `DISPUTE_ADJUSTMENT`） |
| `REFUND_PLATFORM_FEE` | 退還 $5（寫 `DISPUTE_ADJUSTMENT`，`+5`） |
| `WAIVED_PLATFORM_FEE` | $5 從未收 / 註銷 |

> **`resolution` 必須顯式選擇，不可以有 default。** 動錢的裁決，
> 「我沒有選擇所以跟 default」是不能接受的回答。與 `DepositAdjustIn.reason`
> 必填是同一個原則。

3. **全部寫 `AdminAuditLog`**（P3 的稽核擴充）—— 這是 admin 動錢的動作，
   今天完全沒有記錄。
4. 寫 `dispute.resolved_by` / `resolved_at` / `resolution_note`。

> **與 P3 的 dispute 表統一。** P3 §4 為後台設計了一張通用 dispute 表。
> 這裡的 `order_disputes` **就是同一張表**（訂單相關的 dispute），
> 不應開兩張。P3 的表若涵蓋非訂單類（例如 App 問題），則用 `order_id`
> 可為 NULL 來區分。**實作時務必只做一張表。**

### 4.5 出發（既有端點，新增扣費）

```
POST /api/v1/orders/{order_id}/start
```

現行 `order_start` 只有 `transition(order, IN_TRIP)`。新增：

1. `orders.started_at = now()`。
2. **扣 $5 平台費**：寫 `LedgerEntry`，`entry_type = PLATFORM_TRIP_FEE`，
   `amount_hkd = -5`，`driver_profile_id = 該司機`，
   `order_id = 該單`，**`reference = f"trip:{order_id}"`**。
   > `reference` 唯一 → 重複 `/start` 不會扣兩次。與 `weekly:<period>`
   > 同一模式（`uq_ledger_reference` partial unique index 已存在）。
3. 寫 `order_events`。


---

## 5. 司機不得違約的規則

需求：「司機接單後原則上不得違約，除非乘客態度惡劣、嘔吐這些原因。」
以及：「行程開始前 5 分鐘內就不能取消，雙方都不能。乘客違約要賠全額、
司機賠 50%。」

**現狀已部分覆蓋**：`order_cancel` 對 ACCEPTED / DRIVER_ARRIVED 的司機取消
扣 `no_show_penalty_hkd`（HK$50）。但：

- **`reason` 是自由文字，不驗證。** 司機打「乘客醉酒」就等於豁免，沒有人審。
- **`IN_TRIP` 的司機取消不可達**（狀態機擋死），所以 in-trip 違約的懲罰
  根本沒有路徑。
- **乘客一方完全沒有違約成本。** 乘客在司機到達後取消是免費的。

**重設計後的規則矩陣：**

| 階段 | 誰能單方結束 | 機制 | 財務後果 |
|---|---|---|---|
| BROADCASTING / ACCEPTED | 雙方 | `POST /cancel` | 免費 |
| **DRIVER_ARRIVED（已驗證到達）** | **雙方都不能取消** | 沒有路徑 | 見下 |
| IN_TRIP | 雙方 | `POST /interrupt` | 即時結束；錢由事後 dispute 判 |

### 5.1 到達驗證（新增）—— 取消鎖定的前提條件

> **修訂（依用戶 2026-10-01 第三輪）**：原本的「距出發 5 分鐘鎖定窗」
> **已取消**，因為系統沒有預計到達時間（ETA）可以計算。
> 改為：**司機「到達」不是司機自己說了算，而是必須通過兩重驗證**，
> 驗證通過才鎖定取消權。

**規則**：司機按「我已到達」時，必須同時滿足：

| # | 驗證 | 失敗後果 |
|---|---|---|
| 1 | **司機 GPS 接近上車點** | 拒絕，訂單維持 `ACCEPTED` |
| 2 | **乘客提供電話尾 4 位並確認** | 拒絕，訂單維持 `ACCEPTED` |

兩者**都通過**，訂單才轉 `DRIVER_ARRIVED`，`driver_arrived_at` 才寫入。
**在此之前，司機不算到達** —— 不計入「到達後不能取消」的鎖定，
亦不影響任何以到達為前提的規則。

#### 5.1.1 為什麼需要驗證（不是為了防作弊，是為了解鎖一個真實的兩難）

如果只靠司機單方面按鈕：

- **司機可以在 500 米外按「已到達」**，然後催乘客、或啟動等時計費。
- **乘客無法證明司機未到**，爭議變成各說各話。
- **一旦按了就不能取消** —— 那麼司機只要亂按，乘客就失去取消權，
  這是一個**司機單方面就能剝奪乘客權利**的路徑。必須堵。

所以「到達」這個狀態必須有**雙方以外的證據**（GPS）**加上雙方的確認**
（乘客的電話尾數），才能成立。

#### 5.1.2 GPS 驗證

```
司機按「我已到達」
  → 後端讀 driver_profiles.current_location（最後一次 GPS tick）
  → 計算與 orders.pickup_location 的距離
  → 距離 ≤ ARRIVAL_RADIUS_M（建議 150 米）→ 通過
  → 否則 422，回傳實際距離，前端顯示
     「你距離上車點約 800 米，請再接近」
```

**技術要點：**

- 距離用 **PostGIS** 在 DB 端算（`ST_Distance(current_location,
  pickup_location)`，兩者都已是 `geography(POINT,4326)`，
  距離直接以**米**為單位）—— 不要自己寫 haversine。
- **`current_location` 可能為 NULL**（司機從未上線過 / GPS 失敗）。
  這種情況**不能當作通過**，回 422 並要求司機重新開啟定位。
- **GPS 可能不準確**（高樓密集的香港尤其嚴重，誤差可達 50–100 米）。
  所以半徑要**寬鬆**（150 米而非 30 米），否則會不斷誤拒。
- **不要因為一次失敗就永久拒絕** —— 司機應該可以重新按（GPS 會更新）。

> ⚠️ **一個必須承認的限制**：`current_location` 是由司機的裝置上報的。
> 一個技術上足夠用心的司機可以偽造 GPS。**這層驗證防的是「隨手亂按」，
> 不是「堅決作弊」** —— 後者需要裝置驗證（attestation），
> 那是另一個量級的工作。**但配合第 2 層（乘客確認），
> 偽造 GPS 就沒有意義了** —— 因為乘客不會確認一個沒有出現的司機。

#### 5.1.3 電話尾 4 位驗證（傳統方法，用戶指定）

```
司機按「我已到達」（GPS 通過）
  → 訂單轉入 PENDING_ARRIVAL_CONFIRM（見下）
  → 乘客 App 彈出：「司機聲稱已到達。
                    請把您自己手機號碼的最後 4 位告訴司機，
                    並在下面輸入以確認您已上車。」
  → 乘客輸入 4 位 → 後端比對 `passenger.phone_e164[-4:]`
  → 相符 → DRIVER_ARRIVED，鎖定取消權
  → 不符（3 次）→ 回到 ACCEPTED，開一張 dispute
```

**用誰的電話尾數？** 這裡需要明確 —— 平台上有兩個電話，
`passenger.phone_e164` 與 `driver.user.phone_e164`。

| 選項 | 乘客要輸入 | 安全性 | 可用性 |
|---|---|---|---|
| **A. 輸入乘客自己的尾 4 位** | 乘客的號碼尾 4 位 | ✅ 司機不可能知道 | ⚠️ 乘客要想自己號碼（多數人記得） |
| **B. 輸入司機的尾 4 位** | 司機的號碼尾 4 位 | ❌ 司機自己知道，可以代按 | ⚠️ 乘客要問司機 |

**建議用 A（乘客自己的尾 4 位）**，理由：驗證的目的是「確認**這個乘客**
真的在場」。只有本人知道自己的號碼尾數，而司機無從得知 ——
所以司機能通過的唯一方式，就是**眼前真的有一個乘客**告訴他。
用 B 的話司機自己就能完成驗證，等於第 2 層不存在。

> 這正是傳統的士業的做法（乘客報尾數，司機核對），
> 所以乘客一聽就明白，不需要教育。

**實作要點：**

- **只存 hash，不存明文？** 不需要 —— 尾 4 位只有 10,000 種可能，
  hash 不增加任何安全性，反而令比對複雜。**直接比對 `phone_e164[-4:]`** 即可。
- **限 3 次嘗試**（Redis 計數器，per order）。超過 3 次：
  訂單回 `ACCEPTED`，自動開一張 `order_disputes`
  （`source=ARRIVAL_CONFLICT`）—— 這通常意味著司機按錯了單，
  或者乘客上錯了車，兩者都需要人處理。
- **乘客可主動確認** —— 也可以做一個「確認司機已在場」的按鈕
  （乘客按了就通過），但**輸入 4 位比按鈕好**：按鈕是零成本的，
  乘客可能亂按；輸入 4 位需要實質核對。
- **無障礙 / 特殊情況**：若乘客是聽障、或司機沒有乘客號碼
  （例如經第三方叫車），提供「我無法核對」的路徑 → 開 dispute 由 admin 處理。
  **不要讓驗證變成一個無法繞過的死結。**

#### 5.1.4 新狀態 `PENDING_ARRIVAL_CONFIRM`

```python
class OrderStatus(str, enum.Enum):
    ...
    ACCEPTED = "ACCEPTED"
    PENDING_ARRIVAL_CONFIRM = "PENDING_ARRIVAL_CONFIRM"  # 司機聲稱到達，待乘客核對
    DRIVER_ARRIVED = "DRIVER_ARRIVED"
    ...
```

轉移：

```python
ACCEPTED: {
    PENDING_ARRIVAL_CONFIRM,   # 司機按「已到達」，GPS 通過
    CANCELLED,
},
PENDING_ARRIVAL_CONFIRM: {
    DRIVER_ARRIVED,            # 乘客核對尾 4 位成功
    ACCEPTED,                  # 核對失敗 3 次 / 乘客否認 -> 回到 ACCEPTED 並開 dispute
    CANCELLED,                 # 可取消，但已構成違約（見 §5.2）
},
DRIVER_ARRIVED: {IN_TRIP, INTERRUPTED},  # 取消權在此鎖定
```

> **在 `PENDING_ARRIVAL_CONFIRM` 期間雙方仍可取消**，但**已構成違約**
> （與 `ACCEPTED` 同級，因為司機已經到了附近、成本已經產生）。
> 這是刻意的 —— 既然「到達」還未被證實，就不應該用強制力鎖定取消權；
> 但「未被證實到達」不等於「沒有成本」，所以用違約金而不是硬鎖。
> 只有 `DRIVER_ARRIVED`（已雙重驗證）才**硬鎖**取消權。

### 5.2 到達後不能取消

**規則**：訂單處於 `DRIVER_ARRIVED`（已驗證到達）時，**雙方都不能取消**。

| 情況 | 出路 |
|---|---|
| 乘客不想上車 | 不能取消；只能等司機開始行程後用 `POST /interrupt`，或由司機取消（司機取消會被罰） |
| 司機不想載 | 不能取消；只能 `POST /interrupt` |
| 任何一方認為被騙 | 開 dispute |

> ⚠️ **這裡有一個必須正視的設計問題。** 「到達後不能取消」+「in-trip
> 才有中斷」= 在 `DRIVER_ARRIVED` 這個狀態，**雙方都被鎖死**，
> 唯一出路是司機按「開始行程」。如果司機拒絕開始（例如發現乘客醉酒），
> 系統沒有出路。
>
> **兩個處理方式：**
> 1. **允許 `DRIVER_ARRIVED → IN_TRIP → INTERRUPTED`** —— 即司機先開始、
>    再立即中斷。流程上可行，但會產生一張「開始了 0 秒的行程」，
>    數據上難看。
> 2. **允許 `DRIVER_ARRIVED` 直接 `INTERRUPTED`**（加一條轉移），
>    語意是「在車上但未出發就出事」。**我建議這個** ——
>    它誠實反映現實（乘客上車後發現問題，車未開）。
>
> 我在 §2.2 的轉移表已按 (2) 加入。

**違約罰款**（在 `DRIVER_ARRIVED` 或之後，一方造成行程無實質進行）：

> 注意用詞：到達之後**沒有「取消」**（`CANCELLED` 不可達，見 §2.2 不變式 1）。
> 表格講的是**違約責任**，觸發事件可能是 `INTERRUPTED`（即時），
> 或者 admin 事後判定的違約（例如司機在 `DRIVER_ARRIVED` 後長時間不開車）。

| 違約方 | 罰款 | 冷靜期 |
|---|---|---|
| **乘客** | **全額**（100% `estimated_total_hkd`） | **15 分鐘內不能開新單** |
| **司機** | **50%** `estimated_total_hkd` | **15 分鐘內不能接新單** |

**計費基準是該單的估價快照**，不是定額 —— $300 的機場單與 $40 的短程單，
違約成本不可能一樣。現行的 `no_show_penalty_hkd = 50` 定額被取代。

> **已按 DECISION-5 改為「即時扣款 + 15 分鐘冷靜期」**（用戶第四輪拍板）。
> 原本設計是「寫入待裁決，由 admin 判」—— 現已改為**先扣**，
> 但**保留申訴入口**：admin 事後判「違約不成立」時，
> 寫 `DISPUTE_ADJUSTMENT` 退回已扣金額，並清除冷靜期。詳見 §9 DECISION-5。

#### 5.2.1 到達前的違約取消（`ACCEPTED` / `PENDING_ARRIVAL_CONFIRM`）

到達前**可以取消**（不硬鎖 —— 因為「到達」未被證實），但**不是免費**。
分界線是「司機是否已經付出成本」：

| 階段 | 司機成本 | 取消後果 |
|---|---|---|
| `CREATED` / `BROADCASTING` | 沒有（尚未有人接單） | **免費取消**，寫 `CANCELLED` |
| `ACCEPTED` / `PENDING_ARRIVAL_CONFIRM` | **有**（已接單、已出發、已在路上） | 取消**成立但構成違約**，按 §5.2 基準（乘客 100% / 司機 50%）**即時扣款 + 15 分鐘冷靜期** |

> **為什麼不硬鎖？** 由 `ACCEPTED` 到「到達」可能幾分鐘到十幾分鐘。
> 硬鎖等於禁止乘客在等車期間改變主意 —— 而乘客改變主意本身不是罪，
> 只是有代價。**用定價取代禁止**：想取消就付代價，不想付就等。
>
> **扣款與冷靜期都是即時的**（DECISION-5）：即時寫
> `CANCELLATION_PENALTY` ledger + 即時設 Redis 冷靜期 key。
> 申訴入口仍然開放，admin 推翻時退款並解鎖。

> **同時收緊 `ACCEPTED` 起的取消 `reason`：** 改成必填 `reason_code`
> （用 `InterruptionReason` enum）。因為由 `ACCEPTED` 起的取消已經有違約
> 後果，**一個不驗證的自由文字欄位就是違約判斷的繞過口**。
> 乘客醉酒 / 司機拒載等例外應可被審計，而不是打一行字就免罰。

**計費基準**：同 §5.2 —— 該單的估價快照，乘客 100%、司機 50%。


### 5.3 in-trip 的司機違約

| 階段 | 司機能否單方結束 | 財務後果 |
|---|---|---|
| IN_TRIP | 可以，用 `POST /interrupt` | 即時結束；由事後 dispute 判 |

**關鍵：in-trip 的是非曲直移交 admin 事後判斷，而不是即時阻擋。**
理由：in-trip 的爭議（究竟是乘客嘔吐還是司機想放棄訂單）無法用規則判斷，
強行自動化必然誤判。`orders.interrupted_by_kind='driver'` +
`interruption_reason` 就是交給 admin 的證據。若 admin 判 `CHARGE_DRIVER`，
寫 `DISPUTE_ADJUSTMENT`，金額由 admin 決定。

> **取消的三個階段對照**（詳見 §5.2）：
>
> - `CREATED` / `BROADCASTING` —— **免費取消**，直接 `CANCELLED`。
> - `ACCEPTED` / `PENDING_ARRIVAL_CONFIRM` —— 可取消但**已構成違約**，
>   **即時扣款 + 15 分鐘冷靜期**（DECISION-5）。
> - `DRIVER_ARRIVED` 或之後 —— **不可取消**，只能 `INTERRUPTED`（即時生效）。
>
> 分別在於「有沒有一個客觀事實（司機已到）做分界」。到達前是**時序**問題，
> 規則可以判斷；到達後是**是非**問題，規則判斷不到。
> **能自動化的自動化，不能的不要假裝可以。**

---

## 6. 前端互動流程

### 6.1 乘客端（`mobile/lib/features/passenger/trip_tracking_screen.dart`）

現有畫面已有「取消」按鈕。新設計：

```
┌─ 行程中 (IN_TRIP) ────────────────────────┐
│  司機已出發 · 預計 12 分鐘到達              │
│  [ 地圖 / 司機位置 ]                       │
│                                            │
│  車資估價 HK$86.00（僅供參考）              │
│  [ 改目的地 ]      [ 中斷行程 ]            │
└────────────────────────────────────────────┘

「改目的地」→ 地點搜尋 → 確認頁
   「新估價 HK$104.00（+HK$18.00）
    實際車資仍由你與司機協商（法例第374D章）」
   [ 確認更改 ]
   ※ 不經 admin，行程繼續

「中斷行程」→ 原因選單（必選）：
   ⚪ 交通意外 / 撞車
   ⚪ 與司機發生衝突
   ⚪ 司機態度惡劣
   ⚪ 車輛故障
   ⚪ 路線不安全
   ⚪ 車資爭議
   ⚪ 其他（請說明 —— 文字框必填）
   [ 確認中斷 ]
   ⚠️ 「中斷後行程即時結束。平台會稍後處理費用安排。」

中斷後 (INTERRUPTED)：行程結束畫面
   橫幅：「行程已結束。如對費用有疑問，可提出申訴。」
   [ 提出申訴 ]
```

**乘客核對畫面（新增，`PENDING_ARRIVAL_CONFIRM`）：**

```
┌─ 司機說他到了 ────────────────────────────┐
│                                            │
│  司機 陳大文 · 車牌 AB1234                 │
│  位置：距離上車點約 42 米                   │
│                                            │
│  請確認司機在場：                           │
│  輸入司機面前乘客的電話號碼最後 4 位         │
│                                            │
│  ┌────┬────┬────┬────┐                    │
│  │    │    │    │    │                    │
│  └────┴────┴────┴────┘                    │
│                                            │
│  [ 確認 ]                                  │
│                                            │
│  找不到司機？ [ 司機沒有出現 ]              │
└────────────────────────────────────────────┘

核對正確後 (DRIVER_ARRIVED)：
  「已確認司機在場。行程即將開始。」

核對失敗 3 次：
  「無法確認。我們已通知平台，客服會盡快聯絡你。
   你可以重新叫車，或等候跟進。」
```

> **為什麼乘客要輸入「自己的」號碼尾 4 位而非司機的？**
> 見 §5.1.3 —— 只有乘客本人知道自己的號碼，司機無從得知。
> 所以司機無法代替乘客完成這個動作，驗證才有意義。
>
> **輸入 4 位比一個「司機已在場」按鈕好**：按鈕是零成本的，
> 乘客可能隨手按；輸入 4 位需要實質核對（看向司機、問一句）。
> 這正是傳統的士業的做法，乘客一聽就明白。

**到達後的畫面（取消權已鎖定）：**

```
┌─ 司機已到達 ──────────────────────────────┐
│  司機 陳大文 · 車牌 AB1234                 │
│  ⚠️ 已確認到達，無法取消                   │
│     如無法乘車，請與司機溝通或提出申訴       │
│  [ 提出申訴 ]                              │
└────────────────────────────────────────────┘
```

> **不要再顯示一個灰色的「取消」按鈕。** 一個存在但按不了（或按了要付
> 全額）的按鈕比沒有按鈕更糟 —— 它引誘人按，然後懲罰他。
> 到達後的正確動作是給一條**出路**（申訴），而不是一個陷阱。

**為什麼「中斷」按鈕要放在「改目的地」旁邊而不是藏進選單？**
撞車的時候，人是在驚慌狀態。埋藏三層選單的功能在那種時刻等於不存在。
兩個動作都是「行程中途的重大變更」，放在同一排是合理的資訊層級。


### 6.2 司機端

**接單大廳（核心：終點地標一眼可見）—— 這是預約服務的介面重點：**

```
┌─ 附近訂單（預約）──────────────────────────┐
│  🏝 香港迪士尼樂園                21:30    │
│     距離 8.2 km · 預計 18 分鐘              │
│     ⏰ 30 分鐘後開始接單                    │
├────────────────────────────────────────────┤
│  ✈️  香港國際機場                 07:15    │
│     距離 32 km · 預計 40 分鐘               │
├────────────────────────────────────────────┤
│  🚧 港珠澳大橋香港口岸            09:00    │
│     距離 28 km · 預計 35 分鐘               │
└────────────────────────────────────────────┘

有別於即時單（顯示上車點 pin），
預約單**以終點地標為主標題**，令司機一眼辨識。
```

> **為甚麼預約單要以「終點地標」做主標題？**
> 因為預約單的價值在於**司機可以提前規劃**。一張
> 「→ 迪士尼 21:30」的單，司機可以判斷「我今晚可以做」；
> 一張只有座標的單，司機要自己查地圖才知道是哪裡。
> **地標就是把座標翻譯成可以立即決策的資訊。**
>
> 即時單仍然是「上車點優先」（因為司機要立即去接人）；
> 預約單是「終點優先」（因為司機要規劃行程）。
> **兩種單的顯示邏輯不同，是刻意的。**

**司機的預約偏好頁（見 §3.6.5）：**

```
「我的偏好」→「預約單」

  我想去哪類終點（對應 landmarks.category）
   ☑ 機場
   ☑ 口岸 / 邊境
   ☑ 主題公園
   ☑ 場館（紅館、會展、亞博）
   ☐ 商場
   ☐ 寫字樓
   ☑ 海旁 / 地標
   ☐ 醫院
   ☐ 其他

  我通常在哪區開始：[ 觀塘 ▾ ]
  可接時段：[ 06:00 ] 至 [ 10:00 ]

  [ 儲存 ]

  說明：以上設定只影響哪些預約單會主動通知你。
       你仍然可以在「附近訂單」看到所有訂單。
```

**中斷行程原因（司機版）：**

對稱設計，但原因選單**不同**：

```
「中斷行程」原因（司機版）：
   ⚪ 交通意外 / 撞車
   ⚪ 與乘客發生衝突
   ⚪ 乘客態度惡劣
   ⚪ 乘客嘔吐 / 嚴重不適      ← 需求明確點名
   ⚪ 車輛故障
   ⚪ 路線不安全

   ⚪ 車資爭議
   ⚪ 其他（請說明）
```

> **兩邊共用同一個 `InterruptionReason` enum**，但前端只顯示該角色合理的選項。
> 這樣做，DB 只有一個 enum，但 UI 不會出現「司機投訴自己態度惡劣」這種選項。
> 後端**仍然要校驗**（`interrupted_by_kind='driver'` 時拒絕
> `PASSENGER_MISCONDUCT` 這種「乘客的錯但司機提出」的不合理組合）——
> 前端過濾是禮貌，後端校驗是授權。

**司機「我已到達」按鈕（新增）：**

```
┌─ 前往上車點 ──────────────────────────────┐
│  上車點：中環 交易廣場                     │
│  [ 地圖 ]                                  │
│                                            │
│  ✅ GPS 已定位（距上車點 42 米）            │
│                                            │
│  [ 我已到達 ]                              │
└────────────────────────────────────────────┘

距離太遠時：
  ┌────────────────────────────────────────┐
  │  你距離上車點約 480 米                  │
  │  請再接近上車點                        │
  │  [ 重新定位 ]                          │
  └────────────────────────────────────────┘

GPS 不可用時：
  ┌────────────────────────────────────────┐
  │  無法取得你的位置                       │
  │  請確認已開啟定位權限                    │
  │  [ 開啟設定 ]                          │
  └────────────────────────────────────────┘
```

> **為什麼要顯示「距上車點 42 米」而不是只有一個按鈕？**
> 司機需要知道**為什麼**按鈕可用或不可用。一個沒有解釋的灰色按鈕會令
> 司機反覆按、然後打去客服。顯示距離讓司機自己判斷，是省客服成本的做法。
>
> **「重新定位」而不是只有「重試」** —— 措辭要指出是定位問題，
> 這樣司機才知道要移動或開啟 GPS，而不是單單再按一次。

**司機被鎖定時的畫面（DECISION-3 / DECISION-5）：**

```
保證金不足時（接單頁）：
  ┌────────────────────────────────────────┐
  │  ⚠️ 保證金不足，暫時無法接單            │
  │  目前結餘：HK$ -20.00                   │
  │  請先補款以恢復接單                      │
  │  [ 立即補款 ]                          │
  └────────────────────────────────────────┘
  ※ 已在途的行程不受影響，可以繼續完成。

違約冷靜期時（接單頁）：
  ┌────────────────────────────────────────┐
  │  ⏳ 冷靜期剩餘 12:30                     │
  │  因上一程取消，15 分鐘內不能接新單        │
  │  [ 查看詳情 ]                          │
  └────────────────────────────────────────┘
  ※ 倒數結束自動恢復，不需任何操作。
```

> **兩種鎖定必須用不同的視覺語言**：保證金不足是**要你行動**（橙色、有按鈕），
> 冷靜期是**等時間過去**（灰色、倒數、無需操作）。
> 若兩者用同一種提示，司機會分不清「我現在要不要做點什麼」——
> 尤其是保證金不足時他會坐着等倒數，永遠等不到。
>
> **「已在途的行程不受影響」這句必須寫出來** —— 否則司機會恐慌，
> 以為連手上這程也收不到錢。

### 6.3 Admin 端（新增頁面）

```
/disputes   「爭議處理」（預設按 SLA 到期升序）

┌──────────────────────────────────────────┐
│ 待處理 (3)                    [ 篩選 ▾ ] │
├──────────────────────────────────────────┤
│ #A1B2  自動開單 · 交通意外 · SLA 剩 42 分 │
│   行程：中環 → 銅鑼灣 · 司機 陳大文        │
│   由：司機提出中斷 · 對：乘客             │
│   [ 查看詳情 ]                            │
├──────────────────────────────────────────┤
│ #C3D4  到達爭議 · 尾 4 位核對失敗 3 次 · 1 小時前 │
│   [ 查看詳情 ]                            │
└──────────────────────────────────────────┘

詳情頁：
  · 雙方陳述（note）
  · 行程時間軸（來自 order_events）
  · **到達驗證記錄**（`arrival_claimed_at`、當時 GPS 距離、核對嘗試次數）
  · 司機歷史（過去中斷次數、取消率、到達爭議次數）
  · 乘客歷史
  ─────────────────────────────
  裁決結果（必選，無預設）：
    ⚪ 不處理            ⚪ 退還 / 註銷平台費
    ⚪ 向乘客收全額      ⚪ 向司機收 50%
  理由（必填）：________________
  [ 提交裁決 ]
```

**「查看歷史」為什麼重要？** 一個司機每月中斷 8 次，與一個司機入行兩年第一次
中斷，同樣的裁決不應該。把歷史放在決策畫面旁，不是裝飾，是令裁決有依據。

**「到達驗證記錄」是這次新增的重點欄位** —— 一張到達爭議的單，
admin 最需要知道的事實是「司機聲稱到達時，他距離上車點多遠」。
這個數字已在 `arrival_gps_distance_m` 存下來（見 §3.1），
admin 不需要猜。**但同時要記得：GPS 距離只是一個參考，不是判決** ——
香港高樓區的 GPS 誤差可以很大，一個 480 米的讀數可能是真的，
也可能是一個 ping 的偏差。

`Shell.tsx` 的 `NAV` 加一項 `{ path: '/disputes', label: '爭議處理',
badge: 'openDisputes' }` —— badge 機制已存在（`pendingKyc` /
`pendingRefunds` 是先例），只需在總覽 API 加一個計數。

---

## 7. 通知：這次不可以再靠輪詢

P1 / P2 的分析已確認：**行程生命週期事件從未被 publish**，
`mobile/lib/features/passenger/trip_tracking_screen.dart` 靠 10 秒輪詢補償。
對「司機位置更新」而言，10 秒延遲可接受。

**但對以下情況不可以：**

- **到達核對請求** —— 司機按了「我已到達」，乘客必須**即刻**看到核對畫面。
  若延遲 10 秒，司機站在街上等，乘客還在看地圖。這是整個到達驗證流程
  只有 10 秒就會崩潰的地方。
- **中斷** —— 「司機按了中斷，因為乘客在車上嘔吐」延遲 10 秒可能已經是 200 米。

因此 P4 **依賴 P2 的修復**：在 `arrival-claim`、`arrival-confirm`、
`interrupt`、`change-destination`、`dispute/resolve` 五個端點裡實際呼叫
`TripHub.publish()`。

> 這解決了 P2 長久以來的懸置狀態 —— 以前「publish 從未被呼叫」是一個已知
> 但無害的缺陷（有輪詢兜底）。P4 令它變成一個**必須修的阻斷項**。

若短期內無法完成 P2，則最低限度要求：
- `arrival-claim` 與 `interrupt` 後，**對方端立即**呼叫一次 `GET /orders/{id}`。
- 前端輪詢間隔在 `PENDING_ARRIVAL_CONFIRM` 與 `INTERRUPTED` 邊界上
  縮短至 2 秒（僅過渡期）。
  （這是臨時措施，不應該寫進長期設計。）

---

## 8. 實作順序與改動清單

| # | 改動 | 檔案 | 備註 |
|---|---|---|---|
| 1 | `OrderStatus` 加 3 態 + `InterruptionReason` + 3 個 `LedgerEntryType` | `app/models/user.py` | `native_enum=False`，enum 加值無需 migration |
| 2 | `orders` 加 15 欄位、`order_disputes`、`order_events` | Alembic migration | 見 §3.1 / §3.3 / §3.4 |
| 3 | 重寫 `ORDER_TRANSITIONS` | `app/services/state_machine.py` | 加 property test 守住 §2.2 的四條不變式 |
| 4 | `arrival-claim` + `arrival-confirm`（拆取代 `/arrive`） | `app/api/orders.py` | **含 GPS 距離計算** |
| 5 | `change-destination` 端點（不經 admin） | `app/api/orders.py` | |
| 6 | `interrupt` 端點（**即時生效** + 自動開 dispute） | `app/api/orders.py` | 單一 transaction |
| 7 | `start` 加 `started_at` + $5 扣費 | `app/api/orders.py` | 已拍板：向司機收 |
| 8 | `cancel` 加 `reason_code` 校驗（到達後不可取消） | `app/api/orders.py` | 見 §5.2 |
| 9 | **違約即時扣款 + 15 分鐘冷靜期** | `app/api/orders.py` + `app/core/cooldown.py` | Redis TTL key，見 DECISION-5 |
| 10 | **保證金接單閘門**（餘額 < 0 → 423） | `app/api/orders.py`（`grab` 前） | 見 DECISION-3；**不改 `DriverStatus`** |
| 11 | **`landmarks` 表（終點）+ 19 個種子 + `GET /landmarks`** | 新 `app/api/landmarks.py` + migration | 座標已驗證（含深圳灣口岸，邊界已修好）；**落客位置待人手覆核**（DECISION-7） |
| 12 | **預約單建立邏輯 + 背景廣播 job** | `app/api/orders.py` + `app/services/prebook_service.py` | 2h–3d 窗口，見 §3.6.4 |
| 13 | **司機預約偏好** | `app/api/drivers.py` + `driver_booking_preferences` | 見 §3.6.5 |
| 14 | admin dispute 端點（assign / resolve） | `app/api/admin.py` 或新 `dispute_admin.py` | + `AdminAuditLog` + RBAC |
| 15 | `order_events` 寫入 helper | 新 `app/services/order_event_service.py` | |
| 16 | 通知整合 | `app/services/trip_hub` | **依賴 P2** |
| 17 | 乘客 / 司機 / admin 前端 | `mobile/...` + `admin-web/web/src/pages/` | |
| 18 | badge 計數 + RBAC 接線 | `admin-web` 總覽 API + `Shell.tsx` | 依賴 P3 |

**測試重點（不可以只測 happy path）：**

**狀態機不變式（property-based）：**
- `DRIVER_ARRIVED -> CANCELLED` 必須被拒絕（P4 的核心不變式）。
- `IN_TRIP -> CANCELLED` 必須被拒絕。
- 任何 `DRIVER_ARRIVED` 的單必有 `arrival_confirmed_at`。
- 三個終態出度為零。

**到達驗證：**
- GPS 距離 > 半徑 → 422，**且訂單狀態維持 ACCEPTED**（不可以偷偷改狀態）。
- `current_location IS NULL` → 422，**不可以當作通過**。
- body GPS 與 DB `current_location` 差異 > 500 米 → 拒絕（偽造偵測）。
- 尾 4 位正確 → `DRIVER_ARRIVED` + `arrival_confirmed_at` 同時寫入。
- 尾 4 位錯誤 3 次 → 回 `ACCEPTED` + 開一張 dispute，**且 3 次之後第 4 次
  仍然被拒**（不可以因為計數器重設而變成無限嘗試）。
- **用司機的電話尾數輸入必須失敗**（測：確認驗證的是乘客的號碼）——
  這是防止寫錯成選項 B 的回歸測試。

**取消與違約：**
- 到達前 `POST /cancel` 即時生效且免費。
- 到達後 `POST /cancel` 必須 409 且**不寫任何 ledger**。
- `ACCEPTED` / `PENDING_ARRIVAL_CONFIRM` 取消 → **即時**寫
  `CANCELLATION_PENALTY`（不等裁決）**且**設 15 分鐘 Redis 冷靜期 key。
- 違約扣款 idempotency：`reference = f"penalty:{order_id}:{party}"`，
  重複呼叫不可以扣兩次。
- 冷靜期生效：乘客違約後 15 分鐘內 `POST /orders` 回 429 `COOLDOWN`；
  司機違約後 `POST /orders/{id}/grab` 回 429。**15 分鐘後自動恢復**
  （用假時鐘測 TTL 過期）。
- admin 推翻違約 → 寫 `DISPUTE_ADJUSTMENT` 退款 **且** `DEL` 冷靜期 key，
  之後可以立即再開單 / 接單。

**動錢：**
- `resolution` 缺省時請求必須 422（不可以有 default）。
- dispute 裁決必須寫出一行 `AdminAuditLog`。
- `PLATFORM_TRIP_FEE` 的 idempotency：重複 `/start` 不可以扣兩次
  （用 `reference = "trip:<order_id>"`）。
- 中斷必須**在同一 transaction 內**開出 dispute（測：若 dispute 寫入失敗，
  中斷也要回滾）。

**保證金閘門（DECISION-3）：**
- 餘額 < 0 時 `POST /orders/{id}/grab` 回 423 `DEPOSIT_INSUFFICIENT`
  （**不是 403** —— 這是可修復狀態）。
- **在途訂單不受影響**：司機跑到一半餘額變負，**不會**被踢下車
  （測：`IN_TRIP` 的單在中間扣費後仍然可以 `/complete`）。
- 補款後**即時恢復接單能力**（不需重新入職、不經 `DEPOSIT_REQUIRED`）。
- 欠費**不會**改 `DriverStatus`（仍然是 `ACTIVE`）—— 閘門在接單層，
  不在狀態層。

**預約單：**
- `scheduled_pickup_at` 少於 **2 小時** → 422 `TOO_SOON`。
- `scheduled_pickup_at` 超過 **3 天** → 422 `TOO_FAR`。
- **邊界值**：剛好 2 小時 → 通過；剛好 3 天 → 通過。
- 背景 job 只在 `prebook_visible_from <= now` 時才轉 `BROADCASTING`
  （測一個未來時間的單**不會**被提早廣播）。
- 預約單的搶單走 `GrabService`，**兩個司機同時搶只有一個成功**。
- 到期未匹配 → 升級為即時廣播（**不可以靜靜取消**）。
- **地標座標全部必須通過 `is_in_hong_kong()`** —— 用一個 fixture test
  對 `landmarks` 表所有行跑一次，**任何一行在港境外就 fail**。
  這條測試會捕捉「有人加入了一個深圳座標」的回歸。

**司機 filter：**
- 偏好只影響通知，**不影響 `nearby` 的可見性** ——
  設定「只接機場單」的司機仍然查得到其他單。


---

## 9. 待決問題

### DECISION-1 — $5 平台費：**已拍板，選 A**

> **用戶決定（2026-10-01）**：「我打算稍後還是回到傳統的平台模式，金錢經過
> 平台，$5 費用將會從司機方收入。」

**結論：方案 A — 從司機保證金帳戶扣。**

實作：`/start` 時寫 `LedgerEntry`，`entry_type = PLATFORM_TRIP_FEE`，
`amount_hkd = -5`，`driver_profile_id = 該司機`，
`reference = f"trip:{order_id}"`（唯一，防重複扣）。

**你問哪個方案比較好 —— 我的答案：A，但理由與你想的不完全一樣。**

先講清楚三個方案**不是同一個層次的東西**，所以「哪個好」要分兩步問：

**第一步：$5 哪個階段收？** 三個方案其實是**兩組**：

- **A** = 現在就從司機保證金扣。**今天可以上線**，零新基建。
- **B / C** = 都假設了一個尚未存在的東西：
  - **B（乘客錢包）** 假設乘客有錢可扣 —— 但乘客端**今日沒有錢包、沒有綁卡**。
  - **C（估價 surcharge）** 假設了車資經平台 —— 但**車資今日不經平台**。

換句話說：**B 與 C 都不是「另一個方案」，而是「一個未來的狀態」。**
你說「稍後還是回到傳統的平台模式，金錢經過平台」——
所以 B 與 C 描述的是**那個未來**，不是現在的替代選項。

**第二步：既然結果是 A，就要處理 A 的兩個副作用：**

| 副作用 | 為什麼嚴重 | 建議 |
|---|---|---|
| **保證金會被扣到負數** | 一個司機跑 100 趟 = -$500。保證金的意義是「違約時有錢可扣」，容許無限負值等於保證金制度失效。 | 見 DECISION-3：需要一個餘額門檻 + 預警 + 補款路徑。**這是 A 的真實成本，不可以當作「加一行 ledger 就算」。** |
| **與週費 $200 並存** | $200/週 + $5/趟：一個日跑 15 單的司機每週 $725。 | 定價問題。**建議考慮 A 但把週費調低或取消** —— 否則等於雙重收費，司機必然抗拒。 |

**所以我的具體建議：選 A，並且把它當作「取代一部份週費」而非「額外疊加」。**
$5/趟 的商業意義是「用者自付、多勞多付」，這比固定週費更公平，也更容易向
司機解釋。若同時收 $200/週 + $5/趟，誘因會變差。

**關於「金錢經過平台」的時序（重要）：**

`fare_calculator.py` 的免責聲明寫著平台是**資訊中介**（Cap. 374D）。
**$5 從司機收，與這段聲明不衝突** —— 因為 $5 是平台對司機的**服務費**，
不是車資的一部分，平台仍然沒有碰過車資。

**但當你真正做到「車資經平台」時，Cap. 374D 的定位就會改變。**
那是一個**需要法律意見**的變更，不是工程改動。
建議：**現在就按 A 實作並上線，把「車資經平台」當作一個獨立的、需要法律
評估的專案** —— 兩者不需要綁在一起。這樣你不會因為一個未定的法律問題
而卡住一個已經可以做的收入模型。


### DECISION-2 — 中斷的即時性：**已拍板**

> **用戶決定（2026-10-01）**：「改地點不會 involve admin，因為行程已經開始了，
> 平台會收到費用。除非 trip 被 cancel、有 dispute，那就直接 interrupted，
> 稍後 admin 才會介入，雙方都不用等 admin。」

**結論：中斷即時生效，admin 只在事後處理 dispute。**
原設計的 `INTERRUPT_PENDING` 狀態與其 partial-unique-index 表**全部刪除**。
本文已按此修訂。連帶簡化：

- 少一個狀態、少一個凍結期、少一個「等裁決」佇列。
- 但要新增 `order_disputes` 表（事後），且**在 `INTERRUPTED` 的同一
  transaction 內自動開單** —— 否則「事後處理」會變成「沒有人記得處理」。
- SLA 仍然需要（見 DECISION-4），但 SLA 逾期**不再阻塞任何人** ——
  它只是一個內部提醒，行程早已結束。

### DECISION-3 — 保證金扣到負數時：**已拍板，司機不可繼續接單**

> **用戶決定（2026-10-01 第四輪）**：「保證金被扣到負數時，司機不可繼續接單。」

**結論：餘額 < 0（不足 `required_hkd`）即不能接新單。**

實作要點與必須配的預警：

- **接單閘門**：`POST /orders/{id}/grab` 與預約單的公開廣播，都先檢查
  `DriverDeposit.balance_hkd + held_hkd >= 0`。不足 → **423 Locked**
  `{"reason": "DEPOSIT_INSUFFICIENT", "balance_hkd": …}`。
  **不是 403** —— 這是可修復的狀態（補款即恢復），不是權限問題。
- **已在途的單不受影響**：閘門只在**接新單**時檢查。
  一個司機跑到一半餘額變負，**不會**被中途踢下車 —— 那會令乘客受害。
  即「不能接新單」不等於「即時停牌」。
- **必須配預警，不可讓司機無聲被鎖**：
  - 餘額 < **$100** → App 內顯著告警 + 一次性推送。
  - 餘額 < **$0** → 每日提醒 + 接單頁顯示鎖定狀態與補款入口。
  - **不會自動 `SUSPENDED`** —— `SUSPENDED` 是合規狀態（KYC / 紀律），
    欠費只是帳務狀態，兩者不應混用。用「接單閘門」而不是改 `DriverStatus`。
- **與 `is_fulfilled` 的關係**：`is_fulfilled` 是 `ACTIVE` 的**入職條件**
  （一次性），**不是**持續條件。所以欠費**不會**把司機由 `ACTIVE` 拉回
  `DEPOSIT_REQUIRED`（那會觸發整條重新入職流程）。改為在接單層加閘門，
  影響面最小。

> **為什麼選這個而不是 C（另設應收帳）？** C 概念更清晰，但要新建一條
> 應收帳鏈路，工作量接近半個乘客錢包。A（不理會）已被否決 —— 它令保證金
> 制度失效。所以選「有閘門 + 有預警」的中間路線，改動集中在一個檢查點。

**待你確認的細節** —— **已拍板（2026-10-01 第六輪）**：

> **用戶決定**：「$100 這個預警門檻已經足夠；司機補款入帳後需要人工處理，
> 不是即時解鎖。」

- **預警門檻確認為 `$100`**（餘額 < $100 告警；餘額 < $0 每日提醒 + 接單頁鎖定）。
- **補款入帳後不即時解鎖**：`DriverDeposit.balance_hkd` 可以由
  `POST /admin/drivers/{id}/deposit/grant` 即時寫入（ledger 是即時的），
  但**接單權限由人手放行**。實作上需要一個明確的「已放行」標記，
  而不是靠「餘額 >= 0」自動推導。

  這樣做的代價與好處：
  - 好處 —— 人手有一次攔截機會，可以看到「這個司機為甚麼會欠到負數」
    （連續違約？爭議中？），避免「補錢即復工」的洗白路徑。
  - 代價 —— 司機補款後仍要等，客服會收到「我已經付錢了為甚麼還不能接單」
    的查詢。所以**補款成功頁面必須明寫「已收到款項，帳戶將由客服於
    工作日內開通」**，不能讓司機以為已經恢復。
  - 這是刻意的摩擦，不是遺漏。若日後投訴量過高，再考慮改為自動解鎖。

  **資料模型影響**：`driver_deposits` 需加一個欄位（建議
  `acceptance_unlocked_at timestamptz NULL`，或一個
  `acceptance_blocked_reason varchar(32)`）。**接單閘門讀它，不讀餘額。**
  即閘門的真實條件是「未被人工封鎖」，而「餘額 < 0」只是**觸發封鎖的原因**。

### DECISION-4 — 5 分鐘鎖定窗：**已作廢**

> **用戶決定（2026-10-01 第三輪）**：「取消鎖定窗的設定，因為目前沒有預計到達
> 時間，請改寫成司機已到達後不能取消。」

**已改為由「已驗證到達」觸發**（見 §5.1）。原本的 5 分鐘窗口、
`cancellation_locked_at`、`no_show_penalty_hkd_snapshot` 全部刪除。
新的鎖定條件不需要任何時間計算 —— 只需要 `order.status == DRIVER_ARRIVED`，
而該狀態本身已由 GPS + 乘客核對雙重保證。

### DECISION-5 — 違約罰款：**已拍板，即時扣款 + 15 分鐘冷靜期（雙方）**

> **用戶決定（2026-10-01 第四輪）**：「違約金即時扣款，司機 15 分鐘內不可以接單、
> 乘客同理不能開單。」

**結論：違約金即時寫 ledger（不等裁決），同時對違約方施加 15 分鐘的
「不可開新單」冷靜期。雙方對稱。**

| 違約方 | 即時扣款 | 冷靜期 |
|---|---|---|
| **乘客** | 即時扣（按該單估價快照，全額 100%） | **15 分鐘內不能開新單** |
| **司機** | 即時扣（按該單估價快照，50%） | **15 分鐘內不能接新單** |

**這改變了 §5.2 / §5.2.1 的做法** —— 原本設計是「寫入待裁決，由 admin 判」。
現在改為**先扣**，並保留申訴入口（人仍然可以事後推翻，但錢已經動了）。

**為什麼冷靜期是 15 分鐘而不是更長？** 這是「冷卻」而不是「懲罰」——
目的是打斷「一路違約、一路再開單」的即時套利。15 分鐘足以令
「連續違約」不可行，又不足以令一個正常用戶覺得被鎖死。
**這是真正的目的：令違約有摩擦成本，而不是令違約者無法使用平台。**

**實作要點：**

- **扣款用 `CANCELLATION_PENALTY` entry type**，`reference` 帶 `order_id`
  （`f"penalty:{order_id}:{party}"`）—— 沿用 `LedgerService.append` 的
  reference 冪等機制，令重試不會雙重扣款。
- **冷靜期用 Redis TTL key**（`cooldown:{party}:{user_id}`，TTL 900 秒），
  **不落 DB**（純時間性，過期即失效，無審計價值）。
  - 乘客：`POST /orders` 前檢查，命中 → **429** `{"reason": "COOLDOWN",
    "retry_after_s": n}`（附 `Retry-After` header）。
  - 司機：`POST /orders/{id}/grab` 前檢查，同樣回 429。
- **冷靜期與欠費閘門是兩個獨立檢查**（DECISION-3 是 `DEPOSIT_INSUFFICIENT`
  → 423；冷靜期是 `COOLDOWN` → 429）。兩者都要過才可接單。
- **申訴入口保留**：若 admin 事後判「違約不成立」，寫一筆
  `DISPUTE_ADJUSTMENT` **退回**已扣的錢，並**清除冷靜期**
  （`DEL cooldown:...`）。即「即時扣」與「可推翻」並存。
- **法律註記**：懲罰性即時扣款比事後裁決更需要明確依據。
  用戶已拍板，但建議條款內明示罰則（金額、觸發條件、冷靜期），
  以免變成不公平合約條款。**這是實作時要寫進用戶協議的，不是工程問題。**

> **原本 §5.2 的「事後裁決」段落已按此更新** —— 見下方的修訂標記。

### DECISION-6 — 預約服務參數：**已拍板**

> **用戶決定（2026-10-01 第四輪）**：「預約最少提前 2 小時，最早 3 天。」

| 參數 | **最終值** | 說明 |
|---|---|---|
| 最短預約提前量 | **2 小時** | 少於 2 小時 → 422 `{"reason": "TOO_SOON"}`，引導走即時單 |
| 最長預約提前量 | **3 天** | 超過 3 天 → 422 `{"reason": "TOO_FAR"}` |
| 提早廣播時間 | **30 分鐘**（沿用） | 到 `scheduled_pickup_at - 30min` 才開始廣播 |

> **2 小時這個值改變了 §3.6.4 的參數表** —— 我原本建議 30 分鐘。
> 用戶選 2 小時是合理的：30 分鐘的「預約」與即時單幾乎沒有分別，
> 而 2 小時才真正叫「預約」（司機可以規劃一整個時段，
> 乘客也可以真的為一個特定行程提前安排）。已同步更新 §3.6.4。

**明確保留**：**未匹配的預約單，到 `scheduled_pickup_at` 仍未有人接，
自動升級為即時廣播並加大半徑** —— 否則乘客會在約定時間站在原地沒有車。

**待你確認的細節（廣播窗口）** —— **已拍板（2026-10-01 第六輪）**：

> **用戶決定**：「預約單直到被選擇（司機爭取）或在沒有被選擇的情況下到達
> 出發時間都可以被司機看到。」

**結論：預約單對司機**的可見性**不設「廣播窗口」**，
而是一個**由 `broadcast_lead_time` 開始、直到結局為止的持續狀態**。

原本的設計是「到 `scheduled_pickup_at - 30min` 才開始廣播」，
暗示廣播有一個**子區間**。這個理解是錯的 —— 正確的規則是：

```
可見（對司機）  從 scheduled_pickup_at - broadcast_lead_time 開始
                到以下**任一**事件為止：
                  (a) 有司機接了這張單（→ ACCEPTED），或
                  (b) 到達 scheduled_pickup_at 仍未有人接（→ 升級為即時廣播）
```

也就是「30 分鐘」是**起始時間**，不是**窗口長度**。
兩條終止條件：

- **(a) 被選擇** —— 一旦有司機搶到，這張單對其他司機立即消失
  （與即時單的 `GrabService` 行為一致，Redis 原子搶單）。
- **(b) 到達出發時間** —— 仍無人接 → 依上面的「明確保留」條款
  **升級為即時廣播並加大半徑**。乘客不會被丟下。

**注意這條規則也解釋了「為甚麼 30 分鐘不是『太短』」** ——
我上一輪提出的疑問（「30 分鐘廣播窗口是否太短」）前提就錯了：
司機不是只有 30 分鐘可看，而是**有 30 分鐘的提前量**，然後
一直看到有人接單或到點為止。所以 `broadcast_lead_time` 的語意是
「**提前多久讓司機知道**」，不是「**讓司機看多久**」。
維持 **30 分鐘**是合理的（這只是「多早曝光」的參數）。

### DECISION-7 — 地標清單：**已補完（19 個即用），座標已過 HK 邊界驗證**

> **用戶決定（2026-10-01 第四輪）**：「地標清單幫我補。」
>
> **用戶決定（2026-10-01 第五輪，修正定位）**：「這些地標都是終點下客，
> 顯示的時候是地圖上出現終點地標，司機可以很快地看到這些單。**不要考慮上客。**」
>
> **用戶決定（2026-10-01 第七輪，深圳灣口岸拍板）**：「深圳灣口岸你幫我決定，
> 因為深圳灣口岸真的是在深圳境內，但是香港租賃管理。定位在香港的士的下客區就好。」

**定位（第五輪確立）：地標 = 終點 / 下客點。** 用途有兩個：
1. **乘客**用它選目的地。
2. **司機**看單時，地圖上直接顯示終點地標（「→ 香港國際機場」），
   可以一眼判斷接不接；並且可以按類別 filter 主動接收通知。

**上客點永遠是乘客的實際位置，與地標無關。** 因此：
- `landmarks` **沒有 `pickup_point`**（已移除）。
- 口岸地標的 `location` 用**港方實際可落客的位置**（去口岸的乘客在市區上車、
  落客在口岸附近），不再需要「港方上車點」的概念；但座標**仍必須在
  `is_in_hong_kong()` 內**，否則建立訂單時會 422（實測深圳灣、文錦渡原座標）。

清單由原本 10 個擴充至 **19 個即用**（原本 18 個 + 深圳灣口岸 1 個；
深圳灣口岸的邊界缺陷已修好，見 §二）。
**每個座標都已用專案自身的 `is_in_hong_kong()` 多邊形驗證過** ——
結果見下表。**這是真實驗證，不是抄座標。**

#### 一、即用（19 個，通過 HK 邊界檢查）

座標來源：OpenStreetMap（社群維護），全部經 `app/core/hk_bounds.py` 驗證。

| `code` | 名稱 | `category` | 座標 (lat, lng) | `radius_m` |
|---|---|---|---|---|
| `HKIA` | 香港國際機場 | `AIRPORT` | 22.312599, 113.917300 | 800 |
| `ASIAWORLD` | 亞洲國際博覽館 | `VENUE` | 22.321251, 113.942968 | 400 |
| `DISNEYLAND` | 香港迪士尼樂園 | `THEME_PARK` | 22.313070, 114.040985 | 600 |
| `OCEAN_PARK` | 海洋公園 | `THEME_PARK` | 22.234767, 114.170817 | 600 |
| `ICC` | 環球貿易廣場 | `OFFICE` | 22.303379, 114.160226 | 200 |
| `IFC` | 國際金融中心 | `OFFICE` | 22.285163, 114.159815 | 200 |
| `HK_CONVENTION` | 香港會議展覽中心 | `VENUE` | 22.282625, 114.173069 | 300 |
| `HARBOUR_CITY` | 海港城 | `MALL` | 22.297002, 114.168420 | 300 |
| `TIMES_SQ` | 時代廣場 | `MALL` | 22.278359, 114.182106 | 200 |
| `TST_PROMENADE` | 尖沙咀海旁 | `WATERFRONT` | 22.299419, 114.185648 | 500 |
| `KT_PROMENADE` | 觀塘海旁 | `WATERFRONT` | 22.312288, 114.217369 | 400 |
| `HK_COLISEUM` | 香港體育館（紅館） | `VENUE` | 22.301318, 114.181981 | 300 |
| `HZMB_PORT` | 港珠澳大橋香港口岸 | `BORDER` | 22.317868, 113.954070 | 500 |
| `LOK_MA_CHAU` | 落馬洲支線管制站 | `BORDER` | 22.515276, 114.065632 | 400 |
| `LO_WU` | 羅湖管制站 | `BORDER` | 22.529713, 114.113850 | 300 |
| `MAN_KAM_TO` | 文錦渡管制站 | `BORDER` | 22.519218, 114.124584 | 300 |
| `QMH` | 瑪麗醫院 | `HOSPITAL` | 22.269875, 114.131214 | 300 |
| `PWH` | 威爾斯親王醫院 | `HOSPITAL` | 22.379609, 114.202193 | 300 |
| `SHENZHEN_BAY` | 深圳灣口岸（港方口岸區）公共運輸交匯處 | `BORDER` | 22.500992, 113.945654 | 400 |

> `category` 新增了 `VENUE`（場館）與 `HOSPITAL`（醫院）兩類，
> 因為它們的預約需求模式與商場 / 寫字樓不同（散場時間高度集中）。

#### 二、深圳灣口岸（**邊界缺陷已修好 — 選項 A**）

> **用戶決定（2026-10-01 第六輪）**：「深圳灣口岸最遠到深圳灣口岸（港方口岸區）
> 的公共運輸交匯處。」
>
> **用戶決定（2026-10-01 第七輪，拍板）**：「深圳灣口岸你幫我決定，因為深圳灣口岸
> 真的是在深圳境內，但是香港租賃管理。定位在香港的士的下客區就好。」

**落客點**：深圳灣口岸（港方口岸區）公共運輸交匯處
→ **`22.500992, 113.945654`**（OSM `bus_station` way 581117553；
Wikimapia 22°30'5"N 113°56'41"E 相差約 100 米，兩個來源互相印證）

完整座標與 Google Maps 連結見 **`docs/LANDMARK_COORDINATES.md` §二**。

| 檢查 | 修好前 | 修好後 |
|---|---|---|
| `is_in_hong_kong(22.500992, 113.945654)` | ❌ False | ✅ **True** |

##### 為何判為香港是正確的（不只是「修個 bug」）

深圳灣口岸分兩部分：**旅檢大樓北部及相連車站由深圳市管轄**，
**旅檢大樓南部及相連的公共運輸交匯處屬「港方口岸區」**。
全國人大常委會 2006-10-31 授權香港特區在口岸內實行全封閉式管理、
實施香港法律；國務院（國函〔2006〕132號）批覆港方口岸區範圍及土地期限。
港方口岸區佔地 **41.565 公頃**，土地**由深圳市政府擁有**，香港**租賃**取得、
**每年付租**，期限至 **2047-06-30**。

一句話：**不在香港土地，但在香港境內。** 所以站在那裡的裝置，
其用戶有權使用這個 app —— 判為境外是真正的 false negative。

##### 原本的缺陷

`_HK_MAIN` 后海灣段只有兩個頂點（`22.4600,113.9200` → `22.5050,113.9600`），
斜率 `dlat/dlng = 1.125`。在交匯處經度 113.945654 上，那條邊緯度只有
**22.488861**，而交匯處在 **22.500992** —— **低 0.0121°（≈1.34 km）**。

後果：一輛開往深圳灣口岸的的士，在**距離目的地還有 1.3 公里時就「離開香港」**，
訂單建立時會以 422 `OUTSIDE_HK` 被拒。

##### 修法（選項 A，只挑出口岸區一塊）

`_HK_MAIN` 的 Deep Bay 段由 **2 個頂點擴至 7 個**，只在口岸一帶向北繞彎：

```
LatLng(22.4600, 113.9200),  # 鰲磡石（不變）
LatLng(22.4830, 113.9330),  # 后海灣，向口岸爬升
LatLng(22.4880, 113.9380),  # 深圳灣大橋港方段
LatLng(22.4940, 113.9400),  # 橋頭引道
LatLng(22.5010, 113.9412),  # 口岸區西牆（交匯處以西 ~450 m）
LatLng(22.5055, 113.9440),  # 口岸區東北，越過交匯處
LatLng(22.5070, 113.9500),  # 口岸區北牆
LatLng(22.5050, 113.9600),  # 后海灣東側（不變）
LatLng(22.5150, 114.0200),  # 深圳河口（不變）
```

西牆刻意留 **~450 米餘量**（交匯處本身有面積 + GPS 誤差，餘量太薄會令真實落客點仍掉出境外）。

##### 驗證（實跑）

- **陽性全 True**：交匯處（OSM + Wikimapia）、交匯處四周 ±250 m、口岸區本體、
  深圳灣大橋港方落腳點、鰲磡石、尖鼻咀、天水圍、元朗、屯門、龍鼓灘、
  落馬洲、羅湖、文錦渡、機場、港珠澳大橋香港口岸、沙頭角、東平洲、長洲。
- **陰性全 False**：蛇口（碼頭 / 海上世界 / 北 / 東北 / 東岸）、
  **深圳灣口岸（深圳側）管制站**、口岸北側、南山、前海、后海灣北面水域、
  福田、羅湖（深圳）、鹽田、寶安、華強北、市民中心。
- **網格掃描**：整片后海灣 / 蛇口角以 0.0025° 步長掃 **212 個境內點**，
  確認沒有任何一點落到「管制站以北」或「蛇口東岸以西」的深圳一側。

測試：`tests/test_hk_bounds.py` 的
`test_the_shenzhen_bay_port_area_defect_cannot_come_back`（鎖死舊的兩頂點邊界）
與 `test_admitting_the_port_area_did_not_admit_shenzhen`（網格性質測試，
已用兩個故意錯誤的邊界驗證**會 fail**）。

> **落馬洲 / 皇崗口岸完全在深圳境內**，香港沒有對應落客點 ——
> 不會也不應成為香港終點。深圳灣之所以不同，是因為它有**港方口岸區**。

> ⚠️ **此段是全模組唯一刻意伸到深圳河走廊以北的地方。**
> 它靠的不是「放寬」，而是「**只在港方口岸區這一小塊放寬**」；
> `hk_bounds.py` 的 `_HK_MAIN` 註釋已寫明。若日後要再動，
> 先跑 `tests/test_hk_bounds.py` —— 那兩個測試就是為此而設。

#### 三、`category` 全集

原本 7 類 → **9 類**：`AIRPORT` / `BORDER` / `THEME_PARK` / `MALL` /
`OFFICE` / `WATERFRONT` / `VENUE` / `HOSPITAL` / `OTHER`。

> 加 `VENUE` 與 `HOSPITAL` 的理由：**散場 / 探病時間高度集中**，
> 是預約需求最強的兩類（紅館演唱會散場、醫院探病時段）。
> 司機 filter 若只有「商場 / 寫字樓」，會錯過最值錢的時段。
>
> **未收錄**（留待你決定）：離島（長洲 / 南丫島渡輪碼頭）、
> 跨境巴士總站、其他主題公園（水上樂園）。需求較低或涉及渡輪接駁，
> 暫不列入首批。

> ⚠️ **上線前必須人手覆核「落客位置」**：19 個座標雖然全部通過邊界檢查，
> 但**「地標中心」不等於「的士可以停車落客的位置」**。
> 尤其：HKIA 應該用**的士落客區**（不是客運大樓幾何中心）、
> 迪士尼用**的士上落客區**、紅館用**暢運道對出**。
> 現時的座標（見 §3.6.3）**足夠開發與測試**，但**上線前必須由人覆核落客位置** ——
> 因為司機看地圖是要**開車去落客**，偏幾百米就會走錯入口。

---

## 10. 與既有設計的相容性檢查

| 既有機制 | P4 是否破壞它 | 說明 |
|---|---|---|
| `assert_order_transition` | 否，是擴充 | 新狀態加入 dict，既有轉移不變 |
| `POST /orders/{id}/arrive` | **被取代** | 拆成 `arrival-claim` + `arrival-confirm`，見 §4.0 |
| `no_show_penalty_hkd` 定額罰款 | **被取代** | 改為按 `estimated_total_hkd` 的 100% / 50%，見 §5.2 |
| `LedgerService.append` 的 reference 冪等 | 否，沿用 | `trip:<order_id>` 同 `weekly:<period>` 同一模式 |
| `LedgerService` 允許負結餘 | **需要政策決定** | 見 DECISION-3（已拍板：負結餘即不能接新單） |
| `DriverDeposit.is_fulfilled` | 否，**重定義用途** | 保持是「入職條件」，**不是**持續條件；欠費改由接單閘門處理 |
| `DriverStatus.ACTIVE` | 否 | 欠費**不改** `DriverStatus`（見 DECISION-3） |
| `POST /orders/{id}/grab` | **加前置檢查** | 冷靜期（429）+ 保證金（423），見 §4.0.6 |
| `POST /orders` | **加前置檢查** | 乘客冷靜期（429），見 §4.0.6 |
| `fare_calculator` 的中介免責聲明 | **現在不動，未來需法律意見** | 見 DECISION-1 |
| `GrabService` 的 Redis 原子搶單 | 否，**重用** | 預約單的搶單走同一套機制，見 §3.6.4 |
| Redis（既有 rate limiter / geo） | 否，**新增用途** | 冷靜期 TTL key（`cooldown:{party}:{id}`），與 rate limit 共用同一 Redis |
| `GeoService` (`GEO_ORDERS_KEY`) | 否，**重用** | 預約單到點後寫入同一個 geo set |
| `driver_profiles.current_location` | 否，**新增用途** | 到達驗證讀它算距離（見 §5.1.2） |
| `require_in_hong_kong` | **否（缺陷已修）** | 地標（**終點**）`location` 校驗在港境內。**深圳灣港方口岸區的落客點 `22.500992,113.945654` 曾被誤判為境外**（`_HK_MAIN` 后海灣段精度不足，差距 ≈1.34 km）；**已按 DECISION-7 選項 A 修正多邊形並補測試**，港方口岸區現已判為境內，蛇口 / 南山 / 前海 / 深圳側管制站仍為 False |
| `TripHub` 位置推送 | 否，但**被依賴** | §7：P4 令 P2 成為阻斷項 |
| `RefundService` / 保證金 | 否 | `DISPUTE_ADJUSTMENT` 是新 entry type |
| admin 退款 / 押金調整 | 否，**新增用途** | admin 推翻違約時要退款 **且** 清冷靜期（見 DECISION-5） |
| analytics（由 `orders` 派生） | **必須更新** | `INTERRUPTED` 不應計入「完成單」；要有「到達爭議次數」「預約單匹配率」「違約次數」新指標。**不更新的話新狀態會靜靜污染現有數字。** |
| P3 的 dispute 表 | **必須合併** | §4.4：`order_disputes` 就是同一張表，不可以開兩張 |

