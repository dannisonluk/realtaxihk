/**
 * Driver-status vocabulary, shared by the queue, the detail page and the fleet
 * roster, so a label is written once.
 *
 * The tones map onto the `.chip--*` classes rather than raw colours: they must
 * agree in both colour schemes, and the HK convention here is that a *problem*
 * is red and a *healthy* account is green.
 */

import type { ChipTone } from '../components/primitives';
import type {
  DriverStatus,
  FleetStatus,
  LicenceReviewStatus,
  RefundStatus,
} from '../api/types';

export const DRIVER_STATUS_LABEL: Record<DriverStatus, string> = {
  PENDING_KYC: '審核中',
  DEPOSIT_REQUIRED: '待繳按金',
  ACTIVE: '已啟用',
  SUSPENDED: '已停權',
  TERMINATED: '已終止',
};

const DRIVER_STATUS_TONE: Record<DriverStatus, ChipTone> = {
  PENDING_KYC: 'warn',
  DEPOSIT_REQUIRED: 'brand',
  ACTIVE: 'ok',
  SUSPENDED: 'danger',
  TERMINATED: 'neutral',
};

export function driverStatusLabel(status: string): string {
  return DRIVER_STATUS_LABEL[status as DriverStatus] ?? status;
}

export function driverStatusTone(status: string): ChipTone {
  return DRIVER_STATUS_TONE[status as DriverStatus] ?? 'neutral';
}

export const REFUND_STATUS_LABEL: Record<RefundStatus, string> = {
  PENDING: '待處理',
  APPROVED: '已批准',
  REJECTED: '已拒絕',
};

const REFUND_STATUS_TONE: Record<RefundStatus, ChipTone> = {
  PENDING: 'warn',
  APPROVED: 'ok',
  REJECTED: 'danger',
};

export function refundStatusLabel(status: string): string {
  return REFUND_STATUS_LABEL[status as RefundStatus] ?? status;
}

export function refundStatusTone(status: string): ChipTone {
  return REFUND_STATUS_TONE[status as RefundStatus] ?? 'neutral';
}

/**
 * The three taxi types, by their licensed Chinese names.
 *
 * A new type is a server change, so the fallback is the raw enum rather than a
 * guess — better to show `URBAN_NEW` than to label it something wrong.
 */
export const TAXI_TYPE_LABEL: Record<string, string> = {
  URBAN: '市區的士',
  NT: '新界的士',
  LANTAU: '大嶼山的士',
};

export function taxiTypeLabel(type: string | null): string {
  if (!type) return '—';
  return TAXI_TYPE_LABEL[type] ?? type;
}

/** An ISO timestamp as a compact local string, or an em dash. */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '—';
  return at.toLocaleString('zh-HK', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** Today's ISO week, e.g. `2026-W47`, matching the server's settlement period. */
export function currentIsoWeek(): string {
  const now = new Date();
  const date = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
  const day = date.getUTCDay() || 7; // Monday = 1 … Sunday = 7
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${date.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

/** The server rejects anything that is not `YYYY-Www` (app/api/fleets.py). */
export const PERIOD_PATTERN = /^\d{4}-W\d{2}$/;

/**
 * Fleet lifecycle. `DISSOLVED` — a fleet is wound up; only a *driver* is
 * terminated. Getting this wrong shows an empty status chip.
 */
export const FLEET_STATUS_LABEL: Record<FleetStatus, string> = {
  ACTIVE: '營運中',
  SUSPENDED: '已停權',
  DISSOLVED: '已解散',
};

const FLEET_STATUS_TONE: Record<FleetStatus, ChipTone> = {
  ACTIVE: 'ok',
  SUSPENDED: 'danger',
  DISSOLVED: 'neutral',
};

export function fleetStatusLabel(status: string): string {
  return FLEET_STATUS_LABEL[status as FleetStatus] ?? status;
}

export function fleetStatusTone(status: string): ChipTone {
  return FLEET_STATUS_TONE[status as FleetStatus] ?? 'neutral';
}

/** Roster roles. OWNER is the licence holder. */
export const MEMBER_ROLE_LABEL: Record<string, string> = {
  OWNER: '車主',
  MANAGER: '管理員',
  MEMBER: '成員',
};

export function memberRoleLabel(role: string | null): string {
  if (!role) return '—';
  return MEMBER_ROLE_LABEL[role] ?? role;
}

/**
 * Ledger entry kinds.
 *
 * The tone carries the direction, so a penalty reads as bad and a top-up as
 * good without the operator parsing the sign.
 */
export const ENTRY_LABEL: Record<string, string> = {
  DEPOSIT_TOPUP: '存入按金',
  WEEKLY_FEE_DEDUCTION: '每週費用',
  PENALTY_DEDUCTION: '罰款',
  REFUND: '退款',
  ADJUSTMENT: '調整',
};

const ENTRY_TONE: Record<string, ChipTone> = {
  DEPOSIT_TOPUP: 'ok',
  WEEKLY_FEE_DEDUCTION: 'neutral',
  PENALTY_DEDUCTION: 'danger',
  REFUND: 'brand',
  ADJUSTMENT: 'warn',
};

export function entryLabel(type: string): string {
  return ENTRY_LABEL[type] ?? type;
}

export function entryTone(type: string): ChipTone {
  return ENTRY_TONE[type] ?? 'neutral';
}

/** A UUID shortened to its first block — enough to correlate rows by eye. */
export function shortId(id: string | null | undefined): string {
  if (!id) return '—';
  return id.split('-')[0] ?? id;
}

/**
 * P-3 licence review states.
 *
 * `SUPERSEDED` is neutral, not a failure: the driver withdrew their own
 * submission to fix a photo, which is the flow working as intended. Colouring it
 * like a rejection would make the queue look like a problem when it is not.
 */
export const LICENCE_STATUS_LABEL: Record<LicenceReviewStatus, string> = {
  PENDING: '待審核',
  APPROVED: '已通過',
  REJECTED: '已拒絕',
  SUPERSEDED: '已撤回',
};

const LICENCE_STATUS_TONE: Record<LicenceReviewStatus, ChipTone> = {
  PENDING: 'warn',
  APPROVED: 'ok',
  REJECTED: 'danger',
  SUPERSEDED: 'neutral',
};

export function licenceStatusLabel(status: string): string {
  return LICENCE_STATUS_LABEL[status as LicenceReviewStatus] ?? status;
}

export function licenceStatusTone(status: string): ChipTone {
  return LICENCE_STATUS_TONE[status as LicenceReviewStatus] ?? 'neutral';
}

/**
 * Document kinds, by their licensed Chinese names.
 *
 * `TAXI_DRIVER_PASS` is the 的士司機證 — the Transport Department's permission to
 * drive a taxi — which is the document an operator is actually checking for.
 */
export const DOCUMENT_KIND_LABEL: Record<string, string> = {
  DRIVER_LICENCE: '正式駕駛執照',
  TAXI_DRIVER_PASS: '的士司機證',
  VEHICLE_REGISTRATION: '車輛登記文件',
  INSURANCE: '保險',
  OTHER: '其他',
};

export function documentKindLabel(kind: string): string {
  return DOCUMENT_KIND_LABEL[kind] ?? kind;
}

/**
 * Bytes as a human string.
 *
 * Decimal-ish steps with a fixed `MB`, because the value being checked against
 * is a per-file ceiling expressed in MB — an operator comparing "4.7 MB" to a
 * "5 MB limit" should not have to convert from MiB.
 */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A date-only ISO string (`2027-09-30T...`) as `YYYY-MM-DD`. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '—';
  return at.toLocaleDateString('zh-HK', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
}
