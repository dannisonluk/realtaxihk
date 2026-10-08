/**
 * Data-loading primitives.
 *
 * Two things the console needs from every page, and neither is in a library:
 *
 *  1. **Sequential** multi-call loading. Firing a page's independent requests
 *     together is the obvious shape, and it is what the console did — until it
 *     ran on a machine that intermittently accepts a connection to the API and
 *     then never answers it. Every parallel call is another connection that can
 *     land on that path, and the hazard grows with the width of the burst.
 *     Sequential calls each open their connection only after the previous one
 *     closed, so there is never more than one in flight. Each call is
 *     ~50–150ms, so a page still resolves in well under a second.
 *  2. A single place to turn an `ApiError` into something renderable.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, CODE } from '../api/client';

export interface Loadable<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  /** Re-run the loader. */
  reload: () => void;
}

/**
 * Run `load` on mount and whenever `deps` change, without racing.
 *
 * The loader receives an `AbortSignal`, so pages that pass it into API calls
 * release their in-flight sockets when the run is superseded or the page
 * unmounts. A late response from a superseded run is still discarded,
 * matching the vanilla build's `renderToken` guard — otherwise a slow first
 * load can overwrite a newer one and the page shows stale data with no error.
 */
export function useLoad<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
): Loadable<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const busyRef = useRef(false);

  // Keep the latest loader without making it a dependency: callers pass an
  // inline arrow, so including it would re-run on every render.
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    busyRef.current = true;
    setLoading(true);
    setError(null);

    void (async () => {
      try {
        const result = await loadRef.current(controller.signal);
        if (cancelled) return;
        setData(result);
      } catch (cause) {
        if (cancelled) return;
        setError(normaliseError(cause));
      } finally {
        if (!cancelled) busyRef.current = false;
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => {
    if (busyRef.current) return;
    setNonce((n) => n + 1);
  }, []);

  return { data, error, loading, reload };
}

/**
 * Run a list of thunks **in order**, passing each result to the next.
 *
 * Returns the results as a tuple. This is the sequential loader the dashboard
 * and the settlement page need; `Promise.all` is deliberately not offered,
 * because using it is the bug described at the top of this file.
 */
export async function inOrder<
  T extends readonly ((signal: AbortSignal) => Promise<unknown>)[],
>(
  thunks: T,
  signal?: AbortSignal,
): Promise<{ [K in keyof T]: Awaited<ReturnType<T[K]>> }> {
  const results: unknown[] = [];
  const nextSignal = signal ?? new AbortController().signal;
  for (const thunk of thunks) {
    results.push(await thunk(nextSignal));
  }
  return results as { [K in keyof T]: Awaited<ReturnType<T[K]>> };
}

/** Anything thrown becomes an Error with a message worth showing. */
export function normaliseError(cause: unknown): Error {
  if (cause instanceof ApiError) return cause;
  if (cause instanceof Error) return cause;
  return new Error(String(cause));
}

/**
 * A short hint for the errors an operator can act on.
 *
 * Takes `t` rather than reading a module-level translation: this is a plain
 * function, not a component, so it cannot call a hook — and the hint has to
 * follow the active language like everything else.
 */
export function errorHint(
  error: Error,
  t: (key: string, options?: Record<string, unknown>) => string,
): string | null {
  if (!(error instanceof ApiError)) return null;
  switch (error.code) {
    case CODE.rateLimited:
      return error.retryAfter === null
        ? t('errors.rateLimitedSoon')
        : t('errors.rateLimitedWait', { minutes: Math.ceil(error.retryAfter / 60) });
    case CODE.serviceUnavailable:
      return t('errors.rateLimited');
    case CODE.network:
      return t('errors.network');
    default:
      return null;
  }
}
