# 架構與業務邏輯導讀

> **這份文檔的定位**：`docs/` 下的其他文檔分成兩類 —— **現行指引**（規範、安全、
> 部署、索引）與**歷史快照**（審查報告、逐行審閱、工作日誌，已歸檔到
> `docs/archive/`）。它們回答「要遵守什麼」與「之前發現了什麼問題、怎樣修的」。
> 這一節回答的是第三個問題：**系統今天是怎樣運作的，為什麼這樣設計**。
>
> 讀者假設：你懂 Python / FastAPI / PostgreSQL，但第一次接觸這個 repo。
> 讀完你應該能夠：講出「一程車由叫車到收費」的完整流程、知道錢在哪裡被改動、
> 知道哪幾條不變式是**不可以**打破的。
>
> 程式碼片段中的 `#` 註釋是**為了這份文檔加的中文說明**，不是檔案原文；
> 檔案原文的註釋是英文。原文比這裡更詳細，值得直接讀。

---

## 1. 一句話講清楚這個產品

香港的士配對平台，走**資訊中介**（information intermediary）路線，
即：平台**不提供載客服務**，只撮合乘客與司機，估價僅供參考。
這一條不是法律裝飾，它直接決定了三件事：

| 決定 | 原因 |
|---|---|
| 每個 fare 響應都帶**雙語 Cap. 374D 免責聲明** | 中介定位的法律要求 |
| 估價永遠帶 `tariff_version` | 日後有人質疑「當時為何收這個價」，要答得出 |
| 司機自行報 `distance_km`，平台不強制 | 平台不是承運人；但這確實是已知的 gaming 風險（見 §8） |

```python
# app/services/fare_calculator.py
"""Fares produced here are ESTIMATES ONLY. Every breakdown must carry the
information-intermediary disclaimer per Cap. 374D."""
```

**技術棧**：FastAPI（async）+ PostgreSQL 16/PostGIS + Redis 7 + Alembic，
SQLAlchemy 2.0 async。3.5 個客戶端：Flutter（司機／乘客／管理）、
零構建 ES-module 控制台（admin-web）、以及 WebSocket 推送通道。

---

## 2. 一程車的完整生命週期

先看業務流程，再看程式碼。這是最有效的閱讀順序——分層架構本身沒有意義，
**它是為了支撐這條流程而存在的**。

```
① 叫車          ② 廣播           ③ 搶單          ④ 到達/開始        ⑤ 完成          ⑥ 收費
POST /orders → Redis GEO → SETNX+Lua → 狀態機推進 → 凍結 fare → 週結算
               nearby      DB 條件式                          ledger 記帳
```

### ① 建立訂單——報價必須被凍結

```python
# app/api/orders.py（示意）
# 估價結果在建立訂單的一刻寫入 fare_json，之後永不重算。
# 為什麼：tariff 會在 7 月調整。如果訂單只存「距離 + 時間」，
# 日後重算就會用新價錢去結算舊行程——帳目對不上，而且無法解釋。
```

這條規則叫 **fare snapshot**，它是整個財務可審計性的起點。

### ② 廣播——Redis 只做索引，不是事實來源

訂單進入 `BROADCASTING`，同時以 GEO 成員寫入 Redis 的
`geo:orders:active`，司機用 `GEOSEARCH` 找附近的單。

### ③ 搶單——**這是整個系統最值得讀的一段併發邏輯**

```python
# app/services/grab_service.py
# 贏家：Redis SETNX 鎖 → 條件式 UPDATE ... WHERE status='BROADCASTING'
#       → rowcount == 1 → commit → 釋放鎖
lock_key = f"lock:grab:{order_id}"
acquired = await self.redis.set(lock_key, lock_token, nx=True, px=_LOCK_TTL_MS)
if not acquired:
    return False                     # 輸了：別人正在搶

# ……中間略……

# 關鍵：UPDATE 的 WHERE 條件帶 status。所以「只有 BROADCASTING 的單可以被搶」
# 是由**資料庫**保證的，不是由 Redis 鎖保證的。
result = await session.execute(
    update(Order)
    .where(Order.id == order.id, Order.status == OrderStatus.BROADCASTING)
    .values(status=OrderStatus.ACCEPTED, driver_id=profile.id, ...)
)
if (result.rowcount or 0) != 1:
    await session.rollback()
    return False                     # 有人搶先一步
```

