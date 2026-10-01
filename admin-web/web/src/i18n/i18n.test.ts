/**
 * The i18n layer itself: key parity, locale resolution, and the two things that
 * can only be checked at runtime — that switching language actually re-renders,
 * and that `<html lang>` follows.
 *
 * Why this file exists at all
 * ---------------------------
 * The `satisfies TranslationShape<typeof zhHant>` annotation already makes a
 * missing English key a **compile error**, so key parity is enforced by `tsc`.
 * That check has one hole the compiler cannot see: it compares the *declared*
 * shape, and a leaf that is an empty string, or a key whose English value is
 * still the Chinese text (a translator's staging mistake), typechecks
 * perfectly. Both ship as a wrong-language or blank label. So the parity walk
 * below runs over the *runtime* resource i18next actually holds.
 *
 * The second half is the part with real teeth for this project. The console
 * defaults to Traditional Chinese but the test environment reported an English
 * browser, which is how the pre-existing suite silently stopped asserting on
 * anything: every test hard-coded a Chinese literal, the app rendered English,
 * and 16 tests failed at once. Rather than pin a locale globally in the test
 * setup — which would hide the same class of regression behind a fixture — the
 * suite now resolves its expectations by *key*. These tests are what makes that
 * safe: they prove every key resolves in both locales, so a key-based
 * expectation can never quietly fall back to the key itself.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { i18n, LOCALES, DEFAULT_LOCALE, formatLocaleFor, type Locale } from './index';
import { zhHant } from './locales/zh-Hant';
import { en } from './locales/en';

/** Every dotted key path in a nested resource object. */
function keyPaths(node: unknown, prefix = ''): string[] {
  if (typeof node !== 'object' || node === null) return [prefix];
  return Object.entries(node as Record<string, unknown>).flatMap(([key, value]) =>
    keyPaths(value, prefix ? `${prefix}.${key}` : key),
  );
}

const zhKeys = keyPaths(zhHant).sort();
const enKeys = keyPaths(en).sort();

