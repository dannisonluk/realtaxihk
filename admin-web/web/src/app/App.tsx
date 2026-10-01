/**
 * Routing and the boot gate.
 *
 * Hash routing, because the console is served as static files with no rewrite
 * rule: a deep link to `/fleets/<id>` would 404 before the app loaded, whereas
 * `#/fleets/<id>` is resolved entirely client-side. React Router does this with
 * `createHashRouter`.
 *
 * Boot sequence, and why it is this order:
 *  1. If there is no stored session, render login.
 *  2. If there is one, re-validate it against `GET /auth/me` **before** rendering
 *     anything. The cached user object is not trusted: an admin whose role was
 *     revoked server-side would otherwise see a console that 403s on every
 *     request. `require_admin` re-reads the live user row, so the token alone is
 *     not proof of anything.
 *  3. Only then mount the shell — and only once the hash agrees with the phase
 *     we are mounting it for. Getting that order wrong is what produced the
 *     post-login **找不到頁面**; the layout effect below carries the full story.
 */

import { useEffect, useLayoutEffect, useMemo, useState } from 'react';
import { createHashRouter, RouterProvider } from 'react-router-dom';
import { ApiClient } from '../api/client';
import { endpoints } from '../api/endpoints';
import { session } from '../api/session';
import { AppProvider } from './AppContext';
import { RequireRole, Shell } from './Shell';
import { useI18n } from '../i18n';
import { LoginPage } from '../pages/LoginPage';
import { DashboardPage } from '../pages/DashboardPage';
import { KycPage } from '../pages/KycPage';
import { LicencePage } from '../pages/LicencePage';
import { AnalyticsPage } from '../pages/AnalyticsPage';
import { RefundsPage } from '../pages/RefundsPage';
import { SettlementPage } from '../pages/SettlementPage';
import { FleetsPage } from '../pages/FleetsPage';
import { FleetDetailPage } from '../pages/FleetDetailPage';
import { DriverDetailPage } from '../pages/DriverDetailPage';
import { OrdersPage } from '../pages/OrdersPage';
import { OrderDetailPage } from '../pages/OrderDetailPage';
import { DisputesPage } from '../pages/DisputesPage';
import { AuditPage } from '../pages/AuditPage';
import { AccountsPage } from '../pages/AccountsPage';
import { SearchPage } from '../pages/SearchPage';

/**
 * The route table.
 *
 * Deliberately a **function**, not a module-scope `createHashRouter(...)` call.
 *
 * `createHashRouter` is not lazy: it builds a `createHashHistory()` immediately
 * and that history reads `window.location` and subscribes to `hashchange` on
 * the spot — i.e. at import time, before React has rendered anything. Calling it
 * at module scope therefore pins the router to whatever hash the document
 * happened to have when the bundle was evaluated. If that was `#/login`, the
 * router is bound to `#/login` for the life of the page, and a later
 * `history.replaceState(...#/)` will not move it, because `replaceState` fires
 * no `hashchange` and the router is never notified.
 *
 * That is the whole of the post-login **找不到頁面** bug, and it is why the fix
 * has to be structural rather than a matter of effect ordering. Building the
 * router inside the component, only once the hash has already been corrected,
 * means it reads the right location on the one and only occasion it is created.
 *
 * The detail routes read their own param via `useParams`, so there is no
 * wrapper here to narrow it — the page's own default (`?? ''`) is the guard, and
 * an empty id produces the server's own 404 rather than a silent blank page.
 *
 * **Role gating is on the route, not in the sidebar.** Hiding a nav entry stops
 * nobody: the hash is editable and a bookmark is a URL. `RequireRole` renders an
 * explanation page, and — as its own docstring says — it is an affordance guard,
 * not a security boundary. The server answers 403 to a forged role, and these
 * pages surface that refusal rather than swallowing it.
 */
