# Admin 認證：模型、authenticator 選型、已知缺口 / Admin Auth

> **EN — Summary.** Answers a design question — *which free, open-source
> authenticator to recommend for standard-TOTP compatibility* — and then
> documents how admin authentication **actually behaves**, including its known
> gaps, so it can be handed over.
>
> **The two facts to take away**: the time-step arithmetic is verified against
> the published RFC 6238 / 4226 test vectors — evidence lives in
> `tests/test_totp.py::TestRfc6238Vectors`, not in this document — because an
> off-by-one in the time step is *silent*: the code still returns 6 digits, just
> the wrong ones. And admin tokens live in HttpOnly refresh cookies with CSRF
> double-submit, **not** in localStorage (see `app/core/admin_cookies.py`).
>
> **中文摘要**：回答一個選型問題（推薦哪個免費、開源、兼容標準 TOTP 的
> authenticator），然後把 admin 認證的**實際行為**（含已知缺口）寫清楚以便交接。
> 兩個要帶走的事實：時間步長算法對 RFC 6238 / 4226 官方向量驗證過（證據在
> `tests/test_totp.py::TestRfc6238Vectors`，**不在本文**）——
> 因為時間步長差一格是**靜默**的：照樣回 6 位數，只是錯的；
> admin token 放 **HttpOnly refresh cookie + CSRF double-submit**
> （見 `app/core/admin_cookies.py`），**不是** localStorage。

本文回答 P-1 遺留的問題：**「推薦一個免費、open source、兼容標準 TOTP app 的 authenticator 方案」**，
並把 admin 認證的實際行為寫清楚，方便日後交接。

實作位置：`app/core/totp.py`、`app/services/admin/admin_auth_service.py`、`app/api/admin_auth.py`。

---

## 1. 認證模型

Admin 與乘客／司機是**兩套完全分開的認證路徑**：

| | 乘客 / 司機 | Admin / Management |
|---|---|---|
| 身分表 | `users` | `admin_accounts` |
| 主要憑證 | 手機號 + OTP | username + password（另需 email） |
| 第二因素 | 無 | **強制** TOTP（6 位數字） |
| Router | `app/api/auth.py` | `app/api/admin_auth.py` |

分成兩個 router 是刻意的：共用一個 router 就要在每個 handler 加 `role` 分支，
而那正是「乘客請求走進 admin 認證路徑」的成因。

### 三步登入狀態機

1. `POST /api/v1/admin/auth/login` — username + password。
   **永遠不直接回 access token**，只回一張 5 分鐘有效的 *challenge* token。
2. `POST /api/v1/admin/auth/totp/verify` — 6 位 TOTP 碼；
   或 `POST /api/v1/admin/auth/recovery` — 用 recovery code。
3. 第 2 步才發出 access token。

challenge token 之所以重要：`principal_from_token` 要求同時有 `sub` 和 `role`，
而 challenge 兩者都沒有。所以即使它洩漏到 `Authorization` header，也**結構上**無法當 access token 用
——不是靠一個日後可能被「優化」掉的檢查。

### Enrolment 的防鎖死設計

`POST /totp/enrol` 回傳 secret、`otpauth://` URI、以及 8 個 recovery code，
但**不會**立刻寫進帳號。pending secret 先放在 Redis（TTL 5 分鐘），
直到 admin 用該 secret 產生一次有效的碼、經 `POST /totp/enrol/confirm` 確認後才持久化。

先存再顯示 QR 是經典鎖死 bug：secret 已入庫，admin 未掃碼就關掉分頁，
帳號從此要求一個沒有人持有的第二因素。

---

## 2. 推薦的 authenticator

