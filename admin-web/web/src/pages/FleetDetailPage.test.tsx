/**
 * The fleet settlement lever.
 *
 * Running a settlement moves money: it charges the roster for the week and
 * overwrites the stored aggregate. The page guards it with a deliberate second
 * press, and this file pins *how* that guard is presented — an in-app confirm
 * modal, never `window.confirm`.
 *
 * The native dialog was the one money action in the console that behaved
 * differently from every other destructive action: it renders outside the
 * app's styling, cannot carry the danger tone, and blocks the whole tab.
 * `tsc` cannot see the difference — a `window.confirm` call and a
 * `useConfirmDialog` open are both well-typed — so it is asserted here, on the
 * DOM (a danger modal appears) and on a spy (`window.confirm` is never called).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StrictMode, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { AppProvider } from '../app/AppContext';
import { i18n } from '../i18n';
import { FleetDetailPage } from './FleetDetailPage';

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

const FLEET_ID = 'ffffffff-0000-4000-8000-000000000001';

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function fleetRow(overrides: Record<string, unknown> = {}) {
  return {
    id: FLEET_ID,
    name: 'Kowloon Fleet',
    license_no: 'TF-001',
    status: 'ACTIVE',
    weekly_fee_discount_percent: '10',
    contact_name: null,
    contact_phone: null,
    note: null,
    created_at: '2026-10-01T00:00:00Z',
    member_count: 0,
    ...overrides,
  };
}

/**
 * Route each request the page makes. `onRun` fires when the settlement POST is
 * actually issued, which is how the second test proves nothing runs until the
 * modal is confirmed. `options.failRun` makes that POST fail with the backend's
 * own envelope, so the failure path can be asserted too.
 */
function stubTransport(onRun?: () => void, options: { failRun?: boolean } = {}) {
  vi.stubGlobal('fetch', (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/settlement/run')) {
      onRun?.();
      if (options.failRun) {
        // The backend's envelope (`{code, message, details}`), not FastAPI's
        // `{detail}` — `toApiError` reads `message` off it.
        return Promise.resolve(
          new Response(
            JSON.stringify({ code: 'CONFLICT', message: 'this week is already settled' }),
            { status: 409, headers: { 'Content-Type': 'application/json' } },
          ),
        );
      }
      return Promise.resolve(
        json({
          period: '2026-W40',
          fee_hkd: '180.00',
          discount_percent: '10',
          member_count: 0,
          charged: 0,
          skipped: 0,
          failed: 0,
          tampered: 0,
          collected_hkd: '0.00',
          created_at: '2026-10-05T00:00:00Z',
        }),
      );
    }
    if (url.includes('/settlement')) return Promise.resolve(json({ items: [], total: 0 }));
    if (url.includes('/members')) return Promise.resolve(json({ items: [], total: 0 }));
    return Promise.resolve(json({ items: [fleetRow()], total: 1 }));
  });
}

function findButton(container: HTMLElement, label: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll('button')).find((button) =>
    (button.textContent ?? '').includes(label),
  );
}

