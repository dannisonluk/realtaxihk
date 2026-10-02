/**
 * Layout auditor for the admin console: does the UI still fit in **English**?
 *
 * Why this exists
 * ---------------
 * The console was designed in Traditional Chinese, where a nav label is two to
 * five characters wide. English labels are two to three times longer --
 * `Weekly settlement` against `每週結算` -- and the sidebar is a fixed 248px.
 * That is the classic i18n layout failure: nothing *errors*, nothing overflows
 * loudly, the label simply wraps to two lines, the nav row grows, and the list
 * that fitted before now scrolls. A screenshot review catches it only if
 * someone happens to look at that page in that language.
 *
 * So this measures geometry rather than eyeballing it, over the full matrix the
 * UI is supposed to support: **{zh-Hant, en} x {light, dark}**. Four combinations,
 * both axes of the requirement.
 *
 * What it checks, per page per combination
 * ----------------------------------------
 *   1. **Horizontal document overflow.** `scrollWidth > clientWidth + 1` on the
 *      document element means something is wider than the viewport — the one
 *      thing that is unambiguously broken rather than merely tight.
 *   2. **Clipped text.** An element whose `scrollWidth` exceeds its own
 *      `clientWidth` while `overflow` is hidden: text that is rendered but
 *      unreachable. Deliberate single-line ellipsis is allow-listed by selector.
 *   3. **Nav labels that wrap.** A `.navlink` taller than the 44px target means
 *      its label took a second line.
 *   4. **Any element leaving the viewport** horizontally, by more than a
 *      tolerance — reported with its selector so the cause is identifiable.
 *      Two exemptions, both of which are "the thing that looks wrong is the
 *      thing working": an element inside a container that scrolls horizontally
 *      on purpose (`table.data` on a phone), and an element inside the Leaflet
 *      map, which sizes its panes larger than its box and clips them by design.
 *      `.sr-only` is likewise allow-listed for check 2 — it is 1px with hidden
 *      overflow *by definition*.
 *   5. **Console errors / page exceptions**, as in the other verifiers.
 *   6. **SVG text scaled below its authored size.** A chart drawn in a `viewBox`
 *      with `width: 100%` scales *everything* inside it, text included, so the
 *      axis labels shrink with the card: the analytics chart's 10-unit labels
 *      render at 6.1px in a 436px card. `getComputedStyle` cannot see this — it
 *      reports the authored size — so the scale factor is recovered from the
 *      rendered width against the `viewBox` and applied by hand. This is the
 *      check that would have caught it, and it is why the default run is at
 *      1440px *and* why `--width 500` is worth running separately.
 *   7. **Interactive controls under 28px tall.** Check 3 only catches a nav
 *      label that *grew*; nothing caught a control that shrank. A `.chip` — a
 *      label treatment — used as a filter button measured 21px on the orders
 *      page, which is how "one class, two jobs" stayed invisible.
 *
 * The API is stubbed at the network layer, so this needs no database and no
 * running backend: only a static server on the built console.
 *
 * Usage — build first, then serve `dist/`:
 *
 *     cd admin-web/web && npm run build
 *     cd admin-web/web/dist && python -m http.server 8099 --bind 127.0.0.1 &
 *     NODE_PATH="$HOME/.workbuddy-ai/binaries/node/versions/22.22.2-3/node_modules/@playwright/cli/node_modules" \
 *       node admin-web/tool/audit_layout.mjs --base http://127.0.0.1:8099 --out .tmp/layout
 *
 * Exit code 0 = every page rendered clean, in every combination.
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium } from 'playwright';

const HERE = dirname(fileURLToPath(import.meta.url));

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
}

const BASE = arg('base', 'http://127.0.0.1:8099');
const OUT = resolve(arg('out', resolve(HERE, '.layout-check')));
const VIEWPORT = {
  width: Number(arg('width', '1440')),
  height: Number(arg('height', '900')),
};

/**
 * The console's own API base override.
 *
 * Without it `resolveBaseUrl()` falls through to `//<host>:8000`, so every
 * request goes to an absolute origin the static server does not serve and the
 * run measures a console that never got past its `/auth/me` probe. Worse, the
 * symptom is *silent*: the page renders the sign-in form and reports no console
 * error, so it reads as "the dashboard route is broken" rather than "the
 * harness pointed the app at a port nobody is listening on".
 *
 * `serve.py` reverse-proxies `/api/*` on the console's own origin, which is
 * what this flag selects.
 */
