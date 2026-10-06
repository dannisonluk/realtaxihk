# 項目結構評估 / Structure Review

> **EN — Structure review.** An assessment of the repo's directory layout, with
> each finding ranked by value vs. risk. The one structural gap that matters more
> than any directory is at the bottom (§4).
>
> **中文摘要**：對 repo 目錄佈局的評估，每項按「價值 vs 風險」排序。比任何目錄
> 問題都更重要的那一個結構盲點，放在 §4。

**日期**：2026-10-03 · **方法**：實際讀取目錄與檔案行數，非推測。

---

## 1. 已經很好的部分（不要動）

| 位置 | 為什麼好 |
|---|---|
| `scripts/{ops,verify,dev}` | 按「你在做什麼」分組，規則寫在 `scripts/README.md`；`scripts/_root.py` 把 repo root 的推算集中在一處，由 `tests/infra/test_scripts_root.py` 守住。**這是全 repo 最值得複製的模式。** |
| `app/models/` | 按 bounded context 拆包（`_base` / `user` / `admin` / `fleet` / `licence` / `dispute`），公開 import 路徑不變（`__init__` re-export）。 |
| `app/api/schemas/` | 響應模型獨立於 handler，且由夹具反推、有審計腳本守住。 |
| `deploy/` + `docker-compose.prod.yml` | prod 是 **overlay** 而非獨立檔，基礎檔承載全部加固。 |
| `docs/` | 本回合已重整：11 份現行文檔 + `archive/` 放帶日期的歷史快照（見 `docs/README.md`）。 |

---

## 2. 建議（按價值／風險排序）

| # | 項目 | 價值 | 風險 | 狀態 |
|---|---|---|---|---|
| **R1** | 拆 `app/api/admin.py`（1,983 行） | 高 | 中 | ✅ 已完成 |
| **R2** | `tests/` 由 39 個平鋪檔分組 | 高 | 低 | ✅ 已完成 |
| **R3** | mobile 測試搬入 `test/`，用 `flutter test` | 中低 | 中 | ⛔ **未做** —— 見下方說明 |
| **R4** | 加 `app/api/router.py` 集中掛載 | 中 | 低 | ✅ 已完成 |
| **R5** | `admin-web/` legacy 三件搬入 `legacy/` | 中 | 低中 | ✅ 已完成 |
| **R6** | `app/services/` 27 個平鋪檔按 context 分組 | 中 | 中 | ✅ 已完成 |
| **R7** | `service_area` 兩份同名 | 低中 | 低 | ✅ 已完成 |
| **R8** | `DEPLOY_TARGET_DECISION.md` 待主機名定案後歸檔 | 低 | 低 | ⏸ 待主機名 |
| **R9** | CI 沒有任何型別檢查器 | 高 | 低 | ✅ 已完成（並抓出一個真 bug） |

### R1 — `app/api/admin.py` 是全 repo 最大的檔案 ✅ 已處理

**1,983 行**，佔 `app/api/` 的 38%、整個後端的 10%。其餘 admin 模組早已拆出
（`admin_auth.py` 440 · `admin_licence.py` 156 · `admin_analytics.py` 132），
所以它是一個**未完成的拆分**，不是刻意的設計。

**已拆成 `app/api/admin/` 套件**（12 個檔案）：`drivers.py` · `settlement.py` ·
`refunds.py` · `audit.py` · `accounts.py` · `orders.py` · `disputes.py` ·
`search.py` · `live.py`，加上 `_roles.py`（角色閘門與政策註釋）、`_shared.py`
（跨資源 helper：`_ledger_out` / `_refund_out` / `_actor_username`）、
`__init__.py`（只做 router 聚合，保留原 prefix、tags 與註冊順序）。

**驗收結果**：

1. `app.openapi()` 拆前拆後**完全相同**（82 paths / 89 operations，JSON 逐鍵比對）。
2. 27 個 route 裝飾器一個不少；58 個頂層陳述經 AST 比對**零缺失**。
3. `ruff check` / `ruff format --check` clean。

