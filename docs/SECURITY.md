# 安全模型與加固指南（Security Guide）

- **日期**：2026-10-01
- **狀態**：基於一次完整的安全審計（實跑驗證，非閱讀推斷）
- **範圍**：後端 API、管理後台 console、認證路徑、秘密管理

> 本文分三部分：**已驗證的安全控制**（實測通過）、**已修復的發現**（附嚴重度；
> **加固路線圖**。凡標「實測」者，都是用真實進程 / RFC 向量 / 隨機取樣跑出來的結論，
> 不是讀 code 的推測。

---

## 1. 已驗證的安全控制

以下每一項都是**實跑驗證**過的，不是設計意圖。

### 1.1 TOTP 實作正確（RFC 級驗證）

| 驗證 | 方法 | 結果 |
|---|---|---|
| RFC 6238 Appendix B 全部 8 位數向量 | 6 組官方測試向量 | **全部通過**（`94287082`、`07081804`、`14050471`、`89005924`、`69279037`、`65353130`） |
| RFC 4226 Appendix D 全部 6 位數向量 | 10 組官方測試向量 | **全部通過** |
| 亂數來源 | 原始碼 | `secrets.token_bytes`（CSPRNG） |
| 比較 | 原始碼 | `hmac.compare_digest`（constant-time） |
| 時序側通道 | 原始碼 | 完整走訪 window，**無提前 return** |
| 密鑰長度下限 | `normalize_secret` | 拒絕 < 10 bytes |

**結論**：TOTP 實作在協定層面無可攻擊面。這是審計中最乾淨的一塊。

### 1.2 Challenge token 結構上無法當 access token（實測）

管理員登入第一步只回 **challenge token**。用真密鑰簽發的 challenge 丟進
`principal_from_token` → **401**。

原因不是「有一個檢查會拒絕」，而是 **challenge 根本沒有 `sub` 與 `role` claim**。
這是結構性隔離，不會因為日後有人「優化」程式碼而失效。

### 1.3 Scope 提權在結構上不可行（實測）

`app/core/deps.py:73` 的判定是：

```python
scope = SCOPE_ADMIN if claims.get("scope") == SCOPE_ADMIN else SCOPE_USER
```

實測 `"ADMIN"`、`"Admin"`、`" admin"`、`"admin "`、`123`、`None`、`"true"`
**全部**得到 `is_admin=False`。只有**精確字串** `"admin"` 才提權。

**注意**：這是*允許清單*（allow-list），不是拒絕清單 —— 這個方向是對的。
但它同時意味著**任何型別轉換或 strip 都不存在**，這是刻意的。

### 1.4 秘密管理：乾淨

| 檢查 | 方法 | 結果 |
|---|---|---|
| `.env` 是否曾進 git | `git log --all -- .env` | **從未**。`.gitignore` 第 8 行 |
| 有無硬編碼金鑰 | 全 repo 掃描 | 只有**測試 fixture** 與**探針字串**，全部標記 `# noqa: S105 — drill fixture, not a credential` |
| 唯一可疑字串追溯 | 完整 git history | `dev-only-secret-change-in-prod-...` 是**開發佔位符**，於 `1b55c0f` 加入 `.env.example`，於 `417b754`（SEC-06/19–22/31 加固）**移除**並改為空值 |
| prod 是否會拒該字串 | `config.py:218-223` | **會**。`startswith("dev-only")` 與 `_KNOWN_DEV_SECRETS` 雙重拒絕 |

### 1.5 Prod fail-closed 驗證器（`config.py:197-263`）

啟動時即拒絕（非執行期）：

- `APP_ENV` 未設 / 非 `dev|test|prod` 白名單 → 拒（`production`、`PROD`、`staging`、`prod ` 全部拒）
- `JWT_SECRET_KEY` = 已提交的 dev 值 → 拒
- `POSTGRES_PASSWORD` = `change-me-dev` → 拒
- `ALLOW_DEV_OTP=1` 且 prod → 拒
- `SMTP_HOST` / `SMTP_FROM` 缺失 → 拒
- `PUBLIC_BASE_URL` 非 `https://` → 拒
- `JWT_SECRET_KEY` < 32 字元，或**不同字元 < 8 個** → 拒（擋 `"x"*64` 這類無熵長字串）

### 1.6 XSS 面：零 sink

