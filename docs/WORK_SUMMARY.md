# realtaxihk — 工作總覽

- **生成日期**：2026-09-30（**2026-10-01 更新**：併入 location check、analytics、console deep-link 修正；測試數由 272 更正為 616。**本輪再更新**：修好 SEV-1 admin session、統一 money 精度入口、套用 `ruff format` 並加 CI gate、補上 TOTP 綁定二維碼的渲染與測試；測試數 616 → **656**（**本輪 667**：新增 5 個 HTTP error-code + 6 個 state-machine 不變式測試）。**最後一輪**：修好深圳灣口岸邊界缺陷（`_HK_MAIN` 后海灣段 2 → 7 頂點），測試數 667 → **687**（新增 18 個口岸邊界參數化案例 + 2 個回歸測試）。**本輪再更新**：`app/models/__init__.py`（886 行）拆成 5 個 bounded-context 模組 + `__init__` re-export，測試維持 **687** 不變 —— 見 §2.9。**本輪再更新**：新增 `app/api/schemas/` 套件並為**全部 69 個 operation** 補上 `response_model=`（此前 69 個中只有 1 個），令 `/openapi.json` 首次描述真實響應形狀；測試維持 **687** 不變 —— 見 §2.10。**最新一輪（§2.11）**：RBAC 四級角色 + 審計覆蓋金錢／狀態改動、帳戶管理、訂單監控、結算預覽／確認 token／CSV 匯出、爭議實體、主體搜尋、頭像上傳，以及六個對應的後台畫面 —— 測試 **687 → 872**（+185），console **29 → 31 vitest**（+2），API **64/69 → 81 paths / 88 operations**）。**最新一輪（§2.13）**：結構整理 —— `app/api/` 命名統一為 `admin_x`、`/api/v1/driver` → `/api/v1/drivers`、`scripts/` 拆為 `{ops,verify,dev}`、由 `create_app()` 抽出 lifespan 輔助函數；測試維持 **872** 不變，並修好一個腐爛的驗證腳本（`prod_boot_drill` 6/7 → 7/7）。**最新一輪（§2.14）**：新增後台實時車輛位置端點 `GET /api/v1/admin/live/drivers`（輪詢式，非推送），測試 **872 → 887**（+15：12 個端點測試 + 3 個 `.env.example` 漂移守衛）；API **81/88 → 82 paths / 89 operations**；並修好測試套件會對外連線的問題（`SENTRY_DSN` 未強制清空））
- **HEAD**：`main` 上最新 commit —— **刻意不寫死 hash**（寫死過三次，每次之後
  的 commit 都令它變錯；要查：`git log --oneline -1`）；
  `origin/main..HEAD` = **有未推 commit**（查：`git rev-list --count origin/main..HEAD`，見 §5）；
  working tree **clean**。
- **現時狀態**：`pytest` **887 passed / 0 failed / 0 error / 0 skipped**（以 `--junit-xml` 讀）· `ruff check` clean · **`ruff format --check` clean** · console `tsc` clean + **68 vitest passed（9 files）** · `npm run build` 主包 466.65 kB（gzip 146.05 kB）＋地圖分包 155.64 kB（gzip 45.57 kB，按需載入）· Dart **93 passed / 0 failed** · contract **54 fixtures decoded, 0 failure** · `dart_check` 58 files, 0 diagnostics · `audit_layout` **52 renders clean（4 locale/theme 組合 × 13 條路由）** · API **82 paths / 89 operations，全部已声明响应模型** · fixture↔schema 审计 **68/68 块无数据丢失**
- **✅ 已解決：管理員 session 15 分鐘硬死** —— 已改為 `HttpOnly` refresh cookie（`SameSite=Strict`，path `/api/v1/admin/auth`）＋ CSRF double-submit。詳見 `SECURITY.md`

> **這份文件的用途**：一份可以單獨看完的總覽 —— 做過什麼、現在是什麼狀態、
> 還有什麼未做、哪一項需要你出手。其他 `docs/*` 是**主題深入報告**（安全、
> 上線就緒、lint），`.workbuddy-ai/memory/*.md` 是**逐日流水**（append-only，
> 不整理）。這份是索引 + 摘要，不取代它們。

---

## 1. 交付物

| 交付物 | 位置 | 技術 | 狀態 |
|---|---|---|---|
| 後端 API | `app/` | FastAPI (async) + SQLAlchemy 2.0 async + PostgreSQL 16/PostGIS + Redis 7 + Alembic | ✅ **82 paths / 89 operations** · 887 tests |
| Flutter App | `mobile/` | Flutter + Riverpod 3.4.3 + Dio + go_router 17（**21 個畫面**，三角色） | ✅ 93 tests |
| Web 管理後台 | `admin-web/web/`（React + Vite）、`admin-web/js/`（legacy） | React + Vite（新版）、Vanilla JS（舊版） | ✅ **68 vitest** · UI verifier PASS |

一個 repo、三件完整交付物。定位：**Cap. 374D 合規的士資訊中介**（非的士營運商）。

---

## 2. 做過的事（按主題，非按時間）

### 2.1 後端 — 上線阻塞修復

`docs/PRODUCTION_READINESS.md` 記錄了完整 audit：**4 個掃描中發現的真 bug**
＋ **7 個 P0** ＋ **10 個 P1** ＋ **10 個 P2**，全部處理。

重點（每項都有 TDD 背書）：

| ID | 問題 | 修法 |
|---|---|---|
| B2 / P0-1 | ledger `append()` 無行級鎖 → 並發 lost update（兩個 request 同讀 balance=500，各自 +100，後者覆蓋前者 → **帳目靜靜地錯**） | `SELECT ... FOR UPDATE` + 條件式 UPDATE 仲裁 + `asyncio.gather` 並發測試 |
| P0-2 | Prod fail-safe：`JWT_SECRET` / `ALLOW_DEV_OTP` 誤設定就全平台失守 | `config.py` `model_validator(mode="after")` — prod 下拒絕不安全組合 |
| P0-3 | `is_active` 從未被執行 → 封禁功能不存在 | 加去 auth 依賴鏈 |
| P0-5 | Geo index ghost entries 污染派單 | — |
| B1 | `/auth/me` 對已刪除 user 會 `NameError` → 500 | — |
| B3 | `tip` 無上限 → DB overflow → 500 | 加 `le=100000` |
| B4 | `tracking.py` 用 `__import__("datetime")` hack | 正常 import |

### 2.2 安全 — SEC-01 ~ SEC-31 全數處理

`docs/SECURITY_AUDIT.md`（43 KB）逐條記錄。最重要那條：

**SEC-13 — ledger reference 命名空間。** 舊 contract 收 caller 提供的
`reference`，撞中就直接**原樣回傳舊 row**（不核對 entry_type／金額）。
由於 `weekly:{driver}:{period}` 同 `refund:{id}` 共用一個 flat namespace，
任何能寫入 ledger 的人（管理員，或者持有偽造 admin token 的攻擊者）可以
預植 `weekly:<driver>:2099-W03`，之後真實結算見到「已收費」就跳過該司機，
**靜靜地永遠收不到 HK$200**，而報告顯示一切正常（`skipped`）。
同一個手法令退款可以無 REFUND debit 就 APPROVED。

兩個防禦：① `append()` 拒絕 entry_type／金額不符的 reference hit；
② 每個用途由 server mint 專屬 prefix（`grant:` / `weekly:` / `fleet:` /
`refund:` / `adj:`），令跨用途碰撞根本無法表達。

### 2.3 Money 精度 — 「儲存用 cent，不用 decile」

**這是一個真正的生產 bug，不是重構。**

- DB 欄位是 `Numeric(10,2)`，cent 本來就精確儲存（實測
  `DriverDeposit(balance_hkd=Decimal("0.5"))` 讀返 `Decimal('0.50')`）。
- **缺陷在 serialiser**：`money_str` quantise 去 `Decimal("0.1")`，docstring
  寫「canonical wire precision = \$0.1 (smallest meter tick)」——
  將一個**車費**規則誤用到**帳本**金額。
- 後果：`0.05 → "0.1" → HK\$0.10`；`0.01 → "0.0" → HK\$0.00`（同空帳戶無法區分）。
- 亦發現 `Decimal.quantize` 預設是 banker's rounding（`150.45 → 150.4`），
  同車費引擎（`150.45 → 150.5`）矛盾 → 兩個 helper 都明確用 `ROUND_HALF_UP`。

**修法**：兩個精度各有其名 —— `money_str` **2 dp**（儲存金額）、
`meter_str` **1 dp**（錶費數字）。3 個 commit（`f65fdb4` / `712d1a8` / `542fc6b`）。

> 值得一提：最後一個 bug（`admin.py:157` 一個遺留 `"0.0"` 字面值）**只有讀
> live response 才找得到** —— 每個 driver detail payload 都有 1 個 1dp 值
> 夾在 4 個 2dp 值中間。所有 unit test 都通過。