> **為什麼要兩層？**
> Redis 的鎖 key `lock:grab:{order_id}` 是**完全可預測**的（就是 order id）。
> 任何能碰到 Redis 的人都可以 `SET` 這個 key 並永久持有，令訂單永遠搶不到——
> 這個攻擊在真實環境被驗證過（SEC-06）。
>
> 所以設計上刻意讓**正確性不依賴 Redis**：就算兩個司機同時繞過鎖，
> 第二個 `UPDATE` 也會匹配 0 行而輸掉。Redis 只負責「通常情況下更快」，
> 資料庫才是裁判。**這是整個 repo 反覆出現的一個模式**，值得記住。

釋放鎖用 Lua 腳本做 token 比對，避免「慢贏家刪掉後繼者的鎖」：

```lua
-- 只有當 key 的值仍然是我的 token 才刪——否則那是別人的鎖
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
```

### ④⑤ 狀態機——不合法轉換直接拋錯

```python
# app/services/state_machine.py
ORDER_TRANSITIONS = {
    CREATED:          {BROADCASTING, CANCELLED},
    BROADCASTING:     {ACCEPTED, CANCELLED},
    ACCEPTED:         {DRIVER_ARRIVED, CANCELLED},
    DRIVER_ARRIVED:   {IN_TRIP, CANCELLED},
    IN_TRIP:          {COMPLETED},        # 行程中不能取消，只能完成
    COMPLETED:        set(),              # 終態：無出邊
    CANCELLED:        set(),              # 終態：無出邊
}
```

這張表**同時是安全產物**，不只是領域模型。它讓
「已取消的訂單不可能被完成」成為**不變式**而不是慣例。
`tests/test_orders_module.py::TestStateMachineInvariants` 直接斷言兩個性質：

1. **終態沒有出邊**——有的話，已完成的行程可以被重開，
   而所有收益數字（週結算、analytics API）都是從 `orders` 推出來的。
2. **`CANCELLED` 只能在行程開始前到達**——永遠不可以從 `IN_TRIP` 進來。

> 作者在 docstring 明確寫下：日後 P4 要加 `INTERRUPTED`（終態）與
> `DESTINATION_CHANGED`（非終態）時，**必須重新檢查這兩個性質**。
> 這是很罕見但很有價值的做法——把「未來的自己會踩的坑」寫在檔案裡。

---

## 3. 錢：整個系統最需要小心的部分

如果只能讀一個 module，讀 `ledger_service.py`。這裡的設計密度最高。

### 3.1 帳本是 append-only 的，餘額是一條鏈

```python
# app/services/ledger_service.py
"""All money mutations (deposits, weekly fees, penalties, refunds) MUST go
through LedgerService.append; direct balance edits are forbidden."""
```

每一筆 `ledger_entries` 都記錄 `balance_after`，所以**餘額可以被重算驗證**。
這樣設計的代價是寫入要序列化，於是：

```python
# 併發（P0-1）：deposit 那一行 SELECT ... FOR UPDATE。
# 並發的 append 在這一行的鎖上排隊，所以「餘額更新遺失」不可能發生。
# 這是用**資料庫行鎖**換取正確性，而不是用樂觀重試——因為錢出錯的代價
# 遠高於偶爾的等待。
```

### 3.2 五種 entry type，各有自己的 reference 命名空間

這一段是 SEC-13，也是我認為整個 repo **最精妙的一處業務邏輯**。
背景是：舊設計讓呼叫方自己傳 `reference`，命中時**直接回傳舊列，
完全不檢查它是否符合呼叫方要求的 entry_type 與金額**。