- Console 全樹掃描：**無** `dangerouslySetInnerHTML`、`innerHTML`、`eval`、`new Function`
- 所有伺服器資料經 React 自動轉義
- `index.html` 有 `referrer: no-referrer` 與 `robots: noindex, nofollow`

---

## 2. 已修復的發現

### ✅ SEV-1（已修復）：管理員 session 在 15 分鐘後硬死，無法續期

**原位置**：`admin-web/web/src/api/client.ts:259-300`（`refresh()`）
＋ `admin-web/web/src/api/types.ts:63-67`（`AdminSession`）
＋ `app/api/admin_auth.py`（全檔無 `refresh`）

**原現象**：管理員存取權杖 15 分鐘後過期，console 想自動續期，但：

1. `AdminSession` **只有** `access_token`，**沒有** `refresh_token` 欄位。
2. `admin_auth.py` **完全沒有**發 refresh token 的路徑（全檔無 "refresh" 字樣）。
3. `LoginPage.tsx` 存 session 時硬寫 `refreshToken: ''`。
4. `client.ts`：`const refreshToken = session.refreshToken; if (!refreshToken) return false;`
5. refresh 失敗 → `this.onSessionExpired()`
6. `AppContext.tsx`：`onSessionExpired` → `session.clear(); onSignedOut()`（**直接踢回登入頁**）

**原影響**：管理員做到一半（例如填了一半的退款備註、正在審一批 KYC）**突然被登出**，
表單狀態全失。以「每天用的後台」而言這是最刺眼的缺陷。

**已採用的修法：選項 (c) —— `HttpOnly` refresh cookie**

原文件列出 (a)/(b)/(c) 三個選項，最終採用 **(c) 最徹底** 的方案：

1. **Refresh token 改放在 `HttpOnly; Secure; SameSite=Strict` cookie**，
   path 限定 `/api/v1/admin/auth`，**JavaScript 完全讀不到** —— 這樣即使有 XSS，
   也無法偷走 refresh token（對比 (b) 把 token 放 JS 可及的 body/state）。
2. **CSRF double-submit 防護**：另設一個**非** `HttpOnly` 的 cookie 帶 CSRF token，
   client 讀出來放進 `X-CSRF-Token` request header；`SameSite=Strict` 本身已擋大部分
   跨站請求，double-submit 是第二道。
3. `admin_auth.py` 新增 `POST /api/v1/admin/auth/refresh`（輪換）與
   `POST /api/v1/admin/auth/logout`（撤銷 + 清除 cookie）。
4. `AdminSession` 回應**不再**帶 refresh token（瀏覽器自動存 cookie，script 無需碰）。
5. **登出／撤銷後立即清除 cookie**：`clear_session_cookie_headers()` 產生兩個
   `Set-Cookie`（各帶 `Max-Age=0`）經 `HTTPException(headers=...)` 送出。

**驗證**：`tests/test_admin_session_cookie.py`（24 tests）—— 涵蓋輪換、
重放偵測、撤銷、停用帳戶時清除 cookie、logout 對已停用帳戶仍可用等。
全 656 tests 通過。

> **踩過的坑（值得記住）**：
> 1. `app/core/exceptions.py` 的 handler 會**丟棄注入的 `Response` 物件**，
>    自行建一個新的 `JSONResponse` —— 所以 `clear_session_cookies(response)`
>    後才 `raise` 是**無效的**。修法：`clear_session_cookie_headers()` 回傳
>    `{"set-cookie": [...]}` 交給 `HTTPException(headers=...)`。
> 2. **`Set-Cookie` 不能用逗號摺疊**：`expires` 欄位本身含逗號
>    （`Thu, 01 Jan 1970`），客戶端若照逗號拆會把兩個 cookie 都弄壞。
>    handler 必須**逐一** append `(b"set-cookie", value)`。
> 3. 路由表的 live-state guard 審計只讀**宣告的** dependencies，
>    handler body 內的 guard 看不見 —— 需要一個 no-op dependency
>    （`require_live_admin_refresh_session`）把它顯式化。

---

### ✅ SEV-2（已修復）：HTTP 狀態碼改由**型別**決定，不再比對訊息字串

**原位置**：`app/api/admin_auth.py:118`（修復前）

```python
status = 429 if "too many" in str(exc) else 401
```