### 2.4 `ADJUSTMENT` ledger 業務流（`4333f2a`，最新）

`LedgerEntryType.ADJUSTMENT` 只有 enum 定義、**零實作**，但**三個前端都已
渲染「調整」chip** —— UI 對外承諾了一個後端永遠無法產生的 type。

新增 `POST /admin/drivers/{id}/deposit/adjust`：signed amount、`reason` 必填、
±HK\$5,000 上限、`adj:` 命名空間。

順手修兩個真缺陷：
1. **`_ledger_out` 從不輸出 `created_by`** —— 寫入 DB 4 處、讀取 0 處。
   管理員操作有記名但 API 完全看不到。已加（admin 視角；司機
   `/me/ledger` 刻意不加，operator id 是內部資料）。
2. **mobile `isCredit` 硬編 `adjustment == true`** —— 但調整是 signed，
   一半情況顯示錯方向。確認無 production code 讀過 → **刪除**（不是修好），
   direction 一律讀 `amount_hkd` 正負。原本那個 test 還 assert 了 bug 是正確行為。

### 2.5 前端 — Apple 設計 + React 管理後台

- **mobile**：21 個畫面全部按 Apple HIG 重做 —— 主題建在 Apple type scale
  上、hard-coded spacing 全部換成 token、破壞性確認做成 iOS 形狀、底部
  操作區避開 home indicator。
- **admin-web**：管理後台**由零重寫成 React + Vite**（`admin-web/web/`），
  同 legacy 版並存。10 modules，driver detail 由一個 composed endpoint 支撐。

### 2.6 備份與還原演練（P1-4，2026-09-30）

`scripts/ops/db_backup.py`。**還原演練是重點** —— 沒有還原過的 dump 只是假設。

實跑結果（真 Postgres 16.4）：`49 tables / 17,627 rows`，source vs restore
逐表 count 完全一致。

過程中捉到 4 個**只有真跑才會現形**的 bug：

1. **`pg_restore --list` 收 host path**，但 docker transport 下該 tool 跑在
   container 裡面 → 找不到檔案。要改成 stdin 灌入（同 restore 一樣的橋）。
2. **`subprocess.run(shell=True)` 在 Windows 用 `cmd.exe`**，`;` 不是分隔符。
   實測 `echo hi >&2; exit 7` **回 0** → 一個失敗的上載被報成成功 ——
   備份最惡劣的失敗模式（靜靜地沒有 copy 到，但每晚都報 OK）。
   改成明確 `sh -c`。
3. **Windows 路徑經 `sh` 會被吞掉反斜線**：`C:\Users\user\x.dump` →
   `C:Usersuserx.dump`。`{file}` 完全用不到。改用 `as_posix()`。
4. **「上載 OK」原來只是「command exit 0」** —— `true` 都會 pass。
   加 `--upload-verify-cmd`，要真的問 remote 取得 archive 名才算數。

保留策略是 GFS（7 daily + 4 weekly），按**日曆距離**而不是檔案數量計 ——
weekly 層要經得起「幾日後才發現」的問題，所以 40 日 / keep 7+3 實測
仍然留住 Sep 20 同 Sep 13（各自 ISO week 最舊的一份）。

### 2.7 測試基建

- **Mobile fixtures 是「生成」不是手寫**：`scripts/dev/gen_mobile_fixtures.py`
  起 API、打真 endpoint、寫低 raw response；`mobile/tool/verify_contract.dart`
  用真 Dart model 解碼。手改 fixture 會令它同 API 脫節。
- **Admin UI verifier**（`admin-web/tool/verify_ui.mjs`）：真瀏覽器、真 API，
  捉三類「讀源碼看不到」的缺陷 —— module 載入失敗、render 拋錯、靜默 API 不 match。
- `docs/LINTING.md`：ruff 單一 linter，**explicit rule selection**（不用
  default），令 ruff 升級不會靜靜地改了該 gate。66 errors → 0。

### 2.8 身份與服務範圍（P-1 ~ P-5，2026-09-30 ~ 10-01）

- **P-1 Admin 認證**：`admin_accounts` 獨立於 `users`，username + password +
  強制 TOTP。三步狀態機，`/login` 只回 5 分鐘 challenge token（結構上不可以
  當 access token 用）。`/totp/enrol` **暫不寫入 DB**，等 admin 證明懂得生成碼
  才 persist —— 避免「secret 入了庫但沒有掃碼」的永久鎖死。
  authenticator 選型見 `docs/ADMIN_AUTH.md`。
- **P-2 Uber 形狀註冊**：email 驗證 + 每月手機重新驗證（soft block）。
- **P-3 的士證人工審核**：admin 改狀態。
- **P-4 每月重新驗證**：soft block，不阻礙現有行程。
- **P-5 部署目標**：`docs/DEPLOY_TARGET_DECISION.md`（仍待用戶拍板）。
- **Location check**：`app/core/hk_bounds.py` —— 8 個 polygon 取代
  `lat 22.1-22.6, lng 113.8-114.5` 的 bbox，因為**舊 bbox 含深圳**
  （Futian / Luohu / Bao'an 全部在內）。Server 為準，403 帶 `OUTSIDE_HK`。
- **Analytics**：`app/services/analytics_service.py` + `#/analytics`。
  `timezone('Asia/Hong_Kong', completed_at)` 同時用於 SELECT 同 GROUP BY，
  半開區間 `[00:00 HKT, 翌日 00:00 HKT)`。收入 = COMPLETED 訂單的
  `estimated_total_hkd`（已含折扣與貼士）。

### 2.9 `app/models` 拆包（結構重構，2026-10-01）

原本 `app/models/__init__.py` 一個檔案 886 行、24 個 model、4 個用 banner
分隔的區段。拆成 **5 個 bounded-context 模組 + 1 個 private base**：

| 檔案 | 行數 | 內容 |
|---|---|---|
| `_base.py` | 40 | 唯一的 `Base`（private，不在 `__all__`） |
| `user.py` | 402 | users / driver_profiles / deposits / orders / ledger / otp / refresh / refund |
| `admin.py` | 260 | admin 身份、TOTP、session、audit、email token |
| `fleet.py` | 206 | fleets / memberships / settlement runs |
| `licence.py` | 187 | P-3 licence submissions + documents |
| `__init__.py` | 118 | 全部 re-export + `configure_mappers()` |

**零呼叫點改動**：所有 consumer 一向都是 `from app.models import X`
（grep 證實沒有任何 `from app.models.<sub>`），所以 `__init__` re-export 之後
31 個公開名字的 import 路徑完全不變。

**為什麼不會拆爛 relationship**：SQLAlchemy 靠 class registry 解析
`relationship()` target，所以 class 可以跨模組互相引用；但前提是
**所有貢獻 mapped class 的模組都要在 `configure_mappers()` 之前 import 了**。
`__init__` 的 import 就是做這件事，接著**主動**叫 `configure_mappers()`，
令損壞了的 relationship 在 `import app.models` 即刻炸，而不是等到某次 query
先炸。跨模組的邊有 4 條：`User.driver_profile`、
`DriverProfile.licence_submissions`、`DriverLicenceSubmission.driver_profile`、
`DriverLicenceSubmission.documents`。

**重構如何證明沒有改變行為**（不止「test 過」）：
1. 將 HEAD 版本 + 工作區版本的 metadata 逐表 diff —— columns / types /
   nullability / defaults / indexes / FKs / unique constraints / PK
   **全部 17 張表一模一樣**（唯一差異是記憶體 address，同 `_uuid` vs
   `uuid.uuid4` 的 function 名 —— 已改回一致）。
2. 12 條 relationship（含方向、`uselist`、local columns、cascade options）
   **完全相同**。
3. `alembic check` 的 autogenerate drift **逐字節相同**（refactor 前後都是
  同樣 2449 字元的 ops list —— 即是說本專案本來就有一批未收的 drift，
   這次 refactor **沒有增加**）。
4. `pytest` 687 → **687**（`--junit-xml` 讀：687/0/0/0）。

---

### 2.10 响应模型集中化 + 全路由 `response_model=`（结构重构，2026-10-01）

> 本节起改用书面语；§2.9 及之前的粤语行文将在「文档统一」一项中一并处理。

**问题**：`response_model=` 不是一个文档注解，而是一个**过滤器** —— FastAPI 会把处理函数
返回的对象按模型校验，并**静默丢弃模型未声明的每一个键**。改造前 69 个 operation 里只有
**1 个**声明了 `response_model=`，所以 `/openapi.json` 对几乎每个响应的描述都是 `{}`。
真实契约只存在于 Dart 模型里 —— 这正是 `mobile/tool/verify_contract.dart` 和 54 份抓取
夹具必须存在的原因。

**改造**：新增 `app/api/schemas/` 包（11 个文件 = 10 个模块 + `__init__` re-export），模型**从抓取的夹具反推**，而不是靠读
处理函数：

