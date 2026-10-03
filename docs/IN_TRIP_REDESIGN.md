# In-Trip 業務邏輯重新設計 / In-Trip Redesign

> **EN — Summary.** A redesign proposal for the in-trip phase (P4), not yet
> implemented. It widens the order state machine beyond today's `IN_TRIP →
> COMPLETED` dead end, adding `DESTINATION_CHANGED` (non-terminal) and
> `INTERRUPTED` (terminal), plus a per-trip platform fee and the rules for when
> a driver may refuse or end a trip.
>
> **Read this before editing `ORDER_TRANSITIONS`.** The state machine has two
> invariants asserted by tests — terminal states have no outgoing edges, and
> `CANCELLED` is unreachable once a trip is under way. `INTERRUPTED` is terminal
> while `DESTINATION_CHANGED` is not, so both must be re-checked when this lands.

> **中文摘要**：**P4 in-trip 重新設計提案，尚未實作**。把訂單狀態機由 `IN_TRIP →
> COMPLETED` 的死巷擴闊，加入 `DESTINATION_CHANGED`（非終態）與 `INTERRUPTED`（終態）。
> **改動 `ORDER_TRANSITIONS` 前務必先讀** —— 狀態機有兩條由測試守住的不變式。涵蓋資料庫
> schema、API 設計、狀態機、前端流程。**七個 DECISION 全部已拍板，集中於 §9。** 座標完整
> 清單（含 Google Maps 連結）見 `docs/LANDMARK_COORDINATES.md`。

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
| `POST /{id}/cancel` | 雙方 | `→ CANCELLED`；司機在 ACCEPTED / DRIVER_ARRIVED 取消會被扣 `no_show_penalty_hkd`（預設 HK$50，`PENALTY_DEDUCTION`） |

取消是**單一 Payload** `CancelIn { reason: str = "" }` —— 只有自由文字，沒有結構化原因分類，
`reason` 也**不經任何驗證**就寫進 `orders.cancellation_reason`。

### 1.3 三個現存缺口（與本需求直接相關）

- **(a) `IN_TRIP` 是死胡同 —— 只能向前完成。** 途中撞車、改目的地、乘客願意落車而司機同時在
  路邊等客，全部無法表達；系統會逼營運同事「照按 complete」再私下處理，帳目與現實脫節。
- **(b) 平台從未向「行程」收費。** 現行收入模型是**每週服務費**（`weekly_fee_hkd = 200`，
  `WEEKLY_FEE_DEDUCTION`）。`LedgerEntryType` 只有 DEPOSIT_TOPUP / WEEKLY_FEE_DEDUCTION /
  PENALTY_DEDUCTION / REFUND / ADJUSTMENT —— **沒有「按趟收費」這個 type**。
- **(c) 乘客端沒有付款工具。** `DriverDeposit` 只覆蓋司機；乘客 `User` 沒有錢包、沒有綁卡、
  沒有信用額。`fare_calculator.py` 免責聲明寫明：

  > 「車費估價僅供參考。平台僅屬資訊中介，最終車資由乘客與司機自願協商確認
  > （香港法例第374D章）。」

  即**車資不經平台**，是司機收現金 / 自己收款。所以「$5 平台費」不可能是「由車資抽成」
  —— 平台沒有收到過車資（見 §9 DECISION-1）。

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

> `INTERRUPT_PENDING`（等 admin 裁決）**已刪除**：改地點不涉及 admin，中斷**即時生效**，
> admin 只在**事後**介入（dispute，見 §4.4）。**新增 `PENDING_ARRIVAL_CONFIRM`**：司機按
> 「已到達」後待乘客核對（見 §5.1）。

**設計取捨：**

| 狀態 | 為什麼需要 | 約束 |
|---|---|---|
| `PENDING_ARRIVAL_CONFIRM` | 「到達」必須雙重驗證（GPS + 乘客尾 4 位）；此態 = 「GPS 過了、等乘客核對」。 | **不能跳過**：否則司機可在 500 米外單方面剝奪乘客取消權。 |
| `DESTINATION_CHANGED` | 需求要求 cancel 與 change destination 區分狀態。 | **非常態（non-terminal）**：改完行程繼續，轉回 `IN_TRIP`。**不涉及 admin。** |
| `INTERRUPTED` | 行程提前結束，須與 `COMPLETED` 分開，否則收入報表把「未行完的單」算成完成單。**終態，即時生效。** | 不重用 `CANCELLED`：取消屬「未出發」，中斷屬「已出發但提前結束」，理賠、結算、統計三者不同。 |

> **核心不變式：`CANCELLED` 不再從任何已驗證到達之後的狀態可達。** 到達之後沒有「取消」，
> 只有「中斷」，令非正常結束都留下結構化原因（`interruption_reason`）供 admin **事後**判決。

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

**不變式（應寫成 property-based test）：**

1. `CANCELLED` 的前驅**只能**是 `CREATED` / `BROADCASTING` / `ACCEPTED` /
   `PENDING_ARRIVAL_CONFIRM`。**沒有任何 `DRIVER_ARRIVED` 或之後的狀態可以走到 `CANCELLED`。**
2. 三個終態 `{COMPLETED, INTERRUPTED, CANCELLED}` 的出度為零。
3. 「有 `completed_at`」的所有單，狀態必為 `COMPLETED`。
4. **`DRIVER_ARRIVED` 必定有 `arrival_confirmed_at`**（到達必須經雙重驗證）。

### 2.3 狀態圖

`CREATED → BROADCASTING → ACCEPTED → PENDING_ARRIVAL_CONFIRM → DRIVER_ARRIVED → IN_TRIP`。
`CREATED` / `BROADCASTING` / `ACCEPTED` / `PENDING_ARRIVAL_CONFIRM` 全部可 `CANCELLED`
（到達未證實前）；`PENDING_ARRIVAL_CONFIRM` 核對失敗 3 次 → 回 `ACCEPTED` + 開 dispute。
`DRIVER_ARRIVED` 之後：`CANCELLED` 不可達，只有 `COMPLETED` / `INTERRUPTED`。`IN_TRIP` /
`DESTINATION_CHANGED` 可 → `COMPLETED` / `INTERRUPTED`；`DESTINATION_CHANGED` 可回落 `IN_TRIP`。
終態 `{COMPLETED, INTERRUPTED, CANCELLED}` 出度為零。admin 不在路徑上，只在事後處理 dispute。

---

## 3. 資料庫 Schema 變更

### 3.1 `orders` 表新增欄位

```sql
ALTER TABLE orders
  ADD COLUMN started_at               timestamptz,   -- IN_TRIP 起點，$5 計費基準
  ADD COLUMN platform_fee_hkd         numeric(10,2), -- 平台費快照（同 tariff_version 理由）
  ADD COLUMN platform_fee_charged_at  timestamptz,
  ADD COLUMN original_dropoff_address text,          -- 保留原始目的地，不覆蓋，可審計
  ADD COLUMN original_dropoff_location geography(POINT, 4326),
  ADD COLUMN destination_changed_at   timestamptz,
  ADD COLUMN destination_change_count smallint NOT NULL DEFAULT 0,
  ADD COLUMN interruption_reason      varchar(32),   -- enum, 見 3.2
  ADD COLUMN interrupted_at           timestamptz,
  ADD COLUMN interrupted_by_kind      varchar(16),   -- 'passenger' | 'driver'
  ADD COLUMN arrival_claimed_at       timestamptz,   -- 司機按「已到達」的時間
  ADD COLUMN arrival_gps_distance_m   numeric(7,1),  -- 按鈕當時與上車點的距離
  ADD COLUMN arrival_confirmed_at     timestamptz,   -- 乘客核對尾 4 位成功的時間
  ADD COLUMN arrival_pin_attempts     smallint NOT NULL DEFAULT 0;
