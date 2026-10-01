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
 * Order lifecycle states.
 *
 * The tones follow the same rule as everywhere else in this file: a *problem*
 * is red and a *healthy* state is green. So `CANCELLED` and `NO_DRIVER` are
 * `danger` — somebody has to look at both — while `COMPLETED` is neutral rather
 * than green, because a finished trip is not something anyone acts on. Green is
 * reserved for the states that mean "still working as intended".
 */
export const ORDER_STATUS_LABEL: Record<string, string> = {
  CREATED: '已建立',
  BROADCASTING: '廣播中',
  ACCEPTED: '已接單',
  DRIVER_ARRIVED: '司機到達',
  IN_TRIP: '行程中',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
  NO_DRIVER: '無人接單',
};

const ORDER_STATUS_TONE: Record<string, ChipTone> = {
  CREATED: 'neutral',
  BROADCASTING: 'brand',
  ACCEPTED: 'ok',
  DRIVER_ARRIVED: 'ok',
  IN_TRIP: 'ok',
  COMPLETED: 'neutral',
  CANCELLED: 'danger',
  NO_DRIVER: 'danger',
};

export function orderStatusLabel(status: string): string {
  return ORDER_STATUS_LABEL[status] ?? status;
}

export function orderStatusTone(status: string): ChipTone {
  return ORDER_STATUS_TONE[status] ?? 'neutral';
}

/**
 * Dispute severity.
 *
 * `SAFETY_CRITICAL` is `danger`, and so is `HIGH` — the distinction between
 * them is the *deadline* (1h vs 4h), which the console shows as a countdown,
 * not the colour. Colouring only the top level would make a 4-hour case look
 * like a routine one.
 */
export const DISPUTE_SEVERITY_LABEL: Record<string, string> = {
  LOW: '低',
  NORMAL: '一般',
  HIGH: '高',
  SAFETY_CRITICAL: '安全緊急',
};

const DISPUTE_SEVERITY_TONE: Record<string, ChipTone> = {
  LOW: 'neutral',
  NORMAL: 'warn',
  HIGH: 'danger',
  SAFETY_CRITICAL: 'danger',
};

export function disputeSeverityLabel(severity: string): string {
  return DISPUTE_SEVERITY_LABEL[severity] ?? severity;
}

export function disputeSeverityTone(severity: string): ChipTone {
  return DISPUTE_SEVERITY_TONE[severity] ?? 'neutral';
}

export const DISPUTE_CATEGORY_LABEL: Record<string, string> = {
  FARE: '車費',
  CONDUCT: '服務態度',
  SAFETY: '安全',
  LOST_ITEM: '失物',
  APP_ISSUE: '應用程式問題',
  OTHER: '其他',
};

export function disputeCategoryLabel(category: string): string {
  return DISPUTE_CATEGORY_LABEL[category] ?? category;
}

export const DISPUTE_STATUS_LABEL: Record<string, string> = {
  OPEN: '待處理',
  INVESTIGATING: '調查中',
  AWAITING_PARTY: '待對方回覆',
  ESCALATED: '已升級',
  RESOLVED: '已裁決',
  CLOSED: '已結案',
};

const DISPUTE_STATUS_TONE: Record<string, ChipTone> = {
  OPEN: 'warn',
  INVESTIGATING: 'brand',
  AWAITING_PARTY: 'neutral',
  ESCALATED: 'danger',
  RESOLVED: 'ok',
  CLOSED: 'neutral',
};

export function disputeStatusLabel(status: string): string {
  return DISPUTE_STATUS_LABEL[status] ?? status;
}

export function disputeStatusTone(status: string): ChipTone {
  return DISPUTE_STATUS_TONE[status] ?? 'neutral';
}

/**
 * How a case ended.
 *
 * `moves_money` is the field that matters — four of the five resolutions charge
 * or refund somebody, and the server echoes that flag so this mapping is only
 * for the label. It is not used to decide whether FINANCE is required.
 */
export const DISPUTE_RESOLUTION_LABEL: Record<string, string> = {
  NONE: '不作收費',
  CHARGE_PASSENGER: '向乘客收費',
  CHARGE_DRIVER: '向司機收費',
  REFUND_PLATFORM_FEE: '退還平台費',
  WAIVED_PLATFORM_FEE: '豁免平台費',
};

export function disputeResolutionLabel(resolution: string | null): string {
  if (!resolution) return '尚未裁決';
  return DISPUTE_RESOLUTION_LABEL[resolution] ?? resolution;
}

/**
 * The two sources a case can come from.
 *
 * `INTERRUPTION` is a case opened by the in-trip interruption flow, which is
 * designed but not yet implemented — it is labelled anyway so the value does
 * not render as a raw enum the day it starts arriving.
 */
export const DISPUTE_SOURCE_LABEL: Record<string, string> = {
  ADMIN_CREATED: '人手開立',
  PASSENGER: '乘客提出',
  DRIVER: '司機提出',
  INTERRUPTION: '行程中斷',
  AUTOMATED: '系統偵測',
};

export function disputeSourceLabel(source: string): string {
  return DISPUTE_SOURCE_LABEL[source] ?? source;
}

/**
 * A deadline as a human countdown.
 *
 * Negative is the common case on an incident queue and is rendered as overdue
 * rather than as a negative number — an operator should not have to read a sign
 * to know a case has breached.
 */
export function formatCountdown(seconds: number): string {
  const overdue = seconds < 0;
  const magnitude = Math.abs(seconds);
  const hours = Math.floor(magnitude / 3600);
  const minutes = Math.floor((magnitude % 3600) / 60);
  let body: string;
  if (hours >= 24) {
    const days = Math.floor(hours / 24);
    body = `${days} 天 ${hours % 24} 小時`;
  } else if (hours >= 1) {
    body = `${hours} 小時 ${minutes} 分`;
  } else {
    body = `${minutes} 分`;
  }
  return overdue ? `已逾期 ${body}` : `剩餘 ${body}`;
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
