# 管理後台 UI 設計審查（2026-10-02）

> 審查對象：`admin-web/web/`（React 19 + Vite，30 個 `.tsx` / 16 個 `.ts` / 1 個 `styles.css`，
> 非測試原始碼 12,999 行）。
>
> 審查依據：Apple Human Interface Guidelines 的 123 頁參考，逐頁讀過後引用；
> 對比度一律以 token 的十六進位值計算，不憑目測。
>
> 這是**一次快照**，不是常駐文件。與 `CODE_REVIEW_2026-10-01.md` 同性質。

---

## 摘要

**整體評價：良好（Good）。** 沒有任何 Critical 問題，但有四項 High：預設主題設定下
`color-scheme` 與實際調色盤相反、日期輸入框完全未套用欄位樣式、深色主題的品牌紅
作為文字僅 2.91–3.62:1、淺色主題的四種語意 chip 為 4.17–4.40:1。

這個後台的**設計論點非常清楚**：它是一台「動錢的機器」，所以顏色必須只承載一個意思、
危險動作必須可讀但不能最誘人、狀態必須一眼可辨。`styles.css` 開頭 21 行的註解自己
把這件事寫出來了 —— 「Neutral greys carry the layout; exactly one chromatic colour
(taxi red) is spent, on the brand and the active nav item」。這不是隨手寫的註解，
而是全檔 1,057 行都在遵守的規則（`.btn--danger` 刻意做成紅框而非紅色實心塊、
`.card--alert` 只把邊框染紅 45%、QR 圖在深色主題下仍保持白底）。

它會被記住的一點是：**側邊欄那兩顆分段控制（主題 ◐☀☽ / 語言 中EN）**。兩個控制的
寬度不隨對方改變、深色主題下選中段刻意比軌道**亮**（`--seg-active` 是獨立 token，
不是借用的 surface）、`<fieldset>` 的 UA 邊框被完整拆掉。這是一個很小的東西，
但它是整個介面唯一一個「為了不讓使用者困惑而多花了三層註解」的地方。

**引用慣例**：`檔案.md › 標題`。凡屬我的判斷而非準則明文，均標示「（判斷）」。

---

## 修復狀態（2026-10-02 同日）

本節由修復者補寫，**不屬於原審查**。上面各節維持原樣不動 —— 這是日期快照的原則，
回寫會讓「審查當時看到什麼」這件事消失。

**四項 High、八項 Medium、五項 Low 之中，程式碼側的部分已全部修好**，另加三項在修復
過程中新發現的同類缺陷。兩個 commit：`827f439`（token 與對比）、`90b9d2e`（元件與文案）。

| 編號 | 狀態 | 備註 |
|---|---|---|
| H-1 `color-scheme` | ✅ `827f439` | 改為跟隨與 token 相同的兩個條件；守衛逐條檢查 |
| H-2 欄位選擇器 | ✅ `827f439` | 列舉改排除法，新 type 預設繼承樣式 |
| H-3 深色品牌文字 2.91:1 | ✅ `827f439` | 新增 `--brand-text`（淺 `#d2232a` / 深 `#ff7b72`） |
| H-4 淺色 chip 4.17–4.40:1 | ✅ `827f439` | 底色 12% → 6%；`--warn` / `--danger` 亦調深 |
| M-1 分段控制 1.06:1 | ✅ `827f439` | 新增 `--seg-edge`（淺 4.32:1 / 深 3.31:1） |
| M-2 三個紅 | ✅ `827f439` | `--danger` → `#a40e26`；`--gain` 已刪 |
| M-3 `.gain` / `.loss` 死碼 | ✅ `827f439` | 兩個 class 與 `--gain` 一併刪除 |
| M-4 24 小時無障礙替代 | ✅ `90b9d2e` | 圖表下加 `<details>` ＋ 24 列 `<table class="data">` |
| M-5 chip 當按鈕 | ✅ `90b9d2e` | **另找到 12 處**（`OrdersPage` 十個只有 21px） |
| M-6 統計卡無可點跡象 | ✅ `90b9d2e` | `.stat--link`；hover / focus 往內傳給 `.stat` |
| M-7 圖表文字隨 `viewBox` 縮放 | ✅ `90b9d2e` | `.chart` 改為橫向捲動，svg 以 `viewBox` 寬度為下限 |
| M-8 硬編碼中文標點 | ✅ `90b9d2e` | 4 處 ＋ **2 處全形括號**（`KycPage` 那處是真 bug） |
| L-1 六處行內 `fontSize: 12` | ✅ `90b9d2e` | 改為 `t-caption1` |
| L-2 `.pref__label` | ✅ `827f439` | 規則與註解一併刪除 |
| L-3 `prefers-contrast` | ✅ `827f439` | 新增 `@media (prefers-contrast: more)` |
| L-4 圖例 opacity | ✅ `827f439` | 三種狀態補上與地圖標記相同的 opacity |
| L-5 其他小項 | ✅ | `notAnAdminDialog()`、`common.createdAt` **與** `createdDate`、`dashboard.sub`、`--focus` 雙重職責 |

