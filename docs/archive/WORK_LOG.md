# 工作日誌 — 2026-09 至 2026-10（歷史文件）

> **這是歷史記錄，不是現行指引。** 由 `docs/WORK_SUMMARY.md` 第 2 節抽出，記錄
> 每一輪工作的問題、修法與證據（§2.1 … §2.21）。內容**刻意不更新** ——
> 裡面的測試數、行號、檔案佈局都反映當時狀態，改了等於偽造量測記錄。
>
> 要知道**今天**的狀態與仍未做項，讀 `docs/WORK_SUMMARY.md`。
>
> 文中出現的 `docs/XXX.md` 連結，若該文檔已歸檔，實際位置是
> `docs/archive/XXX.md`（見 `docs/archive/README.md`）。

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

### 2.16 正式部署 overlay + 連線池設定化（2026-10-02）

`deploy/README.md` 一直把 `docker-compose.prod.yml` 列為未完成，理由是「要先確認
VPS 與主機名」。這一輪把它補上，並一併處理 `REALTIME_POSITION_COST.md` §3.5 標記為
**正確性問題**（而非優化）的那一項。

**連線池的三個數字是同一個決定。** SQLAlchemy 的池是 **per-process** 建立的，所以
api 對 Postgres 的實際需求是 `(pool + overflow) × 行程數`。原本 `app/core/db.py`
把 `10 + 20` 寫死，而 §3.5 指出四個行程即 120 條 —— 對 `max_connections=100` 會爆，
且**只在正式環境的並發下才會出現**。三個值現已改由環境變數驅動
（`app/core/config.py` 的 `db_pool_size` / `db_max_overflow` /
`db_statement_cache_size`），並在 overlay 內明確寫成 `(10 + 20) × 1 = 30 ≤ 100`。

`DB_STATEMENT_CACHE_SIZE` 是為 PgBouncer 準備的：asyncpg 預設每條連線快取 100 條
prepared statement，而 transaction pooling 下同一筆交易的 PREPARE 與 EXECUTE 可能
落在**不同的伺服器連線**上，後者會以
`prepared statement "__asyncpg_stmt_N__" does not exist` 失敗 —— 看起來像資料庫故障。
它是 asyncpg 的 **connect 參數**，所以必須走 `connect_args`；當成 engine kwarg
不是被忽略，而是**直接 raise**（`Invalid argument(s) ... sent to create_engine()`）。

**測試套件看不到這個 bug，原因本身值得記錄**：`tests/conftest.py` 自建 `NullPool`
engine 打 per-test 資料庫，**從不呼叫 `app.core.db.get_engine()`** —— 應用程式自己的
池設定從來沒有被任何測試建構過。新增 `tests/test_db_pool_config.py`（3 tests），
以 spy 攔 `create_async_engine` 斷言實際傳入的 kwargs。

**`API_WORKERS` 預設為 1，這是正確性而非保守。** `ConnectionRegistry` 與速率限制
計數器都存在行程記憶體內，N 個行程會把 `WS_MAX_CONNECTIONS_TOTAL` 與各項速率上限
**各別執行 N 次**，而且不會有任何錯誤訊息。要加寬是三個步驟的組合，寫在
`deploy/README.md`。

**`docker-compose.prod.yml` 是 overlay，不是獨立檔。** 基礎檔承載全部安全加固
（SEC-06 的 loopback 綁定、SEC-19 的密碼必填、SEC-31 的 `--no-proxy-headers`），
獨立檔等於複製一份 —— 而下一次修安全問題時被漏掉的永遠是第二份。新增的服務是
nginx（掛 `deploy/nginx/realtaxihk.conf`）與 certbot（每 12 小時
`renew --webroot`）；nginx 容器內另有每 6 小時 `nginx -s reload` 的迴圈，因為兩個
容器之間沒有訊號通道，而 nginx 只在啟動或 reload 時讀取憑證。憑證必須在第一次
`up` **之前**存在，否則 nginx 會無限重啟 —— 首次簽發用 `--standalone`，步驟寫在
`deploy/README.md`。

