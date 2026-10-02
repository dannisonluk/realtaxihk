/**
 * The live map page, driven against a stubbed transport and a stubbed Leaflet.
 *
 * What is worth testing here is what neither the server nor `tsc` can check:
 *
 *  * **the coordinate order handed to Leaflet.** The server sends `lat`/`lng`
 *    as named fields; Leaflet takes a positional `[lat, lng]`. Transposing them
 *    is the classic bug in every map integration, it type-checks perfectly
 *    (`[number, number]` either way), and the symptom is markers in the South
 *    China Sea. It is asserted directly, and by mutation: swapping the two
 *    arguments fails this test and nothing else.
 *  * **the row order.** Staleness-descending is a deliberate choice, and a
 *    recency sort is the reflex — so it needs a test that fails when someone
 *    "fixes" it.
 *  * **the request parameters.** `include_offline` is the server's own rule for
 *    what the map shows; a client-side filter would drift from it.
 *
 * Leaflet is mocked rather than rendered because jsdom has no layout engine, so
 * a real `L.map()` builds a zero-height canvas and draws nothing. Asserting
 * against a mock of the *calls* is the honest test of the integration; the
 * visual result is what `admin-web/tool/audit_layout.mjs` covers in a real
 * browser.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StrictMode, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';

/**
 * `vi.hoisted`, not a plain `const`.
 *
 * `vi.mock` is hoisted above the imports, so a factory that closes over a
 * module-scope `const` runs while that binding is still in its temporal dead
 * zone. `vi.hoisted` moves the declaration up with it.
 */
const { circleMarkerCalls, mapCalls } = vi.hoisted(() => ({
  circleMarkerCalls: [] as Array<{ latlng: [number, number]; className: string }>,
  mapCalls: [] as Array<[number, number]>,
}));

vi.mock('leaflet', () => {
  const layerGroup = { addTo: () => layerGroup, clearLayers: () => undefined };
  const marker = {
    bindTooltip: () => marker,
    on: () => marker,
    addTo: () => marker,
  };
  const leaflet = {
    map: (host: HTMLElement, options: { center: [number, number] }) => {
      mapCalls.push(options.center);
      void host;
      return { remove: () => undefined, addLayer: () => undefined };
    },
    tileLayer: () => ({ addTo: () => undefined }),
    layerGroup: () => layerGroup,
    circleMarker: (latlng: [number, number], options: { className?: string }) => {
      circleMarkerCalls.push({ latlng, className: options.className ?? '' });
      return marker;
    },
  };
  return { default: leaflet, ...leaflet };
});

import { AppProvider } from '../app/AppContext';
import { i18n } from '../i18n';
import { LiveMapPage } from './LiveMapPage';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/** Read a string out of the active locale's resource, by key. */
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

/** A driver with a fresh fix, running a trip. */
const RUNNING = {
  driver_profile_id: 'aaaaaaaa-0000-4000-8000-000000000001',
  status: 'ACTIVE',
  taxi_type: 'URBAN',
  vehicle_reg_mark: 'AB1234',
  is_online: true,
  last_location_at: new Date(Date.now() - 2_000).toISOString(),
  lat: 22.3193,
  lng: 114.1694,
  order_id: 'bbbbbbbb-0000-4000-8000-000000000001',
  order_status: 'IN_TRIP',
};

/** Online, unassigned, and reporting. The common case. */
const IDLE = {
  driver_profile_id: 'aaaaaaaa-0000-4000-8000-000000000002',
  status: 'ACTIVE',
  taxi_type: 'NT',
  vehicle_reg_mark: 'CD5678',
  is_online: true,
  last_location_at: new Date(Date.now() - 5_000).toISOString(),
  lat: 22.2783,
  lng: 114.1747,
  order_id: null,
  order_status: null,
};

/** Stopped reporting well past `STALE_AFTER_MS` (three poll intervals). */
const STALE = {
  driver_profile_id: 'aaaaaaaa-0000-4000-8000-000000000003',
  status: 'ACTIVE',
  taxi_type: 'URBAN',
  vehicle_reg_mark: 'EF9012',
  is_online: true,
  last_location_at: '2020-01-01T00:00:00.000Z',
  lat: 22.4501,
  lng: 114.0301,
  order_id: null,
  order_status: null,
};

function snapshot(drivers: unknown[], truncated = false) {
  return {
    generated_at: new Date().toISOString(),
    drivers,
    truncated,
  };
}

/** Serve `payload` and record every request URL. */
function stubTransport(payload: unknown) {
  const calls: string[] = [];
  vi.stubGlobal('fetch', (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    calls.push(url);
    return Promise.resolve(
      new Response(JSON.stringify(payload), {
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
        {/*
          The page calls `useNavigate` to open a driver's detail page, so it has
          to be inside a router. `MemoryRouter` rather than the app's
          `createHashRouter`: this test is about the page, not about routing, and
          a memory history does not touch `window.location` — which the layout
          auditor's other harnesses do care about.
        */}
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <AppProvider onSignedOut={() => {}}>
            <LiveMapPage />
          </AppProvider>
        </MemoryRouter>
      </StrictMode>,
    );
  });
  // One turn for the snapshot request, one for the state it sets.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 20));
  });
}

