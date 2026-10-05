/**
 * The order table — driven against a stubbed transport.
 *
 * This file exists for the fare-mode annotation, which is the one thing in
 * this table that speaks about money. The rule it enforces is asymmetric on
 * purpose:
 *
 *  * a `FIXED` trip is **annotated** (`Urban taxi · Agreed price`) — a fixed
 *    price changes how the whole row reads, because the meter total that was
 *    actually rung up is a different number and that is expected, not a
 *    billing error;
 *  * a `METER` trip is **left plain** — it is the default, and a label on
 *    every row is the noise that trains an operator to stop reading the
 *    column at all.
 *
 * `tsc` cannot check this: `order.fare_mode === 'FIXED' ? ... : ''` and an
 * unconditional render are both valid TS, and rendering the annotation for
 * both modes is a perfectly well-typed regression. So it is pinned here, on
 * rendered text.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StrictMode, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { AppProvider } from '../app/AppContext';
import { i18n } from '../i18n';
import { OrdersPage } from './OrdersPage';

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

/** One row, with the handful of fields the table renders. */
function row(overrides: Record<string, unknown> = {}) {
  return {
    id: 'cccccccc-0000-4000-8000-000000000001',
    status: 'COMPLETED',
    taxi_type: 'URBAN',
    fare_mode: 'METER',
    passenger_id: 'pppppppp-0000-4000-8000-000000000001',
    driver_id: null,
    pickup_address: 'Central',
    dropoff_address: 'Kowloon',
    distance_km: '8.4',
    discount_percent: '0',
    estimated_total_hkd: '105.00',
    final_total_hkd: '105.00',
    accepted_at: null,
    completed_at: null,
    created_at: '2026-10-05T04:00:00Z',
    ...overrides,
  };
}

function page(items: unknown[]) {
  return { items, total: items.length, limit: 50, offset: 0 };
}

function stubTransport(payload: unknown) {
  vi.stubGlobal('fetch', () =>
    Promise.resolve(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  );
}

/**
 * The rendered text of the trip table only, excluding the filter chips.
 *
 * Scoping matters: the fare-mode filter chips legitimately carry both labels,
 * so asserting on `container.textContent` would make the "FIXED is annotated"
 * test pass without any annotation in the row — turning a regression guard
 * into a tautology.
 */
function tableText(container: HTMLElement): string {
  return container.querySelector('tbody')?.textContent ?? '';
}

async function renderAndSettle(root: Root) {
  await act(async () => {
    root.render(
      <StrictMode>
        <MemoryRouter
          initialEntries={['/orders']}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <AppProvider onSignedOut={() => {}}>
            <Routes>
              <Route path="/orders" element={<OrdersPage />} />
            </Routes>
          </AppProvider>
        </MemoryRouter>
      </StrictMode>,
    );
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 20));
  });
}

describe('the orders table', () => {
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

  it('annotates a fixed-price trip with the fare mode', async () => {
    stubTransport(page([row({ fare_mode: 'FIXED' })]));
    await renderAndSettle(root);

    const body = tableText(container);
    expect(body).toContain(text('enum.taxiType.URBAN'));
    expect(body).toContain(text('enum.fareMode.FIXED'));
  });

  it('leaves a metered trip unannotated', async () => {
    stubTransport(page([row({ fare_mode: 'METER' })]));
    await renderAndSettle(root);

    const body = tableText(container);
    // The taxi type is present; the fare label is not. Default-valued labels
    // on every row are what makes people stop reading the column.
    expect(body).toContain(text('enum.taxiType.URBAN'));
    expect(body).not.toContain(text('enum.fareMode.METER'));
  });

  it('sends the fare-mode filter to the server when a chip is chosen', async () => {
    const urls: string[] = [];
    vi.stubGlobal('fetch', (input: RequestInfo | URL) => {
      urls.push(String(input));
      return Promise.resolve(
        new Response(JSON.stringify(page([row()])), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    });
    await renderAndSettle(root);

    const chip = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === text('enum.fareMode.FIXED'),
    );
    expect(chip, 'the FIXED filter chip should be rendered').toBeTruthy();
    await act(async () => {
      chip?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // The filter must reach the wire: a chip that only changed local state
    // would leave the table showing every trip while claiming to be filtered.
    expect(urls.some((u) => u.includes('fare_mode=FIXED'))).toBe(true);
  });
});
