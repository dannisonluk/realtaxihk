/**
 * The HTTP client.
 *
 * Ported from `admin-web/js/api.js`, which mirrors
 * `mobile/lib/core/network/api_client.dart` deliberately: same refresh
 * strategy, same error envelope, so a bug fixed in one is a bug to check in the
 * other. The backend's error shape is `{code, message, details}` — **not**
 * FastAPI's default `{"detail": ...}` — see `app/core/exceptions.py`.
 */

import { session } from './session';
import { i18n } from '../i18n';

/**
 * The API base URL.
 *
 * Three cases, in order:
 *
 *   1. `?api=<base>` — an explicit absolute base for a split deploy.
 *   2. `?api=same-origin` — the console's own origin, with the static server
 *      reverse-proxying `/api/*` to the API. `serve.py` does exactly that, so
 *      the whole app is reachable on one port with **no CORS preflight**: a
 *      same-origin `fetch` is a simple request, which removes the OPTIONS
 *      round-trip (and, in a locked-down sandbox, the preflight's follow-up
 *      request that never arrives). Used by the UI verifier.
 *   3. Default — the API on the same host, port 8000.
 */
export function resolveBaseUrl(): string {
  const override = new URLSearchParams(window.location.search).get('api');
  if (override === 'same-origin') return '';
  if (override) return override.replace(/\/$/, '');
  return `${window.location.protocol}//${window.location.hostname}:8000`;
}

export interface ApiErrorInit {
  code: string;
  message: string;
  status: number;
  details?: Record<string, unknown>;
  retryAfter?: number | null;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;
  readonly retryAfter: number | null;

  constructor({ code, message, status, details, retryAfter }: ApiErrorInit) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.details = details ?? {};
    this.retryAfter = retryAfter ?? null;
  }

  /** A 401 anywhere means the session is gone; the shell signs the operator out. */
  get isAuthFailure(): boolean {
    return this.status === 401 || this.code === CODE.unauthorized;
  }
}

/** Codes the server uses, so views do not spell strings. */
export const CODE = {
  network: 'NETWORK',
  unauthorized: 'UNAUTHORIZED',
  forbidden: 'FORBIDDEN',
  notFound: 'NOT_FOUND',
  conflict: 'CONFLICT',
  rateLimited: 'RATE_LIMITED',
  businessRule: 'BUSINESS_RULE_VIOLATION',
  validationError: 'VALIDATION_ERROR',
  serviceUnavailable: 'SERVICE_UNAVAILABLE',
  /**
   * 423 — the server has *locked* a state the caller must change before the
   * request can succeed (P4: `DEPOSIT_INSUFFICIENT` on a driver in arrears).
   *
   * Kept distinct from `forbidden` on purpose, and the distinction is the whole
   * reason the server returns 423 rather than 403: 403 means "you may not", and
   * retrying is pointless, whereas 423 means "not while this is true", and the
   * fix is an action the operator can take. A view that collapsed the two would
   * tell an admin to give up on something they can actually resolve.
   */
  locked: 'LOCKED',
} as const;

function parseRetryAfter(raw: string | null): number | null {
  if (!raw) return null;
  const seconds = Number(raw);
  if (Number.isFinite(seconds)) return Math.max(0, seconds);
  const at = Date.parse(raw);
  return Number.isNaN(at) ? null : Math.max(0, (at - Date.now()) / 1000);
}

/**
 * How many extra attempts a **GET** gets when the connection fails.
 *
 * Three, because the drop is frequent on this environment rather than rare: a
 * burst of six requests reliably loses one. Writes are never retried — see the
 * note in `send` — and a genuinely down API still surfaces within ~1s, since
 * the backoff is short.
 */
const RETRY_ON_TRANSPORT = 3;

/** Linear backoff between retries. Short: the drop is transient or it is not. */
const RETRY_BACKOFF_MS = 150;

const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Build an ApiError from the server's envelope, tolerating a non-JSON body. */
export function toApiError(status: number, body: unknown, retryAfter: number | null): ApiError {
  if (body && typeof body === 'object' && typeof (body as { code?: unknown }).code === 'string') {
    const envelope = body as { code: string; message?: string; details?: Record<string, unknown> };
    return new ApiError({
      code: envelope.code,
      // The server's own message wins when it sent one; the translated string is
      // the fallback. This is the *server's* sentence, not the console's, so it
      // is deliberately not translated here — the backend answers in one
      // language and passing its wording through unaltered is the honest thing.
      message: envelope.message ?? i18n.t('errors.requestFailed'),
      status,
      ...(envelope.details ? { details: envelope.details } : {}),
      retryAfter,
    });
  }
  return new ApiError({
    code: 'UNKNOWN',
    message: i18n.t('errors.badEnvelope', { status }),
    status,
    retryAfter,
  });
}

