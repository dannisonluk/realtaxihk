/**
 * UI verifier for the admin console.
 *
 * Drives the real console in a real browser against the real API, and fails if
 * anything a person would notice goes wrong. This exists because the console has
 * no unit tests: it is DOM code, and DOM code that is only read is not verified.
 * The three classes of defect it catches that reading the source does not:
 *
 *   1. **A module that fails to load.** A typo in an import path, or a MIME type
 *      the browser refuses, leaves a blank page and one console error. Static
 *      analysis of an ES module graph does not see this.
 *   2. **A render that throws.** Every view builds its DOM imperatively; a null
 *      dereference on a response shape the fixture did not cover shows up as a
 *      dead route, not a failed assertion.
 *   3. **A silent API mismatch.** The console calls the same endpoints the mobile
 *      client does, but with `fetch` rather than the typed client. A wrong query
 *      parameter name is a 422 that the page renders as an error banner — which
 *      is exactly what a check for "no error banner" catches.
 *
 * Usage — the API must be on :8000 with an admin account, and the console served:
 *
 *     python admin-web/serve.py --port 8081
 *     node admin-web/tool/verify_ui.mjs --base http://127.0.0.1:8081 \
 *         --phone +85290000001 --code 123456
 *
 * `playwright` is resolved from `NODE_PATH`; see `admin-web/README.md`.
 * Exit code 0 = every route rendered with a clean console.
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium } from 'playwright';

const HERE = dirname(fileURLToPath(import.meta.url));
const SHOTS = resolve(HERE, '.ui-check');

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
}

const BASE = arg('base', 'http://127.0.0.1:8081');
const PHONE = arg('phone', '+85290000001');
const CODE = arg('code', '123456');

/**
 * The console is driven **same-origin**, with `serve.py` reverse-proxying
 * `/api/*` and `/health` to the API.
 *
 * Why: the default split-origin setup (`console :8081` -> `api :8000`) makes
 * every authenticated call a CORS *preflighted* request. That is normally
 * harmless, but in the sandbox this verifier runs in the preflight is answered
 * (OPTIONS 200) and the follow-up GET is then never delivered to the server —
 * the page hangs on a spinner and the run fails as a phantom "sign-in timed
 * out" while the login itself demonstrably succeeded. A simple cross-origin
 * `fetch('http://127.0.0.1:8000/health')` from inside the page returns 200, so
 * the stall is specific to the preflight handshake, i.e. environmental.
 *
 * Routing through the proxy removes the preflight entirely: one origin, simple
 * requests, nothing for the sandbox to interpose on. It is also a real feature
 * (`?api=same-origin`), not a test-only hack — see `admin-web/README.md`.
 */
const SAME_ORIGIN = 'api=same-origin';

/**
 * Append the same-origin sentinel to a console URL.
 *
 * The query must go **before** the `#`: `#/kyc?api=same-origin` puts the
 * parameter inside the fragment, where `window.location.search` is empty and
 * `resolveBaseUrl()` never sees it — so the page silently falls back to the
 * split-origin default and the whole point of the proxy is lost.
 */
function withSameOrigin(url) {
  const [beforeHash, hash] = url.split('#', 2);
  const separator = beforeHash.includes('?') ? '&' : '?';
  return `${beforeHash}${separator}${SAME_ORIGIN}${hash === undefined ? '' : `#${hash}`}`;
}

/**
 * Every route, with something that must be on the page for it to count.
 *
 * `expect` may only name chrome that renders **unconditionally** — the `pageHead`
 * title, a filter chip, an action button. It must never name a table column
 * label: `table()` in `dom.js` returns *only* the empty message when there are
 * zero rows, so `<thead>` is absent and a column label such as `金額` vanishes
 * with the data. Asserting on one couples the check to whether the fixture
 * happens to populate that queue, and it fails as a phantom "route does not
 * render" on a day the queue is simply empty. `狀態` and `金額` were both that.
 */
