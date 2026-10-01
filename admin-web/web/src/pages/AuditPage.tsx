/**
 * The audit trail.
 *
 * Readable by **every** admin role, including SUPPORT, and that is deliberate
 * rather than an oversight: the log records what was done by whom, and
 * restricting its reading would mean the people least able to change anything
 * are also the least able to notice that something was changed. It holds no
 * secrets — no password, no TOTP secret, no token — only decisions and their
 * actors.
 *
 * Two things the page gets right where a naive one does not:
 *
 * 1. **`payload` is rendered as key/value pairs, not as JSON.** The column is
 *    `dict | None` on the server precisely because its shape differs per event,
 *    and a JSON blob in a table cell is a wall of braces with the two values
 *    that matter buried in it. Flattening to rows makes a role change read as
 *    `from: SUPPORT → to: FINANCE`.
 * 2. **`outcome` is shown, not filtered away.** A refused admin action is the
 *    most interesting kind of audit row there is, and a page that only listed
 *    successes would hide exactly the thing it exists to reveal.
 */

import { useState } from 'react';
import { endpoints } from '../api/endpoints';
import type { AuditRow } from '../api/types';
import { Card, Chip, Empty, Rows } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { useApp } from '../app/AppContext';
import { useLoad } from '../app/useLoad';
import { formatTime, shortId } from '../lib/labels';
import { PageHead } from '../app/Shell';

const PAGE_SIZE = 50;

/**
 * Event names, grouped by what they touch.
 *
 * The filter is a free-text box rather than a dropdown on purpose: the set is
 * open (every new audited action adds one) and a hard-coded list here would go
 * stale silently, making an event un-filterable the day after it ships. The
 * quick-pick chips below cover the ones an operator actually reaches for.
 */
const QUICK_EVENTS: { value: string; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'ad.settlement', label: '結算' },
  { value: 'ad.dispute', label: '爭議' },
  { value: 'ad.role', label: '權限' },
  { value: 'ad.account', label: '帳戶' },
  { value: 'ad.login', label: '登入' },
];

export function AuditPage() {
  const { client } = useApp();
  const [event, setEvent] = useState('');
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, error, loading, reload } = useLoad(
    () =>
      endpoints.audit.list(client, {
        event: event || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    [client, event, offset],
  );

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const hasMore = offset + PAGE_SIZE < total;

  return (
    <>
      <PageHead
        title="審計紀錄"
        subtitle="誰在何時做了什麼決定。所有管理員權限均可閱讀；紀錄不含任何密碼、TOTP 密鑰或權杖。"
      />

      <div className="filters">
        {QUICK_EVENTS.map((item) => (
          <button
            key={item.label}
            type="button"
            className={event === item.value ? 'chip chip--brand' : 'chip'}
            aria-pressed={event === item.value}
            onClick={() => {
              setEvent(item.value);
              setOffset(0);
            }}
          >
            {item.label}
          </button>
        ))}
        <input
          type="search"
          placeholder="或輸入完整事件名稱篩選"
          style={{ maxWidth: 260 }}
          onChange={(e) => {
            setEvent(e.target.value.trim());
            setOffset(0);
          }}
        />
      </div>

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {!loading && !error ? (
        <Card>
          {items.length === 0 ? (
            <Empty title="沒有符合條件的紀錄。" />
          ) : (
            <>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>時間</th>
                      <th>事件</th>
                      <th>結果</th>
                      <th>操作人</th>
                      <th>說明</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((row) => (
                      <AuditTableRow
                        key={row.id}
                        row={row}
                        expanded={expanded === row.id}
                        onToggle={() => setExpanded(expanded === row.id ? null : row.id)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
              {total > PAGE_SIZE ? (
                <div className="spread" style={{ marginTop: 16 }}>
                  <div className="dim t-caption1">
                    顯示第 {offset + 1}–{offset + items.length} 筆，共 {total} 筆。
                  </div>
                  <div className="row">
                    <button
                      type="button"
                      className="btn btn--sm"
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                    >
                      上一頁
                    </button>
                    <button
                      type="button"
                      className="btn btn--sm"
                      disabled={!hasMore}
                      onClick={() => setOffset(offset + PAGE_SIZE)}
                    >
                      下一頁
                    </button>
                  </div>
                </div>
              ) : null}
            </>
          )}
        </Card>
      ) : null}
    </>
  );
}

function AuditTableRow({
  row,
  expanded,
  onToggle,
}: {
  row: AuditRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  const hasDetail = Boolean(row.payload && Object.keys(row.payload).length > 0);

  return (
    <>
      <tr>
        <td>{formatTime(row.created_at)}</td>
        <td className="mono" style={{ fontSize: 12 }}>
          {row.event}
        </td>
        <td>
          {/*
            The default is SUCCESS, so an explicit failure is the one that needs
            colour — and a refusal is the most interesting row on the page.
          */}
          {row.outcome === 'SUCCESS' ? (
            <Chip tone="ok">成功</Chip>
          ) : (
            <Chip tone="danger">{row.outcome}</Chip>
          )}
        </td>
        <td>
          {/*
            `actor_username` is the *attempted* username, denormalised onto the
            row. It is present even when `actor_id` is null — a failed login
            against a username that does not exist — which is why it is shown in
            preference to the id rather than as a fallback to it.
          */}
          {row.actor_username ?? (row.actor_id ? <span className="mono">{shortId(row.actor_id)}</span> : <span className="dim">匿名</span>)}
          {row.ip_address ? (
            <div className="dim t-caption1 mono">{row.ip_address}</div>
          ) : null}
        </td>
        <td>{row.detail || <span className="dim">—</span>}</td>
        <td>
          {hasDetail ? (
            <button type="button" className="btn btn--sm" onClick={onToggle}>
              {expanded ? '收起' : '詳情'}
            </button>
          ) : (
            <span className="dim">—</span>
          )}
        </td>
      </tr>
      {expanded && hasDetail ? (
        <tr>
          <td colSpan={6} style={{ background: 'var(--surface-2)' }}>
            <Rows>
              {Object.entries(row.payload ?? {}).map(([key, value]) => (
                <div className="rows__item" key={key}>
                  <div className="rows__label mono">{key}</div>
                  <div className="rows__value">{renderPayloadValue(value)}</div>
                </div>
              ))}
            </Rows>
            {row.user_agent ? (
              <div className="dim t-caption1" style={{ marginTop: 8 }}>
                {row.user_agent}
              </div>
            ) : null}
          </td>
        </tr>
      ) : null}
    </>
  );
}

/**
 * A payload value, rendered without hiding structure.
 *
 * `null` is a real answer here — it means "the field was not set", which for a
 * `from`/`to` pair is different from an empty string. Booleans are spelled out
 * because `true` in a table cell reads as a label rather than a value.
 */
function renderPayloadValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return '—';
  }
}