**PgBouncer 刻意沒有附上 compose 檔。** 本環境起不到 Docker，設定無法驗證，而設定
錯的表現是**連線失敗**而非啟動錯誤 —— 那正是本專案反覆記錄的「看起來對、其實無聲
失效」。內容與四個驗證檢查點寫在 `deploy/README.md`。**連線池的正確性問題已由設定化
解決**，PgBouncer 現在是擴容手段，不再是上線阻礙。

**守衛**：`tests/test_prod_compose_pool_arithmetic.py`（4 tests）直接讀 compose 檔，
斷言算式成立、`--workers` 確實取自 `${API_WORKERS}`（否則算式守的是一個容器根本沒
在用的數字）、以及 `Settings` 的預設值對單行程安全。**已用突變測試證明**：把
`DB_MAX_OVERFLOW` 改成 90、以及把 `--workers` 寫死成 4，兩者都會讓對應測試變紅。
`tests/test_env_example.py` 亦擴大為同時掃描 overlay（`API_WORKERS` /
`PG_MAX_CONNECTIONS` 是 overlay 才引入的變數），並修好它自己的一個真缺陷：原本會把
**註解裡說明語法**的 `${VAR:-default}` 當成真的變數。

順帶修好一個會靜默漂移的地方：`scripts/verify/bench_location_pipeline.py` 原本把
`pool_size=10 + max_overflow=20` 寫死在輸出字串裡，改為讀 `Settings` —— 否則部署改了
池大小之後，這支基準腳本仍會拿一個過期的上限去對比實測負載。

**驗證**：`pytest` **887 → 894**（`--junit-xml` 讀：894/0/0/0）· `ruff check` clean ·
`ruff format --check` clean（137 files）· `npm run build` / `tsc` / `vitest` 未受影響。

---

### 2.17 全代碼與 UI 設計審查 + 兩個未上鎖的讀取（2026-10-02）

**後端與 Mobile 的深挖發現三個缺陷，全部是「讀源碼」而非「測試變紅」找到的**，
且事後都以突變測試證明修復有效（見 commit `be90c7e`）：

1. **WebSocket 握手佔住一條連線池連線，直到 socket 關閉。** FastAPI 在 handler
   **return** 時才拆解 dependency，而 WebSocket 的 return 是 socket 關閉 —— 可能是
   數小時。`Depends(get_session)` 因此每個開著的 socket 都釘住一條池連線。
   測試套件**結構上看不到**：`tests/conftest.py` 用 `NullPool` 覆寫
   `get_session` 與 `get_session_factory`，沒有上限可以撞。已改為握手在自己的
   scoped session 內完成。
2. **兩次並行的取消可以重複收 HK$50 罰款。** `order_cancel` 先讀、斷言狀態轉換、
   寫 ledger、最後才寫狀態。兩個請求都讀到 `ACCEPTED`、都通過守衛、都寫了一筆。
   `_get_order` 新增 `for_update=True`（cancel/arrive/start/complete 皆傳），
   讓讀取與守衛原子化：後到者阻塞在列鎖上、重讀到 `CANCELLED`、在碰到 ledger
   之前就被拒絕。
3. **Mobile 用 double 相減計算金額。** 司機押金差額在 `500.00 - 499.70` 時顯示為
   `HK$0.30000000000001137`。`Money.minus` / `Money.fromCents` 改以整數分運算。

**UI 設計審查**（Apple HIG，逐頁讀過後引用，對比度以 token 的十六進位值實算）
寫成 `docs/UI_DESIGN_REVIEW_2026-10-02.md`。**無 Critical，四項 High**：

- `color-scheme: dark` 被無條件掛在 `:root[data-theme="system"]` 上，而
  `@media (prefers-color-scheme: dark)` 只覆寫自訂屬性、**沒有重宣告
  `color-scheme`**。於是「淺色系統 + 預設主題 `system`」（＝最多人會遇到的組合）
  會用淺色調色盤配深色原生控件：捲軸、`<select>` 彈出層、checkbox 全部變深色。
  `tool/check_theme_tokens.py` 只比對 `--*` 自訂屬性，看不到這一條。
