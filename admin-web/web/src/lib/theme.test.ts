/**
 * Tests for the theme preference.
 *
 * The thing worth testing here is not "does the attribute get written" — that is
 * one line — but the two ways this feature goes wrong in production:
 *
 *   1. **The boot script and the module disagree.** `index.html` cannot import
 *      `theme.ts`, so its copy of the read/apply logic is hand-maintained. If the
 *      storage key or the accepted values drift, the first paint uses one theme
 *      and React immediately corrects it to another, which is *worse* than no
 *      boot script: the flash becomes a visible snap on every load.
 *   2. **A storage failure takes the console down.** `localStorage` throws (not
 *      returns null) in Safari private mode and under some enterprise policies.
 *      A cosmetic preference must never be able to stop the console booting.
 *
 * Both are tested against the real exports, not a re-implementation.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { THEME_BOOT_SCRIPT, applyThemeMode, readThemeMode } from './theme';

const STORAGE_KEY = 'realtaxihk.console.theme';

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe('readThemeMode', () => {
  it('defaults to system when nothing is stored', () => {
    expect(readThemeMode()).toBe('system');
  });

  it('returns each stored valid mode verbatim', () => {
    for (const mode of ['light', 'dark', 'system'] as const) {
      localStorage.setItem(STORAGE_KEY, mode);
      expect(readThemeMode()).toBe(mode);
    }
  });

  it('falls back to system for a value it cannot interpret', () => {
    // A leftover from a previous version of the console, or a hand-edit in
    // devtools. Neither may produce an unstyled page, so anything unrecognised
    // resolves to the safest mode rather than being passed through to the
    // stylesheet as an attribute no rule matches.
    localStorage.setItem(STORAGE_KEY, 'midnight');
    expect(readThemeMode()).toBe('system');
  });

  it('survives a storage that throws', () => {
    const original = Storage.prototype.getItem;
    Storage.prototype.getItem = () => {
      throw new DOMException('denied', 'SecurityError');
    };
    try {
      expect(readThemeMode()).toBe('system');
    } finally {
      Storage.prototype.getItem = original;
    }
  });
});

describe('applyThemeMode', () => {
  it('writes the mode onto <html> as data-theme', () => {
    applyThemeMode('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('is idempotent and overwrites', () => {
    applyThemeMode('dark');
    applyThemeMode('light');
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});

describe('THEME_BOOT_SCRIPT', () => {
  /**
   * Run the boot script the way the browser does — as a bare script — and check
   * it lands on the same attribute the module would. This is the anti-drift
   * test: if `theme.ts` changes its key or its accepted set without the inline
   * script following, this fails.
   */
  function runBootScript() {
    // eslint-disable-next-line no-new-func
    new Function(THEME_BOOT_SCRIPT)();
  }

  it('agrees with the module for a stored dark preference', () => {
    localStorage.setItem(STORAGE_KEY, 'dark');
    runBootScript();
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('agrees with the module when nothing is stored', () => {
    runBootScript();
    expect(document.documentElement.dataset.theme).toBe('system');
  });

  it('rejects a value the module would reject', () => {
    localStorage.setItem(STORAGE_KEY, 'sepia');
    runBootScript();
    expect(document.documentElement.dataset.theme).toBe('system');
  });

  it('still themes the page when storage throws', () => {
    const original = Storage.prototype.getItem;
    Storage.prototype.getItem = () => {
      throw new DOMException('denied', 'SecurityError');
    };
    try {
      runBootScript();
      expect(document.documentElement.dataset.theme).toBe('system');
    } finally {
      Storage.prototype.getItem = original;
    }
  });

  /**
   * The inline script in `index.html` and `THEME_BOOT_SCRIPT` in this module are
   * two copies of the same logic. The module export exists so a future bundler
   * *could* inline it; today `index.html` is hand-written, so the drift risk is
   * real.
   *
   * The presence of the script in the served HTML is asserted by the UI verifier
   * (`admin-web/tool/verify_ui.mjs`), which already fetches the page — it can see
   * the real bytes the browser receives, which a jsdom test reading a source file
   * cannot. What is asserted *here* is the half that only exists at runtime: that
   * executing the boot script leaves the same attribute the hook would.
   */
});