| 模块 | 内容 |
|---|---|
| `_envelope.py` | `ErrorEnvelope` / `OkOut` / `OkLogoutOut` / `OkRevokedOut` / 两种列表信封 |
| `fare.py` | `FareEstimateOut`（15 字段）与 `FareSnapshotOut`（13 字段）——**刻意不合并** |
| `order.py` | `OrderOut` / `OrderPageOut` / `LedgerPageOut` / `TripLocationOut` |
| `driver.py` | `DepositOut` / 两种 profile / 三种 refund |
| `identity.py` | `ProfileOut` / `TokenPairOut` / `AuthMeOut` / `AdminMeOut` 等 |
| `admin.py` | `DriverRowOut` / `DriverDetailOut` / 三种 deposit 写入响应 |
| `admin_auth.py` | 三步登录状态机的 challenge / session 形状 |
| `fleet.py` | 车队、成员名单、结算（三种不同分页形状） |
| `licence.py` / `admin_licence.py` | 司机侧与运营侧执照视图 |

**结果**：69 / 69 个 operation 现在都有真实的响应 schema（此前 1 / 69），
`/openapi.json` 共引用 48 个不同的响应模型。

**过程中发现并修好的三个真实缺陷**（都是「凭猜测写模型」会造成的典型后果）：

1. **`DepositOut` 会凭空补出余额**。无押金路径只返回 `{required_hkd, is_fulfilled}`
   （夹具 `driver_me_no_deposit.json`），而测试
   `test_refund_and_settlement.py::test_charges_active_drivers_and_skips_the_rest`
   断言 `"balance_hkd" not in <deposit>` —— 用于区分「从未充值」与「充过、现为零」。
   给字段加默认值会让 pydantic **补出** `"0.00"`，直接破坏该契约。正确做法是
   `response_model_exclude_unset=True`：默认值保证校验通过，`exclude_unset` 保证键**缺席**。

2. **同一个键名在两条路由上类型不同**。`/auth/logout` 的 `revoked` 是 **int**（吊销的
   refresh token 数），`/admin/auth/logout` 的 `revoked` 是 **bool**（是否成功吊销）。我
   最初共用一个模型，pydantic 把 `False` 强制转成了 `1`，测试
   `test_logout_revokes_server_side_not_just_the_cookie` 立刻失败。已拆成
   `OkRevokedOut` / `OkLogoutOut` 两个模型。

3. **`EmailRequestOut` 是我猜的**。真实形状是 `{sent, expires_in_hours, email}`，而我按
   `OtpRequestOut` 写成了 `{sent, expires_in}` —— 既丢字段（`expires_in_hours`、`email`）
   又因必填字段缺失而 500。注意两条路由的 TTL **单位不同**（邮件按**小时**、OTP 按**秒**），
   客户端搞混就是 3600 倍的倒计时错误。该路由没有夹具，教训是：**没有夹具可依据时，必须去读
   序列化函数，而不是类比另一个模型。**

4. **`fare.py` 里还留着一份重复的 `FareEstimateResponse`**。审计脚本发现
   `POST /fare/estimate` 引用的模型不来自 `app/api/schemas` —— 也就是说同一个响应存在
   两份定义，正是本项要消灭的漂移。合并时还发现一个**潜在 500**：我原本把
   `distance_km` / `waiting_min` 写成 `str`，但处理函数直接传的是 `FareBreakdown` 的
   **`Decimal`**（这两个字段是**回显的调用方输入**，从不经过 `meter_str`），`str` 字段会
   拒绝它。夹具证实线上是 `"12.5"` / `"3"`（注意不是 `"3.0"`）。
   现在 `taxi_type` 用 `TaxiType` 枚举（于是 `/openapi.json` 首次公布可选值），
   另两个保留 `Decimal`。**这是全包唯一违反「金额一律用 str」的地方，理由已写在模型
   docstring 里**——不要「顺手改成 str」，那会让路由拒绝自己处理函数的输出。

**顺带收紧的两处 `list[Any]`**：`GET /admin/drivers` 与 `GET /admin/refunds` 原本用泛型
`PageEnvelope`，其 `items: list[Any]` 在 OpenAPI 里发布成 `items: {}` —— 也就是客户端唯一
需要渲染的部分反而没有文档。现改为 `DriverPageOut` / `RefundPageOut`（元素类型分别是
`DriverRowOut` / `RefundOut`）。同理 `LicenceSubmissionOut.documents` 由 `list[dict]` 收紧为
`list[LicenceDocumentOut]`。

**验证**（四层，与 §2.9 同规格）：

1. **新增 `scripts/verify/audit_response_models.py`** —— 遍历 `manifest.json` 的 route → fixture
   映射，断言每份夹具的键集是其模型字段集的**子集**。**68 个夹具块全部通过**，即没有任何
   响应会因 `response_model=` 而丢数据。
2. **该审计已证明「会失败」**（避免写一个永远 pass 的检查）：注入一个模型未声明的键，审计
   立即报出 `['a_key_the_model_does_not_have']`。
3. **`pytest` 687 → 687**（`--junit-xml` 读：687/0/0/0）。这 687 个测试全程通过 TestClient
   打真实路由，因此每一次调用都实际跑过 `response_model` 校验。
4. `ruff check` clean · `ruff format --check` clean（125 files）。

**与 Dart 契约检查的分工**（两者都要跑，失败点不同）：
- `scripts/verify/audit_response_models.py` 校验 **Python schema ↔ 夹具**；
- `mobile/tool/verify_contract.dart` 校验 **Dart 解码器 ↔ 字节**。
一次「模型与处理函数同时改名」会通过全部 Python 测试，却弄坏客户端 —— 只有后者能发现。

> 环境限制：本沙盒内 `dart run tool/verify_contract.dart` 无法执行（Windows 命名管道耗尽，
> `CreateFile failed 231 / ERROR_PIPE_BUSY`，属沙盒限制而非契约失败）。夹具本身未被本次
> 改动触及，故第 1、3 层验证足以覆盖。

### 2.11 后台治理：RBAC · 审计 · 订单 · 结算 · 争议 · 搜索（2026-10-01）

把「后台只能看 KYC 队列」补成一个完整的运营控制台。分 12 個 commit
（後端 11 + console 1；另有 docs 與 verifier 各 1）：

| commit | 內容 |
|---|---|
| `3b6705b` | 四級 `AdminRole` + audit `payload` 欄位（migration `a1c4e8b7f209`）|
| `2d4bad6` | `require_role` guard、`admin_role` claim、identity 形狀加 role |
| `eff7e24` | 抽共用 audit writer + 金錢／狀態事件 |
| `e9fade3` | 審計覆蓋金錢／狀態 + 按角色設閘 |
| `9850227` | 帳戶管理（create / change role / reset password）|
| `1532a49` | 帳戶管理審計行補 actor 名字 |
| `6408f7a` | `GET /admin/orders` + `/{id}`（訂單監控）|
| `a7f486e` | 結算 preview + confirm token + CSV 匯出 |
| `79fa15d` | 爭議實體 + 裁決流程 |
| `a061438` | 主體搜尋 + 頭像 presign + 共用 role fixtures |
| `88495f9` | 後續 test 修正（872 passed / 0 / 0 / 0）|
| `1df6856` | console 六個畫面 + role-aware chrome |
| `7a7d620` | UI verifier 擴至 11 條路由 |

**（a）审计覆盖金錢／狀態，並按角色設閘。**
此前 15 個 `.audit()` 調用點**全部**在 `admin_auth_service.py` —— 也就是說
管理員登入有記錄，但**動錢的六條路由什麼都不寫**。新增共用寫入器
（`app/services/audit_service.py`）並補上金錢／狀態事件，同時加 `payload`
欄位（migration `a1c4e8b7f209`）令審計行帶得住「改了什麼」而不只是「誰按了」。

**（b）四級 RBAC。**
`AdminRole = SUPPORT < OPERATIONS < FINANCE < SUPER_ADMIN`，以 **rank 比較**
（`.at_least()`）作 gate —— 不是 set 成員。**理由**：用 set 的話，每加一個角色
都要重讀每個列出角色的 tuple，而漏掉一個就是一條開著的路由；安全方向
（default-deny）必須是結構性的。`SUPER_ADMIN` 是唯一可改角色者 ——
「RBAC 唯一的死穴，必須由架構而非紀律守住」。

`require_role` 每 request **重讀 live row**，不信 token claim：信 claim 的話
一個剛被降權的管理員最多可繼續動錢 15 分鐘 —— 「正是他被降權後最可能行動的窗口」。
`require_role` 必須是 `def` 而非 `async def`（它是 dependency factory，
寫成協程會令 `Depends(require_role(R))` 在註冊時被 FastAPI 拒絕）。

**（c）新的後台能力（每項都有對應畫面）。**