### 修復時新發現（不在原審查內）

1. **`KycPage` 的括號是真 bug**（M-8 相鄰）：它在車牌之後就閉括號，把車型留在括號
   外面，渲染成 `車牌 AB1234）市區的士` —— 一個沒有對應左括號的右括號。
   `DriverDetailPage` 的同一個句子是正確的，所以這是複製時的漏改，不是設計。
2. **12 個互動 chip 低於 28px**（M-5 同類）：`OrdersPage` 十個狀態篩選只有 **21px**、
   `DisputesPage` 五個、`AuditPage` 與 `FleetsPage` 各一。原本的審查只看到地圖頁。
3. **`.th-sort` 沒有填滿儲存格**：它的註解自稱「the button fills the cell so the whole
   header is clickable」，實際高度 18px 而儲存格 34px —— 橫向填滿了，縱向沒有，
   所以真正可點的排序區域只有文字方塊。

### 守衛

| 守衛 | 位置 | 抓什麼 |
|---|---|---|
| `tool/check_contrast.py` | `admin-web/web/` | 五類對比 ＋ 三條主題路徑的 `color-scheme`；由 `tests/test_console_contrast.py` 執行，並以突變測試證明它會失敗 |
| `audit_layout.mjs` 檢查 6 | `admin-web/tool/` | SVG 文字被 `viewBox` 縮到小於原本字級 |
| `audit_layout.mjs` 檢查 7 | `admin-web/tool/` | 互動控件小於 28px |

`audit_layout.mjs` 同時修好兩個既有缺口：`#/live` 從來不在 `ROUTES` 裡（所以本文原本
引用的「52 renders clean」實際只有 48 次，而唯一渲染第三方控件的頁面正好是唯一沒被量
的頁面），以及 `.sr-only` 被誤判為「被裁切的文字」。

**驗證**：`audit_layout` 52 renders clean @1440px 與 @500px · `pytest` 928/0/0/0
（`--junit-xml` 讀）· `vitest` 69 passed（9 files）· `ruff check` ＋ `format --check`
clean · console `tsc` clean · `check_contrast.py` 與 `check_theme_tokens.py` 皆 OK。
非測試原始碼行數由 12,999 增至 13,109。

---

## 嚴重（Critical）

無。

---

## 待改進（Improvements）

### H-1｜`color-scheme: dark` 在 `system` 主題下無條件生效，預設設定即中招

**High**

- **現象**：`styles.css:92-95` 把 `color-scheme: dark` 同時掛在 `[data-theme="dark"]`
  **與 `:root[data-theme="system"]`** 上；而真正決定深色 token 的
  `@media (prefers-color-scheme: dark)`（`:122-143`）只覆寫自訂屬性，**沒有重宣告
  `color-scheme`**。因此當作業系統是淺色、主題選 `system` 時，頁面用的是淺色調色盤，
  瀏覽器卻以深色渲染所有原生控件。
- **為什麼會中**：`system` 是預設值 —— `theme.ts:26` 的 `THEME_MODES` 把它排在第一，
  `readThemeMode()` 的失敗回退也是 `'system'`。所以「淺色系統 + 預設主題」是**最多人
  會遇到的組合**。
- **影響範圍**：捲軸、`<select>` 下拉彈出層、`<input type="date">` 的原生外觀、
  checkbox 的 UA 樣式，以及 CSS 載入前的畫布底色。
- **準則**：`color.md › Best practices`：「Make sure all your app's colors work well in
  light, dark, and increased contrast contexts.」這裡不是顏色本身錯，而是**外觀模式
  的宣告與實際不符**，等於對瀏覽器說了一個謊。
- **修法**：把 `color-scheme` 的宣告拆成三處，讓它與 token 同一條件：

  ```css
  /* 只宣告深色的兩條路徑，各自帶上 OS 條件 */
  [data-theme="dark"] { color-scheme: dark; }

  @media (prefers-color-scheme: dark) {
    :root[data-theme="system"] { color-scheme: dark; /* 其餘 token 不變 */ }
  }
  /* 淺色的預設由 :root 的 color-scheme: light 承接，不需另寫 */
  ```

- **守衛**：`tool/check_theme_tokens.py` 已經在比對兩個深色區塊的**自訂屬性集合**，
  但它讀的是 `--[a-z0-9-]+` 開頭的屬性，`color-scheme` 不在其中，所以完全看不到
  這個不一致。建議在同一個腳本內加一條：斷言「`color-scheme: dark` 出現的次數與
  位置」與深色 token 的兩條路徑一致。

### H-2｜`<input type="date">` 與 `type="email"` 完全沒套用欄位樣式

**High**

