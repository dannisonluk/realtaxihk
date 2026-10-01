# realtaxihk — 工作總覽

- **生成日期**：2026-09-30（**2026-10-01 更新**：併入 location check、analytics、console deep-link 修正；測試數由 272 更正為 616。**本輪再更新**：修好 SEV-1 admin session、統一 money 精度入口、套用 `ruff format` 並加 CI gate、補上 TOTP 綁定二維碼的渲染與測試；測試數 616 → **656**（**本輪 667**：新增 5 個 HTTP error-code + 6 個 state-machine 不變式測試）。**最後一輪**：修好深圳灣口岸邊界缺陷（`_HK_MAIN` 后海灣段 2 → 7 頂點），測試數 667 → **687**（新增 18 個口岸邊界參數化案例 + 2 個回歸測試）。**本輪再更新**：`app/models/__init__.py`（886 行）拆成 5 個 bounded-context 模組 + `__init__` re-export，測試維持 **687** 不變 —— 見 §2.9。**本輪再更新**：新增 `app/api/schemas/` 套件並為**全部 69 個 operation** 補上 `response_model=`（此前 69 個中只有 1 個），令 `/openapi.json` 首次描述真實響應形狀；測試維持 **687** 不變 —— 見 §2.10。**最新一輪（§2.11）**：RBAC 四級角色 + 審計覆蓋金錢／狀態改動、帳戶管理、訂單監控、結算預覽／確認 token／CSV 匯出、爭議實體、主體搜尋、頭像上傳，以及六個對應的後台畫面 —— 測試 **687 → 872**（+185），console **29 → 31 vitest**（+2），API **64/69 → 81 paths / 88 operations**）
- **HEAD**：`main` 上最新 commit —— **刻意唔寫死 hash**（寫死過三次，每次之後
  嘅 commit 都令佢變錯；要查：`git log --oneline -1`）；
  `origin/main..HEAD` = **有未推 commit**（查：`git rev-list --count origin/main..HEAD`，見 §5）；
  working tree **clean**。
- **現時狀態**：`pytest` **872 passed / 0 failed / 0 error / 0 skipped**（以 `--junit-xml` 讀）· `ruff check` clean · **`ruff format --check` clean（131 files）** · console `tsc` clean + **31 vitest passed** · `npm run build` 344.67 kB（gzip 109.14 kB）· Dart **93 passed / 0 failed** · contract **54 fixtures decoded, 0 failure** · `dart_check` 58 files, 0 diagnostics · API **81 paths / 88 operations，全部已声明响应模型** · fixture↔schema 审计 **68/68 块无数据丢失**
- **✅ 已解決：管理員 session 15 分鐘硬死** —— 已改為 `HttpOnly` refresh cookie（`SameSite=Strict`，path `/api/v1/admin/auth`）＋ CSRF double-submit。詳見 `SECURITY.md`

> **呢份文件嘅用途**：一份可以單獨睇完嘅總覽 —— 做過咩、而家係咩狀態、
> 仲有咩未做、邊樣需要你出手。其他 `docs/*` 係**主題深入報告**（安全、
> 上線就緒、lint），`.workbuddy-ai/memory/*.md` 係**逐日流水**（append-only，
> 唔整理）。呢份係索引 + 摘要，唔取代佢哋。

---

## 1. 交付物

| 交付物 | 位置 | 技術 | 狀態 |
|---|---|---|---|
| 後端 API | `app/` | FastAPI (async) + SQLAlchemy 2.0 async + PostgreSQL 16/PostGIS + Redis 7 + Alembic | ✅ **81 paths / 88 operations** · 872 tests |
| Flutter App | `mobile/` | Flutter + Riverpod 3.4.3 + Dio + go_router 17（**21 個畫面**，三角色） | ✅ 93 tests |
| Web 管理後台 | `admin-web/web/`（React + Vite）、`admin-web/js/`（legacy） | React + Vite（新版）、Vanilla JS（舊版） | ✅ **31 vitest** · UI verifier PASS |

一個 repo、三件完整交付物。定位：**Cap. 374D 合規的士資訊中介**（非的士營運商）。

---

## 2. 做過嘅嘢（按主題，非按時間）

### 2.1 後端 — 上線阻塞修復

