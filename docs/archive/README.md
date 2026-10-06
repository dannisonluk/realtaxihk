# `docs/archive/` — 歷史快照

> **EN — Archive.** Dated point-in-time records: audit reports, code reviews,
> UI reviews, and the round-by-round work log. They are **evidence, not
> guidance** — test counts, line numbers, and file layouts inside them reflect
> the state *at the time of writing* and are deliberately left unedited.
> Editing them would amount to falsifying a measurement record.
>
> **中文摘要**：這裡是**帶日期的量測記錄** —— 審計報告、程式碼審閱、UI 審查、
> 逐輪工作日誌。它們是**證據，不是指引**；裡面的測試數、行號、檔案佈局都反映
> **當時**狀態，**刻意保持原樣**。改了等於偽造量測記錄。

要知道**今天**的狀態與仍未做項，讀 [`../WORK_SUMMARY.md`](../WORK_SUMMARY.md)。
文檔全貌見 [`../README.md`](../README.md)。

---

## 為什麼不整理它們

這個 repo 曾經因為「把舊文檔的數字更新到最新」而製造過假記錄 ——
一份 2026-10-01 的審查報告被改成寫著後來的測試數，於是它不再描述它聲稱
描述的那一天。現在的分工是：

| | 現行文檔（`docs/*.md`） | 歷史快照（`docs/archive/*.md`） |
|---|---|---|
| 描述 | 系統**今天**的樣子 | 某一天**量到**什麼 |
| 測試數／行號／路徑 | 跟現況更新 | **刻意不改** |
| 失效的引用 | 重新指向現行位置 | 保留，必要時加「已失效」註記 |
| 過時的做法 | 改寫 | 保留原文 |

> **唯一例外**：`SECURITY_AUDIT.md` 的逐字探測輸出不可修改 —— 那是攻擊重現的
> 原始記錄。文首有「路徑更名說明」處理後來改名造成的引用失效。

---

## 內容