**問題**：HTTP 狀態碼由**字串比對錯誤訊息**決定。目前剛好正確（所有
`"too many attempts — try again later"` 都是真的 429），但**改一個訊息字串就會
靜默改變安全相關的狀態碼**。這是一條脆弱耦合，不是當下的錯誤。

**已完成的修復**：
1. `admin_auth_service.py` 新增 `AdminAuthThrottled(AdminAuthError)` 與
   `AdminAccountLocked(AdminAuthThrottled)` 兩個型別。
2. 4 處「too many attempts」改拋 `AdminAuthThrottled`；
   2 處「account temporarily locked」改拋 `AdminAccountLocked`。
3. `admin_auth.py::_run` 改為 `except AdminAccountLocked → 401`、
   `except AdminAuthThrottled → 429`、`except AdminAuthError → 401`。
   **用型別分派，訊息可以自由改。**

**外部可觀察行為完全不變**（35 個 admin auth 測試全過）。

#### ⚠️ 過程中的一個重要修正：原本建議「locked 改回 429」是**錯的**

我一開始建議把 locked 帳號由 401 改成 429，理由是「無法區分鎖定與打錯密碼」。
**改了之後有兩個測試失敗**，而它們證明了這個建議是錯的：

```python
def test_lockout_does_not_expose_is_active(client):
    """A brute-force lock must be indistinguishable from a ban at the API edge."""
    assert locked.status_code == 401
```

**這是一個刻意的安全設計**：若 locked 回 429 而打錯密碼回 401，
攻擊者就能藉此**區分「這個 username 存在且已被鎖」與「我只是被限流」**，
等於確認帳號存在。**429 是端點層的拒絕，401 是帳號層的拒絕 —— 兩者不應在
wire 上可分。** 因此修復後 locked 仍回 401，差異只保留在 log 裡。

> 教訓：審計時看到「401 與 429 混用」很容易判為缺陷，但先讀測試再下結論。
> 這裡的測試 docstring 寫得很清楚，是**測試**救了這個改動。

---

### ✅ SEV-4b（已修復）：弱斷言改為可證偽

**原位置**：`tests/test_admin_auth_api.py:674`

```python
assert 429 in statuses or 401 in statuses   # 舊
assert statuses[-1] in (401, 429)           # 舊
```

**問題**：`or` 令斷言在任何「有 401」的執行中都成立 —— 即使限流器**完全壞掉**
（永遠不回 429）也照過。原註釋理由（「兩者都可能先觸發」）是對的，但斷言寫得太弱。

**已修復**：現在 401/429 由型別決定，預期結果可確定，故改為
`assert statuses[-1] == 429`（並附上實際序列於失敗訊息中）。實測通過。

---

### 🟡 SEV-3：顯示層 round-mode 不一致（未修）

**位置**：`app/services/analytics_service.py:141`、`:191`、`:224`

```python
return str((Decimal(numerator) / denominator).quantize(Decimal("0.01")))
```

`Decimal.quantize` **預設是 ROUND_HALF_EVEN（銀行家捨入）**，不是商業慣用的
ROUND_HALF_UP。全專案的金額路徑（`app/core/money.py:42,52`、`fare_calculator.py:288`）
都**明確**帶 `rounding=ROUND_HALF_UP`，只有這三處漏了。

**實測差異**：`1/200 → 0.00`（應 0.01）、`1/8 → 0.12`（應 0.13）、`1.005 → 1.00`（應 1.01）。

**影響**：**僅顯示層**。這些是衍生比率與距離統計，**不是儲存的金額**，
所以不影響帳目正確性。但一個「統計數字偶爾差一分」的後台會侵蝕信任。

**為何未順手改**：需要先確認「統計比率該不該用商業捨入」是產品決定，不是純技術
選擇（會計上 HALF_EVEN 反而更中性）。**建議你決定後再改**，改法是這三處補
`rounding=ROUND_HALF_UP`，或統一在 `money.py` 開一個 `ratio_str()`。

---

### ✅ SEV-4a / 4c（已修復）：死碼與文件不實