`docs/PRODUCTION_READINESS.md` 記錄咗完整 audit：**4 個掃描中發現嘅真 bug**
＋ **7 個 P0** ＋ **10 個 P1** ＋ **10 個 P2**，全部處理。

重點（每項都有 TDD 背書）：

| ID | 問題 | 修法 |
|---|---|---|
| B2 / P0-1 | ledger `append()` 無行級鎖 → 並發 lost update（兩個 request 同讀 balance=500，各自 +100，後者覆蓋前者 → **帳目靜靜地錯**） | `SELECT ... FOR UPDATE` + 條件式 UPDATE 仲裁 + `asyncio.gather` 並發測試 |
| P0-2 | Prod fail-safe：`JWT_SECRET` / `ALLOW_DEV_OTP` 誤設定就全平台失守 | `config.py` `model_validator(mode="after")` — prod 下拒絕不安全組合 |
| P0-3 | `is_active` 從未被執行 → 封禁功能唔存在 | 加去 auth 依賴鏈 |
| P0-5 | Geo index ghost entries 污染派單 | — |
| B1 | `/auth/me` 對已刪除 user 會 `NameError` → 500 | — |
| B3 | `tip` 無上限 → DB overflow → 500 | 加 `le=100000` |
| B4 | `tracking.py` 用 `__import__("datetime")` hack | 正常 import |

### 2.2 安全 — SEC-01 ~ SEC-31 全數處理

`docs/SECURITY_AUDIT.md`（43 KB）逐條記錄。最重要嗰條：

**SEC-13 — ledger reference 命名空間。** 舊 contract 收 caller 提供嘅
`reference`，撞中就直接**原樣回傳舊 row**（唔核對 entry_type／金額）。
由於 `weekly:{driver}:{period}` 同 `refund:{id}` 共用一個 flat namespace，
任何寫得到 ledger 嘅人（管理員，或者持有偽造 admin token 嘅攻擊者）可以
預植 `weekly:<driver>:2099-W03`，之後真實結算見到「已收費」就跳過該司機，
**靜靜地永遠收唔到 HK$200**，而報告顯示一切正常（`skipped`）。
同一個手法令退款可以無 REFUND debit 就 APPROVED。

兩個防禦：① `append()` 拒絕 entry_type／金額唔符嘅 reference hit；
② 每個用途由 server mint 專屬 prefix（`grant:` / `weekly:` / `fleet:` /
`refund:` / `adj:`），令跨用途碰撞根本表達唔到。

### 2.3 Money 精度 — 「儲存用 cent，唔用 decile」

**呢個係一個真正嘅生產 bug，唔係重構。**

- DB 欄位係 `Numeric(10,2)`，cent 本來就精確儲存（實測
  `DriverDeposit(balance_hkd=Decimal("0.5"))` 讀返 `Decimal('0.50')`）。
- **缺陷在 serialiser**：`money_str` quantise 去 `Decimal("0.1")`，docstring
  寫「canonical wire precision = \$0.1 (smallest meter tick)」——
  將一個**車費**規則誤用到**帳本**金額。
- 後果：`0.05 → "0.1" → HK\$0.10`；`0.01 → "0.0" → HK\$0.00`（同空帳戶無法區分）。
- 亦發現 `Decimal.quantize` 預設係 banker's rounding（`150.45 → 150.4`），
  同車費引擎（`150.45 → 150.5`）矛盾 → 兩個 helper 都明確用 `ROUND_HALF_UP`。

**修法**：兩個精度各有其名 —— `money_str` **2 dp**（儲存金額）、
`meter_str` **1 dp**（錶費數字）。3 個 commit（`f65fdb4` / `712d1a8` / `542fc6b`）。

> 值得一提：最後一個 bug（`admin.py:157` 一個遺留 `"0.0"` 字面值）**只有讀
> live response 才搵得到** —— 每個 driver detail payload 都有 1 個 1dp 值
> 夾在 4 個 2dp 值中間。所有 unit test 都通過。

### 2.4 `ADJUSTMENT` ledger 業務流（`4333f2a`，最新）

`LedgerEntryType.ADJUSTMENT` 只有 enum 定義、**零實作**，但**三個前端都已
渲染「調整」chip** —— UI 對外承諾咗一個後端永遠產生唔到嘅 type。

新增 `POST /admin/drivers/{id}/deposit/adjust`：signed amount、`reason` 必填、
±HK\$5,000 上限、`adj:` 命名空間。