> **過程中踩到的坑（值得記住）**：`ast.FunctionDef.lineno` 指向 `def` 那一行，
> **不包含 `@decorator`**。第一版抽取腳本因此產生了一個「27 條路由、0 個
> `@router.`」的套件 —— 而且我當時的保真檢查用了**同一個錯誤假設**，所以報
> 「0 缺失」。**用同一個假設寫的檢查，驗不出基於該假設的 bug。**
> 修法是 `min(node.lineno, node.decorator_list[0].lineno)`，而驗證改成
> **獨立的 AST dump 比對 + route 裝飾器計數**。

> 先做這一項再做 R4／R6，因為它們都會動到 import。

### R2 — `tests/` 39 個檔案平鋪 ✅ 已處理

**已分為 3 組**，分類用**客觀準則**而不是憑感覺 —— 「是否使用 `client` fixture」
（即是否驅動 HTTP 介面）：

| group | 檔數 | 準則 |
|---|---|---|
| `api/` | 24 | 使用 `client` fixture，驅動真實 HTTP 介面 |
| `domain/` | 6 | 純邏輯，無 DB：fare、money、hk_bounds、passwords、totp |
| `infra/` | 9 | 對檔案與配置的守衛：migration parity、db pool、backup、compose 算式、`scripts/` root、`.env.example`、console 對比、money 註解 |

**注意（與 `scripts/` 同一個陷阱）**：8 個測試用 `__file__` 推算 repo root
（`Path(__file__).resolve().parent.parent`）。**每加一層目錄，這些全部要改**，
而且**改錯是靜默的** —— 路徑會指到 `tests/` 而不是 repo root，測試照樣收集、
照樣執行，只是掃錯目錄。

- 已把 7 個檔案的深度加一層（`parent.parent` → `.parent.parent.parent`、
  `parents[1]` → `parents[2]`）。
- **`test_scripts_root.py` 刻意手動處理**：它有一個 `__file__` 出現在**f-string
  訊息內**，示範「腳本應該怎樣寫」。那裡的 `parent.parent` 是**正確的**
  （腳本在 `scripts/<group>/x.py`，比測試淺一層），自動替換會把它改壞。
- `conftest.py` 留在 `tests/` 根層；子目錄**不加** `__init__.py`（39 個檔名唯一，
  rootdir 模式不會撞名）。

驗證：`pytest --junit-xml` 讀出仍是 **960 passed / 0 failed**；ruff clean。

### R3 — mobile 的測試不在 `test/` ⛔ 未做（刻意）

`mobile/test/` 只有 `fixtures/`；97 條斷言住在手寫 harness
`mobile/tool/run_tests.dart`，靠 `dart --packages=… tool/run_tests.dart` 執行。

**先更正我在 §2 給的價值評級。** 我原本寫「中高」，前提是「CI 跑不到這些斷言」——
**這是錯的**。CI 的 `mobile` job 已經在跑 `dart --packages=… tool/run_tests.dart`
與 `verify_contract.dart`，所以**沒有任何覆蓋缺口**。R3 真正的價值只有兩個：
刪掉一套自製 harness（美觀）、以及讓日後可以寫 widget test。

**不做，因為在本機無法驗證。** 這一節原本的風險描述寫得對，但低估了程度：

- `flutter test` 需要 Flutter tool，而它一啟動就跑 `git log` 取版本新鮮度 →
  spawn `git.exe` → 實測**崩潰**（`ProcessException … OS error code: 231`，
  即 `ERROR_PIPE_BUSY`）；`flutter --version` 同樣崩潰。
- `dart analyze` 也在 `runDartdev` 失敗（同一根因）。
- `package:test` **根本沒有安裝**（`pubspec.lock` 只有 `flutter_test` 與
  `test_api`），而加它要跑 `pub get` —— 一樣跑不到。

所以要嘛寫出**無法執行、無法解析**的檔案，要嘛違反本專案「全部實跑」的規則。
**兩者都不接受**，所以這一項留給有可用 Flutter SDK 的機器。

**移植時要守住的量**（本回合實測，作為驗收基線）：