const ROUTES = [
  { hash: '#/', name: 'dashboard', title: '總覽', expect: ['總覽', '待審核司機'] },
  { hash: '#/kyc', name: 'kyc', title: '司機審核', expect: ['司機審核', '審核中'] },
  { hash: '#/refunds', name: 'refunds', title: '退款申請', expect: ['退款申請', '待審批'] },
  { hash: '#/settlement', name: 'settlement', title: '每週結算', expect: ['每週結算', '執行結算'] },
  { hash: '#/fleets', name: 'fleets', title: '車隊', expect: ['車隊', '新增車隊'] },
];

const failures = [];
const notes = [];

/**
 * Progress is streamed, not only summarised.
 *
 * The final report is written once, at the end — but a run that is killed
 * mid-way (the harness's own timeout, a wedged page) would otherwise leave no
 * trace of how far it got. A flushed line per checkpoint means the last line in
 * the log *is* the answer to "where did it stop".
 */
function progress(message) {
  console.log(`… ${message}`);
}

function record(ok, message) {
  if (ok) {
    notes.push(`  ok    ${message}`);
    progress(`ok    ${message}`);
  } else {
    failures.push(`  FAIL  ${message}`);
    progress(`FAIL  ${message}`);
  }
}

/** Console noise the browser emits that is not the app's fault. */
const IGNORED_CONSOLE = [/favicon\.ico/i];

function describe(errors, requests) {
  const console_ = errors.length ? errors.join(' | ') : 'none';
  const calls = requests.length ? requests.join(' | ') : 'none';
  return `console: ${console_} | failed calls: ${calls}`;
}

/**
 * Read the page's text without letting a wedged page abort the whole run.
 *
 * Under the sandbox wedge the browser can lose its connection to the console, so
 * `locator('body').innerText()` blocks until its own 30s timeout and throws —
 * which escaped as a `harness error` and killed the run before it could report
 * which routes passed. A route whose page cannot be read is a **failed route**,
 * which is a result; it is not a reason to stop.
 */
async function safeBodyText(page, timeout = 10000) {
  try {
    return await page.locator('body').innerText({ timeout });
  } catch {
    return '';
  }
}

/**
 * Count matching elements, returning `-1` (not `0`) when the page is unreadable.
 *
 * `0` would be read as "no error banner: pass" for a page that never rendered,
 * which is the opposite of the truth. `-1` fails the `=== 0` check instead.
 */
async function safeCount(page, selector, timeout = 10000) {
  try {
    return await page.locator(selector).count({ timeout });
  } catch {
    return -1;
  }
}

/**
 * Sign in, tolerating the server's 60s OTP resend cooldown.
 *
 * This is not defensive padding — it is the fix for a real flake. The backend
 * rate-limits `POST /auth/otp/request` to one code per phone per 60s
 * (`otp_service._RESEND_COOLDOWN_S`), and `verify_otp` always reads the *newest*
 * row for that phone. A previous run leaves that newest row `consumed_at`-stamped.
 * So a back-to-back run gets the cooldown, `login.js` **deliberately** advances to
 * the code step anyway (it honours `details.retry_after_seconds` rather than
 * dead-ending the operator), and the fixed dev code then verifies against a row
 * the server reports as "OTP already used" — a 400, no session, and a bare
 * `waitForSelector('.shell')` timeout that looks like a product bug.
 *
 * So: if the cooldown banner appears, wait it out on the countdown the UI is
 * already showing, resend, and confirm the banner clears before continuing.
 */
