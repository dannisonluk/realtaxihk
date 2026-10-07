/**
 * The analytics page, driven against a stubbed transport.
 *
 * What is worth testing here is the wiring the server cannot check: that the
 * page asks for the right thing when a filter changes, that the heat map always
 * draws 24 slots, and that money is rendered as the exact string the server
 * sent rather than a re-formatted number.
 *
 * The money assertion is the one with teeth. The server is careful to keep
 * amounts as 2-dp strings precisely because a float cannot hold cents; a view
 * that parsed them back to `Number` for display would undo that in one line,
 * and would do it invisibly — `"0.10"` and `0.1` look identical until the
 * arithmetic stops matching the ledger.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StrictMode, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { AppProvider } from '../app/AppContext';
import { i18n } from '../i18n';
import { AnalyticsPage } from './AnalyticsPage';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Read a string out of the active locale's resource.
 *
 * The table headers are translated, so the sort test has to ask for the header
 * *by key* — a Chinese literal would pin the file to one locale and would also
 * stop noticing a copy change.
 */
function text(key: string): string {
  const target = i18n.resolvedLanguage ?? 'zh-Hant';
  const read = (source: unknown) => {
    let cursor: unknown = source;
    for (const part of key.split('.')) {
      if (typeof cursor !== 'object' || cursor === null) return undefined;
      cursor = (cursor as Record<string, unknown>)[part];
    }
    return typeof cursor === 'string' ? cursor : undefined;
  };
  return (
    read(i18n.getResourceBundle(target, 'translation')) ??
    read(i18n.getResourceBundle('zh-Hant', 'translation')) ??
    key
  );
}

/** A summary payload with two buckets and a deliberately awkward money value. */
const SUMMARY = {
  range: {
    from: '2026-09-01',
    to: '2026-09-02',
    granularity: 'day',
    taxi_type: null,
    timezone: 'Asia/Hong_Kong',
  },
  totals: {
    orders: 3,
    earnings_hkd: '1234.50',
    avg_fare_hkd: '411.50',
    distance_km: '42.00',
    buckets: 2,
    days: 2,
  },
  buckets: [
    {
      bucket: '2026-09-01',
      orders: 1,
      earnings_hkd: '0.10',
      avg_fare_hkd: '0.10',
      distance_km: '1.00',
    },
    {
      bucket: '2026-09-02',
      orders: 2,
      earnings_hkd: '1234.40',
      avg_fare_hkd: '617.20',
      distance_km: '41.00',
    },
  ],
  sort: { by: 'bucket', dir: 'asc' },
};

/** 24 hour slots, with hour 19 carrying the peak. */
const HEATMAP = {
  range: {
    from: '2026-09-01',
    to: '2026-09-02',
    taxi_type: null,
    timezone: 'Asia/Hong_Kong',
    days: 2,
  },
  hours: Array.from({ length: 24 }, (_, hour) => ({
    hour,
    avg_per_day_hkd: hour === 19 ? '200.00' : '0.00',
    avg_per_active_day_hkd: hour === 19 ? '400.00' : '0.00',
    earnings_hkd: hour === 19 ? '400.00' : '0.00',
    orders: hour === 19 ? 2 : 0,
    active_days: hour === 19 ? 1 : 0,
    avg_orders_per_day: hour === 19 ? '1.00' : '0.00',
  })),
  max_avg_per_day_hkd: '200.00',
  peak_hour: 19,
  busiest_hour: 19,
  scale_max_hkd: '200.00',
};

/** Operations analytics: a different shape from earnings, so the page’s loader must not mix them up. */
const OPERATIONS = {
  range: {
    from: '2026-09-01',
    to: '2026-09-02',
    taxi_type: null,
    timezone: 'Asia/Hong_Kong',
    days: 2,
  },
  funnel: {
    created: 5,
    accepted: 3,
    completed: 1,
    interrupted: 1,
    cancelled: 1,
    active: 2,
  },
  cancellations: {
    passenger: 1,
    driver: 1,
    timeout: 1,
    unattributed: 1,
  },
  latency: {
    acceptance_avg_s: '90.00',
    arrival_avg_s: '600.00',
  },
  acceptance_rate: '60.00',
  cancellation_rate: '20.00',
};

