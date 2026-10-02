/**
 * Cold-start `<html lang>`: the console boots into a **Traditional Chinese**
 * preference.
 *
 * The sibling of `htmlLang.cold-start-en.test.ts`, and separate for the reason
 * documented there: a module's startup path can only be evaluated once per file,
 * so each boot case needs its own module graph. This one covers the *other*
 * half — that the startup write is not accidentally hard-coded to English, and
 * that the default locale reaches the attribute too rather than leaving it as
 * whatever the HTML file happened to say.
 */

import { afterEach, describe, expect, it } from 'vitest';

const LOCALE_KEY = 'realtaxihk.console.locale';

localStorage.setItem(LOCALE_KEY, 'zh-Hant');
document.documentElement.lang = '';

const { i18n } = await import('./index');

describe('cold start with a stored Traditional Chinese preference', () => {
  afterEach(() => {
    localStorage.removeItem(LOCALE_KEY);
  });

  it('resolves to Traditional Chinese', () => {
    expect(i18n.resolvedLanguage).toBe('zh-Hant');
  });

  it('writes <html lang> from the startup path', () => {
    expect(document.documentElement.lang).toBe('zh-Hant');
  });

  it('renders a Chinese string, so the language really is active', () => {
    expect(i18n.t('login.title')).toBe('管理員登入');
  });
});