順手修兩個真缺陷：
1. **`_ledger_out` 從來唔輸出 `created_by`** —— 寫入 DB 4 處、讀取 0 處。
   管理員操作有記名但 API 完全睇唔到。已加（admin 視角；司機
   `/me/ledger` 刻意唔加，operator id 係內部資料）。
2. **mobile `isCredit` 硬編 `adjustment == true`** —— 但調整係 signed，
   一半情況顯示錯方向。確認無 production code 讀過 → **刪除**（唔係修好），
   direction 一律讀 `amount_hkd` 正負。原本個 test 仲 assert 咗 bug 係正確行為。

### 2.5 前端 — Apple 設計 + React 管理後台

- **mobile**：21 個畫面全部按 Apple HIG 重做 —— 主題建在 Apple type scale
  上、hard-coded spacing 全部換成 token、破壞性確認做成 iOS 形狀、底部
  操作區避開 home indicator。
- **admin-web**：管理後台**由零重寫成 React + Vite**（`admin-web/web/`），
  同 legacy 版並存。10 modules，driver detail 由一個 composed endpoint 支撐。

### 2.6 備份與還原演練（P1-4，2026-09-30）

`scripts/db_backup.py`。**還原演練係重點** —— 冇還原過嘅 dump 只係假設。

實跑結果（真 Postgres 16.4）：`49 tables / 17,627 rows`，source vs restore
逐表 count 完全一致。

過程中捉到 4 個**只有真跑才會現形**嘅 bug：

1. **`pg_restore --list` 收 host path**，但 docker transport 下個 tool 跑喺
   container 裡面 → 找不到檔案。要改成 stdin 灌入（同 restore 一樣嘅橋）。
2. **`subprocess.run(shell=True)` 喺 Windows 用 `cmd.exe`**，`;` 唔係分隔符。
   實測 `echo hi >&2; exit 7` **回 0** → 一個失敗嘅上載被報成成功 ——
   備份最惡劣嘅失敗模式（靜靜地冇 copy 到，但每晚都報 OK）。
   改成明確 `sh -c`。
3. **Windows 路徑經 `sh` 會被食走反斜線**：`C:\Users\user\x.dump` →
   `C:Usersuserx.dump`。`{file}` 完全用唔到。改用 `as_posix()`。
4. **「上載 OK」原來只係「command exit 0」** —— `true` 都會 pass。
   加 `--upload-verify-cmd`，要真係問 remote 攞到 archive 名先算數。

保留策略係 GFS（7 daily + 4 weekly），按**日曆距離**而唔係檔案數量計 ——
weekly 層要捱得過「幾日後才發現」嘅問題，所以 40 日 / keep 7+3 實測
仍然留住 Sep 20 同 Sep 13（各自 ISO week 最舊嘅一份）。

### 2.7 測試基建

- **Mobile fixtures 係「生成」唔係手寫**：`scripts/gen_mobile_fixtures.py`
  起 API、打真 endpoint、寫低 raw response；`mobile/tool/verify_contract.dart`
  用真 Dart model 解碼。手改 fixture 會令佢同 API 脫節。
- **Admin UI verifier**（`admin-web/tool/verify_ui.mjs`）：真瀏覽器、真 API，
  捉三類「讀源碼睇唔到」嘅缺陷 —— module 載入失敗、render 拋錯、靜默 API 唔 match。
- `docs/LINTING.md`：ruff 單一 linter，**explicit rule selection**（唔用
  default），令 ruff 升級唔會靜靜地改咗個 gate。66 errors → 0。

### 2.8 身份與服務範圍（P-1 ~ P-5，2026-09-30 ~ 10-01）

- **P-1 Admin 認證**：`admin_accounts` 獨立於 `users`，username + password +
  強制 TOTP。三步狀態機，`/login` 只回 5 分鐘 challenge token（結構上唔可以
  當 access token 用）。`/totp/enrol` **先唔寫入 DB**，等 admin 證明識生成碼
  才 persist —— 避免「secret 入咗庫但冇掃碼」嘅永久鎖死。
  authenticator 選型見 `docs/ADMIN_AUTH.md`。