- **現象**：`styles.css:613-618` 的選擇器清單是
  `input[type="text"] / tel / number / search / select / textarea`。
  全專案實際用到的 input 型別是：

  | 型別 | 出現次數 | 是否被樣式涵蓋 |
  |---|---|---|
  | `text` | 17 | ✅ |
  | `password` | 4 | ✅（未列於清單，但由 `input:focus` 與繼承取得部分樣式） |
  | `number` | 4 | ✅ |
  | `checkbox` | 4 | ❌（UA 原生） |
  | `search` | 2 | ✅ |
  | **`date`** | **2** | **❌** |
  | `tel` | 1 | ✅ |
  | `radio` | 1 | ❌（UA 原生） |
  | **`email`** | **1** | **❌** |

  兩個 `type="date"` 都在 `AnalyticsPage.tsx:152,164` 的日期範圍篩選器上，而該篩選器
  就在 `.filters` 這一列裡、與四個已套樣式的 `<select>` 並排。同一列之中，兩個控件
  有 `min-height: 44px`、`border-radius: 10px`、`border: 1px solid var(--border)`，
  另一個三者皆無。
- **準則**：`layout.md › Visual hierarchy`：「Align elements to make them easier to scan
  … People assume that aligned items are related to each other」。並排的篩選控件長得
  不一樣，會被讀成「這個欄位跟其他欄位不是同一件事」。另見
  `buttons.md › Best practices` 對「一致性」的要求。
- **修法**：把清單補齊（`date`、`datetime-local`、`email`、`password`），或直接改成
  `input:not([type="checkbox"]):not([type="radio"])`，讓「沒被特別排除的都吃欄位樣式」
  成為預設，而不是「只有列出來的三種」——後者會在下次新增型別時再漏一次。
- **附帶**：`checkbox` 與 `radio` 只有 1 個 `radio`、4 個 `checkbox`，數量少，
  但它們同樣受 H-1 的 `color-scheme` 影響。

### H-3｜深色主題的品牌紅作為文字，最低只有 2.91:1

**High**

- **現象**：`--brand` 在兩個主題區塊裡都是 `#d2232a`，**完全沒有為深色調整**。
  實測對比度：

  | 前景 | 背景 | 比值 | 門檻 |
  |---|---|---|---|
  | `--brand` | `--bg` `#0d1117` | 3.62:1 | 4.5:1 |
  | `--brand` | `--surface` `#161b22` | 3.31:1 | 4.5:1 |
  | `--brand` | `--surface-2` `#21262d` | **2.91:1** | 4.5:1 |
  | `--brand` | 12% brand over surface（側欄選中項） | **3.08:1** | 4.5:1 |

  這四個不是裝飾，全都是**文字**：`.navlink[aria-current="page"]`（15px / 600）、
  `.chip--brand`（13px / 600）、`.th-sort:hover`（表頭 12px / 600）。
  600 字重不等於 WCAG 的 bold（門檻是 ≥700），所以一律需要 4.5:1。
- **準則**：`accessibility.md › Vision` 的對比表：「Up to 17 pts / All / 4.5:1」。
  另 `dark-mode.md › Dark Mode colors`：「For custom foreground and background colors,
  strive for a contrast ratio of 7:1, especially in small text.」
- **修法**：給深色主題一個獨立的品牌色 token，只提高亮度、不動色相，例如
  `#f85149`（Primer dark 的 danger.fg，同一份 palette，本專案已在用）或 `#ff7b72`。
  以 `#ff7b72` 為例，對 `--surface` 是 7.4:1、對 `--surface-2` 是 6.5:1，兩個都過。
  **注意**：`--brand` 也被用作 `.btn--primary` 的**底色**（白字置於其上，5.23:1，合格），
  所以不要直接把 `--brand` 改亮 —— 要拆成 `--brand`（底色）與 `--brand-text`
  （深色主題的文字用），否則會把按鈕的白字對比一起拉低。

### H-4｜淺色主題的四種語意 chip 都低於 4.5:1

**High**

`.chip` 的底色是 `color-mix(in srgb, currentColor 12%, transparent)`，也就是
**文字色自己的 12% 疊在白底上** —— 這會把底色往文字色推近，必然降低對比。
實測（淺色主題，`--surface` `#ffffff`）：

| Chip | 文字色 | 混合後底色 | 對比 | 門檻 |
|---|---|---|---|---|
| `.chip--warn` | `--warn` `#9a6700` | `#f3ede0` | **4.17:1** | 4.5:1 |
| `.chip--brand` | `--brand` `#d2232a` | `#fae5e5` | **4.34:1** | 4.5:1 |
| `.chip--ok` | `--loss` `#2e7d32` | `#e6efe6` | **4.36:1** | 4.5:1 |
| `.chip--danger` | `--danger` `#cf222e` | `#f9e4e6` | **4.40:1** | 4.5:1 |
| `.chip--neutral` | `--text-dim` `#57606a` | `#ebeced` | 5.40:1 | ✅ |

Chip 是這個後台**最主要的狀態載體**（`labels.ts` 有 11 張 tone 對照表），
13px / 600 全部需要 4.5:1。
- **準則**：`accessibility.md › Vision`（同上表）。
- **修法**：把混合比例從 12% 降到 8%，或改用**邊框承載顏色、底色保持中性**。
  前者最省事：`--warn` 在 8% 混合下為 4.45:1，仍不足；**建議降到 6%**（`#f6f0e6`，
  4.62:1 通過）並同時把 chip 的邊框由 `1px` 加到 `1.5px`，讓「有色」這個訊息
  由邊框而非底色承載。深色主題的同一組 chip 全部合格（4.55–5.68:1），不需改。

