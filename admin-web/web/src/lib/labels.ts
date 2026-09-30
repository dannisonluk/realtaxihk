/**
 * Driver-status vocabulary, shared by the queue, the detail page and the fleet
 * roster, so a label is written once.
 *
 * The tones map onto the `.chip--*` classes rather than raw colours: they must
 * agree in both colour schemes, and the HK convention here is that a *problem*
 * is red and a *healthy* account is green.
 */

import type { ChipTone } from '../components/primitives';
import type { DriverStatus, FleetStatus, RefundStatus } from '../api/types';

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
