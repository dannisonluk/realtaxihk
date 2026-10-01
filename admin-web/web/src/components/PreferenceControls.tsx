/**
 * The console's two session-wide preferences, as one file: **theme** and
 * **language**.
 *
 * They live together because they are the same *kind* of control — a small
 * three-or-two-way segmented switch pinned at the foot of the sidebar, reached
 * once and then left alone — and because keeping them adjacent is what stops one
 * growing a different visual language from the other.
 *
 * Both are preferences, not navigation or authority: neither touches the API,
 * neither is role-gated, and both survive a sign-out. A shared machine may be
 * used by two operators in a shift, and re-picking "English, dark" every time
 * would be the sort of small tax that ends with people leaving the browser in a
 * state nobody chose.
 *
 * **Neither control changes width when the other does.** That is the concrete
 * constraint the glyph choices below exist to satisfy: the segments are icons or
 * two-letter codes, never words, so switching language cannot reflow the switch
 * that switched it — the exact defect this pass is meant to remove.
 */

import { useTheme, THEME_MODES, type ThemeMode } from '../lib/theme';
import { useI18n, LOCALES, type Locale } from '../i18n';

const THEME_GLYPH: Record<ThemeMode, string> = {
  system: '\u25D0', // ◐ half-filled circle — "let the system decide"
  light: '\u2600', // ☀
  dark: '\u263D', // ☽
};

/**
 * The language segments.
 *
 * `中` / `EN` are the two forms a Hong Kong operator recognises instantly, and —
 * unlike "繁體中文" / "English" — they are the same width *in both locales*.
 */
const LOCALE_GLYPH: Record<Locale, string> = {
  'zh-Hant': '中',
  en: 'EN',
};

export function ThemeSwitch() {
  const { mode, setMode } = useTheme();
  const { t } = useI18n();

  return (
    <fieldset className="pref">
      <legend className="sr-only">{t('prefs.theme')}</legend>
      <div className="segmented" role="group" aria-label={t('prefs.theme')}>
        {THEME_MODES.map((value) => (
          <button
            key={value}
            type="button"
            className="segmented__btn"
            aria-pressed={mode === value}
            title={t(`prefs.theme.${value}`)}
            onClick={() => setMode(value)}
          >
            <span aria-hidden="true">{THEME_GLYPH[value]}</span>
            <span className="sr-only">{t(`prefs.theme.${value}`)}</span>
          </button>
        ))}
      </div>
    </fieldset>
  );
}

export function LanguageSwitch() {
  const { locale, setLocale, t } = useI18n();

  return (
    <fieldset className="pref">
      <legend className="sr-only">{t('prefs.language')}</legend>
      <div className="segmented" role="group" aria-label={t('prefs.language')}>
        {LOCALES.map((value) => (
          <button
            key={value}
            type="button"
            className="segmented__btn"
            aria-pressed={locale === value}
            // The accessible name is the language's own endonym ("繁體中文" /
            // "English"), read from the *current* locale's resources — so it is
            // always in a language the reader can parse, and it names the
            // language rather than translating it.
            title={t(`prefs.language.${value}`)}
            onClick={() => setLocale(value)}
          >
            <span aria-hidden="true">{LOCALE_GLYPH[value]}</span>
            <span className="sr-only">{t(`prefs.language.${value}`)}</span>
          </button>
        ))}
      </div>
    </fieldset>
  );
}

/** Both preferences as one block, for the sidebar footer. */
export function PreferenceControls() {
  return (
    <div className="prefs">
      <ThemeSwitch />
      <LanguageSwitch />
    </div>
  );
}