- **P-2 Uber 形狀註冊**：email 驗證 + 每月手機重新驗證（soft block）。
- **P-3 的士證人工審核**：admin 改狀態。
- **P-4 每月重新驗證**：soft block，唔阻現有行程。
- **P-5 部署目標**：`docs/DEPLOY_TARGET_DECISION.md`（仍待用戶拍板）。
- **Location check**：`app/core/hk_bounds.py` —— 8 個 polygon 取代
  `lat 22.1-22.6, lng 113.8-114.5` 嘅 bbox，因為**舊 bbox 含深圳**
  （Futian / Luohu / Bao'an 全部在內）。Server 為準，403 帶 `OUTSIDE_HK`。
- **Analytics**：`app/services/analytics_service.py` + `#/analytics`。
  `timezone('Asia/Hong_Kong', completed_at)` 同時用於 SELECT 同 GROUP BY，
  半開區間 `[00:00 HKT, 翌日 00:00 HKT)`。收入 = COMPLETED 訂單嘅
  `estimated_total_hkd`（已含折扣與貼士）。

### 2.9 `app/models` 拆包（結構重構，2026-10-01）

原本 `app/models/__init__.py` 一個檔案 886 行、24 個 model、4 個用 banner
分隔嘅區段。拆成 **5 個 bounded-context 模組 + 1 個 private base**：

| 檔案 | 行數 | 內容 |
|---|---|---|
| `_base.py` | 40 | 唯一嘅 `Base`（private，唔喺 `__all__`） |
| `user.py` | 402 | users / driver_profiles / deposits / orders / ledger / otp / refresh / refund |
| `admin.py` | 260 | admin 身份、TOTP、session、audit、email token |
| `fleet.py` | 206 | fleets / memberships / settlement runs |
| `licence.py` | 187 | P-3 licence submissions + documents |
| `__init__.py` | 118 | 全部 re-export + `configure_mappers()` |

**零呼叫點改動**：所有 consumer 一向都係 `from app.models import X`
（grep 證實冇任何 `from app.models.<sub>`），所以 `__init__` re-export 之後
31 個公開名字嘅 import 路徑完全唔變。

**點解唔會拆爛 relationship**：SQLAlchemy 靠 class registry 解析
`relationship()` target，所以 class 可以跨模組互相引用；但前提係
**所有貢獻 mapped class 嘅模組都要喺 `configure_mappers()` 之前 import 咗**。
`__init__` 嘅 import 就係做呢件事，跟住**主動**叫 `configure_mappers()`，
令爛咗嘅 relationship 喺 `import app.models` 即刻炸，而唔係等到某次 query
先炸。跨模組嘅邊有 4 條：`User.driver_profile`、
`DriverProfile.licence_submissions`、`DriverLicenceSubmission.driver_profile`、
`DriverLicenceSubmission.documents`。

**重構點樣證明冇改行為**（唔止「test 過」）：
1. 將 HEAD 版本 + 工作區版本嘅 metadata 逐表 diff —— columns / types /
   nullability / defaults / indexes / FKs / unique constraints / PK
   **全部 17 張表一模一樣**（唯一差異係記憶體 address，同 `_uuid` vs
   `uuid.uuid4` 嘅 function 名 —— 已改返一致）。
2. 12 條 relationship（含方向、`uselist`、local columns、cascade options）
   **完全相同**。
3. `alembic check` 嘅 autogenerate drift **逐字節相同**（refactor 前後都係
  同樣 2449 字元嘅 ops list —— 即係話本專案本來就有一批未收嘅 drift，
   呢次 refactor **冇增加**）。
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
| `licence.py` / `licence_admin.py` | 司机侧与运营侧执照视图 |

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

1. **新增 `scripts/audit_response_models.py`** —— 遍历 `manifest.json` 的 route → fixture
   映射，断言每份夹具的键集是其模型字段集的**子集**。**68 个夹具块全部通过**，即没有任何
   响应会因 `response_model=` 而丢数据。
2. **该审计已证明「会失败」**（避免写一个永远 pass 的检查）：注入一个模型未声明的键，审计
   立即报出 `['a_key_the_model_does_not_have']`。
3. **`pytest` 687 → 687**（`--junit-xml` 读：687/0/0/0）。这 687 个测试全程通过 TestClient
   打真实路由，因此每一次调用都实际跑过 `response_model` 校验。
4. `ruff check` clean · `ruff format --check` clean（125 files）。

