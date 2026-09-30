/**
 * Domain types for the console.
 *
 * Hand-written from `app/api/admin.py`, which is the authority: these are the
 * fields the console actually reads, spelled as the server sends them. Money is
 * always a **string** on the wire (the server formats it with `money_str`), so
 * every amount here is a string — do not "helpfully" type it as `number` or the
 * page will render `NaN` after arithmetic.
 */

/** `GET /api/v1/auth/me` */
export interface AdminIdentity {
  id: string;
  phone_masked: string;
  role: string;
}

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
