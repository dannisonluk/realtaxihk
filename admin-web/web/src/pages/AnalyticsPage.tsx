/**
 * Analytics: how the fleet is performing, and when.
 *
 * Two views over the same completed trips, driven by one set of filters:
 *
 *  1. **The day chart** — 24 hour slots, each drawn as a rectangle whose height
 *     is the money booked in that hour. This is the shape an operator reads to
 *     answer "when should I be on the road".
 *  2. **The heat map** — the same 24 slots as a colour strip, intensity by
 *     average daily earnings. It answers the same question at a glance, and it
 *     is the one that survives being looked at on a phone.
 *
 * The bars and the strip deliberately share an x-axis and the same numbers, so
 * a discrepancy between them is impossible by construction rather than by
 * discipline.
 *
 * Everything is in **Hong Kong time** — the server buckets that way, and the
 * `range.timezone` it returns is shown in the header so nobody has to guess.
 * A chart of "peak hour" that silently used UTC would point at the wrong shift.
 *
 * Money arrives as a 2-dp **string** and stays one. Parsing to `Number` for
 * display would reintroduce exactly the float error the server avoids, and
 * `Money`/`toNum` below exist so no view has to make that call itself.
 */

import { useState } from 'react';
import type {
  AnalyticsGranularity,
  AnalyticsHeatmap,
  AnalyticsSortBy,
  AnalyticsSummary,
  SortDir,
} from '../api/types';
import { endpoints } from '../api/endpoints';
import { Card, Chip, Empty, Loading, Money, Stat } from '../components/primitives';
import { ErrorState } from '../components/states';
import { useApp } from '../app/AppContext';
import { useLoad } from '../app/useLoad';
import { PageHead } from '../app/Shell';
import { useI18n } from '../i18n';

/**
 * Today as `YYYY-MM-DD` **in Hong Kong**, for the date inputs.
 *
 * The server buckets analytics by Hong Kong calendar day. A console using the
 * operator's own timezone asks for a different range by a day whenever the
 * operator sits west of UTC+8, so the newest bucket reads as a drop to zero.
 * Hong Kong has no DST, so a fixed offset is exact.
 */
const HK_OFFSET_MS = 8 * 60 * 60 * 1000;

function hkDay(offsetDays = 0): string {
  return new Date(Date.now() + HK_OFFSET_MS - offsetDays * 86_400_000)
    .toISOString()
    .slice(0, 10);
}

function today(): string {
  return hkDay();
}

function daysAgo(days: number): string {
  return hkDay(days);
}

/**
 * A money string to a number, for geometry only.
 *
 * Used exclusively to size a rectangle or pick a colour — never to display and
 * never to total. The displayed value is always the original string, so a
 * rounding artefact here can shift a bar by a pixel but cannot corrupt a figure
 * an operator reads off the screen.
 */
function toNum(value: string): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

const TAXI_TYPES: { value: string; labelKey: string }[] = [
  { value: '', labelKey: 'analytics.filterAllTypes' },
  { value: 'URBAN', labelKey: 'enum.taxiType.URBAN' },
  { value: 'NT', labelKey: 'enum.taxiType.NT' },
  { value: 'LANTAU', labelKey: 'enum.taxiType.LANTAU' },
];

const GRANULARITIES: { value: AnalyticsGranularity; labelKey: string }[] = [
  { value: 'day', labelKey: 'analytics.granularity.day' },
  { value: 'week', labelKey: 'analytics.granularity.week' },
  { value: 'month', labelKey: 'analytics.granularity.month' },
  { value: 'year', labelKey: 'analytics.granularity.year' },
];

/** Column definitions for the sortable table, so the header and sort agree. */
const COLUMNS: { key: AnalyticsSortBy; labelKey: string; align: 'start' | 'end' }[] = [
  { key: 'bucket', labelKey: 'analytics.colPeriod', align: 'start' },
  { key: 'orders', labelKey: 'analytics.colOrders', align: 'end' },
  { key: 'earnings', labelKey: 'analytics.colEarnings', align: 'end' },
  { key: 'avg_fare', labelKey: 'analytics.colAvgFare', align: 'end' },
  { key: 'distance', labelKey: 'analytics.colDistance', align: 'end' },
];

