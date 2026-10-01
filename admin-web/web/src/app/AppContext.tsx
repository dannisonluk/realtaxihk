/**
 * The console's app-wide state: the API client, the signed-in operator, the
 * toast queue, and the sidebar badge counts.
 *
 * Split out of the shell so a page can call `useApp()` instead of receiving six
 * props it mostly ignores — the vanilla build threaded `{client, navigate,
 * refreshBadges}` through every view constructor.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { ApiClient } from '../api/client';
import { endpoints } from '../api/endpoints';
import { session, type AdminUser } from '../api/session';
import type { AdminRole } from '../api/types';
import { roleAtLeast } from '../api/types';
import { i18n } from '../i18n';

export interface Toast {
  id: number;
  message: string;
  tone: 'info' | 'error';
}

interface AppState {
  client: ApiClient;
  user: AdminUser | null;
  /**
   * The signed-in operator's role, or `null` before `/auth/me` has answered.
   *
   * A **display** fact, not a security boundary. `require_role` re-reads the
   * live `admin_accounts` row on every request, so a role forged in
   * `sessionStorage` produces 403s rather than access. What this value does is
   * decide which pages are worth rendering: offering an operator a button that
   * can only 403 is the failure mode being avoided, and it is a failure of
   * *affordance*, not of authority.
   */
  role: AdminRole | null;
  /** Whether the signed-in operator's role is at least `minimum`. */
  hasRole: (minimum: AdminRole) => boolean;
  toasts: Toast[];
  notify: (message: string, tone?: Toast['tone']) => void;
  dismissToast: (id: number) => void;
  badges: { pendingKyc: number | null; pendingRefunds: number | null };
  setBadges: (next: Partial<{ pendingKyc: number; pendingRefunds: number }>) => void;
  refreshBadges: () => Promise<void>;
  signOut: () => Promise<void>;
  onSignedOut: () => void;
}

const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const value = useContext(AppContext);
  // A programming error, not a user-facing condition — but it can surface in a
  // crash overlay, so it goes through the same translations as everything else.
  if (!value) throw new Error(i18n.t('errors.provider'));
  return value;
}

export function AppProvider({
  onSignedOut,
  children,
}: {
  onSignedOut: () => void;
  children: ReactNode;
}) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextToastId = useRef(1);

  const dismissToast = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const notify = useCallback(
    (message: string, tone: Toast['tone'] = 'info') => {
      const id = nextToastId.current++;
      setToasts((current) => [...current, { id, message, tone }]);
      // Auto-dismiss, but leave failures up long enough to read: this console
      // reports money-moving errors through here.
      window.setTimeout(() => dismissToast(id), tone === 'error' ? 9000 : 4500);
    },
    [dismissToast],
  );

  // One client for the app's lifetime. `onSessionExpired` must not be re-created
  // per render or the client would hold a stale closure over `notify`.
  const client = useMemo(
    () =>
      new ApiClient({
        onSessionExpired: () => {
          session.clear();
          onSignedOut();
        },
      }),
    [onSignedOut],
  );

  const [badges, setBadgesState] = useState<{
    pendingKyc: number | null;
    pendingRefunds: number | null;
  }>({ pendingKyc: null, pendingRefunds: null });

  const setBadges = useCallback((next: Partial<{ pendingKyc: number; pendingRefunds: number }>) => {
    setBadgesState((current) => ({ ...current, ...next }));
  }, []);

  /** Best-effort: a failure here must not break the shell. */
  const refreshBadges = useCallback(async () => {
    try {
      // Sequential, not `Promise.all`. The console runs against an environment
      // that intermittently accepts a connection and then never answers it, and
      // every parallel call is another chance to land on that path — see the
      // docstring in `useLoad.ts`, which is the project's own statement of this
      // rule. Two calls at ~50-150ms each is not worth the risk.
      const kyc = await endpoints.drivers.list(client, { status: 'PENDING_KYC', limit: 1 });
      const refunds = await endpoints.refunds.list(client, { status: 'PENDING', limit: 1 });
      setBadges({ pendingKyc: kyc.total ?? 0, pendingRefunds: refunds.total ?? 0 });
    } catch {
      // Leave the previous numbers; the pages themselves report real errors.
    }
  }, [client, setBadges]);

  const signOut = useCallback(async () => {
    try {
      // Revokes every access and refresh token for this account, so signing out
      // on a shared machine is real rather than cosmetic.
      await endpoints.auth.logout(client);
    } catch {
      // Even if the server call fails, drop the local session.
    }
    session.clear();
    onSignedOut();
  }, [client, onSignedOut]);

  // Expose the operator reactively: `session` is a plain object, so a change to
  // it does not re-render anything on its own.
  const [user, setUser] = useState<AdminUser | null>(() => session.user);
  useEffect(() => {
    setUser(session.user);
  }, []);

  /**
   * The RBAC rank, read off `admin_role` on the signed-in identity.
   *
   * **`admin_role`, never `role`.** The two fields on the same token answer
   * different questions and only one of them is a rank:
   *
   *   - `role` is the **principal kind** — always the literal `'ADMIN'` for an
   *     admin token. It is what the boot gate checks ("is this an admin session
   *     at all"). It is **not** a key in `ROLE_RANK`, so reading it here ranks
   *     every operator below `SUPPORT`, the nav filters to nothing, and every
   *     gated page renders 沒有存取權限 — on a perfectly valid login.
   *   - `admin_role` is the **rank** — `SUPPORT` … `SUPER_ADMIN`. This is the
   *     one the nav and `RequireRole` compare against.
   *
   * Both `POST /admin/auth/login` (`_admin_out`) and `GET /auth/me`
   * (`AdminMeOut`) send `admin_role`; only the latter also sends `role`. The
   * login response is what `session.save()` stores, so a `role`-only read would
   * also have been reading a field the login response does not contain at all.
   *
   * An unrecognised or absent value yields `null` rather than a guess, and
   * `hasRole` then answers `false` for everything — the default-deny direction,
   * matching the server's rank comparison. The server re-reads the live row on
   * every request regardless, so this is an affordance decision only.
   */
  const role = (user?.admin_role ?? null) as AdminRole | null;

  const hasRole = useCallback(
    (minimum: AdminRole) => roleAtLeast(role, minimum),
    [role],
  );

  const value = useMemo<AppState>(
    () => ({
      client,
      user,
      role,
      hasRole,
      toasts,
      notify,
      dismissToast,
      badges,
      setBadges,
      refreshBadges,
      signOut,
      onSignedOut,
    }),
    [client, user, role, hasRole, toasts, notify, dismissToast, badges, setBadges, refreshBadges, signOut, onSignedOut],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