而 `weekly:{driver}:{period}` 和 `refund:{id}` 共用**同一個扁平命名空間**。
於是任何能寫入 ledger 的人（一個管理員，或持有偽造 token 的攻擊者）
可以**預先植入** `weekly:<driver>:2099-W03`，之後真正的結算跑起來會看到
「已經收過錢」，跳過該司機，**HK$200 服務費靜靜地永遠收不到**——
而結算報告顯示為一次普通的 `skipped`。同一個手法也可以讓一筆退款
走到 `APPROVED` 卻完全沒有 `REFUND` 借方。

現在的兩道防線：

```python
# 防線一：append() 拒絕「reference 命中但 entry_type 或金額不符」的請求。
#         所以碰撞永遠不可能靜靜地把一筆收費變成 no-op。
# 防線二：所有 reference 由下面的 helper 鑄造，每個都有自己的 prefix，
#         令「跨用途碰撞」在表達上就不可能。
def reference_for_grant(driver_profile_id, client_key=None) -> str:
    return f"grant:{driver_profile_id}:{client_key or uuid4().hex}"

def reference_for_weekly(driver_profile_id, period) -> str:
    return f"weekly:{driver_profile_id}:{period}"

def reference_for_fleet_weekly(fleet_id, period, driver_profile_id) -> str:
    # 刻意與 weekly: 不同 prefix —— 兩者由不同 job 收費。
    # 共用命名空間會讓平台結算與車隊結算碰撞：要麼靜靜吞掉一筆收費，
    # 要麼雙重收費。fleet id 放進 reference，令司機的車隊史
    # 可以單憑 ledger 重建。
    return f"fleet:{fleet_id}:{period}:{driver_profile_id}"

def reference_for_refund(refund_id) -> str:
    return f"refund:{refund_id}"

def reference_for_adjustment(driver_profile_id, reason, client_key=None) -> str:
    return f"adj:..."    # 管理員人工修正，必帶原因
```

| Entry type | Prefix | 誰觸發 |
|---|---|---|
| `DEPOSIT_TOPUP` | `grant:` | 管理員批核按金 |
| `WEEKLY_FEE_DEDUCTION` | `weekly:` | 平台週結算 job |
| `WEEKLY_FEE_DEDUCTION`（車隊） | `fleet:` | 車隊結算 job |
| `PENALTY_DEDUCTION` | — | 例：$50 爽約罰款 |
| `REFUND` | `refund:` | 批核退款 |
| `ADJUSTMENT` | `adj:` | 管理員人工修正（±HK$5,000，必填原因） |

### 3.3 週結算——收入模型，以及它現在會「報警」而不是「靜默」

```python
# app/services/settlement_service.py
"""Every ACTIVE driver pays a flat weekly service fee, deducted from their
deposit balance through the append-only ledger.
Arrears are allowed by design: 餘額變負的司機仍然可以接單，下次充值時清還。"""
```

**欠款（arrears）是刻意合法的**——負餘額代表司機欠平台錢，
不是資料損壞。這一點很容易被誤解為 bug，所以在此明寫。

幂等性以 **ISO 週**為單位：

```python
def period_key(now=None) -> str:
    # ISO 週由週一起算，且歸屬於「包含其週四」的那一年，
    # 所以 key 整週穩定，且跨年不會碰撞（2026-W01 接 2025-W53，不是 2025-W01）。
    iso = (now or datetime.now(UTC)).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"
```

配合 `uq_ledger_reference` 這個 partial UNIQUE index，
job 跑兩次（崩潰後重試、排程重疊、人手觸發）也只會收一次錢。

而 SEC-13 修好之後，「已收費」變成**被驗證**而不是**被假設**：

> 舊行為：只檢查「有沒有一行持有這個 reference」，有就當作 `skipped`。
> 新行為：**比對 entry_type 與金額**，不符就回報 `tampered` 並以
> **CRITICAL** 級別記 log。
> 這就是「靜默的收入流失」與「有人會收到警報」之間的差別。