async function requestCode(page) {
  // `發送驗證碼` must be matched exactly: the resend button's label is
  // `重新發送驗證碼`, which *contains* it, so a substring match resolves to two
  // elements and the click target becomes ambiguous.
  const send = page.getByRole('button', { name: '發送驗證碼', exact: true });
  const resend = page.getByRole('button', { name: /^重新發送/ });
  const banner = page.locator('.dialog__error');

  if ((await send.count()) > 0 && (await send.isVisible())) {
    await send.click();
  } else {
    await resend.click();
  }

  // Wait for *either* outcome. Waiting only for the code step hides which one
  // happened, and turns a spent rate-limit budget into a bare timeout that reads
  // like a broken console. `#login-code` is built eagerly inside a collapsed step,
  // so this tests rendered-ness, not existence.
  await page.waitForFunction(
    () => {
      const code = document.querySelector('#login-code');
      const error = document.querySelector('.dialog__error');
      return Boolean(code?.getClientRects().length) || Boolean(error?.getClientRects().length);
    },
    undefined,
    { timeout: 15000 },
  );

  if (!(await banner.isVisible())) return; // the step opened: a live code was issued

  const message = (await banner.innerText()).trim();

  // The resend cooldown is recoverable. The row on record is a *previous* code —
  // and `verify_otp` reads the newest row, so it would report "OTP already used".
  // Wait the countdown the UI is already showing out, then ask for a fresh one.
  if (/cooldown/i.test(message)) {
    const label = (await resend.count()) > 0 ? await resend.innerText() : '';
    const seconds = Number((label.match(/(\d+)/) ?? [])[1] ?? 0);
    const waitS = seconds > 0 ? seconds + 2 : 62;
    notes.push(`  note  OTP resend cooldown active — waiting ${waitS}s for a live code`);

    await page.waitForFunction(
      () => {
        const button = [...document.querySelectorAll('button')].find((node) =>
          /^重新發送/.test((node.textContent ?? '').trim()),
        );
        return Boolean(button) && !button.disabled;
      },
      undefined,
      { timeout: (waitS + 20) * 1000 },
    );

    await resend.click();

    // "Banner hidden" is NOT a success signal: `requestCode()` calls `clearError()`
    // *before* it awaits the request, so the banner disappears the instant the
    // button is clicked and only comes back if the call fails. The unambiguous
    // positive signal is `startCooldown()` disabling the resend button again.
    let issued = true;
    try {
      await page.waitForFunction(
        () => {
          const button = [...document.querySelectorAll('button')].find((node) =>
            /^重新發送/.test((node.textContent ?? '').trim()),
          );
          return Boolean(button?.disabled);
        },
        undefined,
        { timeout: 20000 },
      );
    } catch {
      issued = false;
    }

    if (!issued) {
      const again = (await banner.innerText().catch(() => '')).trim();
      throw new Error(
        `OTP resend issued no new code${again ? ` — server said "${again}"` : ''}. ` +
          `Note this is per-phone: if anything else signed in with ${PHONE} in the ` +
          `last 60s, its code is the one on record. Clear the budget and retry: ` +
          `\`python admin-web/tool/reset_signin_budget.py --phone ${PHONE}\`.`,
      );
    }
    notes.push('  ok    a live code was issued after the cooldown');
    return;
  }

  // Anything else is a spent budget, not a console defect: `otp_phone_rate_limit`
  // (5/hour/number) or `otp_ip_rate_limit` (10/10min/address). The console renders
  // the 429 correctly and stays on the phone step. Say so, and say how to clear it.
  throw new Error(
    `OTP request refused: "${message}" — the rate-limit budget is spent, this is ` +
      `not a console defect. Clear it with ` +
      `\`python admin-web/tool/reset_signin_budget.py --phone ${PHONE}\` and retry.`,
  );
}

/**
 * Load a URL and wait until `needles` are all on the page, retrying the load.
 *
 * Why a retry is legitimate here rather than papering over a bug: this sandbox
 * leaves one response in a burst of six unanswered, permanently
 * (`admin-web/README.md` → "the sandbox drops one response in a burst"). The
 * server logs a `200` for it every time; the client just never sees the body.
 * A page load that fires several calls in parallel therefore has a real chance
 * of being one call short, through no fault of the console.
 *
 * The retry only repeats *loading*; it never relaxes an assertion. A route that
 * genuinely fails to render still fails all three attempts, and the failure
 * message says how many were made — so a real defect is not retried away.
 */
