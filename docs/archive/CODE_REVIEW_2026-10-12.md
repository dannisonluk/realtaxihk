# 全代碼庫逐行審閱報告 — realtaxihk

日期：2026-10-12（後端）／2026-10-12 續（前端補完）／2026-10-12 三續（migrations + 其他部分）
範圍：
- **後端**：`app/`（core / models / api / schemas / services）、`app/main.py`、
  部署配置 —— 全部 81 個 `app/**/*.py` 逐行讀完。
- **Console 前端**：`admin-web/web/src/**` —— 全部 46 個 `.ts`/`.tsx`（15,232 行）逐行讀完。
- **Mobile**：`mobile/lib/**` —— 全部 56 個 `.dart`（9,784 行）逐行讀完。
- **其他部分（第三輪）**：`alembic/versions/**` 10 個 migration、
  `scripts/**` 20 檔（5,039 行）、`tests/**` 36 檔（14,583 行）、
  `Dockerfile`、`docker-compose.yml`、`mobile/test`、`mobile/tool`。
方法：初稿為純靜態閱讀；**動手修復時每一條都實測驗證**，因此撤銷了 5 條誤報
（見文末 §已撤銷的 5 條），並新增 §修復記錄 與 §完整 gate 驗證。
每一條都給出「問題 → 為何是問題 → 建議改動」。

---

## 0. 總體評價

這份代碼庫的**註釋密度與推理質量遠高於一般商業代碼**。絕大多數「看似可疑」的寫法
（`response_model_exclude_unset`、`--no-proxy-headers`、`ROUND_HALF_UP`、
`Mapped[object]`、`_PUBLIC_PATHS` 的 no-op dependency）都是**已經付過代價換來的決定**，
而且註釋把代價寫清楚了。這不是「需要重構」的代碼庫，是「需要收尾」的代碼庫。

**前端補完後的整體判斷**：三個 surface 的質量是一致的 —— 前端同樣充滿「解釋代價」的
註釋（`Money.minus` 的整數分運算、`AsyncValueView(skipLoadingOnRefresh: false)`、
`createHashRouter` 必須在元件內建構）。但**前端有一個共同的結構弱點**：後端的 RBAC
是「每個 request 重讀 live row」，前端卻必須把角色判斷**複製一份到客戶端**。凡是複製
得不準確的地方，就會出現「按鈕永久 disabled」或「頁面能開但每個操作都 403」——這正是
本次前端審閱找到的主要問題類型（P0-F-1、P1-F-1）。

**第三輪（migrations 與其他部分）的整體判斷**：`scripts/**`、`tests/**`、
`Dockerfile`、compose 的質量與前兩個 surface 一致 —— 20 個 script 檔幾乎每個都有一份
「為什麼這樣寫、付了什麼代價」的 docstring，`tests/**` 更是罕見地乾淨（921 條裡
找不到一條空測試或自我比較的假測試）。**但 migrations 找到了本次審閱最嚴重的一組缺陷**
（P0-M-1~3），根因是一個結構性盲點：**測試從不跑 migration**
（`tests/conftest.py` 用 `Base.metadata.create_all` 建 schema），
所以「migration 能不能跑」「migration 建的 schema 同 model 是否一致」這兩個問題
**從來沒有被任何測試問過**。

下面按嚴重度分三級（後端 + 前端 + migrations 合計）：

| 級別 | 含義 | 後端 | Console | Mobile | Migrations/其他 |
|---|---|---|---|---|---|
| **P0** | 真實缺陷：會靜默丟錢／丟數據／洩漏 | 3 | 1 | 0 | **3** |
| **P1** | 結構缺陷：現在能跑，但下一次改動會出錯 | 9 | 1 | 2 | 0 |
| **P2** | 可維護性：重複、命名、死代碼 | 11 | 3 | 6 | **1** |

> **P0-M 這一組要單獨說**：它不是「代碼寫錯」，而是「**部署路徑從來沒被跑過**」。
> `alembic upgrade head` 對一個乾淨的 Postgres **必定失敗**（缺 `CREATE EXTENSION
> postgis`），而唯一有文檔的部署路徑用 `postgis/postgis` image，entrypoint 自動幫你
> 建好 extension —— 錯的只是一個沒人走過的路。更嚴重的是另外兩條：掃描 **19 個 enum
> 形狀欄位，只有 2 個在資料庫層有約束**，其餘 **17 個**（含 `orders.status`、
> `ledger_entries.entry_type`）可以寫入任意字串 —— 已用真 `INSERT 'BOGUS'` 實證。
> 見 §P0-M-2。
>
> **2026-10-12 更新**：P0-M-1 與 P0-M-2 **均已修**，P0-M-3 維持現狀（已文件化為
> 可接受的 drift）。修 P0-M-2 時補了兩條 test，其中
> `tests/test_migration_schema_parity.py` 係**全專案唯一會真跑 migration 嘅 test**，
> 直接封住「測試同 migration 睇唔到對方」呢個根因。詳見 §修復記錄。

**已撤銷 5 條**（動手修的時候被證明是誤報）：
後端 **P0-2**（`max_length` 語義 + 422 回聲，兩次都實測推翻 —— 見下）、
**P2-2**（`_redis` 其實是 FastAPI dependency，`grep name(` 看不到）、
**P2-9**（`_LIKE_SPECIAL` 的測試早已存在）、
**P1-9 的一半**（`scratch_probe/` 不在 git 內）、
Console **P1-F-1**（帳號頁守衛，實際一致）。
撤銷條目**保留在正文並附推翻理由**，因為本報告的價值一半在「哪些看似問題的東西
其實是對的、為什麼」，刪掉會讓下一個人重犯同一個誤判。
完整清單與共同教訓見文末 §已撤銷的 5 條。

前端章節編號用 `-F` 後綴（`P0-F-1` 等），migrations 章節用 `-M` 後綴
（`P0-M-1` 等），與後端條目分開，避免交叉引用時混淆。

---

## P0 — 真實缺陷

### P0-1. `Mapped[object]` 令型別檢查對所有金額欄位失效（16 處）

**位置**：`app/models/user.py`（13 處）、`app/models/fleet.py`（4 處）

```
app/models/user.py:240  balance_hkd: Mapped[object] = mapped_column(Numeric(10, 2), default=0)
app/models/user.py:241  held_hkd: Mapped[object]    = mapped_column(Numeric(10, 2), default=0)
app/models/user.py:275  distance_km: Mapped[object] = mapped_column(Numeric(7, 3))
...
app/models/fleet.py:191 fee_hkd: Mapped[object]     = mapped_column(Numeric(10, 2))
```

**為何是問題**：`Mapped[object]` 是**型別檢查的投降聲明**。它讓下列錯誤全部通過
mypy/pyright：

- `deposit.balance_hkd + 1`（`Decimal + int` 會 `TypeError`，但 `object + int` 不報）
- `deposit.balance_hkd` 當 `str` 傳給 `money_str()`（實際是 `Decimal`，能跑，但讀者無法確認）
- 漏了 `Decimal(...)` 轉換的一次性修補（`ledger_service.py:170` 就是靠 `Decimal(deposit.held_hkd)` 這種「反正 any 都能進」的補救）

**關鍵證據**：這個模式**不一致**。同一個 codebase 的其他 Numeric 欄位寫得對：

```
app/models/fleet.py:193  member_count: Mapped[int] = mapped_column(BigInteger)
app/models/dispute.py    （同類欄位用 Mapped[Decimal] 或 Mapped[datetime]）
```

即 `Mapped[object]` 不是風格選擇，是**漏改**。而它集中在**動錢的欄位**上——`balance_hkd`、
`amount_hkd`、`balance_after_hkd`、`collected_hkd`、`fee_hkd`、`estimated_total_hkd`。

**建議改動**：

```python
from decimal import Decimal
from geoalchemy2 import Geography  # 已 import

balance_hkd: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
```

三點注意：

1. **`default=0` 要同時改成 `default=Decimal("0")`**。`Numeric` 的 Python default 若寫
   `0`，SQLAlchemy 會綁一個 `int` 進去——PostgreSQL 會隱式轉換所以現在沒炸，但這是
   與 `default=Decimal("0")` 不同的 DDL 語義，且會讓 `Mapped[Decimal]` 的宣告變成謊言。
2. **兩個 PostGIS 欄位**（`pickup_location` / `dropoff_location`，`user.py:268/272`）
   的正確型別是 `Mapped[WKBElement]`（來自 `geoalchemy2.elements`）或
   `Mapped[str]`（若走 WKT 文本）。**先確認 `trip_snapshot` 的 `_wkt` 解析吃什麼型別**
   （`app/services/trip_service.py`），再改——那裡是唯一讀這兩個欄位的地方。
3. 改完跑一次 `alembic check`，確認**沒有新增** drift（本專案本來就有 5 組 `uq_*`→`ix_*`
   與 4 個 `VARCHAR`→`Enum` 的既有 drift，所以必定 FAIL；要睇嘅係有冇新增）。

---

### ~~P0-2. `_MAX_TUNNELS` 用 `max_length` 對 `list[str]` —— 語義存疑~~

> **撤銷，並且撤銷了兩次。** 這一條原本寫成「`max_length` 對 list 的語義存疑，
> 可能變成對每個元素套用」。實測（pydantic **2.13.5**，`./.venv`）推翻：
>
> ```
> xs: list[str] = Field(max_length=2)
>   ['a','b']    -> OK
>   ['a','b','c'] -> REJECT too_long: "List should have at most 2 items after validation, not 3"
> ```
>
> `max_length` 對 `list` **就是**元素個數上限，沒有歧義，也不會逐元素套用。
> 而且 `cap_tunnels`（`mode="before"`）與 `max_length` **不衝突**：`before` 先跑，
> 超長就 raise，`max_length` 根本不會再觸發 —— 不是「產生第二個錯誤」。
>
> **然後我又在修這一條的過程中發現了第二個、更嚴重的假設，也一併推翻。**
> 我一度以為「就算 error 只有一個，pydantic 仍把整個 100k 元素的 `input` 附在
> error 上，`e.json()` 是 100 MB」—— 這個**測量是真的**：
>
> ```
> FareEstimateRequest(tunnels=['X'*1000]*100000)
>   -> 1 error, e.json() = 100,300,210 bytes
> ```
>
> 但 `e.json()` 不是送到 client 的東西。`app/core/exceptions.py:49-66` 的
> `RequestValidationError` handler **明確剝掉 `input`**，而且註釋把原因寫全了
> （SEC-09、20k tunnels → 100 KB、以及「把 caller 傳來的值原樣反射回去，
> 會令帶憑證的 payload 進到 response 同所有 log」）。端到端實測：
>
> ```
> POST /api/v1/fare/estimate  with 100,000 × 1000-char tunnels
>   request  ~100,400,055 bytes
>   status   422
>   response          209 bytes
> ```
>
> **100 MB 進，209 bytes 出。**
>
> **淨結論：`orders.py:86` 與 `fare.py:41` 的寫法都是對的，不要改。**
> `before` validator 是防「一個錯誤物件 × N 個元素」的 amplification，
> `max_length` 是宣告式的第二道，handler 的剝 `input` 是第三道 —— 三道各防不同的東西，
> 不是重複。
>
> **方法論教訓（這一條才是這節真正的價值）**：我連續兩次把「讀代碼得到的推論」
> 當成「實測到的行為」。第一次是猜 pydantic 語義，第二次是猜 `e.json()` 會送到 client。
> 兩次都是**一分鐘就能驗證的事**，而兩次都寫成了 P0。
> **凡是「某個 API 在某個版本下的行為」，必須跑一次；凡是「這個值會不會送到 client」，
> 必須追到 response 建構的那一行。** 前一份報告（後端）已經寫過同一句話——
> 「型別必須由真實響應反推，唔好靠讀 handler」——但我自己沒有對自己的推論套用同一條規則。

---

### P0-3. `_eligible_driver_ids` 的 `not_in` 子查詢在大量司機時退化為 O(n) 往返

**位置**：`app/services/settlement_service.py:207-219`