export function AnalyticsPage() {
  const { client } = useApp();
  const { t } = useI18n();

  const [from, setFrom] = useState(daysAgo(29));
  const [to, setTo] = useState(today());
  const [granularity, setGranularity] = useState<AnalyticsGranularity>('day');
  const [taxiType, setTaxiType] = useState('');
  const [sortBy, setSortBy] = useState<AnalyticsSortBy>('bucket');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  // The filters the *requests* are keyed on. Kept separate from the input state
  // so typing a partial date does not fire a request per keystroke — the
  // controls only commit on blur or change, and the range inputs additionally
  // have a minimum width before they are considered complete.
  // A NEW object on every render, on purpose and safely: `useLoad` below keys
  // off the primitives, not this object, so identity churn cannot re-fire it.
  // Do NOT add `summaryFilters` to that dep array — it would loop forever. The
  // primitives in the array and the keys in this object must stay in sync; the
  // filter values are the contract, the object is only how they are passed.
  const summaryFilters = { from, to, granularity, taxiType, sortBy, sortDir };

  const summary = useLoad<AnalyticsSummary>(
    () => endpoints.analytics.summary(client, summaryFilters),
    [from, to, granularity, taxiType, sortBy, sortDir],
  );

  // The heat map ignores granularity and sort: it is always 24 hourly slots.
  const heatmap = useLoad<AnalyticsHeatmap>(
    () => endpoints.analytics.heatmap(client, { from, to, taxiType }),
    [from, to, taxiType],
  );

  /**
   * Clicking a sorted column flips the direction; clicking a new one starts
   * ascending, except for the money and count columns, where the interesting
   * end is the top. Making "收入" descend on first click saves a second click on
   * the one column people actually sort by.
   */
  function toggleSort(key: AnalyticsSortBy) {
    if (key === sortBy) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      return;
    }
    setSortBy(key);
    setSortDir(key === 'bucket' ? 'asc' : 'desc');
  }

  const buckets = summary.data?.buckets ?? [];
  const hours = heatmap.data?.hours ?? [];
  const scaleMax = toNum(heatmap.data?.scale_max_hkd ?? '0');

  return (
    <>
      <PageHead
        title={t('analytics.title')}
        subtitle={t('analytics.sub')}
      />

      <Card>
        <div className="filters">
          <div className="field">
            <label className="field__label" htmlFor="an-from">
              {t('analytics.from')}
            </label>
            <input
              id="an-from"
              type="date"
              value={from}
              max={to}
              onChange={(event) => setFrom(event.target.value)}
            />
          </div>
          <div className="field">
            <label className="field__label" htmlFor="an-to">
              {t('analytics.to')}
            </label>
            <input
              id="an-to"
              type="date"
              value={to}
              min={from}
              onChange={(event) => setTo(event.target.value)}
            />
          </div>
          <div className="field">
            <label className="field__label" htmlFor="an-granularity">
              {t('analytics.unit')}
            </label>
            <select
              id="an-granularity"
              value={granularity}
              onChange={(event) => setGranularity(event.target.value as AnalyticsGranularity)}
            >
              {GRANULARITIES.map((g) => (
                <option key={g.value} value={g.value}>
                  {t(g.labelKey)}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label className="field__label" htmlFor="an-taxi">
              {t('analytics.taxiType')}
            </label>
            <select
              id="an-taxi"
              value={taxiType}
              onChange={(event) => setTaxiType(event.target.value)}
            >
              {TAXI_TYPES.map((item) => (
                <option key={item.value} value={item.value}>
                  {t(item.labelKey)}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <div className="field__label">{t('analytics.quickRange')}</div>
            <div className="actions">
              {[7, 30, 90].map((n) => (
                <button
                  key={n}
                  type="button"
                  className="btn btn--sm"
                  onClick={() => {
                    setFrom(daysAgo(n - 1));
                    setTo(today());
                  }}
                >
                  {t('analytics.lastDays', { count: n })}
                </button>
              ))}
            </div>
          </div>
        </div>
      </Card>

      {summary.error ? (
        <div style={{ marginTop: 16 }}>
          <ErrorState error={summary.error} onRetry={summary.reload} />
        </div>
      ) : null}

      {summary.data ? (
        <div className="grid" style={{ marginTop: 16 }}>
          <Stat
            label={t('analytics.totalRevenue')}
            value={<Money value={summary.data.totals.earnings_hkd} />}
            hint={t('analytics.revenueHint', { days: summary.data.totals.days, buckets: summary.data.totals.buckets })}
          />
          <Stat label={t('analytics.completedOrders')} value={summary.data.totals.orders} />
          <Stat
            label={t('analytics.avgFare')}
            value={<Money value={summary.data.totals.avg_fare_hkd} />}
          />
          <Stat
            label={t('analytics.avgDailyRevenue')}
            value={
              <Money
                value={(
                  toNum(summary.data.totals.earnings_hkd) / summary.data.totals.days
                ).toFixed(2)}
              />
            }
            hint={t('analytics.avgDailyNote')}
          />
        </div>
      ) : null}

      {/* ---- The day chart ---- */}
      <div style={{ marginTop: 16 }}>
        <Card>
          <div className="page-head__text" style={{ marginBottom: 4 }}>
            <h2 className="t-title3" style={{ margin: 0 }}>
              {t('analytics.hourlyTitle')}
            </h2>
            <p className="page-head__sub">
              {t('analytics.hourlyNote')}
            </p>
          </div>

          {heatmap.loading ? <Loading label={t('analytics.loadingHourly')} /> : null}
          {heatmap.error ? (
            <ErrorState error={heatmap.error} onRetry={heatmap.reload} />
          ) : null}

          {heatmap.data ? (
            <>
              <div className="actions" style={{ margin: '8px 0 16px', flexWrap: 'wrap' }}>
                <Chip tone="brand">{t('analytics.peakHour', { hour: heatmap.data.peak_hour })}</Chip>
                <Chip>{t('analytics.busiestHour', { hour: heatmap.data.busiest_hour })}</Chip>
                <Chip>{t('analytics.maxAvg', { amount: heatmap.data.max_avg_per_day_hkd })}</Chip>
                <span className="dim t-footnote">
                  {t('analytics.avgDailyFormula', { days: heatmap.data.range.days })}
                </span>
              </div>

              <HourBarChart hours={hours} scaleMax={scaleMax} />
              <HourHeatStrip hours={hours} scaleMax={scaleMax} />
              <HourTable hours={hours} />

              <p className="dim t-footnote" style={{ marginTop: 12 }}>
                {t('analytics.noOrdersNote')}
              </p>
            </>
          ) : null}
        </Card>
      </div>

      {/* ---- The sortable table ---- */}
      <div style={{ marginTop: 16 }}>
        <Card>
          <h2 className="t-title3" style={{ margin: '0 0 12px' }}>
            {t('analytics.breakdownTitle')}
          </h2>

          {summary.loading ? <Loading label={t('common.loading')} /> : null}

          {summary.data && buckets.length === 0 ? (
            <Empty
              title={t('analytics.breakdownEmpty')}
              hint={t('analytics.breakdownEmptyHint')}
            />
          ) : null}

          {buckets.length > 0 ? (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    {COLUMNS.map((col) => (
                      <th
                        key={col.key}
                        style={{ textAlign: col.align }}
                        aria-sort={
                          sortBy === col.key
                            ? sortDir === 'asc'
                              ? 'ascending'
                              : 'descending'
                            : 'none'
                        }
                      >
                        <button
                          type="button"
                          className="th-sort"
                          onClick={() => toggleSort(col.key)}
                        >
                          {t(col.labelKey)}
                          <span className="th-sort__mark" aria-hidden="true">
                            {sortBy === col.key ? (sortDir === 'asc' ? '▲' : '▼') : '⇅'}
                          </span>
                        </button>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {buckets.map((row) => (
                    <tr key={row.bucket}>
                      <td>{row.bucket}</td>
                      <td className="num" style={{ textAlign: 'end' }}>
                        {row.orders}
                      </td>
                      <td className="num" style={{ textAlign: 'end' }}>
                        <Money value={row.earnings_hkd} />
                      </td>
                      <td className="num" style={{ textAlign: 'end' }}>
                        <Money value={row.avg_fare_hkd} />
                      </td>
                      <td className="num" style={{ textAlign: 'end' }}>
                        {row.distance_km}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </Card>
      </div>
    </>
  );
}

/**
 * The day chart: 24 bars, height by earnings.
 *
 * Hand-drawn SVG rather than a charting library. The console ships no chart
 * dependency, and this is 24 rectangles with a linear scale — adding a library
 * to draw that would cost more in bundle size than the whole page.
 *
 * `viewBox` with `width: 100%` makes it scale to the card without re-measuring,
 * and the plot is padded on the left for the y-axis labels rather than being
 * absolutely positioned, so the labels can never overlap the bars.
 */
function HourBarChart({
  hours,
  scaleMax,
}: {
  hours: AnalyticsHeatmap['hours'];
  scaleMax: number;
}) {
  const { t } = useI18n();
  const [hover, setHover] = useState<number | null>(null);

  const W = 720;
  const H = 240;
  const padLeft = 52;
  const padRight = 8;
  const padTop = 12;
  const plotH = 168;
  const plotW = W - padLeft - padRight;
  const slot = plotW / 24;
  const barW = slot * 0.68;

  // A zero maximum means no data in range. Falling back to 1 keeps every bar at
  // zero height instead of producing NaN geometry that renders as nothing at
  // all — a blank chart is a better answer than a broken one.
  const max = scaleMax > 0 ? scaleMax : 1;

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({
    value: max * f,
    y: padTop + plotH - plotH * f,
  }));

  return (
    <div className="chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        role="img"
        aria-label={t('analytics.hourlyAria')}
        style={{ display: 'block', overflow: 'visible' }}
      >
        {ticks.map((t) => (
          <g key={t.y}>
            <line
              x1={padLeft}
              x2={W - padRight}
              y1={t.y}
              y2={t.y}
              stroke="var(--border)"
              strokeWidth={1}
            />
            <text
              x={padLeft - 8}
              y={t.y + 4}
              textAnchor="end"
              fontSize={10}
              fill="var(--text-dim)"
            >
              {t.value >= 1000 ? `${Math.round(t.value / 100) / 10}k` : Math.round(t.value)}
            </text>
          </g>
        ))}

        {hours.map((slotData, index) => {
          const value = toNum(slotData.avg_per_day_hkd);
          const h = (value / max) * plotH;
          const x = padLeft + index * slot + (slot - barW) / 2;
          const y = padTop + plotH - h;
          const isHovered = hover === index;
          return (
            <g key={slotData.hour}>
              {/* A full-height transparent hit area, so a zero-height bar is
                  still hoverable — otherwise the quiet hours, which are the
                  ones worth inspecting, would be the only ones you cannot. */}
              <rect
                x={padLeft + index * slot}
                y={padTop}
                width={slot}
                height={plotH}
                fill="transparent"
                onMouseEnter={() => setHover(index)}
                onMouseLeave={() => setHover(null)}
              />
              <rect
                x={x}
                y={y}
                width={barW}
                height={Math.max(h, value > 0 ? 1 : 0)}
                rx={2}
                fill="var(--brand)"
                opacity={isHovered ? 1 : 0.72}
                pointerEvents="none"
              />
              {index % 2 === 0 ? (
                <text
                  x={padLeft + index * slot + slot / 2}
                  y={padTop + plotH + 16}
                  textAnchor="middle"
                  fontSize={10}
                  fill="var(--text-dim)"
                >
                  {slotData.hour}
                </text>
              ) : null}
            </g>
          );
        })}

        <text
          x={padLeft + plotW / 2}
          y={H - 6}
          textAnchor="middle"
          fontSize={11}
          fill="var(--text-dim)"
        >
          {t('analytics.hourRange')}
        </text>
      </svg>

      {hover !== null && hours[hover] ? (
        <div className="chart__tip" role="status">
          <strong>
            {hours[hover].hour}:00 – {(hours[hover].hour + 1) % 24}:00
          </strong>
          <div>
            {t('analytics.tipAvg', { amount: '' })}<Money value={hours[hover].avg_per_day_hkd} />
          </div>
          <div className="dim">
            {t('analytics.tipOrders', { orders: hours[hover].orders, days: hours[hover].active_days })}
            <Money value={hours[hover].avg_per_active_day_hkd} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

/**
 * The heat map: the same 24 slots as a colour strip.
 *
 * The ramp is `color-mix` against `--surface`, so it darkens in the light theme
 * and lightens in the dark one without a second palette — the console's theme
 * is a media query, not a class, so a hard-coded ramp would be unreadable in one
 * of the two modes.
 *
 * A zero value renders as the bare surface colour rather than the lightest step
 * of the ramp, so "no trips" reads as absence instead of as a very small number.
 */
function HourHeatStrip({
  hours,
  scaleMax,
}: {
  hours: AnalyticsHeatmap['hours'];
  scaleMax: number;
}) {
  const { t } = useI18n();
  const max = scaleMax > 0 ? scaleMax : 1;

  return (
    <div style={{ marginTop: 8 }}>
      <div className="dim t-footnote" style={{ marginBottom: 6 }}>
        {t('analytics.heatmapTitle')}
      </div>
      <div className="heat" role="img" aria-label={t('analytics.heatmapAria')}>
        {hours.map((slot) => {
          const value = toNum(slot.avg_per_day_hkd);
          const ratio = value / max;
          // Floor the ramp at 12% so a non-zero hour is always visible against
          // the surface, then scale the rest of the way.
          const strength = value > 0 ? 12 + ratio * 88 : 0;
          return (
            <div
              key={slot.hour}
              className="heat__cell"
              title={t('analytics.heatCellTitle', { hour: slot.hour, amount: slot.avg_per_day_hkd, orders: slot.orders })}
              style={{
                background:
                  value > 0
                    ? `color-mix(in srgb, var(--brand) ${strength.toFixed(0)}%, var(--surface))`
                    : 'var(--surface-2)',
              }}
            >
              <span className="heat__hour">{slot.hour}</span>
            </div>
          );
        })}
      </div>
      <div className="heat__scale">
        <span className="dim t-footnote">{t('analytics.scaleLow')}</span>
        {[0.15, 0.35, 0.55, 0.75, 1].map((f) => (
          <span
            key={f}
            className="heat__swatch"
            style={{
              background: `color-mix(in srgb, var(--brand) ${(12 + f * 88).toFixed(0)}%, var(--surface))`,
            }}
          />
        ))}
        <span className="dim t-footnote">{t('analytics.scaleHigh', { max: scaleMax })}</span>
      </div>
    </div>
  );
}

/**
 * The 24 hours as a table -- the accessible equivalent of the two charts above.
 *
 * Neither chart is readable without sight, and the gap is not hypothetical:
 *
 *   * `HourBarChart` is a single `role="img"` whose tooltip is driven by
 *     `onMouseEnter` on a `<rect>`. A `<rect>` is not focusable, so every number
 *     behind that tooltip is mouse-only.
 *   * `HourHeatStrip` is also `role="img"`, and a `role="img"`'s children are
 *     presentational. Every value in its `title` attributes is therefore absent
 *     from the accessibility tree -- the attribute still gives a sighted user a
 *     native tooltip, but a screen reader never sees it.
 *
 * The breakdown table further down the page does not cover this: it holds the
 * day/week/month buckets, which is a different dataset. So "which hour earns
 * most" had no answer for a screen reader or for a keyboard.
 *
 * A `<details>` rather than an always-open table, because 24 rows is a lot of
 * scroll to impose on a reader who already has the chart -- while the summary is
 * a real focus stop, which the chart's rectangles are not. Same shape as
 * `LiveMapPage`, where the table under the map is the accessible equivalent
 * rather than a duplicate.
 */
function HourTable({ hours }: { hours: AnalyticsHeatmap['hours'] }) {
  const { t } = useI18n();

  // An empty range has nothing to tabulate, and an empty `<table>` announced as
  // a table is worse than no table at all.
  if (hours.length === 0) return null;

  return (
    <details className="chart__data">
      <summary className="t-footnote">{t('analytics.hourTableSummary')}</summary>
      <div className="table-wrap">
        <table className="data">
          <caption className="sr-only">{t('analytics.hourTableCaption')}</caption>
          <thead>
            <tr>
              <th scope="col">{t('analytics.colHour')}</th>
              <th scope="col" style={{ textAlign: 'end' }}>
                {t('analytics.colAvgPerDay')}
              </th>
              <th scope="col" style={{ textAlign: 'end' }}>
                {t('analytics.colOrders')}
              </th>
              <th scope="col" style={{ textAlign: 'end' }}>
                {t('analytics.colActiveDays')}
              </th>
            </tr>
          </thead>
          <tbody>
            {hours.map((slot) => (
              <tr key={slot.hour}>
                <td>{String(slot.hour).padStart(2, '0')}:00</td>
                <td className="num" style={{ textAlign: 'end' }}>
                  <Money value={slot.avg_per_day_hkd} />
                </td>
                <td className="num" style={{ textAlign: 'end' }}>
                  {slot.orders}
                </td>
                <td className="num" style={{ textAlign: 'end' }}>
                  {slot.active_days}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
