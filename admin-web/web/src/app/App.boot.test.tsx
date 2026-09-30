/**
 * The boot gate, driven from a real sign-in to a real screen.
 *
 * Why this test exists
 * --------------------
 * The UI verifier found that a *successful* admin sign-in landed the operator
 * on **找不到頁面** — the router's `*` route — even though the token was valid
 * and every console route worked once reached by hand. The address bar read
 * `#/` the whole time.
 *
 * Two things had to be true at once, which is why it hid for so long:
 *
 *  - `LoginPage` renders *instead of* the router while signed out, and never
 *    touched the hash, so it handed the router `#/login`.
 *  - `createHashRouter` resolves the hash **at mount**. The old code corrected
 *    the hash in a *passive* effect keyed off `phase`, which React runs after
 *    committing the render that mounts the router. So the router resolved
 *    `#/login`, matched nothing, and settled on `*`. `history.replaceState`
 *    fires no `hashchange`, so the router was never told and stayed there.
 *
 * The lesson this test encodes: **assert on the screen, not on the URL.** The
 * old code asserted `location.hash === '#/'` and passed — it *was* `#/`, while
 * the screen said 找不到頁面. Only the rendered heading distinguishes them.
 *
 * React 18 quirk worth knowing: these tests wrap `<App />` in `<StrictMode>` to
 * match `main.tsx`, and StrictMode double-invokes effects. That is not a
 * nuisance here — it is the stricter of the two modes, and the suite should
 * fail on a fix that only works without it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StrictMode, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { session } from '../api/session';
import { App } from './App';

// React 18's `act` needs this flag set before any render, or every update warns.
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/** The `*` route's heading. If this ever renders, the gate has failed. */
const NOT_FOUND = '找不到頁面';
const DASHBOARD = '總覽';
const LOGIN = '管理員登入';

/** A signed-in admin, as `GET /auth/me` returns it. */
const ADMIN_ME = {
  id: '11111111-2222-3333-4444-555555555555',
  username: 'operator',
  email_masked: 'o***r@example.com',
  role: 'ADMIN',
};

/**
 * Install a `fetch` that answers only the routes given. Anything else rejects,
 * so a test that accidentally depends on a real endpoint fails loudly rather
 * than hanging on a live socket.
 *
 * `delayMs` is not decoration. The defect this file guards against needs the
 * `/auth/me` probe to be *slow*: the router has to stay parked on the stale
 * `#/login` long enough for React to commit a render with it. Resolve
 * instantly and `act` flushes the whole phase transition in one batch, the
 * router never sees `#/login`, and the bug hides — which is precisely how it
 * survived a green test suite. A few milliseconds is enough to expose it.
 */
function stubTransport(routes: Record<string, unknown>, delayMs = 0) {
  vi.stubGlobal('fetch', (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    const answer = (body: unknown) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    for (const [fragment, body] of Object.entries(routes)) {
      if (url.includes(fragment)) {
        if (delayMs > 0) {
          return new Promise<Response>((resolve) =>
            setTimeout(() => resolve(answer(body)), delayMs),
          );
        }
        return Promise.resolve(answer(body));
      }
    }
    return Promise.reject(new Error(`unstubbed request: ${url}`));
  });
}

/** Seed a session, as a completed sign-in would have. */
function seedSession() {
  sessionStorage.setItem(
    'realtaxi.admin.session',
    JSON.stringify({
      accessToken: 'stub-access-token',
      refreshToken: '',
      user: { id: ADMIN_ME.id, phone_masked: '', role: 'ADMIN' },
    }),
  );
}

/** Render, then let the async probe settle. */
async function renderAndSettle(root: Root) {
  await act(async () => {
    root.render(
      <StrictMode>
        <App />
      </StrictMode>,
    );
  });
  // Render once while `phase` is still `checking` — nothing async has resolved
  // yet — so React has committed a frame the router could have resolved
  // against. Then let the probe land.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  // A second, longer turn lets a slow `/auth/me` resolve and the resulting
  // phase transition run to completion.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });
}

