# `docs/` — 文檔索引

> **EN — Docs index.** Seventeen living documents plus an `archive/` of dated
> snapshots. Start with `ARCHITECTURE.md` if you want to understand the system,
> `DEVELOPMENT.md` if you are about to change it, and `WORK_SUMMARY.md` if you
> need to know what is still outstanding.
>
> **中文摘要**：17 份現行文檔 + 一個放歷史快照的 `archive/`。要**理解系統**讀
> `ARCHITECTURE.md`；要**動手改**讀 `DEVELOPMENT.md`；要知道**還欠什麼**讀
> `WORK_SUMMARY.md`。

---

## 從哪裡開始

| 你是誰 | 依序讀 |
|---|---|
| **第一次接觸這個 repo** | [`../README.md`](../README.md) → [`ARCHITECTURE.md`](ARCHITECTURE.md) → [`WORK_SUMMARY.md`](WORK_SUMMARY.md) §4 |
| **要改後端／前端代碼** | [`DEVELOPMENT.md`](DEVELOPMENT.md)（規範 + 怪癖 + 方法論）→ [`ARCHITECTURE.md`](ARCHITECTURE.md) §10 不變式清單 |
| **要上線／運維** | [`DEPLOYMENT_REQUIREMENTS.md`](DEPLOYMENT_REQUIREMENTS.md) → [`DEPLOY_TARGET_DECISION.md`](DEPLOY_TARGET_DECISION.md) → [`../deploy/README.md`](../deploy/README.md) |
| **做安全審視** | [`SECURITY.md`](SECURITY.md) → [`archive/SECURITY_AUDIT.md`](archive/SECURITY_AUDIT.md) |
| **做 QA／測試** | [`QA_TEST_ENVIRONMENT.md`](QA_TEST_ENVIRONMENT.md) |
| **接手未完成的產品工作** | [`WORK_SUMMARY.md`](WORK_SUMMARY.md) §4 → [`FEATURE_EXPANSION_2026-10-05.md`](FEATURE_EXPANSION_2026-10-05.md) |
| **接手未修的審計項** | [`AUDIT_FINDINGS_LINEBYLINE.md`](AUDIT_FINDINGS_LINEBYLINE.md)（**全項目 open items 的權威清單**） |
| **想知道現在有什麼壞掉** | [`ERROR_SCAN_2026-10-05.md`](ERROR_SCAN_2026-10-05.md) |

---

## 現行文檔

| 文檔 | 讀它來了解 | 性質 |
|---|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | **起點。** 一程車由叫車到收費的完整流程、錢在哪裡被改動、**23 條不變式清單**（每條都曾經是 bug）、值得學的教訓 | 導讀 |
| [`DEVELOPMENT.md`](DEVELOPMENT.md) | 分層規範（後端／Flutter／React）、**12 條非顯而易見的後端行為怪癖**、lint gate、環境限制、方法論教訓 | 指南 |
| [`WORK_SUMMARY.md`](WORK_SUMMARY.md) | 已建了什麼、實跑驗證了什麼、**還有什麼未做與為什麼**（§4 分憑證阻塞／刻意取捨） | 索引 |
| [`SECURITY.md`](SECURITY.md) | 安全模型、已驗證的控制、SEV 分級發現、加固路線圖 | 報告 |
| [`ADMIN_AUTH.md`](ADMIN_AUTH.md) | 管理員認證模型、三步登入狀態機、authenticator 選型、已知缺口 | 設計 |
| [`ADMIN_CONSOLE_DESIGN.md`](ADMIN_CONSOLE_DESIGN.md) | 後台九大模組的設計（帳戶、訂單、結算、爭議、RBAC、分析、稽核、風控、工單）＋落地順序＋明確不建議做的事 | 設計 |
| [`IN_TRIP_REDESIGN.md`](IN_TRIP_REDESIGN.md) | 未實作的 in-trip + 預約重設計：新狀態機、到達雙重驗證、違約扣款、schema、API、前端流程、$5 平台費（7 個 DECISION 已拍板） | 提案 |
| [`DEPLOYMENT_REQUIREMENTS.md`](DEPLOYMENT_REQUIREMENTS.md) | 部署的硬約束、執行環境、**完整環境變數清單**、步驟、上線前 gap list | 清單 |
| [`DEPLOY_TARGET_DECISION.md`](DEPLOY_TARGET_DECISION.md) | 部署選型 A/B/C 的取捨與成本，以及選定後的執行順序 | 決策 |
| [`QA_TEST_ENVIRONMENT.md`](QA_TEST_ENVIRONMENT.md) | 測試環境交接：**四個必改的環境變數**、OTP 怎麼拿（**不會**出現在回應裡）、管理員怎麼建、三個客戶端各連哪個位址、**手機 App 首次登入的兩道牆**（§6）、12 條實際卡過的陷阱 | 清單 |
| [`LANDMARK_COORDINATES.md`](LANDMARK_COORDINATES.md) | 19 個地標落客座標（供人手覆核）＋深圳灣口岸港方口岸區的完整幾何分析與法律依據 | 參考資料 |
| [`REALTIME_POSITION_COST.md`](REALTIME_POSITION_COST.md) | 一個 GPS tick 的成本實測、不同並發下的開銷、擴展天花板、5 項按投報率排序的優化 | 分析 |
| [`STRUCTURE_REVIEW.md`](STRUCTURE_REVIEW.md) | 目錄佈局的評估：已很好的部分、8 項按價值／風險排序的建議、以及比目錄更重要的那個結構盲點 | 評估 |
| [`AUDIT_FINDINGS_LINEBYLINE.md`](AUDIT_FINDINGS_LINEBYLINE.md) | **逐行審計的 findings log（活文件）**：每個發現的編號、證據（檔案:行號）、修復狀態。要查「還有哪項未修」以這裡為權威 | 審計 |
| [`AUDIT_REPORT_2026-10-04.md`](AUDIT_REPORT_2026-10-04.md) | 企業級全量審計報告 v4（修復輪）：分輪發現與處置（帶日期，見下方說明） | 報告 |
| [`FEATURE_EXPANSION_2026-10-05.md`](FEATURE_EXPANSION_2026-10-05.md) | 下一波產品功能的整合設計 backlog（取代舊的分散提案） | 提案 |
| [`ERROR_SCAN_2026-10-05.md`](ERROR_SCAN_2026-10-05.md) | **實跑式全專案錯誤掃描**：每個 gate 的真實輸出、已驗證乾淨項、以及哪些 gate 因環境未跑（帶日期） | 報告 |

