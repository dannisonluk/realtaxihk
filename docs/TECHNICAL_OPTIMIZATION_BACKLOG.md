# TECHNICAL_OPTIMIZATION_BACKLOG

> 技術優化 backlog：低風險、非功能性的改進清單，與產品功能提案
> （`FEATURE_EXPANSION_2026-10-05.md`）分開追蹤。目標是讓系統更快、更穩、
> 更省資源、更好維護，而不是加新功能。
>
> 狀態圖例：✅ 已落地 · ⏳ 已排程／進行中 · ☐ 待辦（含原因）

---

## 第一批（2026-10-08 落地）

### ✅ 1. CI 依賴審計（pip-audit + npm audit）

**問題**：CI 從來沒有檢查過依賴的安全性。依賴一旦有已知漏洞（CVE），
不會有任何 gate 攔住，直到有人手動跑 audit 才發現。

**落地內容**（`.github/workflows/ci.yml`）：

- `test` job 新增 **pip-audit** step：`uvx pip-audit --path .venv --progress-spinner off -f json`
  - 不用 `uv run pip-audit`：pip-audit 不是專案依賴，`uv run` 會找不到。
  - 不直接用 `uvx pip-audit .`：repo root 沒有 lockfile，會報
    `no lockfiles found`；改指 `.venv`（`uv sync --frozen` 之後的環境）就認得到。
  - 本地實跑：`No known vulnerabilities found`，exit 0 ✅
- `admin-web` job 新增 **npm audit** step：`npm audit --omit=dev --audit-level=high --json`
  - 只審 production 依賴（`--omit=dev`）。完整 `npm audit` 目前會報 dev-only
    的 Vite 工具鏈漏洞（vite / vitest / source-map-js），見下方 ☐ 3。
  - 本地實跑：exit 0 ✅（production 依賴無 high+ 漏洞）

**驗證**：兩條指令都在本機實跑過、exit code 都正確。

### ✅ 2. admin console 載入中斷（AbortSignal 貫穿 fetch）

**問題**：`useLoad` 之前只做「晚到回應丟棄」（renderToken 守衛），
但**底層 fetch 沒有真的被取消**。頁面切走／依賴改變時，in-flight 的 socket
留在原地，直到 server 回應或 timeout —— 慢網路下會積一堆無人接收的請求，
佔住連接池，也浪費 server 資源。

**落地內容**：

- `admin-web/web/src/app/useLoad.ts`
  - loader 合約改為 `load: (signal: AbortSignal) => Promise<T>`。
  - 每次 effect 建立自己的 `AbortController`；cleanup（切頁／deps 改變）時
    `controller.abort()`。
  - `inOrder` 同步改為接受 `(signal) => Promise<unknown>` 的 thunks +
    optional signal，逐個傳給每個 thunk。
- `admin-web/web/src/api/client.ts`
  - `get`／`send` 已支援 `signal`（`fetch(url, { signal })` 已有，見
    `send()` 的 `signal` option）；abort 例外被明確視為**不是** transport
    failure（`if (signal?.aborted) throw cause;` 不重試）—— 正確行為：
    取消就是取消，不該重發。
- `admin-web/web/src/api/endpoints.ts`
  - 所有 list/detail/stats/summary 類 GET endpoint 都加 optional
    `signal?: AbortSignal` 參數並傳給 `client.get(..., signal)`。
- 所有用 `useLoad` 的頁面（13 頁）都把 signal 傳進 endpoint call：
  - 直接呼叫型：Accounts、Destinations、Disputes(detail)、DriverDetail、
    Fleets、FleetDetail(members)、Licence(detail)、OrderDetail
  - `inOrder` 串接型：Dashboard（6 calls）、Disputes queue（list+stats）
  - 單一 async 型：Kyc、Licence、Refunds
  - Analytics 四條（summary / heatmap / operations / supply）
- `admin-web/web/src/app/useLoad.test.tsx`
  - 新增測試：deps 改變時**上一個 loader 的 signal 會被 abort**、
    新 loader 的 signal 保持非 aborted。

**效果**：切頁或改 filter 時，舊請求的 socket 立即釋放；`fetch` 收到
`AbortError`，不會被誤當成網路失敗重試。

**驗證**：`npm run typecheck` ✅；`npx vitest run` **97 passed（14 檔）** ✅
（含新增的 abort 測試）。`npm run build` 見下方全驗證。

---

## 下一批（☐ 未排程）

### ☐ 3. 升級 Vite 工具鏈（dev-only 漏洞）

**現況**：完整 `npm audit` 會報 dev-only 漏洞，集中在 Vite 生態
（`vite` / `vitest` / `source-map-js`），severity high。

**為什麼還沒做**：升級 Vite 是 major-bump（v5 → v6/v7 或以上），
vitest 與 `@vitejs/plugin-react` 要一起動，風險是 plugin 相容性與
build 行為改變。目前 production 依賴**乾淨**（CI gate 已用 `--omit=dev`
蓋住），dev 漏洞不影響交付物，所以列為排程項而不是立即修。

**要做的事**：
1. 確認最新 stable 的 vite / vitest / plugin-react 版本（用
   `C:\Program Files\nodejs\npm.cmd view <pkg> version`，shell 內建
   `npm` 在 MSYS 會被誤解析）。
2. 升 major 後跑完整 gate：`npm run typecheck` + vitest + `npm run build`。
3. 升完把 CI 的 npm audit 改回完整版（去掉 `--omit=dev`）。

### ☐ 4. Android backup 旗標

`AndroidManifest.xml` 目前 `android:allowBackup`／`fullBackupContent` 的狀態
值得再確認（見 `mobile/android/app/src/main/AndroidManifest.xml`）。
決定方向：正式 App 對乘客敏感資料（登入 token、位置歷史）應考慮
`android:allowBackup="false"` 或提供 backup 規則，防止 token 被備份到
Google 雲端／adb 提取。這屬於上線前安全取捨，等部署目標確定後一併拍板。

### ☐ 5. 位置權限「開啟設定」深連結

App 被拒位置權限後，目前的引導路徑可加「直接打開系統設定頁」的按鈕
（`openAppSettings()` 深連結），減少用戶在權限設定裡迷路。
低風險 UX 改善，待 mobile 下一輪一併做。

### ☐ 6. FleetDetail／Licence detail 失敗重試 UX

兩頁目前用標準 `ErrorState`（已有 `onRetry={reload}`），但「自動重試一次」
或「顯示已重試次數」的體驗可以更好。低優先，因為 reload 已經能用。

---

## 追蹤原則

- 本文件是**現行文檔**（不是 archive snapshot）：落地了就改狀態、改了代碼
  就更新對應行的描述。
- 計數器（測試數等）只寫實跑量到的數字，方法寫在 `docs/README.md` 的
  量測基準表。
- 每個 item 的「驗證」欄只寫**真的跑過**的結果。
