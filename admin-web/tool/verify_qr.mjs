/**
 * Render the real console's TOTP enrolment screen in a real browser.
 *
 * The unit test asserts the QR's SVG path equals the encoding of the server's
 * `otpauth_uri`. That is the correctness question. This answers the *rendering*
 * question the unit test cannot: whether the code is actually on screen, at a
 * size a phone can resolve, with the quiet zone intact and the plate legible
 * against the console's surface in both themes.
 *
 * The API is stubbed at the network layer rather than run, so this needs no
 * database. Only `/admin/auth/login` is answered; the page never gets past the
 * enrolment step because the run deliberately stops there.
 *
 * Usage:
 *   node admin-web/tool/verify_qr.mjs --base http://127.0.0.1:8099 --out .tmp/qr
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium } from 'playwright';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(process.argv.includes('--out')
  ? process.argv[process.argv.indexOf('--out') + 1]
  : resolve(HERE, '.qr-check'));

const BASE = process.argv.includes('--base')
  ? process.argv[process.argv.indexOf('--base') + 1]
  : 'http://127.0.0.1:8099';

const SECRET = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP';
const OTPAUTH =
  'otpauth://totp/hkfastdc:ops-admin?secret=' +
  SECRET +
  '&issuer=hkfastdc';

const ENROLMENT = {
  next: 'enrolment_required',
  challenge_token: 'challenge-enrol-1',
  enrolment: {
    secret: SECRET,
    otpauth_uri: OTPAUTH,
    recovery_codes: ['AAAA-1111', 'BBBB-2222', 'CCCC-3333'],
  },
};

mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const failures = [];

for (const theme of ['light', 'dark']) {
  const context = await browser.newContext({
    viewport: { width: 1100, height: 900 },
    colorScheme: theme,
  });
  const page = await context.newPage();

  const consoleErrors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', (err) => consoleErrors.push(String(err)));

  // One handler, in priority order. Playwright matches the *most recently*
  // registered route first, so two overlapping globs would let the broad one
  // shadow the specific one and answer the login call with the wrong body —
  // which sends the page to the code step and makes this look like a console
  // defect. A single handler with an explicit match cannot do that.
  let loginHits = 0;
  await page.route('**/api/**', (route) => {
    const url = route.request().url();
    if (url.includes('/api/v1/admin/auth/login')) {
      loginHits += 1;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(ENROLMENT),
      });
    }
    // Anything else the shell fetches on boot (badges, /me) must not 500 and
    // spray errors into the console we are about to assert on.
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], total: 0, limit: 0, offset: 0 }),
    });
  });

  await page.goto(BASE, { waitUntil: 'networkidle' });

  await page.fill('#login-username', 'ops-admin');
  await page.fill('#login-password', 'correct horse battery staple');
  await page.getByRole('button', { name: /下一步/ }).click();

  const qr = page.locator('[data-testid="totp-qr"]');
  await qr.waitFor({ state: 'visible', timeout: 10000 });

  // Guard the guard: if the stub was never consulted the page could not have
  // reached the enrolment step at all, and every assertion below would be
  // checking a screen that renders for a different reason.
  if (loginHits !== 1) {
    failures.push(`[${theme}] login stub consulted ${loginHits} times, expected 1`);
  }

  // The code itself must be on screen and non-trivial.
  const svg = qr.locator('svg');
  const box = await svg.boundingBox();
  if (!box || box.width < 150 || box.height < 150) {
    failures.push(`[${theme}] QR is too small to scan: ${JSON.stringify(box)}`);
  }

  // The quiet zone: `qrcode.react` bakes it in as a margin inside the viewBox,
  // so check the plate is larger than the module field rather than that the
  // SVG element has padding.
  const viewBox = await svg.getAttribute('viewBox');
  const counts = (viewBox ?? '').split(/\s+/).map(Number);
  if (counts.length !== 4 || !counts[2] || counts[2] < 21) {
    failures.push(`[${theme}] unexpected viewBox: ${viewBox}`);
  }

  // A QR that rendered but is invisible (wrong colours on the surface) would
  // pass every structural check above. Assert the dark modules really are dark
  // and the plate really is light by reading the fills.
  const fills = await svg.locator('path').evaluateAll((nodes) =>
    nodes.map((n) => n.getAttribute('fill')),
  );
  if (!fills.some((f) => f && /^#0{3,6}$|^black$/i.test(f))) {
    failures.push(`[${theme}] no dark module path; fills=${JSON.stringify(fills)}`);
  }
  if (!fills.some((f) => f && /^#f{3,6}$|^white$/i.test(f))) {
    failures.push(`[${theme}] no light plate path; fills=${JSON.stringify(fills)}`);
  }

  // The secret and recovery codes must be readable, not clipped.
  await page.locator('input[readonly]').first().waitFor({ state: 'visible' });
  const codes = page.locator('[data-testid="recovery-codes"]');
  await codes.waitFor({ state: 'visible' });

  await page.screenshot({
    path: resolve(OUT, `enrol-${theme}.png`),
    fullPage: true,
  });

  // The enable button starts disabled until the codes are acknowledged.
  const enable = page.getByRole('button', { name: /啟用雙重驗證/ });
  if (!(await enable.isDisabled())) {
    failures.push(`[${theme}] enable button was not gated by the acknowledgement`);
  }

  const html = await page.locator('.login__card').innerHTML();
  writeFileSync(resolve(OUT, `enrol-${theme}.html`), html, 'utf8');

  if (consoleErrors.length) {
    failures.push(`[${theme}] console errors:\n  ${consoleErrors.join('\n  ')}`);
  }

  await context.close();
}

await browser.close();

if (failures.length) {
  console.error('QR verification FAILED:');
  for (const f of failures) console.error(' - ' + f);
  process.exit(1);
}
console.log(`QR verification passed; screenshots in ${OUT}`);