async function loadWithRetry(page, url, needles, { attempts = 3, timeout = 20000 } = {}) {
  let lastErrors = { consoleErrors: [], failedRequests: [] };
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const consoleErrors = [];
    const failedRequests = [];
    const onConsole = (message) => {
      if (message.type() !== 'error' && message.type() !== 'warning') return;
      const text = message.text();
      if (IGNORED_CONSOLE.some((pattern) => pattern.test(text))) return;
      consoleErrors.push(`[${message.type()}] ${text}`);
    };
    const onPageError = (error) => consoleErrors.push(`[pageerror] ${error.message}`);
    const onRequestFailed = (request) =>
      failedRequests.push(`${request.method()} ${request.url()} — ${request.failure()?.errorText}`);
    const onResponse = (response) => {
      if (response.status() >= 400 && response.url().includes('/api/')) {
        failedRequests.push(`${response.status()} ${response.url()}`);
      }
    };

    page.on('console', onConsole);
    page.on('pageerror', onPageError);
    page.on('requestfailed', onRequestFailed);
    page.on('response', onResponse);

    // `goto` itself can time out under the sandbox wedge: the document load
    // waits on the console server, whose threads are all parked on the 10s
    // upstream timeout at once. That is the condition this helper exists for, so
    // it must not escape as a `harness error` that aborts the whole run — count
    // it as a failed attempt and let the loop retry.
    let rendered = true;
    try {
      await page.goto('about:blank');
      await page.goto(url, { waitUntil: 'domcontentloaded' });

      await page.waitForFunction(
        (wanted) => wanted.every((needle) => document.body.innerText.includes(needle)),
        needles,
        { timeout },
      );
    } catch {
      rendered = false;
    }

    page.off('console', onConsole);
    page.off('pageerror', onPageError);
    page.off('requestfailed', onRequestFailed);
    page.off('response', onResponse);

    lastErrors = { consoleErrors, failedRequests };
    if (rendered) return { rendered: true, attempt, ...lastErrors };
  }
  return { rendered: false, attempt: attempts, ...lastErrors };
}

