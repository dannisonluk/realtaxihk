/**
 * The failure and loading surfaces every page shares.
 *
 * Apple's guidance for a status screen (`feedback.md`, `writing.md`): say what
 * happened, what to do about it, and offer one action. The message is the
 * server's own, because a generic "something went wrong" tells an operator
 * nothing they can act on.
 */

import { Message } from './primitives';
import { errorHint } from '../app/useLoad';
import { useI18n } from '../i18n';

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const { t } = useI18n();
  const hint = errorHint(error, t);
  return (
    <div className="stack">
      <Message tone="error">
        <div style={{ fontWeight: 600 }}>{error.message}</div>
        {hint ? <div style={{ marginTop: 4 }}>{hint}</div> : null}
      </Message>
      {onRetry ? (
        <div>
          <button type="button" className="btn" onClick={onRetry}>
            {t('common.retry')}
          </button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * `label` defaults to the translated "loading…" but stays overridable: several
 * pages pass a more specific phrase ("Loading driver roster…") that reads better
 * than the generic one.
 */
export function LoadingState({ label }: { label?: string }) {
  const { t } = useI18n();
  return (
    <div className="empty" aria-live="polite">
      {label ?? t('common.loading')}
    </div>
  );
}
