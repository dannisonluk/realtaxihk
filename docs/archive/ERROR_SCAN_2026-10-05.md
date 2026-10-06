# 全專案錯誤掃描 — 2026-10-05（更新版）

掃描方式：實跑每一個 gate（ruff / mypy / compileall / tsc / dart analyze / alembic heads /
vitest / pytest），非靜態猜測。本更新版取代同日早段版本：Docker Desktop 已經開起，
`db`（15433）/ `redis`（16379）healthy，pytest 已經可以實跑，唔再係「環境阻塞」。

> **2026-10-06 收斂註記**：本檔列出的未修項**全部已結案** ——
> - **E-2 / E-3**（mobile `fixed_offers_screen.dart` 的 `$` 未 escape +
>   `use_build_context_synchronously`）：已隨 WIP 收斂修好。實跑
>   `python mobile/tool/dart_check.py mobile` = **80 files opened / 0 diagnostics**。
> - **§5 雜物**：12 個掃描殘留 `.txt`（`run_1`…`run_10`、`nearby_5x`、
>   `pytest_verify_ae87a05`）已刪；36 個 vite timestamp `.mjs` 早已被 `.gitignore`
>   覆蓋（`admin-web/web/vite.config.ts.timestamp-*.mjs`）。
> - **§6.3 geo key namespace**：**已修**。`geo:orders:active` 已改為
>   `geo_orders_key()` = `f"{settings.redis_key_namespace}geo:orders:active"`
>   （`app/services/order/geo_service.py`），`grab_service` / `maintenance` 兩個
>   消費點一併改用共用函式；並行 pytest 不再互相污染。
> - **pytest 狀態更正**：一次過 `pytest tests` 全套**已可穩定完成** ——
>   **1157 passed / 0 failed / 0 error / 0 skipped**（2026-10-06，junit `.tmp/full2.xml`）。
> - **新增發現：`dart format` 排版閘係紅嘅（已修）。** 本檔當日只跑了
>   `dart harness` / `verify_contract` / `dart_check`，**冇跑排版閘**。2026-10-06
>   補跑 CI 的 `dart format --line-length 100 --output=none --set-exit-if-changed lib tool`：
>   `HEAD`／`origin/main` **19 檔唔過**（工作區連 WIP 24 檔）。即該閘自 `ae97e61`
>   加落之後再度漂移，CI 從未真正執行過。已用同 CI 一致嘅 SDK（Flutter 3.44.0 /
>   Dart 3.12.0）重排全樹，逐檔核對非空白內容零改動；現況 **0 changed**。
> - **§6.1 ERROR_SCAN 歸檔**：仍留在 `docs/`（與 `AUDIT_REPORT_2026-10-04.md` 同類：
>   帶日期但在 `docs/`）；是否遷入 `archive/` 仍待 owner 決定。
>
> 本檔其餘內容**保持原樣** —— 它是當日量測記錄，不追現況。

## 0. 現時狀態一覽

| Gate | 結果 | 證據 |
|---|---|---|
| `ruff check app tests scripts` | ✅ `All checks passed!` | 實跑 |
| `ruff format --check` | Only 1 個檔案需格式化 | `scripts/dev/serve_and_run_browser.py`（sibling WIP，未碰） |
| `mypy app` | ✅ 0 issues（124 files） | 實跑 |
| `python -m compileall app` | ✅ rc=0 | 實跑 |
| `alembic heads` | ✅ 單一 head `c1f2e3d4a5b6`（19 migrations） | 實跑 |
| `tsc --noEmit`（admin-web） | ✅ exit 0 | 實跑 |
| `vitest run`（admin-web） | ✅ **79 passed / 11 files** | 實跑 |
| `dart harness` | ✅ **149 passed / 0 failed** | 實跑 |
| `verify_contract.dart` | ✅ **61 fixtures / 0 failure**（共 63 個 fixture json） | 實跑 |
| pytest `tests/api` | ✅ **778 passed / 0 failed** | 實跑（junit `.tmp/api_only.xml`） |
| pytest `tests/domain`+`tests/infra` | ✅ **371 passed / 0 failed** | 實跑（junit `.tmp/domain_infra.xml`） |
| pytest 全套（一次過 `pytest tests`） | ⚠️ 非完成狀態：本日試兩次，56% / 43% 中途中止，無 junit | 背景實跑，中斷 |

> ⚠️ **唔好同時開兩個 pytest process。** `nearby` 那批測試與 Redis GEO index
> （`geo:orders:active`）共用狀態，而該 key 沒有 per-process namespace。並行跑會
> 互相污染出「假 failed」（實測並行時 4 failed；單獨跑 `tests/api` 778/778、`nearby`
> 整檔 18/18、pair 24/24 全綠）。

## 1. 已修正（早段版本列為錯誤，現已唔存在）

### E-1（已修）admin-web TypeScript 編譯失敗
- 原錯誤：`TS18048: 'first' is possibly 'undefined'` / `'last' is possibly 'undefined'`
  喺 `admin-web/web/src/components/primitives.tsx:198` 及 `:201`。
- 現況：`npx tsc --noEmit` **exit 0**（admin-web），Build 已恢復。
- 來源：原先係未 commit 的 focus-trap WIP，sibling agent 已收斂成單一實作。