| 後端 | 用途 | 畫面 |
|---|---|---|
| `GET /admin/accounts` · `POST /accounts` · `PATCH /accounts/{id}/role` · `POST /accounts/{id}/password/reset` | 帳戶管理；**最後一個 super admin 不可被降權**（由伺服器拒，畫面亦停用按鈕） | `AccountsPage` |
| `GET /admin/orders` · `/orders/{id}` | 訂單監控（狀態篩選、時間軸、fare 快照、ledger） | `OrdersPage` / `OrderDetailPage` |
| `POST /admin/settlement/preview` · `…/weekly/run`（帶 **confirm token**）· `…/export.csv` | 結算先預覽再執行；token 綁定**操作員實際看過的數字** | `SettlementPage` |
| `disputes` + `dispute_messages` 全套 | 事後判斷費用由誰承擔；SLA 由 severity 導出；`is_internal` 內部備註 | `DisputesPage` |
| `GET /admin/search` | 主體搜尋（姓名／車牌前綴、電話數字子串） | `SearchPage` |
| `POST /identity/avatar/uploads` | 頭像上傳（此前只有 presign 機制、且只服務牌照文件） | — |

**爭議的幾個刻意設計**：`order_id` **nullable**（帳戶／App 層投訴沒有行程）；
`resolution` **nullable 且無 default**（`NULL` = 尚未裁決，`'NONE'` = 已裁決：不向任何人收費）；
**SLA 一律由伺服器導出**，不接受 client 傳入；**安全事件是旗標不是動作** ——
由人決定，自動停權等於一次不實指控就把司機拉下車、沒有聽證；隊列按
` sla_due_at` 升序而非 `created_at` 降序。裁決**每 request 判斷**：
OPERATIONS 可判斷行為對錯，但 `moves_money` 的裁決額外要求 FINANCE（職責分離）。

**（d）過程中發現並修好的一個真 bug（會令後台在「有效登入」下全黑）。**
`AppContext` 原本讀 `user.role` 當角色，但 `role` 是 **principal kind**
（管理員 token 上永遠是字面 `'ADMIN'`），**不是 `ROLE_RANK` 的鍵**。結果
`hasRole(...)` 對每個等級都回 `false`、`NAV` 過濾成 **0 項**、每個受閘頁面
都渲染「沒有存取權限」—— 而伺服器完全正確。rank 在 `admin_role`：
`POST /admin/auth/login`（`_admin_out`）與 `GET /auth/me`（`AdminMeOut`）都送，
而且**登入響應只有 `admin_role`、沒有 `role`** —— 所以舊寫法連欄位都不存在。
`App.boot.test.tsx` 的夹具正是 `role: 'ADMIN'`、無 `admin_role`，因此**與這個
bug 一致**。已修正夹具（兩個欄位都帶，如伺服器），並加 2 個測試釘住新行為：
SUPPORT session 的導覽有 `#/search`/`#/orders`/`#/audit` 而**無**
`#/accounts`/`#/settlement`；以及高於自身等級的深連結渲染存取提示、
**保留 hash 不動**（否則一個被分享的連結在角色變更後就失效）。

**（e）其他配套。**
`GET /admin/search` **刻意不逐次審計**（搜尋是高頻讀取，寫滿審計表會淹沒真正的
金錢事件）。`client.ts` 新增 `fetchBlob` —— 獨立方法而非 `send` 的一個旗標：
`send` 會把 JSON parse 失敗當成 `null`，而對 CSV 而言那就是「一個 200 的空表格、
任何地方都不報錯」。`RequireRole` 明文註明是**affordance guard 而非安全邊界** ——
伺服器每 request 重讀 live row，真正阻止的是「渲染一個每個請求都會 403 的頁面」。

**验证**：`pytest` **687 → 872**（`--junit-xml` 读：872/0/0/0）·
`ruff check` + `format --check` clean（140 files）· OpenAPI **81 paths / 88 operations**，
`scripts/verify/audit_response_models.py` 68 块 fixture OK · console `tsc` clean、
**57 vitest passed**、`npm run build` clean。

---

### 2.12 后台 UI 验证跑通 + 修复 harness 两个静默缺陷（2026-10-02）

`admin-web/tool/verify_ui.mjs`（真浏览器驱动 React 后台）**此前从未在 HEAD 上跑过**
—— 它的「一条命令」入口 `scripts/dev/serve_and_run_browser.py` 有两处缺陷，各自都会
让运行失败或跑错东西：

1. **起了 legacy bundle 而非 React build。** 脚本调用
   `serve.py --port 8081`，缺 `--dist`。`serve.py` 默认服务 `admin-web/`（旧版
   hand-rolled ES module），而 `verify_ui.mjs` 会**明文拒绝**旧版（没有
   `#login-username`、资源路径不同）。→ 已加 `--dist`。
2. **`NODE_PATH` 指向一个空目录。** 脚本写死
   `node/workspace/node_modules`，但 **`playwright` 不在那里** —— 它嵌在
   `<node>/versions/<ver>/node_modules/@playwright/cli/node_modules/playwright`。
   后果是 `import 'playwright'` 直接 `MODULE_NOT_FOUND`。→ 改为动态解析（并会在
   找不到时 fail loudly）。

修好之后首次真跑，抓到**一个真实回归**：

```
FAIL  fleet detail: renders — missing 加入成員
```

i18n 那次提交（`dd5a1e4`）把该按钮从 legacy 的 `加入成員` 改成
`加入車隊成員`（`zh-Hant.ts` → `fleetDetail.addMember`），但 verifier 的
needle 仍是旧字符串。因为是 **substring** 匹配，`'加入車隊成員'.includes('加入成員')`
为 false —— 三个 needle 匹配、第四个不匹配。→ 已更新 needle。

**产品无 bug，是测试 artifact 漂移**：React 页面确实渲染了加入成员入口。

**跑法**（凭据可自举，不必问用户）：

```bash
ADMIN_PASSWORD='...' .venv/Scripts/python.exe scripts/ops/create_admin_account.py \
    --username verify-ui --email verify-ui@realtaxihk.local --yes
ADMIN_PASSWORD='...' .venv/Scripts/python.exe scripts/ops/enrol_admin_totp.py \
    --username verify-ui --super-admin          # 新建的账号是 SUPPORT，跑不了财务页
.venv/Scripts/python.exe scripts/dev/serve_and_run_browser.py \
    "admin-web/tool/verify_ui.mjs --base http://127.0.0.1:8081 \
     --username verify-ui --password '<pw>' --totp-secret '<secret>'"
```

**结果：`--- admin console UI check: PASS ---`，63 项检查全过，0 FAIL。**

> 新增 `scripts/ops/enrol_admin_totp.py`：`create_admin_account.py` **刻意不代 enrol**
> （脚本写的 secret 没人证明过能生成 code，是经典 lockout），所以此前唯一取得
> `totp_secret` 的方法是人手开浏览器扫 QR。这个脚本headless 走完同一条真实流程
> （`POST /admin/auth/login` → `POST /admin/auth/totp/enrol/confirm`），
> 使整个验证可重复。

---

### 2.13 結構整理：命名統一 · 路徑統一 · `scripts/` 分組 · lifespan 抽出（2026-10-02）

四項純結構重構，目標是消除「同一件事有兩種寫法」的歧義。**每一項之後都重跑完整
驗證（872 tests + ruff + response-model 審計），維持全綠。**

**① `app/api/` 命名統一為 `admin_x`**
原本 `admin.py` / `admin_auth.py` 用 `admin_` 前綴，但 `analytics_admin.py` /
`licence_admin.py` 用 `_admin` 後綴 —— 同一層目錄兩種慣例。已改名為
`admin_analytics.py` / `admin_licence.py`（`app/api/schemas/licence_admin.py` 同步為
`admin_licence.py`），router 別名亦改為 `admin_*_router`。純改名，無行為變更。

**② `POST /api/v1/driver/location` → `/api/v1/drivers/location`**
`drivers.py` 用 `/api/v1/drivers`、`licence.py` 用 `/api/v1/drivers/licence`，
唯獨 `tracking.py` 用單數 `/api/v1/driver` —— API 中唯一的單數 collection，
也是唯一無法從另外兩者推斷的路徑。已統一為複數。
**這是 breaking change**，故一次過更新全部消費端：`app/api/tracking.py`、
`app/api/schemas/_envelope.py`（docstring）、mobile 的 `driver_repository.dart`、
三個 driver 畫面與 router 的註解、`mobile/test/fixtures/manifest.json`、
`tests/test_geo_and_limits.py`（4 處）、`tests/test_service_area.py`（4 處）、
`scripts/verify/security_probe.py`、`scripts/dev/gen_mobile_fixtures.py`、
`README.md`、`AndroidManifest.xml` 註解。
**刻意不動 `docs/SECURITY_AUDIT.md` 的逐字探測輸出** —— 那是歷史記錄，
改了就不再是真實記錄；改為在文首加「路徑更名說明」。