> **「現行」與「帶日期」的界線**：`AUDIT_FINDINGS_LINEBYLINE.md` 是**活文件**——
> 條目狀態會隨修復進度更新，是 open items 的權威。
> `AUDIT_REPORT_2026-10-04.md` 與 `ERROR_SCAN_2026-10-05.md` 帶日期、記的是
> **當天量到什麼**（含當天的 HEAD），按第 3 條守則**不追現況**；要今天的狀態讀
> `WORK_SUMMARY.md`。

## 相關文檔（不在 `docs/`）

| 文檔 | 內容 |
|---|---|
| [`../README.md`](../README.md) | 專案入口：產品定位、三件交付物、快速上手、API 一覽、配置、部署與運維 |
| [`../scripts/README.md`](../scripts/README.md) | `scripts/` 的 `ops/` · `verify/` · `dev/` 分組規則與逐個腳本說明 |
| [`../mobile/README.md`](../mobile/README.md) | Flutter 客戶端：建置、contract 驗證、要 work around 的後端行為、已知缺口 |
| [`../admin-web/README.md`](../admin-web/README.md) | 管理後台：React 與 legacy 兩版、真瀏覽器驗證、雙語版面審計、代碼守則 |
| [`../deploy/README.md`](../deploy/README.md) | 正式部署（選項 A）：nginx、憑證簽發與續期、三個必須對齊的代理設定、連線池算式 |

---

## `archive/` — 歷史快照，**不更新**

這些是**帶日期的量測記錄**。它們的價值在於「當時真的量到什麼」，
所以裡面的測試數、行號、檔案佈局**刻意保持原樣** —— 改了等於偽造量測記錄。
要知道今天的狀態，讀 [`WORK_SUMMARY.md`](WORK_SUMMARY.md)。

| 文檔 | 是什麼 | 日期 |
|---|---|---|
| [`archive/WORK_LOG.md`](archive/WORK_LOG.md) | 逐輪工作日誌（問題 → 修法 → 證據，§2.1…§2.21） | 2026-09 ~ 10 |
| [`archive/SECURITY_AUDIT.md`](archive/SECURITY_AUDIT.md) | 安全審計：SEC-01..31 逐條、風險總表、含攻擊重現證據 | 2026-09-29 |
| [`archive/PRODUCTION_READINESS.md`](archive/PRODUCTION_READINESS.md) | 上線就緒審計：4 bug / 7 P0 / 10 P1 / 10 P2 逐條狀態與修復記錄 | 2026-09-29 |
| [`archive/CODE_REVIEW_2026-10-01.md`](archive/CODE_REVIEW_2026-10-01.md) | 深度程式碼審查（6 高 / 4 中 / 6 註釋級） | 2026-10-01 |
| [`archive/CODE_REVIEW_2026-10-12.md`](archive/CODE_REVIEW_2026-10-12.md) | 全代碼庫逐行審閱（P0 / P1 / P2 + migrations + 前端），含已撤銷的誤報 | 2026-10-12 |
| [`archive/UI_DESIGN_REVIEW_2026-10-02.md`](archive/UI_DESIGN_REVIEW_2026-10-02.md) | 管理後台 UI 設計審查（Apple HIG、對比度、無障礙） | 2026-10-02 |