- `<input type="date">`（2 處）與 `type="email"`（1 處）**不在** `styles.css:613-618`
  的選擇器清單內，因此完全沒有欄位樣式，卻與已套樣式的 `<select>` 並排。
- 深色主題的 `--brand` 未調整（兩邊都是 `#d2232a`），作為文字時對 `--surface-2`
  只有 **2.91:1**、對 `--surface` 3.31:1、側欄選中項 3.08:1（門檻 4.5:1）。
- 淺色主題四種語意 chip 為 **4.17–4.40:1**（`.chip` 的底色是 currentColor 的 12%
  疊在白底上，把底色推向文字色，必然降低對比）。

另有 8 項 Medium（分段控制的選中狀態只靠 1.06:1 / 1.25:1 的色差、三個紅色色值
幾乎相同卻各代表一個意思、`.gain`/`.loss` 是死碼且與 `ENTRY_TONE` 的實作相反、
24 小時資料沒有無障礙替代表示、當成按鈕的 `.chip` 只有約 26px 等）與 5 項 Low。

**報告同時記錄了「做得好的地方」** —— 這部分同樣具體：主題是三值而非布林、
`--seg-active` 是獨立 token、地圖下方有等價表格、`generated_at` 與
`last_location_at` 兩個時鐘分開、標記顏色寫在 CSS 讓主題切換零 JS 重繪、
`.heat` 的 24 格不換行。

**驗證**：`pytest` **894 → 897**（`--junit-xml` 讀：897/0/0/0）· `ruff check` clean ·
`ruff format --check` clean（137 files）· mobile `run_tests.dart` 97 passed /
0 failed · `dart_check.py` 58 files / 0 diagnostics。

---

### 2.18 UI 設計審查的修復：4 High + 8 Medium + 5 Low（2026-10-02）

`docs/UI_DESIGN_REVIEW_2026-10-02.md` 是一份**日期快照**，不回寫。本節記錄它的
程式碼側發現如何被修好，以及修的時候又找到什麼。兩次 commit：`827f439`（token 與
對比）＋ `90b9d2e`（元件與文案）。

#### 對比與主題（`827f439`）

四項 High 全部源於「沒有任何守衛在量」，所以修法分兩半：改值，然後加守衛。

- **H-1 `color-scheme`**：原本無條件掛在 `:root[data-theme="system"]` 上，而
  `@media (prefers-color-scheme: dark)` 只覆寫自訂屬性。改為跟隨與 token 相同的
  兩個條件（`:root` 宣告 `light`、`[data-theme="dark"]` 區塊內宣告 `dark`、
  media query 內的 `[data-theme="system"]` 宣告 `dark`）。
- **H-3 品牌紅兼兩職**：`--brand` 同時要當「白字的底」與「深色底上的文字」是做不到
  的 —— 白字在 `--brand` 上是 5.23:1（主要按鈕靠它），同一個紅當文字在
  `--surface-2` 上只有 2.91:1。拆出 `--brand-text`（淺色 `#d2232a`、深色
  `#ff7b72`），側欄選中項／`.chip--brand`／排序表頭 hover 都改用它。
- **H-4 chip 底色**：`color-mix(in srgb, currentColor N%, transparent)` 是把文字色
  **混進**底色，N 越大底色越靠向文字、對比越低。12% → 6%，最差由 4.17:1 升到
  4.73:1；`--warn`/`--danger` 亦調為 `#8f5f00`/`#a40e26`（後者原本與 `--brand`
  只差 1.02:1，等於同一個紅兩個名字）。
- **H-2 欄位選擇器**：原本列舉 5 個 `input[type=...]`，漏了 `date` 與 `email`。
  改成排除法，新的 type 預設繼承欄位樣式。
- **M-1 分段控制**：選中狀態不能靠填色（淺色軌道上的白色填色只有 1.06:1；深色軌道上
  整個灰階在文字開始不合格前最高約 2.3:1），改由新的 `--seg-edge` 承載
  （淺 4.32:1、深 3.31:1）。
