/**
 * Typed endpoint wrappers.
 *
 * One function per endpoint, so no view builds a path by hand. The paths here
 * are the contract; `tests/test_fleets.py` and `scripts/gen_mobile_fixtures.py`
 * pin the same ones from the other side.
 */

import type { ApiClient } from './client';
import type {
  AdjustResult,
  AdminDriverRow,
  AdminIdentity,
  AdminLoginResult,
  AdminSession,
  AnalyticsGranularity,
  AnalyticsHeatmap,
  AnalyticsSortBy,
  AnalyticsSummary,
  AuthTokens,
  DriverProfileDetail,
  DriverStatus,
  FleetDetail,
  FleetMember,
  FleetRow,
  FleetSettlementRow,
  FleetSettlementRunResult,
  GrantResult,
  LicenceDecisionResult,
  LicenceReviewStatus,
  LicenceSubmissionDetail,
  LicenceSubmissionRow,
  Paged,
  RefundRow,
  RefundStatus,
  SettlementRunResult,
  SortDir,
} from './types';

export const endpoints = {
  auth: {
    /**
     * Admin sign-in, step 1: username + password.
     *
     * Returns a **challenge**, never an access token. `next` says what step 2
     * is: `totp_required` for an enrolled admin, `enrolment_required` for a
     * first login (which also carries the QR material).
     */
    adminLogin: (client: ApiClient, username: string, password: string) =>
      client.post<AdminLoginResult>('/api/v1/admin/auth/login', {
        body: { username, password },
        authenticated: false,
      }),
    /** Step 2a: the 6-digit authenticator code. This is what mints the token. */
    adminVerifyTotp: (client: ApiClient, challengeToken: string, code: string) =>
      client.post<AdminSession>('/api/v1/admin/auth/totp/verify', {
        body: { challenge_token: challengeToken, code },
        authenticated: false,
      }),
    /** Step 2b: a single-use recovery code, for a lost phone. */
    adminUseRecovery: (client: ApiClient, challengeToken: string, code: string) =>
      client.post<AdminSession>('/api/v1/admin/auth/recovery', {
        body: { challenge_token: challengeToken, code },
        authenticated: false,
      }),
    /** Step 2c (first login): prove one code from the pending secret. */
    adminConfirmEnrolment: (client: ApiClient, challengeToken: string, code: string) =>
      client.post<AdminSession>('/api/v1/admin/auth/totp/enrol/confirm', {
        body: { challenge_token: challengeToken, code },
        authenticated: false,
      }),
    requestOtp: (client: ApiClient, phone: string) =>
      client.post<{ sent: boolean }>('/api/v1/auth/otp/request', {
        body: { phone_e164: phone },
        authenticated: false,
      }),
    verifyOtp: (client: ApiClient, phone: string, code: string) =>
      client.post<AuthTokens>('/api/v1/auth/otp/verify', {
        body: { phone_e164: phone, code },
      }),
    me: (client: ApiClient) => client.get<AdminIdentity>('/api/v1/auth/me'),
    logout: (client: ApiClient) => client.post<null>('/api/v1/auth/logout'),
  },

  drivers: {
    /** The KYC queue, oldest first. */
    list: (client: ApiClient, { status, limit = 100, offset = 0 }: {
      status?: DriverStatus;
      limit?: number;
      offset?: number;
    } = {}) =>
      client.get<Paged<AdminDriverRow>>('/api/v1/admin/drivers', {
        status_filter: status,
        limit,
        offset,
      }),
    /**
     * One driver in full: profile, deposit, statement, refund history, fleet.
     *
     * Composed server-side so the page cannot render a half-loaded driver. The
     * ledger and refund lists are capped and newest-first.
     */
    detail: (client: ApiClient, driverId: string, { ledgerLimit = 50, refundLimit = 20 }: {
      ledgerLimit?: number;
      refundLimit?: number;
    } = {}) =>
      client.get<DriverProfileDetail>(`/api/v1/admin/drivers/${encodeURIComponent(driverId)}`, {
        ledger_limit: ledgerLimit,
        refund_limit: refundLimit,
      }),
    /** `approve` -> DEPOSIT_REQUIRED, `reject`/`terminate` -> TERMINATED, `suspend` -> SUSPENDED. */
    review: (client: ApiClient, driverId: string, { decision, note = '' }: {
      decision: string;
      note?: string;
    }) =>
      client.post<AdminDriverRow>(`/api/v1/admin/drivers/${encodeURIComponent(driverId)}/review`, {
        body: { decision, note },
      }),
    /**
     * Credits the deposit. Idempotent when `reference` is supplied: the server
     * namespaces it `grant:<driver>:<key>`, so a retry cannot double-credit and
     * it cannot collide with a settlement or refund reference (`SEC-13`).
     */
    grantDeposit: (client: ApiClient, driverId: string, { amountHkd, note = '', reference }: {
      amountHkd: string;
      note?: string;
      reference?: string;
    }) =>
      client.post<GrantResult>(
        `/api/v1/admin/drivers/${encodeURIComponent(driverId)}/deposit/grant`,
        {
          body: {
            amount_hkd: String(amountHkd),
            note,
            ...(reference ? { reference } : {}),
          },
        },
      ),
    /**
     * A manual correction to the deposit ledger (`ADJUSTMENT`).
     *
     * `amountHkd` is **signed**: negative debits, positive credits. `reason` is
     * required by the server — an adjustment has no upstream event, so the
     * reason *is* the audit trail. Bounded at ±5000 server-side.
     *
     * Different from `grantDeposit`, and not a substitute for it: a grant is a
     * payment received (and counts toward top-up totals), an adjustment is the
     * platform correcting its own books.
     */
    adjustDeposit: (client: ApiClient, driverId: string, { amountHkd, reason, reference }: {
      amountHkd: string;
      reason: string;
      reference?: string;
    }) =>
      client.post<AdjustResult>(
        `/api/v1/admin/drivers/${encodeURIComponent(driverId)}/deposit/adjust`,
        {
          body: {
            amount_hkd: String(amountHkd),
            reason,
            ...(reference ? { reference } : {}),
          },
        },
      ),
  },

  /**
   * P-3: taxi driver licence review.
   *
   * A queue of *submissions*, not of drivers. The driver already has a row in
   * the KYC queue; this is the separate, recurring evidence check — which is why
   * approving here does not touch a trading driver's status.
   */
  licence: {
    /** The review queue, oldest first. `status: 'all'` is the audit view. */
    list: (client: ApiClient, { status = 'PENDING', limit = 100, offset = 0 }: {
      status?: LicenceReviewStatus | 'all';
      limit?: number;
      offset?: number;
    } = {}) =>
      client.get<Paged<LicenceSubmissionRow>>('/api/v1/admin/licence/submissions', {
        status,
        limit,
        offset,
      }),
    /**
     * One submission with its documents and **short-lived signed URLs**.
     *
     * The URLs are minted only here, never in the list: a working link to an
     * identity document must not sit in every poll of the queue. `ttl` is the
     * console's own countdown, capped at 900s server-side.
     */
    detail: (client: ApiClient, submissionId: string, { ttl = 300 }: { ttl?: number } = {}) =>
      client.get<LicenceSubmissionDetail>(
        `/api/v1/admin/licence/submissions/${encodeURIComponent(submissionId)}`,
        { ttl },
      ),
    /**
     * The decision. Terminal — a second call is refused.
     *
     * `reason` is mandatory for a rejection, because a driver who is told only
     * "rejected" cannot tell whether to retake the photo or give up.
     */
    decide: (client: ApiClient, submissionId: string, { approve, reason }: {
      approve: boolean;
      reason?: string;
    }) =>
      client.post<LicenceDecisionResult>(
        `/api/v1/admin/licence/submissions/${encodeURIComponent(submissionId)}/decide`,
        { body: { approve, ...(reason ? { reason } : {}) } },
      ),
  },

  refunds: {
    list: (client: ApiClient, { status, limit = 100, offset = 0 }: {
      status?: RefundStatus;
      limit?: number;
      offset?: number;
    } = {}) =>
      client.get<Paged<RefundRow>>('/api/v1/admin/refunds', {
        status_filter: status,
        limit,
        offset,
      }),
    /** Approving is the only path that moves money out, and it terminates the driver. */
    decide: (client: ApiClient, refundId: string, { approve, note = '' }: {
      approve: boolean;
      note?: string;
    }) =>
      client.post<RefundRow>(`/api/v1/admin/refunds/${encodeURIComponent(refundId)}/decision`, {
        body: { decision: approve ? 'approve' : 'reject', note },
      }),
  },

  settlement: {
    /** Idempotent per ISO week: a re-run charges nobody twice. */
    runWeekly: (client: ApiClient, { period }: { period?: string } = {}) =>
      client.post<SettlementRunResult>('/api/v1/admin/settlement/weekly/run', {
        query: { period },
      }),
  },

  fleets: {
    list: (client: ApiClient, { status, limit = 100, offset = 0 }: {
      status?: string;
      limit?: number;
      offset?: number;
    } = {}) =>
      client.get<Paged<FleetRow>>('/api/v1/admin/fleets', {
        status_filter: status,
        limit,
        offset,
      }),
    create: (client: ApiClient, payload: Record<string, unknown>) =>
      client.post<FleetRow>('/api/v1/admin/fleets', { body: payload }),
    update: (client: ApiClient, fleetId: string, payload: Record<string, unknown>) =>
      client.patch<FleetRow>(`/api/v1/admin/fleets/${encodeURIComponent(fleetId)}`, { body: payload }),
    members: (client: ApiClient, fleetId: string, { includeLeft = false }: { includeLeft?: boolean } = {}) =>
      client.get<{ items: FleetMember[] }>(
        `/api/v1/admin/fleets/${encodeURIComponent(fleetId)}/members`,
        { include_left: includeLeft },
      ),
    addMember: (client: ApiClient, fleetId: string, { driverProfileId, memberRole = 'MEMBER' }: {
      driverProfileId: string;
      memberRole?: string;
    }) =>
      client.post<null>(`/api/v1/admin/fleets/${encodeURIComponent(fleetId)}/members`, {
        body: { driver_profile_id: driverProfileId, member_role: memberRole },
      }),
    removeMember: (client: ApiClient, fleetId: string, driverProfileId: string) =>
      client.del<null>(
        `/api/v1/admin/fleets/${encodeURIComponent(fleetId)}/members/${encodeURIComponent(driverProfileId)}`,
      ),
    settlementHistory: (client: ApiClient, fleetId: string, { limit = 52 }: { limit?: number } = {}) =>
      client.get<{ items: FleetSettlementRow[] }>(
        `/api/v1/admin/fleets/${encodeURIComponent(fleetId)}/settlement`,
        { limit },
      ),
    /** Idempotent per (fleet, ISO week). Rejected for a fleet that is not ACTIVE. */
    runSettlement: (client: ApiClient, fleetId: string, { period }: { period?: string } = {}) =>
      client.post<FleetSettlementRunResult>(
        `/api/v1/admin/fleets/${encodeURIComponent(fleetId)}/settlement/run`,
        { query: { period } },
      ),
    /**
     * The composed detail payload. Falls back to three parallel calls when the
     * server has no single-endpoint for it, so the page shape is identical
     * either way.
     */
    detail: async (client: ApiClient, fleetId: string): Promise<FleetDetail> => {
      const [fleets, members, settlement] = await Promise.all([
        endpoints.fleets.list(client, { limit: 100 }),
        endpoints.fleets.members(client, fleetId, { includeLeft: true }),
        endpoints.fleets.settlementHistory(client, fleetId),
      ]);
      const fleet = fleets.items.find((row) => row.id === fleetId);
      if (!fleet) {
        throw new Error(`找不到車隊 ${fleetId}`);
      }
      return { fleet, members: members.items, settlement: settlement.items };
    },
  },

  /**
   * Operational analytics. Read-only, admin-guarded.
   *
   * Both calls take the same filters so a page can drive the table and the
   * chart from one set of controls. `undefined` values are dropped by
   * `buildQuery`, which is what lets the server apply its own defaults — the
   * range defaults to the last 30 days, so an empty filter object is a valid
   * request rather than a 422.
   */
  analytics: {
    summary: (
      client: ApiClient,
      filters: {
        from?: string;
        to?: string;
        granularity?: AnalyticsGranularity;
        taxiType?: string | null;
        sortBy?: AnalyticsSortBy;
        sortDir?: SortDir;
      } = {},
    ) =>
      client.get<AnalyticsSummary>('/api/v1/admin/analytics', {
        from: filters.from,
        to: filters.to,
        granularity: filters.granularity,
        taxi_type: filters.taxiType ?? undefined,
        sort_by: filters.sortBy,
        sort_dir: filters.sortDir,
      }),

    heatmap: (
      client: ApiClient,
      filters: { from?: string; to?: string; taxiType?: string | null } = {},
    ) =>
      client.get<AnalyticsHeatmap>('/api/v1/admin/analytics/heatmap', {
        from: filters.from,
        to: filters.to,
        taxi_type: filters.taxiType ?? undefined,
      }),
  },
};
