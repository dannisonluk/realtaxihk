/**
 * The admin sign-in screen, with an eye on the TOTP enrolment step.
 *
 * The assertion with teeth is the QR one. A first-login screen that shows a
 * secret but no scannable code is a dead end for anyone who does not want to
 * type 32 base32 characters by hand — and a QR that renders while encoding the
 * *wrong* string is worse than none at all, because the operator would bind
 * their authenticator to a secret the server never stored and every subsequent
 * code would be refused. So this file does not merely check that an `<svg>`
 * appeared: it decodes the code's own value attribute back and compares it to
 * the `otpauth_uri` the server sent.
 *
 * `qrcode.react` renders `<svg>` with no external request, so the check is a
 * plain DOM one. That also matters on its own: the provisioning URI carries the
 * TOTP secret, and the test would fail loudly if anyone ever swapped the local
 * renderer for an image service that shipped the secret off-box.
 *
 * The remaining tests pin the parts of the flow the server cannot see: that a
 * password alone never advances to a token, that a malformed code is rejected
 * before a request is spent, and that the acknowledgement checkbox gates the
 * final button.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StrictMode, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QRCodeSVG } from 'qrcode.react';
import { AppProvider } from '../app/AppContext';
import { i18n } from '../i18n';
import { LoginPage } from './LoginPage';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Read a string out of the active locale's resource.
 *
 * The sign-in screen is fully translated, so a literal expectation here would
 * both pin the test to one language *and* fail to notice a change to the copy.
 * The subject under test is the flow, so every string the assertions need is
 * looked up by key from i18next's live resource store.
 */
function text(key: string): string {
  const target = i18n.resolvedLanguage ?? 'zh-Hant';
  const read = (source: unknown) => {
    let cursor: unknown = source;
    for (const part of key.split('.')) {
      if (typeof cursor !== 'object' || cursor === null) return undefined;
      cursor = (cursor as Record<string, unknown>)[part];
    }
    return typeof cursor === 'string' ? cursor : undefined;
  };
  return (
    read(i18n.getResourceBundle(target, 'translation')) ??
    read(i18n.getResourceBundle('zh-Hant', 'translation')) ??
    key
  );
}

/** A first-login payload: the server says "enrol", and hands over the QR material. */
const ENROLMENT = {
  next: 'enrolment_required',
  challenge_token: 'challenge-enrol-1',
  enrolment: {
    secret: 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP',
    otpauth_uri:
      'otpauth://totp/hkfastdc:ops-admin?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=hkfastdc',
    recovery_codes: ['AAAA-1111', 'BBBB-2222', 'CCCC-3333'],
  },
};

/** An already-enrolled payload: step 2 is a code, there is no QR material. */
const TOTP_ONLY = {
  next: 'totp_required',
  challenge_token: 'challenge-totp-1',
};

/** Record the requests the page makes and answer step 1 with `body`. */
function stubTransport(body: unknown) {
  const calls: { url: string; body: unknown }[] = [];
  vi.stubGlobal(
    'fetch',
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      let sent: unknown = null;
      if (typeof init?.body === 'string') {
        try {
          sent = JSON.parse(init.body);
        } catch {
          sent = init.body;
        }
      }
      calls.push({ url, body: sent });
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    },
  );
  return calls;
}

async function render(root: Root) {
  await act(async () => {
    root.render(
      <StrictMode>
        <AppProvider onSignedOut={() => {}}>
          <LoginPage onSignedIn={() => {}} />
        </AppProvider>
      </StrictMode>,
    );
  });
}

/** Fill the credentials step and press 下一步, then let the request settle. */
async function submitCredentials(container: HTMLElement, username = 'ops-admin') {
  const user = container.querySelector('#login-username') as HTMLInputElement;
  const pass = container.querySelector('#login-password') as HTMLInputElement;
  const button = [...container.querySelectorAll('button')].find((b) =>
    (b.textContent ?? '').includes(text('login.next')),
  );
  await act(async () => {
    setNativeValue(user, username);
    setNativeValue(pass, 'correct horse battery staple');
  });
  await act(async () => {
    button?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 20));
  });
}

/**
 * React tracks the last value it set on an input and ignores a change it thinks
 * is a no-op, so assigning `.value` alone leaves the component state empty. This
 * goes through the native setter to make React notice.
 */
