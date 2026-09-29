/**
 * Tiny DOM helpers.
 *
 * The single rule this file exists to enforce: **server data never becomes
 * markup**. Everything a view renders arrives from the API as an opaque string —
 * a driver's refund note, a fleet contact name, a licence number — and this
 * console displays all of it. Building nodes and assigning `textContent` means a
 * note containing `<img src=x onerror=...>` is rendered as those literal
 * characters, not parsed.
 *
 * So there is deliberately no `html` escape hatch here. If a view needs markup,
 * it composes elements.
 */

/**
 * Create an element.
 *
 * @param {string} tag
 * @param {object} [props] `class`, `text`, `on<Event>`, `dataset`, or any
 *   attribute. `text` is assigned via `textContent` and is the only supported
 *   way to put a string in.
 * @param {Array<Node|string|null|false|undefined>} [children]
 */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);

  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;

    if (key === 'class') {
      node.className = value;
    } else if (key === 'text') {
      node.textContent = String(value);
    } else if (key === 'dataset') {
      Object.assign(node.dataset, value);
    } else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key in node && typeof node[key] === 'boolean') {
      node[key] = Boolean(value);
    } else {
      node.setAttribute(key, String(value));
    }
  }

  append(node, children);
  return node;
}

/**
 * Append children, skipping nullish/false so `cond && el(...)` works inline.
 *
 * Arrays are flattened **recursively**, and that is load-bearing rather than a
 * convenience. A children array is the natural place to drop the result of a
 * `.map()`, and the sidebar is built exactly that way:
 * `[brand, NAV.map(navLink), foot]`. Without flattening, that inner array is not
 * a `Node`, so it fell through to `String(child)` — and stringifying a list of
 * anchors yields their comma-joined `href`s. The nav rendered **zero links** and
 * one long `http://…#/,http://…#/kyc,…` text node in their place, leaving the
 * console unnavigable except by typing hash URLs by hand.
 *
 * @param {Node} parent
 * @param {Node|string|Array|false|null|undefined} children
 */
export function append(parent, children) {
  const list = Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) {
      append(parent, child);
      continue;
    }
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

/** Replace a container's contents. */
export function mount(parent, children) {
  parent.replaceChildren();
  append(parent, children);
  return parent;
}

export function clear(parent) {
  parent.replaceChildren();
  return parent;
}

// ---------------------------------------------------------------- formatting

/**
 * HKD, from the server's decimal **string**.
 *
 * Pydantic serialises `Decimal` as a string, and the amounts are not uniform:
 * the platform's flat fee arrives as `"200"` while a discounted fleet fee is
 * `"150.00"`. So this normalises to two places for display rather than echoing
 * whatever precision the endpoint happened to send.
 */
export function money(canonical, { sign = false } = {}) {
  if (canonical === null || canonical === undefined || canonical === '') return '—';
  const value = Number(canonical);
  if (!Number.isFinite(value)) return String(canonical);
  const body = `HK$${Math.abs(value).toFixed(2)}`;
  if (!sign) return body;
  return `${value < 0 ? '−' : '+'}${body}`;
}

/** `12.5` -> `12.5%`, `25.00` -> `25%` — no precision the operator never typed. */
export function percent(canonical) {
  const value = Number(canonical);
  if (!Number.isFinite(value)) return String(canonical ?? '—');
  return `${Number.isInteger(value) ? value : value.toFixed(1)}%`;
}