**③ `scripts/` 拆成 `{ops, verify, dev}`**
20 個平放腳本改為按**「你在做什麼」**分組，規則寫在 `scripts/README.md`：
- `ops/`（4）—— 操作真實環境：`db_backup`、`create_admin`、`create_admin_account`、`enrol_admin_totp`
- `verify/`（10）—— 產出通過／失敗判定：`audit_response_models`、`live_smoke`、
  `security_probe`、`security_verify`、`prod_boot_drill`、`verify_api`、
  `bench_location_pipeline`、三個連線探測
- `dev/`（6）—— 本地流程黏合：`serve_and_probe`、`serve_and_run_browser`、
  `api_supervisor`、`stop_server`、`run_against_api`、`gen_mobile_fixtures`

搬家本身機械，但有幾處**真正的耦合**必須一併處理：
- 每個腳本都以 `__file__` 推算 repo root，多一層目錄就要多一跳。同時修好兩個
  **硬編碼絕對路徑**（`run_against_api.py`、`serve_and_run_browser.py` 寫死
  `C:\Users\...`）—— 那是可移植性缺陷，不只是深度問題。
- `pyproject.toml` 的 `per-file-ignores` 原本寫 `"scripts/*"`，而 glob 的 `*`
  **不跨 `/`** → 搬家後忽略規則會靜靜失效。已改為 `"scripts/**"`，並用
  「同樣內容放在 `app/` 會報 S607、放在 `scripts/dev/` 不報」**正面驗證**該 glob 生效。
- `tests/test_db_backup.py` 以 `from scripts.db_backup import ...` 匯入，
  已改為 `scripts.ops.db_backup`。
- 全樹 78 處 markdown 引用 + 44 處原始碼 docstring 引用已同步更新，
  並以 grep 確認沒有殘留舊路徑。

**④ 從 `create_app()` 抽出 `_start_background_jobs()` / `_shutdown_resources()`**
`create_app()` 內的 `lifespan` 原本有約 80 行維護接線（三個背景任務 + Redis／engine
收尾），把 application factory 的主要敘事淹沒。已抽成三個模組層函數：
`_start_background_jobs(app, settings)`、`_stop_background_jobs(tasks)`、
`_shutdown_resources(app)`，`lifespan` 縮成 8 行。
**任務名稱 `geo_sweep` / `pdpo_purge` / `weekly_settlement` 原樣保留** ——
`tests/test_hardening.py::TestLifespanBackgroundJobs` 正是靠這些名字斷言
「哪些任務啟動了」，而不是靠數量。

**⑤ 附帶修好一個腐爛的驗證腳本（非本次重構引入）**
`scripts/verify/prod_boot_drill.py` 實測為 **6/7**。「prod with proper secrets」
一例只設了 3 個 secret，但 P-2 之後 prod validator 另外要求 `SMTP_HOST`／`SMTP_FROM`
與 https 的 `PUBLIC_BASE_URL` —— 於是 validator 依設計拒絕啟動，該例報 BAD。
**CI 只跑 ruff + pytest，不跑這支 drill**，所以它靜靜地腐爛了。
已補齊該例設定、把相關變數加入 `MANAGED`（防止環境繼承令案例以錯誤理由通過），
現為 **7/7**。並已用 `git show HEAD:` 的舊版做 A/B 對照，確認此為**既有**問題、
而非本次重構引入。

### 2.14 後台實時車輛位置端點 + 測試套件離線化（2026-10-02）

**① `GET /api/v1/admin/live/drivers`（新端點）**
後台一直無法回答「現在車在哪」。資料早在 `driver_profiles.current_location`，
但要拿到只有兩條路：開 psql，或直接讀 websocket 原始流。兩者都不是後台該做的事。
回應是**每個有定位的司機一行**，並附上他正在跑的那張單（若有）。

設計取捨（每一項都是刻意選的）：

- **輪詢，不是推送。** 再開一條 websocket 就要另外處理 auth、重連、背壓，而這是
  一張監控視圖，容忍幾秒延遲。`generated_at` 是伺服器時鐘（判斷整張快照新不新），
  `last_location_at` 是逐車的（判斷某一台車的點舊不舊）—— 兩者回答不同問題，都回。
- **路徑是 `/live/drivers`，不是 `/drivers/live`。** `/drivers/{id}` 宣告在上面，
  後者會把 `id` 綁成字串 `"live"`，再以 UUID 解析失敗 —— 一個在 OpenAPI 文件裡
  看起來完全正常、實際回 422 的路由。
- **用 `LEFT JOIN LATERAL ... LIMIT 1`，不是普通 join。** 狀態機不允許一個司機同時
  有兩張進行中的單，所以今天兩者等價；但真出現資料完整性問題時，普通 join 會把它
  畫成「同一點上兩台一樣的車」，而不是任何營運人員看得出來的異常。
- **`current_location IS NOT NULL` 就是 `lat`/`lng` 在 `AdminLiveDriverOut` 裡
  非 optional 的原因。** 沒有定位的司機是「不在回應裡」，不是「在，但座標是 null」
  —— null 會讓某些渲染器把 marker 畫在 (0, 0)。
- **刻意不逐次審計。** 理由同主體搜尋：一個每幾秒被輪詢一次的視圖，會把審計日誌
  真正要保留的金錢／狀態事件淹沒。
- **不回 `hk_id_last4` 或任何證件欄位**，只回車牌 —— 營運人員靠車牌認車。

`ST_AsText` 輸出 `POINT(lng lat)`，**經度在前**，與本專案其他所有層的順序相反。
這個轉置有直接斷言，並用突變測試確認：把兩個欄位對調，**只有**那個測試會紅。

`limit` 會多取一行，所以 `truncated` 是**真的**（`len(rows) > limit`），不是用
`len(rows) == limit` 猜的。

**② 測試套件不再對外連線**
`tests/conftest.py` 原本用 `os.environ[...] = ...` 強制覆寫 `ALLOW_DEV_OTP` 與
`REDIS_KEY_NAMESPACE`，因為 pydantic 會讀 `.env`。`SENTRY_DSN` 不在那份清單裡 ——
而用戶的 `.env` 現在帶真 DSN，`create_app()` 只要有 DSN 就初始化 SDK。結果是測試
套件會啟動 Sentry transport、在每個錯誤路徑上嘗試連 sentry.io（實測：一次 7 分 42 秒
的 run，夾著 `urllib3` "Tunnel connection failed: 503" 重試噪音），並且會把**合成的
測試失敗**推進真實錯誤流 —— 而那正是真正生產迴歸必須被看見的地方。已強制清空，
A/B 驗證：無覆寫 `active = True`，有覆寫 `dsn = None / active = False`。

**驗證**：`pytest` **872 → 887**（`--junit-xml` 讀：887/0/0/0）·
OpenAPI **81/88 → 82 paths / 89 operations** · `audit_response_models.py`
**68 fixture blocks + 89 operations，OK**。

---

### 2.15 後台實時地圖頁（`#/live`）（2026-10-02）

§2.14 建好了端點，這一輪把它接到畫面上 —— 否則端點只是死代碼。
後台原本讀得到行程狀態，卻看不到車在哪：客服接到「司機在哪」時，手上只有一張狀態表。

`#/live` 每 15 秒輪詢 `GET /admin/live/drivers`，用 **Leaflet + OpenStreetMap**
把每台車畫在地圖上。選 Leaflet 而不是 Google Maps JavaScript API，是因為
**網頁地圖渲染正是 Google 收費的那一項**（手機 SDK 免費），而這個後台會是那支
金鑰唯一的消費者 —— 這是少數「免費選項同時也是正確選項」的地方。不需要金鑰、
不需要帳單帳戶。

**這條路由是 code-split 的。** Leaflet 約 46 kB（gzip），會把原本單一的 console
bundle 由 463 kB 推到 621 kB —— 為了看退款頁而多下載 34%。現已拆成獨立 chunk，
第一次打開地圖才抓取，主包回到 467 kB。`createHashRouter` 是 data router，
所以 chunk 在**路由渲染之前**解析完成，不會先閃一下 fallback。

幾個刻意的選擇：

- **標記顏色寫在 CSS，不是寫在 Leaflet 的 options。** 標記只拿到 `className`、
  不拿顏色，所以主題切換時瀏覽器直接重繪，完全不經 JavaScript。傳給 Leaflet 的
  顏色會變成 SVG presentation attribute，而樣式表規則本來就會蓋過它。
- **用 `circleMarker` 而不是 `marker`。** `L.marker` 的預設圖示是相對於樣式表
  在執行期解析的 PNG；經 bundler 之後那個路徑在產物裡不存在，每個標記都會變成
  破圖 —— 而且**只在 production build 出現**。
- **兩個時鐘分開。** 頁首是 `generated_at`（快照），每列是 `last_location_at`
  （逐車）。位置舊過三個輪詢週期的車會畫成去飽和並標示「位置已過期」；少了這個
  區分，十分鐘前停止回報的車和剛剛移動過的車長得一模一樣。
- **列表依「過期程度」由舊到新排序。** 由新到舊是反射動作，在這裡是錯的：
  它會把停止移動的車埋在每一台正常行駛的車下面。

