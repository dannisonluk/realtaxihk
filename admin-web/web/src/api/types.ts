/**
 * Domain types for the console.
 *
 * Hand-written from the FastAPI schemas in `app/api/schemas/`, which are the
 * authority: these are the fields the console actually reads, spelled as the
 * server sends them. Money is always a **string** on the wire (the server
 * formats it with `money_str`), so every amount here is a string — do not
 * "helpfully" type it as `number` or the page will render `NaN` after
 * arithmetic.
 *
 * This file is a mirror, not a generator, and it has been wrong before
 * (`phone_masked` once made the sidebar render "—" for every admin because the
 * field it reads is never present on an admin). The wire contract is pinned by
 * `scripts/verify/audit_response_models.py` for the backend and by the mobile
 * fixtures; when you change a server schema, change this mirror in the same
 * commit.
 */

/**
 * `GET /api/v1/auth/me` — and the shape differs by token scope.
 *
 * A passenger/driver token resolves against `users` and carries `phone_masked`.
 * An admin token resolves against `admin_accounts` and carries `username` and
 * `email_masked` instead; there is no phone on an admin account at all.
 *
 * The fields are therefore optional rather than a union of two interfaces: the
 * console reads them defensively (`user?.username ?? user?.phone_masked`), and
 * a union would force a discriminant the server does not send. Declaring only
 * `phone_masked` — as this type used to — is what made the sidebar render `—`
 * for every admin, because the field it read is never present on an admin.
 */
export interface AdminIdentity {
  id: string;
  /**
   * The **principal kind** — `ADMIN` | `PASSENGER` | `DRIVER` — not a rank.
   *
   * Easy to mistake for the RBAC role, and it is not one: on an admin token
   * this is always the literal `'ADMIN'`, which is neither a `AdminRole` key
   * nor comparable to one. The rank lives in `admin_role` below. Reading this
   * field as a rank makes every result rank below `SUPPORT` and hides the
   * entire console — see `AppContext`, which is explicit about the difference.
   */
  role: string;
  /**
   * The **RBAC rank** — `SUPPORT` | `OPERATIONS` | `FINANCE` | `SUPER_ADMIN`.
   *
   * Admin scope only. There is no phone on an admin account, and there is no
   * rank on a passenger one, which is why this is optional rather than
   * required.
   */
  admin_role?: string;
  /** Passenger/driver scope only. */
  phone_masked?: string;
  /** Admin scope only. The login identifier. */
  username?: string;
  /** Admin scope only, masked server-side (`o***r@example.com`). */
  email_masked?: string;
}

/**
 * The **user** (passenger/driver) login response.
 *
 * Carries a `refresh_token` in the body, unlike the admin flow: the mobile app
 * is a native client with no cookie jar, so it must hold the token itself.
 * Nothing in this console consumes this type — an admin session's refresh token
 * arrives as an `HttpOnly` cookie and is never read by script. Kept so the
 * shape is documented and a future shared client cannot silently assume the two
 * flows match.
 */
export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  user: AdminIdentity;
}

/**
 * `POST /api/v1/admin/auth/login` — step 1 of admin sign-in.
 *
 * There is deliberately no `access_token` here. A password alone cannot produce
 * one; the second factor has to be proven first. `enrolment` is present only on
 * a first login, and the secret is returned exactly once.
 */
export interface AdminLoginResult {
  next: 'totp_required' | 'enrolment_required';
  challenge_token: string;
  enrolment?: AdminEnrolment;
}

/** `POST /api/v1/admin/auth/totp/enrol` - same shape, but material is mandatory. */
export type AdminEnrolmentReissue = Omit<AdminLoginResult, 'enrolment'> & {
  enrolment: AdminEnrolment;
};

export interface AdminEnrolment {
  /** Base32, for manual entry when the QR cannot be scanned. */
  secret: string;
  /** `otpauth://totp/...` — a standard key URI any TOTP app accepts. */
  otpauth_uri: string;
  recovery_codes: string[];
}

/** What a successful step 2 returns. */
export interface AdminSession {
  access_token: string;
  token_type: string;
  admin: AdminConsoleUser;
}

export interface AdminConsoleUser {
  id: string;
  username: string;
  email: string;
  full_name: string;
  totp_enrolled: boolean;
  /**
   * The RBAC rank, from `admin_accounts.admin_role`.
   *
   * Sent by the login response (`_admin_out` in `app/api/admin_auth.py`) and by
   * `GET /auth/me` (`AdminMeOut`). It was missing here, which meant the console
   * stored a signed-in identity with no role at all: `AppContext` then answered
   * `hasRole(...) === false` for every rank, the sidebar filtered to **zero**
   * nav items, and every gated page rendered 沒有存取權限 — on a *correct*
   * login. The server was never wrong; this type was.
   */
  admin_role?: string;
}