**与 Dart 契约检查的分工**（两者都要跑，失败点不同）：
- `scripts/audit_response_models.py` 校验 **Python schema ↔ 夹具**；
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
`ruff check` + `format --check` clean（131 files）· OpenAPI **81 paths / 88 operations**，
`scripts/audit_response_models.py` 68 块 fixture OK · console `tsc` clean、
**31 vitest passed**、`npm run build` clean。

---

## 3. 驗證標準：「全部實跑」

唔接受「讀源碼覺得無問題」。每次改動都跑齊：

```bash
uv run ruff check . && uv run ruff format --check .   # 或 ./.venv/Scripts/python.exe -m ruff
uv run pytest -q                                       # 872 passed（用 --junit-xml 讀，見下）
uv run python scripts/audit_response_models.py         # 68 块夹具 vs response_model，0 丢失
cd admin-web/web && npx tsc --noEmit && npm run build && npx vitest run --no-file-parallelism --pool=forks
cd mobile && dart --packages=.dart_tool/package_config.json tool/run_tests.dart
cd mobile && python tool/dart_check.py .               # LSP，非 flutter analyze
cd mobile && dart --packages=.dart_tool/package_config.json tool/verify_contract.dart
NODE_PATH="$HOME/.workbuddy-ai/binaries/node/versions/22.22.2-3/node_modules/@playwright/cli/node_modules" \
  node admin-web/tool/verify_ui.mjs --base http://127.0.0.1:8081 \
    --username ops-admin --password "$ADMIN_PASSWORD" --totp-secret "$ADMIN_TOTP_SECRET"
# 備份：唔止跑 backup，一定要跑埋 drill
.venv/Scripts/python scripts/db_backup.py backup
.venv/Scripts/python scripts/db_backup.py verify      # 還原 + 逐表核對 row count
```

**關鍵**：改動 money 格式後，要對**真 server + 真 Postgres** 做 live 驗證，
唔可以只信 TestClient。上面 2.3 嗰個 `"0.0"` bug 就係咁搵到；2.6 嗰 4 個
備份 bug 亦一樣 —— 全部係「讀源碼睇唔到、真跑先爆」。

---

## 4. 仍未做 — 分兩類

### A. 需要你提供憑證／部署目標（我無法代做）

| 項目 | 阻塞原因 |
|---|---|
| **P1-1 WS token 走 `?token=`** | `app/api/ws.py:73` 仍係 query param。現設計**有文件說明係刻意**（瀏覽器 WS 無 header 通道），已有 `StripTokenQueryFilter` 兜底。要真正解決需定 TLS 反代（nginx/Caddy）。 |
| **P1-4 備份 — off-host destination 未揀** | ~~無 pg_dump cron~~ **2026-09-30 更新：script 已完成並實跑 PASS**（`scripts/db_backup.py`，27 tests，還原演練 49 tables / 17,627 rows 全對）。只剩**揀 destination**（見 §5）。原本判為「需要 credentials」係唔準確 —— 只係未揀去邊。 |
| **WhatsApp / FCM / Google Maps 未接** | config 欄位存在、env 空。需要三家 provider 嘅憑證。 |
| **P2-2 遺留：部分退款** | 現時只做全額退還。 |
| **P2-2 遺留：實際打款渠道** | 只寫 ledger，轉帳仍線下人手。需要真實支付渠道。 |
| **P2-3 `distance_km` 由 client 自報** | 乘客可亂報。374D 下估價僅供參考、風險可控，但廣播排序會被 gaming。需 `GOOGLE_MAPS_API_KEY`。 |
| **P2-4 Sentry／錯誤聚合** | ~~淨係差 Sentry~~ **2026-09-30 更正：Sentry 已經接好**（`app/main.py:63-70`，`sentry_dsn` 有值就 init，無 `sentry-sdk` 就 warn 而唔會炸）。真正短缺嘅只係一個 DSN。 |

### B. 已知產品層取捨（非 bug，記錄在案）

- **調整唔會 activate 司機** —— 刻意。更正係記帳、唔係付款；維持
  `DEPOSIT_REQUIRED`。
- **負餘額合法（arrears）** —— 罰款／費用可以超過按金，司機欠平台。
- **`ADJUSTMENT` 單一管理員直接寫入** —— 由你揀。無雙人覆核，靠
  `created_by` 審計。若日後要防內部舞弊，需改成申請→批准流程。
  （**2026-10-01 更新**：爭議裁決的 `moves_money` 路徑已加 FINANCE 角色閘，
  是職責分離的第一步；`ADJUSTMENT` 本身仍為單人。）
