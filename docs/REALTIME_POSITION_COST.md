# 實時定位功能的系統資源分析

**日期**：2026-10-01
**方法**：實測，非估算。可重現的基準腳本：`scripts/verify/bench_location_pipeline.py`
**結論（先講）**：CPU 完全不是瓶頸（佔整個 tick 的 0.1%）。真正的限制是
**資料庫連線池**，而且這個上限在約 4,000 名同時在線司機時就會撞到。

---

## 1. 一個 GPS tick 由什麼組成

從 `app/api/ws.py::handle_push` 追出來，每個座標推送會依序經過四步：

| # | 步驟 | 類型 | 實測成本 |
|---|---|---|---|
| 1 | JSON 解析 + float 轉換 | CPU | 0.97 µs |
| 2 | `is_in_hong_kong(lat, lng)` 多邊形檢查 | CPU | 2.68 µs |
| 3 | DB：`SELECT` profile → `UPDATE current_location` → `COMMIT`（每次開新 session） | 網絡 + I/O | **3,783 µs** |
| 4 | Redis `PUBLISH` 到該訂單頻道 | 網絡 | **336 µs** |
|   | **合計** | | **≈ 4.12 ms** |

**CPU 只佔 3.65 µs，即 0.1%。資料庫佔 92%。**

這個結果值得強調，因為直覺會答錯。看到「香港多邊形座標檢查」很容易以為
CPU 是熱點，實際上它比一次資料庫往返便宜 **1,000 倍**。

---

## 2. 不同並發下的開銷

以設定的上限 `ws_ticks_per_second = 2`（每條連線每秒 2 個 tick）計算：

| 同時在線司機 | ticks/秒 | CPU 核心 | **所需 DB 連線** | Redis 連線 |
|---:|---:|---:|---:|---:|
| 100 | 200 | 0.00 | 0.7 | 0.1 |
| 500 | 1,000 | 0.00 | 3.6 | 0.3 |
| 1,000 | 2,000 | 0.01 | 7.2 | 0.6 |
| **5,000** | 10,000 | 0.04 | **36.2** | 3.2 |
| **10,000** | 20,000 | 0.07 | **72.4** | 6.4 |

「所需 DB 連線」= 每秒 tick 數 × 每次 tick 佔用連線的時間（3.52 ms）。
即：任何一刻，平均有這麼多連線正被 tick 佔用。

### 連線上限的預設值

```
DB 連線池   : DB_POOL_SIZE(10) + DB_MAX_OVERFLOW(20)  = 30 條（每行程）
Redis 連線池 : redis-py 預設 max_connections          = 100 條
WS 每使用者  : ws_max_connections_per_user            = 5
WS 每行程    : ws_max_connections_total               = 2000
```

DB 的兩個數字已改為由環境變數驅動（`app/core/config.py` 的 `db_pool_size` /
`db_max_overflow`），上表列的是預設值。**這兩個值必須與行程數一起決定**，
理由見 §3.5。

### 瓶頸在哪

**DB 連線池在約 4,000 名司機時飽和。**

```
30 條連線 ÷ 3.52 ms = 每秒最多容納 8,523 個 tick
8,523 ÷ 2 tps       = 約 4,260 名同時在線司機
```

超過之後：新 tick 排隊等連線（`pool_timeout` 預設 30 秒），司機端會看到
`{"type":"error"}` 或者根本沒有 ack。**這不是「慢」，是功能失效。**

同時注意 **WS 每行程上限 2,000** 會先一步觸發——單一行程最多只收
2,000 條 WebSocket，而這與 DB 上限的比值並不匹配。要真正撐到 4,000 名
司機，必須開多個行程，但那又會讓 `ConnectionRegistry`（行程內計數器）
的實際總量變成 2,000 × 行程數，失去全域約束。

---

## 3. 優化方案（按投報率排序）

### 3.1 移除每個 tick 多餘的 `SELECT`（立即可做，−17%）

`handle_push` 每個 tick 都重新查一次司機狀態：

```python
row = (await ops.execute(
    select(DriverProfile.id, DriverProfile.status).where(DriverProfile.user_id == user.id)
)).first()
if row is None or row.status != DriverStatus.ACTIVE:
    await send({"type": "error", "code": "DRIVER_NOT_ACTIVE"})
    return
```

**實測：拿掉這個 SELECT，每次 tick 由 3.52 ms 降至 2.91 ms（快 17%）。**

為何安全：連線建立時已驗證過一次身分與狀態，而 `DriverProfile.status` 在
一次行程中極少改變。改法是把狀態檢查降頻為**每 N 秒或每 N 個 tick 一次**，
而不是每個 tick——保持「停用的司機不能繼續推送」這個不變式，只是讓它
在數秒內生效而非立即。這需要一個明確的取捨：**撤銷延遲 vs DB 負載**。
建議 N = 10 秒（每 20 個 tick 查一次），撤銷延遲最多 10 秒。

### 3.2 合併 tick（最大效益，−4x 至 −10x）

司機端現在每秒送 2 次。對地圖顯示而言這過度了：

- 人類對移動標記的感知更新率約 1 Hz 已足夠流暢
- `geolocator` 的實際 GPS 精度在城市中約 5–10 m，2 Hz 並不會更準

**若改為每 2 秒 1 次，DB 負載直接降 4 倍**（上限由 4,260 升至約 17,000 名司機）；
每 5 秒 1 次降 10 倍。做法有兩個：

