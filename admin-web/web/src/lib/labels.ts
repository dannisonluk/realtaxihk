/**
 * Server vocabulary, shared by every page that renders an enum.
 *
 * The tables below map the server's enum **values to i18n keys**, not to display
 * strings. Display strings live in `src/i18n/locales/`, because they have to
 * exist in two languages and a single-language constant here would silently
 * pin every chip to Chinese.
 *
 * Two layers, deliberately:
 *
 *   1. `*_KEY` maps a value to a key (`DRIVER_STATUS_KEY.ACTIVE` is
 *      `'enum.driverStatus.ACTIVE'`). A *tone* map lives beside it — the tone is
 *      a design decision about severity, not a translation, so it stays here and
 *      does not move to the resource files.
 *   2. `useLabels()` binds those keys to the active language and returns
 *      `label(value)` functions with the same shape the pages already call, so a
 *      page changes `driverStatusLabel(x)` to `labels.driverStatus(x)` and
 *      nothing else.
 *
 * The fallback for an unknown value is the **raw enum**, in both layers: a new
 * server value renders as `NEW_STATE` rather than as an empty chip, which is the
 * one failure mode an operator can actually diagnose.
 */

import { useMemo } from 'react';
import type { ChipTone } from '../components/primitives';
import { useI18n } from '../i18n';
import type {
  DriverStatus,
  FleetStatus,
  LicenceReviewStatus,
  RefundStatus,
} from '../api/types';

/** Wrap an enum value in an i18n key for a given group. */
function keyer(group: string) {
  return (value: string) => `enum.${group}.${value}`;
}

const driverStatusKey = keyer('driverStatus');
const refundStatusKey = keyer('refundStatus');
const taxiTypeKey = keyer('taxiType');
const fleetStatusKey = keyer('fleetStatus');
const memberRoleKey = keyer('memberRole');
const memberStatusKey = keyer('memberStatus');
const entryKey = keyer('entry');
const orderStatusKey = keyer('orderStatus');
const disputeSeverityKey = keyer('disputeSeverity');
const disputeCategoryKey = keyer('disputeCategory');
const disputeStatusKey = keyer('disputeStatus');
const disputeResolutionKey = keyer('disputeResolution');
const disputeSourceKey = keyer('disputeSource');
const licenceStatusKey = keyer('licenceStatus');
const documentKindKey = keyer('documentKind');
const accountKindKey = keyer('accountKind');
const paymentMethodKey = keyer('paymentMethod');
const areaKey = keyer('area');
const requirementKey = keyer('requirement');

// ------------------------------------------------------------------ tones --
// Tones are severity, not language: they describe *how bad* a state is, which is
// the same question in every locale. Kept here rather than in the resources
// because a translator must not be able to change a colour by editing a string.

const DRIVER_STATUS_TONE: Record<DriverStatus, ChipTone> = {
  PENDING_KYC: 'warn',
  DEPOSIT_REQUIRED: 'brand',
  ACTIVE: 'ok',
  SUSPENDED: 'danger',
  TERMINATED: 'neutral',
};

const REFUND_STATUS_TONE: Record<RefundStatus, ChipTone> = {
  PENDING: 'warn',
  APPROVED: 'ok',
  REJECTED: 'danger',
};

const FLEET_STATUS_TONE: Record<FleetStatus, ChipTone> = {
  ACTIVE: 'ok',
  SUSPENDED: 'danger',
  DISSOLVED: 'neutral',
};

/**
 * Ledger entry kinds.
 *
 * The tone carries the direction, so a penalty reads as bad and a top-up as
 * good without the operator parsing the sign.
 */
const ENTRY_TONE: Record<string, ChipTone> = {
  DEPOSIT_TOPUP: 'ok',
  WEEKLY_FEE_DEDUCTION: 'neutral',
  PENALTY_DEDUCTION: 'danger',
  REFUND: 'brand',
  ADJUSTMENT: 'warn',
};

/**
 * Order lifecycle states.
 *
 * `CANCELLED` and `NO_DRIVER` are `danger` — somebody has to look at both —
 * while `COMPLETED` is neutral rather than green, because a finished trip is not
 * something anyone acts on. Green is reserved for the states that mean "still
 * working as intended".
 */
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

/**
 * `SAFETY_CRITICAL` is `danger`, and so is `HIGH` — the distinction between them
 * is the *deadline* (1h vs 4h), which the console shows as a countdown, not the
 * colour. Colouring only the top level would make a 4-hour case look routine.
 */
const DISPUTE_SEVERITY_TONE: Record<string, ChipTone> = {
  LOW: 'neutral',
  NORMAL: 'warn',
  HIGH: 'danger',
  SAFETY_CRITICAL: 'danger',
};

const DISPUTE_STATUS_TONE: Record<string, ChipTone> = {
  OPEN: 'warn',
  INVESTIGATING: 'brand',
  AWAITING_PARTY: 'neutral',
  ESCALATED: 'danger',
  RESOLVED: 'ok',
  CLOSED: 'neutral',
};

/**
 * `SUPERSEDED` is neutral, not a failure: the driver withdrew their own
 * submission to fix a photo, which is the flow working as intended.
 */
const LICENCE_STATUS_TONE: Record<LicenceReviewStatus, ChipTone> = {
  PENDING: 'warn',
  APPROVED: 'ok',
  REJECTED: 'danger',
  SUPERSEDED: 'neutral',
};

// ---------------------------------------------------------------- hook ----