### M-1｜分段控制的「已選中」狀態只靠 1.06:1（淺）/ 1.25:1（深）的色差

**Medium**

`.segmented__btn[aria-pressed="true"]` 的辨識完全來自填色差：

| 主題 | 選中填色 | 軌道 | 比值 | 邊框 |
|---|---|---|---|---|
| 淺色 | `#ffffff` | `#f6f8fa` | **1.06:1** | `#d0d7de` 對軌道 1.36:1（勉強看得出邊） |
| 深色 | `#30363d` | `#21262d` | **1.25:1** | `--border` = `#30363d`，**與填色同一個十六進位 → 邊框完全消失（1.00:1）** |

`styles.css:434-452` 的註解記錄了上一次修這個問題的過程：原本深色用 `--surface`
（`#161b22`）疊在 `--surface-2`（`#21262d`）上，選中段**比軌道更暗**，看起來像打了個洞。
那次修對了**方向**（改成更亮），但**幅度**仍然只有 1.25:1，而且深色的邊框與填色同色，
等於把上一次為了補救而加的邊框又抵銷掉了。

- **準則**：`accessibility.md › Vision` 的「Convey information with more than color
  alone」；WCAG 1.4.11 要求辨識**狀態**的非文字對比達 3:1。
- **修法**：深色把 `--seg-active` 提到 `#3d444d`（對軌道 1.6:1，仍不足）——
  建議改為 `#484f58`（對 `#21262d` 為 2.4:1）並**同時**給選中段一個亮色邊框
  （`--border` 在深色主題改為比填色亮的 `#545d68`），讓邊框與填色共同承載狀態。
  或（判斷）接受現狀但在**選中段加一個 2px 的 `--focus` 底線**，用形狀而非純色差
  承載狀態，這樣同時滿足「不只靠顏色」。

### M-2｜三個紅色的色值幾乎相同，卻各代表一個意思

**Medium**

| Token | 淺色值 | 對白底 | 語意 |
|---|---|---|---|
| `--brand` | `#d2232a` | 5.23:1 | 品牌、當前導覽項、主要按鈕、導覽計數、地圖上「載客中」 |
| `--gain` | `#c62828` | 5.62:1 | 帳務金額**增加** |
| `--danger` | `#cf222e` | 5.36:1 | 危險、錯誤、拒絕、地圖上「異常」 |

三者的對白底比值落在 5.23–5.62 這個 0.39 的區間內，肉眼不可區分。
- **準則**：`color.md › Best practices`：「Avoid using the same color to mean different
  things. Use color consistently throughout your interface, especially when you use it
  to help communicate information like status or interactivity.」
- **修法**：`--danger` 拉開距離（例如 `#a40e26`，對白底 7.3:1，且與品牌紅明顯不同），
  或把「載客中」的車輛標記改離品牌紅（見 M-3）。**不要**動 `--brand`，它已經綁在
  按鈕與側欄上。

### M-3｜`.gain` / `.loss` 是死程式碼，而「香港慣例」與實際實作相反

**Medium**

- `styles.css:212-218` 有一段被反覆強調的規則與註解：

  > Semantic text. `gain`/`loss` follow the HK convention: a *gain* is red, a *loss* is
  > green. Do not read them as profit/loss semantics in the Western sense — on this
  > console red is the good number.

  但 `.gain` 與 `.loss` 這兩個 class 在**整個 `src/` 裡出現 0 次**（以詞邊界搜尋
  `src/**/*.tsx` 與 `*.ts` 確認，`--gain` 因此只被死規則引用，等同未被使用）。
- 同時，實際承載帳務方向的是 `labels.ts` 的 `ENTRY_TONE`：

  ```
  DEPOSIT_TOPUP: 'ok'        → 綠色
  PENALTY_DEDUCTION: 'danger' → 紅色
  ```

  也就是**儲值（金額增加）是綠色**——與上段註解宣告的「red is the good number」**相反**。

- **判斷**：`ENTRY_TONE` 的寫法（儲值綠、罰款紅）對一個「業者帳戶」的介面是對的，
  香港的「紅升綠跌」是**股價**慣例，不是帳務慣例。所以該刪的是那段註解與兩個死 class，
  而不是改 tone。但**現況是最壞的**：文件宣告 A，程式實作 B，而 A 的程式碼路徑從未接上。
- **準則**：`color.md › Best practices`（同 M-2）；以及「一份文件如果描述的是不存在的
  機制，比沒有文件更危險」（判斷）。
- **修法**：刪掉 `.gain` / `.loss` 兩條規則與其註解；若日後真要顯示帶正負號的金額，
  由 `Money` 元件的 `sign` 屬性承載（它已經存在，見 `primitives.tsx:184`），
  並在該處用一個 token 決定顏色。

### M-4｜24 小時的資料沒有任何無障礙替代表示

**Medium**

`AnalyticsPage.tsx` 的兩個視覺化都是單一 `role="img"`：