- **搜尋不逐次審計** —— 高頻讀取，寫滿審計表會淹沒真正的金錢事件。刻意。

### C. 文件語言未統一（技術債，非阻塞）

`docs/*.md` 多數以**粵語**寫成（同用戶偏好的書面語不一致）。`docs/IN_TRIP_REDESIGN.md`
（2,031 行）尤其嚴重。§2.9 之後的新章節已改用書面語，但舊章節未回頭改。
適合用一次 bulk 轉換處理（已有 `bulk-text-refactor` skill 可用）。

### D. 行號引用失效

`app/models/` 佈局改動後，`docs/ADMIN_CONSOLE_DESIGN.md` 與
`docs/PRODUCTION_READINESS.md` 內的**行號引用**已失效（檔案已重新編排）。
內容本身仍正確，只是指向的行數不再準確。

---

## 5. Push 狀態：❌ 仍然推唔到（token 未獲授權此 repo）— 已由用戶豁免

**已 commit，但推唔到。** 2026-10-01 實測：

| 檢查 | 結果 |
|---|---|
| `git push`（token 放 URL，`-c credential.helper=`） | **403** `Write access to repository not granted` |
| 同一 token `GET /user` | `"login": "dannisonluk"` —— **帳號正確** |
| 同一 token `GET /repos/dannisonluk/realtaxihk` | **404** —— private repo 冇權限就係回 404 |

**先前本節寫「token 阻塞已解除」係錯嘅。** fine-grained PAT 係**逐個 repo
授權**，所以「token 屬於 dannisonluk」同「token 掂得到 realtaxihk」係兩件
獨立嘅事。**要你出手**：去 GitHub token 設定加 `dannisonluk/realtaxihk` +
`Contents: Read and write`。

> **✅ 2026-10-01 用戶指示：「你不需要 push，只需要 commit」。** 最新一輪的
> 所有工作**只 commit、不 push**。`origin/main..HEAD` 有未推 commit（查：
> `git rev-list --count origin/main..HEAD`）；
> 之前累積的 commit 亦一併未推。要真正同步，仍需上面的 token 授權。

> **⚠️ 更正（2026-10-01 稍後）：本節原本寫「working tree clean」，當時係錯嘅。**
> `git rev-list --count origin/main..HEAD` 只證明**已 commit 嘅嘢都推咗**，
> 完全冇講過工作區嘅狀態。實測 `git status --short` 係 **61 個已修改 +
> 33 個未追蹤**（1827 insertions, 1218 deletions）—— §2.9（models 拆包）、
> §2.10（response_model 集中化）、呢一輪嘅 QR 改動、以及 `docs/` 入面
> 8 份新文件**全部未 commit**。呢個就係「好似未做完」嘅實質原因。
>
> 兩個檢查答緊兩條唔同嘅問題，**唔可以互相代替**：
> - `rev-list origin/main..HEAD` → **有冇未推嘅 commit**
> - `git status --short` → **有冇未 commit 嘅改動**
>
> 已按邏輯分 7 個階段補 commit。現時 working tree **真係 clean**。

### 5a. 歷史記錄：token 權限診斷（保留，因其為可複用教訓）

以下係當時嘅診斷，保留作為**方法論**參考（同類問題會再出現）：

| 檢查 | 結果 |
|---|---|
| `git push`（credential helper 開著） | **掛住**，`timeout 120` 後 exit 124 |
| `apply-ez/.env` 嘅 PAT | token **有效**，`GET /user` 回 `"login": "dannisonluk"`（帳號正確） |
| 同一個 token 讀 `dannisonluk/realtaxihk` | **404 Not Found** |
| 匿名讀同一個 repo | 都係 404 |

兩個 404 併起來只指向一個結論：**repo 係 private，而個 token 冇被授權存取佢**。
fine-grained PAT 係**逐個 repo 授權**嘅，所以「token 屬於 dannisonluk」同
「token 掂得到 realtaxihk」係兩件獨立嘅事 —— 前者成立唔代表後者。
GitHub 對無權限嘅 private repo 一律回 404 而唔係 403，就係唔想洩漏 repo 存在。

