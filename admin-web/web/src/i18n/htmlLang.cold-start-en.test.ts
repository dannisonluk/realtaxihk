/**
 * Cold-start `<html lang>`: the console boots into an **English** preference.
 *
 * Why this file is alone in its own module graph
 * ---------------------------------------------
 * The defect it guards is invisible to a language-switch test. `<html lang>` is
 * written from i18next's `languageChanged` event, which fires on a *change* and
 * **not** on `init`. So with an operator's stored preference of `en`, the
 * console rendered correct English while `<html lang>` stayed at the `zh-Hant`
 * the HTML file was authored with — and every consumer of that attribute
 * (hyphenation, the CJK-vs-Latin font fallback, and `:lang(en)` in
 * `styles.css`) behaved as if the page were Chinese.
 *
 * Three approaches were tried and only this one works, which is worth recording
 * so nobody retries the others:
 *
 *   1. Asserting after a `changeLanguage('en')` — passes on the broken code,
 *      because a change *does* update the attribute.
 *   2. `vi.resetModules()` + a dynamic import inside a file that also statically
 *      imports the module — Vitest keeps the instance in the module graph, so
 *      the dynamic import returns the cached one. Verified: deleting the
 *      startup line still left it green.
 *   3. Several boot cases in one file, each with its own dynamic import — only
 *      the first is cold; the rest reuse the instance, so their `localStorage`
 *      setup has no effect.
 *
 * Hence: **one boot case, one file, no static import of `./index`**. The
 * companion cases live in sibling files that differ only in which preference is
 * stored.
 */

import { afterEach, describe, expect, it } from 'vitest';

/** The console's storage key for the locale preference. */
const LOCALE_KEY = 'realtaxihk.console.locale';

// Seed before the import, which is what a returning operator's browser does.
localStorage.setItem(LOCALE_KEY, 'en');

// Blank, so a passing result cannot come from a leftover value.
document.documentElement.lang = '';

// The one and only evaluation of the module in this file: a genuine cold start.
const { i18n } = await import('./index');

describe('cold start with a stored English preference', () => {
  afterEach(() => {
    localStorage.removeItem(LOCALE_KEY);
  });

  it('resolves to English', () => {
    expect(i18n.resolvedLanguage).toBe('en');
  });

  it('writes <html lang> from the startup path, with no change event', () => {
    // The assertion with teeth: `languageChanged` never fired.
    expect(document.documentElement.lang).toBe('en');
  });

  it('renders an English string, so the language really is active', () => {
    // Ties the attribute to observable output: if i18next had silently fallen
    // back, the attribute could be right while the text is not.
    expect(i18n.t('login.title')).toBe('Admin sign-in');
  });
});