const API_QUERY = '?api=same-origin';

/** A URL for a route, keeping the API override in the query string. */
function urlFor(hash) {
  return `${BASE}/${API_QUERY}${hash}`;
}

/** The routes the console exposes to a SUPER_ADMIN, with fixture keys. */
const ROUTES = [
  { hash: '#/', page: 'dashboard' },
  { hash: '#/orders', page: 'orders' },
  { hash: '#/live', page: 'live' },
  { hash: '#/search', page: 'search' },
  { hash: '#/disputes', page: 'disputes' },
  { hash: '#/kyc', page: 'kyc' },
  { hash: '#/licences', page: 'licences' },
  { hash: '#/refunds', page: 'refunds' },
  { hash: '#/settlement', page: 'settlement' },
  { hash: '#/fleets', page: 'fleets' },
  { hash: '#/analytics', page: 'analytics' },
  { hash: '#/audit', page: 'audit' },
  { hash: '#/accounts', page: 'accounts' },
];

const COMBOS = [
  { locale: 'zh-Hant', theme: 'light' },
  { locale: 'zh-Hant', theme: 'dark' },
  { locale: 'en', theme: 'light' },
  { locale: 'en', theme: 'dark' },
];

/**
 * Text clipping that is *intended*: a single-line ellipsis on a value that has a
 * `title` attribute or is deliberately truncated. Clipping anywhere else is a
 * defect, so the allow-list is explicit and selector-based rather than a blanket
 * "ignore small overflows".
 */
const ELLIPSIS_OK = [
  '.truncate',
  '.mono--truncate',
  '.cell-ellipsis',
  '.brand__sub',
  '.sidebar__foot',
  // `.sr-only` is 1px square with `overflow: hidden` *by definition* -- that is
  // the technique. Its text is 200-300px wide inside a 1px box, so every
  // visually-hidden caption, legend or label reads as "clipped text" and the
  // check reports the one pattern that exists to help a screen reader. It is
  // deliberately clipped, so it belongs here rather than in the failure list.
  '.sr-only',
];

/**
 * Build one catch-all stub, in priority order.
 *
 * Playwright matches the **most recently registered** route first, so two
 * overlapping globs let the broad one shadow the specific one and answer the
 * login call with the wrong body. One handler with an explicit `if` chain
 * cannot do that, and each answer is counted so a stub that never fires is
 * visible rather than silently typing the wrong screen.
 */
