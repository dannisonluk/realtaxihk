/**
 * Modal helpers.
 *
 * Two shapes the console needs, and the difference is deliberate:
 *
 *  * `useFormDialog` — a form that submits. The modal stays open while the
 *    request is in flight and closes only on success, so a failed action never
 *    discards what the operator typed.
 *  * `useConfirmDialog` — a yes/no on an irreversible action.
 *
 * Both are built from `<Modal>` in `components/primitives.tsx`.
 */

import { useCallback, useState, type ReactNode } from 'react';
import { Message, Modal } from '../components/primitives';
import { normaliseError } from './useLoad';
import { useI18n } from '../i18n';

interface FormDialogSpec {
  title: string;
  confirmLabel: string;
  /** Render the body. Receives nothing: state lives in the component below. */
  body: ReactNode;
  /**
   * Perform the action. Throw to keep the dialog open and show the reason —
   * which is what makes a failed review or grant recoverable.
   */
  onSubmit: () => Promise<void>;
  danger?: boolean;
}

export function useFormDialog() {
  const { t } = useI18n();
  const [spec, setSpec] = useState<FormDialogSpec | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const open = useCallback((next: FormDialogSpec) => {
    setError(null);
    setBusy(false);
    setSpec(next);
  }, []);

  const close = useCallback(() => setSpec(null), []);

  async function submit() {
    if (!spec || busy) return;
    setBusy(true);
    setError(null);
    try {
      await spec.onSubmit();
      setSpec(null);
    } catch (cause) {
      setError(normaliseError(cause));
    } finally {
      setBusy(false);
    }
  }

  const element = spec ? (
    <Modal
      title={spec.title}
      onClose={busy ? () => {} : close}
      footer={
        <>
          <button type="button" className="btn" onClick={close} disabled={busy}>
            {t('common.cancel')}
          </button>
          <button
            type="button"
            className={spec.danger ? 'btn btn--danger' : 'btn btn--primary'}
            onClick={() => void submit()}
            disabled={busy}
          >
            {busy ? t('common.processing') : spec.confirmLabel}
          </button>
        </>
      }
    >
      {error ? (
        <div style={{ marginBottom: 16 }}>
          <Message tone="error" className="dialog__error">
            {error.message}
          </Message>
        </div>
      ) : null}
      {spec.body}
    </Modal>
  ) : null;

  return { open, close, element };
}

interface ConfirmSpec {
  title: string;
  message: ReactNode;
  confirmLabel: string;
  onConfirm: () => Promise<void>;
}

/**
 * A yes/no on an irreversible action.
 *
 * The confirm button is `--danger` whenever the action is destructive, and the
 * body has to state the consequence rather than restate the title.
 */
export function useConfirmDialog(danger = true) {
  const { t } = useI18n();
  const [spec, setSpec] = useState<ConfirmSpec | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const open = useCallback((next: ConfirmSpec) => {
    setError(null);
    setBusy(false);
    setSpec(next);
  }, []);

  async function confirm() {
    if (!spec || busy) return;
    setBusy(true);
    setError(null);
    try {
      await spec.onConfirm();
      setSpec(null);
    } catch (cause) {
      setError(normaliseError(cause));
    } finally {
      setBusy(false);
    }
  }

  const element = spec ? (
    <Modal
      title={spec.title}
      onClose={busy ? () => {} : () => setSpec(null)}
      footer={
        <>
          <button type="button" className="btn" onClick={() => setSpec(null)} disabled={busy}>
            {t('common.cancel')}
          </button>
          <button
            type="button"
            className={danger ? 'btn btn--danger' : 'btn btn--primary'}
            onClick={() => void confirm()}
            disabled={busy}
          >
            {busy ? t('common.processing') : spec.confirmLabel}
          </button>
        </>
      }
    >
      {error ? (
        <div style={{ marginBottom: 16 }}>
          <Message tone="error" className="dialog__error">
            {error.message}
          </Message>
        </div>
      ) : null}
      <div>{spec.message}</div>
    </Modal>
  ) : null;

  return { open, element };
}