export type DriverStatus =
  | 'PENDING_KYC'
  | 'DEPOSIT_REQUIRED'
  | 'ACTIVE'
  | 'SUSPENDED'
  | 'TERMINATED';

/**
 * A driver row as `GET /api/v1/admin/drivers` returns it.
 *
 * Deliberately narrow: the list endpoint carries only what the queue renders in
 * a row. `hk_id_last4`, the deposit and the fleet are on `/admin/drivers/:id`
 * instead — do not add them here, or the type will promise fields the list never
 * sends.
 */
export interface AdminDriverRow {
  id: string;
  status: DriverStatus;
  taxi_type: string | null;
  taxi_driver_plate_no: string | null;
  vehicle_reg_mark: string | null;
}

/** The "everything about one driver" view, from `GET /admin/drivers/:id`. */
export interface DriverProfileDetail extends AdminDriverRow {
  user_id: string;
  hk_id_last4: string | null;
  is_online: boolean;
  kyc_reviewed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  deposit: DepositDetail;
  ledger: { items: LedgerEntry[] };
  refunds: { items: RefundRow[] };
  fleet: DriverFleetMembership | null;
}

/**
 * The deposit, with the shortfall spelled out.
 *
 * `required_hkd` is always present, even for a driver who has never been
 * credited — the page reads `shortfall_hkd` unconditionally, so both branches
 * emit the same keys.
 */
export interface DepositDetail {
  balance_hkd: string;
  held_hkd: string;
  required_hkd: string;
  is_fulfilled: boolean;
  has_account: boolean;
  shortfall_hkd: string;
  /** Set only after an operator manually releases a locked arrears driver. */
  acceptance_unlocked_at: string | null;
  /**
   * `true` only for a driver with an unpaid balance on the road: top-up alone
   * does not clear it. The operator must explicitly unlock acceptance.
   */
  acceptance_locked: boolean;
}

export interface LedgerEntry {
  id: number;
  entry_type: string;
  amount_hkd: string;
  balance_after_hkd: string;
  order_id: string | null;
  note: string | null;
  reference: string | null;
  /** Operator who posted this row; NULL for automated entries (settlement, refund). */
  created_by: string | null;
  created_at: string | null;
}

export interface DriverFleetMembership {
  fleet_id: string;
  name: string;
  license_no: string | null;
  status: string;
  weekly_fee_discount_percent: string;
  member_role: string;
  joined_at: string | null;
}

export type RefundStatus = 'PENDING' | 'APPROVED' | 'REJECTED';

export interface RefundRow {
  id: string;
  driver_profile_id: string;
  amount_hkd: string;
  /** True when the driver claimed less than the full balance (P2-2). */
  is_partial: boolean;
  status: RefundStatus;
  note: string | null;
  /** The operator's note on the decision — distinct from the applicant's. */
  decision_note: string | null;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string | null;
}

export interface Paged<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * The fleet lifecycle. Note the last value is `DISSOLVED`, not `TERMINATED` —
 * a fleet is wound up, a *driver* is terminated. The server's pattern is
 * `^(ACTIVE|SUSPENDED|DISSOLVED)$` (app/api/fleets.py).
 */
export type FleetStatus = 'ACTIVE' | 'SUSPENDED' | 'DISSOLVED';

export interface FleetRow {
  id: string;
  name: string;
  license_no: string | null;
  status: FleetStatus;
  weekly_fee_discount_percent: string;
  contact_name: string | null;
  contact_phone: string | null;
  note: string | null;
  created_at: string | null;
  /** Present on the list response only, where the server counts in one query. */
  member_count?: number;
}

/** A premium destination's visibility. Hidden rows stay in the admin list but never reach the public map. */
export type DestinationStatus = 'ACTIVE' | 'HIDDEN';

/**
 * A premium destination (`/api/v1/admin/destinations`).
 *
 * `avatar_key` is an object key, not a URL. The backend never stores a URL so a
 * leaked row cannot become a download link.
 */
export interface PremiumDestination {
  id: string;
  code: string;
  name_zh: string;
  name_en: string;
  lat: number;
  lng: number;
  radius_m: number;
  avatar_key: string | null;
  status: DestinationStatus;
  created_at: string | null;
}

export interface PremiumDestinationPage {
  items: PremiumDestination[];
}

/**
 * A roster row.
 *
 * Deliberately excludes the driver's HK ID fragment, licence number and vehicle
 * registration: an operator needs to know *who is on the roster*, not to read
 * back the identity documents they supplied. `driver_profile_id` is enough to
 * correlate with the ledger.
 */
