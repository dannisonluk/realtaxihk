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
 *  3. Only then mount the shell.
 */

import { useEffect, useState } from 'react';
import { createHashRouter, RouterProvider } from 'react-router-dom';
import { ApiClient } from '../api/client';
import { endpoints } from '../api/endpoints';
import { session } from '../api/session';
import { AppProvider } from './AppContext';
import { Shell } from './Shell';
import { LoginPage } from '../pages/LoginPage';
import { DashboardPage } from '../pages/DashboardPage';
import { KycPage } from '../pages/KycPage';
import { RefundsPage } from '../pages/RefundsPage';
import { SettlementPage } from '../pages/SettlementPage';
import { FleetsPage } from '../pages/FleetsPage';
import { FleetDetailPage } from '../pages/FleetDetailPage';
import { DriverDetailPage } from '../pages/DriverDetailPage';

/**
 * The two detail routes read their own param via `useParams`, so there is no
 * wrapper here to narrow it — the page's own default (`?? ''`) is the guard, and
 * an empty id produces the server's own 404 rather than a silent blank page.
 */
const router = createHashRouter([
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: 'drivers/:driverId', element: <DriverDetailPage /> },
      { path: 'kyc', element: <KycPage /> },
      { path: 'refunds', element: <RefundsPage /> },
      { path: 'settlement', element: <SettlementPage /> },
      { path: 'fleets', element: <FleetsPage /> },
      { path: 'fleets/:fleetId', element: <FleetDetailPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]);

function NotFoundPage() {
  return (
    <>
      <div className="page-head">
        <h1>找不到頁面</h1>
      </div>
      <div className="card">
        <div className="empty">沒有對應此網址的頁面。</div>
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
  const [phase, setPhase] = useState<'checking' | 'out' | 'in'>(
    session.isSignedIn ? 'checking' : 'out',
  );
  const [signInError, setSignInError] = useState<string | null>(null);

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
            refreshToken: stored.refreshToken,
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
          setSignInError(cause instanceof Error ? cause.message : '無法驗證登入狀態。');
        }
        setPhase('out');
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [phase]);

  if (phase === 'checking') {
    return <div className="empty">正在驗證登入狀態…</div>;
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
      <RouterProvider router={router} />
    </AppProvider>
  );
}

export function App() {
  return <Boot />;
}