function buildRouter() {
  return createHashRouter([
    {
      path: '/',
      element: <Shell />,
      children: [
        { index: true, element: <DashboardPage /> },
        // Any role: this is where support starts, and it must never be the
        // thing that is gated.
        { path: 'search', element: <SearchPage /> },
        { path: 'orders', element: <OrdersPage /> },
        { path: 'orders/:orderId', element: <OrderDetailPage /> },
        { path: 'disputes', element: <DisputesPage /> },
        { path: 'disputes/:disputeId', element: <DisputesPage /> },
        {
          path: 'drivers/:driverId',
          element: (
            <RequireRole role="OPERATIONS">
              <DriverDetailPage />
            </RequireRole>
          ),
        },
        {
          path: 'kyc',
          element: (
            <RequireRole role="OPERATIONS">
              <KycPage />
            </RequireRole>
          ),
        },
        {
          path: 'licences',
          element: (
            <RequireRole role="OPERATIONS">
              <LicencePage />
            </RequireRole>
          ),
        },
        {
          path: 'analytics',
          element: (
            <RequireRole role="OPERATIONS">
              <AnalyticsPage />
            </RequireRole>
          ),
        },
        {
          path: 'refunds',
          element: (
            <RequireRole role="FINANCE">
              <RefundsPage />
            </RequireRole>
          ),
        },
        {
          path: 'settlement',
          element: (
            <RequireRole role="FINANCE">
              <SettlementPage />
            </RequireRole>
          ),
        },
        {
          path: 'fleets',
          element: (
            <RequireRole role="OPERATIONS">
              <FleetsPage />
            </RequireRole>
          ),
        },
        {
          path: 'fleets/:fleetId',
          element: (
            <RequireRole role="OPERATIONS">
              <FleetDetailPage />
            </RequireRole>
          ),
        },
        // Every role, deliberately — see the server route's docstring.
        { path: 'audit', element: <AuditPage /> },
        {
          path: 'accounts',
          element: (
            <RequireRole role="SUPER_ADMIN">
              <AccountsPage />
            </RequireRole>
          ),
        },
        { path: '*', element: <NotFoundPage /> },
      ],
    },
  ]);
}

function NotFoundPage() {
  const { t } = useI18n();
  return (
    <>
      <div className="page-head">
        <h1>{t('notFound.title')}</h1>
      </div>
      <div className="card">
        <div className="empty">{t('notFound.body')}</div>
      </div>
    </>
  );
}

/**
 * The gate in front of the router.
 *
 * It owns three states — checking, signed out, signed in — and does not mount
 * the router until it knows which. Rendering the shell optimistically would
 * flash an admin UI at someone whose role was just revoked.
 */