export interface FleetMember {
  driver_profile_id: string;
  taxi_type: string | null;
  driver_status: string;
  member_role: string;
  status: string;
  joined_at: string | null;
  left_at: string | null;
}

/** One (fleet, ISO week) settlement aggregate. Re-runs update it in place. */
export interface FleetSettlementRow {
  period: string;
  fee_hkd: string;
  discount_percent: string;
  member_count: number;
  charged: number;
  skipped: number;
  failed: number;
  tampered: number;
  collected_hkd: string;
  created_at: string | null;
}

/** What the fleet detail page composes from its three endpoints. */
export interface FleetDetail {
  fleet: FleetRow;
  members: FleetMember[];
  settlement: FleetSettlementRow[];
}

/** `POST /admin/drivers/:id/deposit/grant` */
export interface GrantResult {
  balance_hkd: string;
  is_fulfilled: boolean;
}

/**
 * `POST /admin/drivers/:id/deposit/adjust`
 *
 * `amount_hkd` echoes the signed correction, so the UI can show direction
 * without re-deriving it. `driver_status` is returned for parity with the grant
 * response; unlike a grant, an adjustment never activates a driver.
 */
export interface AdjustResult {
  id: string;
  driver_status: string;
  amount_hkd: string;
  balance_hkd: string;
  is_fulfilled: boolean;
  reference: string | null;
}

/** `POST /admin/drivers/:id/deposit/unlock` — explicit manual release after arrears were topped up. */
export interface DepositUnlockResult {
  id: string;
  balance_hkd: string;
  is_fulfilled: boolean;
  acceptance_unlocked_at: string;
}

/**
 * `POST /admin/settlement/weekly/run`
 *
 * `fleet_managed` is the count the dashboard's note warns about: a driver billed
 * by their fleet is skipped by the platform-wide run, and if this number
 * disagrees with the fleets' own settlements the roster or the period is off.
 */
export interface SettlementRunResult {
  period: string;
  fee_hkd: string;
  eligible_drivers: number;
  fleet_managed: number;
  charged: number;
  skipped: number;
  failed: number;
  tampered: number;
}

/** The per-fleet settlement run. Idempotent per (fleet, ISO week). */
export interface FleetSettlementRunResult {
  period: string;
  gross_fee_hkd: string;
  discount_percent: string;
  fee_hkd: string;
  member_count: number;
  charged: number;
  skipped: number;
  failed: number;
  tampered: number;
  collected_hkd: string;
}

/**
 * P-3: the state of one licence submission.
 *
 * Deliberately separate from `DriverStatus`. A driver can be `ACTIVE` and trading
 * while a *renewal* sits `PENDING` — coupling the two would take someone offline
 * to re-upload a document.
 */
export type LicenceReviewStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'SUPERSEDED';

/**
 * The document kinds a submission can carry.
 *
 * `TAXI_DRIVER_PASS` is the HK 的士司機證 — the right to drive a taxi — while
 * `DRIVER_LICENCE` is the right to drive. Both are required; one alone is half
 * the evidence.
 */
export type DocumentKind =
  | 'DRIVER_LICENCE'
  | 'TAXI_DRIVER_PASS'
  | 'VEHICLE_REGISTRATION'
  | 'INSURANCE'
  | 'OTHER';

/** A queue row. Carries counts, not the documents themselves. */
export interface LicenceSubmissionRow {
  id: string;
  licence_no: string;
  expires_on: string;
  status: LicenceReviewStatus;
  submitted_note: string | null;
  rejection_reason: string | null;
  submitted_at: string;
  reviewed_at: string | null;
  driver_profile_id: string;
  /** The driver's own `DriverStatus`, so the queue shows who is trading. */
  driver_status: DriverStatus | null;
  document_count: number;
  /**
   * Whether both required kinds are attached. `false` is the row an operator
   * must not approve.
   */
  has_required_documents: boolean;
}

/**
 * One document in the detail view.
 *
 * `stored` is the field that matters: it is `false` when the client declared an
 * upload that never landed. Approving in that state is what the server refuses,
 * so the console shows it before the button is pressed.
 */
export interface LicenceDocument {
  id: string;
  kind: DocumentKind;
  content_type: string;
  size_bytes: number;
  uploaded_at: string | null;
  /** A signed, short-lived URL. Present only on the detail endpoint. */
  download_url: string;
  url_expires_in: number;
  stored: boolean;
  stored_size_bytes: number | null;
}

export interface LicenceSubmissionDetail extends Omit<LicenceSubmissionRow, 'document_count'> {
  driver_taxi_type: string | null;
  driver_plate_no: string | null;
  driver_vehicle_reg_mark: string | null;
  reviewed_by: string | null;
  documents: LicenceDocument[];
}

