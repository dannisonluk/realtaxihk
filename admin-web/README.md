# RealTaxi HK — admin console

The web console for the platform's management and admin teams: the KYC queue, the
refund decisions, the weekly platform settlement, and the taxi-fleet register with
its rosters and fleet-level settlement.

There are **two builds**, and they live side by side. `serve.py --dist` picks
between them; without the flag the legacy one is served.

| | entry | build |
|---|---|---|
| **React (current)** | `web/` | Vite + React 18 + TypeScript — `npm run build` → `web/dist` |
| **Legacy** | `index.html`, `js/`, `styles.css` | none — hand-written ES modules |

The React rewrite is the supported console. The legacy bundle is kept because it
still works and it is the reference the rewrite was verified against; remove it
only once nothing depends on it.

### The legacy build — a zero-build ES-module SPA

No bundler, no transpiler, no `package.json`. `index.html` loads `js/app.js` as a
module and the browser does the rest.

```
admin-web/
  web/                React + Vite + TypeScript (see "The React build" below)
  index.html          legacy shell; loads js/app.js as a module
  styles.css          legacy stylesheet
  serve.py            static server (MIME allow-list, no-store, path containment)
  js/                 legacy console — hash router, api, dom helpers, views/
  tool/
    verify_ui.mjs           browser-driven verifier (Playwright)
    reset_signin_budget.py  clears the dev OTP budget so the verifier can re-run
```

## Running it

The API must be up first, and the console is served as static files:

```bash
# API on :8000 (see the root README for the full stack)
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000

# the React console — build once, then serve
cd admin-web/web && npm install && npm run build && cd ../..
.venv/Scripts/python.exe admin-web/serve.py --dist

# the legacy console, unchanged
.venv/Scripts/python.exe admin-web/serve.py
.venv/Scripts/python.exe admin-web/serve.py --port 8081   # or pick a port
```

Then open <http://127.0.0.1:3000>.

## The React build

Vite + React 18 + TypeScript, in `web/`. The rewrite is not a re-skin: three
things from the legacy build are load-bearing and were carried over deliberately.

1. **Sequential multi-call loading** — `useLoad` / `inOrder` in
   `src/app/useLoad.ts`. The browser opens at most **six** sockets per origin, and
   this environment intermittently accepts a connection and then never answers it.
   A route that fires six calls at once parks the entire socket pool on a wedged
   upstream, and the *next* request — even a static file — cannot get one. Calls
   are issued one at a time so there is never more than one in flight.
2. **Server data never becomes markup** — there is no `dangerouslySetInnerHTML`
   anywhere. Driver notes, fleet names and contact details are all
   attacker-controlled strings and this console renders every one of them.
3. **Hash routing** (`createHashRouter`) — the console is static files with no
   rewrite rule, so a History-API deep link would 404 before the app loaded.

**Types are hand-written from `app/api/admin.py`**, which is the authority.
Two things that are easy to get wrong:

- **Money is a `string` on the wire** (the server formats it with `money_str`).
  Typing it as `number` renders `NaN` after the first arithmetic.
- **Two different precisions, and they are not interchangeable.** *Stored* money
  — deposits, ledger amounts, refunds, order totals — matches its
  `Numeric(10, 2)` column and arrives at **2 dp** (`"500.00"`, `"0.05"`). A
  *meter* figure — a fare, a toll, a surcharge — comes from the tariff table and
  arrives at **1 dp** (`"147.1"`). Both render through `Money`, which does not
  care, but do not assume a money string always has two decimals when comparing
  or parsing.
- **`FleetStatus` ends in `DISSOLVED`, not `TERMINATED`** — a fleet is wound up,
  a *driver* is terminated.

```
admin-web/web/
  index.html            Vite entry — the one thing that is not under src/
  vite.config.ts        base './', dev proxy for /api with a 3s upstream timeout
  tsconfig.json         strict + noUnusedLocals + noUncheckedIndexedAccess
  src/
    api/                client, session, types (from app/api/admin.py), endpoints
    app/                AppProvider, useLoad, useDialogs, Shell, routes
    components/         primitives (Card/Chip/Money/Percent), states, toasts
    lib/labels.ts       status vocabulary and the money/percent formatters
    pages/              one component per route
    styles.css          design tokens (iOS type scale, 4pt grid, 44px targets)
```

Verify it the same way as the legacy build — the verifier is build-agnostic:

```bash
.venv/Scripts/python.exe admin-web/serve.py --port 8081 --dist
NODE_PATH="$HOME/.workbuddy-ai/binaries/node/workspace/node_modules" \
  node admin-web/tool/verify_ui.mjs --base http://127.0.0.1:8081 \
    --phone +85290000001 --code 123456
```

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

### Same-origin mode (no CORS at all)

`serve.py` reverse-proxies `/api/*` and `/health` to `--api-target`
(default `http://127.0.0.1:8000`). Open the console with `?api=same-origin` and it
talks to that proxy instead of to `:8000` directly:

```
http://127.0.0.1:8081/?api=same-origin
```

The whole app is then one origin, so every call is a *simple* request — no
`OPTIONS` preflight at all. Two reasons to prefer it:

* **The API is not directly reachable from the browser** (a tunnel, a container
  network, a production reverse proxy). One port, one origin, no CORS list to keep
  in sync.