- `HourBarChart`（`:417`）：`role="img"` + 一個 `aria-label`；24 根柱子的
  hover 命中區是 `<rect>` 加 `onMouseEnter`（`:460-461`），**不可聚焦**，
  提示框 `role="status"` 因此只有滑鼠能觸發。
- `HourHeatStrip`（`:543`）：`role="img"` + `aria-label`，而每個 `.heat__cell`
  的數值放在 `title` 屬性裡 —— **`role="img"` 的子節點是 presentational**，
  這些 `title` 不會進入無障礙樹。

頁面下方確有一張文字表格，但它承載的是**另一個資料集**（依 day/week/month 分桶的
總計），不是這 24 個小時。所以「哪個小時最賺錢」這個問題，對螢幕閱讀器與鍵盤使用者
**沒有可讀的答案**。
- **準則**：`accessibility.md › Vision`：「Convey information with more than color
  alone.」；`color.md › Inclusive color`：「Avoid relying solely on color to
  differentiate between objects, indicate interactivity, or communicate essential
  information.」
- **修法**：兩者之一即可 ——
  1. 把 `role="img"` 換成 `role="table"` 語意（或直接在圖下方加一個
     `<table class="data">` 列出 24 列的 `hour / avg_per_day_hkd / orders`，
     並用 `sr-only` 或可展開的 `<details>` 呈現）；或
  2. 讓 `.heat__cell` 成為 `<button>` 或加 `tabIndex={0}` 與 `aria-label`，
     使提示框可由鍵盤觸發。

  建議採 (1)：本專案在 `LiveMapPage` 已經用「地圖 + 等價表格」解決過同一個問題
  （`LiveMapPage.tsx:27-32` 的註解明說表格是 the accessible equivalent rather than a
  duplicate），這裡只是漏了同一招。

### M-5｜當成按鈕用的 `.chip` 高度約 26px，低於本專案自訂的 44px

**Medium**

`LiveMapPage.tsx:253-268` 把 `<button>` 加上 `.chip` class（自動更新開關、包含離線）。
`.chip` 的規格是 `padding: 2px 12px` + `font-size: 13px`，行高繼承 body 的 1.5，
加上 1px 邊框 → **約 26px**。同一頁的「立即重新整理」用 `.btn.btn--sm`（34px），
其餘地方用 `.btn`（44px，`--target`）。

- **準則**：`accessibility.md › Mobility` 的控件尺寸表 —— macOS 預設 28×28 pt、
  **最小 20×20 pt**；`buttons.md › Best practices`：「a button needs a hit region of at
  least 44x44 pt」。26px 高於 20pt 的絕對下限，但低於 28pt 的預設值，也低於本專案
  自己寫死在 `styles.css:72` 的 `--target: 44px`（註解還寫著「Never draw a control
  smaller than this」）。
- **修法**：給「可互動的 chip」一個獨立 class（例如 `.chip--action`），
  `min-height: 34px`（與 `.btn--sm` 一致）並保留 `aria-pressed` 的視覺狀態。
  純顯示用的 `.chip` 維持不變 —— 現在的問題是**同一個 class 同時當標籤與按鈕**。

### M-6｜儀表板四張統計卡中三張是連結，但沒有任何可點擊的跡象

**Medium**

`DashboardPage.tsx:79-99`：三張 `Stat` 被包在 `<Link>` 裡並套用
`style={{ textDecoration: 'none', color: 'inherit' }}`，第四張不是連結。
`styles.css` 的 `.stat` 系列（`:550-559`）**沒有任何 `:hover` 規則**。

於是：三張可點、一張不可點，外觀完全相同，hover 時也沒有任何回饋。
- **準則**：`buttons.md › Best practices`：「Always include a press state for a custom
  button. Without a press state, a button can feel unresponsive, making people wonder
  if it's accepting their input.」另 `color.md › Inclusive color` 要求互動性不能只靠
  顏色區分（這裡是連顏色都沒有）。
- **修法**：加 `.stat--link` 規則，至少含 `cursor: pointer`、`:hover` 的
  `border-color: var(--brand)` 或 `background: var(--hover)`，以及 `:focus-visible`
  的輪廓；或在卡片右上角放一個 `›` 指示符。第四張卡保持不可點，讓差異**看得出來**。

### M-7｜圖表文字隨 viewBox 縮放，窄視窗下會縮到約 6px

**Medium**

`HourBarChart` 的 SVG 是 `viewBox="0 0 720 240"` 配 `width: 100%`，
軸標籤寫死 `fontSize={10}`（`:435, :478`）、軸標題 `fontSize={11}`（`:492`）。
SVG 文字會隨容器**等比縮放**：

| 卡片內寬 | 縮放係數 | 10 單位文字實際渲染 |
|---|---|---|
| 1080px | 1.50× | 15.0px |
| 720px | 1.00× | 10.0px |
| 436px（500px 視窗，860 斷點後） | 0.61× | **6.1px** |