export interface LicenceDecisionResult {
  id: string;
  status: LicenceReviewStatus;
  reviewed_at: string | null;
  driver_status?: string;
  /** `true` only for a first approval that moved the driver out of PENDING_KYC. */
  driver_promoted?: boolean;
}

/** Bucket width for the analytics table. Matches the server's `Granularity`. */
export type AnalyticsGranularity = 'day' | 'week' | 'month' | 'year';

/** Columns the analytics table can be sorted by. Matches the server allow-list. */
export type AnalyticsSortBy = 'bucket' | 'orders' | 'earnings' | 'avg_fare' | 'distance';

export type SortDir = 'asc' | 'desc';

/** One row of the analytics table. Money is always a 2-dp string, never a float. */
export interface AnalyticsBucket {
  /** ISO date of the bucket's start, in Hong Kong time. */
  bucket: string;
  orders: number;
  earnings_hkd: string;
  avg_fare_hkd: string;
  distance_km: string;
}

export interface AnalyticsTotals {
  orders: number;
  earnings_hkd: string;
  avg_fare_hkd: string;
  distance_km: string;
  buckets: number;
  days: number;
}

export interface AnalyticsRange {
  from: string;
  to: string;
  granularity: AnalyticsGranularity;
  taxi_type: string | null;
  timezone: string;
}

export interface AnalyticsSummary {
  range: AnalyticsRange;
  totals: AnalyticsTotals;
  buckets: AnalyticsBucket[];
  sort: { by: AnalyticsSortBy; dir: SortDir };
}

/** One hour of the day in the heat map. */
export interface AnalyticsHourSlot {
  /** 0-23, in Hong Kong time. */
  hour: number;
  /** Mean earnings in this hour across every day in the range — the charted value. */
  avg_per_day_hkd: string;
  /** Mean earnings on the days this hour was actually worked. */
  avg_per_active_day_hkd: string;
  earnings_hkd: string;
  orders: number;
  active_days: number;
  avg_orders_per_day: string;
}

export interface AnalyticsHeatmap {
  range: {
    from: string;
    to: string;
    taxi_type: string | null;
    timezone: string;
    days: number;
  };
  /** Always 24 entries, including hours with no trips. */
  hours: AnalyticsHourSlot[];
  max_avg_per_day_hkd: string;
  peak_hour: number;
  busiest_hour: number;
  /** The y-axis maximum for the chart. Zero when there is nothing to draw. */
  scale_max_hkd: string;
}

/** Operations analytics: orders created in the range, not completed trips. */
export interface AnalyticsOperationsRange {
  from: string;
  to: string;
  taxi_type: string | null;
  timezone: string;
  days: number;
}

export interface AnalyticsOperationsFunnel {
  created: number;
  accepted: number;
  completed: number;
  interrupted: number;
  cancelled: number;
  active: number;
}

export interface AnalyticsCancellations {
  passenger: number;
  driver: number;
  timeout: number;
  unattributed: number;
}

export interface AnalyticsLatency {
  acceptance_avg_s: string | null;
  arrival_avg_s: string | null;
}

export interface AnalyticsOperations {
  range: AnalyticsOperationsRange;
  funnel: AnalyticsOperationsFunnel;
  cancellations: AnalyticsCancellations;
  latency: AnalyticsLatency;
  acceptance_rate: string;
  cancellation_rate: string;
}

/** Real-time supply snapshot, not a date-bucketed report. */
export interface AnalyticsSupply {
  sampled_at: string;
  active_drivers: number;
  online_drivers: number;
  online_with_gps: number;
  active_orders: number;
  engaged_drivers: number;
  available_drivers: number;
  supply_demand_ratio: string;
}

// ---------------------------------------------------------------------------
// RBAC
// ---------------------------------------------------------------------------

/**
 * The four admin roles, ordered by blast radius.
 *
 * The order is not cosmetic — it is the rank. `SUPPORT < OPERATIONS < FINANCE <
 * SUPER_ADMIN`, matching `AdminRole` in `app/models/admin.py`, and every
 * permission question is `rank(role) >= rank(required)` rather than set
 * membership. The server is the authority and re-reads the live row on every
 * request, so this list only decides **what the console shows**; a forged value
 * here produces 403s, not access.
 *
 * `SUPER_ADMIN` alone may change a role — see `AccountsPage`.
 */
export const ADMIN_ROLES = ['SUPPORT', 'OPERATIONS', 'FINANCE', 'SUPER_ADMIN'] as const;
export type AdminRole = (typeof ADMIN_ROLES)[number];

const ROLE_RANK: Record<AdminRole, number> = {
  SUPPORT: 0,
  OPERATIONS: 1,
  FINANCE: 2,
  SUPER_ADMIN: 3,
};

