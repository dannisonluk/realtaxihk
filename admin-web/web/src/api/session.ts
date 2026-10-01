/**
 * The admin session.
 *
 * What is stored here, and what is deliberately not
 * -------------------------------------------------
 * This object holds the **access token** and the signed-in identity. It does
 * **not** hold the refresh token.
 *
 * The refresh token lives in an `HttpOnly; Secure; SameSite=Strict` cookie set
 * by the API (`app/core/admin_cookies.py`). Script cannot read it — which is the
 * entire point, because the refresh token is the long-lived half of the
 * credential. A copy of it in `sessionStorage` would be readable by any injected
 * script, and the cookie would then be doing no work at all.
 *
 * That design needs two things from this side, both here or in `client.ts`:
 *   1. Every request that should carry the cookie must be sent with
 *      `credentials: 'include'`. A cross-origin `fetch` omits cookies by
 *      default, so forgetting this looks exactly like "the server lost my
 *      session".
 *   2. The refresh call must echo the CSRF token from the readable
 *      `realtaxi_admin_csrf` cookie in an `X-CSRF-Token` header. The browser
 *      attaches the refresh cookie automatically and an attacker can exploit
 *      that; they cannot read the CSRF value to forge the header. That pair —
 *      one value the browser sends for us, one only our origin can read — is
 *      the whole of the CSRF defence.
 *
 * The access token stays in `sessionStorage` rather than memory-only so a
 * reload does not sign the operator out. It is short-lived (15 minutes,
 * `SEC-18`) and the server also checks a Redis revocation epoch, so the window
 * in which a lifted access token is useful is small and closable.
 *
 * No secrets are written to disk, and nothing is logged.
 */

import type { AdminIdentity } from './types';

const KEY = 'realtaxi.admin.session';

/**
 * The signed-in account, as `GET /auth/me` describes it.
 *
 * An alias rather than a second declaration. There were two structurally
 * identical-but-separately-maintained types for this — `AdminIdentity` in
 * `types.ts` and this one — and they drifted: `AdminIdentity` was widened for
 * admin scope while this copy kept `phone_masked` required and never gained
 * `username`, so the sidebar compiled against a shape the server does not send.
 * One definition, imported, means they cannot disagree again.
 */
export type AdminUser = AdminIdentity;

export interface StoredSession {
  accessToken: string;
  user: AdminUser | null;
}

/**
 * The CSRF cookie's name, and its `__Host-` variant.
 *
 * Two spellings because the server picks the prefix from the environment: a
 * production deploy sets `__Host-realtaxi_admin_csrf` (enforced by the browser
 * to mean Secure + path=/ + no Domain), while http dev uses the bare name. Both
 * are read so the console works on either without a build flag.
 */
const CSRF_COOKIE_NAMES = ['__Host-realtaxi_admin_csrf', 'realtaxi_admin_csrf'];

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

/** Read one cookie's value, or `null` if it is not set on this origin. */
function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(';')) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      return decodeURIComponent(trimmed.slice(prefix.length));
    }
  }
  return null;
}

export const session = {
  get(): StoredSession | null {
    return read();
  },

  get accessToken(): string | null {
    return read()?.accessToken ?? null;
  },

  /**
   * The CSRF token to echo in `X-CSRF-Token`, or `null` before sign-in.
   *
   * Not a secret and not treated as one: it exists to prove the request came
   * from a script on this origin. It is read from a **non**-`HttpOnly` cookie
   * because this — the console's own code — is exactly the caller that is
   * supposed to read it.
   */
  get csrfToken(): string | null {
    for (const name of CSRF_COOKIE_NAMES) {
      const value = readCookie(name);
      if (value) return value;
    }
    return null;
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

  save({ accessToken, user }: { accessToken: string; user?: AdminUser | null }): StoredSession {
    const next: StoredSession = {
      accessToken,
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
