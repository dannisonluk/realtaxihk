/**
 * The console's session-wide display preferences, as one file. Today that is
 * **theme**; the language switch joins it in the i18n phase, and the two live
 * together because they are the same *kind* of control — a small three-or-two-
 * way segmented switch pinned at the foot of the sidebar, reached once and then
 * left alone. Keeping them adjacent is what stops one growing a different visual
 * language from the other.
 *
 * A preference, not navigation or authority: it touches no API, is not
 * role-gated, and survives a sign-out. A shared machine may be used by two
 * operators in a shift, and re-picking the theme every morning would be the sort
 * of small tax that ends with the browser left in a state nobody chose.
 */

import { useTheme, THEME_MODES, type ThemeMode } from '../lib/theme';

/**
 * The theme segments. Each is an icon rather than a word, because the three
 * labels ("System" / "Light" / "Dark") differ in width so much between locales
 * that a text version would resize the sidebar when the language changed — the
 * exact class of defect this pass exists to remove. The accessible name carries
 * the word; the pixels carry a glyph.
 */
const THEME_GLYPH: Record<ThemeMode, string> = {
  system: '\u25D0', // ◐ half-filled circle — "let the system decide"
  light: '\u2600', // ☀
  dark: '\u263D', // ☽
};

const THEME_LABEL: Record<ThemeMode, string> = {
  system: 'Theme: follow system',
  light: 'Theme: light',
  dark: 'Theme: dark',
};

export function ThemeSwitch() {
  const { mode, setMode } = useTheme();

  return (
    <fieldset className="pref">
      <legend className="sr-only">Theme</legend>
      <div className="segmented" role="group" aria-label="Theme">
        {THEME_MODES.map((value) => (
          <button
            key={value}
            type="button"
            className="segmented__btn"
            aria-pressed={mode === value}
            title={THEME_LABEL[value]}
            onClick={() => setMode(value)}
          >
            <span aria-hidden="true">{THEME_GLYPH[value]}</span>
            <span className="sr-only">{THEME_LABEL[value]}</span>
          </button>
        ))}
      </div>
    </fieldset>
  );
}

/** The preference block, for the sidebar footer. */
export function PreferenceControls() {
  return (
    <div className="prefs">
      <ThemeSwitch />
    </div>
  );
}