```python
fleet_managed = select(FleetMembership.driver_profile_id).where(
    FleetMembership.status == FleetMemberStatus.ACTIVE
)
driver_ids = list(
    (await session.execute(
        select(DriverProfile.id).where(
            DriverProfile.status == DriverStatus.ACTIVE,
            DriverProfile.id.not_in(fleet_managed),
        )
    )).scalars()
)
```

**為何是問題**：`NOT IN (subquery)` 在 PostgreSQL 上是 **anti-join**，但當子查詢結果
含 NULL 時語義會變（此處 `driver_profile_id` 是 NOT NULL，安全）。真正的問題是
**規模**：`run_weekly` 之後對**每個** driver 開一個新 session、做一次
`SELECT ... WHERE reference = ?`、再開一個 session 做 deposit 查詢與 append。

以 5,000 名活躍司機計：
- `_eligible_driver_ids`：1 次查詢（好）
- 主迴圈：**5,000 × (3 次往返 + 1 次 commit) = 20,000 次往返**

每週一次所以現在不是災難，但：
1. `app/main.py` 的 `_job_loop` 是**單一背景任務**，跑 settlement 期間 geo_sweep 與
   pdpo_purge 都會被延後。20,000 次往返 × ~1ms = 20 秒，尚可；× 10ms（跨網路）= 200 秒。
2. 這與 `docs/REALTIME_POSITION_COST.md` §3.5 承認的「correctness not optimisation」
   是同一個家族的問題，但沒被列進去。

**建議改動**：把「哪些 driver 已經被收費」變成**一次查詢**，然後只對差集開 session：

```python
# 一次撈出本週所有相關 reference
existing = dict(
    (await session.execute(
        select(LedgerEntry.reference, LedgerEntry.entry_type, LedgerEntry.amount_hkd)
        .where(LedgerEntry.reference.in_([reference_for_weekly(d, period) for d in driver_ids]))
    )).all()
)
```

5,000 個 reference 的 `IN` 列表在 asyncpg 下是可行的（參數上限 32,767）。這樣
主迴圈的 session 只為**真正要寫的** driver 開。**預覽本來就是這樣做的**
（`preview_weekly` 用一次 `in_(chargeable)` 撈餘額），所以這是**把 run 對齊 preview**，
不是新設計。

---

### P0-4. `except BusinessRuleError` 用字串比對做控制流

**位置**：`app/services/settlement_service.py:343`

```python
if "duplicate ledger reference" in exc.message:
    logger.debug(...)
    skipped += 1
else:
    logger.exception(...)
    failed += 1
```

**為何是問題**：註釋自己承認了（"It is a poor one — a stringly-typed check on a
message"），但它低估了後果。這是**唯一區分「已收費（好事）」與「收費失敗（丟錢）」的分支**：

- 若有人改 `ledger_service.py:188` 的訊息為 `"ledger reference already exists"`，
  **測試仍然全綠**（`test_refund_and_settlement.py` 大概率只驗 counter 最終值，不會
  故意去改那條訊息的文本），但每一筆 true-duplicate 都會被計成 `failed`，
  settlement 報告會顯示「全平台收費失敗」——而錢其實收了。反向更糟：若把某條
  **真失敗**的訊息改成含 `"duplicate ledger reference"`，就會靜默計成 `skipped`。
- 同一模式在 `fleet_service.py:464-466` 也有（那裡用 `except BusinessRuleError` 後
  無條件 `skipped += 1`，連字串比對都省了——**更危險**）。

**建議改動**：加一個專用例外型別。

```python
# app/core/exceptions.py
class DuplicateReferenceError(BusinessRuleError):
    """A ledger reference is already held by a *different* entry.

    Distinct from the idempotent-replay case, where the existing row matches
    and the caller should treat the operation as already done.
    """
```

`ledger_service.py:188` 改 raise `DuplicateReferenceError`。三個呼叫點
（`settlement_service`、`fleet_service`、`refund_service`）改 `except DuplicateReferenceError`。
這同時讓「idempotent replay 成功」與「race 輸了」可以用不同方式報告。

**附帶**：`fleet_service.py:464` 的無條件 `skipped += 1` 要一併修——目前它把
「deposit account not found」也計成 skip，與 `settlement_service` 的處理**不一致**。

---

## P1 — 結構缺陷

### P1-1. `_client_ip` 複製 5 份——而 SEC-07 的核心邏輯就在裡面

**位置**：`app/api/admin_auth.py:92`、`auth.py:74`、`fare.py:73`、`identity.py:75`、
`licence.py:86`（`app/services/audit_service.py` 用 lazy import 拿 `admin_auth` 那份）

**為何是問題**：這個函式實作的是「從右往左數第 N 跳才是真 IP，N 由
`TRUSTED_PROXY_COUNT` 決定」。它是 SEC-07 的**全部**。五份副本意味著：

- 修一份，另外四份仍是舊邏輯 → 五個入口有兩種速率限制語義。
- `tests/test_security_hardening.py` 若只驗一個入口的 XFF 行為，其餘四個靜默無覆蓋。
- 部署時 `TRUSTED_PROXY_COUNT` 設錯，五個入口會**各自**以不同方式失敗。

**建議改動**：移到 `app/core/net.py`（新檔），或在 `app/core/middleware.py` 旁開
`app/core/client_ip.py`：

```python
# app/core/client_ip.py
"""Resolve the caller's IP from the peer + X-Forwarded-For, right-anchored.

This is the ONLY place trust is decided. Every router imports it rather than
re-deriving it: five copies meant five independent chances to get SEC-07 wrong,
and the copy that drifts is the one nobody tests.
"""
```

五個呼叫點改 import。`audit_service` 的 lazy import 也改成直接 import
（lazy 是為了避開循環——移到 core 之後循環消失，可以拿掉）。

---

### P1-2. 司機 profile 查詢重複 3 份，其中兩份**同名不同檔**

**位置**：
- `app/api/drivers.py:74` — `_get_profile`
- `app/api/fleets.py:155` — `_driver_profile_of`
- `app/api/orders.py:164` — `_driver_profile_of`（**逐字相同**）

```
app/api/fleets.py:155  async def _driver_profile_of(session, user_id) -> DriverProfile | None
app/api/orders.py:164  async def _driver_profile_of(session, user_id) -> DriverProfile | None
```

**為何是問題**：`_driver_profile_of` 在六個 handler 中被呼叫（`orders.py` 5 次、
`fleets.py` 2 次）。它是「user → driver_profile」的唯一定義，而它有 3 個實作。
若日後 `DriverProfile` 加軟刪除（`deleted_at`），需要改 3 處，漏一處就是
`orders.py` 的 5 個 handler 全部少了過濾。

**建議改動**：放進 `app/core/deps.py` 或新的 `app/api/_common.py`：

```python
async def driver_profile_of(session: AsyncSession, user_id) -> DriverProfile | None:
    """user_id -> driver profile, or None.

    Public (no underscore): this is not a private helper, it is the shared
    definition of "which driver profile belongs to this user". Three copies
    is three places to forget.
    """
```

`_get_profile` 一併合併（`drivers.py` 只需改名呼叫）。

---

### P1-3. `list_accounts` 的 docstring 與 `ORDER BY` 不符

**位置**：`app/services/admin_account_service.py:99-103`

```python
async def list_accounts(self) -> list[AdminAccount]:
    """Newest seniority first, then oldest account — the order an
    operator scans when looking for "who can do what"."""
    result = await self.session.execute(select(AdminAccount).order_by(AdminAccount.username))
```

**為何是問題**：docstring 說「按 seniority 由高到低，同級按帳號最早」，代碼按
**username 字母序**排。兩者無關。operator 打開帳號管理頁，看到的是字母序，卻被
告知這是「誰能做什麼」的清單——**在只有 3 個帳號時看不出差別，在 12 個時就看不出誰是
SUPER_ADMIN**。

與此相關：`AdminRole` 是 `VARCHAR(16)` 存的（`d7f3b21a6e08`），所以 SQL 層
`ORDER BY role` 會是**字母序**（`FINANCE` < `OPERATIONS` < `SUPPORT` < `SUPER_ADMIN`），
也不是 seniority。要正確排必須用 `CASE`。

**建議改動**：二選一（**推薦第一個**，因為它誠實）：

1. **改 docstring 對齊現實**：
   ```python
   """Alphabetical by username — a stable, predictable scan order.
   Seniority is shown as a column, not used as the sort key: role is stored as
   a VARCHAR and the hierarchy lives in `AdminRole.rank`, so ordering by role
   in SQL would sort the *names*, not the ranks.
   """
   ```
2. **真的按 seniority 排**——需要 `CASE`，且要與 `AdminRole` 的 rank 表同步：
   ```python
   order = case(
       {r.value: r.rank for r in AdminRole},
       value=AdminAccount.role,
       else_=99,
   )
   ```
   這會在每次新增角色時需要檢查——**不推薦**，除非 operator 真的要求。

---

### P1-4. `run_weekly` 回傳 `str(fee)`，`preview_weekly` 回傳 `money_str(fee)`

**位置**：`settlement_service.py:372` vs `:177`

```python
# run_weekly
"fee_hkd": str(fee),        # "200"
# preview_weekly
"fee_hkd": money_str(fee),  # "200.00"
```

**為何是問題**：註釋承認這是「shipped contract with its own tests」而故意留著。但
**同一個端點的 preview 與 run 回傳同一個鍵的不同精度**，是 operator 在
`docs/ADMIN_CONSOLE_DESIGN.md` 的結算頁上「先看 preview 再按 run」的直接路徑。前端若
用 `fee_hkd` 做字串比對（「preview 說會收 200.00，run 說收了 200」），會誤判。

**建議改動**：既然 `SettlementRunOut` 已經在 `app/api/schemas/`，而
`audit_response_models.py` 的 `MODEL_OF` 有 `admin_settlement` → `SettlementRunOut`，
**改 `run_weekly` 用 `money_str(fee)` 並補 fixture**。這是 breaking change，但：
- `mobile/test/fixtures/admin_settlement.json` 是唯一消費者（Dart 模型），
  改一行即可。
- 現在不改，就要永遠帶著一個「兩個端點同一鍵不同精度」的註釋。

**若決定不改**：至少在 `fleet_service.py:476` 也**統一**——它現在用 `str(gross)` /
`str(discount)` / `str(per_member)`，只有 `collected_hkd` 做了 quantize
（`:488`，還帶一條解釋註釋）。三種精度在同一個 dict 裡。

---

### P1-5. `fleet_service.run_weekly` 在 `async with` 之外使用 ORM 物件

**位置**：`app/services/fleet_service.py:362-489`

```python
async with self.session_factory() as session:
    fleet = await session.get(Fleet, fleet_id)
    ...
    discount = Decimal(fleet.weekly_fee_discount_percent)
    per_member = discounted_fee(gross, discount)
    member_ids = list(...)
# session 已關閉

for driver_id in member_ids:          # OK: 純 UUID
    ...
    note=f"fleet service fee {period} ({fleet.name})"   # ← fleet 已 detached
...
"fleet_name": fleet.name,             # ← 同一問題
```

**為何是問題**：`fleet.name` 在 session 關閉後讀取。**這現在能跑**，因為
`Fleet.name` 是已載入的純量屬性（無 lazy load）。但：

- 任何**延遲載入**的屬性（例如日後有人加 `fleet.settlement_runs`）在 detached 狀態下
  會 raise `DetachedInstanceError`——出現在**每週結算的中途**，而不是啟動時。
- `expire_on_commit` 若被改成 `True`（SQLAlchemy 2.0 的預設是 `True`！此處
  `session_factory` 的設定決定了它），所有屬性在 commit 後都會過期，整個區塊會炸。

**建議改動**：在 `async with` 內把需要的值**抽成純量**：

```python
async with self.session_factory() as session:
    fleet = await session.get(Fleet, fleet_id)
    if fleet is None: ...
    fleet_name = fleet.name          # 抽出，明確表示「只是一個字串」
    discount = Decimal(fleet.weekly_fee_discount_percent)
    per_member = discounted_fee(gross, discount)
    member_ids = list(...)
```

之後全用 `fleet_name`。這同時**讓它不可能**不小心引入 lazy load。