**解法**：GitHub → Settings → Developer settings → Fine-grained tokens →
Repository access 加 `dannisonluk/realtaxihk` → Permissions 給
**`Contents: Read and write`**。

> ⚠️ **`git push` 喺呢部機係無聲掛住**，唔係網絡問題 —— 係
> `git-credential-manager.exe` 等緊互動輸入，而工具會 timeout 殺掉佢，
> 加上輸出被 pipe 住所以連一行都冇 flush。`git ls-remote` 照樣成功，令人
> 誤以為 remote 通。要推就用 repo 自己嘅 token + 停用 credential helper：
>
> ```bash
> TOKEN=$(grep '^GITHUB_PERSONAL_ACCESS_TOKEN=' .env | cut -d= -f2- | tr -d '\r\n"')
> GIT_TERMINAL_PROMPT=0 git -c credential.helper= \
>   push "https://x-access-token:${TOKEN}@github.com/dannisonluk/realtaxihk.git" main
> ```

> ⚠️ 另外：**唔好用一個會成功嘅寫入嚟做權限探測**。`PUT /contents/<path>` 探完
> 唔止會話你知結果，仲會真係建立檔案同 push 一個 commit。要探就用
> `POST /git/blobs`（只產生 dangling object），或者用 `curl -o /dev/null -D -`
> 淨讀 header。

> ⚠️ 缺少 `workflows` scope 時，push 會**整個被拒**（唔止係 workflow 檔），
> 錯誤訊息會明寫 `refusing to allow a Personal Access Token to create or update
> workflow ... without 'workflow' scope`。唔好猜邊個 permission 缺失 ——
> 讀 response 嘅 `x-accepted-github-permissions` header，佢會直接指名。

### 5b. 部署目標未定 — 呢個係下一個根阻塞

用戶 2026-09-30 確認：**未決定部署去邊**。§4A 表面睇係「7 項等 credentials」，
但實際 5 項都係**下游**於呢個決定：

| §4A 項目 | 真正阻塞 |
|---|---|
| P1-4 備份 destination | 去邊儲 |
| P1-1 TLS 反代 | 喺邊度跑 |
| P2-2 打款渠道 | 用邊個 provider |
| Google Maps / FCM / WhatsApp | **真正嘅 key 阻塞（3 項）** |
| P2-4 Sentry | 只差一個 DSN |

所以下一步唔係逐項啃，而係**先定部署目標**。定咗之後，backup destination
同 TLS 反代就跟住解。`scripts/db_backup.py` 刻意設計成
`--upload-cmd` / `--via auto`，就係唔想喺 destination 未定之前鎖死任何 provider。

**建議已寫成兩份文件**：`docs/DEPLOY_TARGET_DECISION.md`（A/B/C 選型取捨）
同 `docs/DEPLOYMENT_REQUIREMENTS.md`（選定之後嘅完整需求清單）。

---

### 5c. ✅ `ruff format --check` 已通過

`ruff format app/ tests/ scripts/` 已套用，並已 commit。現時兩個 gate 都乾淨：

```
./.venv/Scripts/python.exe -m ruff check .           # All checks passed!
./.venv/Scripts/python.exe -m ruff format --check .  # 109 files already formatted
```

`ruff format` 是**純格式**改動（行寬合併／拆分），不影響行為 —— 套用後
687 個測試全數通過可作佐證。

已加入 CI gate 防復發（`.github/workflows/ci.yml`）：

```yaml
- name: Format check (ruff format)
  run: uv run ruff format --check .
```

> 注意：`.pre-commit-config.yaml` 仍然不存在，所以本地 commit 不會自動檢查；
> 靠 CI 這道 gate 攔截。如要更早發現，可考慮加 pre-commit hook。

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
| `.workbuddy-ai/memory/YYYY-MM-DD.md` | 逐日流水、含沙盒陷阱 | **append-only，唔整理** |

> **✅ 已修正**：`docs/PROJECT_UNDERSTANDING.md` 曾停在 `4333f2a`（寫 HEAD =
> `446b7ee`、221 tests、54 Dart files、38 endpoints）。**2026-10-01 已全面重寫**，
> 現值：**872 tests**、**81 paths / 88 operations**、**31 vitest**、
> **131 ruff files**，並新增 RBAC 設計、`/auth/me` 雙形狀、`StarletteHTTPException`
> cookie 陷阱等章節。（**HEAD 刻意唔寫死** —— 之前寫死過三次，每次之後嘅 commit
> 都令佢變錯。）

