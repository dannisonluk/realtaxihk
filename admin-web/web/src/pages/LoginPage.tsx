/**
 * Admin sign-in: username + password, then a TOTP code.
 *
 * Why this is not the OTP screen any more
 * ---------------------------------------
 * Admins live in `admin_accounts`, not `users`, and they authenticate with a
 * password plus a second factor rather than a phone OTP. The old screen posted
 * to `/api/v1/auth/otp/verify` and then checked `role === 'ADMIN'`, which was
 * the whole of admin authorisation — a phone number and an SMS code, with no
 * password and nothing that survives a SIM swap.
 *
 * The three steps, matching `app/api/admin_auth.py`
 * ------------------------------------------------
 *   1. `/login`          — password. Returns a **challenge**, never a token.
 *   2. `/totp/verify`    — 6-digit code from the authenticator app.
 *      `/recovery`       — or a single-use recovery code, if the phone is lost.
 *   3. `/totp/enrol/confirm` — first login only: prove one code from the
 *      secret we just showed, and only then is it stored.
 *
 * The password is held in component state for the length of the enrolment step
 * and never persisted — `session.save` is called only once a real access token
 * exists, so a half-finished sign-in leaves nothing behind.
 */

import { useEffect, useRef, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { endpoints } from '../api/endpoints';
import { session } from '../api/session';
import type { AdminEnrolment, AdminLoginResult } from '../api/types';
import { useApp } from '../app/AppContext';
import { Message } from '../components/primitives';

type Step = 'credentials' | 'totp' | 'enrol' | 'recovery';

/** Where to send the operator for a TOTP app. Plain links, no tracking. */
const AUTHENTICATOR_APPS = [
  { name: 'Google Authenticator', url: 'https://support.google.com/accounts/answer/1066447' },
  { name: 'Microsoft Authenticator', url: 'https://www.microsoft.com/security/mobile-authenticator-app' },
  { name: 'FreeOTP', url: 'https://freeotp.github.io/' },
];

export function LoginPage({ onSignedIn }: { onSignedIn: () => void }) {
  const { client, notify } = useApp();

  const [step, setStep] = useState<Step>('credentials');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [challenge, setChallenge] = useState('');
  const [enrolment, setEnrolment] = useState<AdminEnrolment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [codesAcknowledged, setCodesAcknowledged] = useState(false);

  const codeRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (step === 'totp' || step === 'enrol' || step === 'recovery') codeRef.current?.focus();
  }, [step]);

  /** Store the session and hand control back to the shell. */
  function finish(body: { access_token: string; admin: unknown }) {
    session.save({
      accessToken: body.access_token,
      user: body.admin as ReturnType<typeof session.save>['user'],
    });
    onSignedIn();
  }

  async function submitCredentials() {
    setError(null);
    if (username.trim().length === 0 || password.length === 0) {
      setError('請輸入使用者名稱及密碼。');
      return;
    }
    setBusy(true);
    try {
      const body: AdminLoginResult = await endpoints.auth.adminLogin(
        client,
        username.trim(),
        password,
      );
      setChallenge(body.challenge_token);
      // The password has done its job; drop it rather than keep a credential in
      // memory for the rest of the flow.
      setPassword('');
      if (body.next === 'enrolment_required' && body.enrolment) {
        setEnrolment(body.enrolment);
        setStep('enrol');
      } else {
        setStep('totp');
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '登入失敗。');
    } finally {
      setBusy(false);
    }
  }

  async function submitTotp() {
    setError(null);
    const value = code.trim();
    if (!/^\d{6}$/.test(value)) {
      setError('請輸入 6 位數字驗證碼。');
      return;
    }
    setBusy(true);
    try {
      const body = await endpoints.auth.adminVerifyTotp(client, challenge, value);
      finish(body);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '驗證失敗。');
      setCode('');
    } finally {
      setBusy(false);
    }
  }

  async function submitEnrolment() {
    setError(null);
    const value = code.trim();
    if (!/^\d{6}$/.test(value)) {
      setError('請輸入 6 位數字驗證碼。');
      return;
    }
    setBusy(true);
    try {
      const body = await endpoints.auth.adminConfirmEnrolment(client, challenge, value);
      finish(body);
      notify('雙重驗證已啟用。');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '驗證失敗。');
      setCode('');
    } finally {
      setBusy(false);
    }
  }

  async function submitRecovery() {
    setError(null);
    const value = code.trim();
    if (value.length < 8) {
      setError('請輸入完整的備用碼。');
      return;
    }
    setBusy(true);
    try {
      const body = await endpoints.auth.adminUseRecovery(client, challenge, value);
      finish(body);
      notify('已使用備用碼登入。請盡快重新產生備用碼。');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '備用碼無效。');
      setCode('');
    } finally {
      setBusy(false);
    }
  }

  function signOut() {
    session.clear();
    setStep('credentials');
    setChallenge('');
    setEnrolment(null);
    setCode('');
    setPassword('');
    setError(null);
    setCodesAcknowledged(false);
  }

  return (
    <div className="login">
      <div className="login__card card">
        <div className="brand" style={{ padding: '0 0 18px' }}>
          <div className="brand__mark" aria-hidden="true">
            R
          </div>
          <div className="brand__text">
            <div className="brand__title">RealTaxi HK</div>
            <div className="brand__sub">管理後台</div>
          </div>
        </div>

        <h1 className="t-title1" style={{ margin: '0 0 4px' }}>
          管理員登入
        </h1>
        <p className="login__sub dim">供平台管理團隊及 admin 團隊使用。</p>

        {step === 'credentials' ? (
          <div className="stack" style={{ marginTop: 20 }}>
            <div className="field">
              <label className="field__label" htmlFor="login-username">
                使用者名稱
              </label>
              <input
                id="login-username"
                type="text"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void submitCredentials();
                }}
              />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="login-password">
                密碼
              </label>
              <input
                id="login-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void submitCredentials();
                }}
              />
              <div className="t-footnote dim">
                連續 5 次失敗會鎖定帳戶 15 分鐘。
              </div>
            </div>
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy}
              onClick={() => void submitCredentials()}
            >
              {busy ? '登入中…' : '下一步'}
            </button>
          </div>
        ) : null}

        {step === 'totp' ? (
          <div className="stack" style={{ marginTop: 20 }}>
            <div className="field">
              <label className="field__label" htmlFor="login-code">
                6 位驗證碼
              </label>
              <input
                id="login-code"
                ref={codeRef}
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                placeholder="000000"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void submitTotp();
                }}
              />
              <div className="t-footnote dim">請開啟驗證器應用程式查看即時驗證碼。</div>
            </div>
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy}
              onClick={() => void submitTotp()}
            >
              {busy ? '驗證中…' : '登入'}
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => {
                setCode('');
                setError(null);
                setStep('recovery');
              }}
            >
              無法使用驗證器？改用備用碼
            </button>
            <button type="button" className="btn" disabled={busy} onClick={signOut}>
              返回
            </button>
          </div>
        ) : null}

        {step === 'recovery' ? (
          <div className="stack" style={{ marginTop: 20 }}>
            <div className="field">
              <label className="field__label" htmlFor="login-recovery">
                備用碼
              </label>
              <input
                id="login-recovery"
                ref={codeRef}
                type="text"
                autoComplete="off"
                placeholder="XXXXX-XXXXX"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void submitRecovery();
                }}
              />
              <div className="t-footnote dim">每組備用碼只能使用一次。</div>
            </div>
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy}
              onClick={() => void submitRecovery()}
            >
              {busy ? '驗證中…' : '以備用碼登入'}
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => {
                setCode('');
                setError(null);
                setStep('totp');
              }}
            >
              返回驗證碼
            </button>
          </div>
        ) : null}

        {step === 'enrol' && enrolment ? (
          <div className="stack" style={{ marginTop: 20 }}>
            <Message tone="warn">
              首次登入需要綁定驗證器應用程式。請以應用程式掃描下方二維碼，或手動輸入密鑰。
            </Message>

            {/*
              The QR encodes the same `otpauth://` URI shown as text below, so
              scanning and typing the secret are equivalent paths to one result.
              It is rendered client-side as SVG — the provisioning URI contains
              the TOTP secret, so posting it to an image service (or any third
              party) would hand out the second factor. Nothing here leaves the
              browser.

              Sized via CSS pixels rather than a fixed `size` prop alone so it
              stays scannable on a HiDPI screen: `qrcode.react` emits SVG, which
              is resolution-independent, so the only thing that matters is that
              the on-screen box is large enough for a phone camera to resolve.
              `marginSize` gives the quiet zone the spec requires — without it
              many scanners fail on a code that touches its own border.
            */}
            <div className="qr-enrol">
              <div
                className="qr-enrol__code"
                data-testid="totp-qr"
                role="img"
                aria-label="驗證器綁定二維碼"
              >
                <QRCodeSVG
                  value={enrolment.otpauth_uri}
                  size={176}
                  marginSize={2}
                  level="M"
                  bgColor="#ffffff"
                  fgColor="#000000"
                />
              </div>
              <div className="t-footnote dim">以驗證器應用程式掃描此二維碼</div>
            </div>

            <div className="field">
              <div className="field__label">設定密鑰</div>
              <input readOnly value={enrolment.secret} onFocus={(e) => e.target.select()} />
            </div>

            <div className="field">
              <div className="field__label">設定連結</div>
              <input readOnly value={enrolment.otpauth_uri} onFocus={(e) => e.target.select()} />
            </div>

            <div className="field">
              <div className="field__label">備用碼（請立即抄錄）</div>
              <div className="mono" data-testid="recovery-codes">
                {enrolment.recovery_codes.map((value) => (
                  <div key={value}>{value}</div>
                ))}
              </div>
              <div className="t-footnote dim">
                每組只能使用一次，遺失後無法再查看。此畫面關閉後將不再顯示。
              </div>
            </div>

            <label className="field" style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
              <input
                type="checkbox"
                checked={codesAcknowledged}
                onChange={(event) => setCodesAcknowledged(event.target.checked)}
              />
              <span>我已保存上述備用碼。</span>
            </label>

            <div className="field">
              <label className="field__label" htmlFor="login-enrol-code">
                輸入驗證器顯示的 6 位驗證碼
              </label>
              <input
                id="login-enrol-code"
                ref={codeRef}
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                placeholder="000000"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void submitEnrolment();
                }}
              />
            </div>

            <button
              type="button"
              className="btn btn--primary"
              disabled={busy || !codesAcknowledged}
              onClick={() => void submitEnrolment()}
            >
              {busy ? '啟用中…' : '啟用雙重驗證並登入'}
            </button>
            <button type="button" className="btn" disabled={busy} onClick={signOut}>
              取消
            </button>

            <div className="t-footnote dim">
              沒有驗證器應用程式？
              {AUTHENTICATOR_APPS.map((app, index) => (
                <span key={app.url}>
                  {index > 0 ? '、' : ' '}
                  <a href={app.url} target="_blank" rel="noreferrer noopener">
                    {app.name}
                  </a>
                </span>
              ))}
            </div>
          </div>
        ) : null}

        {error ? (
          <div style={{ marginTop: 16 }} role="alert">
            <Message tone="error" className="dialog__error">
              {error}
            </Message>
          </div>
        ) : null}

        <div className="login__foot t-footnote dim" style={{ marginTop: 20 }}>
          登入憑證只儲存在此分頁的 session storage，關閉分頁即失效。
        </div>
      </div>
    </div>
  );
}

/** Re-exported so a view can show the same "not an admin" dialog if needed. */
export function notAnAdminDialog() {
  // Kept for the callers that still reference it; the console no longer routes
  // a non-admin here, because `/admin/auth/login` refuses them at step 1.
}