/** Whether `role` is at least `minimum`. An unknown role ranks below everything. */
export function roleAtLeast(role: string | null | undefined, minimum: AdminRole): boolean {
  const rank = ROLE_RANK[role as AdminRole];
  return rank !== undefined && rank >= ROLE_RANK[minimum];
}

// ---------------------------------------------------------------------------
// Order monitoring
// ---------------------------------------------------------------------------

/**
 * A trip, as `GET /admin/orders` returns it.
 *
 * The four timestamps are the point of the page: they answer "where is this
 * trip now, and how long has it been there", which the console could not answer
 * before. Each is individually nullable — a trip that never got a driver has no
 * `accepted_at`, and all-null with a `CREATED` status is meaningful rather than
 * missing data.
 *
 * `passenger_id` / `driver_id` are **ids, not names or phones**. Resolving one
 * is a separate, audited action; a list that inlined contact details would turn
 * every scroll into a bulk PII read.
 */
export interface AdminOrderRow {
  id: string;
  status: string;
  taxi_type: string;
  /**
   * `METER` or `FIXED`. A `FIXED` trip had its price agreed before pickup, so
   * a meter total that disagrees is expected, not a billing error — without
   * this an operator reading a dispute cannot tell the two apart.
   */
  fare_mode: string;
  passenger_id: string;
  driver_id: string | null;
  pickup_address: string;
  dropoff_address: string;
  distance_km: string;
  estimated_total_hkd: string;
  discount_percent: string;
  accepted_at: string | null;
  driver_arrived_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  cancellation_reason: string | null;
  created_at: string | null;
}

/** One step of the server-computed timeline. `at` is null when it never happened. */
export interface OrderTimelineStep {
  step: string;
  at: string | null;
  /** Seconds since `created`, computed server-side so DST cannot skew it. */
  elapsed_seconds: number | null;
}

/**
 * What the passenger asked for at booking time, frozen onto the order.
 *
 * `animal` is a **structured detail, not a flag** (`{"kind": "DOG",
 * "height_cm": 35, "weight_kg": 8}`), and it is `null` when the passenger never
 * mentioned a pet — which is *not* the same instruction as "no pet". The server
 * stores `null` rather than an all-false object precisely so the distinction
 * survives; do not collapse it in the UI.
 */
export interface OrderRequirements {
  silent_ride?: boolean;
  no_radio_music?: boolean;
  no_smoke?: boolean;
  no_perfume?: boolean;
  animal?: { kind: string; height_cm?: number; weight_kg?: number } | null;
}

/** The premium destination frozen onto the order — a snapshot, not a live join. */
export interface OrderPremiumDestination {
  id: string;
  code: string;
  name_zh: string;
  name_en: string;
  avatar_key: string | null;
}

/**
 * `GET /admin/orders/{id}` — one trip in full.
 *
 * `fare` is the **frozen `fare_json` snapshot**, not a recomputation: after any
 * tariff change a recomputed number differs, and the disputed amount is always
 * the one the passenger was quoted.
 *
 * The dispute record (`requirements`, both payment sides, `receipt_requested`)
 * rides this detail response only. A dispute is usually a disagreement about
 * what the passenger *asked for* — "I wanted a quiet car", "I asked to pay by
 * Octopus", "I asked for a receipt and never got one" — and the platform
 * recorded the request at booking time, before either party had a story to tell.
 */
export interface AdminOrderDetail extends AdminOrderRow {
  tariff_version: string;
  fare: Record<string, unknown>;
  broadcast_radius_km: string;
  timeline: OrderTimelineStep[];
  ledger: { items: LedgerEntry[] };
  unsettled_penalty: AdminUnsettledPenalty | null;
  requirements: OrderRequirements | null;
  payment_preference: string[];
  driver_payment_methods: string[];
  premium_destination: OrderPremiumDestination | null;
  destination_area: string | null;
  pickup_area: string | null;
  receipt_requested: boolean;
  receipt_requested_at: string | null;
}

/**
 * `GET /admin/orders/{id}` — the passenger-side P4 penalty that is recorded
 * but not debited. `settled: false` never leaves the server without this
 * object, so an operator can see the owed amount before deciding an offline
 * collection.
 */
export interface AdminUnsettledPenalty {
  amount_hkd: string;
  basis_hkd: string;
  share_percent: string;
  reason_code: string | null;
  cancellation_reason: string | null;
  actor_kind: string | null;
  charged_at: string | null;
}