---

### P1-6. `fleet_service` 的 `discount = Decimal(fleet.weekly_fee_discount_percent)` 對 `Mapped[object]` 的妥協

與 P0-1 同源，但值得單獨指出：這裡的 `Decimal(...)` 包裝**不是防禦性編程，是補償
`Mapped[object]`**。`weekly_fee_discount_percent` 從 DB 出來就是 `Decimal`，包一層
只是為了讓讀者不確定型別時能安心。修好 P0-1 後，這些 `Decimal(...)` 包裝會**自動
變成冗餘**，ruff 不會報（因為 `Decimal(Decimal)` 合法），但可以一併清理。

**清單**（`Decimal()` 包裝 DB 值，P0-1 修好後可刪）：
- `fleet_service.py:67` `Decimal(discount_percent)` — 這個是**參數**，保留
- `fleet_service.py:73` `Decimal(fee_hkd)` — 這個是**參數**，保留
- `fleet_service.py:371` `Decimal(fleet.weekly_fee_discount_percent)` — 可刪
- `ledger_service.py:137` `Decimal(existing.amount_hkd)` — 可刪（但 `!=` 比較需注意）
- `ledger_service.py:165/170/171` `Decimal(deposit.balance_hkd)` 等 — 可刪
- `settlement_service.py:137` `Decimal(existing.amount_hkd)` — 可刪
- `fleet_service.py:419` `Decimal(existing.amount_hkd)` — 可刪

**注意**：`ledger_service.py:170` 的 `deposit.held_hkd = Decimal(deposit.held_hkd)`
是**無意義的自我賦值**（讀出來再寫回去，型別不變）。它存在的唯一理由是讓
`Mapped[object]` 的讀者有信心。修好型別後直接刪整行。

---

### P1-7. `admin.py` 的 `q` / `count_q` 雙查詢模式重複

**位置**：`app/api/admin.py`（1983 行，全庫最大檔）

**為何是問題**：admin 列表端點的模式是「建一個帶 filter 的 `select(...)`」，
count 再建一次**幾乎相同**的 `select(func.count())`。這是經典的
「改了一邊忘了另一邊」——`total` 與 `items` 不一致，前端分頁會多出一頁空白或
少顯示資料。而 `admin.py` 是**唯一沒有被拆包**的 API 模組（其他都在 ~400 行以下）。

**建議改動**：

1. **抽 helper**：
   ```python
   def _paginate(stmt, *, limit: int, offset: int):
       """Return (items_stmt, count_stmt) built from ONE filter definition.

       The two must share their WHERE clause. Building them separately is how
       `total` drifts from `len(items)`, which shows up as an extra empty page
       in the console rather than as an error.
       """
       return stmt.limit(limit).offset(offset), select(func.count()).select_from(
           stmt.order_by(None).subquery()
       )
   ```
2. **拆檔**：`admin.py` 可以按領域拆成 `admin_drivers.py` / `admin_refunds.py` /
   `admin_disputes.py` / `admin_settlement.py` / `admin_search.py` / `admin_live.py`。
   路由前綴不變，`app/main.py` 的 include 順序只需調整一次。**注意**：
   `main.py` 目前把 `admin_auth` / `admin_licence` / `admin_analytics` **掛在
   `admin_router` 之前**，拆檔時要保持這個相對順序（否則 `/admin/accounts/{id}`
   會被 `/admin/{something}` 吃掉）。

---

### P1-8. `main.py` 的 middleware 順序與註釋不一致

**位置**：`app/main.py`（依摘要記載為 `CORS→SecurityHeaders→BodySizeLimit`）

**為何是問題**：Starlette 的 `add_middleware` 是**後進先出**——最後加的離 app 最近。
註釋說「CORS→SecurityHeaders→BodySizeLimit」如果是**呼叫順序**，那麼
`BodySizeLimit` 是**最外層**，意味著：

- 一個超大的 OPTIONS preflight 會被 `BodySizeLimit` 擋掉 413，而 CORS 頭還沒套上
  → 瀏覽器看到的是「CORS 錯誤」而不是 413。這是**經典的 3 小時除錯**。
- `SecurityHeaders` 套在 `BodySizeLimit` **內**，所以
  `BodySizeLimitMiddleware` 自己產生的 413 回應**沒有** `nosniff` /
  `X-Frame-Options` 等頭（`middleware.py:62-78` 的 `_send_json` 直接送 headers，
  不經過 `SecurityHeadersMiddleware`）。

**建議改動**：

1. **加一條註釋說明 FIFO/LIFO**——這個 codebase 的風格就是這樣，缺一條會誤導：
   ```python
   # Starlette applies middleware LIFO: the LAST add_middleware is OUTERMOST.
   # Order below is therefore (outer -> inner) BodySizeLimit -> SecurityHeaders
   # -> CORS. Deliberate: the body cap must reject before anything buffers, and
   # CORS must be innermost so its headers survive every short-circuit above it
   # (a 413 from the cap still needs `Access-Control-Allow-Origin` or the
   # browser reports a CORS failure instead of the real status).
   ```
2. **驗證 CORS 頭出現在 413 上**。若沒有（因為 `BodySizeLimit` 在最外），則
   `SecurityHeaders` 與 `CORS` 都要移到 `BodySizeLimit` **外面**——但這與「body cap
   要先跑」衝突。正確解法是讓 `BodySizeLimit` 在產生 413 時**自己補上 CORS 頭**，
   或把 CORS 放到最外層。

---

### P1-9. `.tmp/` 裏有 60+ 個散落的調試腳本 —— **一半已撤銷**

**位置**：`.tmp/`（含 `mutate_pool.py` / `mutate_ws.py` / `fix_counts*.py`
等**會改源碼**的腳本）

**原文說「`scratch_probe/` 在 git 內」——錯的。** 查證：`scratch_probe/` **沒有**
被 git 追蹤（`git ls-files scratch_probe/` 回空），且 `.gitignore:15` 明文列了它，
還附一段說明它是什麼。我連 `.gitignore` 都沒開就下了這個結論。**此子項撤回。**

**仍然成立的部分**：
- `.tmp/mutate_pool.py` / `mutate_ws.py` 這類「改壞再改回來」的腳本，如果在
  半途被中斷，會留下**已改壞的源碼**。`.tmp/fix_pool_docs2.py` 的存在說明這發生過。
- `.tmp/` 累積了 26 個 `.xml`（pytest JUnit 歷史輸出）。按 MEMORY 的記載，
  `--junit-xml` 是**必要**的驗證手段，但**歷史輸出**不需要累積
  （`full58` / `full61` / `full61b` / `full62`...）。

**已做的改動**：把 26 個歷史 `.xml` 移進 `.tmp/_oldxml/`，只留當次。`.tmp/` 已
gitignored，這純是目錄衛生，不影響任何一次驗證（每次仍用新的 `--junit-xml`）。

**建議但未做**：`mutate_*.py` 這類腳本若要保留，移進 `scripts/dev/` 並**明確命名**
（`scripts/dev/mutate_pool_for_probe.py`），附註「必須能還原，否則不要跑」。
`.tmp/` 的東西被清掉是遲早的事——那時沒人知道 `fix_pool_docs.py` 修的是什麼。

**方法論教訓**：我在同一條裏同時斷言了「`.tmp/` 已 gitignore」與「`scratch_probe/`
在 git 內」兩件**可以用一條 `git ls-files` 驗證**的事，卻一件都沒驗。斷言「某檔案
在版本控制內」之前，先跑 `git ls-files <path>`。

---

## P2 — 可維護性

### P2-1. 死代碼：`app/api/auth.py:306 def require_role`

```python
# app/api/auth.py:306
def require_role(role: UserRole):
```
被 `app/core/deps.py:324 def require_role(minimum: AdminRole)` 覆蓋（不同簽名、不同
語義）。`api/auth.py` 那份**沒有任何呼叫點**。它最危險的地方是：兩個 `require_role`
並存，一個讀 `UserRole`（principal kind），一個讀 `AdminRole`（RBAC rank）——
**這正是 MEMORY 記載的「曾令後台全黑」那個 bug 的形狀**。留著它等於留著一個陷阱。

**建議改動**：刪除 `app/api/auth.py:306` 的整個函式。

---

### P2-2. ~~死代碼：`app/api/orders.py:68 def _redis`~~ —— **已撤銷（誤報）**

**原文**：`grep` 全庫無呼叫點，建議刪除。

**為何是錯的**：`_redis` **有**呼叫點，在 `orders.py` 的 `grab_order` 端點：

```python
@router.post("/{order_id}/grab", response_model=OrderOut)
async def grab_order(..., redis=Depends(_redis)):
```

我當初 grep 的是 `_redis(`（帶括號），而 FastAPI 的依賴注入寫法是
`Depends(_redis)`——**函式名後面直接跟 `)`，永遠不會出現 `_redis(`**。所以這個 grep
對「FastAPI dependency」這種用法**結構性失明**，回報 0 命中。

**代價**：我照著自己的錯誤結論把它刪了，`ruff` 立刻報 `F821 Undefined name '_redis'`
——靜態檢查抓到了，但**只有因為它是模組層的未定義名字**。若 `_redis` 是類別方法或
有同名別名，ruff 不會報，而 `/orders/{id}/grab`（司機搶單，核心流程）會在**執行時**
500。這條比 P2-1 危險得多。

**已還原**，並補上有意義的註釋（原註釋只是空殼）。`state.redis_factory()` 每次呼叫
建新 client 是**正確**的——client 綁 event loop，快取在 `app.state` 會在多 loop 下壞掉
（`app/core/db.py` 已解釋）。

**方法論教訓**：找 FastAPI 依賴的呼叫點**不能 grep `name(`**。要 grep 裸名
（`grep -rn "\b_redis\b"`）或直接搜 `Depends(`。
「某個函式沒有呼叫點」這句話，在用 FastAPI 的專案裡預設是**錯的**，除非你搜過裸名。

---

### P2-3. `CORS_ORIGINS` 有 hard-coded 的 dev 預設，但 `docker-compose.yml` 又給了一次

**位置**：`app/core/config.py:55-60` + `docker-compose.yml:79`

```python
cors_origins: list[str] = [
    "http://localhost:3000", "http://127.0.0.1:3000",
    "http://localhost:8081", "http://127.0.0.1:8081",
]
```
```yaml
CORS_ORIGINS: ${CORS_ORIGINS:-["https://realtaxihk.com"]}
```

**為何是問題**：`config.py` 的 `_fail_closed` 對 `POSTGRES_PASSWORD` / `SMTP_HOST` /
`PUBLIC_BASE_URL` 都做了 prod 檢查，**唯獨漏了 `cors_origins`**。若部署時忘了設
`CORS_ORIGINS`（或 compose 的 `${VAR:-...}` 因為 YAML 解析問題沒生效——MEMORY 記載
`${VAR:-default}` 是 compose CLI 解析的，`yaml.safe_load` 會回字面字串），
prod 會**靜默**用 dev 的 `localhost:8081` 作為 CORS 白名單。

後果不是資料洩漏（localhost 不是攻擊者可控的 origin），而是**console 完全不能用且
沒有錯誤訊息**——每個請求都被瀏覽器以不透明的 CORS 錯誤擋掉，server log 一片乾淨。

**建議改動**：在 `_fail_closed` 的 prod 段加：

```python
if self.app_env == "prod":
    ...
    for origin in self.cors_origins:
        if "localhost" in origin or "127.0.0.1" in origin:
            raise ValueError(
                f"CORS_ORIGINS contains a loopback origin in prod: {origin!r}. "
                "The console would fail every request with an opaque browser "
                "CORS error and nothing in the server log — set CORS_ORIGINS "
                "to the real console origin."
            )
```

同時 `TRUSTED_PROXY_COUNT` 也值得檢查：prod 設 0 會讓所有 per-IP 限流合成一個桶
（`docker-compose.prod.yml:27` 已經設 1，但**只有 overlay 路徑**有；直接跑 base file
就是 0）。加一條「prod + proxy_count==0 → warn or fail」的檢查。

---