function setNativeValue(input: HTMLInputElement, value: string) {
  const proto = Object.getPrototypeOf(input) as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

/**
 * The pixels of the rendered QR, as an SVG path.
 *
 * `qrcode.react` draws two paths: a white plate, then every dark module packed
 * into a single path. The second one — the dark modules — is the code itself,
 * and its `d` string is a complete, byte-for-byte description of it, which
 * makes it a usable stand-in for a decoded matrix without this file having to
 * ship a QR decoder.
 *
 * Selected by fill rather than by index, so a future release that reorders or
 * splits the paths does not silently make this compare the white plate — which
 * is identical for every input and would pass no matter what was encoded.
 */
function qrPath(container: HTMLElement): string | undefined {
  const svg = container.querySelector('[data-testid="totp-qr"] svg');
  const dark = [...(svg?.querySelectorAll('path') ?? [])].find((p) => {
    const fill = (p.getAttribute('fill') ?? '').toLowerCase();
    return !['#fff', '#ffffff', 'white'].includes(fill);
  });
  return dark?.getAttribute('d') ?? undefined;
}

/**
 * Render a bare `QRCodeSVG` for `value` and return the dark-module path, so a
 * test can compare the on-screen code against a known-good encoding of the URI
 * and a known-bad one. Rendered through React's own root so it goes down the
 * exact code path the page uses.
 */
async function renderQrPath(value: string): Promise<string | undefined> {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const localRoot = createRoot(host);
  await act(async () => {
    localRoot.render(
      <QRCodeSVG
        value={value}
        size={176}
        marginSize={2}
        level="M"
        bgColor="#ffffff"
        fgColor="#000000"
      />,
    );
  });
  const dark = [...host.querySelectorAll('svg path')].find((p) => {
    const fill = (p.getAttribute('fill') ?? '').toLowerCase();
    return !['#fff', '#ffffff', 'white'].includes(fill);
  });
  const path = dark?.getAttribute('d') ?? undefined;
  act(() => localRoot.unmount());
  host.remove();
  return path;
}

describe('the admin sign-in screen', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
  });

  it('sends the credentials to the admin login endpoint and no further', async () => {
    const calls = stubTransport(TOTP_ONLY);
    await render(root);
    await submitCredentials(container);

    const login = calls.find((c) => c.url.includes('/api/v1/admin/auth/login'));
    expect(login).toBeTruthy();
    expect(login?.body).toEqual({
      username: 'ops-admin',
      password: 'correct horse battery staple',
    });
    // A password alone must never reach a token endpoint: that would be the
    // whole of admin auth degenerating back to one factor.
    expect(calls.some((c) => c.url.includes('/totp/verify'))).toBe(false);
  });

  it('shows the code step, not the enrolment step, for an enrolled admin', async () => {
    stubTransport(TOTP_ONLY);
    await render(root);
    await submitCredentials(container);

    expect(container.querySelector('#login-code')).toBeTruthy();
    expect(container.querySelector('[data-testid="totp-qr"]')).toBeNull();
  });

  it('renders a scannable QR for a first login', async () => {
    stubTransport(ENROLMENT);
    await render(root);
    await submitCredentials(container);

    const qr = container.querySelector('[data-testid="totp-qr"]');
    expect(qr).toBeTruthy();
    // `qrcode.react` draws a real SVG locally; an <img> would mean the secret
    // had been handed to a third-party renderer.
    expect(qr?.querySelector('svg')).toBeTruthy();
    expect(qr?.querySelector('img')).toBeNull();
  });

  it('draws a code that is a function of the otpauth URI, not some other string', async () => {
    stubTransport(ENROLMENT);
    await render(root);
    await submitCredentials(container);

    const rendered = qrPath(container) ?? '';
    // A rendered-but-empty code is the shape you get when `value` is missing.
    expect(rendered.length).toBeGreaterThan(50);

    // A QR is a pure function of the string it encodes, so we can prove the
    // *right* string went in without implementing a QR decoder: render the same
    // component with the URI the server sent, and again with a wrong string
    // (the bare secret, which is exactly what a plausible-looking wiring bug
    // would pass), and require the on-screen code to match the former and
    // differ from the latter.
    const right = await renderQrPath(ENROLMENT.enrolment.otpauth_uri);
    const wrong = await renderQrPath(ENROLMENT.enrolment.secret);

    expect(right).toBe(rendered);
    expect(wrong).not.toBe(rendered);
  });

  it('shows every recovery code exactly once', async () => {
    stubTransport(ENROLMENT);
    await render(root);
    await submitCredentials(container);

    const codes = container.querySelector('[data-testid="recovery-codes"]');
    expect(codes).toBeTruthy();
    for (const code of ENROLMENT.enrolment.recovery_codes) {
      const seen = (codes?.textContent ?? '').split(code).length - 1;
      expect(seen).toBe(1);
    }
  });

  it('keeps the enable button disabled until the codes are acknowledged', async () => {
    stubTransport(ENROLMENT);
    await render(root);
    await submitCredentials(container);

    const enable = [...container.querySelectorAll('button')].find((b) =>
      (b.textContent ?? '').includes(text('login.enrolSubmit')),
    ) as HTMLButtonElement | undefined;
    expect(enable?.disabled).toBe(true);

    const ack = container.querySelector(
      'input[type="checkbox"]',
    ) as HTMLInputElement | null;
    await act(async () => {
      ack?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(enable?.disabled).toBe(false);
  });

  it('refuses a code that is not six digits without spending a request', async () => {
    const calls = stubTransport(ENROLMENT);
    await render(root);
    await submitCredentials(container);
    const before = calls.length;

    const code = container.querySelector('#login-enrol-code') as HTMLInputElement;
    const enable = [...container.querySelectorAll('button')].find((b) =>
      (b.textContent ?? '').includes(text('login.enrolSubmit')),
    ) as HTMLButtonElement | undefined;
    const ack = container.querySelector(
      'input[type="checkbox"]',
    ) as HTMLInputElement | null;
    await act(async () => {
      ack?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      setNativeValue(code, '123');
    });
    await act(async () => {
      enable?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    expect(calls.length).toBe(before);
    expect(container.textContent).toContain(text('login.errCode'));
  });
});