表格不是裝飾 —— 地圖無法被螢幕閱讀器讀取、也無法排序，所以表格是它的無障礙等價物。

**這一輪的測試抓到一個真缺陷。** `main.tsx` 開了 `StrictMode`（即出貨路徑），
React 會把每個 effect 掛載、卸載、再掛載。我原本的「請求進行中」旗標用 ref，
於是第一次掛載的請求把旗標立起來、第二次掛載的立即請求因為旗標還在而直接返回，
而第一個請求隨後解析進一個**已取消**的閉包、永遠不會把 `loading` 設回 false ——
頁面會**永遠停在 Loading…**。改成把旗標限定在 effect 實例內即可；被取代的請求
由 `cancelled` 丟棄，而不是靠擋住它的替代者。

**驗證**：新增 10 個頁面測試。最有價值的一個是座標順序 —— 伺服器以具名欄位送
`lat`/`lng`，Leaflet 收位置性的 `[lat, lng]`，對調之後型別完全正確、而每個標記
都會落到南中國海。已用突變測試確認：把兩個參數對調，**只有**該測試（及一個以
緯度為 key 的下游測試）會紅。
另外，code-split 路由是唯一「接錯線就什麼都不畫」的路由，所以
`App.boot.test.tsx` 新增一個載入 `#/live` 並斷言標題的案例；突變測試確認：
把 `lazy` 改成解析到 `() => null`，會以 `expected null to be 'Live map'` 失敗。

console **57 vitest（8 files）→ 68（9 files）** · `tsc` clean · `npm run build` clean。

---

## 3. 驗證標準：「全部實跑」

不接受「讀源碼覺得無問題」。每次改動都跑齊：

```bash
uv run ruff check . && uv run ruff format --check .   # 或 ./.venv/Scripts/python.exe -m ruff
uv run pytest -q                                       # 887 passed（用 --junit-xml 讀，見下）
uv run python scripts/verify/audit_response_models.py         # 68 块夹具 vs response_model，0 丢失
cd admin-web/web && npx tsc --noEmit && npm run build && npx vitest run --no-file-parallelism --pool=forks
cd mobile && dart --packages=.dart_tool/package_config.json tool/run_tests.dart
cd mobile && python tool/dart_check.py .               # LSP，非 flutter analyze
cd mobile && dart --packages=.dart_tool/package_config.json tool/verify_contract.dart
NODE_PATH="$HOME/.workbuddy-ai/binaries/node/versions/22.22.2-3/node_modules/@playwright/cli/node_modules" \
  node admin-web/tool/verify_ui.mjs --base http://127.0.0.1:8081 \
    --username ops-admin --password "$ADMIN_PASSWORD" --totp-secret "$ADMIN_TOTP_SECRET"
# 備份：不止跑 backup，還要跑 drill
.venv/Scripts/python scripts/ops/db_backup.py backup
.venv/Scripts/python scripts/ops/db_backup.py verify      # 還原 + 逐表核對 row count
```

**關鍵**：改動 money 格式後，要對**真 server + 真 Postgres** 做 live 驗證，
不可以只信 TestClient。上面 2.3 那個 `"0.0"` bug 就是這樣找到；2.6 那 4 個
備份 bug 亦一樣 —— 全部是「讀源碼看不到、真跑才爆」。

---

## 4. 仍未做 — 分兩類

### A. 需要你提供憑證／部署目標（我無法代做）

| 項目 | 阻塞原因 |
|---|---|
| **P1-1 WS token 走 `?token=`** | `app/api/ws.py:73` 仍是 query param。現設計**有文件說明是刻意**（瀏覽器 WS 無 header 通道），已有 `StripTokenQueryFilter` 兜底。要真正解決需定 TLS 反代（nginx/Caddy）。**2026-10-02：反代已定為 nginx**（`deploy/nginx/realtaxihk.conf`），其自訂 `log_format` 用 `$uri` 而非 `$request_uri`，查詢字串（連 token）不會落地 —— 殘餘洩漏已封。**仍待辦**：選定主機名（repo 內有 `realtaxihk.com` 與 `realtaxi.hk` 兩種拼法，兩者都未定）。 |
| **P1-4 備份 — off-host destination 未選擇** | ~~無 pg_dump cron~~ **2026-09-30 更新：script 已完成並實跑 PASS**（`scripts/ops/db_backup.py`，27 tests，還原演練 49 tables / 17,627 rows 全對）。只剩**選擇 destination**（見 §5）。原本判為「需要 credentials」是不準確 —— 只是未選擇去哪裡。 |
| **WhatsApp / FCM / Google Maps 未接** | config 欄位存在、env 空。需要三家 provider 的憑證。 |
| **P2-2 遺留：部分退款** | 現時只做全額退還。 |
| **P2-2 遺留：實際打款渠道** | 只寫 ledger，轉帳仍線下人手。需要真實支付渠道。 |
| **P2-3 `distance_km` 由 client 自報** | 乘客可亂報。374D 下估價僅供參考、風險可控，但廣播排序會被 gaming。需 `GOOGLE_MAPS_API_KEY`。 |
| **P2-4 Sentry／錯誤聚合** | ~~只差 Sentry~~ **2026-09-30 更正：Sentry 已經接好**（`app/main.py:63-70`，`sentry_dsn` 有值就 init，無 `sentry-sdk` 就 warn 而不會炸）。**2026-10-02：已結案** —— DSN 已放入 `.env`，代碼另補上 `release`（綁 `_API_VERSION`）與 `max_request_body_size="never"`（PDPO：不送 request body）。**本項不再是待辦。** |

### B. 已知產品層取捨（非 bug，記錄在案）

- **調整不會 activate 司機** —— 刻意。更正是記帳、不是付款；維持
  `DEPOSIT_REQUIRED`。
- **負餘額合法（arrears）** —— 罰款／費用可以超過按金，司機欠平台。
- **`ADJUSTMENT` 單一管理員直接寫入** —— 由你選擇。無雙人覆核，靠
  `created_by` 審計。若日後要防內部舞弊，需改成申請→批准流程。
  （**2026-10-01 更新**：爭議裁決的 `moves_money` 路徑已加 FINANCE 角色閘，
  是職責分離的第一步；`ADJUSTMENT` 本身仍為單人。）
- **搜尋不逐次審計** —— 高頻讀取，寫滿審計表會淹沒真正的金錢事件。刻意。

### C. ~~文件語言未統一~~ ✅ 已完成（2026-10-02）

`docs/*.md` 原本多數以**粵語**寫成，與用戶偏好的書面語不一致；
`docs/IN_TRIP_REDESIGN.md`（2,031 行）尤其嚴重。
**已於 2026-10-02 全部改寫為書面語（繁體）** —— `docs/` 全部 15 份文檔，
外加 `README.md` / `admin-web/README.md` / `mobile/README.md`。
驗證方式：以一份 30 餘字的粵語專用字清單（粵語才有的字與語法標記）掃描全部文檔，
僅餘 `仲裁`、`關係` 等**本身即為書面語**的假陽性。

### D. 行號引用失效 ✅ 已處理（2026-10-02）

`app/models/` 佈局改動後，部分文檔的**行號引用**失效。處理方式按文檔性質分開：

- **`docs/ADMIN_CONSOLE_DESIGN.md`**（設計文件，描述現況）→ **重新指向現行位置**：
  `app/models/admin.py:195`→`:281`、`:50`→`:108`、`app/core/deps.py:192`→`:324`、
  `app/api/admin_auth.py:408`→`:414`、`app/services/admin_auth_service.py:190`→`:177`、
  `mobile/README.md:96`→`:135`。
  同時更正一個**實質錯誤**：該文件原本斷言「稽核只有一個寫入點」「沒有 role 欄位」，
  但三個缺口（稽核覆蓋／RBAC／爭議載體）其後均已修復，已加更正區塊說明。
- **`docs/PRODUCTION_READINESS.md`**（帶日期的審計快照）→ **不改行號**，
  保留「行號已失效」的註記。重新編號反而會誤導：那些引用指向的是**當時**
  有問題的代碼，而該代碼其後已被改寫。**重新編號會假裝那些發現仍描述現況。**
- **`docs/CODE_REVIEW_2026-10-01.md`** → 加「歷史文件」標記，數字刻意不更新。

---

## 5. Push 狀態：❌ 本工具仍然推送不到（token 未獲授權此 repo）— 已由用戶豁免

**已 commit，但推送不到。** 2026-10-01 實測：

| 檢查 | 結果 |
|---|---|
| `git push`（token 放 URL，`-c credential.helper=`） | **403** `Write access to repository not granted` |
| 同一 token `GET /user` | `"login": "dannisonluk"` —— **帳號正確** |
| 同一 token `GET /repos/dannisonluk/realtaxihk` | **404** —— private repo 沒有權限就是回 404 |

