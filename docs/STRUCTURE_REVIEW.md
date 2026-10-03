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

| # | 項目 | 價值 | 風險 | 依賴 |
|---|---|---|---|---|
| **R1** | 拆 `app/api/admin.py`（1,983 行） | 高 | 中 | — |
| **R2** | `tests/` 由 39 個平鋪檔分組 | 高 | 低 | — |
| **R3** | mobile 測試搬入 `test/`，用 `flutter test` | 中高 | 中 | — |
| **R4** | 加 `app/api/router.py` 集中掛載 | 中 | 低 | R1 |
| **R5** | `admin-web/` legacy 三件搬入 `legacy/` | 中 | 低中 | — |
| **R6** | `app/services/` 27 個平鋪檔按 context 分組 | 中 | 中 | R1 |
| **R7** | `service_area` 兩份同名 | 低中 | 低 | — |
| **R8** | `DEPLOY_TARGET_DECISION.md` 待主機名定案後歸檔 | 低 | 低 | 主機名 |
| **R9** | **CI 沒有任何型別檢查器** | 高 | 低 | — |

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

### R3 — mobile 的測試不在 `test/`

`mobile/test/` 只有 `fixtures/`；97 條斷言住在手寫 harness
`mobile/tool/run_tests.dart`。這是**沙盒限制造成的繞路**（本機跑不到
`flutter test`），但 **CI 是 Linux，跑得到**。

搬成 `mobile/test/*_test.dart`（`package:flutter_test`）之後：CI 可以用
`flutter test` 取代 `dart --packages=… tool/run_tests.dart`，
`verify_contract.dart`（54 fixtures）同理。價值是**刪掉一套自製 harness**。

**風險**：要逐條移植 97 條斷言並確認數目不變；`dart_check.py` 仍要保留（本機
唯一能做的靜態檢查）。

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

- `serve.py` 的 `SERVE_ROOT` 預設改為 `ROOT / "legacy"`（`--dist` 仍然指向
  `web/dist` 不變）。已直接載入模組驗證三個檔案都在新位置。
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

### R9 — CI 沒有任何型別檢查器（本回合發現的實際代價）

CI 的三個 job 只跑 `ruff check` + `ruff format --check` + `pytest`。
**Ruff 不做型別推論**，所以參數註解與函式體不一致時，沒有任何 gate 會紅。

這不是理論風險 —— 本回合就抓到一個活案例：`app/core/money.py` 的四個格式化
函式參數註解為 `Decimal`，但函式體一直是 `Decimal(v)`（docstring 也明說接受
int 與 str）。`money_str(settings.weekly_fee_hkd)`（`weekly_fee_hkd: int`）在
編輯器報錯、CI 全綠，而且已經存在了一段時間。

**建議**：把 `pyright`（或 `mypy`）加進 CI，但**不要一次對全樹開嚴格模式** ——
SQLAlchemy 2.0 的 `Mapped[]` 與 FastAPI 的 `Depends()` 在無 plugin 的情況下
噪音很大，一次開全樹會得到幾百條無關錯誤，然後整條 gate 會被 ignore 掉。

分階段做法：

1. 先用**寬鬆模式**只 gate `app/core/`（純邏輯、無 ORM 泛型），零噪音。
2. 再擴到 `app/services/`，逐個模組收。
3. `app/api/` 與 `app/models/` 最後處理，並視需要引入
   `sqlalchemy2-stubs` / `SQLAlchemy` 官方 typing plugin。
4. 每一步都把「目前錯誤數」寫進 CI 的 `--baseline` 或註解，只擋**新增**的。

> 若不想引入第二套工具鏈，退一步的做法是把 `MoneyInput` 這類聯集型別當成
> 專案慣例寫進 `DEVELOPMENT.md`，讓 code review 時人手看 —— 但那需要有人真的看。

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