- 另：刪 `.gain`/`.loss` 與 `--gain`（M-3 —— 兩個 class 在 `src/` 出現 0 次，而註解
  描述的是**股價**慣例，實際帳務色來自 `labels.ts#ENTRY_TONE`，儲值是綠色）；
  加 `prefers-contrast: more`（L-3）；地圖圖例色塊補上與標記相同的 opacity（L-4）；
  刪死規則 `.pref__label`（L-2）；`--focus` 不再兼任「等待中」的車輛色（L-5）。

**新增守衛 `admin-web/web/tool/check_contrast.py`**：讀 `src/styles.css`（不需
build），檢查五件事 —— 每個文字 token 對每個 surface、每個 chip tone 對**它自己
混出來的**底色、非文字指示 3:1、`--brand-ink` 對 `--brand`，以及三個主題路徑的
`color-scheme`。`tests/test_console_contrast.py` 跑它，並以**突變測試證明它不是
空轉**：把四項 High 各自還原一次，斷言它會失敗。

守衛自己也被抓到兩個 bug，都是「一個不會失敗的守衛比沒有守衛更糟」：①它只匹配
`--*` 自訂屬性，所以 `color-scheme` 的斷言**無論 CSS 怎麼寫都會失敗**；②它沒有先
移除註解，而 `:root` 區塊的註解寫著 `* Values are GitHub Primer's light scale: ...`，
當中的冒號令它被讀成一條宣告，並把後面的 `--brand-text` 與 `--danger` 一起吃掉。
兩者現在都有回歸測試。

#### 元件與文案（`90b9d2e`）

- **M-4**：兩個圖表都是 `role="img"` —— 長條圖的提示框掛在不可聚焦的 `<rect>` 的
  `onMouseEnter` 上，熱力圖的 `title` 則因為 `role="img"` 的子節點是 presentational
  而不進無障礙樹；下方的明細表是**另一個資料集**（day/week/month 分桶）。所以在圖表
  下加一個 `<details>`，承載 24 小時的表格（時段／日均／訂單／有營運日數）。
- **M-5，而且不只在地圖頁**：`.chip` 是**標籤**樣式（約 26px）。審查只在
  `LiveMapPage` 找到 2 處當按鈕用；實際量度所有互動控件後又找到 12 處 ——
  `OrdersPage` 十個狀態篩選只有 **21px**、`DisputesPage` 五個、`AuditPage` 與
  `FleetsPage` 各一。全部加上 `.chip--action`（34px），純顯示的 chip 不動。
- **M-6**：儀表板三張可點卡片原本用行內 `style` 去掉底線，而 `.stat` 完全沒有
  `:hover`，所以可點與不可點長得一模一樣。`.stat--link` 掛在 `<Link>`（grid item）
  上，而 hover 與 focus ring **必須往內傳給 `.stat`** —— 只改外層等於沒改。
- **M-7**：`viewBox` + `width: 100%` 會連**文字**一起縮放 —— 10 單位的軸標籤在
  1080px 卡片上是 10px，在 436px 卡片上只有 **6.1px**。`.chart` 改為橫向捲動容器、
  svg 以自身 `viewBox` 寬度為下限，於是圖表不再縮，改為橫向捲動。
- **M-8**：`、` 與 `。` 是**語言**而非裝飾 —— 英文介面原本會輸出
  `Google Authenticator、Microsoft Authenticator`。改為 `common.listSeparator` /
  `common.sentenceEnd`。另外兩處審查沒提到的全形括號裡，`KycPage` 是**真 bug**：
  它在車牌之後就閉括號，把車型留在外面，渲染成 `車牌 AB1234）市區的士`。
- 六處行內 `fontSize: 12` 改為 `t-caption1`（L-1）；刪 `notAnAdminDialog()` 與
  **兩個**（不只一個）未被引用的 `common.createdAt`/`createdDate`（L-5）；
  `dashboard.sub` 不再寫 "Live"（該頁是一次性讀取、沒有輪詢）。

#### 佈局稽核器補上它本來就該量的東西

`admin-web/tool/audit_layout.mjs` 有兩個缺口讓以上全部通過，還有一個讓它自己的
招牌數字不成立：