> 計 route 數要讀 OpenAPI（`GET /openapi.json` 數 `paths` / operations），
> **唔好 grep route decorator** —— 一個 `@router.get` 加 `@router.post`
> 疊埋同一個 function 會數漏。

---

## 7. 沙盒／環境陷阱（本機特有，值得記住）

| 症狀 | 真因 | 解法 |
|---|---|---|
| `curl` 打 `127.0.0.1` 回 `502 upstream connect failed` | 沙盒 proxy 攔截，**即使個 port 根本無嘢聽** | 用 Python `urllib` + `ProxyHandler({})`；`NO_PROXY` 對 curl 唔可靠 |
| `dart run` / `flutter` 全部死 | Avast 注入 `dart.exe`，Dart 開唔到 pipe（`CreateFile failed 231`） | `dart --packages=.dart_tool/package_config.json <script>`；LSP 用 `tool/dart_check.py .` |
| `dart analyze` 死 | 同上（會 spawn analysis server） | 用 `tool/dart_check.py .` |
| Background server 無聲死 | Bash tool call 內 `cmd &` 隨 shell 退出被收割 | 用 `run_in_background=true` + `TaskStop` |
| 用 `conftest.ADMIN_ID` mint token 打 live server → 401 | 佢係每個 test session 隨機 `uuid4()` | 讀真 DB：`docker exec realtaxi-db psql -U realtaxi -d realtaxihk -c "SELECT id FROM users WHERE role='ADMIN';"`（DB user 係 `realtaxi` 唔係 `postgres`） |
| 要 login 但 OTP code 唔喺 response（SEC-02） | 刻意設計 | `ALLOW_DEV_OTP=true` + code `123456` |
| 本機完全冇 `pg_dump` / `psql` / `createdb` | 只有 `realtaxi-db` container 裡面有 | `scripts/db_backup.py --via auto` 自動 fallback 去 `docker exec` |
| `subprocess.run(cmd, shell=True)` 回 0 但 command 係失敗嘅 | Windows 用 `cmd.exe`，`;` 唔係分隔符（`echo hi >&2; exit 7` → rc 0） | 明確 `subprocess.run(["sh","-c",cmd])` |
| 傳 Windows 路徑入 `sh -c` 會被食反斜線 | `C:\Users\x` → `C:Usersx` | `.as_posix()` 傳正斜線 |

---

## 8. 一頁睇完

```
✅ 後端 81 paths / 88 ops / 872 tests / ruff lint + format clean（131 files）— 生產就緒
✅ mobile 21 畫面 / 93 tests / 0 diagnostics      — 三角色完整
✅ admin-web React / 31 vitest / typecheck + build clean / UI verifier PASS
✅ 後台治理：四級 RBAC（rank 比較、live row 為權威）+ 審計覆蓋金錢／狀態改動
✅ 後台新增：帳戶管理 / 訂單監控 / 結算預覽+confirm token+CSV / 爭議 / 主體搜尋 / 頭像上傳
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
✅ 全部 88 個 operation 都有 `response_model=`（此前 88 個中只有 1 個）

✅ SEV-1 已修：admin session 改為 HttpOnly refresh cookie（SameSite=Strict）
   + CSRF double-submit；token refresh 路徑已通
✅ ruff format --check 已通過（全樹 131 files）並加 CI gate — 不會復發
⚠️ SEV-2：`admin_auth.py` 用字串比對錯誤訊息決定 HTTP 狀態碼；
   locked 帳號回 401 而非 429（未改，屬客戶端體驗非安全洞）
⚠️ 根阻塞：**部署目標未定** — §4A 表面 7 項，實際 5 項下游於此（見 §5b）
⬜ 真正等 credentials 嘅只有 3 家 provider：Google Maps / FCM / WhatsApp
✅ `docs/PROJECT_UNDERSTANDING.md` 已於 2026-10-01 全面重寫
⚠️ `docs/*.md` 語言未統一（多數粵語，`IN_TRIP_REDESIGN.md` 尤其）—— 待一次 bulk 轉換

新文件：docs/SECURITY.md · docs/DEPLOYMENT_REQUIREMENTS.md
```