async function renderAndSettle(root: Root) {
  await act(async () => {
    root.render(
      <StrictMode>
        <MemoryRouter
          initialEntries={[`/fleets/${FLEET_ID}`]}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <AppProvider onSignedOut={() => {}}>
            <Routes>
              <Route path="/fleets/:fleetId" element={<FleetDetailPage />} />
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

describe('the fleet settlement lever', () => {
  let container: HTMLDivElement;
  let root: Root;
  let confirmSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    confirmSpy = vi.fn(() => true);
    vi.stubGlobal('confirm', confirmSpy);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('asks for confirmation in an in-app danger modal, never window.confirm', async () => {
    stubTransport();
    await renderAndSettle(root);

    // No modal until the lever is pressed.
    expect(container.querySelector('.btn--danger')).toBeNull();

    const lever = findButton(container, text('fleetDetail.runNow'));
    expect(lever).toBeTruthy();

    await act(async () => {
      lever?.click();
      await new Promise((r) => setTimeout(r, 10));
    });

    // The confirm step is an in-app modal whose button carries the danger tone
    // (the roster is empty, so this button can only be the modal's), and the
    // native dialog was not used.
    expect(container.querySelector('.btn--danger')).toBeTruthy();
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it('runs the settlement only after the modal is confirmed', async () => {
    let runs = 0;
    stubTransport(() => {
      runs += 1;
    });
    await renderAndSettle(root);

    // The press itself has to be inside `act`: opening the modal is a state
    // update, and firing it bare makes React report an update escaping `act`
    // (which is how this file behaved before the extra tests went in).
    await act(async () => {
      findButton(container, text('fleetDetail.runNow'))?.click();
      await new Promise((r) => setTimeout(r, 10));
    });

    // Opening the modal must not have run anything yet.
    expect(runs).toBe(0);

    const confirmButton = container.querySelector('.btn--danger');
    expect(confirmButton).toBeTruthy();

    await act(async () => {
      (confirmButton as HTMLButtonElement).click();
      await new Promise((r) => setTimeout(r, 20));
    });

    expect(runs).toBe(1);
  });

  it('puts focus back on the lever when the modal closes', async () => {
    stubTransport();
    await renderAndSettle(root);

    // jsdom's `.click()` does not focus the button the way a real press does, so
    // the opener has to be focused explicitly or the assertion below would pass
    // for the wrong reason (restoring focus to `<body>`).
    const lever = findButton(container, text('fleetDetail.runNow'));
    lever?.focus();
    expect(document.activeElement).toBe(lever);

    await act(async () => {
      lever?.click();
      await new Promise((r) => setTimeout(r, 10));
    });

    // Focus moved into the dialog, or a screen reader keeps announcing the page
    // behind it.
    const modal = container.querySelector('.modal');
    expect(modal).toBeTruthy();
    expect(modal?.contains(document.activeElement)).toBe(true);

    const cancel = findButton(container, text('common.cancel'));
    await act(async () => {
      cancel?.click();
      await new Promise((r) => setTimeout(r, 10));
    });

    expect(container.querySelector('.modal')).toBeNull();
    // Without the restore this would be `<body>`, and the next Tab would start
    // from the top of the page instead of from the lever.
    expect(document.activeElement).toBe(lever);
  });

  it('closes on Escape without running the settlement', async () => {
    let runs = 0;
    stubTransport(() => {
      runs += 1;
    });
    await renderAndSettle(root);

    // The press itself has to be inside `act`: opening the modal is a state
    // update, and firing it bare makes React report an update escaping `act`
    // (which is how this file behaved before the extra tests went in).
    await act(async () => {
      findButton(container, text('fleetDetail.runNow'))?.click();
      await new Promise((r) => setTimeout(r, 10));
    });
    expect(container.querySelector('.modal')).toBeTruthy();

    await act(async () => {
      container
        .querySelector('.modal')
        ?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await new Promise((r) => setTimeout(r, 10));
    });

    expect(container.querySelector('.modal')).toBeNull();
    expect(runs).toBe(0);
  });

  it('closes on a backdrop click without running the settlement', async () => {
    let runs = 0;
    stubTransport(() => {
      runs += 1;
    });
    await renderAndSettle(root);

    // The press itself has to be inside `act`: opening the modal is a state
    // update, and firing it bare makes React report an update escaping `act`
    // (which is how this file behaved before the extra tests went in).
    await act(async () => {
      findButton(container, text('fleetDetail.runNow'))?.click();
      await new Promise((r) => setTimeout(r, 10));
    });

    // A press that starts on the backdrop itself dismisses; one that starts
    // inside the dialog and ends on the backdrop must not (that is a drag, not a
    // dismissal), which is why the handler compares target and currentTarget.
    await act(async () => {
      container
        .querySelector('.modal-backdrop')
        ?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      await new Promise((r) => setTimeout(r, 10));
    });

    expect(container.querySelector('.modal')).toBeNull();
    expect(runs).toBe(0);
  });

  it('reports a failed settlement below the lever, not inside the modal', async () => {
    stubTransport(undefined, { failRun: true });
    await renderAndSettle(root);

    // The press itself has to be inside `act`: opening the modal is a state
    // update, and firing it bare makes React report an update escaping `act`
    // (which is how this file behaved before the extra tests went in).
    await act(async () => {
      findButton(container, text('fleetDetail.runNow'))?.click();
      await new Promise((r) => setTimeout(r, 10));
    });

    await act(async () => {
      (container.querySelector('.btn--danger') as HTMLButtonElement).click();
      await new Promise((r) => setTimeout(r, 20));
    });
    // The failure path settles `setRunning(false)` in a `finally` after the
    // error has been rendered; flush it here so React does not warn about an
    // update escaping `act`.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // The modal closes even on failure — deliberately: the roster and history
    // above the lever are still valid, and the operator should be able to read
    // them while deciding what to do. The server's own sentence is what shows.
    expect(container.querySelector('.modal')).toBeNull();
    expect(container.querySelector('.message--error')?.textContent).toContain(
      'this week is already settled',
    );
  });
});
