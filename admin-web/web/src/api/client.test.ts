/**
 * The API client's refresh path — the console half of the admin session fix.
 *
 * What this file is for
 * ---------------------
 * The server test (`tests/test_admin_session_cookie.py`) proves the cookie is
 * `HttpOnly`, rotated, CSRF-bound and revocable. None of that helps if the
 * console does not *send* it. This file pins the three client-side details that
 * make the server side work, each of which fails **silently** if it regresses:
 *
 *  1. `credentials: 'include'` on the refresh request. A cross-origin `fetch`
 *     defaults to `'omit'`, so dropping this means the cookie is never sent, the
 *     server answers "no refresh cookie", and the operator is signed out for no
 *     visible reason — with a perfectly well-formed request in the network tab.
 *  2. The `X-CSRF-Token` header, read from the readable CSRF cookie. Without it
 *     every refresh is a 401 by design.
 *  3. No refresh token in the body, and none expected in the response. If the
 *     client starts reading one from the body, the credential is back within
 *     reach of any injected script and the `HttpOnly` cookie is decoration.
 *
 * Everything is driven through a stubbed `fetch`, so these assert on the
 * request the client actually builds rather than on a mock's call signature.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from './client';
import { session } from './session';

/** The CSRF cookie the server sets and the console reads. */
const CSRF_COOKIE = 'realtaxi_admin_csrf';
const CSRF_VALUE = 'csrf-value-from-the-cookie';

interface Captured {
  url: string;
  init: RequestInit | undefined;
}

/**
 * Install a `fetch` stub that records every request and answers by URL fragment.
 *
 * `answer` receives the recorded request count so a test can make the *first*
 * call to a guarded route fail and the retry succeed — which is how the refresh
 * path is reached at all.
 */
function captureFetch(handler: (url: string, count: number) => Response) {
  const seen: Captured[] = [];
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    seen.push({ url, init });
    return Promise.resolve(handler(url, seen.filter((c) => c.url === url).length));
  });
  return seen;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** Set the CSRF cookie on the jsdom document, as the browser would. */
function setCsrfCookie(value = CSRF_VALUE) {
  document.cookie = `${CSRF_COOKIE}=${encodeURIComponent(value)}; path=/`;
}

