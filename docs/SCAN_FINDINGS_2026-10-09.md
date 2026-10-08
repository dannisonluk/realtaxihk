# realtaxihk — 全檔掃描 Findings（2026-10-09）

> 性質：living scan snapshot，唔係 archive。上一份 snapshot 喺
> `docs/archive/FULL_SCAN_2026-10-08.md`，歷史數字唔改。
> 今次係喺 transport hardening + Vite toolchain commit 之後重新逐檔核對。

## 掃描方式

- 將 repo 分成 backend API/core、backend models/services/migrations、tests/scripts、
  mobile core、mobile data、mobile models、mobile features/router/state、
  mobile platform/config/tool/fixtures、admin + CI/infra、docs/deploy 共 10 條線。
- 每條線只讀，先讀 code + 註解 + 對應 backend contract，再對比 comments 與實作；
  唔會未核對就改。
- 靜態閘已重跑：ruff、mypy、dart format/analyze、mobile 161 VM tests、
  admin tsc/vitest 97/build、WS targeted pytest 13 passed、
  `audit_response_models.py` 81 fixture blocks OK。
- Full backend pytest 於 commit 前重跑；結果以當次 output 為準。

## 已修正（今次 commit）

### 真 contract / 行為問題

| 檔案 | 問題 | 修正 |
|---|---|---|
| `app/api/ws.py` | native mobile 已改用 `Authorization: Bearer` header，但 server 只讀 `?token=` | server 加 `_ws_bearer_token()`，native header 優先，web query fallback；加 2 個 WS auth tests |
| `mobile/lib/core/network/wire.dart` | `asMoneyOrNull` 只接受 String，但 `Money.parse` 本身接受 num，遇到 num 會爆 TypeError | 接受 String/num，其他型別先 throw 有 field 名嘅 `MalformedResponseException` |
| `mobile/lib/core/storage/token_store.dart` | 用 `as String?` 硬 cast cached JSON，遇到 corrupt 值會逃出 `clear()` 路徑 | 改 `is! String` 檢查，corrupt cache 正常清空 |
| `mobile/lib/core/network/api_client.dart` | refresh body malformed 時 `MalformedResponseException` 會由 interceptor 漏出 | 補 `on Exception`，視為 retryable，保留 token 等下一次 retry |
| `mobile/lib/features/driver/driver_onboarding_screen.dart` | 前端車牌 regex 比 backend `min/max 4-8` 更嚴，會擋 Custom Registration Mark | 改為與 API 相同嘅 4-8 length check，plate 審查交畀 review queue |
| `mobile/lib/features/auth/phone_unlock_screen.dart` | UI 話「可更改號碼」但 overdue flow 用 `reverifyPhone` 只接受原號 | 改 copy：此步驟不接受其他號碼；「返回重新輸入」取代誤導性「更改號碼」 |
| `mobile/lib/core/theme/app_theme.dart` | 文檔講「互動文字不小於 11 pt」但 nav label 係 10 pt；tab bar 唔係 opaque | 兩處註解都補返 documented exception |
| `mobile/lib/core/security/password_policy.dart` | 無 NFC normalization 但未註明 | 補註解：server NFC 後 client 先 check，Dart stdlib 冇 NFC，server 先係 authority |
| `mobile/lib/core/format/money.dart` | duration 註解寫 `m:ss`、`hkd` 例子與 display 行為不符 | 兩處註解同步返實際輸出 |

### Doc / 計數 / 引用修正

- 全 repo living docs 嘅 pytest count 由 1295 → 1302；admin vitest 96 → 97；
  mobile README 153/149/63 → 161/161/64；`dart_check.py` 84 → 97。
- `app/api/admin.py` 已拆做 `app/api/admin/`，admin code/doc 引用一齊更新。
- `scripts/verify/audit_response_models.py` 不再讀 manifest.json，scripts README 同步。
- `gen_mobile_fixtures.py` 註解「四個 throwaway phones」→ 五個。
- `docs/IN_TRIP_REDESIGN.md` 註明 `preferred_origin_area` 目前只儲存/回傳，未做 start-area 匹配。
- `mobile/.metadata` 移除唔存在嘅 `ios/Runner.xcodeproj/project.pbxproj` unmanaged entry。
- `docs/MODULE_OVERVIEW.md` 新增，列出三端 module tree、功能、interaction chain、security boundary、未接外部服務。

## 掃描後仍存在（刻意未改）

- `mobile/lib/core/phone.dart` `hkPhoneDigits` 只有 VM test 用，未從 lib 呼叫；
  屬 public helper + test utility，未有破 harm。
- `preferred_origin_area` 已明確標成「存咗但未做 matching predicate」，功能缺口留在
  `docs/IN_TRIP_REDESIGN.md` 而非靜靜當佢做到。
- Production infra 未具備：VPS/TLS/SMTP/R2/backup/mobile release/deploy drill，
  仍係上線前人手項。

## 驗證閘（2026-10-09）

- backend：full pytest **1304 passed（16m07s）**；ruff/mypy/py_compile pass。
- mobile：`dart analyze` 0 issues、161 VM tests pass、format check 0 changed。
- admin：`tsc --noEmit` clean、97 vitest pass、vite build pass。
- WS：`tests/api/test_ws_module.py` 13 passed（含 header + query token）。
- `git diff --check` clean。