1. **客戶端節流**：司機 App 只在位置變化超過閾值（例如 20 m）或距上次
   推送超過 2 秒時才送。省最多，但依賴客戶端自律。
2. **伺服器端合併**：收到 tick 後只在 Redis 記下最新座標，由一個背景
   任務每 2 秒統一 flush 一次。無法被惡意客戶端繞過。

建議**兩者都做**：客戶端負責省流量，伺服器端負責省資料庫。

### 3.3 Redis PUBLISH 合併（−7%）

每個 tick 一次 `PUBLISH`（336 µs）。與 3.2 的伺服器端合併天然契合：
flush 時每個訂單只發一次。另外可考慮用 `PUBLISH` 的批次形式或
`pipeline()` 一次送多個頻道，減少往返次數。

### 3.4 只在有訂單時才寫 DB

現在的設計是只要司機在線推送就寫 `current_location`，不論是否有進行中
的訂單。但乘客只會看**進行中訂單**的位置。空閒司機的位置用途是派單
（`nearby_orders` 的距離排序），那個用途的更新頻率可以低得多（例如 10 秒）。

**建議分兩條管道**：
- **有進行中訂單** → 高頻（1–2 秒）、寫 DB、發 Pub/Sub
- **無訂單（待派單）** → 低頻（10 秒）、只寫 Redis geo index、不寫 PostGIS

這會把最壞情況的 DB 負載再砍一大截，因為大部分司機大部分時間是在等單。

### 3.5 連線池與行程數（部署層）

- 連線池是 **per-process** 的：`(DB_POOL_SIZE + DB_MAX_OVERFLOW) × 行程數`
  才是對 Postgres 提出的總需求。以原本硬編碼的 `10 + 20` 配 4 個行程即 120 條，
  而 Postgres 預設 `max_connections=100` —— **會爆**。
  **這個正確性問題已處理**：三個數字改為由環境變數驅動
  （`app/core/config.py`），並在 `docker-compose.prod.yml` 內明確寫成
  `(10 + 20) × 1 = 30 ≤ 100`，由 `tests/test_prod_compose_pool_arithmetic.py`
  守住。它解決的是**正確性**（不會超出上限），不是**擴容量**。
- 要解除「行程數 × 池大小」這個硬上限——即在單台機器上容納更多行程——
  仍需在資料庫前放 PgBouncer（transaction pooling）。屆時算式要改對
  **PgBouncer 的池大小**，不再是 api 的池大小。
- **走 PgBouncer 時必須同時把 `DB_STATEMENT_CACHE_SIZE` 設為 `0`。**
  asyncpg 預設會為每條連線快取 100 條 prepared statement，而 transaction
  pooling 下同一條交易的前後兩個語句可能落在**不同的伺服器連線**上，
  後者會以 `prepared statement "__asyncpg_stmt_N__" does not exist` 失敗。
  設 0 即停用該快取，這是讓 PgBouncer transaction mode 在各版本都安全的做法。
  直連 Postgres 時維持 100 才是對的（這是效能優化）。
- `ConnectionRegistry` 是行程內計數器，多行程下總量會超。若需要全域上限，
  應改用 Redis 計數（但會引入每連線一次 Redis 往返，需權衡）。

---

## 4. 建議的執行順序

| 優先 | 改動 | 效益 | 風險 |
|---|---|---|---|
| 1 | 司機端節流（只在顯著移動時推送） | 流量與 DB 都大減 | 低，純客戶端 |
| 2 | 移除每 tick 的狀態 SELECT（改為每 10 秒） | −17% DB | 低，撤銷延遲 10 秒 |
| 3 | 空閒司機降頻（10 秒、不寫 PostGIS） | 大幅降低平均負載 | 中，需改派單邏輯 |
| 4 | 伺服器端 tick 合併（背景 flush） | −4x DB | 中，需新背景任務 |
| 5 | PgBouncer + 池大小重算（**擴容時**才需要） | 解除硬上限 | 部署複雜度；連帶要設 `DB_STATEMENT_CACHE_SIZE=0` |

**第 5 項與前四項性質不同**：它是**擴容**手段，不是上線前的阻礙。
原本「多行程會超出 Postgres 連線上限」這個**正確性**問題，已由連線池設定化
解決（見 §3.5）；PgBouncer 要處理的是剩下的**規模**上限。

---

## 5. 這份分析沒有涵蓋的

誠實聲明範圍：

- **數字來自單機、單連線、無並發。** 真實負載下 Postgres 的鎖競爭、
  WAL fsync、Redis 的 fan-out 都會讓數字變差。這裡的價值在於**比較各階段
  的量級**，而不是預測絕對吞吐量。
- **未測 WS 連線本身的記憶體開銷。** 每個 FastAPI WebSocket 連線約佔
  數十 KB（task、buffer），2,000 條約在 100–200 MB 量級。這是估算，未實測。
- **未測網絡頻寬。** 每個 tick 上行約 40 bytes JSON、下行 ack 約 50 bytes，
  加上 WebSocket frame overhead。以 10,000 名司機 × 2 tps 計算約為
  2 MB/s 上行、2.5 MB/s 下行——對現代雲端頻寬不成問題，故未深入。

要我把第 1、2 項（低風險、效益高）實際做出來，再重跑基準驗證嗎？
