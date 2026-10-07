/**
 * The order detail page, driven against a stubbed transport.
 *
 * The dispute card is the reason this file exists. It renders what the
 * passenger asked for **as frozen at booking time**, and the whole point is
 * that the two ambiguous cases are not silently collapsed into confident
 * claims either way:
 *
 *  * `requirements: null` and `requirements: {animal: null}` both mean "no pet
 *    was mentioned", and both must render as *nothing requested* — never as a
 *    chip asserting there is no pet, which is a claim the data does not
 *    support. `animal` is a structured detail here (`{kind, height_cm,
 *    weight_kg}`), and a naive truthiness check on the object is exactly the
 *    bug this guards.
 *  * an empty `payment_preference` is *no record*, not "cash only". An operator
 *    deciding a dispute over "I asked to pay by Octopus" must not be shown an
 *    invented fact.
 *
 * `tsc` cannot check either: both shapes type-check fine, and the wrong render
 * is still valid JSX. Neither can the server — it faithfully returns `null`.
 * So it is asserted here, on rendered text.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StrictMode, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { AppProvider } from '../app/AppContext';
import { i18n } from '../i18n';
import { OrderDetailPage } from './OrderDetailPage';

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

const ORDER_ID = 'cccccccc-0000-4000-8000-000000000001';

/**
 * A minimal detail payload. Only the fields the dispute card reads are
 * meaningful; the rest exist so the page's other sections have something to
 * iterate over.
 */
function detail(overrides: Record<string, unknown> = {}) {
  return {
    id: ORDER_ID,
    status: 'COMPLETED',
    passenger_id: 'pppppppp-0000-4000-8000-000000000001',
    driver_profile_id: null,
    driver_id: null,
    taxi_type: 'URBAN',
    fare_mode: 'METER',
    pickup_address: 'Central',
    dropoff_address: 'Kowloon',
    distance_km: '8.4',
    discount_percent: '0',
    created_at: '2026-10-05T04:00:00Z',
    accepted_at: '2026-10-05T04:02:00Z',
    driver_arrived_at: '2026-10-05T04:09:00Z',
    completed_at: '2026-10-05T04:25:00Z',
    cancelled_at: null,
    cancellation_reason: null,
    timeline: [],
    tariff_version: 'v1',
    fare: {},
    broadcast_radius_km: '3.0',
    estimated_total_hkd: '105.00',
    final_total_hkd: '105.00',
    payment_method: null,
    requirements: null,
    payment_preference: [],
    driver_payment_methods: [],
    premium_destination: null,
    pickup_area: null,
    destination_area: null,
    receipt_requested: false,
    receipt_requested_at: null,
    ledger: { items: [], total: 0 },
    unsettled_penalty: null,
    ...overrides,
  };
}

/** Serve `payload` for every request. */
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

