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

function record(ok, message) {
  if (ok) {
    notes.push(`  ok    ${message}`);
  } else {
    failures.push(`  FAIL  ${message}`);
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

async function main() {
  mkdirSync(SHOTS, { recursive: true });

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 960 },
    deviceScaleFactor: 1,
    // The console is a Chinese UI; without this the screenshots render tofu on a
    // machine with no CJK font installed in the browser sandbox.
    locale: 'zh-HK',
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
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.login__card', { timeout: 15000 });
  record(true, 'login screen renders');

  await page.fill('#login-phone', PHONE);
  await requestCode(page);
  record(await page.isVisible('#login-code'), 'code step appears after requesting an OTP');

  await page.fill('#login-code', CODE);
  await page.getByRole('button', { name: '登入', exact: true }).click();

  let signedIn = true;
  try {
    await page.waitForSelector('.shell', { timeout: 15000 });
  } catch {
    signedIn = false;
  }

  if (!signedIn) {
    // Report *why*, rather than the bare timeout this used to produce: the body
    // text is where `login.js` puts the server's rejection.
    const body = (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 300);
    record(false, `signed in as ${PHONE} — ${describe(consoleErrors, failedRequests)} | page: ${body}`);
    await page.screenshot({ path: `${SHOTS}/signin-failure.png`, fullPage: true });
    notes.push(`  shot  ${SHOTS}/signin-failure.png`);
  } else {
    record(true, `signed in as ${PHONE}`);
    record(
      (await page.locator('.sidebar__foot').innerText()).includes('852'),
      'sidebar shows the masked account',
    );

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

    const sidebarText = await page.locator('.sidebar').innerText();
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
    await page.locator('.navlink').nth(4).click();
    let navWorked = true;
    try {
      await page.waitForFunction(() => window.location.hash === '#/fleets', undefined, {
        timeout: 10000,
      });
    } catch {
      navWorked = false;
    }
    record(navWorked, 'clicking a sidebar link routes to 車隊');
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
      consoleErrors = [];
      failedRequests = [];

      await page.goto('about:blank');
      await page.goto(`${BASE}/${route.hash}`, { waitUntil: 'domcontentloaded' });

      let rendered = true;
      try {
        await page.waitForFunction(
          (needles) => needles.every((needle) => document.body.innerText.includes(needle)),
          route.expect,
          { timeout: 15000 },
        );
      } catch {
        rendered = false;
      }

      const body = await page.locator('body').innerText();
      const missing = route.expect.filter((needle) => !body.includes(needle));
      record(
        rendered && missing.length === 0,
        `${route.name}: renders${missing.length ? ` — missing ${missing.join(', ')}` : ' with expected content'}`,
      );

      const errorBanner = await page.locator('.error').count();
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
    consoleErrors = [];
    failedRequests = [];
    await page.goto('about:blank');
    await page.goto(`${BASE}/#/fleets`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('table tbody tr', { timeout: 15000 });

    const manage = page.getByRole('button', { name: '管理' }).first();
    if ((await manage.count()) > 0) {
      await manage.click();

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

      const body = await page.locator('body').innerText();
      const checks = ['成員名單', '每週結算', '結算紀錄', '加入成員'];
      const missing = checks.filter((needle) => !body.includes(needle));
      record(
        detailRendered && missing.length === 0,
        `fleet detail: renders${missing.length ? ` — missing ${missing.join(', ')}` : ' all sections'}`,
      );
      record((await page.locator('.error').count()) === 0, 'fleet detail: no error banner');
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
      await lever.locator('input[type="text"]').fill('2026-W47');
      await page.getByRole('button', { name: '執行本週車隊結算' }).click();

      let runReported = true;
      try {
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
        (await page.locator('.error').count()) === 0,
        'fleet detail: settlement leaves no error banner',
      );
      await page.screenshot({ path: `${SHOTS}/fleet-settlement.png`, fullPage: true });
      notes.push(`  shot  ${SHOTS}/fleet-settlement.png`);
    } else {
      notes.push('  note  no fleet in the register — detail route skipped');
    }

    // ----------------------------------------------------- session teardown
    await page.getByRole('button', { name: '登出' }).click();
    await page.waitForSelector('.login__card', { timeout: 15000 });
    record(true, 'sign-out returns to the login screen');
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