function installStubs(page, hits) {
  return page.route('**/api/**', (route) => {
    const url = route.request().url();
    const method = route.request().method();
    const want = (fragment) => url.includes(fragment);

    const json = (body, status = 200) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

    hits.total += 1;

    // --- auth ------------------------------------------------------------
    if (want('/admin/auth/login')) {
      hits.login += 1;
      return json({ next: 'totp_required', challenge_token: 'stub-challenge' });
    }
    if (want('/admin/auth/login/totp')) {
      hits.totp += 1;
      return json({
        access_token: 'stub-access-token',
        token_type: 'bearer',
        admin: {
          id: '11111111-2222-3333-4444-555555555555',
          username: 'operator',
          email: 'operator@example.com',
          full_name: 'Operator',
          totp_enrolled: true,
          admin_role: 'SUPER_ADMIN',
        },
      });
    }
    if (want('/admin/auth/me') || want('/auth/me')) {
      hits.me += 1;
      return json({
        id: '11111111-2222-3333-4444-555555555555',
        username: 'operator',
        email_masked: 'o***r@example.com',
        role: 'ADMIN',
        admin_role: 'SUPER_ADMIN',
      });
    }
    if (want('/admin/auth/logout')) {
      return json({ revoked: 1 });
    }
    if (want('/admin/auth/csrf') || want('/admin/auth/refresh')) {
      return json({ ok: true });
    }

    // --- analytics (needs its own shape: two requests per page load) -----
    if (want('/admin/analytics/heatmap')) {
      hits.analytics = (hits.analytics ?? 0) + 1;
      return json({
        range: {
          from: '2026-09-01',
          to: '2026-09-07',
          taxi_type: null,
          timezone: 'Asia/Hong_Kong',
          days: 7,
        },
        hours: Array.from({ length: 24 }, (_, hour) => ({
          hour,
          avg_per_day_hkd: hour === 19 ? '200.00' : '0.00',
          avg_per_active_day_hkd: hour === 19 ? '400.00' : '0.00',
          earnings_hkd: hour === 19 ? '400.00' : '0.00',
          orders: hour === 19 ? 2 : 0,
          active_days: hour === 19 ? 1 : 0,
          avg_orders_per_day: hour === 19 ? '1.00' : '0.00',
        })),
        max_avg_per_day_hkd: '200.00',
        peak_hour: 19,
        busiest_hour: 19,
        scale_max_hkd: '200.00',
      });
    }
    if (want('/admin/analytics')) {
      hits.analytics = (hits.analytics ?? 0) + 1;
      return json({
        range: {
          from: '2026-09-01',
          to: '2026-09-07',
          granularity: 'day',
          taxi_type: null,
          timezone: 'Asia/Hong_Kong',
        },
        totals: {
          orders: 3,
          earnings_hkd: '1234.50',
          avg_fare_hkd: '411.50',
          distance_km: '42.00',
          buckets: 2,
          days: 7,
        },
        buckets: [
          {
            bucket: '2026-09-01',
            orders: 1,
            earnings_hkd: '0.10',
            avg_fare_hkd: '0.10',
            distance_km: '1.00',
          },
          {
            bucket: '2026-09-02',
            orders: 2,
            earnings_hkd: '1234.40',
            avg_fare_hkd: '617.20',
            distance_km: '41.00',
          },
        ],
        sort: { by: 'bucket', dir: 'asc' },
      });
    }

    // --- live map (its own envelope: `{generated_at, drivers, truncated}`) -
    // Not `Paged<T>`, so it cannot fall through to the generic stub below. The
    // second row carries a plate long enough to be a fleet name, which is the
    // point of this auditor: a table that has only ever seen `AB1234` has never
    // been asked to truncate anything.
    if (want('/admin/live/drivers')) {
      return json({
        generated_at: '2026-10-02T12:00:00+08:00',
        drivers: [
          {
            driver_profile_id: '00000000-0000-4000-8000-000000000001',
            status: 'ACTIVE',
            taxi_type: 'NT',
            vehicle_reg_mark: 'AB1234',
            is_online: true,
            last_location_at: '2026-10-02T11:59:50+08:00',
            lat: 22.3193,
            lng: 114.1694,
            order_id: '00000000-0000-4000-8000-0000000000aa',
            order_status: 'IN_TRIP',
          },
          {
            driver_profile_id: '00000000-0000-4000-8000-000000000002',
            status: 'ACTIVE',
            taxi_type: 'URBAN',
            vehicle_reg_mark: 'WAN CHAI — HAPPY VALLEY CIRCUIT (NIGHT SHIFT)',
            is_online: true,
            last_location_at: '2026-10-02T11:40:00+08:00',
            lat: 22.2783,
            lng: 114.1747,
            order_id: null,
            order_status: null,
          },
        ],
        truncated: false,
      });
    }

    // --- everything else: the `Paged<T>` envelope the console tabulates ----
    // The shape is `{items, total, limit, offset}` (`types.ts` `Paged<T>`),
    // spelled exactly. A near-miss here does not degrade gracefully: the pages
    // index straight into the envelope, so a wrong key is a page that throws
    // during render and measures 0ms of nothing. Getting it wrong and blaming
    // the console is the trap this comment exists to prevent.
    //
    // Long strings on purpose. A layout auditor that only ever sees short
    // values certifies a UI that has never been stress-tested -- a real fleet
    // name is not `Test 1`.
    const LONG = 'Wan Chai — Happy Valley Circuit (Night Shift, Licensed Urban Taxi)';
    const row = (id) => {
      const uid = `00000000-0000-4000-8000-${String(id).padStart(12, '0')}`;
      return {
        id: uid,
        driver_profile_id: uid,
        // One object carrying every field the twelve list pages read, so a
        // single stub can serve all of them without a per-route fixture. Extra
        // keys are ignored by the components; missing ones throw.
        status: 'ACTIVE',
        created_at: '2026-09-01T09:00:00+08:00',
        updated_at: '2026-09-02T10:30:00+08:00',
        submitted_at: '2026-09-01T09:00:00+08:00',
        decided_at: null,
        decided_by: null,
        name: LONG,
        full_name: LONG,
        display_name: LONG,
        legal_name: LONG,
        driver_name: LONG,
        passenger_name: LONG,
        note: LONG,
        decision_note: null,
        reason: LONG,
        subject: LONG,
        amount_hkd: '1234.50',
        fee_hkd: '12.50',
        total_hkd: '1247.00',
        balance_hkd: '0.00',
        refund_amount_hkd: '120.00',
        taxi_type: 'NT',
        kind: 'ADMIN',
        actor: 'operator',
        action: 'UPDATE',
        target_type: 'driver',
        target_id: uid,
        phone_e164: '+85290000000',
        email: 'operator@example.com',
        licence_number: 'TAXI-123456',
        vehicle_registration: 'AB1234',
        member_count: 12,
        driver_count: 12,
      };
    };
    hits.list = (hits.list ?? 0) + 1;
    return json({
      items: Array.from({ length: 8 }, (_, i) => row(i)),
      total: 8,
      limit: 20,
      offset: 0,
    });
  });
}

mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const results = [];
const failures = [];

for (const combo of COMBOS) {
  const context = await browser.newContext({
    viewport: VIEWPORT,
    colorScheme: combo.theme,
    // The console resolves its locale from storage first, then the browser.
    // Storage is not writable before the first navigation, so the browser
    // language is set here and storage is written after the page loads.
    locale: combo.locale === 'en' ? 'en-HK' : 'zh-HK',
  });
  const page = await context.newPage();

  const errors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
  });
  page.on('pageerror', (err) => errors.push(String(err)));

  const hits = { total: 0 };
  await installStubs(page, hits);

  /**
   * Seed the session and the pinned preference **before any module runs**.
   *
   * `addInitScript` rather than a `page.evaluate` after a first load, because
   * `sessionStorage` is per-document and does not survive a real navigation:
   * seeding it and then navigating discards it, the app boots signed out, and
   * every route measures the sign-in form. That failure is quiet — no error,
   * no console message — and would have had this auditor certifying twelve
   * screenshots of the login page.
   */
  await page.addInitScript(
    ([locale, theme]) => {
      sessionStorage.setItem(
        'realtaxi.admin.session',
        JSON.stringify({
          accessToken: 'stub-access-token',
          user: {
            id: '11111111-2222-3333-4444-555555555555',
            username: 'operator',
            email: 'operator@example.com',
            full_name: 'Operator',
            totp_enrolled: true,
            admin_role: 'SUPER_ADMIN',
          },
        }),
      );
      localStorage.setItem('realtaxihk.console.locale', locale);
      localStorage.setItem('realtaxihk.console.theme', theme);
    },
    [combo.locale, combo.theme],
  );

  // A `session.clear()` inside the app wipes the module-level cache but not the
  // stored copy, so re-seeding on every navigation is handled by the init script
  // running again. One navigation per route, no warm-up load.
  await page.goto(urlFor('#/'), { waitUntil: 'domcontentloaded' });

  for (const route of ROUTES) {
    const label = `${combo.locale}/${combo.theme} ${route.hash}`;

    await page.goto(urlFor(route.hash), { waitUntil: 'networkidle' });
    // Let the deferred loads land so the table is populated rather than
    // measured in its skeleton state.
    await page.waitForTimeout(400);

    const report = await page.evaluate((ellipsisOk) => {
      const doc = document.documentElement;
      const vw = doc.clientWidth;
      const target = 44; // the console's tap-target height

      const describe = (el) => {
        if (!el) return null;
        const parts = [el.tagName.toLowerCase()];
        if (el.id) parts.push(`#${el.id}`);
        const cls = (el.getAttribute('class') ?? '').trim().split(/\s+/).filter(Boolean);
        if (cls.length) parts.push(`.${cls.slice(0, 3).join('.')}`);
        return parts.join('');
      };

      const clipped = [];
      const escaped = [];

      /**
       * Subtrees the console does not author.
       *
       * Leaflet sizes its tile pane and its zoom pane *larger* than the map box
       * and clips them with `overflow: hidden` — that is how panning works. So
       * the tiles genuinely extend past the map, and the container genuinely
       * reports `scrollWidth > clientWidth` for its own control layer. Neither is
       * a defect the console can fix, and the guarantee that matters — that the
       * *page* does not overflow — is still checked, and still holds. Without
       * this exemption the map page cannot pass, which is how it came to be
       * absent from `ROUTES` while the docs claimed it rendered clean.
       */
      const thirdParty = (el) => el.closest('.leaflet-container') !== null;

      for (const el of document.querySelectorAll('body *')) {
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;

        // (2) text rendered but unreachable
        const hiddenX = ['hidden', 'clip'].includes(style.overflowX);
        if (
          hiddenX &&
          el.scrollWidth > el.clientWidth + 1 &&
          (el.textContent ?? '').trim().length > 0
        ) {
          const allowed = ellipsisOk.some((sel) => el.matches(sel) || el.closest(sel));
          if (!allowed && !thirdParty(el)) {
            clipped.push({
              el: describe(el),
              scrollWidth: el.scrollWidth,
              clientWidth: el.clientWidth,
              text: (el.textContent ?? '').trim().slice(0, 60),
            });
          }
        }

        // (4) anything reaching past the viewport on the x-axis, *unless* it is
        // inside a container that scrolls horizontally on purpose.
        //
        // That exemption is the whole subtlety. A `table.data` on a 380px phone
        // is genuinely wider than the screen, and that is correct: `.table-wrap`
        // is `overflow-x: auto`, so the table scrolls inside its own box and the
        // *page* never overflows. Reporting it as "escaped the viewport" flags
        // the one thing that is working. An audit that cries wolf on every table
        // is an audit nobody reads, so containment is checked rather than
        // assumed: walk up and look for an ancestor that scrolls.
        const inScroller = (el) => {
          let node = el.parentElement;
          while (node && node !== document.body) {
            const s = getComputedStyle(node);
            if (['auto', 'scroll'].includes(s.overflowX) && node.scrollWidth > node.clientWidth) {
              return true;
            }
            node = node.parentElement;
          }
          return false;
        };

        if (rect.right > vw + 1 && !inScroller(el) && !thirdParty(el)) {
          escaped.push({
            el: describe(el),
            right: Math.round(rect.right),
            viewport: vw,
          });
        }
      }

      // (3) nav rows that grew past the tap target = the label wrapped
      const tallNav = [];
      for (const link of document.querySelectorAll('.navlink')) {
        const h = link.getBoundingClientRect().height;
        if (h > target + 2) {
          tallNav.push({ text: (link.textContent ?? '').trim(), height: Math.round(h) });
        }
      }

      // (6) SVG text that the viewBox scaled *down*.
      //
      // The authored size is what `getComputedStyle` returns, and it is a lie
      // about what is on screen: everything inside the svg is multiplied by the
      // `viewBox` scale factor. Recover that factor from the rendered width and
      // apply it, then compare against what the label was authored at — the
      // invariant is "never smaller than authored", which is exactly what
      // `min-width` on the svg restores.
      const scaledText = [];
      for (const svg of document.querySelectorAll('.chart svg')) {
        const box = (svg.getAttribute('viewBox') ?? '').trim().split(/[\s,]+/).map(Number);
        const rendered = svg.getBoundingClientRect().width;
        if (box.length !== 4 || !box[2] || !rendered) continue;
        const scale = rendered / box[2];
        for (const node of svg.querySelectorAll('text')) {
          const authored = parseFloat(getComputedStyle(node).fontSize);
          if (!Number.isFinite(authored)) continue;
          const effective = authored * scale;
          if (effective < authored - 0.05) {
            scaledText.push({
              text: (node.textContent ?? '').trim().slice(0, 12),
              authored: Math.round(authored * 10) / 10,
              effective: Math.round(effective * 10) / 10,
              scale: Math.round(scale * 100) / 100,
            });
          }
        }
      }

      // (7) interactive controls smaller than the 28px default.
      //
      // `tallNav` above only catches a control that grew; nothing caught one that
      // shrank. The console's own rule is 44px (`--target`), but the bar here is
      // the floor below which a control is genuinely awkward to hit: 28px, the
      // macOS default control size. A `.chip` — a *label* treatment — used as a
      // filter button measured 21px on the orders page, and 26px on the live map,
      // which is how "the same class is both a label and a button" went unnoticed.
      const smallTargets = [];
      for (const el of document.querySelectorAll(
        'button, a[href], summary, [role="button"], select',
      )) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        if (thirdParty(el)) continue;
        if (r.height < 28) {
          smallTargets.push({
            el: describe(el),
            height: Math.round(r.height),
            text: (el.textContent ?? '').trim().slice(0, 20),
          });
        }
      }

      return {
        htmlLang: doc.lang,
        theme: doc.dataset.theme,
        overflowX: doc.scrollWidth > doc.clientWidth + 1 ? doc.scrollWidth - doc.clientWidth : 0,
        clipped,
        escaped,
        tallNav,
        scaledText,
        smallTargets,
        navWidth: Math.round(
          document.querySelector('.sidebar')?.getBoundingClientRect().width ?? 0,
        ),
        // The screenshots are for a human to skim; the numbers are the test.
        heading: (document.querySelector('.page-head h1')?.textContent ?? '').trim(),
      };
    }, ELLIPSIS_OK);

    await page.screenshot({
      path: resolve(OUT, `${combo.locale}-${combo.theme}-${route.page}.png`),
      fullPage: true,
    });

    const entry = { combo, route: route.hash, page: route.page, ...report };
    results.push(entry);

    if (report.overflowX > 0) {
      failures.push(
        `${label}: document overflows horizontally by ${report.overflowX}px`,
      );
    }
    for (const item of report.clipped.slice(0, 6)) {
      failures.push(
        `${label}: clipped text in ${item.el} (${item.scrollWidth} > ${item.clientWidth}) "${item.text}"`,
      );
    }
    for (const item of report.escaped.slice(0, 6)) {
      failures.push(`${label}: ${item.el} extends to ${item.right}px past viewport ${item.viewport}`);
    }
    for (const item of report.tallNav) {
      failures.push(`${label}: nav label wrapped — "${item.text}" is ${item.height}px tall`);
    }
    for (const item of report.scaledText.slice(0, 4)) {
      failures.push(
        `${label}: chart text "${item.text}" renders at ${item.effective}px, scaled from ` +
          `${item.authored}px (x${item.scale}) — the viewBox is shrinking it`,
      );
    }
    for (const item of report.smallTargets.slice(0, 6)) {
      failures.push(
        `${label}: ${item.el} is ${item.height}px tall (under 28px) — "${item.text}"`,
      );
    }
    if (report.heading === '') {
      failures.push(`${label}: no heading rendered (page body empty?)`);
    }
    // The locale must reach `<html lang>`, which is what `:lang(en)` in
    // styles.css and the font fallback key off. This is a real defect that
    // shipped: the attribute is written from i18next's `languageChanged`, which
    // does not fire for the locale an app *starts* in — so an English console
    // rendered correct English while telling CSS it was Chinese. A screenshot
    // cannot show that, and neither can any check that only looks at text.
    if (report.htmlLang !== combo.locale) {
      failures.push(
        `${label}: <html lang> is "${report.htmlLang}", expected "${combo.locale}"`,
      );
    }
    if (report.theme !== combo.theme) {
      failures.push(
        `${label}: <html data-theme> is "${report.theme}", expected "${combo.theme}"`,
      );
    }
  }

  // Console errors are per-combination, collected across every route above.
  for (const text of errors.slice(0, 8)) {
    failures.push(`${combo.locale}/${combo.theme}: console error — ${text}`);
  }

  await context.close();
}