| 檔案 | 現況 |
|---|---|
| `mobile/tool/run_tests.dart` | **見 `dart tool/run_tests.dart` 即時輸出**（會隨新測試變動，唔好寫死數字） |
| `mobile/tool/verify_contract.dart` | **見即時輸出**（fixture 數量會隨 `manifest.json` 增長；基線係 56+ fixtures，0 failure） |

harness 自己的 docstring 已經寫明它是「遷移清單」；10 個 `_xTests()` 函式就是
10 個目標檔案的骨架。`dart_check.py` 要保留 —— 它是本機唯一能做的靜態檢查。

### R4 — 路由掛載集中在 `main.py` ✅ 已處理

原本 16 個 router 在 `app/main.py` 逐個 `include_router`，路由表只能靠在腦內重組
那 20 行。**已加 `app/api/router.py`**：一個 `api_router` 聚合全部，`main.py` 只
`include_router` 一次，而**註冊順序的註釋也搬過去**（三個 admin router 必須排在
平台 `admin_router` 之前，否則 `/admin/auth/*`、`/admin/licence/*`、
`/admin/analytics/*` 會被它吞掉）。路由表現在可以在一處審閱。

**驗證**：`app.openapi()` 完全相同；並特別驗了
`tests/api/test_security_hardening.py` 的 `_iter_api_routes` —— 它遞迴走訪路由樹，
仍然找到 **89 條 APIRoute、51 條非公開 `/api/v1` 路由、0 個缺 guard**，
`checked >= 15` 的下限守衛也成立。這一項必須驗，因為 R4 把路由樹的嵌套由
**1 層變成 2 層**，而該走訪器是整個測試套件唯一依賴路由樹形狀的地方。

### R5 — `admin-web/` 混著兩代 console ✅ 已處理

`admin-web/` 頂層原本同時有：legacy 三件（`index.html` · `js/` · `styles.css`）、
React build（`web/`）、驗證腳本（`tool/`）、`serve.py`、`README.md`。

**已把 legacy 三件搬入 `admin-web/legacy/`**，`admin-web/` 頂層現在只餘
`README.md` · `serve.py` · `legacy/` · `tool/` · `web/`。

- `serve.py` 的 `SERVE_ROOT` 預設改為 `ROOT / "web" / "dist"`（React build）；
  `--legacy` 才指向 `legacy/`。已直接載入模組驗證三個檔案都在新位置。
- `verify_ui.mjs` 的拒絕訊息由 `admin-web/js` 改為 `admin-web/legacy`。
  它的偵測邏輯不受影響 —— 它讀的是**已服務的 HTML**（legacy 仍然載入
  `/js/app.js`，Vite build 載入 `/assets/`），不是磁碟路徑。

### R6 — `app/services/` 27 個平鋪模組 ✅ 已處理

`app/models/` 已按 context 拆包，`app/services/` 沒有。**已同構分組為 7 個
bounded context**，對齊文檔已定義的模組（A 認證／B 派單／C 帳本／E 車隊／F 後台），
而不是我臨時發明一套分類：

| group | 模組數 | 內容 |
|---|---|---|
| `auth/` | 4 | OTP、refresh、phone re-verify、identity |
| `licence/` | 3 | 文件提交／審核、object storage |
| `order/` | 6 | 訂單、搶單、狀態機、行程、fare engine、geo |
| `ledger/` | 4 | 帳本、結算 job、confirm token、退款 |
| `fleet/` | 1 | 車隊（持牌營運商） |
| `admin/` | 7 | 後台帳戶／認證／審計／爭議／分析／搜尋 |
| `infra/` | 2 | 通知、背景任務 |

**這是本清單改動面最廣的一項**：151 個引用、64 個檔案（含 `docs/` 與 CI 設定）。

**原建議有一處寫錯了，在此更正。** 我原本說「先加 `__init__.py` re-export 再搬，
就享有零呼叫點改動」—— **不成立**。`app/models/` 能做到零改動，是因為 consumer
本來就用 `from app.models import X`；services 的 consumer 用的是**深層路徑**
（`from app.services.ledger_service import LedgerService`），re-export 層幫不上忙，
舊路徑照樣斷。所以改法是**直接改寫全部引用**。