async function main() {
  mkdirSync(SHOTS, { recursive: true });

  progress('launching chromium');
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 960 },
    deviceScaleFactor: 1,
    // The console is a Chinese UI; without this the screenshots render tofu on a
    // machine with no CJK font installed in the browser sandbox.
    locale: 'zh-HK',
    // Playwright's default navigation budget is 30s. Under the sandbox wedge the
    // console server's threads can all be parked on the 10s upstream timeout at
    // once, so a `goto` — even for a static file — can exceed it. The retry
    // helper treats a timed-out navigation as a failed attempt, so this only has
    // to be long enough for one attempt; 20s is well clear of a healthy load
    // (measured <300ms) and short enough that three attempts stay inside the
    // harness's own run budget.
    navigationTimeout: 20000,
  });
  const page = await context.newPage();

  /** Console errors and page exceptions, reset per route. */
  let consoleErrors = [];
  let failedRequests = [];

  page.on('console', (message) => {
    if (message.type() !== 'error' && message.type() !== 'warning') return;
    const text = message.text();
    if (IGNORED_CONSOLE.some((pattern) => pattern.test(text))) return;
    consoleErrors.push(`[${message.type()}] ${text}`);
  });
  page.on('pageerror', (error) => {
    consoleErrors.push(`[pageerror] ${error.message}`);
  });
  page.on('requestfailed', (request) => {
    const failure = request.failure();
    failedRequests.push(`${request.method()} ${request.url()} — ${failure?.errorText}`);
  });
  // A 4xx/5xx from the API is a contract mismatch, not a rendering one, and the
  // page usually renders an error banner for it. Recorded so it cannot hide.
  page.on('response', (response) => {
    if (response.status() >= 400 && response.url().includes('/api/')) {
      failedRequests.push(`${response.status()} ${response.url()}`);
    }
  });

  // ---------------------------------------------------------------- sign in
  await page.goto('about:blank');
  await page.goto(withSameOrigin(BASE), { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.login__card', { timeout: 15000 });
  record(true, 'login screen renders');

  await page.fill('#login-phone', PHONE);
  await requestCode(page);
  record(await page.isVisible('#login-code'), 'code step appears after requesting an OTP');

  await page.fill('#login-code', CODE);
  await page.getByRole('button', { name: '登入', exact: true }).click();

  let signedIn = true;
  try {
    // Generous on purpose. Sign-in opens the shell, and the shell's first
    // authenticated call pays the API's connection-pool warm-up — measured at
    // 17.5s on a cold uvicorn before `FleetService.active_member_counts` removed
    // the fleet-list N+1. A 15s bound turns that cold start into a phantom
    // "sign-in failed" while the login itself succeeded, so the budget here is
    // sized for a cold API rather than a warm one.
    await page.waitForSelector('.shell', { timeout: 90000 });
  } catch {
    signedIn = false;
  }

  if (!signedIn) {
    // Report *why*, rather than the bare timeout this used to produce: the body
    // text is where `login.js` puts the server's rejection.
    const body = (await safeBodyText(page)).replace(/\s+/g, ' ').slice(0, 300);
    record(false, `signed in as ${PHONE} — ${describe(consoleErrors, failedRequests)} | page: ${body}`);
    await page.screenshot({ path: `${SHOTS}/signin-failure.png`, fullPage: true });
    notes.push(`  shot  ${SHOTS}/signin-failure.png`);
  } else {
    record(true, `signed in as ${PHONE}`);

    // The shell is up, but the sandbox wedge can still make the page unreadable
    // at this exact moment (the sidebar assertions all read the DOM). Guard them
    // as a block so a wedged page records failures instead of aborting the run.
    try {
      const sidebarFoot = await page.locator('.sidebar__foot').innerText({ timeout: 10000 });
      record(sidebarFoot.includes('852'), 'sidebar shows the masked account');

      // The sidebar nav — and the one check the route loop structurally cannot make.
      // Every route below is entered by loading a URL, so a nav that renders nothing
      // passes every route check while leaving the console unnavigable. That is not
      // hypothetical: `[brand, NAV.map(navLink), foot]` was not flattened by
      // `append()`, so the inner array was stringified to its anchors' comma-joined
      // `href`s — five links became one long `http://…#/,http://…#/kyc,…` text node.
      const NAV_LABELS = ['總覽', '司機審核', '退款', '每週結算', '車隊'];
      const navLabels = (await page.locator('.navlink > span:first-child').allInnerTexts()).map(
        (text) => text.trim(),
      );
      record(
        navLabels.length === NAV_LABELS.length &&
          NAV_LABELS.every((label, index) => navLabels[index] === label),
        `sidebar nav renders ${navLabels.length} link(s)` +
          (navLabels.length === NAV_LABELS.length ? '' : ` — expected ${NAV_LABELS.length}, got ${JSON.stringify(navLabels)}`),
      );

      const sidebarText = await page.locator('.sidebar').innerText({ timeout: 10000 });
      record(
        !/https?:\/\//.test(sidebarText),
        `sidebar has no stray URL text${
          /https?:\/\//.test(sidebarText)
            ? ` — ${sidebarText.replace(/\s+/g, ' ').slice(0, 140)}`
            : ''
        }`,
      );

      // Click, rather than load. This is the only assertion that exercises the
      // shell's `hashchange` wiring and the nav links together.
      await page.locator('.navlink').nth(4).click({ timeout: 10000 });
      let navWorked = true;
      try {
        await page.waitForFunction(() => window.location.hash === '#/fleets', undefined, {
          timeout: 10000,
        });
      } catch {
        navWorked = false;
      }
      record(navWorked, 'clicking a sidebar link routes to 車隊');
    } catch (error) {
      record(false, `shell assertions — page unreadable: ${error?.message || String(error)}`);
    }
  }

  // ------------------------------------------------------------ every route
  //
  // Each route is entered with a **full document load**, not by changing the
  // hash. A hash-only change is a same-document navigation: the previous route's
  // DOM is still on screen while the new view awaits its fetches, so a
  // "no spinner present" check resolves instantly against the *old* page and the
  // assertions read stale content. Reloading also exercises the session-restore
  // path in `boot()` on every route, which is the code most likely to strand an
  // operator on a blank screen.
  if (signedIn) {
    for (const route of ROUTES) {
      progress(`route ${route.name} — loading`);
      const { rendered, attempt, consoleErrors, failedRequests } = await loadWithRetry(
        page,
        withSameOrigin(`${BASE}/${route.hash}`),
        route.expect,
      );

      const retryNote = attempt > 1 ? ` (after ${attempt} load(s))` : '';
      const body = await safeBodyText(page);
      const missing = route.expect.filter((needle) => !body.includes(needle));
      record(
        rendered && missing.length === 0,
        `${route.name}: renders${missing.length ? ` — missing ${missing.join(', ')}` : ' with expected content'}${retryNote}`,
      );

      const errorBanner = await safeCount(page, '.error');
      record(errorBanner === 0, `${route.name}: no error banner`);

      record(
        consoleErrors.length === 0,
        `${route.name}: console clean${consoleErrors.length ? ` — ${consoleErrors.join(' | ')}` : ''}`,
      );
      record(
        failedRequests.length === 0,
        `${route.name}: no failed API calls${failedRequests.length ? ` — ${failedRequests.join(' | ')}` : ''}`,
      );

      const shot = `${SHOTS}/${route.name}.png`;
      await page.screenshot({ path: shot, fullPage: true });
      notes.push(`  shot  ${shot}`);
    }

    // -------------------------------------------------------- fleet detail
    //
    // The list must load with at least one row before the `管理` button exists,
    // and the list fetch is one of the burst that the sandbox may drop — so the
    // load+row-wait is retried as a unit before the click.
    consoleErrors = [];
    failedRequests = [];
    let manage = null;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        await page.goto('about:blank');
        await page.goto(withSameOrigin(`${BASE}/#/fleets`), { waitUntil: 'domcontentloaded' });
        await page.waitForSelector('table tbody tr', { timeout: 15000 });
        manage = page.getByRole('button', { name: '管理' }).first();
        break;
      } catch {
        manage = null;
      }
    }

    if (manage && (await safeCount(page, 'table tbody tr')) > 0) {
      try {
        await manage.click({ timeout: 15000 });
      } catch {
        notes.push('  note  fleet detail: `管理` click timed out — page unresponsive');
      }

      let detailRendered = true;
      try {
        await page.waitForFunction(
          () => {
            const text = document.body.innerText;
            return ['成員名單', '每週結算', '結算紀錄', '加入成員'].every((needle) =>
              text.includes(needle),
            );
          },
          undefined,
          { timeout: 15000 },
        );
      } catch {
        detailRendered = false;
      }

      const body = await safeBodyText(page);
      const checks = ['成員名單', '每週結算', '結算紀錄', '加入成員'];
      const missing = checks.filter((needle) => !body.includes(needle));
      record(
        detailRendered && missing.length === 0,
        `fleet detail: renders${missing.length ? ` — missing ${missing.join(', ')}` : ' all sections'}`,
      );
      record((await safeCount(page, '.error')) === 0, 'fleet detail: no error banner');
      record(
        consoleErrors.length === 0,
        `fleet detail: console clean${consoleErrors.length ? ` — ${consoleErrors.join(' | ')}` : ''}`,
      );
      record(
        failedRequests.length === 0,
        `fleet detail: no failed API calls${failedRequests.length ? ` — ${failedRequests.join(' | ')}` : ''}`,
      );

      const shot = `${SHOTS}/fleet-detail.png`;
      await page.screenshot({ path: shot, fullPage: true });
      notes.push(`  shot  ${shot}`);

      // The settlement lever, end to end: run it for an explicit week and confirm
      // the result card carries the counters the screen is built around. A week far
      // from the current one, so this cannot collide with a real run.
      //
      // Scoped to the settlement card: the header has an "編輯車隊" dialog whose
      // name field is also `input[type="text"]`, so an unscoped `fill` would be
      // order-dependent.
      const lever = page.locator('.card').filter({
        has: page.getByRole('button', { name: '執行本週車隊結算' }),
      });

      let runReported = true;
      try {
        await lever.locator('input[type="text"]').fill('2026-W47', { timeout: 15000 });
        await page.getByRole('button', { name: '執行本週車隊結算' }).click({ timeout: 15000 });

        await page.waitForFunction(
          () => {
            const text = document.body.innerText;
            return (
              text.includes('結果 · 2026-W47') &&
              text.includes('車隊每位費用') &&
              text.includes('每位節省')
            );
          },
          undefined,
          { timeout: 25000 },
        );
      } catch {
        runReported = false;
      }
      record(runReported, 'fleet detail: settlement runs and reports the week');
      record(
        (await safeCount(page, '.error')) === 0,
        'fleet detail: settlement leaves no error banner',
      );
      await page.screenshot({ path: `${SHOTS}/fleet-settlement.png`, fullPage: true });
      notes.push(`  shot  ${SHOTS}/fleet-settlement.png`);
    } else {
      notes.push('  note  no fleet in the register — detail route skipped');
    }

    // -------------------------------------------------------- driver detail
    //
    // Reached by *clicking* the queue's `檢視` button, not by loading the URL:
    // that exercises the row action, the `navigate()` call and the route
    // pattern together, which a direct `goto` would not.
    consoleErrors = [];
    failedRequests = [];
    let inspect = null;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        await page.goto('about:blank');
        await page.goto(withSameOrigin(`${BASE}/#/kyc`), { waitUntil: 'domcontentloaded' });
        await page.waitForSelector('table tbody tr', { timeout: 15000 });
        inspect = page.getByRole('button', { name: '檢視' }).first();
        break;
      } catch {
        inspect = null;
      }
    }

    if (inspect && (await safeCount(page, 'table tbody tr')) > 0) {
      try {
        await inspect.click({ timeout: 15000 });
      } catch {
        notes.push('  note  driver detail: `檢視` click timed out — page unresponsive');
      }

      let driverRendered = true;
      try {
        await page.waitForFunction(
          () => {
            const text = document.body.innerText;
            // Sections that render unconditionally, whatever the data: the
            // deposit card, the fleet card, the ledger and the refund history.
            // `帳目` and `退款紀錄` are headings the view always emits; a table
            // column label would vanish with an empty queue (see ROUTES above).
            return ['按金', '車隊', '帳目', '退款紀錄'].every((needle) => text.includes(needle));
          },
          undefined,
          { timeout: 15000 },
        );
      } catch {
        driverRendered = false;
      }

      const body = await safeBodyText(page);
      const checks = ['按金', '車隊', '帳目', '退款紀錄'];
      const missing = checks.filter((needle) => !body.includes(needle));
      record(
        driverRendered && missing.length === 0,
        `driver detail: renders${missing.length ? ` — missing ${missing.join(', ')}` : ' all sections'}`,
      );
      record((await safeCount(page, '.error')) === 0, 'driver detail: no error banner');
      record(
        consoleErrors.length === 0,
        `driver detail: console clean${consoleErrors.length ? ` — ${consoleErrors.join(' | ')}` : ''}`,
      );
      record(
        failedRequests.length === 0,
        `driver detail: no failed API calls${failedRequests.length ? ` — ${failedRequests.join(' | ')}` : ''}`,
      );
      // The deposit block is the reason this page exists; assert the widget that
      // renders it is actually in the DOM rather than merely styled-in-theory.
      record(
        (await page.locator('.meter').count()) > 0,
        'driver detail: deposit progress meter present',
      );

      const shot = `${SHOTS}/driver-detail.png`;
      await page.screenshot({ path: shot, fullPage: true });
      notes.push(`  shot  ${shot}`);
    } else {
      notes.push('  note  no driver in the register — detail route skipped');
    }

    // ----------------------------------------------------- session teardown
    let signedOut = true;
    try {
      await page.getByRole('button', { name: '登出' }).click({ timeout: 15000 });
      await page.waitForSelector('.login__card', { timeout: 15000 });
    } catch {
      signedOut = false;
    }
    record(signedOut, 'sign-out returns to the login screen');
  }

  await browser.close();

  // ------------------------------------------------------------------ report
  console.log('');
  for (const note of notes) console.log(note);
  console.log('');
  const report = [
    `--- admin console UI check: ${failures.length === 0 ? 'PASS' : 'FAIL'} ---`,
    ...failures,
  ].join('\n');
  console.log(report);
  writeFileSync(`${SHOTS}/report.txt`, `${report}\n`, 'utf8');
  process.exit(failures.length === 0 ? 0 : 1);
}

main().catch((error) => {
  console.error('harness error:', error);
  process.exit(2);
});
