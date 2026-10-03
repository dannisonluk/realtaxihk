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
 *         --username ops-admin --password "$ADMIN_PASSWORD" \
 *         --totp-secret "$ADMIN_TOTP_SECRET"
 *
 * `playwright` is resolved from `NODE_PATH`; see `admin-web/README.md`.
 * Exit code 0 = every route rendered with a clean console.
 */

import { createHmac } from 'node:crypto';
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

// Admin console sign-in is username + password + TOTP. The phone/OTP flow this
// verifier used to drive is gone from the console — and it could never have
// signed in anyway, because admins live in `admin_accounts`, a different table
// from the `users` the OTP flow authenticates. So `--phone/--code` are replaced
// by credentials plus the account's TOTP secret.
const USERNAME = arg('username', process.env.ADMIN_USERNAME ?? 'ops-admin');
const PASSWORD = arg('password', process.env.ADMIN_PASSWORD ?? '');
const TOTP_SECRET = arg('totp-secret', process.env.ADMIN_TOTP_SECRET ?? '');

// Fail before launching a browser. Without this the run gets as far as the
// password field and then dies on a 30s `waitForSelector` timeout that reads
// like a console defect rather than a missing argument.
if (!PASSWORD || !TOTP_SECRET) {
  console.error(
    'missing credentials: pass --password and --totp-secret (or set ' +
      'ADMIN_PASSWORD / ADMIN_TOTP_SECRET).\n' +
      'Create one with:\n' +
      '  ADMIN_PASSWORD=... python scripts/ops/create_admin_account.py \\\n' +
      '      --username ops-admin --email ops-admin@realtaxihk.local --yes\n' +
      'then enrol TOTP through POST /api/v1/admin/auth/login and\n' +
      '/totp/enrol/confirm, and read the secret from admin_accounts.totp_secret.',
  );
  process.exit(2);
}

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
  // The weekly settlement page's action is `預覽（不會收費）`, not a "run"
  // button: previewing produces a token-bound preview, and the actual charge is
  // raised from a fleet's detail page (covered by the fleet-detail checks
  // below). A needle of `執行結算` matched nothing and made a correct page look
  // broken.
  { hash: '#/settlement', name: 'settlement', title: '每週結算', expect: ['每週結算', '預覽'] },
  { hash: '#/fleets', name: 'fleets', title: '車隊', expect: ['車隊', '新增車隊'] },
  // The analytics page is the one route whose content depends on there being
  // completed orders in range. It must still render its headings and charts
  // when the range is empty — a dashboard that white-screens on "no data" is
  // worse than one that shows zeroes — so the needles are the section titles,
  // not any figure.
  { hash: '#/analytics', name: 'analytics', title: '表現分析', expect: ['表現分析', '時段分佈'] },
  // --- The governance screens. Same rule as above: needles are chrome that
  // renders with an empty result set. Each one names the page title plus a
  // control that is present regardless of whether the queue has rows —
  // otherwise a route "fails" on the day its list happens to be empty, which
  // is a check that reports the data, not the code.
  //
  // These six were the reason to extend this file at all: they were added to
  // the console without ever being driven in a browser, and the classes of
  // defect this verifier exists to catch (a bad import, a render that throws
  // on a real response shape, a query-parameter typo that comes back 422) are
  // exactly the ones a page cannot reveal by being read.
  { hash: '#/search', name: 'search', title: '搜尋', expect: ['搜尋', '帳戶'] },
  { hash: '#/orders', name: 'orders', title: '訂單', expect: ['訂單', '進行中'] },
  { hash: '#/disputes', name: 'disputes', title: '爭議', expect: ['爭議', '只看逾期'] },
  { hash: '#/audit', name: 'audit', title: '審計紀錄', expect: ['審計紀錄', '登入'] },
  { hash: '#/accounts', name: 'accounts', title: '管理員帳戶', expect: ['管理員帳戶', '超級管理員'] },
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
 * The TOTP code an authenticator app would show right now.
 *
 * RFC 6238 with the parameters this project actually issues — HMAC-SHA1, 6
 * digits, a 30-second step — matching `app/core/totp.py`. Implemented here
 * rather than shelled out to Python so the verifier stays one self-contained
 * command with no venv dependency.
 *
 * Note there is no ±1-step window here on purpose: the verifier submits exactly
 * one code, and the server's window already tolerates a slow run. Widening it
 * client-side would only hide a clock problem.
 */
