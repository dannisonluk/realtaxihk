/**
 * Toasts.
 *
 * The console reports both outcomes here — a saved fleet and a rejected refund
 * look the same shape, differing only in tone. `role="status"` for successes so
 * a screen reader announces them politely, `role="alert"` for failures so it
 * interrupts.
 */

import { useApp } from '../app/AppContext';
import { useI18n } from '../i18n';

export function ToastStack() {
  const { toasts, dismissToast } = useApp();
  const { t } = useI18n();

  if (toasts.length === 0) return null;

  return (
    <div className="toast-stack">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={toast.tone === 'error' ? 'toast toast--error' : 'toast'}
          role={toast.tone === 'error' ? 'alert' : 'status'}
        >
          <div className="spread">
            <span>{toast.message}</span>
            <button
              type="button"
              className="btn btn--sm"
              onClick={() => dismissToast(toast.id)}
              aria-label={t('common.closeNotice')}
            >
              {t('common.close')}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