### 3.4 車隊結算，以及那個很容易漏掉的雙重收費縫隙

車隊是**持牌營運商**——牌照由運輸署發出——所以它由管理員建立，
不開放自助登記。**名冊（roster）就是收費邊界**：

```python
# app/services/fleet_service.py 的設計要點
# ACTIVE 成員會「離開」平台全站的週結算，改為按其車隊的折扣價收費。
# 一個 partial unique index 保證司機最多只出現在一份 ACTIVE 名冊上，
# 所以「第二次加入」是 409，而不是靜靜地雙重收費。
#
# 陷阱：車隊結算的幂等檢查是 per (fleet, ISO week)，
# 而平台結算的是 per (driver, ISO week)。兩者用的 reference 不同
# （fleet: vs weekly:），所以**任何一邊的幂等檢查都不會保護另一邊**。
# 平台結算因此必須主動排除已入名冊的司機，並把排除數量
# 回報為 fleet_managed——讓「被排除了幾個」是可見的，而不是猜的。
```

**這是全篇最值得注意的一段。** 兩個各自正確的幂等機制，
組合起來留下一個縫隙；修法是讓平台結算**主動排除**並**回報數量**。

---

## 4. 金額精度：三種字串，三種問題

`app/core/money.py` 很短，但它是全 repo 最容易改錯的地方。

| 函式 | 精度 | 用於 |
|---|---|---|
| `money_str()` | 2 dp | **已儲存**的金額（所有 `Numeric(10,2)` 欄位） |
| `meter_str()` | 1 dp | **跳錶讀數**（車費、隧道費、附加費） |
| `quantize_money()` | 2 dp，回傳 `Decimal` | 需要**繼續參與運算**的金額 |
| `ratio_str()` | 2 dp | **衍生**數字（平均值、比率、每日趟數） |

```python
# 為什麼不能只用一種：
# 已儲存金額在資料庫是 Numeric(10,2)，整分保存。
# 若在 wire 上序列化成 1 dp，會**在儲存之後**破壞精度——
# 餘額 0.05 會出去成 "0.1" 顯示 HK$0.10；
# 0.01 會出去成 "0.0" 顯示 HK$0.00，即一筆真實入帳顯示為零。
#
# 而跳錶價的 tariff 表每一跳都是「角」的整數倍，
# 所以 1 dp 才是它的**真實精度**，兩者回答不同的問題。
```

`ROUND_HALF_UP` 是刻意的：

```python
# Decimal.quantize 預設是 ROUND_HALF_EVEN（銀行家捨入），
# 遇上 .5 會往偶數位靠：150.45 -> 150.4。
# 但車費引擎已經是 150.45 -> 150.5，
# 留著預設值會令「同一個數字的兩種視圖」互相矛盾。
```

`ratio_str()` 修的是一個**曾經真實存在**的 bug：analytics 的比率用
half-even，而同一畫面上所有金額用 half-up，`1.005` 在這裡是 `"1.00"`、
在其他地方是 `"1.01"`。兩者單獨看都站得住腳，**同一個響應裡出現兩者不行**。

> 有一個實作細節值得學：`ratio_str` 接受 `Decimal | int | str`，
> 因為呼叫點的分母是被除出來的 `Decimal`、計數是 `int`、
> 而重讀先前算好的值是 `str`。要求呼叫方自己轉換，
> 正好會製造 `int.quantize` 的 `AttributeError`。

---

## 5. 分層架構，以及一個關鍵設計哲學

```
app/api/           HTTP 層——只做 I/O、驗證、錯誤映射。業務規則不寫在這裡。
app/services/      領域邏輯——所有業務不變式住在這裡。
app/models/        SQLAlchemy 2.0 declarative，按 bounded context 拆包。
app/core/          橫切關注點——config、money、deps（RBAC）、rate_limit…
app/api/schemas/   Pydantic 響應模型——見下方警告。
```

### 貫穿全 repo 的模式：正確性不依賴那個「快」的元件

