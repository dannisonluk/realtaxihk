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
| `scripts/{ops,verify,dev}` | 按「你在做什麼」分組，規則寫在 `scripts/README.md`；`scripts/_root.py` 把 repo root 的推算集中在一處，由 `tests/test_scripts_root.py` 守住。**這是全 repo 最值得複製的模式。** |
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

### R1 — `app/api/admin.py` 是全 repo 最大的檔案

**1,983 行**，佔 `app/api/` 的 38%、整個後端的 10%。其餘 admin 模組早已拆出
（`admin_auth.py` 440 · `admin_licence.py` 156 · `admin_analytics.py` 132），
所以它是一個**未完成的拆分**，不是刻意的設計。

建議拆成 `app/api/admin/` 套件：`drivers.py` · `refunds.py` · `settlement.py` ·
`fleets.py` · `disputes.py` · `accounts.py` · `orders.py` · `search.py` ·
`live.py` · `audit.py`，`__init__.py` 只做 router 聚合。

**驗收條件（硬性）**：

1. `GET /openapi.json` 的 **82 paths / 89 operations 完全不變** —— 拆檔前後各
   導出一份 JSON 做 diff。
2. `tests/test_security_hardening.py` 靠 `dependency.call.__name__` 找 guard，
   所以 `require_role_guard` 等名稱**不可改**。
3. `tests/test_api_and_security.py` 的 route-table 審計要照樣通過。

> 先做這一項再做 R4／R6，因為它們都會動到 import。

### R2 — `tests/` 39 個檔案平鋪

`tests/` 目前 39 個 `.py` 平鋪。建議按領域分組（鏡像 `app/`）：

```
tests/
  conftest.py            # 必須留在這一層（session 層守衛）
  api/                   # test_admin_*.py, test_auth_module.py, test_orders_module.py, test_fleets.py…
  services/              # test_fare_*, test_deposits_module.py, test_refund_and_settlement.py…
  core/                  # test_money_wire.py, test_hk_bounds.py, test_totp.py, test_passwords.py…
  infra/                 # test_migration_schema_parity.py, test_db_pool_config.py, test_db_backup.py,
                         # test_prod_compose_pool_arithmetic.py, test_scripts_root.py, test_env_example.py
```

**注意**：`tests/` 現在**沒有** `__init__.py`（正確）。加子目錄後若兩個子目錄
有同名檔案會撞 module 名 —— 現時沒有同名，但**要加 `__init__.py` 或保持檔名唯一**。
驗收：`pytest --collect-only -q` 的數目**必須仍是 960**。

### R3 — mobile 的測試不在 `test/`

`mobile/test/` 只有 `fixtures/`；97 條斷言住在手寫 harness
`mobile/tool/run_tests.dart`。這是**沙盒限制造成的繞路**（本機跑不到
`flutter test`），但 **CI 是 Linux，跑得到**。

搬成 `mobile/test/*_test.dart`（`package:flutter_test`）之後：CI 可以用
`flutter test` 取代 `dart --packages=… tool/run_tests.dart`，
`verify_contract.dart`（54 fixtures）同理。價值是**刪掉一套自製 harness**。

**風險**：要逐條移植 97 條斷言並確認數目不變；`dart_check.py` 仍要保留（本機
唯一能做的靜態檢查）。

### R4 — 路由掛載集中在 `main.py`

現在 16 個 router 在 `app/main.py` 逐個 `include_router`。加一個
`app/api/router.py` 只做聚合，`main.py` 只 import 一次 —— 好處是**路由表可以
在一處審閱**（route-table 審計的對象也集中）。低風險，與 R1 一起做。

### R5 — `admin-web/` 混著兩代 console

`admin-web/` 頂層同時有：legacy 三件（`index.html` · `js/` · `styles.css`）、
React build（`web/`）、驗證腳本（`tool/`）、`serve.py`、`README.md`。
建議 legacy 三件搬入 `admin-web/legacy/`，並更新 `serve.py` 的兩個服務路徑。
注意 `verify_ui.mjs` **明文拒絕** legacy build，所以搬動不會影響 UI 驗證。

### R6 — `app/services/` 27 個平鋪模組

`app/models/` 已按 context 拆包，`app/services/` 沒有。建議同構分組
（`auth/` · `order/` · `ledger/` · `fleet/` · `licence/` · `admin/`）。

**與 `app/models/` 的關鍵差別**：models 有 `__init__` re-export，所以拆包**零
呼叫點改動**；services **沒有**這層聚合，所以每個 `from app.services.x import y`
都要改。這是本清單中改動面最廣的一項 —— 要做的話，先加一層
`app/services/__init__.py` re-export 再搬，才享有同樣的零改動。

### R7 — `service_area` 兩份同名

`app/api/service_area.py`（70 行，endpoint）與 `app/core/service_area.py`
（59 行，強制執行的閘門）**是刻意不同的兩層**，docstring 也講清楚了 ——
但同名會令 grep 與閱讀容易搞混。建議把 route 那份改名為
`app/api/service_area_route.py`。低風險。

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
（`tests/test_migration_schema_parity.py`）。

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
