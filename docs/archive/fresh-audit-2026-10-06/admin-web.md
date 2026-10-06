# Fresh Audit — React Admin Console (`admin-web/web/src`)

Date: 2026-10-06. Auditor: independent deep-dive subagent. Basis: actual TS/TSX source, tests, `package.json`, `tsconfig.json`, `vite.config.ts`, plus real `npm run typecheck` (passes) and `npx vitest run --no-file-parallelism --pool=forks` (12 files / 87 tests pass) run in this session. No prior audit file, deliverable, or handoff note was consulted.

## (a) Executive summary (5 lines)

- The admin console is unusually disciplined for its size: a single `useLoad` hook with a cancellation flag guards every list/detail page against stale overwrites, `inOrder` exists to avoid parallel-hang hazards, and all 87 tests plus a strict `tsc` pass cleanly in this session.
- The settlement flow is the strongest part of the app: preview issues a server-bound `confirm_token`, the run passes period+token together, `busyRef` + `disabled` protect against double-run, and editing the period invalidates the preview — no identity gap found.
- The modal in `primitives.tsx` implements a real focus trap (wrap-around on Tab/Shift+Tab) and focus restore to the opener; it lacks only background inertness and an `aria-describedby`, both minor.
- The main concrete defects found are cosmetic-to-medium: a KYC list that renders stale rows while loading/error, a fleet-create note field whose placeholder is the contact-name key ("Optional/選填" instead of a note hint), an error state without retry in two detail panels, and a server-supplied `download_url` rendered as an anchor without protocol validation.
- No XSS sink (`dangerouslySetInnerHTML`), no unmount `setState` warning pattern, no fetch race / stale-overwrite path, and no double-submit hole on destructive actions were found; i18n key parity is enforced at compile time and by a runtime test.

## (b) Findings table

| ID | Severity | Title | file:line | Evidence | Concrete fix |
|----|----------|-------|-----------|----------|--------------|
| F1 | Medium | KYC list shows stale rows while a new filter is loading, and keeps them beside the error state on failure | `src/pages/KycPage.tsx:217-220` | `{loading ? <LoadingState /> : null}` then `{error ? <ErrorState …/> : null}` then `{data ? (…table…) : null}` — `data` is never cleared by `useLoad` (src/app/useLoad.ts:36,49), so on filter switch the previous status list stays visible under the spinner, and on error the old list renders next to the error card. | Gate the table on `!loading && !error && data` like OrdersPage:159 / RefundsPage:130 / DisputesPage:174; or have `useLoad` clear data when a new fetch starts. |
| F2 | Low | Fleet note textarea placeholder reuses the contact-name key → shows "Optional / 選填" on the note field | `src/pages/FleetsPage.tsx:245-250`; keys `src/i18n/locales/en.ts:553`, `src/i18n/locales/zh-Hant.ts:559` | `placeholder={t('fleets.fieldContactPlaceholder')}` on the `<textarea name=note>`; no `fleets.fieldNotePlaceholder` key exists in either locale. Copy-paste artefact. | Add `fieldNotePlaceholder` to both locales (test suite enforces parity) and use it at line 249. |
| F3 | Low | Two detail panels render `ErrorState` without a retry path | `src/pages/LicencePage.tsx:248-249` (SubmissionDetail per row) and `src/pages/FleetDetailPage.tsx:556-557` (roster) | `if (error) return <ErrorState error={error} />;` — no `onRetry`, and the `useLoad` result's `reload` is not destructured (LicencePage:243, FleetDetailPage:551). Only way back is reopening the row / reloading the page. | Destructure `reload` and pass `onRetry={reload}`; or expose the retry from the parent's reload. |
| F4 | Low | Server-supplied document URL is rendered as a bare anchor without protocol validation | `src/pages/LicencePage.tsx:330-338` | `<a href={doc.download_url} target="_blank" rel="noreferrer">` — `download_url` comes from the API for each stored doc. React does not sanitise `href`; a `javascript:`/`data:` value would execute on click. (Low because the value is server-controlled and only reachable by an authenticated admin.) | Validate on render: only render the anchor when `new URL(doc.download_url).protocol` is `http:`/`https:`, else render a disabled notice; belt-and-braces for a backend compromise. |
| F5 | Low | Table header cells lack `scope="col"` on every list page | e.g. `src/pages/OrdersPage.tsx:170-178`, `src/pages/KycPage.tsx:230-235`, `src/pages/SearchPage.tsx:170-176`, `src/pages/AnalyticsPage.tsx:331-357` | Plain `<th>{t(…)}</th>` everywhere; only the analytics `HourTable` uses `scope="col"` (AnalyticsPage.tsx:638-647). Screen readers must infer direction from document order. | Add `scope="col"` to the first row of every `<thead>`; keep `aria-sort` (already present) on sortable columns. |
| F6 | Low | In-flight page changes on Orders/Audit can be raced by quick successive pagination clicks | `src/pages/OrdersPage.tsx:198-213` (AuditPage.tsx:141-146 similar) | Prev/Next buttons only disable on `offset===0` / `!hasMore`, not on `loading`. `useLoad` does drop the stale response, so the result is only a skipped query, not stale data. | `disabled={loading || offset === 0}` etc., matching the Settlement page's `disabled={busy}` pattern. |
| F7 | Low | Analytics renders stale data and the error card simultaneously on reload failure | `src/pages/AnalyticsPage.tsx:240-244, 246-270, 321-326` | Error branch and data branch are independent (`{summary.error ? …} {summary.data ? …}`); `useLoad` keeps old data after a failed reload, so totals/table remain next to the ErrorState. | Render data only when `!summary.error`, or clear data on error; KYC-style single-source-of-truth for load state. |
| F8 | Info | `useLoad` has no `AbortController`; cancelled fetches still occupy a socket until completion | `src/app/useLoad.ts:46-71`, `src/api/client.ts:214,277` | Cancellation is a boolean flag; the underlying `fetch` is never aborted, so a superseded slow request keeps its connection open. Written up in useLoad.ts:29-33 as the deliberate race guard — correct, but a network-level optimisation opportunity. | Pass an `AbortSignal` derived from the effect's cleanup (and abort via `busyRef`-style guard) to `endpoints.…`/`fetch` when the transport supports it. |

