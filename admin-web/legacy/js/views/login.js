/**
 * Admin sign-in.
 *
 * Two steps, matching the API: request a code, then verify it. Signup always
 * creates a PASSENGER (`app/services/auth/account_service.py`), so an admin
 * account has to be granted out of band — `scripts/ops/create_admin.py`. This
 * screen therefore checks the role and refuses a non-admin rather than letting
 * them in to watch every request 403.
 */

import { api, ApiError, CODE } from '../api.js';
import { el, field, openDialog, toast } from '../dom.js';
import { session } from '../session.js';

const PHONE_PATTERN = /^\+852\d{8}$/;

export function LoginView({ client, onSignedIn }) {
  const phoneInput = el('input', {
    id: 'login-phone',
    type: 'tel',
    inputmode: 'tel',
    autocomplete: 'username',
    placeholder: '+85290000001',
    value: '+852',
  });

  const codeInput = el('input', {
    id: 'login-code',
    type: 'text',
    inputmode: 'numeric',
    autocomplete: 'one-time-code',
    maxlength: '6',
    placeholder: '6 位驗證碼',
  });

  const phoneStep = el('div', {}, [
    field('管理員手機號碼', phoneInput, '格式 +852 加上 8 位數字。'),
    el('button', {
      type: 'button',
      class: 'btn btn--primary',
      text: '發送驗證碼',
      style: 'width:100%',
      onClick: () => requestCode(),
    }),
  ]);

  const codeStep = el('div', { hidden: true }, [
    field('驗證碼', codeInput, '開發環境的固定驗證碼為 123456。'),
    el('button', {
      type: 'button',
      class: 'btn btn--primary',
      text: '登入',
      style: 'width:100%',
      onClick: () => verifyCode(),
    }),
    el('button', {
      type: 'button',
      text: '重新發送驗證碼',
      style: 'width:100%;margin-top:8px',
      onClick: () => requestCode(),
    }),
  ]);

  const status = el('div', { class: 'dialog__error', hidden: true });

  function showError(message) {
    status.textContent = message;
    status.hidden = false;
  }

  function clearError() {
    status.hidden = true;
  }

  function currentPhone() {
    return phoneInput.value.trim();
  }

  /**
   * The resend cooldown is a 60s lookback over `otp_codes` server-side, not a
   * rate-limit window, and it reports `details.retry_after_seconds`. Counting it
   * down is why the backend had to stop discarding `details` — see the
   * `BusinessRuleError` branch in `app/api/auth.py`.
   */
  function startCooldown(seconds) {
    const resend = codeStep.querySelector('button:last-of-type');
    let remaining = Math.ceil(seconds);
    resend.disabled = true;
    resend.textContent = `重新發送（${remaining}s）`;
    const timer = setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        clearInterval(timer);
        resend.disabled = false;
        resend.textContent = '重新發送驗證碼';
        return;
      }
      resend.textContent = `重新發送（${remaining}s）`;
    }, 1000);
  }

  async function requestCode() {
    clearError();
    const phone = currentPhone();
    if (!PHONE_PATTERN.test(phone)) {
      showError('手機號碼格式應為 +852 加上 8 位數字，例如 +85290000001。');
      return;
    }

    try {
      const body = await api.auth.requestOtp(client, phone);
      phoneStep.hidden = true;
      codeStep.hidden = false;
      codeInput.focus();
      toast(`驗證碼已發送，${body.expires_in ?? 300} 秒內有效。`);
      startCooldown(60);
    } catch (error) {
      if (error instanceof ApiError && error.details?.retry_after_seconds) {
        // Already requested: move on to the code step and honour the cooldown
        // rather than dead-ending the operator.
        phoneStep.hidden = true;
        codeStep.hidden = false;
        codeInput.focus();
        startCooldown(error.details.retry_after_seconds);
        showError(error.message);
        return;
      }
      showError(error?.message || '無法發送驗證碼。');
    }
  }

  async function verifyCode() {
    clearError();
    const phone = currentPhone();
    const code = codeInput.value.trim();
    if (code.length === 0) {
      showError('請輸入驗證碼。');
      return;
    }

    let body;
    try {
      body = await api.auth.verifyOtp(client, phone, code);
    } catch (error) {
      const remaining = error?.details?.attempts_remaining;
      showError(
        remaining === undefined
          ? error?.message || '驗證失敗。'
          : `${error.message}（剩餘 ${remaining} 次嘗試）`,
      );
      return;
    }

    if (body?.user?.role !== 'ADMIN') {
      // The server would 403 every admin route. Refuse here so the reason is a
      // sentence rather than a console full of failures.
      showError('此帳戶沒有管理權限。請聯絡平台管理員開通。');
      codeInput.value = '';
      return;
    }

    session.save({
      accessToken: body.access_token,
      refreshToken: body.refresh_token,
      user: body.user,
    });
    onSignedIn();
  }

  phoneInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') requestCode();
  });
  codeInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') verifyCode();
  });

  return el('div', { class: 'login' }, [
    el('div', { class: 'login__card' }, [
      el('div', { class: 'brand', style: 'padding:0 0 18px' }, [
        el('div', { class: 'brand__mark', text: 'R' }),
        el('div', { class: 'brand__text' }, [
          el('div', { class: 'brand__title', text: 'hkfastdc' }),
          el('div', { class: 'brand__sub', text: '管理後台' }),
        ]),
      ]),
      el('h1', { class: 'login__title', text: '管理員登入' }),
      el('p', { class: 'login__sub', text: '供平台管理團隊及 admin 團隊使用。' }),
      phoneStep,
      codeStep,
      status,
      el('div', { class: 'login__foot' }, [
        '登入憑證只儲存在此分頁的 session storage，關閉分頁即失效。',
      ]),
    ]),
  ]);
}

/** Re-exported so a view can show the same "not an admin" dialog if needed. */
export function notAnAdminDialog() {
  openDialog({
    title: '沒有管理權限',
    body: el('p', { text: '此帳戶沒有 ADMIN 角色，無法使用管理後台。' }),
    confirmLabel: '知道了',
    onSubmit: (close) => close(),
  });
}

export { CODE };