### 3 個 Ruff lint（已修）
- 原列表：`scripts/verify/audit_response_models.py:35`（I001）、
  `scripts/dev/gen_mobile_fixtures.py:166`（I001）、
  `admin-web/serve.py:297`（E501）。
- 現況：`ruff check .` = **All checks passed!**；`serve.py:297` 屬 sibling WIP，
  未有令 `ruff check` 報錯。

## 2. 仍然存在嘅問題（未修正，因屬 sibling 未 commit WIP）

### E-2 mobile Dart 語法／常數錯誤（blocker，未 commit）
- 檔案：`mobile/lib/features/driver/fixed_offers_screen.dart:157`
- 錯誤：`missing_identifier` + `invalid_constant`
- 原因：字串 `'司機實收（HK$）'` 入面 `$` 未 escape，Dart 當 `$）` 係插值。
- 修法：`'司機實收（HK\$）'`，或 raw string。
- 影響：呢個 files 編譯唔到，mobile build 會斷。
- **狀態：該檔案係其他 agent 未 commit WIP，未有指令前唔會掂。**

### E-3（Warning，同一檔案）`use_build_context_synchronously`
- 檔案：`mobile/lib/features/driver/fixed_offers_screen.dart:151`
- 問題：`await` 之後即刻用 `context`，無 `if (!mounted) return;`。
- 修法：`await showModalBottomSheet` 之後補 `if (!mounted) return;`。
- **狀態：同上，未 commit WIP，未有指令前唔會掂。**

## 3. 已驗證乾淨（實跑，非假設）

- `python -m compileall app` → rc=0
- `python -m mypy app` → `Success: no issues found in 124 source files`
- `alembic heads` → **單一 head** `c1f2e3d4a5b6`；`alembic history` 係一條完整直鏈，
  無 orphan / 無分叉。
- `ruff check app tests scripts` → `All checks passed!`
- admin-web `tsc --noEmit` → exit 0
- admin-web `vitest run --no-file-parallelism --pool=forks` → **79 passed / 11 files**
- mobile harness → **149 passed / 0 failed**；`verify_contract.dart` → **61 fixtures / 0 failure**
- pytest `tests/api` → **778 passed / 0 failed**（`.tmp/api_only.xml`）
- pytest `tests/domain` + `tests/infra` → **371 passed / 0 failed**（`.tmp/domain_infra.xml`）
- pytest `tests/api/test_nearby_filters.py` → **18 passed / 0 failed**（`.tmp/nearby_whole.txt`）
- pytest pair `test_fixed_fare_offers.py + test_nearby_filters.py` → **24 passed / 0 failed**
  （`.tmp/pair_run.txt`）

## 4. pytest 環境阻塞之更正

早段版本寫「Docker Desktop 未啟動、15433 / 16379 無 listener、1060 個測試零覆蓋」，
呢個狀態已經唔成立：

- `docker compose up -d db redis` 已啟動，`realtaxi-db` / `realtaxi-redis` healthy。
- pytest 可以實跑：`tests/api` **778/778**（`.tmp/api_only.xml`）、
  `tests/domain`+`tests/infra` **371/371**（`.tmp/domain_infra.xml`）、
  `nearby` 整檔 **18/18**（`.tmp/nearby_whole.txt`）、pair **24/24**（`.tmp/pair_run.txt`）。
- 一次過 `pytest tests` clean run 喺本日試咗兩次（56% / 43%）都中途中止、無 junit，
  因此**唔宣稱單一 full-suite 全綠**；778+371 係兩個子套件各自全綠。

## 5. 掃描期間順手發現的雜物（非錯誤，但污染 repo）

`git status` 的 untracked 檔有大量掃描殘留物，應該清走或加落 `.gitignore`：

- repo 根目錄：`alembic_heads.txt`、`alembic_heads2.txt`、`alembic_history.txt`、
  `alembic_history2.txt`、`alembic_versions.txt`、`f06_count.txt`、`fixtures_files.txt`、
  `gitdiffstat.txt`、`gitdiffstat2.txt`、`gitlog.txt`、`gitstatus.txt`、`gitstatus2.txt`、
  `mid.diff.txt`、`middleware.diff.txt`、`ruff_check.txt`、`ruff_check2.txt`、`ruff_check3.txt`
  （共 **17 個**）
- `admin-web/web/`：**36 個** `vite.config.ts.timestamp-*.mjs` 殘留檔（vite config 熱重載產物）
- **另外**：`alembic_heads.txt` 內容寫「兩個 head」，係舊快照，已過時，勿當真。

## 6. 待決事項（未處理）

1. **ERROR_SCAN 歸檔**：按 `docs/README.md` charter，審計／掃描報告應放 `docs/archive/`
   並用帶日期檔名；此檔目前係現行 `docs/` 內嘅參考文件，是否遷入 archive 待 owner 決定。
2. **雜物清理策略**：17 個根目錄 `.txt` ＋ 40 個 vite timestamp `.mjs`，要直接刪定加 `.gitignore` 未定。
3. **geo key namespace 修補**：`tests/conftest.py` 已為 rate-limit key 加 per-process namespace，
  但 `geo:orders:active` 未加；修法係將 GEO 相關 key 都納入 `REDIS_KEY_NAMESPACE`，
  令並行 pytest / CI 唔會互相污染。屬 test-isolation 缺口，未改。