type Query = Record<string, string | number | boolean | null | undefined>;

function buildQuery(query?: Query): string {
  if (!query) return '';
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue;
    params.set(key, String(value));
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : '';
}

interface SendOptions {
  body?: unknown;
  query?: Query;
  retried?: boolean;
  authenticated?: boolean;
  attempt?: number;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly onSessionExpired: () => void;

  /**
   * At most one refresh in flight. Without this, several concurrent 401s fire
   * several refreshes; all but one would present an already-rotated token, and
   * the server treats a replayed refresh token as theft and revokes every
   * session the user holds (`SEC-17`).
   */
  private refreshInFlight: Promise<boolean> | null = null;

  constructor({ baseUrl, onSessionExpired }: { baseUrl?: string; onSessionExpired?: () => void } = {}) {
    this.baseUrl = (baseUrl ?? resolveBaseUrl()).replace(/\/$/, '');
    this.onSessionExpired = onSessionExpired ?? (() => {});
  }

  get<T>(path: string, query?: Query): Promise<T> {
    return this.send<T>('GET', path, { query });
  }

  post<T>(path: string, options: Omit<SendOptions, 'retried' | 'attempt'> = {}): Promise<T> {
    return this.send<T>('POST', path, options);
  }

  patch<T>(path: string, options: Omit<SendOptions, 'retried' | 'attempt'> = {}): Promise<T> {
    return this.send<T>('PATCH', path, options);
  }

  /** `DELETE` returns a body on the routes that use it (a removed roster row). */
  del<T>(path: string, options: Omit<SendOptions, 'retried' | 'attempt'> = {}): Promise<T> {
    return this.send<T>('DELETE', path, options);
  }