> 詳見 [`archive/README.md`](archive/README.md)。

---

## 文檔守則

1. **一律用書面語（繁體）**，不用粵語。英文技術術語照留。
2. **不要寫死 HEAD hash** —— 寫死過三次，每次之後的 commit 都令它變錯。
   要查用 `git log --oneline -1`。
3. **「現值」vs「有日期快照」是判斷標準，不是「數字舊不舊」。**
   現行文檔的測試數、路徑數要跟現況；帶日期的快照**刻意不改**。
4. **`docs/archive/` 只進不出**：新的審計／審查報告用帶日期的檔名，直接寫進
   `archive/`，不要混進現行文檔。**唯一的例外**是上表下方註解點名的那兩份帶日期
   報告（`AUDIT_REPORT_2026-10-04.md`、`ERROR_SCAN_2026-10-05.md`）：它們刻意留在
   `docs/`，因為索引要讓人找得到「當天量到什麼」。除此之外的新報告一律入
   `archive/`。
5. **改了架構就同步**：`ARCHITECTURE.md`、`DEVELOPMENT.md`、`WORK_SUMMARY.md`
   三者是最容易漂移的，改動後要一起看。
6. **計數器只寫「實跑量到」的數字，並講明用什麼方法量。**
   寫一個沒跑過的測試數，等於偽造量測記錄 —— 與第 3 條同一個道理。
   量不到就寫「未驗證」並註明原因（例如環境缺 DB），**不要**沿用上一個數字。

---

## 量測基準（2026-10-06）

本表是**現行文檔引用的計數器的唯一來源**。改了架構或加了測試／畫面，先重跑下面的
量法，再更新引用它的文檔（`../README.md`、`WORK_SUMMARY.md`）。

| 計數器 | 現值 | 怎樣量 |
|---|---|---|
| 後端 `app/` | 128 個 `.py` · 26,725 LOC | `find app -name "*.py" \| wc -l` |
| API surface | **106 paths / 119 operations** | `create_app().openapi()['paths']` |
| response_model 覆蓋 | 97/101 schema reachable；119 operations 全有 `response_model` | `scripts/verify/audit_response_models.py` |
| Alembic | **20** migrations · 單一 head `b8d1f2a3c4e5` | `alembic heads` / `ls alembic/versions/*.py` |
| `tests/` | 56 個 `.py`（55 個 `test_*.py` · 998 個 `def test_`） | `find tests -name "test_*.py" \| wc -l` |
| 後端 pytest | **全套 1219 passed / 0 failed / 0 error**（2026-10-06 單一 process 實跑） | `python -m pytest -q --junit-xml=…` |
| `mobile/lib` | 82 個 `.dart` · 17,042 LOC · **29** 個 `*_screen.dart` | `find mobile/lib -name "*.dart"` |
| Dart harness | **153 passed / 0 failed** | `dart … tool/run_tests.dart` |
| Dart LSP check | **84 files opened / 0 diagnostics** | `python mobile/tool/dart_check.py mobile` |
| Contract | 65 個 fixture json；harness 解到 **64** 個 · 0 failure | `dart … tool/verify_contract.dart` |
| `admin-web/web/src` | 49 個 `.ts/.tsx` · 16,965 LOC · 18 個頁面 | `ls src/pages/*.tsx \| grep -v .test.` |
| 後台 vitest | **81 passed（11 檔）** | `npx vitest run --no-file-parallelism --pool=forks` |
| `scripts/` | 22 個 `.py` | `find scripts -name "*.py"` |

> ✅ **後端測試 2026-10-06 全套一次過實跑全綠**（Docker Desktop 開住、`realtaxi-db` ＋
> `realtaxi-redis` healthy）：`.venv/Scripts/python.exe -m pytest tests -q --junit-xml=.tmp/final2.xml`
> = **1219 passed / 0 failed / 0 error / 0 skipped**。先前「一次過跑會中途中止、唔敢
> 宣稱 full-suite 全綠」嘅情況已經消失；單一 process 順序跑穩定。
> ⚠️ 並行跑兩隻 pytest 仍不建議（爭同一 DB/Redis 資源）。**GEO index 已納入
> `REDIS_KEY_NAMESPACE`**（`app/services/order/geo_service.py::geo_orders_key`），
> 所以「兩隻 run 互相污染出假 failed」嘅根因已消除。
> 逐次掃描的歷史快照見 [`ERROR_SCAN_2026-10-05.md`](ERROR_SCAN_2026-10-05.md)。