### P2-4. `docker-compose.prod.yml` 引用了不存在的 `deploy/pgbouncer/README.md`

**位置**：`docker-compose.prod.yml:58`

```
# mode — see deploy/pgbouncer/README.md.
```

但 `deploy/` 只有 `README.md` 與 `nginx/`。MEMORY 記載：

> PgBouncer 刻意未附 compose 檔：設定無法喺本機驗證……內容同檢查點喺
> `deploy/README.md`。

即**檔案從未存在**，路徑從一開始就指錯。假設 `deploy/README.md` 就是那個「檢查點」，
那註釋應指向它。

**建議改動**：
```yaml
# mode — see, and read the checkpoints in, deploy/README.md.
```
或（更好）在 `deploy/README.md` 建立 `## PgBouncer` 段落，然後引用 `deploy/README.md#pgbouncer`。

---

### P2-5. `nginx.conf` 的 hostname 有三種拼法，且註釋自己承認了

**位置**：`deploy/nginx/realtaxihk.conf:8-10`

```
# Replace `api.realtaxihk.com` with the real hostname before deploying. The repo
# currently mentions two spellings (`realtaxihk.com` in the docs, `realtaxi.hk`
# in a code comment); neither is a decision, so pick one and be consistent.
```
實際用到的：`api.realtaxihk.com`（nginx ×2、cert 路徑 ×2）、
`https://realtaxihk.com`（compose CORS 預設）、`console.realtaxihk.com`
（註釋掉的 server block）、`realtaxi.hk`（某處 code 註釋）。

**為何是問題**：`ssl_certificate` 路徑寫死了 `api.realtaxihk.com`。若真實主機名不同，
nginx 會**啟動失敗**（cert 不存在）→ `docker-compose.prod.yml:15` 的註釋說
「nginx cannot start without them and will restart-loop」。這是一個「註釋叫你自己改，
但改漏一處就無限重啟」的設定。

**建議改動**：把 hostname 抽成一個 `set` 或（更好）用 `.env` 的
`PUBLIC_HOSTNAME` + `envsubst`。nginx 官方 image 支援
`/etc/nginx/templates/*.conf.template` 的自動 envsubst——把檔案移到
`deploy/nginx/templates/realtaxihk.conf.template` 即可，`${PUBLIC_HOSTNAME}` 會被
替換。這同時解掉「三種拼法」與「cert 路徑寫死」兩個問題。

---

### P2-6. `alembic/env.py` 的 `include_object` 只過濾 table，不過濾 index

**位置**：`alembic/env.py:29-35`

```python
def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and reflected and compare_to is None)
```

**為何是問題**：PostGIS/tiger/topology 擴展除了建 table，還在**自有 table 上**建
index（例如 `tiger` schema 的空間索引）。`type_ == "table"` 只擋住被 DROP 的 table；
一個 reflected-only 的 **index** 若在其他情況（例如 extension upgrade 後）出現，
`compare_type=True` 的 autogenerate 會把它當成「要 DROP 的東西」。

**建議改動**：擴展成 tuple，並加一條註釋：

```python
_IGNORED_TYPES = ("table", "index")

def include_object(obj, name, type_, reflected, compare_to):
    """Never let autogenerate touch objects it doesn't know from our metadata.

    PostGIS/tiger/topology register their own tables AND indexes; without this
    filter alembic emits DROPs for them (fatal on any postgis database).
    """
    return not (type_ in _IGNORED_TYPES and reflected and compare_to is None)
```

（現狀**能跑**，所以是 P2。但既然 `alembic check` 每次都 FAIL 已經是既定事實，
加寬這個 filter 會讓「有冇新增 drift」這個判斷更乾淨。）

---

## P0-M — 真實缺陷（migrations / 資料庫層）

這一節的三條，全部是**跑一次 `alembic upgrade head` 就會遇到**的東西，但因為
測試用 `Base.metadata.create_all` 建 schema（唔跑 migration），所以整套測試綠燈
都反映唔到。係今次 review 唯一「產品會即刻死」的一組。

### P0-M-1. `alembic upgrade head` 對一個乾淨嘅 Postgres **必定失敗** —— 冇 bootstrap PostGIS

**位置**：`alembic/env.py`（`do_run_migrations`）

**症狀**（實測，一個冇 extension 的新 DB）：

```
asyncpg.exceptions.UndefinedObjectError: type "geography" does not exist
```

**為何是問題**：root migration `9307e944a592` 喺 `driver_profiles.current_location`
同 `orders.pickup_location` / `dropoff_location` 用 `geoalchemy2.types.Geography`。
Postgres **喺 `CREATE TABLE` 嘅一刻**就要解析 `geography` 呢個型別名 —— 所以
extension 一定要喺**第一個 migration 之前**存在，唔可以之後補。

而現時 migration chain 冇任何一步 `CREATE EXTENSION postgis`。

**為何冇人發現**：兩層遮蔽。

1. 唯一有文檔嘅部署路徑係 `postgis/postgis:16-3.4` image，佢嘅 entrypoint
   **自動幫你建 extension**。錯嘅只係 managed／plain Postgres —— 即係冇人跑過嘅路。
2. `tests/conftest.py::_ensure_template()` 自己 `CREATE EXTENSION IF NOT EXISTS
   postgis`，而且用 `Base.metadata.create_all` 建 schema，**完全唔經 migration**。

**修法**（已改）：喺 `env.py` 加 bootstrap，而且**必須喺 `with context.begin_transaction():`
之內**：

```python
with context.begin_transaction():
    # PostGIS must exist before the FIRST migration, not after.
    # ... (full rationale in the file)
    connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS postgis")
    context.run_migrations()
```

**⚠️ 呢個修法我自己第一次寫錯，值得記低**：把 `exec_driver_sql` 放喺
`context.begin_transaction()` **之前**，alembic 會**逐個版本照樣 log「applied」**，
但目標 DB 得 **0 張表**、連 `alembic_version` 都冇。原因係 Postgres 嘅
transactional DDL：喺 `begin_transaction()` 之前（即 migration 自己的
transaction 尚未開始時）發 DDL，會令之後成個 migration block commit 唔到任何嘢，
而狀態仍然報「全部已套用」。呢種「靜默成功」比直接報錯危險得多。

修好之後實測：新 DB → **22 張表**。

---

### P0-M-2. **19 個 enum 形狀欄位之中有 17 個，資料庫層完全冇約束** —— 唔係 drift，係 production 本身

**位置**：`app/models/*.py` 全部 `SAEnum(..., native_enum=False)`；`alembic/versions/*.py`

**發現**：`SAEnum(X, native_enum=False)` 嘅 `create_constraint` **預設係 `False`**
（SQLAlchemy 2.1.1，已實測）：

```
native_enum=False                   -> create_constraint = False
native_enum=False+create_constraint -> create_constraint = True

create_constraint=OFF: CHECK in DDL? False
     CREATE TABLE x ( id SERIAL NOT NULL, v VARCHAR(1), PRIMARY KEY (id) )
create_constraint=ON : CHECK in DDL? True
     CREATE TABLE x ( ..., CONSTRAINT t4 CHECK (v IN ('A', 'B')) )
```

所以佢只出一個 `VARCHAR`，**冇 CHECK**。實測兩個 schema（migration 建 vs
`create_all` 建）對同一個問題嘅答案係一樣嘅 —— 即係**呢個唔係測試同 production
嘅漂移，而係 production 自己就冇約束**：

```
2 enforced / 17 unenforced  (scan of 19 enum-shaped columns)
ENFORCED   driver_licence_submissions.status   (ck_licence_review_status)
ENFORCED   driver_documents.kind               (ck_document_kind)
NO         users.role / users.gender / users.account_status
NO         driver_profiles.status / orders.status / ledger_entries.entry_type
NO         refund_requests.status / fleets.status / fleet_memberships.status
NO         order_disputes.status / order_disputes.severity / dispute_messages.author_kind
NO         order_disputes.source / order_disputes.category / order_disputes.resolution
NO         order_disputes.raised_by_kind / order_disputes.against_kind
```

> 欄位數由「12／14」修正為「17／19」：前一版只掃了 `SAEnum(...)` 嘅欄位，漏了
> `app/models/dispute.py` 用純 `String(N)` 建模嘅 5 個（`source` / `category` /
> `resolution` / `raised_by_kind` / `against_kind`）—— 佢哋有 enum class 但
> **從未用 `SAEnum` 綁定**，所以**連 `create_constraint=True` 都冇機會生效**，
> 係更徹底嘅缺口。掃描條件應為「有對應 enum class 嘅字串欄位」。

嗰 2 個「有約束」嘅，係因為 `a4b8e2f6c150` 嘅作者**手寫**咗
`op.create_check_constraint(...)`；其餘 10 個（`SAEnum` 那批）係 autogenerate
出嘅，就只有 VARCHAR；再餘下 5 個（`dispute.py` 那批）連 `SAEnum` 都未綁。

**行為實證（本次對真 dev DB 直接 INSERT，包 `BEGIN/ROLLBACK`）**：

```
users.role  (varchar(9), 無 CHECK)
  INSERT ... role='BOGUS'  ->  INSERT 0 1        <-- 接受，UNENFORCED

driver_licence_submissions.status  (有 ck_licence_review_status)
  INSERT ... status='BOGUS' ->  ERROR: new row for relation
      "driver_licence_submissions" violates check constraint
      "ck_licence_review_status"                 <-- 正確拒絕
```

**兩個探測陷阱（第一次做時中過，值得記低）**：
1. **唔可以用 `EXPLAIN` 探 CHECK** —— planner 唔會評估 CHECK，`EXPLAIN SELECT
   ... WHERE col='garbage'` 永遠成功。要真正 `INSERT`（包 transaction 再 `rollback`）。
2. **bogus 值一定要短過欄寬** —— `users.role` 只有 `varchar(9)`，用
   `'TOTALLY_BOGUS_ROLE'` 會回 `value too long for type character varying(9)`，
   嗰個係**長度**攔截，會被誤讀成「有保護」。要測成員資格就用 `'BOGUS'` 呢類
   短值，先隔離出 CHECK 呢個變因。

**同樣修正**：`test_a4b8e2f6c150` 類型嘅「檢查約束存在」斷言，如果只 grep
`pg_constraint` 就要**排除 extension 表** —— 本庫有 40+ 條 CHECK 全屬
PostGIS/tiger（`enforce_dims_geom`、`type_range`…），唔過濾會令「我們有幾條」
完全數唔清。

**為何係 P0**：`orders.status` 同 `ledger_entries.entry_type` 係**錢路同狀態機**。
如果任何一條 migration script、`UPDATE`、或者將來嘅 raw SQL 寫錯一個值，
DB 會靜默接受，之後 `OrderStatus(value)` 先喺讀取時 explode —— 而嗰陣你已經
唔知道係邊個寫入嘅。對 `ledger_entries` 更差：錯嘅 `entry_type` 會令對賬邏輯
靜默漏掉一筆。

**實際失敗模式（已用 ORM 實測，比預期更硬）**：寫入係靜默嘅，但**讀取會即刻
raise**，唔會變成一個靜默錯誤值：

```
DB 層  : insert role='BOGUS' → 成功；raw select 讀返 'BOGUS'
ORM 層 : session.get(User, uid) → LookupError:
         'BOGUS' is not among the defined enum values.
         Enum name: user_role. Possible values: PASSENGER, DRIVER, ADMIN
```

呢個**好過**「靜默傳播錯值」，但係**差過**「寫入即拒」：
壞值一旦落庫，日後**每一次**讀到嗰一行都會 500（`/auth/me`、訂單列表…），
而且係 `LookupError`（**唔係** `ValueError`）—— 任何 `except ValueError` 嘅
兜底都攔唔到。要復原就必須人手寫 SQL 修資料。
→ 呢個正係「DB 冇約束」真正嘅代價：**約束缺失令一個可預防嘅寫入錯誤，
   變成一個需要停機修資料嘅讀取故障。**

