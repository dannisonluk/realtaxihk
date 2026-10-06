/**
 * Shared presentational primitives.
 *
 * The one rule this file enforces, carried over from the vanilla build: **server
 * data never becomes markup**. Everything rendered here arrives from the API as
 * an opaque string — a driver's refund note, a fleet contact name, a licence
 * number — and React escapes all of it on the way into the DOM. So there is
 * deliberately no `dangerouslySetInnerHTML` anywhere in this app. If a component
 * needs markup, it composes elements.
 */

import { useEffect, useRef, type ReactNode } from 'react';
import { useI18n } from '../i18n';

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={className ? `card ${className}` : 'card'}>{children}</div>;
}

/**
 * iOS's inset grouped list: a titled surface whose rows belong together.
 *
 * Grouping is what tells a reader which fields are related — Apple calls it out
 * in `layout.md › Visual hierarchy`.
 */
export function Section({
  title,
  footnote,
  children,
  actions,
}: {
  title: string;
  footnote?: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="section">
      <div className="spread">
        <div className="section__head">{title}</div>
        {actions}
      </div>
      <Card>{children}</Card>
      {footnote ? <div className="section__foot">{footnote}</div> : null}
    </section>
  );
}

/** A label/value row. The label column is fixed so stacked rows align. */
export function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="rows__item">
      <div className="rows__label">{label}</div>
      <div className="rows__value">{children}</div>
    </div>
  );
}

export function Rows({ children }: { children: ReactNode }) {
  return <div className="rows">{children}</div>;
}

export type ChipTone = 'neutral' | 'ok' | 'warn' | 'danger' | 'brand';

export function Chip({ children, tone = 'neutral' }: { children: ReactNode; tone?: ChipTone }) {
  return <span className={`chip chip--${tone}`}>{children}</span>;
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="stat">
      <div className="stat__label">{label}</div>
      <div className="stat__value num">{value}</div>
      {hint ? <div className="stat__hint">{hint}</div> : null}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty">
      <div className="empty__title">{title}</div>
      {hint ? <div>{hint}</div> : null}
    </div>
  );
}

export function Message({
  tone = 'error',
  children,
  className = '',
}: {
  tone?: 'error' | 'ok' | 'warn';
  children: ReactNode;
  className?: string;
}) {
  // `role="alert"` on the failure case only: a successful save should not
  // interrupt a screen reader mid-sentence, a failure should.
  //
  // `className` exists so a caller can carry a stable hook for the UI verifier
  // (`.dialog__error`) without depending on the tone class, which is composed.
  return (
    <div
      className={`message message--${tone}${className ? ` ${className}` : ''}`}
      role={tone === 'error' ? 'alert' : undefined}
    >
      <div>{children}</div>
    </div>
  );
}

export function Loading({ label }: { label?: string }) {
  const { t } = useI18n();
  return (
    <div className="empty" aria-live="polite">
      {label ?? t('common.loading')}
    </div>
  );
}

/**
 * A modal that behaves like a sheet: Escape closes it, the backdrop closes it,
 * focus moves in on open. Built on <dialog>-like semantics by hand rather than
 * the native element because the console needs consistent styling in both
 * colour schemes.
 */
export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
}) {
  const boxRef = useRef<HTMLDivElement>(null);

  // Focus moves into the dialog on open (otherwise a screen reader keeps
  // announcing the page behind it) and is trapped there: the last tabbable
  // control wraps to the first, and Shift+Tab from the first wraps to the
  // last. Escape is handled below rather than by a global listener so it
  // never fires for a modal that has already closed.
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    // Where focus was before the dialog opened. Without putting it back, closing
    // the dialog leaves focus on `<body>`, so the next Tab starts at the top of
    // the page instead of from the control that opened it — the keyboard user
    // loses their place every time.
    const opener = document.activeElement as HTMLElement | null;
    const focusable = () =>
      Array.from(
        box.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => el.offsetParent !== null || el === document.activeElement);
    const first = focusable()[0];
    (first ?? box).focus();
    return () => {
      // `isConnected` because the opener is often a row's button, and acting on
      // a row usually re-renders the list away from under it.
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={boxRef}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            onClose();
            return;
          }
          if (event.key !== 'Tab') return;
          const box = boxRef.current;
          if (!box) return;
          const items = Array.from(
            box.querySelectorAll<HTMLElement>(
              'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
            ),
          ).filter((el) => el.offsetParent !== null);
          if (items.length === 0) return;
          const first = items[0];
          const last = items[items.length - 1];
          if (!first || !last) return;
          const active = document.activeElement as HTMLElement | null;
          if (!event.shiftKey && active === last) {
            event.preventDefault();
            first.focus();
          } else if (event.shiftKey && (active === first || active === box)) {
            event.preventDefault();
            last.focus();
          }
        }}
      >
        <h2>{title}</h2>
        <div className="modal__body">{children}</div>
        <div className="modal__actions">{footer}</div>
      </div>
    </div>
  );
}

/**
 * A money amount, tabular so columns of figures line up.
 *
 * Ported from the vanilla `dom.js#money`, and the details matter:
 *
 *  * **No space** between `HK$` and the figure — `HK$500.00`, not `HK$ 500.00`.
 *  * **Always two decimals.** The server is inconsistent: the platform's flat
 *    fee arrives as `"200"` while a discounted fleet fee is `"150.00"`. Echoing
 *    that verbatim puts `HK$0.0` next to `HK$1234.5` and the column stops
 *    reading as money.
 *  * `sign` prefixes `+`/`−` and takes the absolute value, because a ledger
 *    deduction is stored **negative** (`amount_hkd=-fee`) and `−HK$200.00`
 *    double-negates into a plus. The minus is U+2212, not a hyphen: it is the
 *    same width as the plus, so a signed column stays aligned.
 */
export function Money({
  value,
  className = '',
  sign = false,
}: {
  value: string | number | null | undefined;
  className?: string;
  sign?: boolean;
}) {
  return (
    <span className={`num ${className}`}>
      {formatMoney(value, sign)}
    </span>
  );
}

function formatMoney(value: string | number | null | undefined, sign: boolean): string {
  if (value === null || value === undefined || value === '') return '—';
  const amount = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(amount)) return String(value);

  // Grouped manually: `toLocaleString` varies by locale and a money column
  // should not reflow because the browser is set to a different region.
  const [whole = '0', fraction = '00'] = Math.abs(amount).toFixed(2).split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const body = `HK$${grouped}.${fraction}`;
  if (!sign) return body;
  return `${amount < 0 ? '\u2212' : '+'}${body}`;
}

/**
 * A percentage.
 *
 * `12.5` -> `12.5%`, `25.00` -> `25%`. The server sends the discount as a
 * decimal string, and printing `25.00%` shows precision the operator never
 * typed — so an integral value loses its fraction entirely.
 */
export function Percent({ value }: { value: string | number | null | undefined }) {
  const amount = typeof value === 'number' ? value : Number(value);
  if (value === null || value === undefined || !Number.isFinite(amount)) return <>—</>;
  return <>{Number.isInteger(amount) ? amount : amount.toFixed(1)}%</>;
}