```

> `arrival_gps_distance_m` 是**事後爭議的證據**（即時判斷只看是否 ≤ 半徑）。`arrival_claimed_at`
> （司機聲稱到達，可能被拒）與 `driver_arrived_at`（到達已證實）是兩個事件，後者只在
> `arrival_confirmed_at` 有值時才寫。`original_dropoff_*` 用欄位而非子表（只改一次是絕大多數
> 情況）；完整改動史由 `order_events`（§3.4）提供。

### 3.2 新增 enum

```python
class InterruptionReason(str, enum.Enum):
    """中斷原因。用 enum 而非自由文字：這是分派給 admin 的判決依據，需要可統計。"""

    ACCIDENT = "ACCIDENT"                  # 撞車 / 交通意外
    CONFLICT = "CONFLICT"                  # 與對方發生衝突
    PASSENGER_MISCONDUCT = "PASSENGER_MISCONDUCT"  # 乘客態度惡劣
    PASSENGER_SICK = "PASSENGER_SICK"      # 乘客嘔吐 / 身體不適
    VEHICLE_BREAKDOWN = "VEHICLE_BREAKDOWN"    # 車輛故障
    UNSAFE_ROUTE = "UNSAFE_ROUTE"          # 路況 / 路線安全問題
    FARE_DISPUTE = "FARE_DISPUTE"          # 車資爭議
    OTHER = "OTHER"                        # 必須附文字說明
