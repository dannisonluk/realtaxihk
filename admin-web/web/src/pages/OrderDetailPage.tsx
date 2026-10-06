/**
 * One trip, in full.
 *
 * This is the page that answers the phone call. A passenger says the driver
 * never arrived; the operator needs the state, the driver, and how long each
 * step took — and the past-tense question ("how long did it sit in ACCEPTED
 * before somebody moved it") is answered by the timeline, not by the timestamps
 * alone.
 *
 * Three things are deliberately read from the server rather than computed here:
 *
 * * **`fare`** is the stored `fare_json` **snapshot**, not a recomputation. A
 *   fare recomputed today is a different number after any tariff change, and the
 *   disputed amount is always the one the passenger was actually quoted — which
 *   is also why `tariff_version` is shown beside it.
 * * **`timeline[].elapsed_seconds`** is computed server-side. Subtracting two
 *   ISO strings in the browser gets the DST case wrong; Hong Kong has no DST
 *   today, but a server-side subtraction is correct regardless and costs
 *   nothing. The UI must never re-derive it.
 * * **`broadcast_radius_km`** so "why did nobody take it" is answerable at all.
 */

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { endpoints } from '../api/endpoints';
import { Card, Chip, DetailRow, Money, Rows } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { useApp } from '../app/AppContext';
import { useLoad } from '../app/useLoad';
import { formatTime, shortId, useLabels } from '../lib/labels';
import type { OrderReceipt, OrderRequirements } from '../api/types';
import { useI18n } from '../i18n';
import { PageHead } from '../app/Shell';