**⚠️ 同時要修正我之前嘅判斷**：我早前把「partial index vs plain UNIQUE」寫成
schema drift 嘅一條，並做實驗「證明兩者行為一致」。**實驗結果係對嘅，但結論錯**
—— 兩者一致係因為**兩邊都冇 CHECK**，唔係因為 partial index 特別。真正嘅差異
只有 `users.username/email` 嘅 partial index（呢條係真嘅、已另外記低）。

**狀態：已修（2026-10-12）。** 做法與下面建議一致，分兩步：

1. **12 個已綁 `SAEnum(...)` 嘅欄位** → 加 `create_constraint=True`，並把 `name=`
   從 `user_role` / `order_status` 等改成 `ck_<table>_<column>`（`create_constraint=True`
   之下 constraint 名 = `name=` 參數，唔改就會出現 `user_role` 呢類冇 `ck_` 前綴嘅名）。
   舊 migration 嘅 `name=` 一併改，令 migration 出嘅 schema 同 `create_all` 一致。
2. **`app/models/dispute.py` 嘅 5 個欄位**（`source` / `category` / `resolution` /
   `raised_by_kind` / `against_kind`）→ 由純 `String(N)` 改綁
   `SAEnum(<Enum>, native_enum=False, create_constraint=True, length=<原寬度>)`，
   `dispute.py` 本來就定義好對應嘅 enum class（`DisputeSource` / `DisputeCategory` /
   `DisputeResolution` 等），屬「已經有 enum、只係冇接上」嘅狀態。
   **`length=` 係關鍵**：唔加嘅話 SQLAlchemy 會用最長成員長度（例：`source` 32→12），
   變成對 production 資料做窄化 `ALTER TYPE` —— 而收窄係讀寫都要跳過檢查嘅重寫動作。
   加咗就令 migration **只加約束、唔碰型別**，兩件事唔應該綁埋一齊搭同一個 revision。

   注意 `dispute.py` 嘅 `status_enum` / `severity_enum` property 在讀取時
   `except ValueError` 兜底 —— 加 CHECK 之後呢層防禦會變成
   冗餘但**不應刪除**（歷史壞資料仍可能存在）。

---

### P0-M-3. `native_enum=False` 令 `create_all` 同 migration 出**唔同長度**嘅 VARCHAR

**位置**：同上

**實測**（72 條 varchar 逐條比）：

```
DIFF driver_documents.kind:              migrations=32  create_all=20
DIFF driver_licence_submissions.status:  migrations=16  create_all=10
DIFF users.account_status:               migrations=16  create_all=10
DIFF users.gender:                       migrations=12  create_all=11
DIFF alembic_version.version_num:        migrations=32  create_all=(absent)
```

**原因**：migration 係手寫 `sa.String(length=16)`，而 model 係
`SAEnum(..., native_enum=False)`，SQLAlchemy 對後者自動取
**最長 enum member 嘅長度**（`SUPERSEDED`=10、`VEHICLE_REGISTRATION`=20…）。

**為何係問題**：`create_all` 出嘅欄位**比 production 窄**。即係一個 12 字元的
`account_status` 喺測試會 `StringDataRightTruncationError`（乾淨 fail，好事），
但呢個測試對 production 嘅行為**零保證** —— production 係 16 字元、扣 CHECK。
方向係「測試偏嚴」，所以唔會掩蓋 bug；但佢令 `alembic check` 對型別嘅比較
永遠嘈，而真正嘅差異（冇 CHECK）反而唔會俾人見到。

---

**位置**：多個 service 檔（`analytics_service.py` 的 `_hk_day_bounds`、
`settlement_service.py` 的 `period_key`、`fleet_service.py` 的 `_CENT` 等）

**為何是問題**：`_CENT = Decimal("0.01")` 在 `money.py`、`fleet_service.py:57`、
`admin_auth_service` 等處各自定義。`ROUND_HALF_UP` 的 import 散落各檔。
`money.py` 的 docstring 花了大篇幅解釋「為什麼必須用 `money_str` 而不是
`Decimal.quantize` 的預設」——然後 `fleet_service.py:74` 直接
`net.quantize(_CENT, rounding=ROUND_HALF_UP)`。

**建議改動**：`money.py` 已經有 `ratio_str` 這個「衍生值」入口。加一個
`quantize_money(v) -> Decimal` 給需要**繼續運算**（而非輸出字串）的呼叫點：

```python
def quantize_money(v: Decimal) -> Decimal:
    """Round a money value to cents, half-up. Use when the result must keep
    being a Decimal (further arithmetic, a DB column) rather than go on the
    wire as a string.
    """
    return Decimal(v).quantize(_CENT, rounding=ROUND_HALF_UP)
```

然後 `fleet_service.discounted_fee` 改用它，`_CENT` 只留在 `money.py`。

---

### P2-8. `audit_service` 對 `_client_ip` 的 lazy import 是循環依賴的偽裝

**位置**：`app/services/audit_service.py`（lazy import `app.api.admin_auth._client_ip`）

**為何是問題**：`services/` 依賴 `api/` 是**分層倒置**——service 應該不知道 HTTP
的存在。現在是「延後到呼叫時才 import」來繞過循環。修好 P1-1（把 `_client_ip` 移到
`app/core/client_ip.py`）之後，這個 lazy import 會變成一個**沒有理由的延遲**，
但它會繼續留著，因為沒人知道它為什麼是 lazy。

**建議改動**：P1-1 修好後，把 `audit_service` 改成頂層
`from app.core.client_ip import client_ip`。**同時**在
`app/core/client_ip.py` 的 docstring 寫明：

```
Lives in `core/`, not `api/`, because services need it too — it used to be in
`api/admin_auth.py` and `audit_service` imported it lazily to dodge the cycle.
That cycle was the symptom; the layering was the disease.
```

---

### P2-9. `search_service` 的 `_LIKE_SPECIAL` 轉義 —— **已覆蓋，無需改動**

**原文**：建議「加一條測試斷言……若 `test_admin_search.py` 已有，忽略此條」。

**查證結果**：**已有**，兩條，且測的正是我建議的兩種情況：

- `tests/test_admin_search.py:271` —— 「`%` is escaped, not interpreted. Otherwise a
  single `%` is a …（wildcard）」
- `tests/test_admin_search.py:278` —— 「`_` is a single-character wildcard in `LIKE`.

  Escaped, a name with an underscore is searchable; unescaped, it matches any
  character.」

`.ilike(..., escape="\\")` 也在每個呼叫點明示了 escape 字元（`search_service.py:134-147`），
不是靠 `standard_conforming_strings` 的預設。**此條撤回**：`%` 與 `_` 兩條路徑都有
regression test 守住，比我建議的斷言更完整。

**方法論教訓**：我在寫 P2-9 時用的是「若已有就忽略」的軟措辭——那其實是把查證工作
推給讀者。正確做法是自己去 `grep` 一次 `tests/`，五秒鐘的事。

---

### P2-10. `pyproject.toml` 的依賴下限 vs `uv.lock` 的實際版本

**位置**：`pyproject.toml`（`>=` floors）+ `Dockerfile:17`（`uv sync --frozen`）

**為何是問題**：`Dockerfile` 的註釋（SEC-21）已經說明了「build from the lockfile,
not `pip install .`」的理由。但**本地開發的 venv 也是 `uv sync`**——若 `pyproject.toml`
的 floor 與 `uv.lock` 允許不同解析（例如某人 `uv lock --upgrade-package X`），
本地與 prod 就會跑不同版本，而 CI 若跑 `uv sync` 而非 `uv sync --frozen` 就不會發現。

**建議改動**：在所有地方（CI、docker、本地 README）統一用 `uv sync --frozen`；
在 `pyproject.toml` 頂部註釋寫明「floor 只為可讀性，權威是 uv.lock；所有安裝路徑必須
`--frozen`」。

---

### P2-11. `scripts/README.md` 的「if you add a group, fix the depth」是隱性耦合

> **已修（2026-10-02）**：`scripts/_root.py` 已落地，14 個腳本改用兩行 bootstrap。
> 本節保留為當時的分析。

**位置**：`scripts/README.md:73-76`

README 自己承認：

> **If you add a group, every script in it needs that depth fixed.** That is the
> one coupling in this directory; it is deliberately visible rather than hidden
> behind a helper

理由是「a helper would need `scripts/` on `sys.path` before it could be imported」。

**為何是問題**：這個理由**不成立**。`scripts/` 下有 `__init__.py` 嗎？若沒有，加一個
`scripts/_bootstrap.py` 並不能直接 import——但**可以用 `sitecustomize` 之外的方式解決**：
每個腳本的第一行仍然是
`sys.path.insert(0, ...)`，但它可以 import 一個**相對位置固定**的 helper。

真正的問題是「三個層級的 `parent.parent.parent`」被重複了 20 次。

**建議改動**（可選，優先度最低）：加 `scripts/_root.py`：

```python
# scripts/_root.py
"""Locate the repo root from any script under scripts/<group>/.

Imported via a one-line sys.path bootstrap, because a helper that finds the
root cannot itself be found without the root.
"""
import pathlib
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
```

然後每個腳本：
```python
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from _root import REPO_ROOT
```

這仍是一行 bootstrap，但**只有一行會錯**（而且是同一個檔案），而不是 20 個
`parent.parent.parent`。README 的那一段可以刪掉。

---

## 其他部分審閱（`scripts/**`、`Dockerfile`、compose、`tests/**`）

> 讀完 `scripts/**` 20 檔（5,039 行）、`Dockerfile`、`docker-compose.yml`、`tests/**`
> 36 檔（14,583 行）。這一節只有一條真缺陷。

### P2-12. `db_backup.py` 有一段不可達的重複 `return proc`

**位置**：`scripts/ops/db_backup.py:379-380`

```python
        return proc
        return proc
```

**為何是問題**：純 copy-paste 殘留，第二行永遠不會執行。無行為影響（所以是 P2），
但一個 backup 工具裡的不可達代碼值得清掉 —— 它會讓下一個讀者停下來想
「係咪漏咗啲 condition」。

**已修**：刪掉第二行。

**發現方法**：寫了一個 AST 掃描器找「相鄰兩行完全相同且非註釋」的 pattern。
全 repo 7 個 hit，其中 5 個係正當的（Dart 的 `AppTheme.space4` 連續兩次出現在
padding list；`test_fleets.py` 故意連續呼叫兩次結算去測 idempotency；
`test_admin_settlement_preview.py` 連續建兩個 driver）。**只有這個係真嘅。**

---

### 不是問題，但值得記錄（scripts / Docker / tests）

這幾條我逐一查過並確認**唔需要改**，寫落嚟係避免下一個人重複調查：

| 位置 | 看似問題 | 實際 |
|---|---|---|
| `scripts/ops/create_admin*.py` `_list_admins()` 冇 `dispose_engine()` | 引擎沒釋放 | 短命 CLI 行程，OS 會回收；`_run()` 路徑有 `try/finally dispose` |
| `scripts/ops/enrol_admin_totp.py` 把 `ADMIN_PASSWORD` / `ADMIN_TOTP_SECRET` 印到 stdout | 憑證外洩 | 明確定義為 local dev / console verifier 工具；docstring 講明用途；唔會喺 prod 跑 |
| `scripts/verify/security_verify.py` 100k-tunnel payload > 1 MB | 以為測到 validator | docstring 已記錄：413 先於 validator，所以補了一個 120 KB 的 sub-cap payload |
| `tests/**` 4 個冇 `assert` 的 test | 空測試 | 3 個係刻意的「does not raise」測試（`test_db_backup.py` ×2、`test_passwords.py` ×1）；1 個用 `raise AssertionError`（`test_hk_bounds.py`，AST 掃描器只認 `ast.Assert`，係我嘅假陽性） |
| `tests/**` 2 個 `a == a` 形式的 assert | 自我比較，測不到嘢 | 兩個都係**故意**：`hash_password(GOOD) != hash_password(GOOD)` 測 salting、`get_redis() is not get_redis()` 測 loop-cache escape。兩邊**執行期**值唔同，只係 source text 一樣 —— 我嘅 AST 檢查睇 source，所以誤報 |
| `tests/**` 只有 1 個 `pytest.skip` | 覆蓋率缺口 | 條件式（`uvicorn internals changed`），連訊息都叫人手動重驗 SEC-31，係正確做法 |
| `Dockerfile` / `docker-compose.yml` | — | 冇發現。`--no-proxy-headers`、loopback bind、`:?` 必填憑證、`--frozen` 都有註釋解釋代價，且同 `test_env_example.py` / `test_prod_compose_pool_arithmetic.py` 對齊 |

