/**
 * The console's i18n entry point.
 *
 * Two locales, both shipped in the bundle: Traditional Chinese (`zh-Hant`, the
 * default and the language the platform's business actually runs in) and English.
 *
 * **The resources are typed, not stringly-typed.** `en.ts` is declared to have
 * the exact shape of `zh-Hant.ts` (`satisfies typeof zhHant`), so a key that
 * exists in Chinese and not in English is a *compile error*. This is the whole
 * reason the layer is worth having: with an untyped `t('some.key')`, a typo or a
 * forgotten translation silently falls back — usually to the key itself, which
 * ships a raw dotted string to an operator. Here the failure is `tsc`, before
 * anything is built.
 *
 * A flat `t` is used at call sites (`t('nav.orders')`). The nesting is only in
 * the resource files, and i18next's default `.`-separated key resolution means
 * one string per call — no namespace arguments, no `useTranslation('x')` in
 * every component.
 */

import i18n from 'i18next';
import { initReactI18next, useTranslation } from 'react-i18next';
import { useCallback } from 'react';
import { zhHant } from './locales/zh-Hant';
import { en } from './locales/en';

export const LOCALES = ['zh-Hant', 'en'] as const;
export type Locale = (typeof LOCALES)[number];

/** The locale used when nothing is stored and the browser asks for neither. */
export const DEFAULT_LOCALE: Locale = 'zh-Hant';

/** The BCP-47 tags handed to `Intl` / `toLocaleDateString`. Kept apart from the
 * i18next locale names because `zh-Hant` needs a region (`zh-HK`) for a correct
 * date format, while the resource key does not. */
const FORMAT_LOCALE: Record<Locale, string> = {
  'zh-Hant': 'zh-HK',
  en: 'en-HK',
};

const STORAGE_KEY = 'realtaxihk.console.locale';

function isLocale(value: unknown): value is Locale {
  return value === 'zh-Hant' || value === 'en';
}

/**
 * Resolve the starting locale: an explicit choice wins, then the browser.
 *
 * `navigator.language` is matched on its primary subtag, so `zh-TW`, `zh-HK`
 * and `zh-Hant-TW` all land on `zh-Hant` and every flavour of `en` (including
 * `en-GB`) lands on `en`. Anything else — `ja`, `fr` — falls to the default
 * rather than guessing: a Japanese operator is better served by either of the
 * two real translations than by a machine-guessed third.
 */
export function resolveInitialLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (isLocale(stored)) return stored;
  } catch {
    /* Storage denied — fall through to detection, then the default. */
  }
  const nav = typeof navigator !== 'undefined' ? navigator.language : '';
  if (/^en\b/i.test(nav)) return 'en';
  if (/^zh\b/i.test(nav)) return 'zh-Hant';
  return DEFAULT_LOCALE;
}

export function formatLocaleFor(locale: Locale): string {
  return FORMAT_LOCALE[locale];
}

/**
 * Initialise i18next once, at module load.
 *
 * `initAsync: false` matters: with the default (`true`), i18next defers
 * initialisation to a microtask and the first `t()` call can return the key
 * before the bundled resources are in place. With it off, the synchronous
 * bundled resources are ready before `init` returns, so the first paint is
 * already translated. (i18next renamed this from `initImmediate` in v23; v26
 * only accepts `initAsync`.)
 */
void i18n.use(initReactI18next).init({
  resources: {
    'zh-Hant': { translation: zhHant },
    en: { translation: en },
  },
  lng: resolveInitialLocale(),
  fallbackLng: DEFAULT_LOCALE,
  // `false` for the flat `t('nav.orders')` style: keys are literal, not plural
  // templates split on `.`.
  keySeparator: '.',
  nsSeparator: false,
  initAsync: false,
  interpolation: {
    // React already escapes everything it renders (`primitives.tsx` has no
    // `dangerouslySetInnerHTML` anywhere). Letting i18next escape as well would
    // double-encode quotes in a fleet name shown inside a sentence.
    escapeValue: false,
  },
  returnNull: false,
});

/**
 * Write the active locale onto `<html lang>`.
 *
 * This drives hyphenation, the CJK-vs-Latin font fallback, and the `:lang()`
 * rules in `styles.css` — including `:lang(en) .t-section`, which sets Latin
 * letter-spacing. If the attribute is stale, all of those are wrong while the
 * visible text is right, which is exactly the kind of defect a screenshot does
 * not show and a language-switch smoke test does not catch.
 */
function syncHtmlLang(locale: string) {
  if (typeof document !== 'undefined') {
    document.documentElement.lang = locale;
  }
}

i18n.on('languageChanged', syncHtmlLang);

// And once for the *initial* locale.
//
// `languageChanged` fires on a change, not on `init`. With a stored preference
// of `en`, `init({ lng: 'en' })` sets the language silently, the listener above
// never runs, and `<html lang>` stays at the `zh-Hant` the HTML file was
// authored with — so the console rendered correct English while telling every
// downstream consumer it was Chinese. Registering the listener is not enough;
// the starting value has to be applied too.
syncHtmlLang(i18n.resolvedLanguage ?? DEFAULT_LOCALE);

export interface I18nController {
  /** Translate. Accepts interpolation values as the second argument. */
  t: (key: string, options?: Record<string, unknown>) => string;
  locale: Locale;
  setLocale: (locale: Locale) => void;
  /** The `Intl` tag for this locale — for `toLocaleDateString` and friends. */
  formatLocale: string;
}

/**
 * The one hook components use.
 *
 * It wraps `useTranslation` rather than re-exporting it so that `locale` and
 * `setLocale` come from the same place as `t`, and — importantly — so a locale
 * change *re-renders* the caller. react-i18next does that by default via its
 * `languageChanged` subscription inside `useTranslation`; this delegates to it
 * rather than duplicating the subscription, which is what a hand-rolled
 * `useState` + `i18n.on(...)` would risk getting subtly wrong.
 */
export function useI18n(): I18nController {
  const { t, i18n: instance } = useTranslation();
  const locale = (instance.resolvedLanguage ?? DEFAULT_LOCALE) as Locale;

  const setLocale = useCallback(
    (next: Locale) => {
      void instance.changeLanguage(next);
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        /* A locale that will not persist is still a locale that works. */
      }
    },
    [instance],
  );

  return {
    t: t as I18nController['t'],
    locale,
    setLocale,
    formatLocale: formatLocaleFor(locale),
  };
}

/** The raw instance, for the rare module-level caller outside React. */
export { i18n };