export function OrderDetailPage() {
  const { orderId = '' } = useParams();
  const { client } = useApp();
  const { t, formatLocale } = useI18n();
  const labels = useLabels();

  const { data, error, loading, reload } = useLoad(
    () => endpoints.orders.detail(client, orderId),
    [client, orderId],
  );
  const [receipt, setReceipt] = useState<OrderReceipt | null>(null);
  const [receiptError, setReceiptError] = useState<string | null>(null);

  if (loading) return <LoadingState />;
  if (error) {
    return (
      <div>
        <div style={{ marginBottom: 12 }}>
          <Link className="btn" to="/orders">
            {t('orderDetail.back')}
          </Link>
        </div>
        <ErrorState error={error} onRetry={reload} />
      </div>
    );
  }
  if (!data) return null;

  const ledger = data.ledger?.items ?? [];

  return (
    <div>
      <PageHead
        title={t('orderDetail.title')}
        actions={
          <Link className="btn" to="/orders">
            {t('orderDetail.back')}
          </Link>
        }
      />

      <Card className="card--pad">
        <div className="row-inline" style={{ marginBottom: 16 }}>
          <Chip tone={labels.orderStatusTone(data.status)}>{labels.orderStatus(data.status)}</Chip>
          <span className="dim mono">{shortId(data.id)}</span>
          <span className="dim">{labels.taxiType(data.taxi_type)}</span>
          {/* Shown unconditionally, unlike the list: on a dispute page the
              operator must be able to *read* the fare mode, not infer it from
              the absence of a badge. */}
          <Chip tone={data.fare_mode === 'FIXED' ? 'brand' : 'neutral'}>
            {labels.fareMode(data.fare_mode)}
          </Chip>
        </div>

        <Rows>
          <DetailRow label={t('orderDetail.pickup')}>{data.pickup_address || '—'}</DetailRow>
          <DetailRow label={t('orderDetail.dropoff')}>{data.dropoff_address || '—'}</DetailRow>
          <DetailRow label={t('orderDetail.fare')}>
            <Money value={data.estimated_total_hkd} />
            {Number(data.discount_percent) > 0 ? (
              <span className="dim">{t('orderDetail.discount', { percent: data.discount_percent })}</span>
            ) : null}
          </DetailRow>
          <DetailRow label={t('orderDetail.distance')}>{data.distance_km} km</DetailRow>
          <DetailRow label={t('orderDetail.radius')}>{data.broadcast_radius_km} km</DetailRow>
          <DetailRow label={t('orderDetail.driver')}>
            {data.driver_id ? (
              <Link className="mono" to={`/drivers/${data.driver_id}`}>
                {shortId(data.driver_id)}
              </Link>
            ) : (
              <span className="dim">{t('common.unassigned')}</span>
            )}
          </DetailRow>
          <DetailRow label={t('orderDetail.passenger')}>
            <span className="mono">{shortId(data.passenger_id)}</span>
            <span className="dim t-caption1">{t('orderDetail.passengerNote')}</span>
          </DetailRow>
          {data.cancellation_reason ? (
            <DetailRow label={t('orderDetail.cancelReason')}>{data.cancellation_reason}</DetailRow>
          ) : null}
        </Rows>
      </Card>

      <h2>{t('orderDetail.disputeTitle')}</h2>
      <Card className="card--pad">
        <p className="dim" style={{ marginTop: 0 }}>
          {t('orderDetail.disputeNote')}
        </p>
        <Rows>
          <DetailRow label={t('orderDetail.requirementsLabel')}>
            <RequirementChips requirements={data.requirements} t={t} />
          </DetailRow>
          <DetailRow label={t('orderDetail.paymentPreferenceLabel')}>
            <PaymentMethods methods={data.payment_preference} labels={labels} t={t} />
          </DetailRow>
          <DetailRow label={t('orderDetail.driverMethodsLabel')}>
            <PaymentMethods methods={data.driver_payment_methods} labels={labels} t={t} />
          </DetailRow>
          <DetailRow label={t('orderDetail.premiumDestinationLabel')}>
            {data.premium_destination ? (
              <>
                <span>{formatLocale === 'zh-HK'
                  ? data.premium_destination.name_zh
                  : data.premium_destination.name_en}</span>
                <span className="dim mono"> {data.premium_destination.code}</span>
              </>
            ) : (
              <span className="dim">{t('orderDetail.noneRecorded')}</span>
            )}
          </DetailRow>
          <DetailRow label={t('orderDetail.areaLabel')}>
            {data.pickup_area || data.destination_area ? (
              <span>
                {labels.area(data.pickup_area)} → {labels.area(data.destination_area)}
              </span>
            ) : (
              <span className="dim">{t('orderDetail.noneRecorded')}</span>
            )}
          </DetailRow>
          <DetailRow label={t('orderDetail.receiptLabel')}>
            {data.receipt_requested ? (
              <>
                <Chip tone="ok">{t('orderDetail.receiptRequested')}</Chip>
                {data.receipt_requested_at ? (
                  <span className="dim"> {formatTime(data.receipt_requested_at, formatLocale)}</span>
                ) : null}
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={() => {
                    setReceipt(null);
                    setReceiptError(null);
                    void endpoints.orders
                      .receipt(client, data.id)
                      .then(setReceipt)
                      .catch((e: unknown) => setReceiptError(e instanceof Error ? e.message : String(e)));
                  }}
                >
                  {t('orderDetail.viewReceipt')}
                </button>
              </>
            ) : (
              <span className="dim">{t('orderDetail.receiptNotRequested')}</span>
            )}
          </DetailRow>
        </Rows>
      </Card>

      {receipt || receiptError ? (
        <div style={{ marginTop: 16 }}>
          <h2>{t('orderDetail.receiptTitle')}</h2>
          {receipt ? (
            <Card className="card--pad">
              <div className="row-inline" style={{ marginBottom: 12 }}>
                <Money value={receipt.total_hkd} />
                <span className="dim">{formatTime(receipt.issued_at, formatLocale)}</span>
              </div>
              <pre className="receipt-text">{receipt.text}</pre>
            </Card>
          ) : null}
          {receiptError ? (
            <p className="dim" style={{ color: 'var(--danger, #b42318)' }}>
              {receiptError}
            </p>
          ) : null}
        </div>
      ) : null}

      {/*
        The timeline carries the whole diagnostic value of the page: the
        *deltas* answer "how long did it sit there", which the four raw
        timestamps do not. A step that never happened is rendered as missing
        rather than omitted, so the list keeps a fixed length and an absent step
        is visibly absent.
      */}
      <h2>{t('orderDetail.timelineTitle')}</h2>
      <Card>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">{t('orderDetail.colStep')}</th>
                <th scope="col">{t('orderDetail.colTime')}</th>
                <th scope="col" className="num">{t('orderDetail.colSince')}</th>
              </tr>
            </thead>
            <tbody>
              {data.timeline.map((step) => (
                <tr key={step.step}>
                  <td>{t('enum.orderTimeline.' + step.step)}</td>
                  <td>{step.at ? formatTime(step.at, formatLocale) : <span className="dim">{t('orderDetail.notHappened')}</span>}</td>
                  <td className="num">
                    {step.elapsed_seconds === null ? (
                      <span className="dim">—</span>
                    ) : (
                      formatDuration(step.elapsed_seconds, t)
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <h2>{t('orderDetail.snapshotTitle')}</h2>
      <Card className="card--pad">
        <p className="dim" style={{ marginTop: 0 }}>
          {t('orderDetail.snapshotNote', { version: data.tariff_version })}
        </p>
        {Object.keys(data.fare ?? {}).length === 0 ? (
          <div className="empty">{t('orderDetail.snapshotEmpty')}</div>
        ) : (
          <Rows>
            {Object.entries(data.fare).map(([key, value]) => (
              <DetailRow key={key} label={key}>
                <span className="mono">{formatFareValue(value)}</span>
              </DetailRow>
            ))}
          </Rows>
        )}
      </Card>

      <h2>{t('orderDetail.ledgerTitle')}</h2>
      <Card>
        {ledger.length === 0 ? (
          <div className="empty">
            {t('orderDetail.ledgerEmpty')}<span className="dim">{t('orderDetail.ledgerEmptyNote')}</span>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">{t('common.time')}</th>
                  <th scope="col">{t('orderDetail.colType')}</th>
                  <th scope="col" className="num">{t('common.amount')}</th>
                  <th scope="col" className="num">{t('common.balance')}</th>
                  <th scope="col">{t('orderDetail.colNote')}</th>
                </tr>
              </thead>
              <tbody>
                {ledger.map((entry) => (
                  <tr key={entry.id}>
                    <td>{formatTime(entry.created_at, formatLocale)}</td>
                    <td>
                      <Chip tone={labels.entryTone(entry.entry_type)}>{labels.entry(entry.entry_type)}</Chip>
                    </td>
                    <td className="num">
                      <Money value={entry.amount_hkd} sign />
                    </td>
                    <td className="num">
                      <Money value={entry.balance_after_hkd} sign />
                    </td>
                    <td>{entry.note || <span className="dim">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

/**
 * The requirement chips, with `animal` rendered as the detail it is.
 *
 * `null` is **not** "no requirements" and **not** "no pet": it means the
 * passenger was never asked, or asked for nothing. The distinction matters in a
 * dispute, so an empty record says so in words rather than rendering an empty
 * cell an operator would read as "confirmed: no pet".
 */
function RequirementChips({
  requirements,
  t,
}: {
  requirements: OrderRequirements | null;
  t: (key: string, options?: Record<string, unknown>) => string;
}) {
  const flags = (
    ['silent_ride', 'no_radio_music', 'no_smoke', 'no_perfume'] as const
  ).filter((key) => requirements?.[key] === true);

  const animal = requirements?.animal ?? null;
  if (flags.length === 0 && !animal) {
    return <span className="dim">{t('orderDetail.noRequirements')}</span>;
  }

  return (
    <>
      {flags.map((key) => (
        <Chip key={key} tone="brand">
          {t('enum.requirement.' + key)}
        </Chip>
      ))}
      {animal ? (
        <Chip tone="warn">
          {t('orderDetail.animalChip', {
            kind: animal.kind,
            size: formatAnimalSize(animal),
          })}
        </Chip>
      ) : null}
    </>
  );
}

/** `35×8` — centimetres and kilograms, or just the kind if neither was given. */
function formatAnimalSize(animal: NonNullable<OrderRequirements['animal']>): string {
  const height = animal.height_cm;
  const weight = animal.weight_kg;
  if (height === undefined && weight === undefined) return '';
  return `${height ?? '?'}×${weight ?? '?'}`;
}

/**
 * A list of server method codes as chips, or an explicit "nothing recorded".
 *
 * An empty list is not "cash only" — it is "the platform has no record", which
 * is what the operator has to say when a passenger claims they asked for
 * Octopus. Rendering it as a chip would invent a fact.
 */
function PaymentMethods({
  methods,
  labels,
  t,
}: {
  methods: string[];
  labels: { paymentMethod: (method: string) => string };
  t: (key: string, options?: Record<string, unknown>) => string;
}) {
  if (methods.length === 0) {
    return <span className="dim">{t('orderDetail.noneRecorded')}</span>;
  }
  return (
    <>
      {methods.map((method) => (
        <Chip key={method} tone="neutral">
          {labels.paymentMethod(method)}
        </Chip>
      ))}
    </>
  );
}

/**
 * A duration, in the unit an operator would say it in.
 *
 * Seconds only below a minute, because "0.4 minutes" is not a thing anyone
 * says, and hours only above an hour so a long wait reads as `2 小時 5 分`
 * rather than `125 分`.
 */
function formatDuration(
  seconds: number,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  if (seconds < 0) return '—';
  if (seconds < 60) return t('duration.seconds', { seconds });
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    const rest = seconds % 60;
    return rest === 0 ? t('duration.minutes', { minutes }) : t('duration.minutesSeconds', { minutes, seconds: rest });
  }
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes === 0
    ? t('duration.hoursOnly', { hours })
    : t('duration.hoursRestMinutes', { hours, minutes: restMinutes });
}

/**
 * The `fare_json` snapshot has no declared schema — it is whatever the tariff
 * wrote at the time. Primitives render as themselves; anything nested is shown
 * as compact JSON rather than `[object Object]`.
 */
function formatFareValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return '—';
  }
}