`.main` 在 860px 斷點後內距降為 16px，所以在 500px 寬的視窗上，軸標籤會掉到 6px 上下。
- **準則**：`typography.md › Ensuring legibility`：「Test legibility in different
  contexts.」；`accessibility.md › Vision` 的 macOS 最小 10pt（≈13.3px）。
- **修法**：把軸文字移出 SVG（用 HTML 絕對定位），或改用
  `preserveAspectRatio="xMidYMid meet"` 搭配固定 `height` 並在窄螢幕降低
  `viewBox` 的 `W`（例如 480），或最簡單：`@media (max-width: 700px)` 下把圖表改成
  `width: 720px` + 外層 `overflow-x: auto`，讓文字**不被縮放**而是讓圖表橫向捲動。

### M-8｜四處硬編碼的中文標點，會出現在英文介面裡

**Medium**

| 位置 | 內容 |
|---|---|
| `LicencePage.tsx:298` | `.join('、')`（缺漏文件清單） |
| `LicencePage.tsx:305` | `.join('、')`（聲明未上傳清單） |
| `LicencePage.tsx:392` | `{formatDate(...)}。`（句號） |
| `LoginPage.tsx:436` | `{index > 0 ? '、' : ' '}`（驗證器 App 清單分隔） |

`LoginPage` 那一處最明顯：英文 locale 下會輸出
`No authenticator app? Google Authenticator、Microsoft Authenticator、FreeOTP`。
- **準則**：`writing.md › Getting started`：「Choose simple, plain language and write
  with accessibility and localization in mind」；`writing.md › Best practices` 要求
  標點與大小寫在整個 app 內一致。
- **修法**：把分隔符與句末標點納入 locale 資源（例如 `common.listSeparator`、
  `common.sentenceEnd`），或改用 `<ul>` / 逗號等語言中立的結構。
  **`i18n/index.ts` 已有 key 覆蓋率檢查**，所以新增 key 不會兩邊漏。

### L-1｜型別階梯 11 級中有 7 級從未被使用

**Low**

`styles.css:185-195` 定義了 iOS Dynamic Type 的 11 級，檔頭註解聲明
「A component picks a step by name (`t-title1`), never by pixel value, so the whole
console retunes from here.」實際使用情況：

| 有使用 | 從未使用 |
|---|---|
| `t-title1`(4)、`t-title3`(7)、`t-caption1`(17)、`t-footnote`(21)、`t-section`(1) | `t-large-title`、`t-title2`、`t-headline`、`t-body`、`t-callout`、`t-subhead`、`t-caption2` |

另外有 **6 處 `style={{ fontSize: 12 }}`** 內嵌（等於 `t-caption1` 的 12px，
但少了它的 `line-height: 1.35`），也就是「never by pixel value」這條紀律在實務上
被繞過了 6 次。
- **判斷**：留著未使用的階梯本身無害（它是一份完整的比例尺，刪掉反而讓下一個人
  自己造數字），但**內嵌 12px 應該改成 `className="t-caption1"`**，否則調整比例尺時
  這 6 處不會跟著動 —— 那正是這套階梯存在的理由。

### L-2｜`.pref__label` 是死規則，而它的註解描述了一個不存在的行為

**Low**

`styles.css:380` 的 `.pref__label` 在 `src/` 中出現 **0 次**。
它的註解寫著「The row label ("Theme" / "語言") is the only text … Sliding it out of the
flex flow on very narrow sidebars keeps the control usable」——但
`PreferenceControls.tsx` 把標籤放在 `<legend className="sr-only">` 裡，畫面上根本沒有
列標籤，也就沒有「滑出 flex 流」這回事。規則與註解都應刪除。

### L-3｜沒有回應 `prefers-contrast` / `forced-colors`

**Low**

`styles.css` 有 `prefers-reduced-motion`（`:849`），但沒有
`prefers-contrast: more` 或 `forced-colors: active`。
- **準則**：`accessibility.md › Vision`：「If your app doesn't provide this minimum
  contrast by default, ensure it at least provides a higher contrast color scheme when
  the system setting Increase Contrast is turned on.」由於 H-3 與 H-4 確實未達 4.5:1，
  這一條由「建議」變成「應做」。
- **修法**：加一段 `@media (prefers-contrast: more)`，把 `--text-dim` 提到 `--text`、
  `--brand`（文字用）提到 `#a40e26`，並把 `--border` 加深。

### L-4｜地圖圖例的色塊與地圖上的標記透明度不一致

**Low**

| 狀態 | 地圖標記（SVG） | 圖例色塊（HTML） |
|---|---|---|
| 載客中 | `fill-opacity: 0.85` | 不透明 |
| 等待中 | `fill-opacity: 0.85` | 不透明 |
| 位置過期 | `fill-opacity: 0.35` | 不透明 |

「位置過期」那一格差最多：圖例是一顆實心灰點，地圖上是一顆 35% 透明、疊在
彩色 OSM 圖磚上的灰點，兩者看起來不像同一種東西。`styles.css:958-962` 的註解說
「Deliberately desaturated rather than hidden: it is still a car」，但 35% 疊在
繁忙的地圖上（判斷）已接近「hidden」。
- **修法**：圖例色塊加上同樣的 `opacity`，或把標記的 `fill-opacity` 提到 0.55 並
  改用 `--text-dim` 的深一階（`--text` 60%）搭配白色外框，讓它在圖磚上仍有輪廓。

