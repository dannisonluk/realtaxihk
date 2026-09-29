/**
 * The HTTP client.
 *
 * Mirrors `mobile/lib/core/network/api_client.dart` deliberately: same refresh
 * strategy, same error envelope, so a bug fixed in one is a bug to check in the
 * other. The backend's error shape is `{code, message, details}` — **not**
 * FastAPI's default `{"detail": ...}` — see `app/core/exceptions.py`.
 */

import { session } from './session.js';

/** The API base URL. Same-origin by default; override with `?api=` for a split deploy. */
export function resolveBaseUrl() {
  const override = new URLSearchParams(window.location.search).get('api');
  if (override) return override.replace(/\/$/, '');
  // The console is served from :3000 and the API listens on :8000, so a bare
  // relative path would hit the static server.
  return `${window.location.protocol}//${window.location.hostname}:8000`;
}

export class ApiError extends Error {
  constructor({ code, message, status, details, retryAfter }) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.details = details || {};
    this.retryAfter = retryAfter || null;
  }

  /** A 401 anywhere means the session is gone; the shell signs the operator out. */
  get isAuthFailure() {
    return this.status === 401 || this.code === 'UNAUTHORIZED';
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
};

function parseRetryAfter(raw) {
  if (!raw) return null;
  const seconds = Number(raw);
  if (Number.isFinite(seconds)) return Math.max(0, seconds);
  const at = Date.parse(raw);
  return Number.isNaN(at) ? null : Math.max(0, (at - Date.now()) / 1000);
}

/** Build an ApiError from the server's envelope, tolerating a non-JSON body. */
export function toApiError(status, body, retryAfter) {
  if (body && typeof body === 'object' && typeof body.code === 'string') {
    return new ApiError({
      code: body.code,
      message: body.message || '請求失敗。',
      status,
      details: body.details,
      retryAfter,
    });
  }
  return new ApiError({
    code: 'UNKNOWN',
    message: `伺服器回應 ${status}，但格式無法辨識。`,
    status,
    retryAfter,
  });
}

function buildQuery(query) {
  if (!query) return '';
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue;
    params.set(key, String(value));
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : '';
}

export class ApiClient {
  /**
   * @param {{baseUrl?: string, onSessionExpired?: () => void}} [options]
   */
  constructor({ baseUrl, onSessionExpired } = {}) {
    this.baseUrl = (baseUrl || resolveBaseUrl()).replace(/\/$/, '');
    this.onSessionExpired = onSessionExpired || (() => {});
    /**
     * At most one refresh in flight. Without this, several concurrent 401s fire
     * several refreshes; all but one would present an already-rotated token, and
     * the server treats a replayed refresh token as theft and revokes every
     * session the user holds (`SEC-17`).
     */
    this._refreshInFlight = null;
  }

  get(path, query) {
    return this._send('GET', path, { query });
  }

  post(path, { body, query } = {}) {
    return this._send('POST', path, { body, query });
  }

  patch(path, { body, query } = {}) {
    return this._send('PATCH', path, { body, query });
  }

  /** `DELETE` returns a body on the routes that use it (a removed roster row). */
  del(path, { body, query } = {}) {
    return this._send('DELETE', path, { body, query });
  }

  async _send(method, path, { body, query, retried = false, authenticated = true } = {}) {
    const headers = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';

    const token = session.accessToken;
    if (authenticated && token) headers.Authorization = `Bearer ${token}`;

    let response;
    try {
      response = await fetch(`${this.baseUrl}${path}${buildQuery(query)}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        // The refresh token is a bearer credential and the API is a different
        // origin, so credentials are never sent implicitly.
        credentials: 'omit',
        mode: 'cors',
      });
    } catch (cause) {
      throw new ApiError({
        code: CODE.network,
        message: '無法連接伺服器。請檢查網絡。',
        status: 0,
        details: { cause: String(cause) },
      });
    }

    const retryAfter = parseRetryAfter(response.headers.get('retry-after'));
    const text = await response.text();
    let parsed = null;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = null;
      }
    }

    if (response.ok) {
      return parsed;
    }

    if (response.status === 401 && authenticated && !retried) {
      const refreshed = await this._refreshOnce();
      if (refreshed) {
        return this._send(method, path, { body, query, retried: true, authenticated });
      }
      this.onSessionExpired();
    }

    throw toApiError(response.status, parsed, retryAfter);
  }

  _refreshOnce() {
    if (this._refreshInFlight) return this._refreshInFlight;
    const attempt = this._refresh().finally(() => {
      this._refreshInFlight = null;
    });
    this._refreshInFlight = attempt;
    return attempt;
  }

  async _refresh() {
    const refreshToken = session.refreshToken;
    if (!refreshToken) return false;

    let response;
    try {
      response = await fetch(`${this.baseUrl}/api/v1/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
        credentials: 'omit',
        mode: 'cors',
      });
    } catch {
      // Offline. The tokens are still good; the caller surfaces the failure.
      return false;
    }