/** Serve the detail payload, and the receipt payload for the receipt path. */
function stubTransportWithReceipt(detailPayload: unknown, receiptPayload: unknown) {
  vi.stubGlobal('fetch', (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes('/receipt') ? receiptPayload : detailPayload;
    return Promise.resolve(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  });
}

/** A frozen receipt payload, mirroring `ReceiptOut`. */
function receipt(overrides: Record<string, unknown> = {}) {
  return {
    order_id: ORDER_ID,
    issued_at: '2026-10-05T04:26:00Z',
    status: 'COMPLETED',
    taxi_type: 'URBAN',
    fare_mode: 'METER',
    pickup_address: 'Central',
    dropoff_address: 'Kowloon',
    distance_km: '8.4',
    pickup_area: null,
    destination_area: null,
    premium_destination: null,
    total_hkd: '130.20',
    fare: { meter_fare: '75.2', surcharges_total: '55.0', total_fare: '130.2' },
    fixed_fare: null,
    requirements: { silent_ride: true },
    payment_preference: ['CASH'],
    driver_payment_methods: ['OCTOPUS'],
    passenger_name: null,
    tariff_version: 'v1',
    created_at: null,
    completed_at: '2026-10-05T04:25:00Z',
    text: 'hkfastdc.com — 車費收據 / Fare Receipt\n總額 Total : HK$ 130.2\n完全靜音 Silent ride',
    disclaimer_zh: '免責聲明：資訊中介平台',
    disclaimer_en: 'Disclaimer: information intermediary platform',
    ...overrides,
  };
}

/** The rendered text of the dispute card, and only that card. */
function disputeCardText(container: HTMLElement): string {
  // Select on the *note*, not the heading: the `<h2>` is a sibling of the card,
  // so keying off the title returns nothing.
  const marker = text('orderDetail.requirementsLabel');
  const cards = Array.from(container.querySelectorAll('.card--pad'));
  const card = cards.find((c) => (c.textContent ?? '').includes(marker));
  return card?.textContent ?? '';
}

async function renderAndSettle(root: Root) {
  await act(async () => {
    root.render(
      <StrictMode>
        <MemoryRouter
          initialEntries={[`/orders/${ORDER_ID}`]}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <AppProvider onSignedOut={() => {}}>
            <Routes>
              <Route path="/orders/:orderId" element={<OrderDetailPage />} />
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

describe('the order detail page', () => {
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

  it('renders a requested requirement as its own label', async () => {
    stubTransport(
      detail({
        requirements: {
          silent_ride: true,
          no_radio_music: false,
          no_smoke: false,
          no_perfume: false,
          animal: null,
        },
      }),
    );
    await renderAndSettle(root);

    const card = disputeCardText(container);
    // The `<h2>` sits above the card, so the heading is asserted on the page.
    expect(container.textContent).toContain(text('orderDetail.disputeTitle'));
    expect(card).toContain(text('enum.requirement.silent_ride'));
    // A flag that is `false` is not a request and must not be shown.
    expect(card).not.toContain(text('enum.requirement.no_smoke'));
  });

  it('treats a null animal as "nothing requested", not as "no pet"', async () => {
    stubTransport(
      detail({
        requirements: {
          silent_ride: false,
          no_radio_music: false,
          no_smoke: false,
          no_perfume: false,
          animal: null,
        },
      }),
    );
    await renderAndSettle(root);

    const card = disputeCardText(container);
    // An all-false record (plus a null animal) is indistinguishable from
    // "never asked", so it says so — it does not assert the absence of a pet.
    expect(card).toContain(text('orderDetail.noRequirements'));
    expect(card).not.toContain('DOG');
  });

  it('renders an animal with its recorded size', async () => {
    stubTransport(
      detail({
        requirements: {
          silent_ride: false,
          no_radio_music: false,
          no_smoke: false,
          no_perfume: false,
          animal: { kind: 'DOG', height_cm: 35, weight_kg: 8 },
        },
      }),
    );
    await renderAndSettle(root);

    const card = disputeCardText(container);
    expect(card).toContain('DOG');
    expect(card).toContain('35');
    expect(card).toContain('8');
    expect(card).not.toContain(text('orderDetail.noRequirements'));
  });

  it('says "not recorded" for an empty payment preference, never "cash"', async () => {
    stubTransport(detail({ payment_preference: [] }));
    await renderAndSettle(root);

    const card = disputeCardText(container);
    expect(card).toContain(text('orderDetail.noneRecorded'));
    expect(card).not.toContain(text('enum.paymentMethod.CASH'));
  });

  it('renders the passenger preference and the driver methods separately', async () => {
    stubTransport(
      detail({
        payment_preference: ['OCTOPUS'],
        driver_payment_methods: ['CASH', 'OCTOPUS'],
      }),
    );
    await renderAndSettle(root);

    const card = disputeCardText(container);
    expect(card).toContain(text('orderDetail.paymentPreferenceLabel'));
    expect(card).toContain(text('orderDetail.driverMethodsLabel'));
    expect(card).toContain(text('enum.paymentMethod.OCTOPUS'));
    expect(card).toContain(text('enum.paymentMethod.CASH'));
  });

  it('shows whether a receipt was requested, and when', async () => {
    stubTransport(
      detail({
        receipt_requested: true,
        receipt_requested_at: '2026-10-05T04:26:00Z',
      }),
    );
    await renderAndSettle(root);

    const card = disputeCardText(container);
    expect(card).toContain(text('orderDetail.receiptRequested'));
    expect(card).not.toContain(text('orderDetail.receiptNotRequested'));
  });

  it('does not offer a receipt until one has been requested', async () => {
    stubTransport(detail({ receipt_requested: false }));
    await renderAndSettle(root);

    expect(container.textContent).not.toContain(text('orderDetail.viewReceipt'));
  });

  it('opens the frozen receipt document for a requested receipt', async () => {
    stubTransportWithReceipt(
      detail({
        receipt_requested: true,
        receipt_requested_at: '2026-10-05T04:26:00Z',
      }),
      receipt(),
    );
    await renderAndSettle(root);

    const button = Array.from(container.querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').includes(text('orderDetail.viewReceipt')),
    );
    expect(button).toBeTruthy();

    await act(async () => {
      button?.click();
      await new Promise((r) => setTimeout(r, 20));
    });

    // The structured money field and the server-rendered document both appear;
    // the document is the same bytes the passenger would hold.
    expect(container.textContent).toContain('130.20');
    expect(container.textContent).toContain('車費收據 / Fare Receipt');
    expect(container.textContent).toContain('完全靜音 Silent ride');
  });

  it('shows a recorded but uncollected passenger penalty', async () => {
    stubTransport(
      detail({
        status: 'CANCELLED',
        unsettled_penalty: {
          amount_hkd: '105.00',
          basis_hkd: '105.00',
          share_percent: '100',
          reason_code: 'NO_SHOW',
          cancellation_reason: 'NO_SHOW',
          actor_kind: 'PASSENGER',
          charged_at: '2026-09-02T10:02:00Z',
        },
      }),
    );
    await renderAndSettle(root);

    expect(container.textContent).toContain(text('orderDetail.unsettledPenaltyLabel'));
    expect(container.textContent).toContain(text('orderDetail.unsettledPenaltyTitle'));
    expect(container.textContent).toContain('105.00');
    expect(container.textContent).toContain('NO_SHOW');
  });

  it('does not offer a penalty card when none is recorded', async () => {
    stubTransport(detail({ unsettled_penalty: null }));
    await renderAndSettle(root);

    expect(container.textContent).not.toContain(text('orderDetail.unsettledPenaltyLabel'));
    expect(container.textContent).not.toContain(text('orderDetail.unsettledPenaltyTitle'));
  });

  // The one that stops an operator reading "the meter said $140 but the
  // passenger was quoted $105" as a billing error.
  it('states the fare mode in words, for both modes', async () => {
    stubTransport(detail({ fare_mode: 'FIXED' }));
    await renderAndSettle(root);
    expect(container.textContent).toContain(text('enum.fareMode.FIXED'));

    act(() => root.unmount());
    container.remove();

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    stubTransport(detail({ fare_mode: 'METER' }));
    await renderAndSettle(root);
    expect(container.textContent).toContain(text('enum.fareMode.METER'));
  });
});