在搶單是「正確性不依賴 Redis」，在配置層是「不依賴環境變數有設好」。
這個思路在 `app/core/config.py` 表現得最清楚——**fail-closed**：

```python
VALID_APP_ENVS = ("dev", "test", "prod")

# SEC-01：刻意用空字串做 sentinel，不是 "dev"。空的就啟動失敗。
app_env: str = ""

# 為什麼：舊版 `app_env: str = "dev"` 會把任何沒有 .env 的容器
# （也就是每一個 Docker image——Dockerfile 從不 COPY .env）
# 變成一台會把固定 OTP 123456 寫進響應內文的 dev 伺服器。
```

```python
# SEC-02：app_env 是「白名單比對」，不是「等於比對」。
# 舊檢查是 `if self.app_env == "prod"`，
# 所以 production / PROD / staging / "prod "（尾隨空格）
# 會**跳過每一項 prod 安全檢查**，讓平台帶著
# repo 內的 JWT secret 啟動——ADMIN token 可離線偽造。
if self.app_env not in VALID_APP_ENVS:
    raise ValueError(...)
```

JWT secret 不只要長，還要有真實熵值：

```python
_MIN_SECRET_CHARS = 32
_MIN_SECRET_DISTINCT = 8      # `"x" * 64` 會被拒絕
```

而在 `APP_ENV=prod` 下，app **拒絕啟動**，如果：

| # | 條件 |
|---|---|
| 1 | `JWT_SECRET_KEY` 仍是 dev 預設值 |
| 2 | `POSTGRES_PASSWORD` 仍是 dev 預設值 |
| 3 | `ALLOW_DEV_OTP` 被設為開 |
| 4 | `PUBLIC_BASE_URL` 不是 `https://` |
| 5 | `CORS_ORIGINS` 未設 |
| 6 | `CORS_ORIGINS` 含 `*` |
| 7 | `CORS_ORIGINS` 含明文 http origin |
| 8 | `SMTP_HOST` / `SMTP_FROM` 未設 |
| 9 | `TRUSTED_PROXY_COUNT < 1` |

這幾條由 `scripts/verify/prod_boot_drill.py` 用 **11 個案例**守住
（1 個正常啟動 + 10 個必須拒絕）。

---

## 6. RBAC：排名比較，不是集合成員

`AdminRole` 有四級而不是三級，因為有**兩個**職責分離要表達：

```
SUPPORT(0) < OPERATIONS(1) < FINANCE(2) < SUPER_ADMIN(3)
```

| 分離 | 理由（原文照譯） |
|---|---|
| `SUPPORT` vs `OPERATIONS` | 客服的工作是**記錄**問題，營運的工作是**決定**問題。合併的話，一線客服就繼承了 KYC 批核權——那是關於「司機可否營運」的合規判斷，不是一線任務。 |
| `OPERATIONS` vs `FINANCE` | KYC 不該被「受益於更多司機上線的人」放寬，而錢不該被「批核文件的人」調動。標準的職責分離。 |

`SUPER_ADMIN` 是唯一可以改動角色的角色：

> 如果 `OPERATIONS` 或 `FINANCE` 可以，那麼整個階層就可以被**自我提權**繞過，
> 其餘每一道邊界都會變成裝飾品。這是 RBAC 唯一一個**不能靠紀律關閉**
> 的洞，所以它由 dependency 關閉。

**排名比較而非集合成員**：

```python
# 權限檢查問 role.rank >= AdminRole.FINANCE.rank。
# 用集合的話，每次新增角色都要重讀每一組枚舉角色的 tuple，
# 而漏掉一組就是一條開放的路由。
# 排名令 default-deny 的方向自動成立：
# 日後加在底層的角色，碰不到它上面的任何東西。
```

### 兩個很容易混淆的欄位

| 欄位 | 意思 | 值 |
|---|---|---|
| `role` | **principal kind** | 管理員 token 上永遠是字面 `'ADMIN'` |
| `admin_role` | **RBAC 等級** | `SUPPORT` … `SUPER_ADMIN` |