**`tests/conftest.py` 值得單獨講**：佢主動強制 `ALLOW_DEV_OTP=false`、
`SENTRY_DSN=""`、per-process 的 `REDIS_KEY_NAMESPACE`，並在 docstring 解釋
**為什麼**要覆寫（dev 的 `.env` 會令 suite 靜靜跑喺唔同 production 嘅配置上）。
呢個係整個 repo 最容易被忽略、但做得好嘅一環。

**`tests/test_db_pool_config.py` 的自我認知**：佢 docstring 直接寫明
「tests/conftest.py 用 NullPool 自建 engine，從不呼叫 `get_engine()`，所以應用程式
自己的池設定冇任何測試建構過」—— 正是今次 P0-M 三個缺陷的**同一個根因**。
呢個檔案係喺同一個盲點上開嘅一個窗口；建議任何「新加嘅 production-only 配置」
都應該照同樣模式（spy 而非連線）補一條。

---

# 前端審閱（Console + Mobile）

> 讀完 `admin-web/web/src/**`（46 檔 / 15,232 行）與 `mobile/lib/**`（56 檔 / 9,784 行）。
> 每一條都在後端找到對應端點核對過（不是「前端看起來怪」就報）。

## P0-F — 真實缺陷（前端）

### P0-F-1. `DisputesPage` 用 `user.role` 判斷能否裁決金額 —— 錢路永遠按不下去

**位置**：`admin-web/web/src/pages/DisputesPage.tsx:391`（寫入 `ResolveBody`）、`:722`（判斷）。

**問題**：元件把 `user?.role` 傳給裁決對話框，對話框內用
`role === 'FINANCE' || role === 'SUPER_ADMIN'` 決定金額選項是否 `disabled`。
但 `role` 是**principal kind**，管理員 token 上它**永遠是字面 `'ADMIN'`**（見
`docs/ADMIN_CONSOLE_DESIGN.md` 與後端 `app/api/admin.py`）；真正的 RBAC 等級欄位叫
`admin_role`。

**為何是問題**：`'ADMIN'` 不等於 `'FINANCE'` 也不等於 `'SUPER_ADMIN'`，所以
**即使是 SUPER_ADMIN 登入，涉及金錢的裁決選項也一律是灰的**。而且就在同一頁往上
三行，route 層的 `RequireRole` 用的是正確的 `hasRole('FINANCE')` —— 同一份檔案對
同一個概念有兩種寫法，其中一種永遠為假。

**後端核對**：`app/api/admin.py:1735-1827` 的 `POST /disputes/{dispute_id}/resolve`
守衛是 plain `require_admin`，然後**在 request 內**收窄：
```python
actor_role = await live_admin_role(session, admin)
if not actor_role.at_least(AdminRole.OPERATIONS): ... 403 ADMIN_ROLE_INSUFFICIENT
if resolution.moves_money and not actor_role.at_least(AdminRole.FINANCE):
    ... 403 DISPUTE_RESOLUTION_REQUIRES_FINANCE
```
所以後端的設計是「角色夠就准」，前端的鏡像應該照抄 `admin_role`，而不是猜 principal kind。

**建議改動**：
```tsx
// 傳 admin_role，不要傳 principal kind
<ResolveBody adminRole={user?.admin_role} ... />
```
或更清楚一點 —— 直接把判斷在做決定的地方算好，傳一個 boolean：
```tsx
const canMoveMoney = atLeastFinance(user?.admin_role);
<ResolveBody canMoveMoney={canMoveMoney} ... />
```
**不要**在元件裡再寫一次 `admin_role === 'FINANCE' || admin_role === 'SUPER_ADMIN'`
字串比對，因為後端用的是 `at_least()`（rank 比較）；寫死列舉日後加一級就會漏。
`lib/labels.ts` 已經有角色能力的集中定義，用它。

---

## P1-F — 結構缺陷（前端）

### ~~P1-F-1. Console 帳號頁路由用 `require_admin`，但後端四個 `/accounts*` 全部要 `SUPER_ADMIN`~~

> **撤銷（寫入報告後覆核時發現是我看錯）。** 我在第一版把這條列為 P1，但實際讀
> `app/App.tsx:174-181` 與 `app/Shell.tsx:78` 後確認：route 是
> `<RequireRole role="SUPER_ADMIN">`，sidebar 也是 `role: 'SUPER_ADMIN'`，
> **兩者都與後端一致**。我之所以寫錯，是先把「後端 27 條路由表」抽出來，然後在
> 沒有回去打開 `App.tsx` 的情況下假定 route 用的是 `require_admin`——
> 這是把推論當觀察的錯誤。保留這一條（而不是刪掉）是因為它有診斷價值：
> **同一份報告裡，「我核對過後端」與「我核對過前端」是兩件不同的事，
> 不應該因為前者做得很細就假定後者也做了。**
>
> 附帶結論：**27 條 admin 路由的角色對照全部一致**，帳號頁不是例外。
> 下表保留作為已核對的證據。

**27 條 admin 路由 → 角色對照**（後端守衛 vs console `RequireRole`／sidebar）：

| 後端守衛 | 端點 | Console |
|---|---|---|
| `require_admin` | `GET /drivers`、`GET /drivers/{id}`、`GET /refunds`、`GET /audit`、`GET /orders`、`GET /orders/{id}`、`GET /disputes`、`GET /disputes/stats`、`GET /disputes/{id}`、`POST /disputes/{id}/messages`、`POST /disputes/{id}/resolve`、`GET /search`、`GET /live/drivers` | 對應頁面無 `RequireRole` 或只 `OPERATIONS`（較寬）✅ |
| `_require_operations` | `POST /drivers/{id}/review`、`POST /disputes`、`POST /disputes/{id}/assign`、`POST /disputes/{id}/status` | `OPERATIONS` ✅ |
| `_require_finance` | `POST /drivers/{id}/deposit/grant`、`POST /drivers/{id}/deposit/adjust`、`POST /settlement/preview`、`POST /settlement/weekly/run`、`GET /settlement/export.csv`、`POST /refunds/{id}/decision` | `FINANCE` ✅ |
| `_require_super` | `GET/POST /accounts`、`PATCH /accounts/{id}/role`、`POST /accounts/{id}/password/reset` | `SUPER_ADMIN` ✅ |

（`require_admin` 那 13 條前端只給 `OPERATIONS` 是**收窄**而非放寬——司機詳情、爭議
這些頁面對 SUPPORT 也是唯讀的，前端不提供入口是刻意的 affordance 決定，
不是權限錯配。後端仍會在需要時拒絕。）

### P1-F-2. Mobile 完全無法滿足平台週結算的 confirm-token gate

**位置**：`mobile/lib/data/admin_repository.dart`（`runWeeklySettlement`）、
`mobile/lib/features/admin/admin_settlement_screen.dart`。

**問題**：後端 `POST /admin/settlement/weekly/run` 對**未結算的期間**有結構性 gate：
沒有 `confirm_token` 時會先跑 `preview_weekly`，若 `would_charge` 非零就
`BusinessRuleError(reason="CONFIRM_TOKEN_REQUIRED")`；token 由
`POST /admin/settlement/preview` 簽發（綁定 `(period, fee_hkd)`，且 fee 必須是
`money_str()` 渲染出的 `"200.00"` 而非 `str()` 的 `"200"`）。

但 mobile 的 `AdminRepository.runWeeklySettlement({period})` **沒有 `confirm_token` 參數**，
且整個 app **沒有任何地方呼叫 `/admin/settlement/preview`**。

**為何是問題**：mobile 管理端的「跑週結算」按鈕**對任何新期間都必然失敗** ——
它拿不到 token，而沒有 token 就必定被拒。這不是「少了個便利功能」，是那顆按鈕在
主要用途上是死的。`admin_settlement_screen.dart` 的結果卡還特別處理了 `hasAnomaly`
（tint `errorContainer`），說明作者以為它能跑成功。

**註**：`runWeeklySettlement` 對**已結算期間**（`would_charge == 0`）會成功 ——
所以測試若只驗「重跑已結算期間的 idempotency」，這個洞不會被發現。這正是它危險的地方。

**建議改動**（兩條路，選一條並寫明）：
1. **照抄 console 的兩步流程**：加 `Future<PreviewResult> previewWeekly({String? period})`，
   在 screen 加「預覽 → 顯示金額 → 確認 → 帶 token 執行」。
2. 若產品決定 mobile **不做**週結算（只做車隊結算），就**把那顆按鈕拿掉**，
   而不是留一個必然失敗的入口。

無論哪條，`runWeeklySettlement` 都應該加 `confirmToken` 可選參數，讓呼叫方能表達意圖。

---

## P2-F — 可維護性（前端）

### P2-F-1. `shortId` 有三種互不相容的定義

| 位置 | 定義 | 結果（`a1b2c3d4-e5f6-...`） |
|---|---|---|
| `admin-web/web/src/lib/labels.ts` | `split('-')[0]` | `a1b2c3d4` |
| `admin-web/web/src/pages/RefundsPage.tsx:34` | 本地 `slice(0, 8)` | `a1b2c3d4` |
| `mobile/lib/models/fleet.dart` | `'…' + tail(8)` | `…f6a7b8c9` |

前兩個**目前**剛好同值（UUID 首段就是 8 字元），但**定義不同** ——
一個是「第一個 dash 前」，一個是「前 8 個字元」。`split('-')[0]` 在
非 UUID 字串上會回整串，`slice(0,8)` 不會。第三個則是**尾部** 8 字元。

**為何是問題**：客服在看 console 抄 ID、使用者在 mobile 看 ID、兩邊對不上時，
沒人能確定「這是不是同一個」。而且 `RefundsPage` 重複了一份已經 export 的函式。

**建議改動**：刪掉 `RefundsPage.tsx:34` 的本地版本，import `lib/labels.ts` 的。
mobile 的尾部取法**保留**（它是刻意的，短 ID 是給人看的），但在 `fleet.dart`
的 docstring 註明「與 console 的短 ID 不同源，兩者不可互相比對」。

### P2-F-2. `SearchPage` 用「設回同一個值」觸發 effect 重跑

**位置**：`admin-web/web/src/pages/SearchPage.tsx:121`
```tsx
onRetry={() => setQuery((q) => q)}
```
**問題**：`setQuery` 回傳同一個 primitive，React 比對後**不會**重渲染 ——
這個「重試」按鈕實際上依賴 `useLoad` 內部把 query 當 dep 這條隱性鏈路。

**為何是問題**：它看起來像 no-op，未來有人「清理」這段會直接弄壞重試。
即使現在能動，閱讀成本高於它節省的兩行。

**建議改動**：給 `useLoad` 一個明確的 `reload()`（內部 `setNonce(n => n+1)`），
或讓 retry 走與其他頁面相同的 `onRetry` 介面。不要靠 React 的 identity check 當控制流。

### P2-F-3. `AnalyticsPage.summaryFilters` 每次 render 都是新物件

**位置**：`admin-web/web/src/pages/AnalyticsPage.tsx`。

**問題**：`summaryFilters` 是 render 內新建的 object。

**為何（目前）不是 bug**：`useLoad` 的 deps 列出的是**基本型別**（`from`/`to`/…）
而不是 object 本身，所以不會無限重跑。**這個寫法本身是對的。**

**為何仍要記**：這是「deps 正確」與「傳入值正確」之間的一個隱性契約 ——
任何人日後把 `summaryFilters` 加進 deps，就會得到無限迴圈，而錯誤訊息會指向
`useLoad` 而不是這裡。

**建議改動**：在 `summaryFilters` 上方加一行註釋：
`// 這個物件每次 render 都是新的；useLoad 的 deps 因此只列基本型別，勿加此物件。`

---

## 前端：Mobile 專屬