- **`#/live` 從來不在 `ROUTES` 裡** —— 所以「52 renders clean」實際只有 48 次，而
  唯一渲染第三方控件的頁面正好是唯一沒被量的頁面。已加入；它帶出的兩項 Leaflet
  發現以理由豁免（Leaflet 刻意把 pane 畫得比容器大再裁切，那正是拖曳的實作方式；
  真正要守的「頁面本身不橫向溢出」仍然在檢查，而且仍然成立）。
- **`.sr-only` 被判為「被裁切的文字」** —— 它**定義上**就是 1px + `overflow: hidden`，
  所以每一個視覺隱藏標題都觸發這條檢查，等於在舉報唯一一個為了螢幕閱讀器而存在的
  樣式。已加入白名單。
- **新增檢查 6：SVG 文字被縮到小於原本字級。** `getComputedStyle` 看不到 `viewBox`
  的縮放，所以改為由渲染寬度反推縮放係數再手動套用。把 `min-width` 還原之後，它
  **複現出審查的原始數字**：10px → 6px（×0.6），每次渲染 18 個標籤。
- **新增檢查 7：互動控件小於 28px。** 原有的目標尺寸檢查只抓「變大」的控件，沒有
  任何東西抓「變小」的。這一條找出了上面那 12 個 chip —— 以及 `.th-sort`：它的註解
  自稱「填滿整個儲存格」，實際只有 18px 高，而儲存格是 34px。

**驗證**：`audit_layout` **52 renders clean @1440px 與 @500px**（後者正是審查量到
6.1px 的那個寬度）· `pytest` **897 → 909**（`--junit-xml` 讀：909/0/0/0；其後再加
14 條 enum／migration 守衛測試，現為 928）·
`ruff check` + `format --check` clean · console `tsc` clean · `vitest` **69 passed
（9 files）** · `tool/check_contrast.py` 與 `tool/check_theme_tokens.py` 皆 OK。

> **測試斷言被改窄，不是改弱**：`AnalyticsPage.test.tsx` 有兩條原本對整頁
> `querySelectorAll('tbody tr')` 斷言，在加入 24 行的無障礙表格後會拿到 26 行，
> 而其中一行合法地渲染 `HK$0.00`（該測試原本斷言整頁不得出現這個字串）。改為只取
> **明細表**的列（以 `closest('details')` 過濾），斷言的內容一條都沒少。

### 2.19 全倉再掃描：enum CHECK 約束上鎖 + 文檔數字校正（2026-10-12）

一輪「還有沒有漏」的獨立掃描，產出兩件事。

**（a）P0-M-2：20 個 enum 欄位補上 DB 層 CHECK。** 詳見
`docs/CODE_REVIEW_2026-10-12.md`。核心：`SAEnum(X, native_enum=False)` 的
`create_constraint` **預設為 False**，所以 19 個 enum 形狀欄位在 DB 層只是
`VARCHAR` —— 應用層驗證被繞過時（直接寫入、資料修復、舊應用版本）可以存入
任何字串，而 SQLAlchemy 讀回時拋的是 `LookupError` 而非 `ValueError`，
`except ValueError` 的容錯全部接不住，該列之後每次讀取都 500。
修法：8 個 model、20 欄一律 `create_constraint=True` 並統一命名為
`ck_<table>_<column>`；`dispute.py` 的 5 個原本只是 `String(N)` 的欄位一併綁
`SAEnum` 並用 `length=` 釘住原寬度（否則會產生收窄的 `ALTER TYPE`）。
新增 migration `2e276a320b35`（+17 條 CHECK，2 條既有除外），附 pre-flight
guard：遷移前先查有無超範圍列，有就 raise 具名 `RuntimeError` 而不是讓
`ALTER TABLE` 硬失敗。新增兩個守衛測試：
`tests/test_enum_check_constraints.py`（metadata 層，4 條）與
`tests/test_migration_schema_parity.py`（**全套件唯一真正跑 migration 的測試**，
3 條）。測試 **909 → 928**。