function totpNow(secret) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const cleaned = secret.replace(/[\s-]/g, '').toUpperCase();
  let bits = '';
  for (const char of cleaned) {
    const index = alphabet.indexOf(char);
    if (index === -1) throw new Error(`totp secret is not base32: ${char}`);
    bits += index.toString(2).padStart(5, '0');
  }
  const key = Buffer.alloc(Math.floor(bits.length / 8));
  for (let i = 0; i < key.length; i += 1) {
    key[i] = parseInt(bits.slice(i * 8, i * 8 + 8), 2);
  }

  const counter = Math.floor(Date.now() / 1000 / 30);
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(counter));
  const mac = createHmac('sha1', key).update(message).digest();

  // Dynamic truncation: the low nibble of the last byte picks a 4-byte window.
  const offset = mac[mac.length - 1] & 0x0f;
  const value =
    ((mac[offset] & 0x7f) << 24) |
    (mac[offset + 1] << 16) |
    (mac[offset + 2] << 8) |
    mac[offset + 3];
  return String(value % 1_000_000).padStart(6, '0');
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

  // ------------------------------------------------------------------ preflight
  //
  // `serve.py` defaults to the **legacy** bundle; the React build needs `--dist`.
  // The two consoles have entirely different sign-in screens, so pointing this
  // verifier at the default gets a phone-OTP form where it expects a username
  // field, and it dies on a 30s `waitForSelector` timeout that reads like "the
  // console is broken" — while the console is fine and the URL is simply the
  // other product.
  //
  // Fetched rather than assumed: the served `index.html` names its own bundle,
  // and the legacy one loads `/js/`, the Vite build `/assets/`. This is a
  // one-request check against a server that is about to be driven anyway, and
  // it converts a 30-second mystifying timeout into an instruction.
  try {
    const res = await fetch(withSameOrigin(BASE), { redirect: 'follow' });
    const html = await res.text();
    const isLegacy = /src=["'][^"']*\/js\//.test(html) && !/\/assets\//.test(html);
    if (isLegacy) {
      console.error(
        `refusing to run: ${BASE} is serving the LEGACY console (admin-web/legacy).\n` +
          'This verifier only drives the React build.\n' +
          `Restart the server with --dist, e.g.\n` +
          `  python admin-web/serve.py --port 8081 --dist\n` +
          'then re-run with --base pointing at it.',
      );
      process.exit(2);
    }
  } catch (cause) {
    // A failed preflight is not proof the target is wrong — the sandbox proxy
    // can refuse a bare fetch from node while the browser reaches it fine. Only
    // an affirmative "this is the legacy bundle" is grounds to abort.
    progress(`preflight inconclusive (${cause?.message ?? cause}) — continuing`);
  }

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

  await page.fill('#login-username', USERNAME);
  await page.fill('#login-password', PASSWORD);
  await page.getByRole('button', { name: '下一步', exact: true }).click();
  // `waitForSelector`, not `isVisible`. The password step is an async round trip
  // (and, on a first login, a Redis write), so an instantaneous visibility check
  // races the transition and reports a failure on a sign-in that then succeeds —
  // which is exactly what the first run of this produced.
  let totpStep = true;
  try {
    await page.waitForSelector('#login-code', { timeout: 20000 });
  } catch {
    totpStep = false;
  }
  record(totpStep, 'TOTP step appears after the password');

  // Computed here, not once at startup: enrolment replay protection refuses a
  // code at or before the last accepted step, so a code generated before a slow
  // route walk could be stale by the time sign-in runs.
  await page.fill('#login-code', totpNow(TOTP_SECRET));
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
    record(false, `signed in as ${USERNAME} — ${describe(consoleErrors, failedRequests)} | page: ${body}`);
    await page.screenshot({ path: `${SHOTS}/signin-failure.png`, fullPage: true });
    notes.push(`  shot  ${SHOTS}/signin-failure.png`);
  } else {
    record(true, `signed in as ${USERNAME}`);

    // The shell is up, but the sandbox wedge can still make the page unreadable
    // at this exact moment (the sidebar assertions all read the DOM). Guard them
    // as a block so a wedged page records failures instead of aborting the run.
    try {
      const sidebarFoot = await page.locator('.sidebar__foot').innerText({ timeout: 10000 });
      // The account shown is the admin's `username`, not a masked phone: an
      // admin token resolves against `admin_accounts`, which has no phone. This
      // assertion used to require `852` and failed the moment the console moved
      // to admin sign-in — correctly, because the foot was rendering an em dash.
      record(sidebarFoot.includes(USERNAME), `sidebar shows the signed-in account (${USERNAME})`);

      // The sidebar nav — and the one check the route loop structurally cannot make.
      // Every route below is entered by loading a URL, so a nav that renders nothing
      // passes every route check while leaving the console unnavigable. That is not
      // hypothetical: `[brand, NAV.map(navLink), foot]` was not flattened by
      // `append()`, so the inner array was stringified to its anchors' comma-joined
      // `href`s — five links became one long `http://…#/,http://…#/kyc,…` text node.
      // Order matters and is asserted, because the nav is the only way an
      // operator moves around. This list must mirror `NAV` in `Shell.tsx`
      // exactly, including order — when it drifts, the check does not fail
      // loudly, it just stops covering whatever was added. The governance
      // screens (搜尋/訂單/爭議/審計紀錄/管理員帳戶) were inserted after this
      // list was first written; before the fix this line reported a stale
      // count of 7 while the shell rendered 12.
      const NAV_LABELS = [
        '總覽',
        '搜尋',
        '訂單',
        '爭議',
        '司機審核',
        '的士證審核',
        '退款',
        '每週結算',
        '車隊',
        '表現分析',
        '審計紀錄',
        '管理員帳戶',
      ];
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
      //
      // Selected by label, not by index. The index form (`nth(4)`) silently
      // started clicking 每週結算 instead of 車隊 the moment a link was inserted
      // above it, and the failure read as "the nav is broken" rather than "the
      // verifier is stale".
      await page.getByRole('link', { name: '車隊', exact: true }).click({ timeout: 10000 });
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
            return ['成員名單', '每週結算', '結算紀錄', '加入車隊成員'].every((needle) =>
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
      const checks = ['成員名單', '每週結算', '結算紀錄', '加入車隊成員'];
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
