/**
 * The console's theme preference: `light`, `dark`, or `system`.
 *
 * Three-way rather than a boolean, because "follow the OS" is a real and
 * different choice from "always dark". An operator on a shared machine may want
 * the theme pinned regardless of how a colleague left the desktop; an operator
 * on a laptop that dims after sunset wants it to track the system. Collapsing
 * those into one toggle forces a false choice.
 *
 * The preference is persisted to `localStorage` and mirrored onto
 * `<html data-theme="...">`, which is the *only* thing the stylesheet reads —
 * no component passes colours down, so a re-theme is one attribute write and
 * every surface follows in the same frame.
 *
 * `system` is left unresolved here: the CSS media query handles it, so that a
 * live OS change repaints instantly with no JavaScript and no listener. The
 * event listener below exists only to keep the toggle button's own label
 * (`aria-pressed`) honest about which mode is *selected*, not which is *shown*.
 */

import { useCallback, useEffect, useState } from 'react';

export type ThemeMode = 'light' | 'dark' | 'system';

/** The order the segmented control presents. `system` first as the default. */
export const THEME_MODES: readonly ThemeMode[] = ['system', 'light', 'dark'] as const;

const STORAGE_KEY = 'realtaxihk.console.theme';

function isThemeMode(value: unknown): value is ThemeMode {
  return value === 'light' || value === 'dark' || value === 'system';
}

/**
 * Read the stored preference.
 *
 * Wrapped because `localStorage` throws rather than returning null in a small
 * number of real situations — Safari private mode historically, and any browser
 * with storage disabled by policy. A console that refuses to boot because it
 * could not read a *cosmetic* preference would be a worse bug than the one this
 * guards against, so every failure falls back to `system`.
 */
export function readThemeMode(): ThemeMode {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return isThemeMode(raw) ? raw : 'system';
  } catch {
    return 'system';
  }
}

function writeThemeMode(mode: ThemeMode): void {
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    /* A theme that will not persist is still a theme that works. */
  }
}

/** Apply the mode to the document root. Idempotent, so callers need no guard. */
export function applyThemeMode(mode: ThemeMode): void {
  document.documentElement.dataset.theme = mode;
}

/** The OS preference right now, for the rare case a caller needs the resolved value. */
function systemPrefersDark(): boolean {
  return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches;
}

export interface ThemeController {
  /** Which of the three modes is *selected*. */
  mode: ThemeMode;
  /** Which theme is actually showing — `system` resolved to a concrete value. */
  resolved: 'light' | 'dark';
  setMode: (mode: ThemeMode) => void;
  /** Advance to the next mode in `THEME_MODES`, wrapping. What the button does. */
  cycle: () => void;
}

export function useTheme(): ThemeController {
  const [mode, setModeState] = useState<ThemeMode>(() => readThemeMode());
  const [systemDark, setSystemDark] = useState<boolean>(() => systemPrefersDark());

  // Push the attribute on every change. Runs on mount too, which is what makes
  // a stored `dark` survive a reload — `index.html` sets nothing, so without
  // this the first paint would be light and then flash.
  useEffect(() => {
    applyThemeMode(mode);
    writeThemeMode(mode);
  }, [mode]);

  // Track the OS only while the selection is `system`: the resolved value is
  // needed for the label, and `resolved` must not change for a pinned theme.
  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const query = matchMedia('(prefers-color-scheme: dark)');
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);

  const setMode = useCallback((next: ThemeMode) => setModeState(next), []);

  const cycle = useCallback(() => {
    setModeState((current) => {
      const index = THEME_MODES.indexOf(current);
      // `?? THEME_MODES[0]` is not defensive noise: `noUncheckedIndexedAccess`
      // types every index read as possibly-undefined, and `indexOf` returning
      // -1 would make `(-1 + 1) % 3` = 0 anyway. The fallback states the intent
      // — an unknown current mode cycles back to the default — and satisfies
      // the checker without a cast.
      return THEME_MODES[(index + 1) % THEME_MODES.length] ?? THEME_MODES[0] ?? 'system';
    });
  }, []);

  const resolved: 'light' | 'dark' = mode === 'system' ? (systemDark ? 'dark' : 'light') : mode;

  return { mode, resolved, setMode, cycle };
}

/**
 * Inline script for `index.html`, run before the bundle so the first paint is
 * already correct.
 *
 * Without this there is a flash of the wrong theme: the HTML paints light, the
 * bundle downloads, React mounts, and only then does `useTheme` write the
 * attribute. On a dark-pinned console that is a white flash every reload. The
 * body is a copy of `readThemeMode` + `applyThemeMode` above — it cannot import
 * them, because it runs before any module system exists.
 */
export const THEME_BOOT_SCRIPT = `(function(){try{var m=localStorage.getItem(${JSON.stringify(
  STORAGE_KEY,
)});if(m!=='light'&&m!=='dark'&&m!=='system')m='system';document.documentElement.dataset.theme=m}catch(e){document.documentElement.dataset.theme='system'}})()`;