  /**
   * A binary response — the settlement CSV — with the same auth path as JSON.
   *
   * Deliberately a separate method rather than a flag on `send`: `send` parses
   * every body as JSON and *tolerates* a parse failure by returning `null`,
   * which for a CSV would silently hand the caller an empty spreadsheet with a
   * 200 status and no error anywhere. There is no useful thing to do with a
   * failure here except throw.
   *
   * It still carries the access token and `credentials: 'include'`, so the
   * refresh-on-401 path is the same one every other call takes — the alternative
   * (building a URL with a token in the query string) would put a live
   * credential into browser history and any intermediary's access log.
   *
   * Not retried on transport failure: this is a GET, but a half-downloaded CSV
   * that the caller believes is complete is worse than an error.
   */
  async fetchBlob(path: string): Promise<Blob> {
    const headers: Record<string, string> = { Accept: 'text/csv' };
    const token = session.accessToken;
    if (token) headers.Authorization = `Bearer ${token}`;

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method: 'GET',
        headers,
        credentials: 'include',
        mode: 'cors',
      });
    } catch (cause) {
      throw new ApiError({
        code: CODE.network,
        message: i18n.t('errors.offline'),
        status: 0,
        details: { cause: String(cause) },
      });
    }

    if (response.status === 401) {
      const refreshed = await this.refreshOnce();
      if (refreshed) return this.fetchBlob(path);
      this.onSessionExpired();
    }

    if (!response.ok) {
      // The error body is JSON even though the success body is not, so the
      // envelope still parses and the caller gets the server's own reason
      // (`VERIFICATION_REQUIRED`, `ADMIN_ROLE_INSUFFICIENT`, ...) rather than a
      // status code.
      const text = await response.text();
      let parsed: unknown = null;
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = null;
      }
      throw toApiError(response.status, parsed, parseRetryAfter(response.headers.get('retry-after')));
    }

    return response.blob();
  }

  private async send<T>(
    method: string,
    path: string,
    { body, query, retried = false, authenticated = true, attempt = 0 }: SendOptions = {},
  ): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';

    const token = session.accessToken;
    if (authenticated && token) headers.Authorization = `Bearer ${token}`;

    // Every request carries the admin cookies. The refresh cookie is scoped to
    // `/api/v1/admin/auth` server-side, so the browser attaches it only where it
    // is needed — and `logout` relies on it being present even though no
    // `Authorization` header is sent (the access token may have expired).
    //
    // Costs one preflight on a cross-origin deploy (`credentials: 'include'`
    // makes the request non-simple), which the server already answers: CORS is
    // configured with an explicit origin list and `allow_credentials=True`.
    // Worth stating plainly: `'include'` requires `allow_origins` to be exact —
    // a wildcard would be rejected by the browser, so this is also what pins the
    // CORS config to a real allow-list.
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}${buildQuery(query)}`, {
        method,
        headers,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        credentials: 'include',
        mode: 'cors',
      });
    } catch (cause) {
      // A transport failure on a GET is retried before it is reported.
      //
      // Why: a dropped connection is not evidence of anything, and `boot()`
      // renders the login screen for any error that is not a 401. Without a
      // retry, one reset on `GET /auth/me` signs an operator out of a session
      // that is perfectly valid — which is both wrong and indistinguishable
      // from a real auth failure. Only GET is retried: a POST that reached the
      // server may have had its effect, and re-issuing it could double up.
      if (method === 'GET' && attempt < RETRY_ON_TRANSPORT) {
        await delay(RETRY_BACKOFF_MS * (attempt + 1));
        return this.send<T>(method, path, { body, query, retried, authenticated, attempt: attempt + 1 });
      }
      throw new ApiError({
        code: CODE.network,
        message: i18n.t('errors.offline'),
        status: 0,
        details: { cause: String(cause), attempts: attempt + 1 },
      });
    }

    const retryAfter = parseRetryAfter(response.headers.get('retry-after'));
    const text = await response.text();
    let parsed: unknown = null;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = null;
      }
    }

    // A 5xx is not the request's fault, so a GET is retried once more. 501 is
    // excluded: that is a contract error, and repeating it changes nothing.
    if (method === 'GET' && attempt < RETRY_ON_TRANSPORT && [502, 503, 504].includes(response.status)) {
      await delay(RETRY_BACKOFF_MS * (attempt + 1));
      return this.send<T>(method, path, { body, query, retried, authenticated, attempt: attempt + 1 });
    }

    if (response.ok) {
      return parsed as T;
    }

    if (response.status === 401 && authenticated && !retried) {
      const refreshed = await this.refreshOnce();
      if (refreshed) {
        return this.send<T>(method, path, { body, query, retried: true, authenticated });
      }
      this.onSessionExpired();
    }

    throw toApiError(response.status, parsed, retryAfter);
  }

  private refreshOnce(): Promise<boolean> {
    if (this.refreshInFlight) return this.refreshInFlight;
    const attempt = this.refresh().finally(() => {
      this.refreshInFlight = null;
    });
    this.refreshInFlight = attempt;
    return attempt;
  }

  private async refresh(): Promise<boolean> {
    /**
     * Exchange the HttpOnly cookie for a new access token.
     *
     * No body and no `Authorization` header: the refresh token is a cookie the
     * browser attaches for us, and the only thing this request has to add is
     * the CSRF token in a header. That signature is the design — an endpoint
     * that accepted a token from JavaScript would put the long-lived credential
     * back within reach of any injected script, which is what moving it to a
     * cookie was for.
     *
     * `credentials: 'include'` is mandatory and its absence is silent: a
     * cross-origin `fetch` defaults to `'omit'`, so the cookie is not sent, the
     * server answers "no refresh cookie", and the console signs the operator
     * out for no visible reason. It cannot be inferred from a network tab
     * either — the request looks perfectly well-formed.
     */
    const csrf = session.csrfToken;
    if (!csrf) {
      // No CSRF cookie: either never signed in, or the pair was cleared. There
      // is nothing to refresh with, so do not send a request that can only 401.
      return false;
    }

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}/api/v1/admin/auth/refresh`, {
        method: 'POST',
        headers: { Accept: 'application/json', 'X-CSRF-Token': csrf },
        credentials: 'include',
        mode: 'cors',
      });
    } catch {
      // Offline. The cookie is still good; the caller surfaces the failure.
      return false;
    }

    if (!response.ok) {
      // A 401 here means the cookie was absent, expired, or replayed and the
      // server has already revoked the family. There is nothing usable left, so
      // drop the access token too rather than leaving a half-session that makes
      // every later request take the same doomed path.
      session.clear();
      return false;
    }

    const body = (await response.json()) as {
      access_token?: unknown;
      admin?: unknown;
    };
    if (typeof body.access_token !== 'string') {
      session.clear();
      return false;
    }

    // No `refresh_token` in the body — the rotated pair arrived as `Set-Cookie`
    // and the browser has already stored it. `session.save` therefore has
    // nothing to do with the refresh token, which is the point.
    session.save({
      accessToken: body.access_token,
      user: body.admin as ReturnType<typeof session.save>['user'],
    });
    return true;
  }
}