**首選：[Ente Auth](https://ente.io/auth/)**

- **Open source**：App 端 GPL-3.0，server 端 AGPL-3.0，程式碼在 GitHub 公開。
- **免費**，沒有付費牆擋住 TOTP。
- **全平台**：Android、iOS、Windows、macOS、Linux、Web。
  Admin 團隊用甚麼裝置都有，這是唯一一個不用妥協的選項。
- **可選 E2E 加密同步**：換手機不必重新逐個 enrol；不想同步也可以只用本機。
- 匯入／匯出標準 `otpauth://`，日後要換 app 不會被鎖住。

**如果政策不允許雲端同步：**

- Android → **[Aegis Authenticator](https://getaegis.app/)**（GPL-3.0）。
  預設完全離線，加密備份，是 Android 上最完整的 open source 選擇。
- iOS → **[Tofu](https://github.com/iKenndac/Tofu)**（MIT）。**但要注意**：原作者公開表示近年
  投入時間有限、改進不多，目前由 `iKenndac` 接手維護，屬於「能用但發展緩慢」。
  如果 iOS 要的是活躍維護的離線選項，這一格目前沒有跟 Aegis 同等級的答案，
  實務上會直接用 Ente Auth（它本身覆蓋 iOS，只是同步功能預設關掉就等於離線）。

換句話說：**Ente Auth 是唯一一個不需要在「平台覆蓋」和「open source」之間取捨的選擇**，
這正是它被列為首選的原因，而不是因為它在任何單一維度上最強。

**如果是自架環境：** [Vaultwarden](https://github.com/dani-garcia/vaultwarden)（Bitwarden 相容的
open source server）配上任何支援 TOTP 的 client，等於密碼與第二因素同一套自管。

**不推薦 Authy**：協定上相容，但**不是 open source**，而且它的桌面版已停用，等於把第二因素
綁死在單一商業雲。`app/core/totp.py` 的 docstring 把它列在相容清單裡，指的是協定相容，
不代表推薦。

### 為甚麼全部都可以用：參數就是 RFC 預設值

我們發出的 `otpauth://` URI 是：

```
otpauth://totp/RealTaxi%20HK:<username>?secret=<base32>&issuer=RealTaxi+HK&digits=6&period=30
```

| 參數 | 值 | 為甚麼 |
|---|---|---|
| algorithm | SHA-1（預設，URI 中刻意省略） | RFC 6238 的互通預設；Google Authenticator 的 TOTP 只支援 SHA-1。SHA-1 的碰撞弱點對 HMAC 無意義（HMAC 只需 PRF 安全性）。 |
| digits | 6 | RFC 預設，所有 app 都假設這個值。 |
| period | 30 秒 | 同上。 |
| secret | 20 bytes / 160-bit base32，無 padding | RFC 4226 §4 對 SHA-1 的建議長度。 |

驗證端接受前後各一個 step（`valid_window=1`），容忍時鐘漂移與「打到一半剛好換碼」，
並用 `hmac.compare_digest` 做 constant-time 比較（naive `==` 會洩漏前幾位是否正確，
對 6 位空間來說足以破防）。

`valid_window=1` 把暴力破解空間由 1-in-10⁶ 擴到 3-in-10⁶，
所以 verify endpoint **必須**限流、而且每個碼只能用一次（`last_used_counter` 防重放）。
這是實作上已處理的，不是待辦事項。

---

## 3. 已知缺口

1. ✅ **Console 沒有渲染 QR code —— 已完成。**
   `LoginPage.tsx` 原本只把 `otpauth_uri` 當純文字顯示，admin 要手動把 secret 打進
   app，或自己把 URI 丟進外部 QR 產生器（後者會把 secret 交給第三方）。
   現已加入 `qrcode.react`（零 runtime 依賴、ISC license）在 **client 端**渲染 SVG，
   secret 不經過任何外部服務。二維碼下方仍保留 secret 與 URI 文字，供無法掃碼時手動輸入。
   驗證方式：Playwright 截圖（明暗兩主題）＋ **獨立解碼器**讀回像素，
   解出的正是伺服器送出的 `otpauth_uri`。
2. **Secret 以明文顯示在畫面上。** 這是 enrol 階段的必要之惡（admin 必須看到才能輸入），
   但意味著 enrol 畫面不應被截圖或錄屏分享。已用 5 分鐘 TTL 限制暴露窗口。
3. **TOTP 的時鐘漂移沒有自助校正。** 如果某台裝置時鐘偏差超過 ±30 秒，該 admin 只能用
   recovery code 登入，再由另一個 admin 重設。可考慮在 `totp/verify` 失敗時回傳
   server 時間提示。
4. **Recovery code 用掉後不會自動補發。** 8 個用完就沒有了，需要人工重設。

第 1 點（最影響首次登入體驗）已解決；其餘三點不阻塞上線。