describe('the live map page', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    circleMarkerCalls.length = 0;
    mapCalls.length = 0;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('asks the live endpoint, and hides offline drivers by default', async () => {
    const calls = stubTransport(snapshot([RUNNING, IDLE]));
    await renderAndSettle(root);

    const poll = calls.filter((u) => u.includes('/api/v1/admin/live/drivers'));
    // `StrictMode` mounts every effect twice in development, so the count is two
    // rather than one and asserting an exact number would pin a dev-only
    // behaviour. What matters is that no poll asked for offline drivers.
    expect(poll.length).toBeGreaterThan(0);
    expect(poll.every((u) => u.includes('include_offline=false'))).toBe(true);
  });

  it('hands Leaflet [lat, lng], not [lng, lat]', async () => {
    stubTransport(snapshot([RUNNING]));
    await renderAndSettle(root);

    // The whole point: `lat` is the first element. A transposition here is
    // invisible to `tsc` — both are `[number, number]` — and puts every marker
    // in the Gulf of Guinea.
    expect(
      circleMarkerCalls.some(
        (c) => c.latlng[0] === RUNNING.lat && c.latlng[1] === RUNNING.lng,
      ),
    ).toBe(true);
    // Asserted negatively too, because the positive form alone would still pass
    // if the marker were drawn twice, once each way.
    expect(
      circleMarkerCalls.some(
        (c) => c.latlng[0] === RUNNING.lng && c.latlng[1] === RUNNING.lat,
      ),
    ).toBe(false);
  });

  it('marks a car on a trip differently from one that is waiting', async () => {
    stubTransport(snapshot([RUNNING, IDLE]));
    await renderAndSettle(root);

    const byClass = new Map(circleMarkerCalls.map((c) => [c.latlng[0], c.className]));
    expect(byClass.get(RUNNING.lat)).toContain('livemap__pin--running');
    expect(byClass.get(IDLE.lat)).toContain('livemap__pin--idle');
  });

  it('marks a car that stopped reporting as stale', async () => {
    stubTransport(snapshot([STALE]));
    await renderAndSettle(root);

    expect(circleMarkerCalls[0]?.className).toContain('livemap__pin--stale');
    // And the row says so in words, because the colour is not available to a
    // screen reader.
    expect(container.textContent).toContain(text('live.stale'));
  });

  it('lists the most stale car first', async () => {
    stubTransport(snapshot([RUNNING, IDLE, STALE]));
    await renderAndSettle(root);

    const rows = Array.from(container.querySelectorAll('tbody tr'));
    expect(rows).toHaveLength(3);
    // Staleness descending, deliberately. A recency sort — the reflex — would
    // bury the one car an operator needs to see under the two that are fine.
    expect(rows[0]?.textContent).toContain('EF9012');
  });

  it('shows a trip status only for a car that has an order', async () => {
    stubTransport(snapshot([RUNNING, IDLE]));
    await renderAndSettle(root);

    const rows = Array.from(container.querySelectorAll('tbody tr'));
    const running = rows.find((r) => r.textContent?.includes('AB1234'));
    const idle = rows.find((r) => r.textContent?.includes('CD5678'));

    expect(running?.textContent).toContain(text('enum.orderStatus.IN_TRIP'));
    expect(idle?.textContent).toContain(text('live.idle'));
  });

  it('asks for offline drivers once the toggle is on', async () => {
    const calls = stubTransport(snapshot([RUNNING]));
    await renderAndSettle(root);

    const toggle = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === text('live.includeOffline'),
    );
    expect(toggle).toBeDefined();

    await act(async () => {
      toggle?.click();
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // At least one poll carried the new filter. Again not an exact count: the
    // effect re-runs on the filter change and `StrictMode` doubles it.
    expect(
      calls.filter(
        (u) => u.includes('/api/v1/admin/live/drivers') && u.includes('include_offline=true'),
      ).length,
    ).toBeGreaterThan(0);
  });

  it('says so when the fleet was truncated', async () => {
    stubTransport(snapshot([RUNNING], true));
    await renderAndSettle(root);
    expect(container.textContent).toContain(text('live.truncated'));
  });

  it('does not claim truncation when the server did not', async () => {
    stubTransport(snapshot([RUNNING], false));
    await renderAndSettle(root);
    expect(container.textContent).not.toContain(text('live.truncated'));
  });

  it('renders an empty state rather than a bare table', async () => {
    stubTransport(snapshot([]));
    await renderAndSettle(root);

    expect(container.querySelectorAll('tbody tr')).toHaveLength(0);
    expect(container.textContent).toContain(text('live.emptyOnlineOnly'));
  });
});
