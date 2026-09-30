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

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const hint = errorHint(error);
  return (
    <div className="stack">
      <Message tone="error">
        <div style={{ fontWeight: 600 }}>{error.message}</div>
        {hint ? <div style={{ marginTop: 4 }}>{hint}</div> : null}
      </Message>
      {onRetry ? (
        <div>
          <button type="button" className="btn" onClick={onRetry}>
            重試
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function LoadingState({ label = '載入中…' }: { label?: string }) {
  return (
    <div className="empty" aria-live="polite">
      {label}
    </div>
  );
}