**也刻意沒有為每個 group 加 re-export**：那會令每個 group 的 `__init__` 成為互相
import 的樞紐，製造 import cycle —— 比路徑長一點更糟。`app/models/` 能安全
re-export，是因為 model 模組只 import `_base`。

**驗證**：`app.openapi()` **剝掉 `description` 後完全相同**（只有 4 條 description
有變，逐條檢查過，全部是 docstring 內被更新的服務路徑文字）；960 tests 全過。

### R7 — `service_area` 兩份同名 ✅ 已處理

`app/api/service_area.py`（70 行，endpoint）與 `app/core/service_area.py`
（59 行，強制執行的閘門）**是刻意不同的兩層**，docstring 也講清楚了 ——
但同名會令 grep 與閱讀容易搞混。

**已改名為 `app/api/service_area_route.py`**（唯一外部引用是 `app/main.py`）。
`app.openapi()` 前後完全一致。

### R9 — CI 沒有任何型別檢查器 ✅ 已處理（並抓出一個真 bug）

原本 CI 只跑 `ruff check` + `ruff format --check` + `pytest`。
**Ruff 不做型別推論**，所以參數註解與函式體不一致時，沒有任何 gate 會紅。

**已加第四個 job `types`**（`uv run mypy`，設定在 `pyproject.toml` 的
`[tool.mypy]`），gate 整個 `app/`。

**我原本的判斷是錯的，在此更正。** 我預期 SQLAlchemy 的 `Mapped[]` 與 FastAPI 的
`Depends()` 在無 plugin 下會產生幾百條噪音，所以要分階段。實際量測：整個 `app/`
只有 **26 個**錯誤 —— SQLAlchemy 2.0 與 FastAPI 都自帶 typing，不需要 plugin。
所以一次 gate 全樹，沒有分階段。

**這 26 個錯誤裡有一個是真 bug**（其餘是註解缺口或 mypy 的泛型限制）：

> **`app/api/orders.py` 的 keyset 游標比較錯了一整年。**
> `ORDER BY created_at DESC, id DESC` 是複合排序，游標卻寫成 Python 的 tuple 比較
> `(Order.created_at, Order.id) < (anchor[0], anchor[1])`。
> Python 的 tuple `<` 會**先測相等**，而 `bool(Order.created_at == ts)` 對
> SQLAlchemy 欄位回 **`False`（不拋錯）**，於是直接落到第二步的
> `created_at < ts` —— **`id` tie-breaker 從未進入 SQL**（實測：產生的 SQL 就是
> `orders.created_at < :created_at_1`）。
>
> 這在兩張單同一時間戳時就會出錯，而那不是假設：`created_at` 是
> `server_default=func.now()`，Postgres 的 `now()` 是**交易時間戳**，
> 同一交易寫入的列完全相同。後果是**分頁靜默丟單**。
>
> 修法：`tuple_(Order.created_at, Order.id) < tuple_(anchor[0], anchor[1])`。
> 新增 `test_history_keyset_survives_a_shared_timestamp` —— 它**先被證實會失敗**
> 才修（用 `exec_sql` 強制兩張單同一時間戳，修正前第二頁回空）。
> 原有的 `test_history_keyset_pagination` 用了 `first or second` 的弱斷言，
> 所以蓋不住。

其餘 25 個分三類：**註解與實作不符**（`same_site_policy() -> str`、
`_as_aware` 的 `datetime | None`、`claimed` 的 dict key）、**forward reference**
（`app/models/` 兩個跨模組 relationship，改用 `TYPE_CHECKING` import，並移除已
多餘的 `# noqa: F821`）、以及 **mypy 的泛型限制**（Core DML 的
`Result.rowcount` 要用 `cast("CursorResult[Any]", …)`；`HTTPException.headers`
要容納 `set-cookie` 的值清單，Starlette 宣告為 `Mapping[str, str]`）。