## (c) What is already well done

- **Async discipline**: every list/detail page funnels through `useLoad` with a per-effect `cancelled` flag (useLoad.ts:46-66) — no stale overwrite path found anywhere. `inOrder` (useLoad.ts:82-90) is used by the dashboard (DashboardPage.tsx:36) and is documentedly preferred over `Promise.all`. Search has a sequence-token guard (SearchPage.tsx:81-95); LiveMap's poll is guarded by a locally-scoped `busy` flag that is StrictMode-safe (LiveMapPage.tsx:131-168), pauses on hidden tabs, and keeps the last snapshot on failure.
- **Settlement flow (lead #3 — verified good)**: preview issues a server `confirm_token` with expiry (SettlementPage.tsx:77-81); run sends `period + confirmToken` together (122-125); a `busyRef` guard plus `disabled={busy}` on preview/export/run buttons prevents double-run (70-72, 117-119, 219-224, 298-301); editing the period invalidates preview/result/error (203-211); export revokes its object URL in `finally` (158-168); partial failures are surfaced (failed/tampered counters, 178, 254-269).
- **Destructive actions** all confirm first and report failure: account deactivate via `useConfirmDialog` (AccountsPage.tsx:185-195), reset-password and refund/KYC decide via `useFormDialog` whose submit button is `disabled={busy}` and keeps the dialog open + shows the error on throw (useDialogs.tsx:46-58, 119-131).
- **Modal (lead #2 — verified good)**: `role="dialog"` + `aria-modal`, focus moved in on open and restored to the opener on close (primitives.tsx:152-173), Tab/Shift+Tab trap with wrap-around (189-214), Escape and backdrop close (178-192), each instance gets its own effect.
- **i18n**: `en` is type-checked against `zh-Hant` (`satisfies`), runtime key-parity + empty-leaf + CJK-in-en + placeholder-parity tests (i18n.test.ts:44-110), 87/87 tests green.
- **Error surfacing**: `ErrorState` with `onRetry` on every main list fetch path (Orders/Kyc/Refunds/Fleets/Licence/Disputes/Destinations/Audit/Search/Analytics/settlement), toasts with `role="alert"` vs `role="status"` (ToastStack.tsx:25), error hints for rate-limit/service/network (useLoad.ts:106-123).
- **XSS surface is closed**: zero `dangerouslySetInnerHTML` in app code (i18n sets `escapeValue:false` deliberately, primitives.tsx:7-8, i18n/index.ts:96-99); all dynamic text is rendered as text nodes.
- **AccountsPage (lead #1 — verified clean)**: fetched list is never mutated — role/active changes call the API then `reload()` (AccountsPage.tsx:68-87, 189-207); dialogs use local mutable form objects (89-114), and role buttons + deactivate disable while `pendingId === account.id` (315, 343-347). Last-super-admin demote/deactivate blocking is enforced client- and server-side (263-269, 334-339).

## (d) Async correctness (races, aborts, unmount, double-submit)

- Fetch races / stale overwrites: **none found**. `useLoad` and `useLiveSnapshot` both discard superseded responses via per-effect flags (useLoad.ts:53-61; LiveMapPage.tsx:156-167); Search uses a token (`seq.current`, SearchPage.tsx:81-95); Analytics keys its two requests on primitive arrays so object identity churn cannot re-fire (AnalyticsPage.tsx:117-133, comment at 118-121).
- AbortController: **not used anywhere** (single reference is the file header). This is the one real async gap — see F8. Cost is only network-socket hygiene; correctness is preserved by the flags.
- setState-after-unmount warnings: none — every async block checks its flag before `setState` (useLoad.ts:54-61, LiveMapPage.tsx:156-167, SearchPage.tsx:87-95).
- Double-submit: dialogs disable their buttons while busy (useDialogs.tsx:66-76, 139-149); Settlement uses `busyRef` **and** `disabled={busy}` (SettlementPage.tsx:70-72, 117-119, 219-224, 298-301); Accounts role buttons disable per-row (AccountsPage.tsx:315). Minor: Orders/Audit pagination buttons are not disabled during `loading` (F6), and the Accounts "reset password" row button is not disabled while another account's request is pending — but each dialog's own busy guard prevents double-submit of the destructive action itself.

## (e) Error handling

- All primary fetch paths surface `ErrorState` with `onRetry`: OrdersPage.tsx:157, KycPage.tsx:218, RefundsPage.tsx:128, FleetsPage.tsx:122, LicencePage.tsx:118, DisputesPage.tsx:172, DestinationsPage.tsx:163, AuditPage.tsx:108, SearchPage.tsx:134 (+ retry via `retryNonce`), AnalyticsPage.tsx:242/286, SettlementPage.tsx:230-234, DashboardPage.tsx:74. Two per-row detail loads omit retry (F3).
- Settlement run failure: caught, normalised, shown in `ErrorState`, token expiry handled explicitly with `errTokenExpired` and preview cleared (SettlementPage.tsx:112-116, 130-132). Refund/KYC/disable failures: dialog stays open, error shown in `Message tone="error"`, nothing silently swallowed (useDialogs.tsx:53-58, 126-131).
- Nonce/retry: `useLoad.reload` bumps a nonce (useLoad.ts:70); Search's retry nonce is documented against the `setQuery((q)=>q)` anti-pattern (SearchPage.tsx:60-69).
- Best-effort paths are explicitly swallowed and documented as such: badge refresh (AppContext.tsx:116-129) and sign-out server call (132-142).

## (f) Accessibility

- Modal focus trap: implemented and correct (see (c)) — deficits are background inertness (main app content under the backdrop remains reachable by programmatic focus) and no `aria-describedby` wiring the body text to the dialog (only `aria-label={title}`, primitives.tsx:185-187).
- Async announcements: toasts announce via `role="status"` / `role="alert"` (ToastStack.tsx:25); the failure `Message` uses `role="alert"` (primitives.tsx:104-112); login error uses `role="alert"` (LoginPage.tsx:447). Loading/Empty states use `aria-live="polite"` (states.tsx:42, primitives.tsx:122). ErrorState itself does not announce — acceptable because the toast/message path covers it.
- Keyboard reachability: all actions are real `<button>`s; no keyboard-only gap found (the analytics chart's tooltip is mouse-only, but an accessible `<details>` table with caption+scope is provided right below it — AnalyticsPage.tsx:601-668).
- Labels: inputs use `htmlFor`/`id` pairs or a wrapping `<label className="field">`; QR/secret inputs on login have labels or are read-only copy fields (LoginPage.tsx:193-247, 371-405). `aria-label` used on decorative/menu/close elements.
- Table semantics: real `<table>/<thead>/<tbody>` everywhere, `aria-sort` on analytics columns; missing `scope="col"` (F5) and a few action columns with empty `<th/>` are the only gaps.

## (g) i18n

- No hardcoded UI strings found in pages — every label/placeholder/notice goes through `t(...)` (verified by grep across pages/components/app; literals like `'—'`, status codes and enum values are data, not UI copy).
- Bilingual completeness: enforced twice — compile-time `en satisfies` the zh shape, and runtime parity tests covering key sets, empty leaves, untranslated-CJK-in-en, and placeholder-consistency per key (i18n.test.ts:44-110). Both locales ship (~1195 / ~1188 lines). The one defect found is a *wrong-key* reuse, not a missing key (F2), which the parity tests can't catch because both keys exist.

## (h) List/table correctness

- Loading/empty/error states: correct on every main list fetch (see (e)). Exceptions: KYC renders stale `data` while loading and on error (F1) and Analytics keeps stale data next to its error card (F7); both are the same root cause (useLoad never clears `data`), so one fix in the hook plus two gated renders resolves both.
- Pagination: Orders and Audit paginate with offset/PAGE_SIZE, correct count strings (`common.count`), Prev disabled at 0, Next disabled at `!hasMore` (OrdersPage.tsx:192-216); filters reset `offset` to 0 on change (OrdersPage.tsx:133-149). Pagination buttons are not load-aware (F6).
- Filters-reset-error: `useLoad` clears `error` at the start of every run (useLoad.ts:49), so a failed filter fetch is not sticky across filter changes. Search clears error on new query (SearchPage.tsx:76, 89). Settlement period edits clear error/result/preview explicitly (SettlementPage.tsx:208-210).
- Empty vs loading distinction: all pages gate empty states on `!loading && !error` (except KYC — F1); Search separates "did not search / too short / no match" into three distinct states (SearchPage.tsx:141-210).

## (i) State-management hooks (`useLoad`, `useDialogs`)

- `useLoad` correctness: latest-loader-via-ref avoids inline-arrow dep churn (useLoad.ts:41-44); effect deps are `[...deps, nonce]` so reload re-runs without identity issues (68); cleanup sets `cancelled` only — no state set after unmount. No effect-dependency bug found. Design note: `data` is deliberately preserved across runs (used by LiveMap snapshot semantics) but that is exactly what F1/F7 trip over.
- `useDialogs`: `open` resets busy+error (useDialogs.tsx:38-42); submit/confirm guard on `busy` prevents re-entry (47, 120); throw keeps the modal open and shows the message (53-58). `onClose` is a no-op while busy so an in-flight submit can't be dismissed mid-flight (63, 136). No state-in-closure bug found — bodies receive `onChange` callbacks that mutate a captured local object, which is stable because `open` (and the spec) is fixed for the dialog's lifetime (AccountsPage.tsx:89-113, KycPage.tsx:65-82).
- LiveMap's `useLiveSnapshot` is a bespoke hook and is well-built: StrictMode-safe local `busy`, visibility-aware timer, filter via ref, effect keyed on all inputs (LiveMapPage.tsx:114-201).

## (j) XSS surface

- No `dangerouslySetInnerHTML` anywhere in app code; `escapeValue: false` in i18n is safe because React escapes all rendered text (i18n/index.ts:96-99, primitives.tsx:7-8). Interpolated `{{query}}`, usernames, notes, and addresses are all text nodes or `title` attributes (React-escaped).
- The single residual surface is `href={doc.download_url}` written verbatim into an anchor `target="_blank"` (LicencePage.tsx:330-338) — F4. Server data today, but an anchor whose scheme is not validated is one backend bug away from `javascript:`.

## (k) Stale comments / code verifiable from source

- F2 is the clearest stale-code artefact: the note placeholder still calls the contact-key (FleetsPage.tsx:249).
- Comments match code everywhere else audited: `useLoad` header describes exactly what it does (sequential `inOrder` + renderToken guard); AppContext's `admin_role`-vs-`role` warning (AppContext.tsx:151-173) matches `types.ts` usage; SearchPage's retry-nonce comment is accurate and explains an actual bailed-out pitfall (SearchPage.tsx:60-69); SettlementPage's idempotency/token-bound comments match the implementation (SettlementPage.tsx:5-14); Analytics' timezone/money-string comments are load-bearing and correct (AnalyticsPage.tsx:17-24). No dead code or TODO/FIXME found.

## Leads verified against source

1. **AccountsPage state mutation — CLEAN.** No fetched state is mutated; edits are local dialog forms; role/active actions are API-call-plus-`reload()` (AccountsPage.tsx:68-87, 116-174, 185-207, 315, 343-347).
2. **Modal focus trap/restore — GOOD with two minor gaps.** Trap + restore implemented (primitives.tsx:152-173, 189-214); background not inert and no `aria-describedby` (F-a11y note).
3. **Settlement identity & double-run — GOOD.** Token-bound preview, period+token passed together, `busyRef`+`disabled`, preview invalidation on edit (SettlementPage.tsx:67-89, 107-136, 203-211, 219-226, 298-301).
4. **List pages loading/empty/error — GOOD except KYC.** All list pages gate on `!loading && !error` (h) except KYC (F1); per-row detail loads lack retry (F3).

---
*Verification run in-session: `npm run typecheck` → exit 0; `npx vitest run --no-file-parallelism --pool=forks` → 12 files / 87 tests passed.*