`require_role(R)` 必須讀 `admin_role`。讀 `role` 的話，
**所有人**都會排在 `SUPPORT` 之下——這曾經令整個後台變黑。

而且 `require_role` 是 dependency **factory，必須是 `def` 而非 `async def`**
（協程物件會在 FastAPI 註冊時被拒）。

### 權威是資料庫那一行，不是 token 裡的 claim

```python
# 每個 request 都重讀 admin_accounts，不信 token 的 claim。
# 否則一個剛被降權的管理員，可以繼續動錢直到 token 過期（15 分鐘）。
# 角色需求取決於請求內容時（例如爭議裁決），用 deps.live_admin_role()
# ——row 不存在就 fail closed。
```

---

## 7. 兩張很容易踩的網：response_model 與 HTTPException cookies

這兩點都是「型別/框架行為令測試全綠、而產品在最普通的 happy path 上全壞」，
所以獨立成節。

### 7.1 `response_model=` 是過濾器，不是註解

```python
# FastAPI 會**靜默丟棄**模型沒有宣告的鍵。
# 所以：模型一定要**由真實響應的夹具反推**，
# 不可以靠讀 handler 或類比另一個 model。
```

三個實際踩過的坑：

| 坑 | 具體 |
|---|---|
| **同名 key ≠ 同型別** | `/auth/logout` 的 `revoked` 是 **int**（吊銷數量），`/admin/auth/logout` 是 **bool**。共用會令 pydantic 靜默把 `False` 轉成 `1`。 |
| **handler 有時不出某 key** | 用 `response_model_exclude_unset=True`，**不要**補 default。 |
| **金額不一定是 str** | `FareEstimateOut` 是唯一例外：`distance_km` / `waiting_min` 是**回顯呼叫方輸入**，傳 `Decimal`。改成 `str` 會令 route 拒絕自己 handler 的輸出 → 500。 |

驗證工具：`scripts/verify/audit_response_models.py` → `fixture blocks checked: 68` + `OK`。

> 方法論教訓（值得記住）：**夹具是手寫的，它可以和 bug 一致。**
> 型別必須由**真實響應**反推。

### 7.2 HTTPException 的 handler 會重建響應物件

```python
# app/core/exceptions.py 的 handler 建立全新的 JSONResponse，
# 只搬 exc.headers。
#
# 所以：clear_session_cookies(response) 之後 raise HTTPException(...)
# → 掛在 injected Response 上的東西全部掉失，cookie 不會被清。整個是 no-op。
#
# 要「拒絕的同時清 cookie」，一定要經 HTTPException(headers=...)，
# 用 app/core/admin_cookies.py 的 clear_session_cookie_headers()。
```

還有一個細節：handler 收到的是 **list** 形式的 `set-cookie`（逐條 append），
**不可以 comma-fold**——`expires=Thu, 01 Jan ...` 自帶逗號，一 split 兩條都爛。

---

## 8. 地理邊界：香港特別行政區的範圍

`app/core/hk_bounds.py` 定義服務範圍，郵政編碼那一類東西。它有**兩個刻意的例外**，
改動之前必須讀註釋並跑 `tests/test_hk_bounds.py`（87 條）。

**例外一：深圳河沿線。** 邊界跟深圳河走廊走，在落馬洲、羅湖、文錦渡、
沙頭角留下香港一側。鬆弛最多約 610 m，**不可以**到深圳建成區。

**例外二：深圳灣口岸港方口岸區。** 這一塊**故意伸到深圳河走廊以北**：

| 事實 | 值 |
|---|---|
| 法律依據 | 人大常委 2006-10-31 授權全封閉管理；國函〔2006〕132號 |
| 面積 | 41.565 公頃 |
| 司法管轄 | **深圳土地、香港司法管轄** |
| 租期至 | **2047-06-30** |
| 落客點 | `22.500992, 113.945654` |
| Deep Bay 段 | **7 個頂點** |