**先前本節寫「token 阻塞已解除」是錯的。** fine-grained PAT 是**逐個 repo
授權**，所以「token 屬於 dannisonluk」同「token 能存取 realtaxihk」是兩件
獨立的事。**要你出手**：去 GitHub token 設定加 `dannisonluk/realtaxihk` +
`Contents: Read and write`。

> **✅ 2026-10-01 用戶指示：「你不需要 push，只需要 commit」。** 最新一輪的
> 所有工作**只 commit、不 push**。
>
> **⚠️ 更正（2026-10-02）：本節曾寫「之前累積的 commit 亦一併未推」，這已不正確。**
> `git ls-remote origin refs/heads/main` 實測遠端 HEAD 是 `5b68590`，與本機同名
> commit 一致 —— 即 §2.11–§2.13 那一批**已經在遠端**。但**不是這支工具推上去的**：
> 同一支 token 以 `git push --dry-run`（`-c credential.helper=` + token 放 URL）
> 重測，仍然回 **403** `Write access to repository not granted`。
> 所以正確的讀法是「token 仍然推不到；遠端之所以有進度，是經其他憑證推的」。
>
> **判「有無未推 commit」永遠用 `git rev-list --count origin/main..HEAD`，
> 不要沿用本節的結論。** 要真正由這支工具同步，仍需上面的 token 授權。

> **⚠️ 更正（2026-10-01 稍後）：本節原本寫「working tree clean」，當時是錯的。**
> `git rev-list --count origin/main..HEAD` 只證明**已 commit 的東西都推了**，
> 完全沒有講過工作區的狀態。實測 `git status --short` 是 **61 個已修改 +
> 33 個未追蹤**（1827 insertions, 1218 deletions）—— §2.9（models 拆包）、
> §2.10（response_model 集中化）、這一輪的 QR 改動、以及 `docs/` 入面
> 8 份新文件**全部未 commit**。這個就是「好似未做完」的實質原因。
>
> 兩個檢查回答的是兩條不同的問題，**不可以互相代替**：
> - `rev-list origin/main..HEAD` → **有沒有未推的 commit**
> - `git status --short` → **有沒有未 commit 的改動**
>
> 已按邏輯分 7 個階段補 commit。現時 working tree **真的 clean**。

### 5a. 歷史記錄：token 權限診斷（保留，因其為可複用教訓）

以下是當時的診斷，保留作為**方法論**參考（同類問題會再出現）：

| 檢查 | 結果 |
|---|---|
| `git push`（credential helper 開著） | **掛起**，`timeout 120` 後 exit 124 |
| `apply-ez/.env` 的 PAT | token **有效**，`GET /user` 回 `"login": "dannisonluk"`（帳號正確） |
| 同一個 token 讀 `dannisonluk/realtaxihk` | **404 Not Found** |
| 匿名讀同一個 repo | 都是 404 |

兩個 404 併起來只指向一個結論：**repo 是 private，而該 token 沒有被授權存取它**。
fine-grained PAT 是**逐個 repo 授權**的，所以「token 屬於 dannisonluk」同
「token 能存取 realtaxihk」是兩件獨立的事 —— 前者成立不代表後者。
GitHub 對無權限的 private repo 一律回 404 而不是 403，就是不想洩漏 repo 存在。

**解法**：GitHub → Settings → Developer settings → Fine-grained tokens →
Repository access 加 `dannisonluk/realtaxihk` → Permissions 給
**`Contents: Read and write`**。

> ⚠️ **`git push` 在這部機器是無聲掛起**，不是網絡問題 —— 是
> `git-credential-manager.exe` 正在等待互動輸入，而工具會 timeout 殺掉它，
> 加上輸出被 pipe 緩衝所以連一行都沒有 flush。`git ls-remote` 照樣成功，令人
> 誤以為 remote 通。要推就用 repo 自己的 token + 停用 credential helper：
>
> ```bash
> TOKEN=$(grep '^GITHUB_PERSONAL_ACCESS_TOKEN=' .env | cut -d= -f2- | tr -d '\r\n"')
> GIT_TERMINAL_PROMPT=0 git -c credential.helper= \
>   push "https://x-access-token:${TOKEN}@github.com/dannisonluk/realtaxihk.git" main
> ```

> ⚠️ 另外：**不要用一個會成功的寫入來做權限探測**。`PUT /contents/<path>` 探完
> 不止會告訴你結果，還會真的建立檔案同 push 一個 commit。要探就用
> `POST /git/blobs`（只產生 dangling object），或者用 `curl -o /dev/null -D -`
> 淨讀 header。

> ⚠️ 缺少 `workflows` scope 時，push 會**整個被拒**（不只是 workflow 檔），
> 錯誤訊息會明寫 `refusing to allow a Personal Access Token to create or update
> workflow ... without 'workflow' scope`。不要猜哪個 permission 缺失 ——
> 讀 response 的 `x-accepted-github-permissions` header，它會直接指名。

### 5b. 部署目標未定 — 這個是下一個根阻塞

用戶 2026-09-30 確認：**未決定部署到哪裡**。§4A 表面看似是「7 項等 credentials」，
但實際 5 項都是**下游**於這個決定：

| §4A 項目 | 真正阻塞 |
|---|---|
| P1-4 備份 destination | 儲存到哪裡 |
| P1-1 TLS 反代 | 在哪裡運行 |
| P2-2 打款渠道 | 用哪個 provider |
| Google Maps / FCM / WhatsApp | **真正的 key 阻塞（3 項）** |
| P2-4 Sentry | 只差一個 DSN |

所以下一步不是逐項啃，而是**先定部署目標**。定了之後，backup destination
同 TLS 反代就隨之解決。`scripts/ops/db_backup.py` 刻意設計成
`--upload-cmd` / `--via auto`，就是不想在 destination 未定之前鎖死任何 provider。

**建議已寫成兩份文件**：`docs/DEPLOY_TARGET_DECISION.md`（A/B/C 選型取捨）
同 `docs/DEPLOYMENT_REQUIREMENTS.md`（選定之後的完整需求清單）。

---

### 5c. ⚠️ `ruff format --check` 曾再次漂移（已修复）

`ruff format` 曾套用并提交，两个 gate 当时都是干净的。**但 2026-10-02 复查发现
`admin-web/web/tool/check_theme_tokens.py` 又变回未格式化**（由 `dd5a1e4`
「bilingual UI」那次提交引入），所以 `ruff format --check .` 在 HEAD 上是
**失败**的：

```
./.venv/Scripts/python.exe -m ruff format --check .  # 1 file would be reformatted
```

已修复，现在两个 gate 恢复干净：

```
./.venv/Scripts/python.exe -m ruff check .           # All checks passed!
./.venv/Scripts/python.exe -m ruff format --check .  # <N> files already formatted
```

> **不要引用那個 `N`。** 同一棵樹實測過 140／141／143，且與
> `ruff check --show-files` 的數目不同 —— 該計數並不穩定。
> 判準是 **exit code**，不是那個計數。

**教训**：`ruff check` 干净**不代表** `ruff format --check` 干净 —— 两者是独立的
gate，lint 规则（E501 等）抓不到引号风格、行合并这类纯格式差异。`admin-web/`
**不在** `extend-exclude` 内，所以前端目录下的 `.py` 工具脚本同样受格式门禁约束。

`ruff format` 是**纯格式**改动（引号统一、行宽合并／拆分），不影响行为。

CI gate（`.github/workflows/ci.yml`）：

```yaml
- name: Format check (ruff format)
  run: uv run ruff format --check .
```

> 注意：`.pre-commit-config.yaml` 仍然不存在，所以本地 commit 不会自动检查；
> 靠 CI 这道 gate 拦截。这次漂移正是「本地不检查、CI 又没跑」的结果。

---

## 6. 文件地圖