describe('the boot gate', () => {
  let container: HTMLDivElement;
  let root: Root;

  /**
   * The visible page title.
   *
   * Queried as `.page-head h1` **or** `.login .t-title1` on purpose: the shell
   * pages use a real `<h1>` inside `.page-head`, but `LoginPage` styles its
   * title with `.t-title1` and no `<h1>` at all. Querying only `h1` returns
   * `null` on the login screen, which reads as "nothing rendered" when in fact
   * the form is there — a false negative that would hide a real regression.
   */
  const heading = () => {
    const el =
      container.querySelector('.page-head h1') ??
      container.querySelector('.login .t-title1') ??
      container.querySelector('h1');
    return el?.textContent ?? null;
  };

  beforeEach(() => {
    sessionStorage.clear();
    // `session.ts` memoises the parsed session in a module-level variable, which
    // survives across tests in this file. Clearing storage alone is not enough:
    // a previous test's signed-in session would still be returned by
    // `session.isSignedIn`, so the "no session" case would boot straight into
    // `checking`. Reset through the module's own API.
    session.clear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    session.clear();
    sessionStorage.clear();
  });

  /**
   * The regression. `#/login` is the hash the signed-out gate writes, so it is
   * the hash a real post-sign-in render inherits.
   */
  it('renders the dashboard, not the not-found route, after signing in', async () => {
    window.location.hash = '#/login';
    seedSession();
    // A deliberately slow probe: the real defect needs the gap between
    // `phase='checking'` and `phase='in'` to be long enough for the router to
    // commit against the stale hash.
    stubTransport({ '/auth/me': ADMIN_ME }, 10);

    await renderAndSettle(root);

    expect(heading()).not.toBe(NOT_FOUND);
    expect(heading()).toBe(DASHBOARD);
    // The URL and the screen must agree — that is the whole point. Asserting
    // this alone is what the old test did, and it is not sufficient on its own.
    expect(window.location.hash).toBe('#/');
  });

  /**
   * A bare URL with no hash has no path for `createHashRouter` to match, so it
   * falls through to `*` — the same symptom one layer down.
   */
  it('renders the dashboard when the URL has no hash at all', async () => {
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
    seedSession();
    stubTransport({ '/auth/me': ADMIN_ME });

    await renderAndSettle(root);

    expect(heading()).toBe(DASHBOARD);
  });

  /**
   * A deep link must survive the boot gate.
   *
   * This is the regression the UI verifier caught *after* the 找不到頁面 fix
   * landed: the hash-reconciling layout effect had no `checking` branch, so a
   * cold load of `#/analytics` with a stored session fell through to the
   * signed-out case, rewrote the hash to `#/login`, and then normalised that to
   * `#/` on the way in. Every deep link — and every reload of one — silently
   * became the dashboard, which is why the verifier's route checks all read the
   * same body text and every screenshot came out identical.
   *
   * The assertion that matters is the pair: the *screen* and the *hash*. A test
   * that checked only the screen would pass on a fix that redirected to the
   * dashboard and then rendered it, which is a different bug.
   */
  it('keeps a deep link instead of falling back to the dashboard', async () => {
    window.location.hash = '#/analytics';
    seedSession();
    // Slow, for the same reason as the first test: the clobber happened during
    // the `checking` window, so that window has to be wide enough to exist.
    stubTransport({ '/auth/me': ADMIN_ME }, 10);

    await renderAndSettle(root);

    expect(heading()).toBe('表現分析');
    expect(window.location.hash).toBe('#/analytics');
  });

  /** Signed out: the form renders, and the form owns the hash. */
  it('renders the login form when there is no session', async () => {
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
    stubTransport({});

    await renderAndSettle(root);

    expect(heading()).toBe(LOGIN);
    expect(window.location.hash).toBe('#/login');
  });

  /**
   * A session that the server has since rejected must not show a console. The
   * 401 is answered with the error envelope, matching `app/core/exceptions.py`.
   */
  it('falls back to the login form when /auth/me rejects the session', async () => {
    window.location.hash = '#/login';
    seedSession();
    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        new Response('{"code":"UNAUTHORIZED","message":"not authenticated","details":{}}', {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );

    await renderAndSettle(root);

    expect(heading()).not.toBe(NOT_FOUND);
    expect(heading()).toBe(LOGIN);
  });
});