**順帶修好一個既有的不一致**：`uv lock --check` 本來就失敗（lock 與
`pyproject.toml` 不同步）。加入 mypy 後 re-lock，`tzdata` 也一併補上，
`uv lock --check` 現在通過。re-lock 是**純新增**，沒有改動任何既有套件版本。

**覆核時補上的第二個意見**：跑 `pyright`（就是 Pylance 用的引擎）時，它在 mypy
全綠的情況下**另外報了 3 個**：

- `app/core/logging.py` —— `scrub_tokens` 沒有註解，推斷出的返回型別含 `str`，
  而 `LogRecord.args` 只接受 `tuple` 或 `Mapping`。
- `app/services/auth/phone_reverify_service.py` 與
  `app/services/licence/licence_review_service.py` —— 都是「對
  `Mapped[datetime | None]` 屬性賦值後再讀回來」，pyright 報可能的 None 解引用。

根因是 pyright 對 SQLAlchemy 描述符屬性的**賦值後窄化不保留**（`reveal_type`
實測：賦值之後它仍認為是 `datetime | None`）；綁到區域變數即可。三個都已修，
現在 **mypy 與 pyright 都是 0 errors**。

> **教訓**：我第一次做最小重現時用了**普通類別**，結果重現不出來 —— 重現必須用
> 真的 `Base` 子類，否則驗的是另一件事。另外，`pyright` CLI 沒有
> `pyrightconfig.json` 時會回報 182 條 `reportMissingImports`，那是找不到 venv，
> 不是程式碼問題；該檔**刻意沒有加入 repo**（本專案的型別 gate 是 mypy，
> 再放一份 pyright 設定就是第二個真相來源），需要交叉檢查時臨時建立即可。

> **`tests/` 與 `scripts/` 不在任何型別 gate 之內。** mypy 的 `files = ["app"]`
> 是刻意的；`pyright` 對那兩棵樹另有既有 finding —— 實測 **`tests/` 140 個、
> `scripts/` 6 個**（pytest fixture 的 generator 註解、動態屬性賦值、`_root` 的
> sys.path import 等），全部早於本次改動，`app/` 的 0 不受影響。
> 把它們也納入 gate 是另一件工作，不在這 8 項之內。

---

## 3. 根目錄整潔

本回合已處理：

- 刪掉三個 0-byte 垃圾檔（`been` · `omit` · `**EN`）與 `flutter_01.log` ——
  移入 `.tmp/session-junk/`（`.tmp/` 已 gitignore）。
- `.VSCodeCounter/`（VS Code 擴充功能的產生目錄）已加入 `.gitignore`。

現時根目錄只餘 15 個項目，全部是應該在那裡的。

---

## 4. 比目錄更重要的那一個結構盲點

**`tests/conftest.py` 用 `Base.metadata.create_all` 建 schema，從不跑
migration。** 所以「migration 跑不跑得到」「migration 建的 schema 與 model
是否一致」這兩個問題，**只有一個測試問過**
（`tests/infra/test_migration_schema_parity.py`）。

後果不是理論性的：本專案已經因此出過兩次真問題 ——
`alembic upgrade head` 對乾淨 Postgres 必定失敗（缺 `CREATE EXTENSION postgis`），
以及 19 個 enum 欄位有 17 個在資料庫層**完全沒有 CHECK 約束**（寫入靜默、
讀取才 raise）。

**建議**：把「跑真 migration 再比對 schema」納入 CI（該 parity test 已在
`tests/` 內，只要確認 CI 有 PostGIS service container 且真的跑到它），
並在 `docs/DEVELOPMENT.md` 的驗證清單標明**加 migration 後必須手動跑一次
`alembic upgrade head` 對空 DB**。

> 相關細節與陷阱（`create_constraint` 預設、`LookupError` 而非 `ValueError`、
> `alembic check` 必定 FAIL 的正確讀法）見 `docs/DEVELOPMENT.md` 與
> `.workbuddy-ai/memory/MEMORY-backend.md`。