function Boot() {
  const { t } = useI18n();
  const [phase, setPhase] = useState<'checking' | 'out' | 'in'>(
    session.isSignedIn ? 'checking' : 'out',
  );
  const [signInError, setSignInError] = useState<string | null>(null);
  /**
   * Whether the hash has been reconciled with `phase`.
   *
   * This exists solely so the router is never mounted on a render whose hash
   * still says `#/login` — see the layout effect below. It starts `false`: on
   * the very first commit the hash is whatever the browser handed us, and until
   * the effect has inspected it we cannot know whether it is safe to mount the
   * router against it.
   */
  const [hashReady, setHashReady] = useState(false);
  /**
   * The router — created lazily, and only once the hash is right.
   *
   * `hashReady` is the dependency, so the memo computes on the render *after*
   * the layout effect corrected the hash, which is the first moment
   * `createHashRouter` can read the correct location. It is created exactly
   * once, then held for the life of the component: recreating it would hand
   * `<RouterProvider>` a fresh history and reset the operator's navigation.
   *
   * This is the structural half of the fix. The defect was never about effect
   * ordering — `createHashRouter` reads `window.location` and subscribes to
   * `hashchange` the moment it is called, so a module-scope call bound the
   * router to whatever hash the document had when the bundle evaluated.
   */
  const router = useMemo(() => (hashReady ? buildRouter() : null), [hashReady]);

  /**
   * The signed-out state owns the hash; the router never sees `#/login`.
   *
   * The ownership split, stated plainly: while signed out this component
   * renders the login form *instead of* the router, so it is the only thing
   * that knows the operator is looking at a login screen. That means the hash
   * is this component's to keep, in both directions:
   *
   *  - signed out → write `#/login`, so a reload lands back on the form;
   *  - signed in  → write `#/`  , so the router has a real path to resolve.
   *
   * The bug this replaces — post-login **找不到頁面** — was not an effect-ordering
   * problem, although it looked like one. The real cause is in `buildRouter`
   * above: `createHashRouter` binds to `window.location` the instant it is
   * called, and a module-scope call therefore bound the router to `#/login`
   * before React ever rendered. `history.replaceState` fires no `hashchange`,
   * so no later correction could ever reach it: the router stayed parked on its
   * `*` route, rendering 找不到頁面, while the address bar read `#/`.
   *
   * Hence the two halves of the fix, which have to be taken together:
   *   1. this effect corrects the hash, and sets `hashReady`;
   *   2. `router` is memoised on `hashReady`, so `createHashRouter` is called
   *      for the first time on the render *after* the correction — reading `#/`
   *      and nothing else.
   *
   * A layout effect rather than a passive one, because it runs before the
   * browser paints: the correction is not a user-visible step, and a passive
   * effect would let a frame of the wrong URL be painted first.
   *
   * `#/` and not an empty hash: `createHashRouter` resolves `#/` to the shell's
   * `index` route, whereas a bare URL with *no* hash has no path to match and
   * falls through to the same `*` route. And `search` is preserved throughout —
   * `?api=same-origin` is how the console finds its API, so dropping it would
   * break every request.
   */
  useLayoutEffect(() => {
    const base = window.location.pathname + window.location.search;
    // `checking` must be a no-op, and this early return is the whole of the
    // deep-link fix. A cold load of `#/analytics` with a stored session starts
    // in `checking`; without this branch the code below fell through to the
    // signed-out case and rewrote the hash to `#/login`, and the later
    // `checking -> in` transition then saw `#/login` and normalised it to `#/`.
    // Every deep link — `#/analytics`, `#/kyc`, `#/fleets/<id>` — therefore
    // landed on the dashboard, and the address bar agreed with the wrong page.
    // While the session is still being validated the hash is not ours to write:
    // we do not yet know which side of the login we are on.
    if (phase === 'checking') return;
    if (phase === 'in') {
      if (window.location.hash === '' || window.location.hash === '#/login') {
        window.history.replaceState(null, '', `${base}#/`);
      }
      setHashReady(true);
      return;
    }
    if (window.location.hash !== '#/login') {
      window.history.replaceState(null, '', `${base}#/login`);
    }
  }, [phase]);

  // Re-validate on every transition to `checking` — including after the user
  // signs in, which is why this keys off `phase` rather than running once.
  useEffect(() => {
    if (phase !== 'checking') return;
    let cancelled = false;

    void (async () => {
      // A throwaway client for the probe. It is deliberately *not* the one
      // `AppProvider` will build: the probe must not trigger the session-expired
      // callback that flips the app back out, and it does not need one — a 401
      // here is already handled by the phase transition below.
      const client = new ApiClient({});
      try {
        const me = await endpoints.auth.me(client);
        if (cancelled) return;
        if (me.role !== 'ADMIN') {
          session.clear();
          setPhase('out');
          return;
        }
        const stored = session.get();
        if (stored) {
          session.save({
            accessToken: stored.accessToken,
            user: me,
          });
        }
        setPhase('in');
      } catch (cause) {
        if (cancelled) return;
        // A 401 already cleared the session in the interceptor. Anything else
        // (offline, 5xx) is not proof the session is bad, so show the login
        // screen with the reason rather than silently discarding a usable token.
        if (session.isSignedIn) {
          setSignInError(cause instanceof Error ? cause.message : t('boot.verifyFailed'));
        }
        setPhase('out');
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [phase]);

  if (phase === 'checking') {
    return <div className="empty">{t('boot.verifying')}</div>;
  }

  if (phase === 'out') {
    return (
      <AppProvider onSignedOut={() => setPhase('out')}>
        {signInError ? (
          <div style={{ padding: 24 }}>
            <div className="message message--error" role="alert">
              {signInError}
            </div>
          </div>
        ) : null}
        <LoginPage
          onSignedIn={() => {
            setSignInError(null);
            setPhase('checking');
          }}
        />
      </AppProvider>
    );
  }

  return (
    <AppProvider onSignedOut={() => setPhase('out')}>
      {/*
        The router is built here, not at module scope, and only once the hash
        agrees with `phase`. Both halves matter — see `buildRouter` above. It is
        memoised because `RouterProvider` would otherwise be handed a fresh
        router (and thus a fresh history) on every parent render, resetting the
        navigation state under the operator.
      */}
      {router ? <RouterProvider router={router} /> : <div className="empty">{t('boot.loading')}</div>}
    </AppProvider>
  );
}

export function App() {
  return <Boot />;
}