```python
# 不可以「推高成條邊」——那會把蛇口一併收進來。
# 只可以在口岸區那一小塊繞彎，西牆留約 450 m 餘量。
```

守衛測試：`test_the_shenzhen_bay_port_area_defect_cannot_come_back`
與 `test_admitting_the_port_area_did_not_admit_shenzhen`。

> **方法論教訓（原文，很重要）**：寫「唔應該 leak」的測試時，
> 一定要**用一個真的會 leak 的輸入證明它會 fail**。
> 第一次用的「naive fix」根本沒有 leak，報 0 violations——
> 看起來 pass，其實那個測試沒有用。

---

## 9. 客戶端與驗證方式

兩個客戶端都**對真實 API 驗證，不是對 mock**：

```bash
# 用真 API 抓 wire format 進 mobile fixture
.venv/Scripts/python scripts/dev/gen_mobile_fixtures.py

# Dart 靜態分析（經 LSP，由 Python harness 驅動）
.venv/Scripts/python mobile/tool/dart_check.py mobile
```

**為什麼 Dart 要用 Python 驅動**：在 Windows 上，Dart 用**具名管道**接子進程的
stdio，所以在這個沙盒裡 `dart analyze` / `flutter test` 全部會
`ERROR_PIPE_BUSY (231)`。`mobile/tool/dart_check.py` 因此自己寫了一個 LSP client。
它有三個「靜默出 0 diagnostics 而不報錯」的坑，已在檔案內註明。

**CI**（`.github/workflows/ci.yml`）：ruff check → format check → 完整 pytest，
配 PostGIS + Redis service containers。

> ⚠️ **CI 是 `ubuntu-latest`，而開發沙盒是 Windows。**
> 任何碰到 `Path` / `os.sep` / 絕對路徑解讀的測試，**本機綠不代表 CI 綠**。
> 例：`Path("C:/x").is_absolute()` 在 Windows 是 `True`、在 POSIX 是 `False`。
> 這曾經造成一次 CI 紅燈（見 §11）。

---

## 10. 不變式清單

以下每一條都**曾經是 bug**，現在由測試守住。改動相關程式碼前請先讀對應測試。

| # | 不變式 | 守衛 |
|---|---|---|
| 1 | 終態（`COMPLETED`/`CANCELLED`）沒有出邊 | `test_orders_module.py::TestStateMachineInvariants` |
| 2 | `CANCELLED` 不可從 `IN_TRIP` 到達 | 同上 |
| 3 | 訂單搶奪由 DB 條件式 UPDATE 裁決，非 Redis | `test_orders_module.py`（6-way 併發） |
| 4 | 所有金額寫入經 `LedgerService.append` | `test_deposits_module.py` |
| 5 | ledger reference 命中時比對 type 與金額 | `test_security_hardening.py` |
| 6 | 平台與車隊結算不共用 reference 命名空間 | `test_fleets.py` |
| 7 | 司機最多在一份 ACTIVE 名冊 | `test_fleets.py` |
| 8 | 每個 enum 欄位有 DB CHECK 約束 | `test_enum_check_constraints.py` + `test_migration_schema_parity.py` |
| 9 | 金額 wire 格式 2 dp、跳錶 1 dp、比率 2 dp half-up | `test_money_wire.py` |
| 10 | `X-Forwarded-For` 只從右邊算 hop | `test_security_hardening.py::TestForwardedFor` |
| 11 | `APP_ENV` 未設／非白名單 → 啟動失敗 | `prod_boot_drill.py`（11 案例） |
| 12 | prod 下每個必填設定缺失 → 拒絕啟動 | 同上 |
| 13 | 深圳灣口岸區在境內，蛇口在境外 | `test_hk_bounds.py`（87 條） |
| 14 | `response_model` 不丟失任何夹具的鍵 | `audit_response_models.py`（68 blocks） |
| 15 | 連線池 `(pool+overflow)×workers ≤ 100` | `test_prod_compose_pool_arithmetic.py` |
| 16 | 遷移可以在沒有 PostGIS 的 Postgres 上跑 | `test_migration_schema_parity.py` |
| 17 | 腳本不自算 repo root 深度 | `test_scripts_root.py`（26 條） |
| 18 | 路徑測試在 POSIX 與 Windows 皆成立 | `test_db_backup.py::TestShellPaths` |

