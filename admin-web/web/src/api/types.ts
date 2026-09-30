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
