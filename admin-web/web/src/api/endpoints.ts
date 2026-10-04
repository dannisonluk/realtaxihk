/**
 * Typed endpoint wrappers.
 *
 * One function per endpoint, so no view builds a path by hand. The paths here
 * are the contract; `tests/test_fleets.py` and `scripts/dev/gen_mobile_fixtures.py`
 * pin the same ones from the other side.
 */

import type { ApiClient } from './client';
import { i18n } from '../i18n';
import type {
  AdjustResult,
  AdminAccountCreated,
  AdminAccountPage,
  AdminActiveChange,
  AdminDriverRow,
  AdminIdentity,
  AdminLiveDrivers,
  AdminLoginResult,
  AdminOrderDetail,
  AdminOrderRow,
  AdminPasswordReset,
  AdminRoleChange,
  AdminSession,
  AnalyticsGranularity,
  AnalyticsHeatmap,
  AnalyticsSortBy,
  AnalyticsSummary,
  AuditRow,
  AuthTokens,
  DisputeDetail,
  DisputeMessage,
  DisputeResolveResult,
  DisputeRow,
  DisputeStats,
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
  SearchResponse,
  SettlementPreview,
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
    /**
     * Step 2c (first login): prove one code from the pending secret.
     *
     * All three step-2 calls return an `AdminSession`, and all three also cause
     * the server to set the `HttpOnly` refresh cookie. The console does not
     * handle that cookie at all — the browser stores it unprompted — which is
     * why these responses carry no refresh token.
     */
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
    /**
     * End the session.
     *
     * The **admin** endpoint, not `/api/v1/auth/logout`. The user endpoint is
     * gated by a bearer token and resolves the caller against `users`, so an
     * admin calling it would 404 — and, worse, an admin whose 15-minute access
     * token had already expired could not sign out at all, leaving a live
     * refresh cookie in a shared browser.
     *
     * `authenticated: false` because the admin logout deliberately does not
     * require the access token: the cookie is the credential, and clearing it is
     * the whole operation.
     */
    logout: (client: ApiClient) =>
      client.post<{ ok: boolean; revoked: boolean }>('/api/v1/admin/auth/logout', {
        authenticated: false,
      }),
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
    /**
     * Dry-run the weekly run. **Writes nothing**, charges nobody.
     *
     * Returns a `confirm_token` and that is the whole point: the run charges
     * every eligible driver at once, and it is idempotent per ISO week — so the
     * first accidental press is not something you get to undo, because the money
     * is gone and the reference is spent. Holding a token the preview issued is
     * what turns "look before you leap" from a runbook line into a constraint.
     *
     * FINANCE, same as the run: a preview one role can obtain and another can
     * act on is a workflow nobody can complete.
     */
    previewWeekly: (client: ApiClient, { period }: { period?: string } = {}) =>
      client.post<SettlementPreview>('/api/v1/admin/settlement/preview', {
        body: { period: period ?? null },
      }),
    /**
     * Run the weekly service-fee settlement by hand. Idempotent per ISO week.
     *
     * `confirmToken` comes from `previewWeekly` and is bound to that preview's
     * **period and fee**, not to the actor — two finance admins working the same
     * incident should not have to pass a token back and forth, and both actions
     * are audited with their own actor anyway.
     *
     * The server deliberately does **not** require one when the period is
     * already fully settled: that run is a no-op by construction, and demanding
     * a preview to prove nothing will happen would train operators to click
     * through the gate rather than read it.
     */
    runWeekly: (client: ApiClient, { period, confirmToken }: {
      period?: string;
      confirmToken?: string;
    } = {}) =>
      client.post<SettlementRunResult>('/api/v1/admin/settlement/weekly/run', {
        query: { period, confirm_token: confirmToken },
      }),
    /**
     * Every ledger entry for one ISO week, as CSV, for the finance handover.
     *
     * FINANCE only, and it returns a `Blob` rather than JSON — the consumer is a
     * spreadsheet, so `client.get` (which parses JSON) is the wrong tool. A
     * `credentials: 'include'` fetch keeps the auth path identical to every
     * other call rather than hand-building a URL with a token in it.
     */
    exportCsv: async (client: ApiClient, period: string): Promise<Blob> => {
      const response = await client.fetchBlob(
        `/api/v1/admin/settlement/export.csv?period=${encodeURIComponent(period)}`,
      );
      return response;
    },
  },

  /**
   * Order monitoring. Read-only, and readable by every role.
   *
   * The console had no order view at all before this: when a passenger called to
   * say their driver never arrived, there was no way to answer "what state is
   * that trip in, who is the driver, and how long ago did they accept". Every
   * field here already existed on `orders` — nothing new is measured.
   */
  orders: {
    list: (client: ApiClient, filters: {
      status?: string;
      driverId?: string;
      passengerId?: string;
      since?: string;
      until?: string;
      openOnly?: boolean;
      limit?: number;
      offset?: number;
    } = {}) =>
      client.get<Paged<AdminOrderRow>>('/api/v1/admin/orders', {
        status: filters.status,
        driver_id: filters.driverId,
        passenger_id: filters.passengerId,
        since: filters.since,
        until: filters.until,
        open_only: filters.openOnly,
        limit: filters.limit ?? 50,
        offset: filters.offset ?? 0,
      }),
    /**
     * One trip in full, with its frozen fare snapshot and server-computed
     * timeline. `fare` is `orders.fare_json`, **not** a recomputation — the
     * disputed amount is always the one the passenger was actually quoted.
     */
    detail: (client: ApiClient, orderId: string) =>
      client.get<AdminOrderDetail>(`/api/v1/admin/orders/${encodeURIComponent(orderId)}`),
  },

  /**
   * The live fleet map: every driver with a position fix, plus the trip they are
   * on if they have one.
   *
   * **Polled, not pushed.** There is deliberately no websocket for this. The
   * endpoint is a *snapshot*, which means the page owns the refresh rate, can
   * stop asking when the tab is hidden, and cannot be left holding a half-applied
   * stream of deltas after a reconnect. The passenger and driver apps do stream
   * positions — they need to, because a moving marker is the product — but an
   * operations view does not, and paying for a second fan-out channel to serve
   * one is how a free feature becomes a billed one.
   *
   * `includeOffline` exists because "the fleet" and "the cars actually working"
   * are different questions. Filtering the second out of the first in the client
   * would re-implement the server's own `is_online` rule, and the two would
   * disagree the first time that rule changed.
   */
  live: {
    drivers: (client: ApiClient, { includeOffline = false, limit = 500 }: {
      includeOffline?: boolean;
      limit?: number;
    } = {}) =>
      client.get<AdminLiveDrivers>('/api/v1/admin/live/drivers', {
        include_offline: includeOffline,
        limit,
      }),
  },

  /**
   * The audit trail. Readable by **every** role, including SUPPORT.
   *
   * Deliberate: the log records what was done by whom, and restricting its
   * reading would mean the people least able to change anything are also the
   * least able to notice that something was changed. It holds no secrets — no
   * password, no TOTP secret, no token — only decisions and their actors.
   */
  audit: {
    list: (client: ApiClient, filters: {
      event?: string;
      adminId?: string;
      since?: string;
      until?: string;
      limit?: number;
      offset?: number;
    } = {}) =>
      client.get<Paged<AuditRow>>('/api/v1/admin/audit', {
        event: filters.event,
        admin_id: filters.adminId,
        // `since`/`until` are half-open on the server (`>= since`, `< until`),
        // matching the analytics convention, so paging day by day by hand does
        // not show the boundary row twice.
        since: filters.since,
        until: filters.until,
        limit: filters.limit ?? 50,
        offset: filters.offset ?? 0,
      }),
  },

  /**
   * Admin account administration. **SUPER_ADMIN only, all four routes.**
   *
   * These decide who may do everything else, so the RBAC hierarchy has to hold
   * here without help. The server applies `require_role` at the route *and*
   * re-checks in `AdminAccountService`, because an OPERATIONS account promoting
   * itself is the single move that makes every other guard meaningless.
   */
  accounts: {
    /** The roster, plus the count the demote button depends on. */
    list: (client: ApiClient) => client.get<AdminAccountPage>('/api/v1/admin/accounts'),
    /**
     * Create an account. It **cannot log in until it enrols TOTP** — the
     * response carries `totp_enrolment_pending: true` so that is stated rather
     * than left to be inferred from an absent field.
     */
    create: (client: ApiClient, payload: {
      username: string;
      email: string;
      password: string;
      full_name?: string | null;
      admin_role?: string;
    }) => client.post<AdminAccountCreated>('/api/v1/admin/accounts', { body: payload }),
    /**
     * Change a role. A `PATCH` on `/role` rather than a general
     * `PATCH /accounts/{id}` on purpose: a single update endpoint that accepts
     * `is_active` alongside `admin_role` is one where a future field gets added
     * without anyone re-reading which constraints applied to the neighbours.
     */
    changeRole: (client: ApiClient, accountId: string, adminRole: string) =>
      client.patch<AdminRoleChange>(
        `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/role`,
        { body: { admin_role: adminRole } },
      ),
    /**
     * Set another admin's password.
     *
     * Changing **your own** password is a different operation with a different
     * precondition — it must require the current password, or anyone who finds
     * an unlocked session can lock the owner out. This route exists for the "my
     * only admin is locked out" case, which is why it does not ask for the old
     * one and why it clears the lockout counters.
     */
    resetPassword: (client: ApiClient, accountId: string, newPassword: string) =>
      client.post<AdminPasswordReset>(
        `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/password/reset`,
        { body: { new_password: newPassword } },
      ),
    /**
     * Deactivate or reactivate another admin. SUPER_ADMIN only.
     *
     * A `PATCH` on `/active` rather than a general `PATCH /accounts/{id}`, for
     * the reason the `/role` route gives: the constraints differ per field (the
     * last usable super admin cannot be lowered *or* switched off; the self
     * check applies to both), and one combined endpoint is where the next field
     * gets added without anyone re-reading which constraints applied to the
     * neighbours.
     *
     * This is the half of "revoke an admin" that a password reset cannot do. A
     * lost credential and a departed operator are different incidents, and only
     * the second one wants the account gone.
     */
    setActive: (client: ApiClient, accountId: string, isActive: boolean) =>
      client.patch<AdminActiveChange>(
        `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/active`,
        { body: { is_active: isActive } },
      ),
  },

  /**
   * The dispute queue.
   *
   * **Sorted by SLA ascending, not by recency.** An incident queue sorted
   * newest-first answers whatever arrived while somebody was watching and buries
   * the case about to breach.
   */
  disputes: {
    list: (client: ApiClient, filters: {
      status?: string;
      category?: string;
      severity?: string;
      assignedAdminId?: string;
      unassignedOnly?: boolean;
      openOnly?: boolean;
      overdueOnly?: boolean;
      orderId?: string;
      limit?: number;
      offset?: number;
    } = {}) =>
      client.get<Paged<DisputeRow>>('/api/v1/admin/disputes', {
        status: filters.status,
        category: filters.category,
        severity: filters.severity,
        assigned_admin_id: filters.assignedAdminId,
        unassigned_only: filters.unassignedOnly,
        // `open_only` defaults to true server-side: the console's default view
        // is the work remaining, not the archive.
        open_only: filters.openOnly,
        overdue_only: filters.overdueOnly,
        order_id: filters.orderId,
        limit: filters.limit ?? 50,
        offset: filters.offset ?? 0,
      }),
    /** Header counts, computed server-side against one clock. */
    stats: (client: ApiClient) => client.get<DisputeStats>('/api/v1/admin/disputes/stats'),
    detail: (client: ApiClient, disputeId: string) =>
      client.get<DisputeDetail>(`/api/v1/admin/disputes/${encodeURIComponent(disputeId)}`),
    /**
     * Open a case by hand. OPERATIONS or above.
     *
     * A manual case still gets an SLA from its **severity**, the same as an
     * automatic one. An operator-set deadline is how a manually filed safety
     * complaint ends up with less urgency than a machine-filed one.
     */
    create: (client: ApiClient, payload: {
      category: string;
      summary: string;
      order_id?: string | null;
      severity?: string;
      against_kind?: string | null;
      against_id?: string | null;
    }) => client.post<DisputeDetail>('/api/v1/admin/disputes', { body: payload }),
    /** Claim a case, moving `OPEN` to `INVESTIGATING`. */
    assign: (client: ApiClient, disputeId: string) =>
      client.post<DisputeDetail>(`/api/v1/admin/disputes/${encodeURIComponent(disputeId)}/assign`),
    /**
     * Add a turn to the thread. Any role may reply — SUPPORT's whole function is
     * to be the first responder, and gating that would put the least experienced
     * team member in front of a customer with no way to answer them.
     *
     * `isInternal` marks a staff note. The server records the flag on the
     * message and in the audit payload, because "who wrote this, and did they
     * mean the customer to see it" is the question asked when a note is quoted
     * back at us by mistake.
     */
    addMessage: (client: ApiClient, disputeId: string, body: string, isInternal = false) =>
      client.post<DisputeMessage>(
        `/api/v1/admin/disputes/${encodeURIComponent(disputeId)}/messages`,
        { body: { body, is_internal: isInternal } },
      ),
    /** Move between non-terminal states. `RESOLVED` is refused here — resolution carries a note. */
    setStatus: (client: ApiClient, disputeId: string, status: string) =>
      client.post<DisputeDetail>(
        `/api/v1/admin/disputes/${encodeURIComponent(disputeId)}/status`,
        { body: { status } },
      ),
    /**
     * Decide the money question.
     *
     * **The 403 is per-request, not on the route.** Judging conduct is
     * OPERATIONS' job and moving money is FINANCE's, so the endpoint opens for
     * both and then narrows: a resolution whose `moves_money` is true
     * additionally requires FINANCE. The server reads the *live* role, so a
     * demotion takes effect on the next request rather than at token expiry.
     *
     * `note` is required even for `NONE` — "decided: nobody is charged" and "not
     * decided yet" must be distinguishable a month later.
     */
    resolve: (client: ApiClient, disputeId: string, payload: {
      resolution: string;
      note: string;
      close?: boolean;
    }) =>
      client.post<DisputeResolveResult>(
        `/api/v1/admin/disputes/${encodeURIComponent(disputeId)}/resolve`,
        { body: payload },
      ),
  },

  /**
   * Unified subject search — the console's front door.
   *
   * This is the endpoint support hits while a passenger is on the line, so it is
   * deliberately the widest-guarded thing in the console: every role may search,
   * because the alternative is that the person answering the phone cannot look
   * up the caller's account.
   *
   * **Not audited per call.** Every keystroke writing an audit row would make
   * the trail useless through volume and would record *that* someone searched
   * without recording what they then looked at. The audited event is opening the
   * subject's detail page, which is where identity is actually revealed.
   */
  search: {
    query: (client: ApiClient, q: string, limit?: number) =>
      client.get<SearchResponse>('/api/v1/admin/search', { q, limit }),
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
     * The composed detail payload. Three calls, issued **sequentially** — see
     * the docstring in `app/useLoad.ts` for why this codebase does not fan
     * requests out in parallel.
     */
    detail: async (client: ApiClient, fleetId: string): Promise<FleetDetail> => {
      const fleets = await endpoints.fleets.list(client, { limit: 100 });
      const members = await endpoints.fleets.members(client, fleetId, { includeLeft: true });
      const settlement = await endpoints.fleets.settlementHistory(client, fleetId);
      const fleet = fleets.items.find((row) => row.id === fleetId);
      if (!fleet) {
        throw new Error(i18n.t('errors.fleetNotFound', { id: fleetId }));
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