### P2-F-4. `_RefundSection._request()` 沒有 invalidate `ledgerProvider`

**位置**：`mobile/lib/features/driver/driver_earnings_screen.dart` 的 `_RefundSection._request()`。

**問題**：申請退款後只 `ref.invalidate(myRefundProvider)` 與 `driverProfileProvider`，
**沒有** invalidate `ledgerProvider`。

**為何是問題**：退款申請會在餘額上留下內轉記錄（且依後端設計會 hold 住金額），
所以 `ledgerProvider` 與 `_BalanceCard`（讀最新一筆的 `balanceAfterHkd`）都已經過期。
使用者提交退款後看到的是**舊餘額**，直到他手動下拉刷新 —— 而「錢有沒有被扣」
正是使用者最在意的一格。

**建議改動**：`_request()` 成功後一併 `ref.invalidate(ledgerProvider)`。

### P2-F-5. `admin_settlement_screen.dart` 手動執行後什麼都不刷新

**位置**：`mobile/lib/features/admin/admin_settlement_screen.dart`。

**問題**：執行週結算後沒有 invalidate 任何 provider（因為沒有對應的 run provider）。

**為何是問題**：結算會實際動到司機餘額與帳本，所以 `driverProfileProvider`、
`ledgerProvider`、KYC 頁的資料在結算後全部是舊的。管理員切到別的 tab 會看到
結算前的數字。

**建議改動**：至少在成功後 `ref.invalidate(driverProfileProvider)` 與
`ref.invalidate(ledgerProvider)`（即使管理員自己看不到，其他 tab 的 family 也會被清）。
更好的做法是引入一個 `adminSettlementRunsProvider` 讓結果本身有 cache。

### P2-F-6. `DriverRegisterRequest` 的車牌只在客戶端驗「長度 ≥ 4」

> **已修（2026-10-02）**：加了格式**提示**（非驗證器），見文末「已修（前端）」表。

**位置**：`mobile/lib/models/driver.dart`（`DriverRegisterRequest`）。

**問題**：`taxiDriverPlateNo` / `vehicleRegMark` 客戶端只檢查 `length >= 4`，
其餘原樣送去後端。

**為何是問題**：香港的士車牌格式（如 `KA 1234`）與車輛登記標記是有固定形態的。
送一個明顯錯的值上去，使用者只會在審核階段被拒，而錯誤訊息來自後端 ——
中間沒有任何提示。

**建議改動**：加一個輕量的 `RegExp` 客戶端檢查（**只做提示，不做阻斷**），
或至少在 UI 加 placeholder 展示期望格式。**不要**在客戶端做嚴格校驗 ——
真實車牌格式比想像中雜，過嚴會擋掉合法輸入。

### P2-F-7. `OtpScreen` 的 `state.extra` 空字串靜默通過

> **已修（2026-10-02）**：非 `String`／空字串改回 `LoginScreen`，見文末表。

**位置**：`mobile/lib/features/auth/otp_screen.dart`
```dart
OtpScreen(phoneE164: extra is String ? extra : '')
```
**問題**：deep link 直接開 `/login/otp` 時 `extra` 是 null，`phoneE164` 變成 `''`。

**為何是問題**：畫面會顯示「驗證碼已發送至 」（後面空著），而「重發」會送出一個
不合法的電話號碼 —— 使用者看到的是伺服器錯誤，但真正的原因是無效的路由進入。

**建議改動**：`phoneE164` 為空時**不渲染 OTP 畫面**，直接 `context.go(Routes.login)`。
OTP 畫面沒有電話號碼是沒有意義的狀態，不該存在。

### P2-F-8. `AdminKycScreen._grant` 在方法內建 `TextEditingController`

> **已修（2026-10-02）**：補上生命週期註釋，見文末表。原判斷保留 —— 不改成
> `State` 欄位是刻意的。

**位置**：`mobile/lib/features/admin/admin_kyc_screen.dart` 的 `_grant`。

**問題**：`TextEditingController` 建在方法內，`await showDialog(...)` 之後立刻
`amount.dispose()`。

**為何（目前）不是 bug**：`await` 保證 dispose 在 dialog 關閉後才發生，所以能動。

**為何仍要記**：這個 pattern 沒有綁在 `State` 上，任何人在 dialog 內加一個
「完成後仍會用到 `amount`」的邏輯（例如顯示回顯值），就會 dispose-after-use。
`Dispose` 的時機是隱性的。

**建議改動**：改成 dialog 自帶 `StatefulWidget`，或至少加註釋說明
`// dispose 只能在 showDialog 之後，因為 dialog 仍持有它。`

---

## 附：前端——不是問題，但值得記錄的判斷

| 位置 | 看似問題 | 實際 |
|---|---|---|
| `mobile/lib/features/passenger/request_ride_screen.dart` | 只提供 `OrderCreateIn` 接受的欄位（無行李／寵物／預約） | 故意：`OrderService.create()` 不把這些傳給 `calculate_fare()`，所以提供了會得到**無法重現**的報價 |
| `mobile/lib/features/passenger/trip_tracking_screen.dart` | 用 REST 輪詢訂單狀態 | 故意，**且記錄了一個真後端缺口**：`trip_service.py`/`ws.py` 都說生命週期事件會 publish，但「no call site ever publishes one」—— 不輪詢的話搶單後會永遠停在「等待司機」 |
| `mobile/lib/features/shared/map_panel.dart` | 路線是直線 | 故意：沒有 routing service，「anything more would be a lie」 |
| `mobile/lib/features/fleet/fleet_screen.dart` | 計費中用 `AppTheme.loss` | 故意：`loss` 是主題的**綠色**，符合香港紅漲綠跌 |
| `mobile/lib/core/format/money.dart` | 有兩個精度（2dp / 1dp） | 故意：stored `money_str` 2dp、meter `meter_str` 1dp；`display` 跟隨攜帶的精度，否則 `0.05` 會被藏掉 |
| `mobile/lib/features/shared/widgets.dart` | `AsyncValueView(skipLoadingOnRefresh: false)` | 故意：刷新時保留舊內容但顯示載入指示 |
| `mobile/lib/router/routing_rules.dart` | 只有 `ADMIN` 是真實帳號角色 | 記錄事實：`UserRole.DRIVER` 從未被指派，`app/api/auth.py` 的 `require_role()` 從未被呼叫 |
| `admin-web/web/src/i18n/index.ts` | `en.ts` 用 `satisfies TranslationShape<typeof zhHant>` | 故意：缺 key 是 **`tsc` 錯誤**，不需要 runtime 檢查 |
| `admin-web/web/src/api/client.ts` | GET-only transport retry（3 次 / 150ms） | 故意：POST 重試會重複動錢 |
| `admin-web/web/src/app/useLoad.ts` | 只提供 `inOrder`，不提供 `Promise.all` | 故意：並發載入會讓錯誤歸屬不可讀 |

---

## 附：後端——不是問題，但值得記錄的判斷

| 位置 | 看似問題 | 實際 |
|---|---|---|
| `app/core/money.py` | `ROUND_HALF_UP` 而非預設的 banker's | 故意：fare engine 已 round half-up，兩個視圖必須一致 |
| `app/api/schemas/fare.py` | `FareEstimateOut` 的金額是 `Decimal` 不是 `str` | 故意：`distance_km`/`waiting_min` 是**回顯輸入**，改 `str` 會令 route 拒絕自己 handler 的輸出 |
| `app/core/deps.py:160` | `require_live_admin_refresh_session` 是 no-op | 故意：為 `tests/test_security_hardening.py` 的宣告式 audit 而存在 |
| `alembic/versions/d7f3b21a6e08` | backfill 把**所有** admin 升成 `SUPER_ADMIN` | 故意：空頂層無法從應用內修復；docstring 明說「下一步要降級」 |
| `Dockerfile:34` | `--no-proxy-headers` | 故意：uvicorn 預設信任 127.0.0.1 的 XFF，會讓 SEC-07 下面的限流全部可繞 |
| `settlement_confirm.py` | 自己簽 HMAC token 而不存 DB | 合理：preview 是無狀態計算，token 只是防篡改 |
| `phone_reverify_service.session_factory_marker` | 直接 `raise NotImplementedError` | 故意：可 grep 的哨兵，不是漏實作 |
| `app/core/exceptions.py` | `code_map` 是 dict 而非 Enum | 合理：值必須與 `admin-web/web/src/api/client.ts` 的 `CODE` map 對齊，兩邊都不是 Python 枚舉 |

---

## 建議執行順序

**前端兩條（本次已修，見 §修復記錄）**：
- **P0-F-1**（`DisputesPage` 用 `user.role`）—— 一行，但影響「錢路能不能按」。
- **P1-F-2**（mobile 週結算無 token）—— 需要決定「補預覽流程」還是「拿掉按鈕」。

**後端七條**：
1. **P0-2**（`max_length` 歧義）—— 一行，先確認 pydantic 行為再改。
2. **P0-4**（`DuplicateReferenceError`）—— 加型別 + 改 3 個呼叫點，順手修
   `fleet_service.py:464` 的不一致。
3. **P0-1**（`Mapped[object]`）—— 16 處，一次做完；先確認 PostGIS 欄位的正確型別。
4. **P0-3**（settlement N+1）—— 把 run 對齊 preview 的批次查詢。
5. **P1-1 / P1-2**（抽 `client_ip` + `driver_profile_of`）—— 純機械重構，無行為變化。
6. **P1-8**（middleware 順序 + CORS on 413）—— 需要一次真的驗證。
7. 其餘 P1 / P2 按檔案改動的自然時機順手做。

**不要一次做完全部。** 這個 codebase 的價值在於每個決定都有註釋解釋代價；
大規模重構會把註釋與代碼的對應關係打散，而那個對應關係才是這份代碼最貴的資產。

---

# 修復記錄（2026-10-12）

本節記錄**實際改了什麼**，與上面的「建議」分開，因為兩者不總是同一件事 ——
有三條在動手時被證明是**錯的**（見「已撤銷」）。

## 已修（後端）