* **No preflight to go wrong.** A preflighted request is two round-trips, and any
  layer that answers the `OPTIONS` but drops the follow-up `GET` breaks the
  console in a way that looks like a product bug. The UI verifier runs this way
  for exactly that reason.

It is a deployment convenience, not a replacement for CORS: leave `cors_origins`
populated so the split-origin setup keeps working.

## Verifying it

There are no unit tests for the console — it is DOM code, and DOM code that is only
read is not verified. The verifier drives the real thing in a real browser against
the real API, and it is **build-agnostic**: point `--base` at either server.

```bash
# legacy build
.venv/Scripts/python.exe admin-web/serve.py --port 8081 &
# or the React build
.venv/Scripts/python.exe admin-web/serve.py --port 8081 --dist &

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

### Re-running it: the sandbox drops one response in a burst

On the machine this was built on, a uvicorn process **intermittently** stops
answering after roughly nine requests, and a request already accepted blocks in
`getresponse()` for 90s+ instead of failing. It comes and goes — the same probe
that reports `9/20 ok` on one run reports `15/15 ok` minutes later — so it tracks
machine load (a security agent inspecting loopback traffic is the prime suspect),
not anything in this repository. It is **not** the console and **not** the proxy:
the decisive probes below run with neither in the picture.

The clearest measurement: 15 sequential requests straight at the API, no browser
and no proxy.

```
  conn  0  200  0.06s
  ...
  conn  8  200  0.02s        <- nine answered
  conn  9  FAIL TimeoutError 10.02s   <- and then every later one, permanently
  conn 10  FAIL TimeoutError 10.01s
```

The same shape shows through the proxy on the verifier's own sign-in flow —
`otp/request`, `otp/verify`, `auth/me` and six dashboard calls are exactly nine
requests, so the burst spends the whole budget and the first route check dies.
The server logs the nine 200s and then nothing for the tenth:

```
GET  /api/v1/admin/fleets?limit=100&offset=0            -> 200 17.6ms
GET  /api/v1/admin/fleets?status_filter=ACTIVE...       -> API unreachable: [WinError 10054]
```

Evidence, all reproducible with the scripts in `scripts/`:

| Probe | Result |
|---|---|
| `scripts/probe_sequential.py` (20 requests, one after another) | 9 ok, then **every** later request times out |
| `scripts/probe_concurrency.py` (10 at once) | 9 ok, 1 times out at 30s |
| `scripts/probe_retry.py` (3 retries each) | the wedged requests fail **all three** times |
| `scripts/probe_fleet_http.py` (the console's 6 calls, direct) | server logs 6×200 in ~80ms; one client times out at 90s |
| bare `http.server` on the **same port** (20 requests) | **20/20 ok** — the OS loopback stack is fine |
| 20 requests over **one reused** connection | still stops at 9 — it is not a connection count |
| a fresh API process, same probe | a fresh run of up to 15+ requests |

Because the wedge is per-connection, a **fresh** connection is a real second
chance. Four mitigations exploit that rather than pretending to fix it:

1. **The proxy fails fast, and fast is load-bearing.** `serve.py` bounds the
   upstream at `UPSTREAM_TIMEOUT_S` (default **3s**, `SERVE_PROXY_TIMEOUT` to
   override) and answers `502` when it is exceeded.

   The number is not cosmetic. Chromium opens at most **six** sockets to one
   origin, and while six forwarded requests are parked on a wedged upstream those
   six sockets are held for the whole timeout. At 10s, a route that fires six
   calls at once parks the entire pool for ten seconds and the *next* request —
   even a static file — cannot get a socket; the run then stalls after a handful
   of routes, in the browser rather than in this server. That is exactly the
   failure that was observed (death after the third route, every time). 3s is
   still an order of magnitude above the p100 for a healthy request (<200ms), so
   it never fires on a working API, and a wedged batch frees the pool three times
   sooner.
2. **The API is restarted, not just retried.** `scripts/api_supervisor.py`
   health-checks the API and restarts uvicorn the moment it stops answering; a
   fresh process serves a fresh budget. `scripts/serve_and_run_browser.py` starts
   the API this way. Without it the console cannot code around a server that has
   stopped answering, and a retry just re-hits the dead process.
3. **The client retries.** `api.js` retries a **GET** up to three times on a
   transport failure *and* on `502/503/504`, each on a new connection. Writes are
   never retried.
4. **The verifier retries the load.** `loadWithRetry` in `tool/verify_ui.mjs`
   re-loads a route up to three times and reports how many attempts it took, so a
   run that is short one response still passes while a genuinely broken route
   still fails all three.

`SERVE_PROXY_TIMING=1` prints a line per proxied request
(`proxy GET /api/v1/… -> 200 16ms`). It is the only way to tell a stall in the
console from a stall in the browser, and the verifier harness turns it on by
default.

A run that fails at sign-in is worth repeating once. If it keeps failing, the
first thing to check is whether a security agent is filtering loopback
connections, and the second is `reset_signin_budget.py` (above).

## Rules the code holds to

**Server data never becomes markup.** Every view builds nodes and assigns
`textContent`; the React build has no `dangerouslySetInnerHTML` anywhere. Driver
refund notes, fleet contact names and licence numbers are all attacker-controlled
strings and this console renders them, so there is deliberately no `html` escape
hatch in `js/dom.js` and no raw-HTML escape hatch in `web/src/`. If a view needs
markup, it composes elements.

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
