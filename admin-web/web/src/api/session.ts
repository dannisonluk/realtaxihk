/**
 * The admin session.
 *
 * Where the tokens live, and why
 * ------------------------------
 * `sessionStorage`, not `localStorage`.
 *
 * Neither is safe against XSS — any script running on this origin can read both.
 * The difference is blast radius: `localStorage` survives a tab close, a browser
 * restart and a machine reboot, so a token lifted by a single injected script
 * stays usable for its whole lifetime. `sessionStorage` dies with the tab, which
 * caps that window at the operator's working session. React's default escaping
 * also means there is no `dangerouslySetInnerHTML` on server data anywhere in
 * this app — see `src/components/`.
 *
 * The correct design is a refresh token in an `HttpOnly; Secure; SameSite=Strict`
 * cookie set by the server, which script cannot read at all, with CSRF defence on
 * the state-changing routes. That needs a backend endpoint that issues the cookie
 * and a CSRF token, so it is a deliberate follow-up rather than something this
 * module can fake. Until then: session-scoped storage, and the access token is
 * short-lived (the server rotates the refresh token on every use and revokes the
 * whole family on replay, so a leak is detectable).
 *
 * No secrets are written to disk, and nothing is logged.
 */

const KEY = 'realtaxi.admin.session';

export interface AdminUser {
  id: string;
  phone_masked: string;
  role: string;
}

export interface StoredSession {
  accessToken: string;
  refreshToken: string;
  user: AdminUser | null;
}

let cached: StoredSession | null = null;

function read(): StoredSession | null {
  if (cached) return cached;
  try {
    const raw = sessionStorage.getItem(KEY);
    cached = raw ? (JSON.parse(raw) as StoredSession) : null;
  } catch {
    // Corrupt or unreadable (private mode, storage disabled): treat as signed out
    // rather than throwing on boot.
    cached = null;
  }
  return cached;
}

export const session = {
  get(): StoredSession | null {
    return read();
  },

  get accessToken(): string | null {
    return read()?.accessToken ?? null;
  },

  get refreshToken(): string | null {
    return read()?.refreshToken ?? null;
  },

  get user(): AdminUser | null {
    return read()?.user ?? null;
  },

  get isSignedIn(): boolean {
    return Boolean(read()?.accessToken);
  },

  /**
   * The server role. Routing keys off this, but it is **not** a security
   * boundary — every admin route re-reads the live user row server-side
   * (`require_admin`), so a forged value here only produces 403s.
   */
  get isAdmin(): boolean {
    return read()?.user?.role === 'ADMIN';
  },

  save({ accessToken, refreshToken, user }: {
    accessToken: string;
    refreshToken: string;
    user?: AdminUser | null;
  }): StoredSession {
    const next: StoredSession = {
      accessToken,
      refreshToken,
      user: user ?? read()?.user ?? null,
    };
    cached = next;
    try {
      sessionStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      // Storage unavailable: the session still works for this page load, it just
      // will not survive a reload.
    }
    return next;
  },

  clear(): void {
    cached = null;
    try {
      sessionStorage.removeItem(KEY);
    } catch {
      /* nothing to do */
    }
  },
};