export interface Labels {
  driverStatus: (status: string) => string;
  driverStatusTone: (status: string) => ChipTone;
  refundStatus: (status: string) => string;
  refundStatusTone: (status: string) => ChipTone;
  taxiType: (type: string | null) => string;
  fleetStatus: (status: string) => string;
  fleetStatusTone: (status: string) => ChipTone;
  memberRole: (role: string | null) => string;
  memberStatus: (status: string) => string;
  entry: (type: string) => string;
  entryTone: (type: string) => ChipTone;
  orderStatus: (status: string) => string;
  orderStatusTone: (status: string) => ChipTone;
  disputeSeverity: (severity: string) => string;
  disputeSeverityTone: (severity: string) => ChipTone;
  disputeCategory: (category: string) => string;
  disputeStatus: (status: string) => string;
  disputeStatusTone: (status: string) => ChipTone;
  disputeResolution: (resolution: string | null) => string;
  disputeSource: (source: string) => string;
  licenceStatus: (status: string) => string;
  licenceStatusTone: (status: string) => ChipTone;
  documentKind: (kind: string) => string;
  accountKind: (kind: string) => string;
  paymentMethod: (method: string) => string;
  area: (area: string | null) => string;
  requirement: (key: string) => string;
}

/**
 * Every enum labeller, bound to the active locale.
 *
 * `t(key, { defaultValue: raw })` is the fallback mechanism: i18next returns
 * `defaultValue` when the key is absent, so an unknown server value renders as
 * itself. That is why the raw value is passed as `defaultValue` rather than
 * being branched on first — one path, no `if`.
 */
export function useLabels(): Labels {
  const { t } = useI18n();

  return useMemo(() => {
    const label = (keyOf: (v: string) => string) => (value: string | null | undefined) =>
      value ? t(keyOf(value), { defaultValue: value }) : '—';
    const tone = (map: Record<string, ChipTone>) => (value: string) => map[value] ?? 'neutral';

    return {
      driverStatus: label(driverStatusKey),
      driverStatusTone: tone(DRIVER_STATUS_TONE),
      refundStatus: label(refundStatusKey),
      refundStatusTone: tone(REFUND_STATUS_TONE),
      taxiType: label(taxiTypeKey),
      fleetStatus: label(fleetStatusKey),
      fleetStatusTone: tone(FLEET_STATUS_TONE),
      memberRole: label(memberRoleKey),
      memberStatus: label(memberStatusKey),
      entry: label(entryKey),
      entryTone: tone(ENTRY_TONE),
      orderStatus: label(orderStatusKey),
      orderStatusTone: tone(ORDER_STATUS_TONE),
      disputeSeverity: label(disputeSeverityKey),
      disputeSeverityTone: tone(DISPUTE_SEVERITY_TONE),
      disputeCategory: label(disputeCategoryKey),
      disputeStatus: label(disputeStatusKey),
      disputeStatusTone: tone(DISPUTE_STATUS_TONE),
      disputeResolution: (resolution) =>
        resolution
          ? t(disputeResolutionKey(resolution), { defaultValue: resolution })
          : t('enum.disputeResolution._undecided'),
      disputeSource: label(disputeSourceKey),
      licenceStatus: label(licenceStatusKey),
      licenceStatusTone: tone(LICENCE_STATUS_TONE),
      documentKind: label(documentKindKey),
      accountKind: label(accountKindKey),
      paymentMethod: label(paymentMethodKey),
      area: label(areaKey),
      requirement: label(requirementKey),
    };
  }, [t]);
}

// ------------------------------------------------------------ formatting --

/**
 * An ISO timestamp as a compact local string, or an em dash.
 *
 * The locale is passed in rather than read from a module constant, because a
 * `zh-HK` date format in an English console prints `2026/10/01` and an `en-HK`
 * one prints `01/10/2026` — the same string, ambiguous in opposite directions.
 * The caller gets it from `useI18n().formatLocale`.
 */
export function formatTime(
  iso: string | null | undefined,
  locale = 'zh-HK',
): string {
  if (!iso) return '—';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '—';
  return at.toLocaleString(locale, {
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

/** A UUID shortened to its first block — enough to correlate rows by eye. */
export function shortId(id: string | null | undefined): string {
  if (!id) return '—';
  return id.split('-')[0] ?? id;
}

/**
 * A deadline as a human countdown.
 *
 * Negative is the common case on an incident queue and is rendered as overdue
 * rather than as a negative number — an operator should not have to read a sign
 * to know a case has breached.
 *
 * The units come from the translations (`{{days}} 天 {{hours}} 小時` / `{{days}}
 * d {{hours}} h`), so the same numbers read naturally in both languages without
 * a second formatting path.
 */
export function formatCountdown(
  seconds: number,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const overdue = seconds < 0;
  const magnitude = Math.abs(seconds);
  const hours = Math.floor(magnitude / 3600);
  const minutes = Math.floor((magnitude % 3600) / 60);
  const body =
    hours >= 24
      ? t('duration.daysHours', { days: Math.floor(hours / 24), hours: hours % 24 })
      : hours >= 1
        ? t('duration.hoursMinutes', { hours, minutes })
        : t('duration.minutes', { minutes });
  return overdue ? t('duration.overdue', { body }) : t('duration.remaining', { body });
}

/**
 * Bytes as a human string.
 *
 * Decimal-ish steps with a fixed `MB`, because the value being checked against is
 * a per-file ceiling expressed in MB — an operator comparing "4.7 MB" to a
 * "5 MB limit" should not have to convert from MiB. Units are the same in both
 * locales, so this needs no translation.
 */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A date-only ISO string (`2027-09-30T...`) as `YYYY-MM-DD`, or an em dash. */
export function formatDate(iso: string | null | undefined, locale = 'zh-HK'): string {
  if (!iso) return '—';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '—';
  return at.toLocaleDateString(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
}