**（b）文檔測試數校正。** `README.md`（3 處，其中一處寫著 `# 120 tests`，
比同頁的 909 落後 808 條）、`docs/PROJECT_UNDERSTANDING.md`（2 處，
外加 `alembic heads` 由 `a1c4e8b7f209` 更正為 `2e276a320b35`）、
`docs/UI_DESIGN_REVIEW_2026-10-02.md`（1 處）、本文件（7 處）全部由 909 改為
**928**。`docs/CODE_REVIEW_2026-10-12.md` 內的 **921** *刻意不改* —— 那是帶日期
的審查快照，921 是當時的真實讀數（該輪漏收了 7 條新測試），改它等於偽造記錄。
同一原則見 §4D。

> **教訓（值得記住）**：背景 `pytest` 執行期間新增測試檔，該輪**不會**收集到
> 它們。當時回報「921 綠」而 XML 裡 7 條新測試一條都沒有，但總數看起來正常。
> 判斷依據必須是 **grep XML 內的測試名**，不是總數。

### 2.20 收尾：10 個分批 commit + 清掉 P2-F-6/7/8、P2-11（2026-10-12）

**（a）分批 commit。** 之前 57 個 working-tree 改動**一個 commit 都沒有**。
`git rev-list --count origin/main..HEAD` 當時回 **0** —— 這**不代表「都推了」**，
而是代表 HEAD 等於 origin/main，即工作區全部未 commit。兩個檢查回答兩條不同的
問題，不能互相代替（見 §5 更正）：

| 指令 | 回答 |
|---|---|
| `git rev-list --count origin/main..HEAD` | 有沒有**未推的 commit** |
| `git status --short` | 有沒有**未 commit 的改動** |

已按邏輯分成 **10 個 commit**（`c75e8b9` → `549ae7e`），每個獨立可審：

```
c75e8b9 fix(security): one client_ip(), not five copies of the SEC-07 fix
96e979b fix(config): fail closed on two prod settings that silently do nothing
04cf881 fix(settlement): a lost fee could be reported as a clean skip
e1bcf92 fix(models): 20 enum columns had no CHECK constraint (P0-M-2)
4d37253 fix(admin-web): four console defects the review found
7165858 fix(mobile): settlement confirm token, stale balance, id-drift note
9b0c29a fix(alembic): migrations could not run on a Postgres without PostGIS
f3aff72 feat(deploy): TLS terminator, and the pool arithmetic made explicit
2bf256e docs: the 2026-10-12 review, and 14 stale test counts
6ed1ca8 refactor(scripts): the repo root is computed once, not fifteen times (P2-11)
549ae7e fix(mobile): the three product-judgement items from the review (P2-F-6/7/8)
```

**（b）P2-11 —— repo root 只算一次。** 15 份 `parent.parent.parent` 的副本
（散在 14 個檔案、3 種寫法）收斂成 `scripts/_root.py`。**chicken-and-egg 仍然
存在**（`_root` 要 `scripts/` 先上 `sys.path` 才 import 得到），但它現在只活在
**一個地方**而不是十五個。`scripts/__init__.py` 不存在 → 加這個模組**不改變
pytest 收集**。新增 `tests/test_scripts_root.py`（26 tests）守兩件事：任何腳本
重新自算 depth（3 種拼法都抓）就 fail；任何被另一個腳本按名字引用的腳本不存在
也 fail。

**（c）P2-F-6/7/8 —— 原本寫「留給產品判斷」，後來決定一併做。**
車牌格式**提示**（不是驗證器 —— 香港 Custom Registration Marks 是任意字串，
硬擋會拒真車牌）、`state.extra` 型別收窄後退回 `LoginScreen`、
`AdminKycScreen` dispose 順序加註釋。

**（d）順手抓到 2 個 CI 看不見的既有 bug。** 兩個安全 harness
（`security_verify.py` / `security_probe.py`）自 §2.13 目錄重整後就**開不了機** ——
路徑仍指 `scripts/create_admin.py`，實際已移到 `scripts/ops/`。**兩者都不在 CI
裡跑**，所以沒人發現。

> **教訓**：只有 boot 整支 app（`prod_boot_drill.py`）或 spawn 子進程的 harness，
> 才會被「新增一個必填 prod 設定」搞死，而它們**不在 CI**。新增必填設定後要手動
> 跑一次。這次就靠它的 `_PROD_OK` 單一 dict 設計**大聲失敗**（而不是靜靜少測）。

---
