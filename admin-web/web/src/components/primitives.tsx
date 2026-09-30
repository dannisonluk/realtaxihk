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

import type { ReactNode } from 'react';

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

export function Loading({ label = '載入中…' }: { label?: string }) {
  return (
    <div className="empty" aria-live="polite">
      {label}
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
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onClose();
        }}
      >
        <h2>{title}</h2>
        <div className="modal__body">{children}</div>
        <div className="modal__actions">{footer}</div>
      </div>
    </div>
  );
}

/** A money amount, tabular so columns of figures line up. */
export function Money({ value, className = '' }: { value: string | number; className?: string }) {
  const text = typeof value === 'number' ? value.toFixed(2) : value;
  return <span className={`num ${className}`}>HK$ {text}</span>;
}