| 位置 | 問題 | 處置 |
|---|---|---|
| `admin-web/web/src/pages/AnalyticsPage.tsx:466` | `fill={isHovered ? 'var(--brand)' : 'var(--brand)'}` — 三元運算**兩支相同**（死碼） | **已修**：改為 `fill="var(--brand)"`（hover 效果本來就由 `opacity` 表達，行為不變） |
| `app/core/hk_bounds.py` docstring | 寫「**Nine** polygons」，實際 `len(HK_POLYGONS)` = **8** | **已修**：改為 Eight |
| `app/core/hk_bounds.py` 註釋 | 把 Sha Tau Kok 列為「在港側」的座標之一，但該精確座標是**多邊形頂點**，ray-cast 回 False | **已修**：加註說明頂點是邊界特例，並記錄實測結果（真鎮回 True、測試座標回 True） |

---

### 🟡 SEV-5：HK 邊界的頂點特例（潛在，非現實 bug）

**位置**：`app/core/hk_bounds.py` 的 ray-casting

半開區間 `(yi > y) != (yj > y)` 令**頂點本身**的行為依賴遍歷方向。
實測：16 個鄰近取樣中，**只有** Sha Tau Kok 的精確頂點（及一個相鄰角）回 False，
**真鎮**正確回 True，測試用的 `(22.5430, 114.2130)` 亦回 True。

**已驗證無誤的性質**：
- bbox 是**取樣超集**：32,000 個隨機內部點，**0 個**被 bbox 拒絕
- 全部 7 個深圳點拒絕（福田、羅湖、寶安、南山、市民中心、華強北、鹽田港）
- 全部香港地標接受
- `NaN` / `inf` / `-inf` / `1e308` / subnormal 全部正確拒絕（含 `1e400 → inf` 溢出路徑）

**建議**：文件註明頂點是邊界不確定區；若要嚴格，改用 `contains()` 前先對頂點做
epsilon 內縮。

---

## 3. 加固路線圖

### 立即（上線前）

1. ✅ **修 SEV-1 —— 已完成**（採用選項 (c)：`HttpOnly` refresh cookie +
   CSRF double-submit；`/admin/auth/refresh` 與 `/admin/auth/logout` 已通）。
   詳見上文 SEV-1 條目與 `tests/test_admin_session_cookie.py`。
2. ✅ **修 SEV-2 —— 已完成**（型別化 exception；locked 維持 401，見上文說明）。
3. ✅ **`ruff format` 全樹套用 —— 已完成**（109 files already formatted；CI 已加
   `ruff format --check` gate 防復發）。

### 短期

4. ✅ **Console TOTP QR 渲染 —— 已完成**（見 `ADMIN_AUTH.md` 缺口 #1）。
   採用 `qrcode.react`（零 runtime 依賴、ISC）在本機渲染 SVG，secret **不經過**
   任何第三方服務。已用獨立解碼器驗證：截圖內解出的正是伺服器送出的
   `otpauth_uri`（明暗兩主題皆通過）。
5. ✅ **`/auth/refresh` 的 admin 路徑 —— 已完成**（`/api/v1/admin/auth/refresh`）。
   console 原本那段永遠失敗的 refresh 邏輯現在有真實後端支撐。

### 中期

6. ✅ **`HttpOnly` cookie refresh token —— 已完成**（`session.ts` 的設計已落地；
   見上文 SEV-1）。
7. **多 instance jobs 去重**（見 `DEPLOYMENT_REQUIREMENTS.md` §5 #1）。
8. **WAF / rate-limit 邊緣層** —— 目前限流在應用層，`TRUSTED_PROXY_COUNT=1` 是前提。
9. **`alembic downgrade` 演練** —— 回滾路徑未驗證。

---

## 4. 一句話總結

> 認證的核心（TOTP、challenge token、scope 隔離、秘密管理）**實測全部正確**，
> 是這個專案最紮實的部分。原本最刺眼的兩個營運斷點 —— 管理員 15 分鐘被登出
> （SEV-1）與鎖定狀態不可辨（SEV-2）—— **均已修復並有測試背書**。餘下的是
> 顯示精度、死碼與測試品質，以及部署層（中期）的事項。

---

## 相關文件

- `ADMIN_AUTH.md` —— 管理員認證模型與 authenticator 選型
- `DEPLOYMENT_REQUIREMENTS.md` —— 部署需求與 gap list
- `WORK_SUMMARY.md` —— 整體工作總覽
- 驗證方法：`scripts/security_verify.py`、`tests/test_security_hardening.py`