/**
 * `GET /api/v1/admin/orders/{id}/receipt` — the frozen receipt document,
 * read-only for operators.
 *
 * Mirrors `ReceiptOut` from `app/api/schemas/receipt.py`. `fare` and
 * `requirements` stay open records because they are the order's own frozen
 * JSONB blocks, not re-typed fare snapshots. `text` is rendered by the server
 * from the same snapshot, so the structured view and the document view can
 * never disagree.
 */
export interface OrderReceipt {
  order_id: string;
  issued_at: string;
  status: string;
  taxi_type: string;
  fare_mode: string | null;
  pickup_address: string;
  dropoff_address: string;
  distance_km: string;
  pickup_area: string | null;
  destination_area: string | null;
  premium_destination: Record<string, unknown> | null;
  total_hkd: string;
  fare: Record<string, unknown>;
  fixed_fare: Record<string, unknown> | null;
  requirements: Record<string, unknown> | null;
  payment_preference: string[];
  driver_payment_methods: string[];
  passenger_name: string | null;
  tariff_version: string;
  created_at: string | null;
  completed_at: string | null;
  text: string;
  disclaimer_zh: string;
  disclaimer_en: string;
}

/** The five states a trip can be in and still be moving. Mirrors `_ORDER_OPEN_STATUSES`. */
export const OPEN_ORDER_STATUSES = [
  'CREATED',
  'BROADCASTING',
  'ACCEPTED',
  'DRIVER_ARRIVED',
  'IN_TRIP',
] as const;

/** Every lifecycle state, so the filter sends a value the server will accept. */
export const ORDER_STATUSES = [
  'CREATED',
  'BROADCASTING',
  'ACCEPTED',
  'DRIVER_ARRIVED',
  'IN_TRIP',
  'COMPLETED',
  'CANCELLED',
  'NO_DRIVER',
] as const;

/**
 * The two ways a fare can be set. Mirrors the `OrderFareMode` enum, which the
 * server validates the `fare_mode` filter against — a hand-written list here
 * that drifted from it would produce a 400 on a chip the UI itself offered.
 */
export const FARE_MODES = ['METER', 'FIXED'] as const;

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

/**
 * One `admin_audit_log` row.
 *
 * `payload` is typed as an open record on purpose: the shape differs per event,
 * and pinning it to one event's fields would make every other event render as
 * an empty object. The audit page renders it as key/value pairs.
 *
 * `actor_username` is the **attempted** username, denormalised onto the row. It
 * is present even when `actor_id` is null — the failed-login case — so the row
 * stays attributable after the account it names is gone.
 */