```

> 需求「乘客態度惡劣」→ `PASSENGER_MISCONDUCT`、「嘔吐」→ `PASSENGER_SICK`、「撞車」→
> `ACCIDENT`、「發生衝突」→ `CONFLICT`；另補 `VEHICLE_BREAKDOWN` / `UNSAFE_ROUTE` /
> `FARE_DISPUTE`（車壞了、路走不通亦是合理例外）。

### 3.3 新增 `order_disputes` 表（**事後**處理，非阻塞）

> 原 `order_interrupt_requests` 表（partial unique index 保證同時只有一個 PENDING）**已不
> 需要** —— 中斷不再經審批，改為事後 dispute 表。

```sql
CREATE TABLE order_disputes (
    id              uuid PRIMARY KEY,
    order_id        uuid NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    -- 誰提出對什麼不滿。與 interruption_reason 分開：「為何結束」vs「為何不服」。
    raised_by_kind  varchar(16) NOT NULL,   -- 'passenger' | 'driver' | 'admin' | 'system'
    raised_by_id    uuid,
    source          varchar(32) NOT NULL,   -- reason_code 或 'AUTO_INTERRUPTED'
    against_kind    varchar(16),            -- 'passenger' | 'driver' | 'platform'
    status          varchar(16) NOT NULL DEFAULT 'OPEN',
                    -- OPEN | INVESTIGATING | RESOLVED | ESCALATED | CLOSED
    assigned_admin_id uuid REFERENCES admin_accounts(id),   -- 可為 NULL，未指派
    -- 裁決：錢誰屬。明確欄位而非從 ledger 反推（反推無法區分「未裁決」與「裁決為 NONE」）。
    resolution      varchar(24),   -- 'NONE' | 'CHARGE_PASSENGER' | 'CHARGE_DRIVER'
                                   -- | 'REFUND_PLATFORM_FEE' | 'WAIVED_PLATFORM_FEE'
    resolved_by     uuid REFERENCES admin_accounts(id),
    resolved_at     timestamptz,
    resolution_note text,
    sla_due_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- 一張單可以有多個 dispute（乘客 + 司機各提一個），已解決的不應重複開
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

> **由系統在 `INTERRUPTED` 的同一 transaction 內開單**，保證每宗中斷都有 owner、有 SLA。

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

> **與 `AdminAuditLog` 是不同層**：audit log 答「哪個 admin 做了什麼」，order_events 答
> 「這張單發生過什麼」。

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

> `native_enum=False`，加 enum member **不需** migration 改 DB type（`SAEnum` 預設 VARCHAR(30)，
> 最長的 `CANCELLATION_PENALTY` 是 20 字元，安全）。

### 3.6 預約服務（Pre-booking）所需欄位與表

#### 3.6.1 為什麼預約服務對這個業務特別有價值

> **地標是「終點 / 下客點」，不是上客點**（第五輪）：司機看單時地圖上直接標出終點地標（「這
> 張單去機場 / 去迪士尼」），可快速判斷接不接。**完全不考慮上客 —— 上客點永遠是乘客即時的
> 實際位置。** 因此 `landmarks` 不需 `pickup_point`（見 §3.6.3）；邊境口岸的「港方上車點」
> 問題不存在（去口岸的乘客在市區上車、落客在口岸附近）。

**機場、口岸、主題公園**這類行程的終點與時間高度可預測（趕航班 / 閉園時段），司機可提前規劃；
而**司機可以 filter 這類單**（用戶明確要求）—— 即時單是「誰先看到誰搶」，預約單是「誰先規劃
誰得」。

#### 3.6.2 `orders` 新增欄位

```sql
ALTER TABLE orders
  ADD COLUMN order_kind            varchar(16) NOT NULL DEFAULT 'ON_DEMAND',
                                   -- 'ON_DEMAND' | 'SCHEDULED'
  ADD COLUMN scheduled_pickup_at   timestamptz,     -- 預約出發時間；ON_DEMAND 必為 NULL
  ADD COLUMN prebook_visible_from  timestamptz,     -- 提早多久開始讓司機看到並搶單
  ADD COLUMN dropoff_landmark_id   uuid REFERENCES landmarks(id) ON DELETE SET NULL,
  ADD COLUMN prebook_state         varchar(16);     -- 'PENDING' | 'BROADCASTING' | 'MATCHED'
```

> **為什麼 `prebook_state` 不進 `OrderStatus`？** `CREATED` 的預約單與 `CREATED` 的即時單在
> **流程上**同一位置（都是「未廣播」），差別只在**時間**。用獨立欄位 + 背景 job 到點把
> `OrderStatus` 由 `CREATED` 推到 `BROADCASTING`，保留 `OrderStatus` 的純粹性。

#### 3.6.3 `landmarks` 表（地標 = **終點 / 下客點**）

> `landmarks` 只服務**終點顯示**與**司機 filter**，因此**移除 `pickup_point`**。

```sql
CREATE TABLE landmarks (
    id           uuid PRIMARY KEY,
    code         varchar(32) NOT NULL UNIQUE,   -- 'HKIA', 'DISNEYLAND', 'ICC' …
    name_en      varchar(120) NOT NULL,
    name_zh      varchar(120) NOT NULL,
    category     varchar(24) NOT NULL,
                 -- 'AIRPORT' | 'BORDER' | 'THEME_PARK' | 'MALL' | 'OFFICE'
                 -- | 'WATERFRONT' | 'VENUE' | 'HOSPITAL' | 'OTHER'
    location     geography(POINT, 4326) NOT NULL,  -- 終點落客座標；須在 is_in_hong_kong() 內
    radius_m     integer NOT NULL DEFAULT 300,     -- 地理圍欄半徑（米）
    is_active    boolean NOT NULL DEFAULT true,    -- 座標未覆核完的一律 false
    sort_order   integer NOT NULL DEFAULT 100,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_landmarks_location ON landmarks USING gist (location);
CREATE INDEX ix_landmarks_category ON landmarks (category) WHERE is_active;
```

> **`location` 是「落客點」不是「地標中心」**：機場 / 迪士尼 / 紅館的的士落客區可能在幾百米
> 外。司機看地圖是要**開車去**，不是去打卡。**具體落客座標仍需人手覆核**（見 §9 DECISION-7）。

**初始資料：19 個即用地標（含深圳灣口岸，見 §9 DECISION-7），座標全部經
`is_in_hong_kong()` 驗證。**

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

> 口岸類地標的定位已隨第五輪修訂而改變：地標現在是**終點**，`location` 就是**港方口岸區的
> 落客點**。`SHENZHEN_BAY` 的落客點座標**曾被 `is_in_hong_kong()` 拒絕**（邊界多邊形后海灣段
> 精度不足，非座標選錯；第七輪已修正，見 §9 DECISION-7）；`MAN_KAM_TO` 在邊界北側，港方車道
> 路段（紅橋新村文錦渡路 `22.519218, 114.124584`）通過檢查。**皇崗 / 落馬洲口岸完全在深圳
> 境內**，不會也不需要成為香港終點。
>
> ⚠️ **`location` 座標仍需人手覆核落客位置（不是建築物中心）**：`HKIA` 應填**的士落客區**、
> 迪士尼用**的士上落客區**、紅館用**暢運道對出**。現時填社群地圖的地標中心，足夠開發，
> **上線前必須覆核**。

#### 3.6.4 預約單的流程

```
乘客選「預約」→ 選起點（乘客實際位置或自訂地址，**不是地標**）→ 選終點（地標或自由輸入）→ 選時間
  → 建立訂單：order_kind=SCHEDULED, status=CREATED, prebook_state='PENDING',
     prebook_visible_from = scheduled_pickup_at - broadcast_lead_time

背景 job（每分鐘掃一次，或 Redis 定時）：找 prebook_visible_from <= now 且 prebook_state='PENDING'
  的單 → status CREATED→BROADCASTING, prebook_state='BROADCASTING' → 寫入 GEO_ORDERS_KEY
  （令司機 nearby 查詢找到它）→ 通知符合 filter 的司機

可見性（第六輪修正 —— 狀態規則，不是時間窗口）：由 prebook_visible_from 開始**一直可見**，直到
  (a) 有司機搶到（→ ACCEPTED, prebook_state='MATCHED'）或 (b) 到達 scheduled_pickup_at 仍無人接
  （→ 升級為即時廣播）。**不要實作「廣播 N 分鐘後收回」** —— 那會令乘客在到點前沒有車。

司機搶單：同一套 GrabService（Redis 原子），**不需要新機制** → BROADCASTING→ACCEPTED,
  prebook_state='MATCHED'
```

**參數（已由用戶在 DECISION-6 拍板）：**

| 參數 | **值** | 理由 |
|---|---|---|
| `broadcast_lead_time` | **30 分鐘** | **提前量**：到 `scheduled_pickup_at - 30min` 開始對司機可見。**這不是窗口長度** —— 開始之後一直可見，直到被接走或到點 |
| 最短預約提前量 | **2 小時** | 少於 2 小時 → 422 `TOO_SOON`，引導走即時單 |
| 最長預約提前量 | **3 天** | 超過 3 天 → 422 `TOO_FAR` |
| 可見性終止 (a) | 有司機搶到 → 對其他司機立即消失 | 與即時單的 `GrabService` 一致（Redis 原子） |
| 可見性終止 (b) | 到 `scheduled_pickup_at` 仍無人搶 → 自動轉即時廣播（加大半徑） | 否則乘客會靜靜地沒有車 |

> **「未匹配自動升級為即時廣播」是必須做的設計**：若到時間沒有人接就靜靜取消，乘客會在
> 21:00 站在迪士尼門口沒有車 —— 比一開始就拒絕預約更糟。

#### 3.6.5 司機端的 filter（用戶明確要求）

司機 App「我的偏好 → 預約單」：勾選「我想去哪類終點」（對應 `landmarks.category`：機場、
口岸 / 邊境、主題公園、場館（紅館、會展、亞博）、商場、寫字樓、海旁 / 地標、醫院、其他）、
「我通常在哪區開始」（地區下拉）、「可接時段」（如 06:00 至 10:00）。

> **Filter 的 checkbox 與 `landmarks.category` 是 1:1 對應** —— 不要另設 UI 專用分類，否則
> 兩邊會靜靜漂移。**Filter 篩選的是「終點類別」**，不是「上客位置」。

**實作方式：** 新增 `driver_booking_preferences` 表：

```sql
CREATE TABLE driver_booking_preferences (
    driver_profile_id uuid PRIMARY KEY REFERENCES driver_profiles(id) ON DELETE CASCADE,
    -- 想接的「終點」地標分類（array，因為這是純過濾條件，沒有獨立生命週期）
    categories        varchar(24)[] NOT NULL DEFAULT '{}',
    -- 司機想「由哪一區開始」接單：自由文字地區名，不是地標（地標是終點）
    preferred_origin_area varchar(64),
    available_from    time,          -- 可接時段（每日，當地時間）
    available_until   time,
    updated_at        timestamptz NOT NULL DEFAULT now()
);
```

> 原本的 `preferred_origin_landmark_id` 已改為 `preferred_origin_area`：**地標是終點**，用
> 終點地標做「起點偏好」語意上錯誤。
>
> **通知邏輯**：背景 job 只推送給符合 filter 的司機，**但不要把 filter 做成硬性限制** ——
> 司機仍可在「附近訂單」看到所有單。Filter 用途是**主動通知**，不是**封鎖可見性**。
>
> ⚠️ **`varchar[]` 取捨**：查詢用 `categories && ARRAY['AIRPORT']`（`&&` 是「有交集」）。
> 若未來需要「每個分類有不同設定」才改子表；現在只需一個布林過濾，用陣列是對的。

---

## 4. API 設計

### 4.0 到達驗證（取代既有的 `/arrive`）

既有端點 `POST /{order_id}/arrive` 只做 `transition(order, DRIVER_ARRIVED)`，**要拆成兩步**。

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
   SELECT ST_Distance(dp.current_location, o.pickup_location) AS distance_m
   FROM driver_profiles dp, orders o
   WHERE dp.id = :driver_id AND o.id = :order_id
   ```

   - `current_location IS NULL` → 422 `{"reason": "NO_LOCATION"}`
   - `distance_m > ARRIVAL_RADIUS_M` → 422 `{"reason": "TOO_FAR", "distance_m": …}`
3. GPS 通過 → 寫 `arrival_claimed_at` / `arrival_gps_distance_m`，
   `transition(order, PENDING_ARRIVAL_CONFIRM)`。
4. 通知乘客（**必須即時推送**，見 §7）。
5. 響應回 `{"status": "PENDING_ARRIVAL_CONFIRM", "distance_m": 42.5}`。

> `driver_lat` / `driver_lng` 為 body 可選（最準但**不可信**，須通過 `require_in_hong_kong`
> 校驗）；**最終判斷用 DB 的 `current_location`**（經 WebSocket 驗證路徑寫入）。**若兩者差異
> > 500 米 → 直接拒絕並記錄**（通常意味司機嘗試偽造）。

#### 4.0.2 乘客核對尾 4 位

```
POST /api/v1/orders/{order_id}/arrival-confirm
     Auth: 乘客（必須是 order.passenger_id）
     Body: { phone_last4: "1234" }
```

**服務邏輯：**

1. 校驗 `order.status == PENDING_ARRIVAL_CONFIRM`，否則 409。
2. **比對 `order.passenger` 的 `phone_e164[-4:]`**（選項 A，見 §5.1.3）。
   > 要讀**該訂單的乘客**的電話，不是呼叫者的 —— 代叫車情境下可能不同。
3. 成功 → `transition(order, DRIVER_ARRIVED)`；寫 `arrival_confirmed_at`、`driver_arrived_at`
   （**兩者同一次寫入**）；**取消權在此鎖定**。
4. 失敗 → `arrival_pin_attempts += 1`：
   - `< 3` → 401 `{"reason": "PIN_MISMATCH", "attempts_remaining": n}`
   - `== 3` → 回到 `ACCEPTED`，**開一張 `order_disputes`**（`source=ARRIVAL_CONFLICT`），
     通知雙方（通常是司機按錯單或乘客上錯車）。

**回應必須包含「知道了」的確認**，成功時回 `{"status": "DRIVER_ARRIVED", "driver": {…}}`。

#### 4.0.3 預約單的建立

```
POST /api/v1/orders                        （既有端點，擴充 body）
     Body: { ...既有欄位,
       order_kind: "ON_DEMAND" | "SCHEDULED" = "ON_DEMAND",
       scheduled_pickup_at: datetime | null,      # SCHEDULED 時必填
       dropoff_landmark_id: uuid | null }
```

**新增校驗（順序很重要）：**

1. `order_kind == "SCHEDULED"` 時：
   - `scheduled_pickup_at` 必填，否則 422。
   - `scheduled_pickup_at >= now() + 2h`，否則 422 `{"reason": "TOO_SOON"}`（DECISION-6）。
   - `scheduled_pickup_at <= now() + 3d`，否則 422 `{"reason": "TOO_FAR"}`（DECISION-6）。
2. `dropoff_landmark_id` 有值時：校驗地標存在且 `is_active`；**終點以地標為準**（結構化
   資料），但要記錄差異（§3.4 `order_events`，乘客可能輸入了更精確地址）；**不要用地標反推
   上車點**（上車點永遠是乘客實際位置）。
3. `prebook_visible_from = scheduled_pickup_at - broadcast_lead_time`（30 分鐘）。
4. `prebook_state = 'PENDING'`。

#### 4.0.4 地標查詢（公開，供乘客選終點與司機 filter）

```
GET /api/v1/landmarks?category=THEME_PARK,AIRPORT
     Auth: 任何已登入用戶
```

回傳 `code` / `name_en` / `name_zh` / `category` / `location`。**地標是終點** —— 乘客選目的地，
司機做 filter。**應該有快取**：建議 `Cache-Control: public, max-age=3600` 加應用層 Redis 快取。

#### 4.0.5 司機預約偏好

```
GET  /api/v1/drivers/me/booking-preferences
PUT  /api/v1/drivers/me/booking-preferences
     Auth: ACTIVE 司機
     Body: { categories: ["AIRPORT", "THEME_PARK", "WATERFRONT"],   # 想去的終點類別
             preferred_origin_area: "觀塘",                          # 想由哪區開工（自由文字）
             available_from: "06:00", available_until: "10:00" }
```

> **只影響主動通知，不影響可見性**（見 §3.6.5）。端點名用 `booking-preferences` 而非
> `filters`，因為它是**偏好**不是**限制**；`categories` 是**終點**類別。

#### 4.0.6 接單 / 開單的兩道閘門（DECISION-3 + DECISION-5）

**兩個閘門獨立，都要通過才可接單 / 開單。順序：先冷靜期，後餘額。**

| 閘門 | 觸發 | 狀態碼 | `reason` | 誰受影響 |
|---|---|---|---|---|
| **冷靜期** | 上次違約未滿 15 分鐘 | **429** | `COOLDOWN` | 司機（`grab`）、乘客（`POST /orders`） |
| **保證金不足** | `balance + held < 0` | **423** | `DEPOSIT_INSUFFICIENT` | **只有司機**（乘客無保證金） |

```
POST /api/v1/orders/{order_id}/grab          （既有端點，加兩道前置檢查）
     Auth: ACTIVE 司機
     檢查順序：
       1. Redis GET cooldown:driver:{driver_id}
          → 存在 → 429 {"reason": "COOLDOWN", "retry_after_s": n} + header `Retry-After: n`
       2. DriverDeposit.balance_hkd + held_hkd >= 0 ?
          → 否則 → 423 {"reason": "DEPOSIT_INSUFFICIENT", "balance_hkd": …}
       3. （既有）ACTIVE 狀態 + GrabService 原子搶單

POST /api/v1/orders                          （開新單，加一道前置檢查）
     Auth: 乘客
     檢查：Redis GET cooldown:passenger:{user_id} → 存在 → 429 {"reason": "COOLDOWN", ...}
```

> **冷靜期回 429（「時間會解決」，附 `Retry-After`）而餘額回 423（「資源被鎖，要去補款」）**
> —— 使用者行動不同（等 vs 補錢）。**檢查必須在 `GrabService` 之前**，否則會出現「搶到了但
> 接不了」—— 單被鎖住而司機拿不到，其他司機也搶不到。

### 4.1 改目的地

```
POST /api/v1/orders/{order_id}/change-destination
     Auth: 乘客 or 受指派司機
     Body: { dropoff_location: {lat, lng}, dropoff_address: str }
```

**服務邏輯（順序很重要）：**

1. 校驗 `order.status in {IN_TRIP, DESTINATION_CHANGED}`，否則 409。
2. 若 `destination_change_count == 0`：先把**現有** `dropoff_*` 抄到 `original_dropoff_*`
   （只抄一次，不覆蓋，令「最初想去哪裡」永久可查）。
3. `assert_order_transition(status, DESTINATION_CHANGED)`。
4. 呼叫 `calculate_fare()` 用**新**距離重新估價，寫入 `orders.fare_json` 新版本
   （`is_estimate=True`），同時更新 `orders.estimated_total_hkd`。
5. `destination_change_count += 1`，`destination_changed_at = now()`。
6. 寫 `order_events`（`DEST_CHANGED`，payload 含舊/新目的地）。
7. **響應**包含新估價 —— 前端必須即刻顯示「新估價 HK$X，實際車資仍由你與司機協商」。

> **不變式**：`destination_change_count` 要有上限（建議 3），否則乘客可無限次改目的地規避
> 取消費用或拖時間。超出即 429 並附「請與司機直接溝通或提出中斷」。

### 4.2 中斷行程（**即時生效，不等 admin**）

```
POST /api/v1/orders/{order_id}/interrupt
     Auth: 乘客 or 受指派司機（雙方皆可，符合需求）
     Body: { reason_code: InterruptionReason,   # 必填
             note: str }                        # reason_code == OTHER 時必填
```

**服務邏輯（單一 transaction）：**

1. 校驗 `status in {IN_TRIP, DESTINATION_CHANGED}`，否則 409。
2. `assert_order_transition(status, INTERRUPTED)`，`orders.status = INTERRUPTED`。
3. 寫 `orders.interruption_reason` / `interrupted_at` / `interrupted_by_kind`。
4. **不再自動退 $5 —— 費用維持已收狀態。** 若中斷即時退款，任何一方都可以用「中斷」規避
   平台費。**錢的調整交給事後 dispute 裁決**，令「即時結束」與「錢誰屬」解耦。
5. **同一 transaction 內自動開一張 `order_disputes`**（`source=AUTO_INTERRUPTED`，或安全類
   原因則 `AUTO_INTERRUPTED_SAFETY`）。
6. 寫 `order_events`。
7. **發通知**：對方即時收到 + admin 佇列收到一張新 dispute（必須真實推送，不可靠輪詢，見 §7）。
8. **響應即時回 `INTERRUPTED`** —— 沒有「等待中」狀態。

> **中斷是當事人的權利，不是需要批准的請求**（行程出事要人即刻停低）；正因為即時生效，事後
> 裁決就必須更嚴謹 —— 這正是 dispute 表存在的理由。**副作用：`interrupted_by_kind` 的對方會
> 自動成為 dispute 的 `against_kind`**（預設，不是定論；dispute 表可再改）。

### 4.3 完成行程（修訂既有端點）

```
POST /api/v1/orders/{order_id}/complete
     Auth: 受指派司機
```

**變更：** 1. 校驗 `status in {IN_TRIP, DESTINATION_CHANGED}`，否則 409。2. 寫
`orders.completed_at`。3. **$5 平台費在 `/start` 已扣**（見 §4.5），此處不再扣。4. 寫
`order_events`。

### 4.4 Admin 事後處理 dispute

```
GET  /api/v1/admin/disputes?status=OPEN
GET  /api/v1/admin/disputes/{id}
POST /api/v1/admin/disputes/{id}/assign      # 指派給自己 / 他人
POST /api/v1/admin/disputes/{id}/resolve
     Auth: require_finance（會動錢，依 P3 的 RBAC）
     Body: { resolution: "NONE" | "CHARGE_PASSENGER" | "CHARGE_DRIVER"
                       | "REFUND_PLATFORM_FEE" | "WAIVED_PLATFORM_FEE",
             note: str }        # 必填，理由就是稽核軌跡
```

**服務邏輯：** 1. 校驗 `dispute.status in {OPEN, INVESTIGATING, ESCALATED}`。2. `resolution`
的財務後果：

| `resolution` | 會計動作 |
|---|---|
| `NONE` | 錢不動（撞車屬正當，$5 照收） |
| `CHARGE_PASSENGER` | 向乘客收違約罰款（見 §5 的全額 / 50% 規則） |
| `CHARGE_DRIVER` | 從司機保證金扣（寫 `DISPUTE_ADJUSTMENT`） |
| `REFUND_PLATFORM_FEE` | 退還 $5（寫 `DISPUTE_ADJUSTMENT`，`+5`） |
| `WAIVED_PLATFORM_FEE` | $5 從未收 / 註銷 |

> **`resolution` 必須顯式選擇，不可有 default**（與 `DepositAdjustIn.reason` 必填同一原則）。

3. **全部寫 `AdminAuditLog`**（P3 稽核擴充）。4. 寫 `dispute.resolved_by` / `resolved_at` /
`resolution_note`。

> **與 P3 的 dispute 表統一。** 這裡的 `order_disputes` **就是 P3 §4 那張通用表**，不應開
> 兩張。P3 的表若涵蓋非訂單類，用 `order_id` 可為 NULL 區分。**實作時務必只做一張表。**

### 4.5 出發（既有端點，新增扣費）

```
POST /api/v1/orders/{order_id}/start
```

現行 `order_start` 只有 `transition(order, IN_TRIP)`。新增：1. `orders.started_at = now()`。
2. **扣 $5 平台費**：寫 `LedgerEntry`，`entry_type = PLATFORM_TRIP_FEE`，`amount_hkd = -5`，
`driver_profile_id = 該司機`，`order_id = 該單`，**`reference = f"trip:{order_id}"`**
（唯一 → 重複 `/start` 不會扣兩次；與 `weekly:<period>` 同一模式，`uq_ledger_reference`
partial unique index 已存在）。3. 寫 `order_events`。

---

## 5. 司機不得違約的規則

需求：「司機接單後原則上不得違約，除非乘客態度惡劣、嘔吐這些原因。」以及：「行程開始前
5 分鐘內就不能取消，雙方都不能。乘客違約要賠全額、司機賠 50%。」

**現狀已部分覆蓋**：`order_cancel` 對 ACCEPTED / DRIVER_ARRIVED 的司機取消扣
`no_show_penalty_hkd`（HK$50）。但 **`reason` 是自由文字不驗證**（司機打「乘客醉酒」就等於
豁免）；**`IN_TRIP` 的司機取消不可達**（狀態機擋死）；**乘客一方完全沒有違約成本**。

**重設計後的規則矩陣：**

| 階段 | 誰能單方結束 | 機制 | 財務後果 |
|---|---|---|---|
| BROADCASTING / ACCEPTED | 雙方 | `POST /cancel` | 免費 |
| **DRIVER_ARRIVED（已驗證到達）** | **雙方都不能取消** | 沒有路徑 | 見下 |
| IN_TRIP | 雙方 | `POST /interrupt` | 即時結束；錢由事後 dispute 判 |

### 5.1 到達驗證（新增）—— 取消鎖定的前提條件

> 原「距出發 5 分鐘鎖定窗」**已取消**（系統無 ETA 可算）。改為：**司機「到達」不是自己說
> 了算，必須通過兩重驗證**，通過才鎖定取消權。

**規則**：司機按「我已到達」必須同時滿足：

| # | 驗證 | 失敗後果 |
|---|---|---|
| 1 | **司機 GPS 接近上車點** | 拒絕，訂單維持 `ACCEPTED` |
| 2 | **乘客提供電話尾 4 位並確認** | 拒絕，訂單維持 `ACCEPTED` |

兩者**都通過**，訂單才轉 `DRIVER_ARRIVED`、`driver_arrived_at` 才寫入。**在此之前司機不算
到達** —— 不計入「到達後不能取消」的鎖定。

#### 5.1.1 為什麼需要驗證

若只靠司機單方面按鈕，司機可在 500 米外按「已到達」再催乘客 / 啟動等時計費，乘客無法證明
司機未到，且一旦按了就不能取消 —— 這是**司機單方面剝奪乘客權利**的路徑。所以「到達」必須有
**雙方以外的證據（GPS）+ 雙方確認（乘客尾數）**才成立。

#### 5.1.2 GPS 驗證

```
司機按「我已到達」→ 後端讀 driver_profiles.current_location（最後一次 GPS tick）
  → 計算與 orders.pickup_location 的距離
  → 距離 ≤ ARRIVAL_RADIUS_M（建議 150 米）→ 通過
  → 否則 422，回傳實際距離，前端顯示「你距離上車點約 800 米，請再接近」
```

**技術要點：** 距離用 **PostGIS** 在 DB 端算（`ST_Distance(current_location,
pickup_location)`，兩者皆 `geography(POINT,4326)`，距離直接以**米**為單位）—— 不要自己寫
haversine。`current_location` 可能為 NULL（從未上線 / GPS 失敗）→ **不能當作通過**，回 422
並要求重開定位。GPS 誤差（香港高樓區可達 50–100 米），半徑要**寬鬆**（150 米而非 30 米），
否則不斷誤拒。**不要一次失敗就永久拒絕** —— 司機應可重新按。

> **限制**：`current_location` 由司機裝置上報，技術上用心的司機可偽造 —— 這層防「隨手亂按」，
> 不是「堅決作弊」（後者需裝置 attestation）。**但配合第 2 層（乘客確認），偽造 GPS 就沒有
> 意義** —— 乘客不會確認一個沒有出現的司機。

#### 5.1.3 電話尾 4 位驗證（傳統方法，用戶指定）

```
司機按「我已到達」（GPS 通過）→ 訂單轉入 PENDING_ARRIVAL_CONFIRM
  → 乘客 App 彈出：「司機聲稱已到達。請把您自己手機號碼的最後 4 位告訴司機，並在下面輸入
                    以確認您已上車。」
  → 乘客輸入 4 位 → 後端比對 passenger.phone_e164[-4:]
  → 相符 → DRIVER_ARRIVED，鎖定取消權
  → 不符（3 次）→ 回到 ACCEPTED，開一張 dispute
```

**用誰的電話尾數？**

| 選項 | 乘客要輸入 | 安全性 | 可用性 |
|---|---|---|---|
| **A. 輸入乘客自己的尾 4 位** | 乘客的號碼尾 4 位 | ✅ 司機不可能知道 | ⚠️ 乘客要想自己號碼（多數人記得） |
| **B. 輸入司機的尾 4 位** | 司機的號碼尾 4 位 | ❌ 司機自己知道，可以代按 | ⚠️ 乘客要問司機 |

**建議用 A**：驗證目的是「確認**這個乘客**真的在場」；只有本人知道自己的號碼尾數，司機無從
得知 —— 司機能通過的唯一方式，就是眼前真的有一個乘客告訴他。用 B 則司機自己就能完成驗證，
等於第 2 層不存在。這正是傳統的士業做法（乘客報尾數，司機核對）。

**實作要點：**

- **只存 hash？不需要** —— 尾 4 位只有 10,000 種可能，hash 不增安全性反而令比對複雜。
  **直接比對 `phone_e164[-4:]`** 即可。
- **限 3 次嘗試**（Redis 計數器，per order）。超過 3 次：訂單回 `ACCEPTED`，自動開一張
  `order_disputes`（`source=ARRIVAL_CONFLICT`）。
- **乘客可主動確認** —— 也可做「確認司機已在場」按鈕，但**輸入 4 位比按鈕好**：按鈕零成本、
  可能亂按；輸入 4 位需實質核對。
- **無障礙 / 特殊情況**：聽障、或司機無乘客號碼（第三方叫車），提供「我無法核對」路徑 →
  開 dispute 由 admin 處理。**不要讓驗證變成無法繞過的死結。**

#### 5.1.4 新狀態 `PENDING_ARRIVAL_CONFIRM`

轉移見 §2.2。**在此期間雙方仍可取消，但已構成違約**（與 `ACCEPTED` 同級，因司機已到附近、
成本已產生）—— 既然「到達」未被證實，就不應用強制力鎖定取消權；但「未被證實到達」不等於
「沒有成本」，故用違約金而非硬鎖。只有 `DRIVER_ARRIVED`（已雙重驗證）才**硬鎖**取消權。

### 5.2 到達後不能取消

**規則**：訂單處於 `DRIVER_ARRIVED`（已驗證到達）時，**雙方都不能取消**。

| 情況 | 出路 |
|---|---|
| 乘客不想上車 | 不能取消；只能等司機開始行程後用 `POST /interrupt`，或由司機取消（司機取消會被罰） |
| 司機不想載 | 不能取消；只能 `POST /interrupt` |
| 任何一方認為被騙 | 開 dispute |

> **設計問題**：「到達後不能取消」+「in-trip 才有中斷」= 在 `DRIVER_ARRIVED` 雙方被鎖死，
> 唯一出路是司機按「開始行程」。若司機拒絕開始（例如發現乘客醉酒），系統沒有出路。**處理：
> 允許 `DRIVER_ARRIVED` 直接 `INTERRUPTED`**（語意「在車上但未出發就出事」）。§2.2 已加入。

**違約罰款**（在 `DRIVER_ARRIVED` 或之後，一方造成行程無實質進行）：

> 到達之後**沒有「取消」**（`CANCELLED` 不可達，見 §2.2 不變式 1）。下表講**違約責任**，
> 觸發事件可能是 `INTERRUPTED`（即時），或 admin 事後判定的違約（例如司機長時間不開車）。

| 違約方 | 罰款 | 冷靜期 |
|---|---|---|
| **乘客** | **全額**（100% `estimated_total_hkd`） | **15 分鐘內不能開新單** |
| **司機** | **50%** `estimated_total_hkd` | **15 分鐘內不能接新單** |

**計費基準是該單的估價快照**，不是定額 —— $300 的機場單與 $40 的短程單違約成本不可能一樣。
現行 `no_show_penalty_hkd = 50` 定額被取代。已按 DECISION-5 改為「即時扣款 + 15 分鐘冷靜期」，
**保留申訴入口**：admin 事後判「違約不成立」時寫 `DISPUTE_ADJUSTMENT` 退回已扣金額，並清除
冷靜期。

#### 5.2.1 到達前的違約取消（`ACCEPTED` / `PENDING_ARRIVAL_CONFIRM`）

到達前**可以取消**（不硬鎖），但**不是免費**。分界線是「司機是否已付出成本」：

| 階段 | 司機成本 | 取消後果 |
|---|---|---|
| `CREATED` / `BROADCASTING` | 沒有（尚未有人接單） | **免費取消**，寫 `CANCELLED` |
| `ACCEPTED` / `PENDING_ARRIVAL_CONFIRM` | **有**（已接單、已出發、已在路上） | 取消**成立但構成違約**，按 §5.2 基準（乘客 100% / 司機 50%）**即時扣款 + 15 分鐘冷靜期** |

> **為什麼不硬鎖？** 由 `ACCEPTED` 到「到達」可能幾分鐘到十幾分鐘，硬鎖等於禁止乘客在等車期間
> 改變主意 —— 改變主意本身不是罪，只是有代價。**用定價取代禁止。** 扣款與冷靜期都是即時的
> （DECISION-5）：即時寫 `CANCELLATION_PENALTY` ledger + 即時設 Redis 冷靜期 key；申訴入口
> 仍開放。**同時收緊 `ACCEPTED` 起的取消 `reason`**：改成必填 `reason_code`
> （`InterruptionReason` enum）—— 已有違約後果的取消，不驗證的自由文字欄位就是繞過口。

**計費基準**：同 §5.2 —— 該單估價快照，乘客 100%、司機 50%。

### 5.3 in-trip 的司機違約

| 階段 | 司機能否單方結束 | 財務後果 |
|---|---|---|
| IN_TRIP | 可以，用 `POST /interrupt` | 即時結束；由事後 dispute 判 |

**關鍵：in-trip 的是非曲直移交 admin 事後判斷，而不是即時阻擋。** in-trip 的爭議（究竟是
乘客嘔吐還是司機想放棄訂單）無法用規則判斷，強行自動化必然誤判。
`orders.interrupted_by_kind='driver'` + `interruption_reason` 就是交給 admin 的證據。若 admin
判 `CHARGE_DRIVER`，寫 `DISPUTE_ADJUSTMENT`，金額由 admin 決定。

> **取消三階段**：`CREATED` / `BROADCASTING` 免費取消；`ACCEPTED` /
> `PENDING_ARRIVAL_CONFIRM` 可取消但構成違約（即時扣款 + 15 分鐘冷靜期）；`DRIVER_ARRIVED`
> 或之後不可取消，只能 `INTERRUPTED`。分別在於「有沒有客觀事實（司機已到）做分界」：到達前
> 是**時序**問題，規則可判斷；到達後是**是非**問題，規則判斷不到。

---

## 6. 前端互動流程

### 6.1 乘客端（`mobile/lib/features/passenger/trip_tracking_screen.dart`）

- **行程中（`IN_TRIP`）**：顯示司機已出發 / ETA、地圖、車資估價（「僅供參考」），底部兩個並排
  動作 **［改目的地］［中斷行程］**（中斷不可藏進選單 —— 撞車時人處於驚慌狀態）。
- **改目的地**：地點搜尋 → 確認頁顯示「新估價 HK$104.00（+HK$18.00）。實際車資仍由你與司機
  協商（法例第374D章）」→［確認更改］。**不經 admin，行程繼續。**
- **中斷行程**：原因選單（必選，對應 `InterruptionReason`）：交通意外 / 撞車、與司機發生衝突、
  司機態度惡劣、車輛故障、路線不安全、車資爭議、其他（文字框必填）。提示「中斷後行程即時結束。
  平台會稍後處理費用安排。」
- **中斷後（`INTERRUPTED`）**：結束畫面，橫幅「行程已結束。如對費用有疑問，可提出申訴。」＋
  ［提出申訴］。

**乘客核對畫面（新增，`PENDING_ARRIVAL_CONFIRM`）**：顯示司機姓名 / 車牌、與上車點距離；輸入
「司機面前乘客的電話號碼最後 4 位」＋［確認］；附［司機沒有出現］出路。正確 → `DRIVER_ARRIVED`
「已確認司機在場」；失敗 3 次 → 「無法確認。我們已通知平台，客服會盡快聯絡你。」
（乘客輸入**自己的**尾 4 位，見 §5.1.3。）

**到達後（取消權已鎖定）**：顯示司機資料 ＋「⚠️ 已確認到達，無法取消。如無法乘車，請與司機
溝通或提出申訴」＋［提出申訴］。**不再顯示灰色「取消」按鈕** —— 存在但按不了或按了付全額的
按鈕比沒有更糟，引誘人按然後懲罰他。

### 6.2 司機端

- **接單大廳**：預約單以**終點地標為主標題**（如「🏝 香港迪士尼樂園 21:30 · 距離 8.2 km ·
  預計 18 分鐘 · ⏰ 30 分鐘後開始接單」、「✈️ 香港國際機場 07:15」）；即時單顯示上車點 pin。
  **即時單「上車點優先」、預約單「終點優先」，是刻意的。**
- **預約偏好頁**（見 §3.6.5）：勾選終點類別、「我通常在哪區開始」、「可接時段」；須明寫「設定
  只影響哪些預約單會主動通知你，你仍可在『附近訂單』看到所有訂單」。
- **中斷行程原因（司機版）**：與乘客版對稱但選項不同 —— 交通意外 / 撞車、與乘客發生衝突、
  乘客態度惡劣、乘客嘔吐 / 嚴重不適（需求點名）、車輛故障、路線不安全、車資爭議、其他。
  > 兩邊共用同一 `InterruptionReason` enum，前端只顯示該角色合理選項，**後端仍要校驗**
  > （`interrupted_by_kind='driver'` 時拒絕 `PASSENGER_MISCONDUCT` 等）—— 前端過濾是禮貌，
  > 後端校驗是授權。
- **「我已到達」按鈕（新增）**：顯示上車點 +「✅ GPS 已定位（距上車點 42 米）」＋［我已到達］；
  太遠 →「你距離上車點約 480 米，請再接近」＋［重新定位］；GPS 不可用 →「無法取得你的位置，
  請確認已開啟定位權限」＋［開啟設定］。
- **司機被鎖定畫面（DECISION-3 / DECISION-5）**：保證金不足（橙色、要你行動）「⚠️ 保證金不足，
  暫時無法接單。目前結餘 HK$-20.00。請先補款以恢復接單」＋［立即補款］＋「※ 已在途的行程不受
  影響」；違約冷靜期（灰色、等時間過去）「⏳ 冷靜期剩餘 12:30。因上一程取消，15 分鐘內不能接
  新單」＋［查看詳情］＋「※ 倒數結束自動恢復」。
  > 兩種鎖定用不同視覺語言，否則司機分不清要不要行動；「已在途行程不受影響」必須寫出。

### 6.3 Admin 端（新增頁面）

`/disputes`「爭議處理」（預設按 SLA 到期升序）：清單列 `#id`、類型（自動開單 / 到達爭議）、
原因、SLA 剩餘、行程、提出者 / 對象。詳情頁：雙方陳述（`note`）、行程時間軸（`order_events`）、
**到達驗證記錄**（`arrival_claimed_at`、當時 GPS 距離、核對嘗試次數）、司機 / 乘客歷史。裁決
結果（必選，無預設）：不處理 / 退還或註銷平台費 / 向乘客收全額 / 向司機收 50%；理由必填。

> **「到達驗證記錄」是新增重點欄位**：admin 最需要知道「司機聲稱到達時距離上車點多遠」，已存於
> `arrival_gps_distance_m`（§3.1）；但 **GPS 距離只是參考，不是判決**。裁決時並列司機歷史
> （過去中斷次數、取消率、到達爭議次數），令裁決有依據。

`Shell.tsx` 的 `NAV` 加 `{ path: '/disputes', label: '爭議處理', badge: 'openDisputes' }` —— badge
機制已存在（`pendingKyc` / `pendingRefunds` 為先例），只需在總覽 API 加計數。

---

## 7. 通知：這次不可以再靠輪詢

P1 / P2 已確認：**行程生命週期事件從未被 publish**，
`mobile/lib/features/passenger/trip_tracking_screen.dart` 靠 10 秒輪詢補償。「司機位置更新」
10 秒延遲可接受，但以下不可以：**到達核對請求**（司機按「我已到達」後乘客必須**即刻**看到
核對畫面）、**中斷**（延遲 10 秒可能已是 200 米）。

因此 P4 **依賴 P2 的修復**：在 `arrival-claim`、`arrival-confirm`、`interrupt`、
`change-destination`、`dispute/resolve` 五個端點實際呼叫 `TripHub.publish()`。

> 這令 P2 的「publish 從未被呼叫」由已知但無害的缺陷，變成**必須修的阻斷項**。

若短期無法完成 P2，最低限度：`arrival-claim` 與 `interrupt` 後對方端立即呼叫一次
`GET /orders/{id}`；前端輪詢在 `PENDING_ARRIVAL_CONFIRM` / `INTERRUPTED` 邊界縮短至 2 秒
（臨時措施，不寫進長期設計）。

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
| 11 | **`landmarks` 表（終點）+ 19 個種子 + `GET /landmarks`** | 新 `app/api/landmarks.py` + migration | 座標已驗證（含深圳灣口岸）；**落客位置待人手覆核**（DECISION-7） |
| 12 | **預約單建立邏輯 + 背景廣播 job** | `app/api/orders.py` + `app/services/prebook_service.py` | 2h–3d 窗口，見 §3.6.4 |
| 13 | **司機預約偏好** | `app/api/drivers.py` + `driver_booking_preferences` | 見 §3.6.5 |
| 14 | admin dispute 端點（assign / resolve） | `app/api/admin/disputes.py`（原 `app/api/admin.py`，已拆包） | + `AdminAuditLog` + RBAC |
| 15 | `order_events` 寫入 helper | 新 `app/services/order_event_service.py` | |
| 16 | 通知整合 | `app/services/trip_hub` | **依賴 P2** |
| 17 | 乘客 / 司機 / admin 前端 | `mobile/...` + `admin-web/web/src/pages/` | |
| 18 | badge 計數 + RBAC 接線 | `admin-web` 總覽 API + `Shell.tsx` | 依賴 P3 |

**測試重點（不可以只測 happy path）：**

- **狀態機不變式**：`DRIVER_ARRIVED -> CANCELLED` 與 `IN_TRIP -> CANCELLED` 被拒；`DRIVER_ARRIVED`
  必有 `arrival_confirmed_at`；三個終態出度為零。
- **到達驗證**：GPS > 半徑 → 422 且狀態維持 `ACCEPTED`；`current_location IS NULL` → 422 不可當
  通過；body GPS 與 DB 差異 > 500 米 → 拒絕；尾 4 位正確 → `DRIVER_ARRIVED` +
  `arrival_confirmed_at` 同時寫入；錯 3 次 → 回 `ACCEPTED` + 開 dispute，第 4 次仍被拒；用司機
  電話尾數必須失敗（防寫錯成選項 B）。
- **取消與違約**：到達前取消免費；到達後取消 → 409 且不寫 ledger；`ACCEPTED` /
  `PENDING_ARRIVAL_CONFIRM` 取消 → 即時寫 `CANCELLATION_PENALTY` 且設 15 分鐘 Redis 冷靜期；
  扣款 idempotency（`reference = f"penalty:{order_id}:{party}"`）；冷靜期內 `POST /orders` /
  `grab` 回 429、之後自動恢復；admin 推翻 → `DISPUTE_ADJUSTMENT` 退款 且 `DEL` 冷靜期 key。
- **動錢**：`resolution` 缺省 → 422；裁決必寫 `AdminAuditLog`；`PLATFORM_TRIP_FEE` idempotency
  （`reference = "trip:<order_id>"`）；中斷必須在同一 transaction 內開 dispute（失敗則回滾）。
- **保證金閘門**：餘額 < 0 時 `grab` 回 423（非 403）；在途 `IN_TRIP` 單仍可 `/complete`；補款
  後即時恢復接單；欠費不改 `DriverStatus`。
- **預約單**：< 2h → `TOO_SOON`；> 3d → `TOO_FAR`；邊界 2h / 3d → 通過；背景 job 只在
  `prebook_visible_from <= now` 才轉 `BROADCASTING`；搶單走 `GrabService`（兩司機只有一個成功）；
  到期未匹配 → 升級即時廣播（不可靜靜取消）；**fixture test 對 `landmarks` 所有行跑
  `is_in_hong_kong()`，任何一行境外即 fail**。
- **司機 filter**：偏好只影響通知，不影響 `nearby` 可見性。

---

## 9. 待決問題（全部已拍板）

| DECISION | 已定案選項 | 生效參數 | 理由一句 |
|---|---|---|---|
| **1 — $5 平台費** | **A：從司機保證金帳戶扣** | `/start` 寫 `LedgerEntry{entry_type=PLATFORM_TRIP_FEE, amount_hkd=-5, driver_profile_id=該司機, order_id=該單, reference=f"trip:{order_id}"}`（唯一防重複扣） | A 今日可上線、零新基建；B（乘客錢包）/ C（估價 surcharge）皆假設尚未存在的基建（乘客無錢包、車資不經平台）。 |
| **2 — 中斷的即時性** | **中斷即時生效，admin 只在事後處理 dispute** | 刪 `INTERRUPT_PENDING` 及其 partial-unique-index 表；在 `INTERRUPTED` 同一 transaction 內自動開 `order_disputes`；SLA 逾期不再阻塞任何人 | 行程出事要人立即停；事後裁決靠 dispute。 |
| **3 — 保證金扣到負數** | **餘額 < 0 即不能接新單** | 閘門 `balance_hkd + held_hkd >= 0`，不足 → **423** `DEPOSIT_INSUFFICIENT`（**非 403**）；只在**接新單**時檢查，在途單不受影響；**不改 `DriverStatus`**；預警門檻 **$100**（< $100 告警、< $0 每日提醒 + 接單頁鎖定）；**補款後需人工放行**，接單閘門讀「已放行」標記而非餘額，`driver_deposits` 加 `acceptance_unlocked_at timestamptz NULL`（或 `acceptance_blocked_reason varchar(32)`） | 改動集中一個檢查點，避免觸發整條重新入職流程。 |
| **4 — 5 分鐘鎖定窗** | **已作廢** | 改由「已驗證到達」觸發（`order.status == DRIVER_ARRIVED`）；刪 `cancellation_locked_at`、`no_show_penalty_hkd_snapshot` | 系統沒有 ETA 可算。 |
| **5 — 違約罰款** | **即時扣款 + 15 分鐘冷靜期（雙方對稱）** | 乘客即時扣 **100%** 估價快照、司機即時扣 **50%**；用 `CANCELLATION_PENALTY`，`reference = f"penalty:{order_id}:{party}"`；冷靜期用 Redis TTL key `cooldown:{party}:{user_id}`（**900 秒，不落 DB**）；乘客 `POST /orders`、司機 `grab` 前檢查 → **429** `COOLDOWN` + `Retry-After`；admin 推翻 → 寫 `DISPUTE_ADJUSTMENT` 退款 **且** `DEL` 冷靜期 key | 打斷「一路違約、一路再開單」的即時套利（冷靜期是「冷卻」不是「懲罰」）。 |
| **6 — 預約服務參數** | **最短 2 小時、最長 3 天** | 少於 2h → 422 `TOO_SOON`；超過 3d → 422 `TOO_FAR`；`broadcast_lead_time` = **30 分鐘**（語意為「多早曝光」，非窗口長度）；可見性由 `scheduled_pickup_at - 30min` 起，直到 (a) 被接走 或 (b) 到點升級為即時廣播並加大半徑 | 2 小時才真正叫「預約」；未匹配不可靜靜取消。 |
| **7 — 地標清單** | **19 個即用（含深圳灣港方口岸區）** | 座標全部經 `is_in_hong_kong()` 驗證；`category` 由 7 類擴至 **9 類**（加 `VENUE`、`HOSPITAL`）；清單與座標見 §3.6.3；細節見下 | 地標 = 終點 / 下客點。 |

**非顯而易見細節：**

- **地標 = 終點 / 下客點**（第五輪）：用途 (1) 乘客選目的地、(2) 司機看單時地圖顯示終點
  地標並可按類別 filter。**上客點永遠是乘客實際位置**，故 `landmarks` 無 `pickup_point`；
  口岸地標 `location` 用**港方實際落客點**。加 `VENUE` / `HOSPITAL` 因**散場 / 探病時間高度
  集中**（紅館散場、醫院探病時段）。**未收錄**（留待決定）：離島（長洲 / 南丫島渡輪碼頭）、
  跨境巴士總站、其他主題公園（水上樂園）。
- **深圳灣口岸落客點** = 深圳灣口岸（港方口岸區）公共運輸交匯處 **`22.500992, 113.945654`**
  （OSM `bus_station` way 581117553；Wikimapia 22°30'5"N 113°56'41"E 相差約 100 米）。完整
  座標與 Google Maps 連結見 `docs/LANDMARK_COORDINATES.md` §二。
  - **法律定位**：旅檢大樓北部及相連車站由深圳市管轄；**南部及相連的公共運輸交匯處屬
    「港方口岸區」**。全國人大常委會 2006-10-31 授權香港特區在口岸內全封閉式管理、實施香港
    法律；國務院（國函〔2006〕132號）批覆範圍及土地期限。港方口岸區佔地 **41.565 公頃**，
    土地**由深圳市政府擁有**，香港**租賃**取得、每年付租，期限至 **2047-06-30**。一句話：
    **不在香港土地，但在香港境內**（判為境外是真正的 false negative）。
  - **原本的缺陷**：`_HK_MAIN` 后海灣段只有兩個頂點（`22.4600,113.9200` →
    `22.5050,113.9600`），斜率 `dlat/dlng = 1.125`；在交匯處經度 113.945654 上該邊緯度僅
    **22.488861**，比交匯處（22.500992）**低 0.0121°（≈1.34 km）** —— 開往口岸的的士在距
    目的地 1.3 公里時就「離開香港」，建單被 422 `OUTSIDE_HK` 拒絕。
  - **修法（選項 A，只挑出口岸區一塊）**：`_HK_MAIN` 的 Deep Bay 段由 **2 個頂點擴至 7 個**，
    只在口岸一帶向北繞彎（西牆刻意留 **~450 米餘量**，因交匯處有面積 + GPS 誤差）：

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

  - **驗證（實跑）**：交匯處及口岸區、大橋港方落腳點、鰲磡石、尖鼻咀、天水圍、元朗、屯門、
    龍鼓灘、落馬洲、羅湖、文錦渡、機場、港珠澳大橋香港口岸、沙頭角、東平洲、長洲 全 True；
    蛇口各處、**深圳灣口岸深圳側管制站**、南山、前海、后海灣北面水域、福田、羅湖（深圳）、
    鹽田、寶安、華強北、市民中心 全 False；網格掃描后海灣 / 蛇口角 0.0025° 步長 **212 個境內
    點**，無一點落到深圳一側。
  - **測試**：`tests/test_hk_bounds.py` 的
    `test_the_shenzhen_bay_port_area_defect_cannot_come_back`（鎖死舊兩頂點邊界）與
    `test_admitting_the_port_area_did_not_admit_shenzhen`（網格性質測試，已用兩個故意錯誤的
    邊界驗證**會 fail**）。
  - ⚠️ **此段是全模組唯一刻意伸到深圳河走廊以北的地方** —— 靠「只在港方口岸區這一小塊放
    寬」；`hk_bounds.py` 的 `_HK_MAIN` 註釋已寫明。**落馬洲 / 皇崗口岸完全在深圳境內**，
    香港無對應落客點，不會也不應成為香港終點（深圳灣不同，因為它有港方口岸區）。
- ⚠️ **上線前必須人手覆核「落客位置」**：19 個座標雖全部通過邊界檢查，但「地標中心」不等於
  「的士可停車落客的位置」。尤其 HKIA 應用**的士落客區**、迪士尼用**的士上落客區**、紅館用
  **暢運道對出**。

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
