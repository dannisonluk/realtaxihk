/**
 * Admin sign-in.
 *
 * Two steps, matching the API: request a code, then verify it. Signup always
 * creates a PASSENGER (`app/services/otp_service.py`), so an admin account has to
 * be granted out of band — `scripts/create_admin.py`. This screen therefore
 * checks the role and refuses a non-admin rather than letting them in to watch
 * every request 403.
 */

import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { endpoints } from '../api/endpoints';
import { session } from '../api/session';
import { useApp } from '../app/AppContext';
import { Message } from '../components/primitives';

const PHONE_PATTERN = /^\+852\d{8}$/;

export function LoginPage({ onSignedIn }: { onSignedIn: () => void }) {
  const { client, notify } = useApp();

  const [phone, setPhone] = useState('+852');
  const [code, setCode] = useState('');
  const [step, setStep] = useState<'phone' | 'code'>('phone');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** Seconds left before a resend is allowed. 0 means the button is live. */
  const [cooldown, setCooldown] = useState(0);

  const codeRef = useRef<HTMLInputElement>(null);
  const cooldownTimer = useRef<number | null>(null);

  useEffect(() => {
    if (step === 'code') codeRef.current?.focus();
  }, [step]);

  // The resend cooldown is a 60s lookback over `otp_codes` server-side, not a
  // rate-limit window, and it reports `details.retry_after_seconds`. Counting it
  // down is why the backend had to stop discarding `details` — see the
  // `BusinessRuleError` branch in `app/api/auth.py`.
  useEffect(() => {
    if (cooldown <= 0) return;
    cooldownTimer.current = window.setTimeout(() => setCooldown((n) => n - 1), 1000);
    return () => {
      if (cooldownTimer.current !== null) window.clearTimeout(cooldownTimer.current);
    };
  }, [cooldown]);

  async function requestCode() {
    setError(null);
    if (!PHONE_PATTERN.test(phone)) {
      setError('手機號碼格式應為 +852 加上 8 位數字，例如 +85290000001。');
      return;
    }
    setBusy(true);
    try {
      await endpoints.auth.requestOtp(client, phone);
      setStep('code');
      setCooldown(60);
      notify('驗證碼已發送。');
    } catch (cause) {
      const retryAfter =
        cause instanceof ApiError ? Number(cause.details['retry_after_seconds'] ?? 0) : 0;
      if (retryAfter > 0) {
        // Already requested: move on to the code step and honour the cooldown
        // rather than dead-ending the operator.
        setStep('code');
        setCooldown(Math.ceil(retryAfter));
      }
      setError(cause instanceof Error ? cause.message : '無法發送驗證碼。');
    } finally {
      setBusy(false);
    }
  }

  async function verifyCode() {
    setError(null);
    if (code.trim().length === 0) {
      setError('請輸入驗證碼。');
      return;
    }
    setBusy(true);
    try {
      const body = await endpoints.auth.verifyOtp(client, phone, code.trim());
      if (body.user.role !== 'ADMIN') {
        // The server would 403 every admin route. Refuse here so the reason is a
        // sentence rather than a console full of failures.
        setError('此帳戶沒有管理權限。請聯絡平台管理員開通。');
        setCode('');
        return;
      }
      session.save({
        accessToken: body.access_token,
        refreshToken: body.refresh_token,
        user: body.user,
      });
      onSignedIn();
    } catch (cause) {
      const remaining =
        cause instanceof ApiError ? cause.details['attempts_remaining'] : undefined;
      setError(
        remaining === undefined
          ? (cause instanceof Error ? cause.message : '驗證失敗。')
          : `${(cause as Error).message}（剩餘 ${String(remaining)} 次嘗試）`,
      );
    } finally {
      setBusy(false);
    }
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

        {step === 'phone' ? (
          <div className="stack" style={{ marginTop: 20 }}>
            <div className="field">
              <label className="field__label" htmlFor="login-phone">
                管理員手機號碼
              </label>
              <input
                id="login-phone"
                type="tel"
                inputMode="tel"
                autoComplete="username"
                placeholder="+85290000001"
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void requestCode();
                }}
              />
              <div className="t-footnote dim">格式 +852 加上 8 位數字。</div>
            </div>
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy}
              onClick={() => void requestCode()}
            >
              {busy ? '發送中…' : '發送驗證碼'}
            </button>
          </div>
        ) : (
          <div className="stack" style={{ marginTop: 20 }}>
            <div className="field">
              <label className="field__label" htmlFor="login-code">
                驗證碼
              </label>
              <input
                id="login-code"
                ref={codeRef}
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                placeholder="6 位驗證碼"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void verifyCode();
                }}
              />
              <div className="t-footnote dim">開發環境的固定驗證碼為 123456。</div>
            </div>
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy}
              onClick={() => void verifyCode()}
            >
              {busy ? '登入中…' : '登入'}
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy || cooldown > 0}
              onClick={() => void requestCode()}
            >
              {cooldown > 0 ? `重新發送（${cooldown}s）` : '重新發送驗證碼'}
            </button>
          </div>
        )}

        {error ? (
          <div style={{ marginTop: 16 }}>
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