export interface AuditRow {
  id: string;
  actor_id: string | null;
  actor_username: string | null;
  event: string;
  outcome: string;
  detail: string | null;
  payload: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Admin accounts
// ---------------------------------------------------------------------------

/**
 * An admin account, from `GET /admin/accounts`.
 *
 * `totp_enrolled` is derived (`totp_secret is not None`), never the secret —
 * the secret does not leave the server, and enrolment is the only fact the
 * console acts on.
 */
export interface AdminAccount {
  id: string;
  username: string;
  email: string;
  full_name: string | null;
  admin_role: string;
  is_active: boolean;
  totp_enrolled: boolean;
  last_login_at: string | null;
  created_at: string;
}

/**
 * The admin roster plus the one derived count the demote button depends on.
 *
 * `super_admin_count` is on the page, not the row, because it is a property of
 * the whole set: the console must know "is this the last usable one?" before it
 * offers an action the server will refuse. It counts **active** super admins,
 * matching the server — a deactivated one is not a way to grant roles.
 */
export interface AdminAccountPage {
  items: AdminAccount[];
  super_admin_count: number;
  total: number;
}

/** `POST /admin/accounts` — the new account plus the fact that it cannot log in yet. */
export interface AdminAccountCreated extends AdminAccount {
  totp_enrolment_pending: boolean;
}

/**
 * `PATCH /admin/accounts/{id}/role` — carries both sides of the transition.
 *
 * The row only holds the current value, and "what was it before" is what the
 * reader is trying to confirm.
 */
export interface AdminRoleChange {
  id: string;
  previous_role: string;
  admin_role: string;
  super_admin_count: number;
}

/** `POST /admin/accounts/{id}/password/reset` — acknowledgement only, never the password. */
export interface AdminPasswordReset {
  id: string;
  /**
   * `true` — the reset revoked the account's sessions as well as its password.
   *
   * Two things die, by two different mechanisms. The refresh rows are stamped in
   * the same transaction as the password change, so the family cannot rotate.
   * The access token is a signed JWT with no server-side session store, so it is
   * killed at the revocation epoch instead — which makes it at most one
   * round-trip stale, not fifteen minutes stale.
   *
   * Reported rather than assumed: a reset that quietly did not eject anyone is
   * the difference between "the compromised credential is contained" and "it is
   * contained for the next fifteen minutes".
   */
  sessions_revoked: boolean;
}

/**
 * `PATCH /admin/accounts/{id}/active` — acknowledgement of a state change.
 *
 * `previous_is_active` is echoed for the same reason `AdminRoleChange` echoes
 * `previous_role`: the row holds only the new value, and the reader is trying to
 * confirm what changed.
 */
export interface AdminActiveChange {
  id: string;
  is_active: boolean;
  previous_is_active: boolean;
  /**
   * `true` when deactivating, `false` when reactivating.
   *
   * Not a nicety. Deactivation revokes the account's refresh family, and that is
   * most of the point: `require_admin` re-reads `is_active`, but a refresh row is
   * rotated rather than re-read, so an unstamped one would keep minting fresh
   * access tokens for its full lifetime. Reactivation has nothing to revoke,
   * because a deactivated account cannot authenticate to hold a session.
   */
  sessions_revoked: boolean;
}

// ---------------------------------------------------------------------------
// Settlement preview
// ---------------------------------------------------------------------------

/**
 * `POST /admin/settlement/preview` — what a run *would* do, and the token that
 * permits it.
 *
 * `would_charge + already_charged + tampered + skipped_no_deposit_account` equals
 * `eligible_drivers` exactly. `would_go_negative` is a **sub**-count of
 * `would_charge`, not a fifth bucket — those drivers are charged and simply go
 * into arrears, which the platform allows by design.
 *
 * `confirm_token` is the only way to run a settlement; it is signed over this
 * preview's period and fee, so a token for one week cannot be spent on another's.
 */
export interface SettlementPreview {
  period: string;
  fee_hkd: string;
  eligible_drivers: number;
  fleet_managed: number;
  would_charge: number;
  already_charged: number;
  tampered: number;
  skipped_no_deposit_account: number;
  would_go_negative: number;
  shortfall_total_hkd: string;
  total_charge_hkd: string;
  would_charge_driver_ids: string[];
  would_go_negative_driver_ids: string[];
  confirm_token: string;
  confirm_expires_in_seconds: number;
}

// ---------------------------------------------------------------------------
// Disputes
// ---------------------------------------------------------------------------

export const DISPUTE_SEVERITIES = ['LOW', 'NORMAL', 'HIGH', 'SAFETY_CRITICAL'] as const;
export type DisputeSeverity = (typeof DISPUTE_SEVERITIES)[number];

export const DISPUTE_CATEGORIES = [
  'FARE',
  'CONDUCT',
  'SAFETY',
  'LOST_ITEM',
  'APP_ISSUE',
  'OTHER',
] as const;
export type DisputeCategory = (typeof DISPUTE_CATEGORIES)[number];

export const DISPUTE_STATUSES = [
  'OPEN',
  'INVESTIGATING',
  'AWAITING_PARTY',
  'ESCALATED',
  'CLOSED',
] as const;
export type DisputeStatus = (typeof DISPUTE_STATUSES)[number];

/**
 * The five ways a case can end, and the one field the console reads off them.
 *
 * `CHARGE_PASSENGER`, `CHARGE_DRIVER`, `REFUND_PLATFORM_FEE` and
 * `WAIVED_PLATFORM_FEE` all move money; `NONE` decides that nobody is charged.
 * The server echoes `moves_money` on the resolve response so the console does not
 * have to re-derive this — a duplicated enum mapping is exactly what goes stale.
 */
export const DISPUTE_RESOLUTIONS = [
  'NONE',
  'CHARGE_PASSENGER',
  'CHARGE_DRIVER',
  'REFUND_PLATFORM_FEE',
  'WAIVED_PLATFORM_FEE',
] as const;
export type DisputeResolution = (typeof DISPUTE_RESOLUTIONS)[number];

/**
 * One row of the dispute queue.
 *
 * Every enum arrives as its string value, **plus** the derived fields the queue
 * is sorted and coloured by (`sla_hours`, `seconds_until_due`, `is_overdue`), so
 * the console does not re-implement severity→SLA. Two implementations of "is
 * this late" that drift is how a dashboard starts disagreeing with the API.
 *
 * `order_id` is nullable and that is meaningful: account- and app-level
 * complaints have no trip.
 */
export interface DisputeRow {
  id: string;
  order_id: string | null;
  source: string;
  category: string;
  severity: string;
  status: string;
  summary: string;
  raised_by_kind: string;
  against_kind: string | null;
  assigned_admin_id: string | null;
  safety_flag: boolean;
  sla_due_at: string;
  sla_hours: number;
  seconds_until_due: number;
  is_overdue: boolean;
  resolution: string | null;
  resolved_at: string | null;
  created_at: string;
}

/** One turn in a thread. `is_internal` is a staff note that must not be shown to a party. */
export interface DisputeMessage {
  id: number;
  author_kind: string;
  author_id: string | null;
  author_label: string | null;
  body: string;
  is_internal: boolean;
  created_at: string;
}

/** A case plus its whole thread. `messages` includes internal notes — this route is admin-only. */
export interface DisputeDetail extends DisputeRow {
  messages: DisputeMessage[];
  resolution_note: string | null;
  resolved_by: string | null;
  arrival_claimed_at: string | null;
  arrival_gps_distance_m: string | null;
  arrival_pin_attempts: number | null;
}

/**
 * Queue header counts, computed server-side against the same clock the sort uses.
 *
 * With pagination a client-side count is the count of the *current page*, which
 * reads as the total.
 */
export interface DisputeStats {
  total: number;
  open: number;
  overdue: number;
  unassigned: number;
  safety_flag: number;
}

/** `POST /admin/disputes/{id}/resolve` — `moves_money` echoed so the console need not infer it. */
export interface DisputeResolveResult {
  id: string;
  status: string;
  resolution: string;
  moves_money: boolean;
  resolved_at: string;
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

/**
 * One search hit: a passenger or a driver, in a single shape.
 *
 * A **superset** with the irrelevant fields null rather than absent. A
 * discriminated union would be more precise and worse here — the console renders
 * one list, and `item.plate ?? '—'` is the whole rendering either way. `kind` is
 * what the UI branches on.
 *
 * Carries the phone and display name because those are what the caller searched
 * with. Deliberately **no** ID number, no document keys, no ledger: a search
 * result is a pointer, and the detail page is the audited place to look at a
 * person.
 */
export interface SearchResult {
  kind: 'PASSENGER' | 'DRIVER';
  id: string;
  display_name: string | null;
  phone_e164: string;
  username: string | null;
  account_status: string;
  is_active: boolean;
  avatar_key: string | null;
  driver_profile_id: string | null;
  plate: string | null;
  driver_status: string | null;
}

/**
 * Search results plus the two facts the UI must state.
 *
 * `truncated` is **not** derivable from `len(items) === limit`: a result set
 * that is exactly `limit` long may or may not have more, and "showing 20 of 20"
 * when there are 400 is the difference between narrowing the search and
 * believing you have seen everyone.
 *
 * `query_too_short` distinguishes "no matches" from "you did not search", which
 * are otherwise the same empty list.
 */
export interface SearchResponse {
  items: SearchResult[];
  query: string;
  truncated: boolean;
  query_too_short: boolean;
  min_query_length: number;
}

/** `POST /identity/avatar/uploads` — a presigned PUT, minted before the key is claimed. */
export interface AvatarPresign {
  upload_url: string;
  object_key: string;
  expires_in: number;
  max_bytes: number;
  content_type: string;
}

// ---------------------------------------------------------------------------
// Live map
// ---------------------------------------------------------------------------

/**
 * One car on the live map: where it is, and what it is doing.
 *
 * `lat` / `lng` are **not** nullable, and that is a fact about the server rather
 * than optimism: the query filters `current_location IS NOT NULL`, so a driver
 * with no position fix is *absent* from the list rather than present with null
 * coordinates. Typing them as nullable here would oblige every renderer to
 * invent a fallback for a case the API cannot produce, and the obvious fallback
 * — `0` — puts the car in the Gulf of Guinea.
 *
 * `order_id` / `order_status` are null for a driver who is online but
 * unassigned, which is most of the fleet most of the time. They are flat
 * siblings rather than a nested object, so "is this car running a trip" is
 * `order_id !== null` and not a null check on a sub-object.
 */
export interface AdminLiveDriver {
  driver_profile_id: string;
  status: string;
  taxi_type: string;
  vehicle_reg_mark: string;
  is_online: boolean;
  last_location_at: string | null;
  lat: number;
  lng: number;
  order_id: string | null;
  order_status: string | null;
}

/**
 * The whole map in one poll.
 *
 * **Two timestamps, and they are not interchangeable.** `generated_at` is the
 * server clock at the moment of the read — how fresh the *snapshot* is —
 * whereas `last_location_at` is per car — how stale one *vehicle* is. A view
 * that showed only the first would draw a car that stopped reporting ten minutes
 * ago exactly like one that moved a second ago, which is the single most
 * misleading thing a live map can do.
 *
 * `truncated` is stated rather than derived, for the same reason as in
 * `SearchResponse`: `drivers.length === limit` does not tell you whether more
 * exist, and a map that silently drops the 501st car is indistinguishable from a
 * fleet that shrank.
 */
export interface AdminLiveDrivers {
  generated_at: string;
  drivers: AdminLiveDriver[];
  truncated: boolean;
}