### L-5｜其他小項

**Low**

- `LoginPage.tsx:463` 的 `notAnAdminDialog()` 是空的匯出函式，`src/` 中 0 個引用。
- `en.ts:238-239` 的 `common.createdAt` 與 `common.createdDate` 值同為 `'Created'`，
  其一為冗餘。
- `dashboard.sub` 寫 `'Live platform status.'`，但儀表板是一次性讀取、沒有重新整理
  按鈕、也不會自動更新。「Live」在此不準確（`writing.md › Best practices`：
  「Every label says what happens」）。改為 `'Platform status at a glance.'` 之類即可。
- `--focus`（`#0969da`）同時是**鍵盤焦點環**與**地圖上「等待中」的車輛顏色**
  （`.livemap__pin--idle`）。同一色承載「這裡是焦點」與「這台車在等客人」兩種意思
  （`color.md › Best practices`）。建議把等待中的車改為中性色（`--text-dim`），
  把「載客中 vs 等待中」的差異改由**尺寸**承載 —— 程式已經這麼做了
  （`radius: running ? 8 : 6`），顏色可以退讓。

---

## 工藝觀察（Craft notes）

**有明確的觀點，而且貫徹到底。** 這個後台不是模板：它選了 GitHub Primer 的調色盤，
理由是「這是一個白底黑字的管理介面 —— 訂單清單、司機佇列、金額表格 —— 而 Primer
正是為這件事設計過最易讀的東西」（`styles.css:6-8`），然後把**結構**交給 iOS 的
Dynamic Type 比例尺。這是一個有理由的混血，不是隨手拼的。

**克制得很徹底。** 全檔只花一個彩色（品牌紅），其餘全部由灰階承載。
`.btn--danger` 是紅框而非紅色實心塊，註解明說「on a console that moves money, the
dangerous action should be legible but not the most inviting thing on the page」——
這句話本身就是設計觀點。`.card--alert` 只把邊框染紅 45%，`.message--error` 同理。
連 QR code 都考慮到「掃描器需要正確方向的對比，反轉的 QR 會被許多讀取器拒絕」，
所以在深色主題下仍保持白底並補上邊框（`:1027-1035`）。

**可以拿掉的一件配件（Remove one accessory）：** `.stat__value` 的 28px。
它與 `.page-head h1` 的 28px 同級，於是在儀表板上，四張卡片的數字與頁面標題
**一樣大**，`layout.md › Visual hierarchy` 要求的「層級」因此被壓平。
建議降到 24px，讓頁面標題仍是最大的一層。這是唯一一個我認為可以刪的東西 ——
其餘部分已經很精簡。

**文字品質是這個專案最強的部分。** `writing.md › Best practices` 要求
「Be action oriented」、「Write clear error messages」，這裡做到了少見的程度：
- `login.lockNote`：`'Five consecutive failures lock the account for 15 minutes.'`
  —— 說出**後果**，不是「請小心」。
- `login.footNote`：`'Sign-in credentials are held in this tab's session storage and
  expire when it closes.'` —— 說出憑證**存在哪裡、何時消失**。
- `search.sub`：`'… Searching leaves no audit entry; opening a detail page does.'`
  —— 主動揭露稽核邊界，這正是 `design-principles.md` 的 Responsibility。
- `live.paused`：`'Auto-refresh is off. What you see is the snapshot from when it was
  paused; use Refresh now to fetch again.'` —— 說明狀態**並給出下一步**。
- `live.emptyOnlineOnly`：`'No online driver has reported a position. Turn on Include
  offline to see everyone.'` —— 空狀態**邀請下一個動作**（`writing.md`）。
- `refunds.sub` 甚至預告了後果：「Approval is the only action that pays out, and it
  terminates the account.」

**沒有使用任何「Oops!」式的語氣**，錯誤訊息一律說明事實與修法。這是
`writing.md` 明文要求的，而多數介面做不到。

---

## 做得好的地方（What works）

以下是**應該在下次改版中保住**的具體做法，不是泛泛的讚美：

1. **主題是三個值，不是布林。** `theme.ts:1-19` 說明為什麼 `system` 是一個獨立的、
   與「永遠深色」不同的選擇，並把「跟隨系統」交給 CSS media query，讓 OS 切換時
   **不需要 JavaScript 就能重繪**。`index.html` 內嵌的 boot script 讓第一次繪製就是
   正確的主題，而 `theme.test.ts` 用 `new Function()` 真的執行那段腳本、斷言它與模組
   落在同一個屬性上 —— 這是防漂移的正確做法。
2. **`--seg-active` 是獨立 token，不是借用的 surface。** 註解記錄了「借用 surface
   會讓選中段比軌道更暗」這個已經踩過的坑。方向是對的（幅度見 M-1）。