---

## 11. 六個值得學的教訓

這些是這個 repo 反覆交學費換來的，寫下來避免重蹈。

**① 型別漏一個欄位，可以令測試全綠而產品全壞。**
夹具是人手寫的，它和 bug 可以一致。型別必須由**真實響應**反推，
所以有了 `audit_response_models.py`。

**② 「一樣快取」比「兩份一樣的程式碼」更危險。**
SEC-07 的 `client_ip()` 曾有五份 byte-identical 的副本，
任何一份退回 `split(",")[0]` 就會**只讓那一個 endpoint** 重新打開漏洞。
所以它現在是一個 module。

**③ 0 不等於「沒有問題」。**
`git rev-list --count origin/main..HEAD` 回 0，意思是 HEAD **等於** origin/main
（即所有改動都未 commit），**不是**「都推上去了」。
同一個數字可以有三種意思，永遠要配 `git log --oneline origin/main..HEAD` 看。

**④ 只有 boot 整支 app 的 harness 才會被新設定搞死，而它們通常不在 CI。**
`prod_boot_drill.py` 是唯一在 `APP_ENV=prod` 下啟動 app 的東西。
它靠 `_PROD_OK` 這個**單一 dict** 的設計，令新增必填設定時
happy path **大聲失敗**，而不是靜靜地少測幾條。

**⑤ 永遠不要用「一個會成功的寫入」去探權限。**
`PUT /contents/<path>` 探 GitHub 寫權限**真的會建檔並 push**。
要探就用 `POST /git/blobs`（只留 dangling object）。

**⑥ 本機綠不代表 CI 綠，只要測試碰到路徑。**
CI 是 ubuntu，沙盒是 Windows。`Path("C:/x").is_absolute()` 兩邊答案相反。
寫路徑測試要麼用 `PurePosixPath` / `PureWindowsPath` **兩邊各跑一次**證明，
要麼只斷言平台無關的性質——例如 `endswith`，而非 `startswith("C:/")`。

---

## 12. 延伸閱讀

| 想了解 | 讀 |
|---|---|
| 全部未做項與阻塞 | [`WORK_SUMMARY.md`](WORK_SUMMARY.md) §4 |
| 要動手改代碼的規範、怪癖、lint gate | [`DEVELOPMENT.md`](DEVELOPMENT.md) |
| 文檔全貌 | [`README.md`](README.md)（本目錄索引） |
| 管理後台設計 | [`ADMIN_CONSOLE_DESIGN.md`](ADMIN_CONSOLE_DESIGN.md) |
| 未實作的 in-trip 重設計 | [`IN_TRIP_REDESIGN.md`](IN_TRIP_REDESIGN.md) |
| 地理圍欄座標與法律依據 | [`LANDMARK_COORDINATES.md`](LANDMARK_COORDINATES.md) |
| 部署需求與目標決策 | [`DEPLOYMENT_REQUIREMENTS.md`](DEPLOYMENT_REQUIREMENTS.md) · [`DEPLOY_TARGET_DECISION.md`](DEPLOY_TARGET_DECISION.md) |
| 審計與修復記錄（歷史快照） | [`archive/PRODUCTION_READINESS.md`](archive/PRODUCTION_READINESS.md) |
| 安全發現 SEC-01..31（歷史快照） | [`archive/SECURITY_AUDIT.md`](archive/SECURITY_AUDIT.md) |
| 逐行審閱與 UI 審查（歷史快照） | [`archive/`](archive/README.md) |
| 腳本工具群 | [`../scripts/README.md`](../scripts/README.md) |
| Mobile 客戶端 | [`../mobile/README.md`](../mobile/README.md) |