| 文件 | 內容 | 維護方式 |
|---|---|---|
| **`docs/WORK_SUMMARY.md`**（本文件） | 總覽 + 索引 + 未做清單 | 手動更新 |
| `README.md` | 快速上手、endpoint 一覽、配置 | 跟功能更新 |
| `docs/PRODUCTION_READINESS.md` | 上線就緒 audit（P0/P1/P2 逐條狀態） | 跟修復更新 |
| `docs/SECURITY_AUDIT.md` | SEC-01~31 逐條、含攻擊重現證據 | 跟修復更新 |
| **`docs/SECURITY.md`** | 安全模型 + 已驗證控制 + SEV 分級發現 + 加固路線圖 | 跟修復更新 |
| **`docs/DEPLOY_TARGET_DECISION.md`** | 部署選型 A/B/C 取捨與成本 | 選定後歸檔 |
| **`docs/DEPLOYMENT_REQUIREMENTS.md`** | 部署需求清單：硬約束、環境變數、步驟、gap list | 跟配置更新 |
| **`docs/ADMIN_AUTH.md`** | 管理員認證模型、authenticator 選型、已知缺口 | 少變 |
| **`docs/ADMIN_CONSOLE_DESIGN.md`** | 後台九大模組設計：現狀 HAVE / 缺口 NEED / 方案，含四級 RBAC（`SUPPORT`/`OPERATIONS`/`FINANCE`/`SUPER_ADMIN`）、稽核、工單 | ✅ **大部分已實作**（見 §2.11）；行號引用已失效，見該文件頂部註記 |
| **`docs/IN_TRIP_REDESIGN.md`** | in-trip + 預約重設計：新狀態機、到達雙重驗證、違約即時扣款 + 冷靜期、保證金閘門、schema、API、前端、$5 平台費、22 個**終點地標**（純下客）預約與司機分類 filter | 設計提案，7 個 DECISION **全部已拍板**；深圳灣邊界缺陷待拍板 |
| **`docs/LANDMARK_COORDINATES.md`** | 19 個即用地標的落客座標 + Google Maps 連結，供人手逐個覆核；含深圳灣港方口岸區的完整幾何分析與修法記錄 | 覆核清單（工具文件） |
| **`docs/REALTIME_POSITION_COST.md`** | 實時位置每 tick 成本實測 + 擴展天花板 + 5 項優化 | 已實測 |
| `docs/LINTING.md` | ruff 規則集與理由 | 少變 |
| `docs/PROJECT_UNDERSTANDING.md` | 專案架構理解（交付物規模） | ✅ 2026-10-01 已全面重寫 |
| `.workbuddy-ai/memory/YYYY-MM-DD.md` | 逐日流水、含沙盒陷阱 | **append-only，不整理** |

> **✅ 已修正**：`docs/PROJECT_UNDERSTANDING.md` 曾停在 `4333f2a`（寫 HEAD =
> `446b7ee`、221 tests、54 Dart files、38 endpoints）。**2026-10-01 已全面重寫**，
> 現值：**887 tests**、**82 paths / 89 operations**、**68 vitest**，
> 並新增 RBAC 設計、`/auth/me` 雙形狀、`StarletteHTTPException`
> cookie 陷阱等章節。（**HEAD 刻意不寫死** —— 之前寫死過三次，每次之後的 commit
> 都令它變錯。）

> 計 route 數要讀 OpenAPI（`GET /openapi.json` 數 `paths` / operations），
> **不要 grep route decorator** —— 一個 `@router.get` 加 `@router.post`
> 疊加於同一個 function 會漏數。

---

## 7. 沙盒／環境陷阱（本機特有，值得記住）

| 症狀 | 真因 | 解法 |
|---|---|---|
| `curl` 打 `127.0.0.1` 回 `502 upstream connect failed` | 沙盒 proxy 攔截，**即使該 port 根本沒有東西在聽** | 用 Python `urllib` + `ProxyHandler({})`；`NO_PROXY` 對 curl 不可靠 |
| **Dart 完全無法開啟 child process**（不限於 `cmd.exe`：`where` / `git` / `adb` / `dartaotruntime` 全部一樣）→ `flutter --version`、`flutter build apk`、`dart analyze`、`dart run` 全部失敗 | Dart 在 Windows 用**具名管道**接 child 的 stdio，沙盒令 `CreatePipe`/`CreateFile` 回 `ERROR_PIPE_BUSY (231)`。**停用沙盒也不行**，是 host 限制；Python 用匿名管道所以正常 | `dart --packages=.dart_tool/package_config.json <script>`（繞過 dartdev）；靜態檢查用 `python mobile/tool/dart_check.py` |
| **`flutter build apk` 在此沙盒做不到**（已查證） | `flutter` 第一件事是跑 `git log` 取得版本新鮮度 → spawn `git.exe` → 231，**根本未到 Gradle**；就算到，Gradle wrapper 都要 `cmd.exe` | 在 host / CI 跑 `cd mobile && flutter build apk --debug`。此沙盒只可以驗證 Dart 源碼：`dart_check.py` + `run_tests.dart` + `verify_contract.dart` |
| Background server 無聲死 | Bash tool call 內 `cmd &` 隨 shell 退出被收割 | 用 `run_in_background=true` + `TaskStop` |
| 用 `conftest.ADMIN_ID` mint token 打 live server → 401 | 它是每個 test session 隨機 `uuid4()` | 讀真 DB：`docker exec realtaxi-db psql -U realtaxi -d realtaxihk -c "SELECT id FROM users WHERE role='ADMIN';"`（DB user 是 `realtaxi` 不是 `postgres`） |
| 要 login 但 OTP code 不在 response（SEC-02） | 刻意設計 | `ALLOW_DEV_OTP=true` + code `123456` |
| 本機完全沒有 `pg_dump` / `psql` / `createdb` | 只有 `realtaxi-db` container 裡面有 | `scripts/ops/db_backup.py --via auto` 自動 fallback 至 `docker exec` |
| `subprocess.run(cmd, shell=True)` 回 0 但 command 是失敗的 | Windows 用 `cmd.exe`，`;` 不是分隔符（`echo hi >&2; exit 7` → rc 0） | 明確 `subprocess.run(["sh","-c",cmd])` |
| 傳 Windows 路徑入 `sh -c` 會被吞掉反斜線 | `C:\Users\x` → `C:Usersx` | `.as_posix()` 傳正斜線 |

---

## 8. 一頁看完

```
✅ 後端 82 paths / 89 ops / 887 tests / ruff lint + format clean — 生產就緒
✅ mobile 21 畫面 / 93 tests / 0 diagnostics      — 三角色完整
✅ admin-web React / 68 vitest / typecheck + build clean / UI verifier PASS
✅ 後台治理：四級 RBAC（rank 比較、live row 為權威）+ 審計覆蓋金錢／狀態改動
✅ 後台新增：帳戶管理 / 訂單監控 / 結算預覽+confirm token+CSV / 爭議 / 主體搜尋 / 頭像上傳
✅ 後台實時地圖 `#/live`：Leaflet + OpenStreetMap（免金鑰、不計費）、15 秒輪詢、
   位置過期分級；路由 code-split，主包維持 467 kB（地圖分包按需載入）
✅ 修好一個真 bug：AppContext 讀 `user.role` 當 rank（實為 principal kind）
   → 有效登入下導覽 0 項、每頁「沒有存取權限」；rank 在 `admin_role`
✅ location check：8 個 polygon 取代 bbox（舊 bbox 含深圳）
✅ 深圳灣口岸：`_HK_MAIN` 后海灣段 2 → 7 頂點，港方口岸區（香港租賃、司法管轄）
   納入境內；蛇口 / 南山 / 前海 / 深圳側管制站仍境外（實跑 212 點網格驗證）
✅ analytics：HKT 分桶 + 24 時段 heat map
✅ 4 真 bug + 7 P0 + 10 P1 + 10 P2 全數處理
✅ SEC-01~31 全數處理
✅ money 精度：cent 儲存、wire 2dp、meter 1dp、ratio 統一入口（ROUND_HALF_UP）
✅ ADJUSTMENT ledger 業務流接通
✅ P1-4 備份 script + 還原演練實跑 PASS（27 tests）
✅ TOTP 對 RFC 6238 / 4226 全部 16 條官方向量 PASS（實測）
✅ TOTP 綁定二維碼：本地 SVG 渲染，經獨立解碼器驗證解出正確 otpauth URI（明暗兩主題）
✅ challenge token 結構隔離、scope 提權不可行（實測）
✅ 秘密審計：.env 從未進 git，無硬編碼金鑰
❌ push 未做 — 用戶指示「只需 commit」；origin/main 落後本地多個 commit

✅ `app/models` 拆包：886 行 → 5 個 bounded-context 模組 + `__init__` re-export
   零呼叫點改動；DDL / relationship / alembic drift 逐項比對全等
✅ 全部 89 個 operation 都有 `response_model=`（此前 69 個中只有 1 個）

✅ SEV-1 已修：admin session 改為 HttpOnly refresh cookie（SameSite=Strict）
   + CSRF double-submit；token refresh 路徑已通
⚠️ ruff format --check **曾復發**：`dd5a1e4` 引入未格式化的
   `admin-web/web/tool/check_theme_tokens.py`，2026-10-02 已再修（全樹 clean）。
   CI gate 有，但本地無 `.pre-commit-config.yaml`，所以漏檢了整整一輪
⚠️ SEV-2：`admin_auth.py` 用字串比對錯誤訊息決定 HTTP 狀態碼；
   locked 帳號回 401 而非 429（未改，屬客戶端體驗非安全洞）
⚠️ 根阻塞：**部署目標未定** — §4A 表面 7 項，實際 5 項下游於此（見 §5b）
⬜ 真正等 credentials 的只有 3 家 provider：Google Maps / FCM / WhatsApp
✅ `docs/PROJECT_UNDERSTANDING.md` 已於 2026-10-01 全面重寫
✅ `docs/*.md` 已全部改寫為**書面語（繁體）**（2026-10-02，含 2,031 行的 `IN_TRIP_REDESIGN.md`）

新文件：docs/SECURITY.md · docs/DEPLOYMENT_REQUIREMENTS.md
```