describe('the translation resources', () => {
  it('declare exactly the same key set in both locales', () => {
    // `tsc` already proves this on the declared shape; this proves it on what is
    // imported, so a resource assembled at runtime cannot slip past the type.
    expect(enKeys).toEqual(zhKeys);
  });

  it('has no empty leaf in either locale', () => {
    // An empty value typechecks and renders as a blank label — worse than a
    // missing key, which at least shows the dotted path.
    const read = (root: unknown, path: string) =>
      path
        .split('.')
        .reduce<unknown>(
          (acc, part) =>
            typeof acc === 'object' && acc !== null
              ? (acc as Record<string, unknown>)[part]
              : undefined,
          root,
        );
    const blanks = zhKeys.filter(
      (path) => read(zhHant, path) === '' || read(en, path) === '',
    );
    expect(blanks).toEqual([]);
  });

  it('does not leave a Chinese string in the English resource', () => {
    // The failure this catches: a key added to `zh-Hant.ts`, copied into
    // `en.ts` to satisfy the compiler, and never actually translated. It is
    // invisible to `tsc` because both sides are `string`.
    const cjk = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;
    const read = (root: unknown, path: string) =>
      path
        .split('.')
        .reduce<unknown>(
          (acc, part) =>
            typeof acc === 'object' && acc !== null
              ? (acc as Record<string, unknown>)[part]
              : undefined,
          root,
        );
    const untranslated = enKeys.filter((path) => cjk.test(String(read(en, path) ?? '')));
    expect(untranslated).toEqual([]);
  });

  it('interpolates the same placeholders in both locales', () => {
    // A sentence may reorder its values freely, but it may not *drop* one:
    // `{{count}} orders` translated as `orders` loses the number with no error
    // anywhere. Comparing the placeholder sets is what makes that visible.
    const placeholders = (value: string) =>
      [...value.matchAll(/\{\{\s*([a-zA-Z0-9_]+)/g)].map((m) => m[1]).sort();
    const read = (root: unknown, path: string) =>
      path
        .split('.')
        .reduce<unknown>(
          (acc, part) =>
            typeof acc === 'object' && acc !== null
              ? (acc as Record<string, unknown>)[part]
              : undefined,
          root,
        );
    const mismatched = zhKeys.filter(
      (path) =>
        JSON.stringify(placeholders(String(read(zhHant, path)))) !==
        JSON.stringify(placeholders(String(read(en, path)))),
    );
    expect(mismatched).toEqual([]);
  });
});

describe('locale resolution', () => {
  const original = i18n.resolvedLanguage;

  afterEach(async () => {
    // Every test restores the locale, because i18next's language is process-wide
    // and would otherwise leak into the next test — and into the DOM assertion
    // on `<html lang>`.
    await i18n.changeLanguage(original);
  });

  it('ships exactly the two locales the console promises', () => {
    expect([...LOCALES]).toEqual(['zh-Hant', 'en']);
    expect(DEFAULT_LOCALE).toBe('zh-Hant');
  });

  it('resolves a key in every locale', () => {
    // This is the precondition for the rest of the suite asserting by key.
    for (const locale of LOCALES) {
      i18n.changeLanguage(locale);
      for (const path of zhKeys) {
        const value = i18n.t(path);
        expect(value, `${locale}:${path}`).not.toBe(path);
        expect(value.length, `${locale}:${path}`).toBeGreaterThan(0);
      }
    }
  });

  it('gives the two locales different text for the same key', () => {
    // If a language switch produced identical output it would mean the resource
    // map is not being consulted — a broken `lng` wiring, not a translation.
    i18n.changeLanguage('zh-Hant');
    const zh = i18n.t('login.title');
    i18n.changeLanguage('en');
    const eng = i18n.t('login.title');
    expect(zh).not.toBe(eng);
  });

  it('writes the locale onto <html lang> so CSS can see it', async () => {
    // `styles.css` has a `:lang(en)` rule; it is inert unless this attribute is
    // kept honest on every change.
    await i18n.changeLanguage('en');
    expect(document.documentElement.lang).toBe('en');
    await i18n.changeLanguage('zh-Hant');
    expect(document.documentElement.lang).toBe('zh-Hant');
  });

  it('maps each i18next locale to a distinct Intl tag', () => {
    // `zh-Hant` alone is not a usable `Intl` tag — it names a script, not a
    // region, and `Intl` needs the region to pick a date order. The mapping is
    // what supplies it, and keeping the two namespaces apart is what stops a
    // resource key (`zh-Hant`) being handed to `toLocaleDateString` by mistake.
    const tags = LOCALES.map((l: Locale) => formatLocaleFor(l));
    expect(new Set(tags).size).toBe(LOCALES.length);
    expect(formatLocaleFor('zh-Hant')).toBe('zh-HK');
    expect(formatLocaleFor('en')).toBe('en-HK');
  });

  it('needs the region: the HK tag is not the bare language tag', () => {
    // The concrete reason `formatLocale` exists as its own concept.
    //
    // Note what this does *not* assert: that `zh-HK` and `en-HK` differ. They
    // do not — full-ICU formats both as D/M/Y, and `zh-HK` is a day-first
    // locale, not the year-first one a reader might assume from `zh-TW`. That
    // was verified against this machine's ICU, so an assertion to the contrary
    // would be testing a belief rather than the runtime.
    //
    // What is load-bearing is that the *region* is supplied: dropping it and
    // passing the bare language changes the output, which is exactly the bug of
    // feeding `zh-Hant` straight through.
    const iso = '2026-10-02T13:05:00+08:00';
    const at = (tag: string) =>
      new Date(iso).toLocaleDateString(tag, {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
      });
    expect(at(formatLocaleFor('zh-Hant'))).not.toBe(at('zh'));
    expect(at(formatLocaleFor('zh-Hant'))).not.toBe(at('zh-TW'));
  });
});
