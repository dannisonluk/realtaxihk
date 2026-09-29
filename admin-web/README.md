# RealTaxi HK — admin console

The web console for the platform's management and admin teams: the KYC queue, the
refund decisions, the weekly platform settlement, and the taxi-fleet register with
its rosters and fleet-level settlement.

It is a **zero-build ES-module SPA**. No bundler, no transpiler, no `package.json`,
no `node_modules`. `index.html` loads `js/app.js` as a module and the browser does
the rest. The reason is not minimalism for its own sake: this console holds the
admin session for a payments platform, and a build pipeline would mean a dependency
tree of hundreds of packages with install-time script execution — for a UI that is
a few thousand lines of `document.createElement`. There is nothing to build.

```
admin-web/
  index.html          shell; loads js/app.js as a module
  styles.css          one stylesheet, no preprocessor
  serve.py            static server (MIME allow-list, no-store, path containment)
  js/
    app.js            hash router, shell, boot sequence
    api.js            HTTP client + typed endpoint wrappers
    session.js        token storage (sessionStorage — see the caveat below)
    dom.js            element helpers; the "no innerHTML on server data" rule
    views/            one module per route
  tool/
    verify_ui.mjs           browser-driven verifier (Playwright)
    reset_signin_budget.py  clears the dev OTP budget so the verifier can re-run
```

## Running it

The API must be up first, and the console is served as static files:

```bash
# API on :8000 (see the root README for the full stack)
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000

# console on :3000 by default
.venv/Scripts/python.exe admin-web/serve.py
.venv/Scripts/python.exe admin-web/serve.py --port 8081   # or pick a port
```

Then open <http://127.0.0.1:3000>.

### Signing in

Signup always creates a `PASSENGER` (`app/services/otp_service.py`), so an admin
account has to be granted out of band:

```bash
.venv/Scripts/python.exe scripts/create_admin.py --list
.venv/Scripts/python.exe scripts/create_admin.py --phone +85290000001
```

The login screen checks the role and refuses a non-admin, rather than letting them
in to watch every request 403.

With `ALLOW_DEV_OTP=true` the code is the fixed `123456`. That switch is
fail-closed (rejected outright when `APP_ENV=prod`) and the code is **never** echoed
in the response body — `SEC-02`. Read it from the notify seam or use the constant.

### Ports and CORS

`cors_origins` in `app/core/config.py` defaults to four literal origins:

```
http://localhost:3000   http://127.0.0.1:3000
http://localhost:8081   http://127.0.0.1:8081
```

CORS origin matching is a **string comparison**. `http://localhost:8081` and
`http://127.0.0.1:8081` are different origins, and so are different ports. If you
serve the console somewhere else, the preflight fails and the login screen appears
to do nothing — extend that list. (Serving from `127.0.0.1` rather than `localhost`
is deliberate: on Windows `localhost` resolves to `::1` first, the published Docker
ports are IPv4-only, and the connect hangs for ~2s before falling back.)

## Verifying it

There are no unit tests for the console — it is DOM code, and DOM code that is only
read is not verified. The verifier drives the real thing in a real browser against
the real API:

```bash
.venv/Scripts/python.exe admin-web/serve.py --port 8081 &
NODE_PATH="$HOME/.workbuddy-ai/binaries/node/workspace/node_modules" \
  node admin-web/tool/verify_ui.mjs --base http://127.0.0.1:8081
```

`playwright` is resolved from `NODE_PATH`; the browsers are already downloaded
under `~/AppData/Local/ms-playwright`. Exit code 0 means every route rendered with
a clean console and no failed API calls. Screenshots land in `tool/.ui-check/`.

It checks three things reading the source cannot: a module that fails to load
(blank page, one console error), a render that throws (dead route), and a silent
API mismatch (a 422 the page renders as an error banner). It also clicks the
sidebar nav — every route check loads a URL, so a nav that renders *nothing* passes
all of them.

### Re-running it: the OTP budget

The verifier logs in through the real OTP flow, and that flow is deliberately
budgeted — `otp_ip_rate_limit` 10/600s, `otp_phone_rate_limit` 5/3600s, plus a 60s
resend cooldown. Those numbers are correct for production. But running the verifier
a few times in ten minutes from one address spends the budget, and the next run
gets a `429` that `login.js` correctly renders while staying on the phone step — so
the harness times out waiting for the code field and it *looks* like a broken
console. It is a spent budget. Clear it:

```bash
.venv/Scripts/python.exe admin-web/tool/reset_signin_budget.py --phone +85290000001
```

That deletes the rate-limit counters and the stale `otp_codes` rows for the number.
The rows matter as much as the counters: `verify_otp` reads the *newest* row, so a
leftover consumed row makes the next correct code fail with "OTP already used". The
script refuses to run when `APP_ENV=prod` without `--yes`.

## Rules the code holds to

**Server data never becomes markup.** Every view builds nodes and assigns
`textContent`. Driver refund notes, fleet contact names and licence numbers are all
attacker-controlled strings and this console renders them, so there is deliberately
no `html` escape hatch in `js/dom.js`. If a view needs markup, it composes elements.

**`append()` flattens nested arrays.** This is load-bearing, not a convenience. A
children array is the natural place to put a `.map()`, and the sidebar is built as
`[brand, NAV.map(navLink), foot]`. Before flattening, that inner array was not a
`Node`, so it fell through to `String(child)` — and stringifying anchors yields
their comma-joined `href`s. The nav rendered **zero links** and one long
`http://…#/,http://…#/kyc,…` text node, leaving the console unnavigable except by
typing hash URLs. The verifier now asserts the nav renders and that clicking it
routes.

**`table()` renders only the empty message when there are no rows** — no `<thead>`,
so no column labels. An operator sees the filter chips, not an empty grid. Worth
knowing when writing assertions: a check for a column label such as `金額` is
coupled to whether that queue happens to have data, and fails as a phantom "route
does not render" on a day it is simply empty. Assert on the page title, a chip, or
an action button instead.

## Known limitation

**The session lives in `sessionStorage`, not an `HttpOnly` cookie.** Neither
`localStorage` nor `sessionStorage` is safe against XSS — the difference is blast
radius: `sessionStorage` dies with the tab, which caps the window in which a lifted
token is usable. The correct design is a refresh token in an
`HttpOnly; Secure; SameSite=Strict` cookie set by the server, with CSRF defence on
the state-changing routes. That needs a backend endpoint that issues the cookie and
a CSRF token, so it is a deliberate follow-up rather than something the client can
fake. Until then the access token is short-lived, the server rotates the refresh
token on every use, and a replayed refresh token revokes the whole family — so a
leak is detectable. See `js/session.js`.

## Not yet built

- A decision history view for refunds and KYC (the API exposes the rows; the
  console only shows the queue).
- Fleet member bulk import — the roster is added one driver at a time.
- Anything resembling a design system. `styles.css` is one file and the components
  are `el()` compositions; that is deliberate at this size.