/** Record every request the page makes, so the filters can be asserted. */
function stubTransport() {
  const calls: string[] = [];
  vi.stubGlobal('fetch', (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    calls.push(url);
    const body = url.includes('/heatmap')
      ? HEATMAP
      : url.includes('/operations')
        ? OPERATIONS
        : SUMMARY;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  });
  return calls;
}

async function renderAndSettle(root: Root) {
  await act(async () => {
    root.render(
      <StrictMode>
        <AppProvider onSignedOut={() => {}}>
          <AnalyticsPage />
        </AppProvider>
      </StrictMode>,
    );
  });
  // Three turns: one for the summary request, one for operations, one for the
  // heat map. The page loads them sequentially, matching `useLoad`.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 20));
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 20));
  });
}

describe('the analytics page', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('asks for the summary, operations and hourly heat map', async () => {
    const calls = stubTransport();
    await renderAndSettle(root);

    expect(calls.some((u) => u.includes('/api/v1/admin/analytics') && !u.includes('heatmap') && !u.includes('operations'))).toBe(
      true,
    );
    expect(calls.some((u) => u.includes('/api/v1/admin/analytics/heatmap'))).toBe(true);
    expect(calls.some((u) => u.includes('/api/v1/admin/analytics/operations'))).toBe(true);
  });

  it('renders the operations funnel and cancellation attribution', async () => {
    stubTransport();
    await renderAndSettle(root);

    expect(container.textContent).toContain(text('analytics.operationsTitle'));
    expect(container.textContent).toContain('5');
    expect(container.textContent).toContain('60.00');
    expect(container.textContent).toContain('90.00');
    expect(container.textContent).toContain('600.00');
    expect(container.textContent).toContain(text('analytics.cancelPassenger'));
    expect(container.textContent).toContain(text('analytics.cancelUnattributed'));
  });

  /**
   * The rows of the *breakdown* table, not the charts' 24-hour table.
   *
   * There are two `<table class="data">`s on this page. A bare
   * `querySelectorAll('tbody tr')` silently started returning 26 rows instead of
   * 2 the moment the accessible hour table was added — which is why this is a
   * named helper rather than an inline query, and why the tests below are
   * scoped rather than weakened.
   */
  const breakdownRows = (host: HTMLElement) =>
    [...host.querySelectorAll('tbody tr')].filter((row) => !row.closest('details'));

  it('renders one table row per bucket', async () => {
    stubTransport();
    await renderAndSettle(root);

    const rows = breakdownRows(container);
    expect(rows).toHaveLength(2);
    expect(rows[0]?.textContent).toContain('2026-09-01');
    expect(rows[1]?.textContent).toContain('2026-09-02');
  });

  it('renders money with its cents intact', async () => {
    stubTransport();
    await renderAndSettle(root);

    // Scoped to the breakdown table. The hour table legitimately renders
    // `HK$0.00` for the hours with no trips, so asserting over the whole page
    // would report that as the very bug this test exists to catch.
    const text = breakdownRows(container)
      .map((row) => row.textContent ?? '')
      .join(' ');

    // The cents must survive. `"0.10"` rendered as `0.1` would be the visible
    // symptom of a view that had parsed the server's string back to a number.
    expect(text).toContain('HK$0.10');
    // And grouping is the console's own convention (`formatMoney`).
    expect(text).toContain('HK$1,234.40');
    // A sub-cent-looking value must not be rounded away to nothing.
    expect(text).not.toContain('HK$0.00');
  });

  it('offers the 24 hours as a table for readers who cannot use the charts', async () => {
    stubTransport();
    await renderAndSettle(root);

    // Both charts are `role="img"`: the bar chart's tooltip is driven by
    // `onMouseEnter` on a non-focusable `<rect>`, and a `role="img"`'s children
    // are presentational, so the heat strip's `title` attributes never reach the
    // accessibility tree. This table is the only accessible path to the numbers.
    const details = container.querySelector('details.chart__data');
    expect(details).toBeTruthy();
    expect(details?.querySelector('summary')?.textContent).toBe(
      text('analytics.hourTableSummary'),
    );
    expect(details?.querySelector('caption')?.textContent).toBe(
      text('analytics.hourTableCaption'),
    );
    expect(details?.querySelectorAll('thead th')).toHaveLength(4);

    const rows = [...(details?.querySelectorAll('tbody tr') ?? [])];
    expect(rows).toHaveLength(24);

    // The peak hour from the fixture, cell by cell — the point being that the
    // number behind the mouse-only tooltip is now readable.
    const peak = rows.find((row) => row.textContent?.startsWith('19:00'));
    expect([...(peak?.querySelectorAll('td') ?? [])].map((cell) => cell.textContent)).toEqual([
      '19:00',
      'HK$200.00',
      '2',
      '1',
    ]);

    // And the quiet hours are rows too rather than being omitted: "0" is data,
    // which is the same reason the chart draws a bar for every hour.
    const quiet = rows.find((row) => row.textContent?.startsWith('03:00'));
    expect(quiet?.querySelectorAll('td')[1]?.textContent).toBe('HK$0.00');
  });

  it('always draws 24 heat map cells', async () => {
    stubTransport();
    await renderAndSettle(root);

    const cells = container.querySelectorAll('.heat__cell');
    expect(cells).toHaveLength(24);
  });

  it('draws a bar for every hour, including the empty ones', async () => {
    stubTransport();
    await renderAndSettle(root);

    // The chart is an SVG; every hour contributes a hit area plus a bar.
    const bars = container.querySelectorAll('svg rect');
    // 24 hit areas + 24 bars, plus the gridline rects are lines not rects.
    expect(bars.length).toBeGreaterThanOrEqual(48);
  });

  it('reports the peak hour from the payload', async () => {
    stubTransport();
    await renderAndSettle(root);
    expect(container.textContent).toContain('19:00');
  });

  it('requests a descending sort when a money column is clicked', async () => {
    const calls = stubTransport();
    await renderAndSettle(root);
    const before = calls.length;

    // The 收入 column header. Clicking it should sort by earnings, and — for a
    // value column — start at the interesting end rather than ascending.
    const headers = [...container.querySelectorAll('.th-sort')];
    const earnings = headers.find((h) =>
      h.textContent?.includes(text('analytics.colEarnings')),
    );
    expect(earnings).toBeTruthy();

    await act(async () => {
      earnings?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    const fresh = calls.slice(before);
    expect(fresh.length).toBeGreaterThan(0);
    expect(fresh.some((u) => u.includes('sort_by=earnings') && u.includes('sort_dir=desc'))).toBe(
      true,
    );
  });

  it('sends the taxi filter when one is chosen', async () => {
    const calls = stubTransport();
    await renderAndSettle(root);
    const before = calls.length;

    const select = container.querySelector('#an-taxi') as HTMLSelectElement | null;
    expect(select).toBeTruthy();
    await act(async () => {
      if (select) {
        select.value = 'NT';
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    const fresh = calls.slice(before);
    expect(fresh.some((u) => u.includes('taxi_type=NT'))).toBe(true);
  });

  it('sends the granularity when it changes', async () => {
    const calls = stubTransport();
    await renderAndSettle(root);
    const before = calls.length;

    const select = container.querySelector('#an-granularity') as HTMLSelectElement | null;
    await act(async () => {
      if (select) {
        select.value = 'month';
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    expect(calls.slice(before).some((u) => u.includes('granularity=month'))).toBe(true);
  });

  it('marks the sorted column for assistive technology', async () => {
    stubTransport();
    await renderAndSettle(root);

    const sorted = container.querySelector('[aria-sort="ascending"]');
    expect(sorted).toBeTruthy();
    expect(sorted?.textContent).toContain(text('analytics.colPeriod'));
  });
});