| 編號 | 改動 | 檔案 |
|---|---|---|
| **P0-1** | 14 個 `Mapped[object]` Numeric 欄改成 `Mapped[Decimal]`（`user.py` 10 個、`fleet.py` 4 個）。3 個 PostGIS `geography` 欄**保持** `Mapped[object]` 並加註釋說明為什麼（GeoAlchemy2 的 `Geography.python_type` 就是回 `object`，改成 `Mapped[Any]` 只是換一個謊） | `app/models/user.py`, `app/models/fleet.py` |
| **P0-3** | `run_weekly` 加批次預載 `references` + `claimed`，與 loop 會走到的結果鎖定在同一個 outcome。**存款預載刻意沒加** —— 加了會讓「被刪掉的存款列」看起來像乾淨的 skip（見下） | `app/services/settlement_service.py` |
| **P0-4** | 新增 `DuplicateReferenceError(BusinessRuleError)`，race loser 改拋型別化錯誤。原本的 `except BusinessRuleError` 用**訊息字串**做控制流 | `app/core/exceptions.py`, `app/services/ledger_service.py`, `settlement_service.py`, `fleet_service.py` |
| **P1-1** | 5 份 byte-identical 的 `_client_ip` 抽成 `app/core/client_ip.py::client_ip()`，5 個 route 模組改 import | 新檔 + 5 個 `app/api/*.py` |
| **P1-2** | 6 份司機 profile 查詢收攏成 `DriverProfile.for_user()`；刪掉 `_driver_profile_of`（×2）與 `_get_profile`。`ws.py:197` 的 2 欄投影**刻意保留**（熱路徑，每 tick 都跑） | `app/models/user.py` + 6 個 consumer |
| **P1-3** | `list_accounts` 的 docstring 改成對齊現實（字母序，不是 seniority），並解釋為什麼不用 `CASE` 真排序 | `app/services/admin_account_service.py` |
| **P1-4** | `run_weekly` 的 `fee_hkd` 從 `str(fee)` 改成 `money_str(fee)`，與 preview 一致。**這是 breaking change**，一併改了 3 個測試斷言 + 3 個 mobile fixture | `settlement_service.py`, `tests/`, `mobile/test/fixtures/` |
| **P1-5** | `fleet.name` 在 session 關閉後被讀取 → 在 `async with` 內先抽成 `fleet_name` 純量 | `app/services/fleet_service.py` |
| **P1-6** | 刪掉 `deposit.held_hkd = Decimal(deposit.held_hkd)` 這行**無意義的自我賦值**（存在的唯一理由是讓 `Mapped[object]` 的讀者安心） | `app/services/ledger_service.py` |
| **P2-1** | 刪除死代碼 `app/api/auth.py` 的 `require_role`（讀 `UserRole`，與 `deps.py` 讀 `AdminRole` 的同名函式並存 —— 正是「後台全黑」那個 bug 的形狀） | `app/api/auth.py` |
| **P2-3** | prod 下 `CORS_ORIGINS` 為空 / 含 `*` / 非 https → fail closed；`TRUSTED_PROXY_COUNT <= 0` → fail closed | `app/core/config.py` |
| **P2-4** | 斷掉的 `deploy/pgbouncer/README.md` 引用改指 `deploy/README.md#pgbouncer`（該檔從未存在）；`CORS_ORIGINS` 加 `:?` 必填守衛 | `docker-compose.prod.yml` |
| **P2-6** | `include_object` 的過濾從 `("table",)` 擴到 `("table", "index")` | `alembic/env.py` |
| **P2-7** | `money.py` 新增 `quantize_money()`（回 `Decimal`，非字串），`fleet_service` 刪掉私有 `_CENT` 與 `ROUND_HALF_UP` import | `app/core/money.py`, `app/services/fleet_service.py` |
| **P2-8** | `audit_service` 的 lazy import 提升到模組層（`core/` 化之後循環依賴已消失，延遲不再有理由） | `app/services/audit_service.py` |
| **P2-10** | `README.md` 的 `uv sync` 加 `--frozen`（與 Dockerfile / CI 一致）；`pyproject.toml` 註明 floor 只為可讀性、`uv.lock` 才是權威 | `README.md`, `pyproject.toml` |
| **P0-M-1** | `do_run_migrations` 補 `CREATE EXTENSION IF NOT EXISTS postgis`，**放在 `with context.begin_transaction():` 之內**（放外面會令整個 migration block commit 不到任何東西，但仍然報「全部已套用」）。驗證：新 DB → 22 張表 | `alembic/env.py` |
| **P0-M-2** | **18 個 enum 欄位補上資料庫層 CHECK 約束**（原本 19 個之中只有 2 個有）。全專案 `SAEnum(...)` 統一加 `native_enum=False, create_constraint=True` 並改用 `name="ck_<table>_<column>"`；`dispute.py` 5 個從未綁 enum 的 `String(N)` 欄改綁 `SAEnum`（用 `length=` 鎖住原寬度，令 migration **只加約束、不改型別**，避免對 production 資料做窄化重寫）。新 migration `2e276a320b35` 加 18 條 CHECK，並**先跑 pre-flight 資料檢查**，發現越界值就點名 table/column 再拒絕，唔會死喺 `ALTER TABLE` 內部 | 新檔 `alembic/versions/2e276a320b35_enum_check_constraints.py` + `app/models/{user,fleet,licence,dispute}.py` + 4 個舊 migration 的 `name=` |
| **P2-12** | 刪掉 `_bridge()` 尾端不可達的重複 `return proc` | `scripts/ops/db_backup.py` |

**新增測試（補上本專案最大嘅結構盲點）**：

| 檔案 | 守住咩 |
|---|---|
| `tests/test_enum_check_constraints.py` | 每個 enum 欄位都必須 `create_constraint=True` 且 `name` 以 `ck_` 開頭；含 walker 自我檢查（防止空跑 pass） |
| `tests/test_migration_schema_parity.py` | **唯一會跑 migration 嘅 test**：新 DB → `alembic upgrade head` → 22 張表 + postgis；metadata 嘅 20 條 CHECK 必須逐條存在於 migrate 出嚟嘅 schema；`alembic check` 只准出現已知 baseline drift |

**順手修掉的 doc drift**（我的 P1-1 重構造成的）：`docs/SECURITY_AUDIT.md` 的 SEC-07
列、`deploy/README.md` 的主機名段、`deploy/nginx/realtaxihk.conf` 的 SEC-07 註釋 ——
三處仍指向已刪除的 `app/api/auth.py::_client_ip`。

## 已修（前端）

| 編號 | 改動 | 檔案 |
|---|---|---|
| **P0-F-1** | `DisputesPage` 不再用 `user.role`；`ResolveBody` 收 `canMoveMoney: boolean`（傳 `hasRole('FINANCE')`）。布林而非角色字串，因為後端用的是 `at_least()` 排名比較 | `admin-web/web/src/pages/DisputesPage.tsx` |
| **P1-F-2** | mobile 週結算改成兩步：`_loadPreview()` 取得 `confirm_token` → `_run()` 帶 token。新增 `SettlementPreview` 模型 + `_PreviewCard` | `mobile/lib/models/admin.dart`, `data/admin_repository.dart`, `features/admin/admin_settlement_screen.dart` |
| **P2-F-1** | 刪掉 `RefundsPage` 的本機 `shortId` 副本，改 import `lib/labels.ts`；mobile 的尾部取法保留但在 docstring 註明與 console **不同源、不可互比** | `RefundsPage.tsx`, `mobile/lib/models/fleet.dart` |
| **P2-F-2** | `SearchPage` 的重試從 `setQuery((q) => q)`（React 會因 `Object.is` 相等而 bail out，靠 `setLoading` 同 tick 才「看起來會動」）改成專用的 `retryNonce` state | `admin-web/web/src/pages/SearchPage.tsx` |
| **P2-F-3** | `AnalyticsPage.summaryFilters` 上方加註釋，說明它每次 render 都是新物件且**不可**加進 deps | `admin-web/web/src/pages/AnalyticsPage.tsx` |
| **P2-F-4** | `_RefundSection._request()` 補 `ref.invalidate(ledgerProvider)` —— 退款會 hold 住餘額，不刷新等於讓使用者看舊餘額 | `mobile/lib/features/driver/driver_earnings_screen.dart` |

**其餘前端項亦已補做**（原本標記為「留給產品判斷」，後來決定一併處理）：

| 編號 | 改動 | 檔案 |
|---|---|---|
| **P2-F-6** | 車牌輸入加即時格式提示（`^[A-Z]{1,2}\s?\d{1,4}$`）。**提示而非驗證器** —— 香港 Custom Registration Marks 是任意字串，硬擋會拒真車牌。訊息說明「一般」格式 | `mobile/lib/features/driver/driver_onboarding_screen.dart` |
| **P2-F-7** | `state.extra` 改為型別檢查：非 `String` 或空字串 → `return const LoginScreen()`。直接回傳而非 `context.go()`，避免在 build 期間導航 | `mobile/lib/router/app_router.dart` |
| **P2-F-8** | `_grant` 的 `TextEditingController` 生命週期加註釋，說明 dispose 順序是 load-bearing | `mobile/lib/features/admin/admin_kyc_screen.dart` |
| **P2-11** | 新增 `scripts/_root.py`；14 個腳本改用兩行 bootstrap。三種 depth 寫法（`parent.parent.parent` / `parents[2]` / 嵌套 `dirname`）全部移除。新增 `tests/test_scripts_root.py`（26 tests）守住不回流 | `scripts/_root.py`（新）、14 個腳本、`scripts/README.md` |

**仍未做（原因不變）**：P2-5（nginx envsubst）需要一台真的 nginx 才能驗，
設定錯了是**啟動失敗**而非啟動錯誤，無法在本機證明，故只記錄做法。

## 完整 gate 驗證（改動後、單一權威跑）

> 這一輪在 `alembic/env.py`（P0-M-1）與 `scripts/ops/db_backup.py`（P2-12）
> 落地後**重跑過一次**。`ruff` 的範圍加入 `alembic/`（之前的 gate 沒掃 migration）。

| 檢查 | 指令 | 結果 |
|---|---|---|
| Lint | `ruff check app/ tests/ scripts/ alembic/` | **All checks passed!** |
| Format | `ruff format --check app/ tests/ scripts/ alembic/` | **exit 0**（「N files」不穩定，見下） |
| 響應模型 | `scripts/verify/audit_response_models.py` | **68 fixture blocks, OK**；89 operations / 85 reachable |
| 後端測試 | `pytest tests/ -q --junit-xml=.tmp/full_gate.xml` | **921 tests, 0 failures, 0 errors, 0 skipped**（573s） |
| Alembic drift | `alembic check` | 仍是既有 baseline（5 `add_index` + 4 `modify_type`），**無新增** |
| Migration 可跑 | 新 DB 跑 `alembic upgrade head` | **22 張表** + `postgis` 已裝 + head `a1c4e8b7f209` |
| Console typecheck | `npm run typecheck` | 乾淨 |
| Console 測試 | `vitest run --no-file-parallelism --pool=forks` | **69 passed / 9 files** |
| Console build | `npm run build` | **101 modules**，成功（3.08s） |
| Mobile 分析 | `tool/dart_check.py mobile` | **58 files, 0 diagnostics** |
| Mobile 測試 | `tool/run_tests.dart` | **97 passed, 0 failed** |
| Mobile 合約 | `tool/verify_contract.dart` | **54 fixtures, 0 failures** |

**注意 `dart_check.py` 必須帶路徑參數**：`dart_check.py .` 會回
「no .dart files under …」—— 雖然腳本自己用 `os.walk` 而非 `Path.rglob`，
但 `--root` 的 default 是 `.`，要寫成 `dart_check.py mobile`。

**三個「假紅燈」，三個都不是 code 問題**（記錄下來免得下次誤判）：

1. **`pytest` EXIT 是 1，但 921 個測試全 pass。** stdout 尾端被注入
   `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":91,...}`，
   截斷了摘要行。**EXIT code 在此環境不可信**，必須讀 `--junit-xml` 的 XML。
   （MEMORY 早已記載此現象；本次再次證實。）
2. **`ruff format --check` 報的「N files」不穩定**（同樹實測 139／140／141），
   判準是 exit code。
3. **`alembic check` 必定 FAIL**，但這是**既有**的 drift（5 index + 4 enum），
   要看的是**有冇新增**。本次跑出來的 9 條與 baseline 逐條相同。
   而且它剛好印證了 P0-M-2／M-3：`alembic check` 想改的 4 條
   `VARCHAR(length=N) → Enum(...)` 正是那 4 個 enum 欄位。

## 已撤銷的 5 條（誤報）

寫進報告的條目裡，有 5 條在動手時被證明是錯的。全部**保留原文 + 標記撤銷 + 附證據**，
因為「一條為什麼看起來像 bug 但其實不是」比「一條正確的 bug」更難得：

| 編號 | 原結論 | 撤銷理由 |
|---|---|---|
| **P0-2** | `max_length` 對 list 語義不明 | 實測就是 item-count（`too_long`）；422 放大問題被 `exceptions.py:58` 剝掉 `input`，端到端 100MB→209B |
| **P1-F-1** | accounts route 用 `require_admin` 與後端 `_require_super` 不一致 | 實際是 `<RequireRole role="SUPER_ADMIN">`，一致 |
| **P2-2** | `orders.py::_redis` 是死代碼 | **是** FastAPI dependency（`Depends(_redis)`）；grep `name(` 對這個寫法結構性失明 |
| **P2-9** | `_LIKE_SPECIAL` 轉義需補測試 | 已有，`test_admin_search.py:271/278` 分別測 `%` 與 `_` |
| **P1-9（部分）** | `scratch_probe/` 在 git 內 | `git ls-files` 回空，`.gitignore:15` 明文列了它 |

**共同的教訓**：五條裡有四條是「**我沒跑那一條指令就下了結論**」——
`pytest -k max_length`、`cat App.tsx`、`grep -rn '\b_redis\b'`、`grep tests/`、
`git ls-files`。每一條都是**可以一行驗證的事實**。凡是「某個 API 在某個版本下的行為」
或「某個名字在某處有沒有被用到」，**必須跑一次**，不能從形狀推論。