    if (!response.ok) {
      // A 401 here means the token was replayed or expired and the server has
      // already revoked everything. Either way there is nothing usable left.
      session.clear();
      return false;
    }

    const body = await response.json();
    if (typeof body?.access_token !== 'string' || typeof body?.refresh_token !== 'string') {
      session.clear();
      return false;
    }

    session.save({
      accessToken: body.access_token,
      refreshToken: body.refresh_token,
      user: body.user,
    });
    return true;
  }
}

// ---------------------------------------------------------------------------
// Typed endpoint wrappers
//
// One place per endpoint, so no view builds a path by hand. The paths here are
// the contract; `tests/test_fleets.py` and `scripts/gen_mobile_fixtures.py` pin
// the same ones from the other side.
// ---------------------------------------------------------------------------

export const api = {
  auth: {
    requestOtp: (client, phone) => client.post('/api/v1/auth/otp/request', { body: { phone_e164: phone }, authenticated: false }),
    verifyOtp: (client, phone, code) =>
      client.post('/api/v1/auth/otp/verify', { body: { phone_e164: phone, code } }),
    me: (client) => client.get('/api/v1/auth/me'),
    logout: (client) => client.post('/api/v1/auth/logout'),
  },

  drivers: {
    /** The KYC queue, oldest first. */
    list: (client, { status, limit = 100, offset = 0 } = {}) =>
      client.get('/api/v1/admin/drivers', { status_filter: status, limit, offset }),
    /** `approve` -> DEPOSIT_REQUIRED, `reject`/`terminate` -> TERMINATED, `suspend` -> SUSPENDED. */
    review: (client, driverId, { decision, note = '' }) =>
      client.post(`/api/v1/admin/drivers/${driverId}/review`, { body: { decision, note } }),
    /**
     * Credits the deposit. Idempotent when `reference` is supplied: the server
     * namespaces it `grant:<driver>:<key>`, so a retry cannot double-credit and
     * it cannot collide with a settlement or refund reference (`SEC-13`).
     */
    grantDeposit: (client, driverId, { amountHkd, note = '', reference }) =>
      client.post(`/api/v1/admin/drivers/${driverId}/deposit/grant`, {
        body: {
          amount_hkd: String(amountHkd),
          note,
          ...(reference ? { reference } : {}),
        },
      }),
  },

  refunds: {
    list: (client, { status, limit = 100, offset = 0 } = {}) =>
      client.get('/api/v1/admin/refunds', { status_filter: status, limit, offset }),
    /** Approving is the only path that moves money out, and it terminates the driver. */
    decide: (client, refundId, { approve, note = '' }) =>
      client.post(`/api/v1/admin/refunds/${refundId}/decision`, {
        body: { decision: approve ? 'approve' : 'reject', note },
      }),
  },

  settlement: {
    /** Idempotent per ISO week: a re-run charges nobody twice. */
    runWeekly: (client, { period } = {}) =>
      client.post('/api/v1/admin/settlement/weekly/run', { query: { period } }),
  },

  fleets: {
    list: (client, { status, limit = 100, offset = 0 } = {}) =>
      client.get('/api/v1/admin/fleets', { status_filter: status, limit, offset }),
    create: (client, payload) => client.post('/api/v1/admin/fleets', { body: payload }),
    update: (client, fleetId, payload) => client.patch(`/api/v1/admin/fleets/${fleetId}`, { body: payload }),
    members: (client, fleetId, { includeLeft = false } = {}) =>
      client.get(`/api/v1/admin/fleets/${fleetId}/members`, { include_left: includeLeft }),
    addMember: (client, fleetId, { driverProfileId, memberRole = 'MEMBER' }) =>
      client.post(`/api/v1/admin/fleets/${fleetId}/members`, {
        body: { driver_profile_id: driverProfileId, member_role: memberRole },
      }),
    removeMember: (client, fleetId, driverProfileId) =>
      client.del(`/api/v1/admin/fleets/${fleetId}/members/${driverProfileId}`),
    settlementHistory: (client, fleetId, { limit = 52 } = {}) =>
      client.get(`/api/v1/admin/fleets/${fleetId}/settlement`, { limit }),
    /** Idempotent per (fleet, ISO week). Rejected for a fleet that is not ACTIVE. */
    runSettlement: (client, fleetId, { period } = {}) =>
      client.post(`/api/v1/admin/fleets/${fleetId}/settlement/run`, { query: { period } }),
  },
};