await browser.close();

writeFileSync(resolve(OUT, 'layout-report.json'), JSON.stringify(results, null, 2));

// A compact table, because the useful signal is the comparison across the four
// combinations rather than any single number.
console.log('');
console.log('locale  theme  page              navW  ovf  clip  esc  tall  scl  small  heading');
console.log('-'.repeat(91));
for (const r of results) {
  console.log(
    [
      r.combo.locale.padEnd(7),
      r.combo.theme.padEnd(6),
      r.page.padEnd(16),
      String(r.navWidth).padStart(4),
      String(r.overflowX).padStart(4),
      String(r.clipped.length).padStart(5),
      String(r.escaped.length).padStart(4),
      String(r.tallNav.length).padStart(5),
      String(r.scaledText.length).padStart(4),
      String(r.smallTargets.length).padStart(6),
      ` ${r.heading.slice(0, 24)}`,
    ].join(' '),
  );
}
console.log('');

if (failures.length) {
  console.log(`FAIL — ${failures.length} layout problem(s):`);
  for (const line of failures) console.log(`  - ${line}`);
  console.log(`\nReport: ${resolve(OUT, 'layout-report.json')}`);
  process.exit(1);
}

console.log(`OK — ${results.length} page renders clean across 4 locale/theme combinations.`);
console.log(`Report: ${resolve(OUT, 'layout-report.json')}`);
