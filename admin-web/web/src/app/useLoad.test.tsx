import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { useLoad } from './useLoad';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function Harness({ loader }: { loader: (signal: AbortSignal) => Promise<string> }) {
  const { data, error, loading, reload } = useLoad(loader, []);

  return (
    <div>
      <span data-loading={String(loading)} />
      <span>{data ?? 'none'}</span>
      <span>{error?.message ?? ''}</span>
      <button onClick={reload} type="button">
        reload
      </button>
    </div>
  );
}

function DepHarness({ loader, seed }: { loader: (signal: AbortSignal) => Promise<string>; seed: number }) {
  useLoad(loader, [seed]);
  return null;
}

describe('useLoad', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function flush() {
    await act(async () => {
      await Promise.resolve();
    });
  }

  it('ignores reload while a load is already in flight', async () => {
    let resolvePromise!: (value: string) => void;
    const loader = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          resolvePromise = resolve;
        }),
    );

    await act(async () => {
      root.render(<Harness loader={loader} />);
    });
    await flush();

    expect(loader).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-loading]')?.getAttribute('data-loading')).toBe(
      'true',
    );

    await act(async () => {
      container.querySelector('button')?.click();
    });
    await flush();

    expect(loader).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolvePromise('ready');
    });
    await flush();

    expect(container.textContent).toContain('ready');
    expect(container.querySelector('[data-loading]')?.getAttribute('data-loading')).toBe(
      'false',
    );

    await act(async () => {
      container.querySelector('button')?.click();
    });
    await flush();

    expect(loader).toHaveBeenCalledTimes(2);
  });

  it('aborts the previous loader when dependencies change', async () => {
    const signals: AbortSignal[] = [];
    const loader = vi.fn(
      (signal: AbortSignal) =>
        new Promise<string>(() => {
          signals.push(signal);
        }),
    );

    await act(async () => {
      root.render(<DepHarness loader={loader} seed={1} />);
    });
    await flush();

    expect(loader).toHaveBeenCalledTimes(1);
    expect(signals[0]?.aborted).toBe(false);

    await act(async () => {
      root.render(<DepHarness loader={loader} seed={2} />);
    });
    await flush();

    expect(loader).toHaveBeenCalledTimes(2);
    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
  });
});