3. **地圖不只有地圖。** `LiveMapPage.tsx:27-32` 明說「A map is not readable by a
   screen reader and cannot be sorted」，所以下方表格是**等價物而非重複**，
   而且排序刻意用**最舊優先**（`:222-236`），因為「需要被看見的是停止移動的那些車」。
4. **兩個時鐘分開。** `generated_at`（伺服器讀取時刻）與 `last_location_at`（每台車）
   分開顯示，並在超過三個輪詢週期時標為 stale —— 註解指出「不這樣做的話，十分鐘前
   停止回報的車會被畫得跟一秒前移動過的一模一樣，那是即時地圖最誤導人的事」。
5. **標記顏色寫在 CSS 而非 Leaflet options。** 這讓主題切換**零 JavaScript** 重繪，
   而且註解解釋了為什麼可行（Leaflet 把 `stroke`/`fill` 寫成 SVG presentation
   attribute，樣式表規則優先於它）。同理，用 `circleMarker` 而非 `marker` 是為了
   避開「打包後 PNG 圖示路徑失效、只在 production build 出現」的坑。
6. **`.heat` 的 24 格不換行。** `grid-template-columns: repeat(24, minmax(0, 1fr))`
   配 `min-width: 0`，讓格子平均縮小而非讓最後幾格溢出；註解說明「換行會破壞小時的
   由左至右閱讀」。這是把版面規則**寫成理由**，不只是寫成數值。
7. **`prefers-reduced-motion` 已處理**（`:849-851`），且是全域的。
8. **鍵盤焦點環是統一的** `:focus-visible`（`:222-226`），`.segmented__btn` 與
   `.th-sort` 各自微調 offset 而非重寫樣式。
9. **`aria-pressed` 用在所有切換型控件上**（14 處），`role="alert"` 只給失敗訊息
   （`primitives.tsx:104-105` 註解明說「a successful save should not interrupt a screen
   reader mid-sentence, a failure should」）—— 這正是 `feedback.md` 的分寸。
10. **`sr-only` 用得節制且正確**：分段控制的 `<legend>` 讓整組可被朗讀，
    同時不畫出設計不要的標題。

---

## 平台備註（Platform notes）

- **這是網頁應用，不是 macOS 原生應用。** 依 `SKILL.md` 的範圍說明，它取得的是
  **原則與基礎**（無障礙、色彩、字體、版面、文案），而**不套用** Apple 的平台慣例。
  以下三條因此**不算問題**，只是記錄：
  - 沒有選單列（`designing-for-macos.md` 要求每個指令都可從選單列觸達）——
    瀏覽器分頁應用沒有選單列，這不適用。
  - 側邊欄固定 248px 且不可拖曳、不可隱藏（`:232`）—— 桌面上通常期望可調，
    但這是一個單一工作流的內部工具，固定寬度換來版面穩定，是合理的取捨。
  - 視窗底部沒有關鍵資訊（`layout.md › Desktop (macOS)`）—— 已遵守，
    `.sidebar__foot` 放的是帳號與偏好設定，不是關鍵操作。
- **框架對譯**（`cross-platform.md`）：本專案的「tab bar」是側邊欄
  （`NavigationRail` 對譯）、「toolbar」是 `.page-head` 的 `.actions`、
  「system colors」是 `:root` 的自訂屬性、「Dynamic Type」是 `.t-*` 比例尺。
  這些對譯都在檔頭註解裡寫清楚了，是本專案比多數 React 專案嚴謹的地方。
- **`<select>` 的原生彈出層不受 CSS 控制**，只受 `color-scheme` 影響 ——
  這也是為什麼 H-1 不只是捲軸顏色的問題：`AnalyticsPage` 的四個篩選 `<select>`
  在「淺色系統 + `system` 主題」下會彈出**深色**清單。
- **建議的修復順序**（依 `SKILL.md` 的 Design improvement mode）：
  1. 無障礙 → **H-3、H-4、M-4**（對比與替代表示）
  2. 慣例 → **H-1、H-2**（外觀模式宣告、欄位樣式一致性）
  3. 工藝 → **M-1、M-2、M-3、M-6、M-7**
  4. 收尾 → **M-5、M-8、L-1 ~ L-5**

---

## 驗證方式

本報告的每一個對比數字都以 token 的十六進位值計算，計算是
`0.2126R + 0.7152G + 0.0722B` 的相對亮度後取 `(L1+0.05)/(L2+0.05)`，
`color-mix` 的結果以 sRGB 線性插值近似（與瀏覽器一致）。
「未使用的 class」是以**詞邊界**正則搜尋 `src/**/*.tsx` 與 `*.ts` 得出，
因此模板字串組出的名稱（`chip--${tone}`）已人工排除，不計入死碼。
「未使用的 input 型別」是以 `type="..."` 的實際出現次數統計。

未在此報告中查證的事項：真實瀏覽器的渲染結果（本環境無法啟動瀏覽器對
localhost 截圖）、螢幕閱讀器的實際朗讀順序、以及 HiDPI 下的 SVG 銳利度。
以上三項需要 `admin-web/tool/verify_ui.mjs` 在有登入憑證的環境下實跑。