/** ISO-8601 from `datetime.isoformat()` -> local `YYYY-MM-DD HH:mm`. */
export function dateTime(iso) {
  if (!iso) return '—';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return String(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return (
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ` +
    `${pad(at.getHours())}:${pad(at.getMinutes())}`
  );
}

export function date(iso) {
  return dateTime(iso).slice(0, 10);
}

/** A UUID is unreadable at a glance; the tail is enough to point at a row. */
export function shortId(id) {
  if (!id) return '—';
  const text = String(id);
  return text.length <= 12 ? text : `…${text.slice(-8)}`;
}

/** `2026-W38` — the ISO week key the settlement endpoints use. */
export function currentPeriod() {
  const now = new Date();
  const target = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
  const day = target.getUTCDay() || 7; // Mon=1 … Sun=7
  target.setUTCDate(target.getUTCDate() + 4 - day); // nearest Thursday
  const yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((target - yearStart) / 86400000 + 1) / 7);
  return `${target.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

export const PERIOD_PATTERN = /^\d{4}-W\d{2}$/;

// ------------------------------------------------------------------- widgets

/**
 * A status pill.
 *
 * @param {string} label
 * @param {'ok'|'wait'|'bad'|'neutral'|'info'} tone
 */
export function badge(label, tone = 'neutral') {
  return el('span', { class: `badge badge--${tone}`, text: label });
}

export function stat(label, value, { hint, href } = {}) {
  const body = [
    el('div', { class: 'stat__label', text: label }),
    el('div', { class: 'stat__value', text: value }),
    hint ? el('div', { class: 'stat__hint', text: hint }) : null,
  ];

  if (href) {
    return el(
      'a',
      { class: 'stat stat--link', href, style: 'text-decoration:none' },
      body,
    );
  }
  return el('div', { class: 'stat' }, body);
}

/** A label/value list. Values may be strings or nodes. */
export function definitionList(pairs) {
  const items = [];
  for (const [label, value] of pairs) {
    if (value === null || value === undefined || value === false) continue;
    items.push(el('dt', { text: label }));
    items.push(el('dd', {}, value instanceof Node ? value : String(value)));
  }
  return el('dl', { class: 'dl' }, items);
}

/** A labelled form control. */
export function field(label, control, hint) {
  return el('div', { class: 'field' }, [
    el('label', { text: label, for: control.id || undefined }),
    control,
    hint ? el('div', { class: 'field__hint', text: hint }) : null,
  ]);
}

/** A filter row of mutually exclusive chips. */
export function chipRow(options, current, onSelect) {
  return el(
    'div',
    { class: 'chiprow', role: 'group' },
    options.map((option) =>
      el('button', {
        type: 'button',
        class: 'chip',
        text: option.label,
        'aria-pressed': String(option.value === current),
        onClick: () => onSelect(option.value),
      }),
    ),
  );
}

/**
 * A table.
 *
 * @param {Array<{label: string, numeric?: boolean, wrap?: boolean}>} columns
 * @param {Array<Array<Node|string>>} rows
 * @param {string} [emptyMessage]
 */
export function table(columns, rows, emptyMessage = '沒有資料') {
  if (rows.length === 0) {
    return el('div', { class: 'empty', text: emptyMessage });
  }

  const head = el(
    'tr',
    {},
    columns.map((column) =>
      el('th', {
        class: column.numeric ? 'num' : undefined,
        text: column.label,
        scope: 'col',
      }),
    ),
  );

  const body = rows.map((cells) =>
    el(
      'tr',
      {},
      cells.map((cell, index) =>
        el('td', {
          class: [
            columns[index]?.numeric ? 'num' : '',
            columns[index]?.wrap ? 'wrap' : '',
          ]
            .filter(Boolean)
            .join(' ') || undefined,
        }, cell instanceof Node ? [cell] : [String(cell ?? '—')]),
      ),
    ),
  );

  return el(
    'div',
    { class: 'table-wrap' },
    el('table', {}, [el('thead', {}, head), el('tbody', {}, body)]),
  );
}

/** An inline spinner, for a button that is mid-request. */
export function spinner() {
  return el('span', { class: 'spinner', 'aria-hidden': 'true' });
}

export function loading(message = '載入中…') {
  return el('div', { class: 'loading' }, [spinner(), ' ', message]);
}

export function emptyState(message) {
  return el('div', { class: 'empty', text: message });
}

/** An error block, including the server's own message. */
export function errorState(error, onRetry) {
  const hints = {
    NETWORK: '檢查網絡連線。請求未送達伺服器。',
    RATE_LIMITED: '請稍後再試。',
    SERVICE_UNAVAILABLE: '服務繁忙，請稍後再試。',
  };
  const hint = hints[error?.code];

  return el('div', { class: 'error' }, [
    el('div', { text: error?.message || '發生未知錯誤。' }),
    hint ? el('div', { class: 'error__hint', text: hint }) : null,
    error?.status ? el('div', { class: 'error__hint', text: `HTTP ${error.status} · ${error.code || ''}` }) : null,
    onRetry
      ? el('div', { style: 'margin-top:14px' }, [
          el('button', { type: 'button', text: '重試', onClick: onRetry }),
        ])
      : null,
  ]);
}

/**
 * A modal dialog.
 *
 * Native `<dialog>` rather than a hand-rolled overlay: it handles focus
 * trapping, `Esc`, and inertness of the rest of the page, all of which a custom
 * overlay gets wrong.
 *
 * @param {{title: string, body: Node|Node[], confirmLabel?: string,
 *          danger?: boolean, onSubmit: (close: () => void) => Promise<void>}} spec
 */
export function openDialog(spec) {
  const errorBox = el('div', { class: 'dialog__error', hidden: true });
  const confirm = el('button', {
    type: 'button',
    class: spec.danger ? 'btn btn--danger' : 'btn btn--primary',
    text: spec.confirmLabel || '確認',
  });

  const dialog = el('dialog', {}, [
    el('form', { method: 'dialog' }, [
      el('div', { class: 'dialog__body' }, [
        el('h2', { class: 'dialog__title', text: spec.title }),
        spec.body,
        errorBox,
      ]),
      el('div', { class: 'dialog__foot' }, [
        el('button', { type: 'button', text: '取消', onClick: () => dialog.close() }),
        confirm,
      ]),
    ]),
  ]);

  const close = () => dialog.close();

  confirm.addEventListener('click', async () => {
    errorBox.hidden = true;
    confirm.disabled = true;
    const label = confirm.textContent;
    confirm.replaceChildren(spinner());
    try {
      await spec.onSubmit(close);
    } catch (error) {
      // Kept inside the dialog: the form stays filled in so the operator does
      // not have to retype a note after a transient failure.
      errorBox.textContent = error?.message || String(error);
      errorBox.hidden = false;
    } finally {
      confirm.disabled = false;
      confirm.textContent = label;
    }
  });

  dialog.addEventListener('close', () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

/** Transient toast. Errors stay longer — they usually carry a number to read. */
export function toast(message, kind = 'info') {
  let host = document.querySelector('.toasts');
  if (!host) {
    host = el('div', { class: 'toasts', role: 'status' });
    document.body.append(host);
  }
  const node = el('div', { class: `toast${kind === 'error' ? ' toast--error' : ''}`, text: message });
  host.append(node);
  setTimeout(() => node.remove(), kind === 'error' ? 8000 : 4000);
}