describe('the admin refresh path', () => {
  beforeEach(() => {
    session.clear();
    sessionStorage.clear();
    // Clear any cookies a previous test left behind.
    for (const name of ['realtaxi_admin_csrf', '__Host-realtaxi_admin_csrf']) {
      document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
    }
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('reads the CSRF token from the cookie, not from storage', () => {
    setCsrfCookie('abc123');
    expect(session.csrfToken).toBe('abc123');
    // The value must not be mirrored into `sessionStorage` — it is not a secret,
    // but duplicating it there would suggest it is one, and the cookie is the
    // single source of truth for what the server expects.
    expect(sessionStorage.getItem('realtaxi.admin.session')).toBeNull();
  });

  it('has no refreshToken on the session at all', () => {
    // A compile-time property asserted at runtime: the field was removed from
    // `StoredSession` because the value lives in an `HttpOnly` cookie. If it
    // ever comes back, this fails and the reviewer is forced to look at why.
    seedAccessToken();
    expect(session.get()).not.toBeNull();
    expect(Object.keys(session.get()!)).not.toContain('refreshToken');
    expect((session as unknown as { refreshToken?: unknown }).refreshToken).toBeUndefined();
  });

  it('sends the CSRF header and includes credentials on refresh', async () => {
    setCsrfCookie();
    seedAccessToken();

    // The 401 on the guarded GET is what drives the refresh; the refresh then
    // succeeds, so the client retries the original request.
    const seen = captureFetch((url, count) => {
      if (url.includes('/api/v1/admin/auth/refresh')) {
        return json({
          access_token: 'fresh-token',
          token_type: 'bearer',
          admin: { id: '1', role: 'ADMIN' },
        });
      }
      if (url.includes('/api/v1/admin/drivers')) {
        // First attempt 401s to trigger the refresh; the retry succeeds.
        return count === 1 ? json({ code: 'UNAUTHORIZED' }, 401) : json({ items: [] });
      }
      throw new Error(`unstubbed: ${url}`);
    });

    const client = new ApiClient({ baseUrl: 'http://api.test' });
    await client.get('/api/v1/admin/drivers');

    const refreshCall = seen.find((c) => c.url.includes('/api/v1/admin/auth/refresh'));
    expect(refreshCall, 'the client never attempted a refresh').toBeDefined();

    const headers = new Headers(refreshCall!.init?.headers);
    expect(headers.get('X-CSRF-Token')).toBe(CSRF_VALUE);

    // The load-bearing one. `'omit'` (the fetch default) means the cookie is
    // never attached, and the server's only possible answer is 401.
    expect(refreshCall!.init?.credentials).toBe('include');

    // No body, and specifically no token from JavaScript.
    expect(refreshCall!.init?.body).toBeUndefined();
  });

  it('does not even attempt a refresh when there is no CSRF cookie', async () => {
    // No CSRF cookie set: there is nothing to authenticate with, so the client
    // must not send a request that can only 401. This also keeps the console
    // from hammering `/refresh` on every boot of a signed-out tab.
    seedAccessToken();
    const seen = captureFetch((url) => {
      if (url.includes('/api/v1/admin/drivers')) return json({ code: 'UNAUTHORIZED' }, 401);
      throw new Error(`unstubbed: ${url}`);
    });

    const client = new ApiClient({ baseUrl: 'http://api.test' });
    await expect(client.get('/api/v1/admin/drivers')).rejects.toThrow();

    expect(seen.some((c) => c.url.includes('/refresh'))).toBe(false);
  });

  it('clears the stored session when the refresh is refused', async () => {
    // A 401 from `/refresh` means the cookie was absent, expired or replayed,
    // and the server has already revoked the family. Keeping the access token
    // would leave a half-session where every subsequent call repeats the same
    // doomed refresh.
    setCsrfCookie();
    seedAccessToken();
    expect(session.isSignedIn).toBe(true);

    captureFetch(() => json({ code: 'UNAUTHORIZED' }, 401));

    const client = new ApiClient({ baseUrl: 'http://api.test' });
    await expect(client.get('/api/v1/admin/drivers')).rejects.toThrow();

    expect(session.isSignedIn).toBe(false);
  });

  it('does not store a refresh token from the refresh response', async () => {
    // If the server ever regressed and returned one, the client must not adopt
    // it — storing it is the exact exposure the cookie replaced.
    setCsrfCookie();
    seedAccessToken();

    captureFetch((url, count) => {
      if (url.includes('/api/v1/admin/drivers')) {
        return count === 1 ? json({ code: 'UNAUTHORIZED' }, 401) : json({ items: [] });
      }
      // Deliberately includes a `refresh_token`, which the real server does not.
      return json({
        access_token: 'fresh-token',
        refresh_token: 'should-be-ignored',
        admin: { id: '1', role: 'ADMIN' },
      });
    });

    const client = new ApiClient({ baseUrl: 'http://api.test' });
    await client.get('/api/v1/admin/drivers');

    expect(session.get()?.accessToken).toBe('fresh-token');
    expect(JSON.stringify(session.get())).not.toContain('should-be-ignored');
  });

  it('sends credentials on ordinary requests too', async () => {
    // Logout has to work when the access token has already expired, so the
    // cookie must ride along on requests that carry no `Authorization` header.
    seedAccessToken();
    const seen = captureFetch((url) => {
      if (url.includes('/api/v1/admin/drivers')) return json({ items: [] });
      throw new Error(`unstubbed: ${url}`);
    });

    const client = new ApiClient({ baseUrl: 'http://api.test' });
    await client.get('/api/v1/admin/drivers');

    const call = seen.find((c) => c.url.includes('/api/v1/admin/drivers'));
    expect(call!.init?.credentials).toBe('include');
  });
});

/** Put a usable access token in storage, as a completed sign-in would. */
function seedAccessToken() {
  session.save({ accessToken: 'stub-access-token', user: { id: '1', role: 'ADMIN' } });
}