| 文檔 | 是什麼 | 日期 | 相關現行文檔 |
|---|---|---|---|
| [`WORK_LOG.md`](WORK_LOG.md) | 逐輪工作日誌：每一輪的問題、修法與證據（§2.1 後端上線阻塞修復 … §2.21 收尾批次 commit） | 2026-09 ~ 10 | [`../WORK_SUMMARY.md`](../WORK_SUMMARY.md) |
| [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md) | 網絡安全審計：風險總表、SEC-01..31 逐條（Critical / High / Medium / Low）、攻擊重現附錄 | 2026-09-29（含後續輪次） | [`../SECURITY.md`](../SECURITY.md) |
| [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) | 上線就緒審計：掃描發現的 4 個真 bug、7 個 P0、10 個 P1、10 個 P2，逐條狀態與修復記錄、上線日 checklist | 2026-09-29 | [`../WORK_SUMMARY.md`](../WORK_SUMMARY.md) §4 |
| [`CODE_REVIEW_2026-10-01.md`](CODE_REVIEW_2026-10-01.md) | 深度程式碼審查：6 高 / 4 中 / 6 註釋級，含並行測試互相干擾的證據 | 2026-10-01 | — |
| [`CODE_REVIEW_2026-10-12.md`](CODE_REVIEW_2026-10-12.md) | 全代碼庫逐行審閱：P0 / P1 / P2 + migrations / 資料庫層 + 前端，含 **5 條已撤銷的誤報**（誤報本身是可複用教訓） | 2026-10-12 | [`../DEVELOPMENT.md`](../DEVELOPMENT.md) §6 |
| [`UI_DESIGN_REVIEW_2026-10-02.md`](UI_DESIGN_REVIEW_2026-10-02.md) | 管理後台 UI 設計審查（Apple HIG）：4 High / 8 Medium / 5 Low，對比度、無障礙、觸控目標、主題 token | 2026-10-02 | [`../ADMIN_CONSOLE_DESIGN.md`](../ADMIN_CONSOLE_DESIGN.md) |
| [`AGENT_HANDOFF_multi-agent-2026-10-06.md`](AGENT_HANDOFF_multi-agent-2026-10-06.md) | **多 agent 並行協作期**的檔案認領約定、分工表與逐輪進度。協作模式已於 2026-10-06 結束（改為單一 agent 全權承接），檔內「認領」「唔好改另一 agent 嘅檔」等規則**全部失效**；保留只作歷史證據 | 2026-09 ~ 10-06 | [`../WORK_SUMMARY.md`](../WORK_SUMMARY.md) |
| [`AUDIT_FINDINGS_LINEBYLINE.md`](AUDIT_FINDINGS_LINEBYLINE.md) | 逐行審計 findings log（活文件時期），已被 [`AUDIT_2026-10-06.md`](AUDIT_2026-10-06.md) 取代 | 2026-10-04 ~ 10-06 | [`../WORK_SUMMARY.md`](../WORK_SUMMARY.md) §4 |
| [`AUDIT_REPORT_2026-10-04.md`](AUDIT_REPORT_2026-10-04.md) | 企業級全量審計報告 v4 | 2026-10-04 | [`AUDIT_2026-10-06.md`](AUDIT_2026-10-06.md) |
| [`ERROR_SCAN_2026-10-05.md`](ERROR_SCAN_2026-10-05.md) | 實跑式全專案錯誤掃描 | 2026-10-05 | [`AUDIT_2026-10-06.md`](AUDIT_2026-10-06.md) |
| [`PRE_LAUNCH_CHECK_2026-10-06.md`](PRE_LAUNCH_CHECK_2026-10-06.md) | 上線前 Final Check（5-agent 條件 Go）；數字已過時 | 2026-10-06 | [`AUDIT_2026-10-06.md`](AUDIT_2026-10-06.md) |
| [`STRUCTURE_REVIEW.md`](STRUCTURE_REVIEW.md) | 目錄佈局評估 + keyset cursor bug 背景 | 2026-10-06 前 | [`../DEVELOPMENT.md`](../DEVELOPMENT.md) |
| [`AUDIT_2026-10-06.md`](AUDIT_2026-10-06.md) | **Fresh root-and-branch audit**：backend/mobile/admin findings、fix disposition、gate outputs | 2026-10-06 | [`../WORK_SUMMARY.md`](../WORK_SUMMARY.md) |

---

## 檔案內的連結

這些文檔寫成時，其他文檔還在 `docs/` 根目錄。文中出現的 `docs/XXX.md`
若指的是下列檔案，實際位置是 `docs/archive/XXX.md`：

`SECURITY_AUDIT.md` · `PRODUCTION_READINESS.md` · `CODE_REVIEW_2026-10-01.md` ·
`CODE_REVIEW_2026-10-12.md` · `UI_DESIGN_REVIEW_2026-10-02.md` ·
`AUDIT_FINDINGS_LINEBYLINE.md` · `AUDIT_REPORT_2026-10-04.md` ·
`ERROR_SCAN_2026-10-05.md` · `STRUCTURE_REVIEW.md` ·
`PRE_LAUNCH_CHECK_2026-10-06.md`

另外兩份現行文檔已於 2026-10 合併／更名：

| 舊路徑 | 現況 |
|---|---|
| `docs/PROJECT_UNDERSTANDING.md` | 併入 [`../DEVELOPMENT.md`](../DEVELOPMENT.md)（規範、怪癖、方法論） |
| `docs/LINTING.md` | 併入 [`../DEVELOPMENT.md`](../DEVELOPMENT.md) §4（lint gate） |

---

## 新增歷史快照

新的審計／審查報告**直接寫進 `docs/archive/`**，用帶日期的檔名
（`CODE_REVIEW_YYYY-MM-DD.md`），並在本文件的表格加一行。
不要把它們混進 `docs/` 根目錄 —— 那會令「現行指引」與「當時量測」再次混淆。
