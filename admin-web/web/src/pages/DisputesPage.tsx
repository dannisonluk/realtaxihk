/**
 * The dispute queue, and one case's thread.
 *
 * A dispute is the after-the-fact judgement about **who bears a cost**. It is
 * not a refund: a refund returns a deposit, a dispute decides whether a charge
 * should ever have been made. That is why resolution moves money in four of its
 * five forms and why the two roles see different things here.
 *
 * Four decisions this page encodes:
 *
 * 1. **Sorted by SLA ascending, not newest-first.** The server does this and the
 *    page must not re-sort. An incident queue sorted newest-first answers
 *    whatever arrived while somebody was watching and buries the case about to
 *    breach. The header counts come from `/disputes/stats` for the same reason —
 *    a client-side count over one page is the count of that page, which reads as
 *    the total.
 * 2. **Safety is a flag, never an action.** `SAFETY_CRITICAL` *or* any `SAFETY`
 *    category raises `safety_flag`, and a human decides. Auto-suspending on a
 *    report means one false accusation takes a driver off the road with no
 *    hearing.
 * 3. **The resolution control is informed by the live role.** Judging conduct is
 *    OPERATIONS' job and moving money is FINANCE's, so the server opens
 *    `/resolve` to OPERATIONS and narrows per request. The page mirrors that
 *    split by role, but the *authority* is the server's — a forged role here
 *    produces a 403, and that refusal must be surfaced rather than swallowed.
 * 4. **Internal notes are visually distinct and never in the party-facing order.**
 *    `is_internal` is on the wire precisely so this distinction is renderable;
 *    the alternative is two endpoints whose difference is invisible to the
 *    reader.
 */

import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { endpoints } from '../api/endpoints';
import type { DisputeStats } from '../api/types';
import { DISPUTE_CATEGORIES, DISPUTE_RESOLUTIONS, DISPUTE_SEVERITIES, DISPUTE_STATUSES } from '../api/types';
import { Card, Chip, DetailRow, Empty, Rows } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { useApp } from '../app/AppContext';
import { useFormDialog } from '../app/useDialogs';
import { inOrder, useLoad } from '../app/useLoad';
import {
  disputeCategoryLabel,
  disputeResolutionLabel,
  disputeSeverityLabel,
  disputeSeverityTone,
  disputeSourceLabel,
  disputeStatusLabel,
  disputeStatusTone,
  formatCountdown,
  formatTime,
  shortId,
} from '../lib/labels';
import { PageHead } from '../app/Shell';

export function DisputesPage() {
  const { disputeId } = useParams();

  if (disputeId) return <DisputeDetailView disputeId={disputeId} />;
  return <DisputeQueue />;
}

/** The queue, with the header counts the operator triages from. */
function DisputeQueue() {
  const { client } = useApp();
  const navigate = useNavigate();
  const [openOnly, setOpenOnly] = useState(true);
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [unassignedOnly, setUnassignedOnly] = useState(false);
  const [severity, setSeverity] = useState('');
  const dialog = useFormDialog();

  const { data, error, loading, reload } = useLoad(async () => {
    // Sequential — see `useLoad.ts`. The counts and the list are two calls, and
    // this environment intermittently accepts a connection and never answers it.
    const [page, stats] = await inOrder([
      () =>
        endpoints.disputes.list(client, {
          openOnly,
          overdueOnly,
          unassignedOnly,
          severity: severity || undefined,
          limit: 100,
        }),
      () => endpoints.disputes.stats(client),
    ] as const);
    return { items: page.items ?? [], total: page.total ?? 0, stats };
  }, [client, openOnly, overdueOnly, unassignedOnly, severity]);

  function openCase() {
    const form = {
      category: 'OTHER',
      severity: 'NORMAL',
      summary: '',
      orderId: '',
      againstKind: '',
      againstId: '',
    };
    dialog.open({
      title: '開立爭議',
      confirmLabel: '開立',
      body: <OpenCaseBody onChange={(patch) => Object.assign(form, patch)} />,
      onSubmit: async () => {
        if (!form.summary.trim()) throw new Error('請填寫摘要。');
        const created = await endpoints.disputes.create(client, {
          category: form.category,
          summary: form.summary.trim(),
          severity: form.severity,
          order_id: form.orderId.trim() || null,
          against_kind: form.againstKind || null,
          against_id: form.againstId.trim() || null,
        });
        reload();
        navigate(`/disputes/${created.id}`);
      },
    });
  }

  return (
    <>
      <PageHead
        title="爭議"
        subtitle="誰承擔一筆費用的判斷。佇列依 SLA 期限排序，最緊急的排最前。"
        actions={
          <button type="button" className="btn btn--primary" onClick={openCase}>
            開立爭議
          </button>
        }
      />

      {data ? <StatsStrip stats={data.stats} /> : null}

      <div className="filters">
        <button
          type="button"
          className={openOnly ? 'chip chip--brand' : 'chip'}
          aria-pressed={openOnly}
          onClick={() => setOpenOnly(!openOnly)}
        >
          只看未結案
        </button>
        <button
          type="button"
          className={overdueOnly ? 'chip chip--brand' : 'chip'}
          aria-pressed={overdueOnly}
          onClick={() => setOverdueOnly(!overdueOnly)}
        >
          只看逾期
        </button>
        <button
          type="button"
          className={unassignedOnly ? 'chip chip--brand' : 'chip'}
          aria-pressed={unassignedOnly}
          onClick={() => setUnassignedOnly(!unassignedOnly)}
        >
          只看未指派
        </button>
        <button
          type="button"
          className={severity === '' ? 'chip chip--brand' : 'chip'}
          aria-pressed={severity === ''}
          onClick={() => setSeverity('')}
        >
          所有級別
        </button>
        {DISPUTE_SEVERITIES.map((value) => (
          <button
            key={value}
            type="button"
            className={severity === value ? 'chip chip--brand' : 'chip'}
            aria-pressed={severity === value}
            onClick={() => setSeverity(value)}
          >
            {disputeSeverityLabel(value)}
          </button>
        ))}
      </div>

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {!loading && !error ? (
        <Card>
          {(data?.items ?? []).length === 0 ? (
            <Empty
              title={openOnly ? '目前沒有未結案的爭議。' : '沒有符合條件的爭議。'}
              hint="乘客或司機提出申訴後，個案會出現在此。"
            />
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>限期</th>
                    <th>級別</th>
                    <th>類別</th>
                    <th>摘要</th>
                    <th>狀態</th>
                    <th>指派</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {(data?.items ?? []).map((row) => (
                    <tr key={row.id} style={row.is_overdue ? { background: 'var(--surface-2)' } : undefined}>
                      <td>
                        <div
                          className="num"
                          style={row.is_overdue ? { color: 'var(--danger)', fontWeight: 600 } : undefined}
                        >
                          {formatCountdown(row.seconds_until_due)}
                        </div>
                        <div className="dim t-caption1">{row.sla_hours} 小時預算</div>
                      </td>
                      <td>
                        <Chip tone={disputeSeverityTone(row.severity)}>
                          {disputeSeverityLabel(row.severity)}
                        </Chip>
                        {row.safety_flag ? (
                          <div style={{ marginTop: 4 }}>
                            <Chip tone="danger">安全</Chip>
                          </div>
                        ) : null}
                      </td>
                      <td>{disputeCategoryLabel(row.category)}</td>
                      <td>
                        <div className="truncate" style={{ maxWidth: 320 }} title={row.summary}>
                          {row.summary}
                        </div>
                        <div className="dim t-caption1">
                          {disputeSourceLabel(row.source)}
                          {row.order_id ? (
                            <>
                              {' · 訂單 '}
                              <span className="mono">{shortId(row.order_id)}</span>
                            </>
                          ) : (
                            ' · 無關聯訂單'
                          )}
                        </div>
                      </td>
                      <td>
                        <Chip tone={disputeStatusTone(row.status)}>
                          {disputeStatusLabel(row.status)}
                        </Chip>
                      </td>
                      <td className="mono">
                        {row.assigned_admin_id ? (
                          shortId(row.assigned_admin_id)
                        ) : (
                          <span className="dim">未指派</span>
                        )}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn btn--sm"
                          onClick={() => navigate(`/disputes/${row.id}`)}
                        >
                          檢視
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      ) : null}

      {dialog.element}
    </>
  );
}

/**
 * The header counts.
 *
 * Server-computed against the same clock the sort uses, so the number and the
 * order it describes cannot disagree.
 */
function StatsStrip({ stats }: { stats: DisputeStats }) {
  return (
    <div className="grid" style={{ marginBottom: 16 }}>
      <div className="stat">
        <div className="stat__label">未結案</div>
        <div className="stat__value num">{stats.open}</div>
        <div className="stat__hint">全部紀錄 {stats.total} 宗</div>
      </div>
      <div className="stat">
        <div className="stat__label">已逾期</div>
        <div className="stat__value num" style={stats.overdue > 0 ? { color: 'var(--danger)' } : undefined}>
          {stats.overdue}
        </div>
        <div className="stat__hint">已超出 SLA 期限</div>
      </div>
      <div className="stat">
        <div className="stat__label">未指派</div>
        <div className="stat__value num">{stats.unassigned}</div>
        <div className="stat__hint">尚無負責人</div>
      </div>
      <div className="stat">
        <div className="stat__label">安全標記</div>
        <div className="stat__value num" style={stats.safety_flag > 0 ? { color: 'var(--danger)' } : undefined}>
          {stats.safety_flag}
        </div>
        <div className="stat__hint">需人手判斷，系統不會自動處置</div>
      </div>
    </div>
  );
}

function DisputeDetailView({ disputeId }: { disputeId: string }) {
  const { client, notify, hasRole, user } = useApp();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const resolveDialog = useFormDialog();
  const noteDialog = useFormDialog();

  const { data, error, loading, reload } = useLoad(
    () => endpoints.disputes.detail(client, disputeId),
    [client, disputeId],
  );

  async function guard(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } catch (cause) {
      // The server's refusal is surfaced verbatim. A 403 here means the live
      // role disagrees with what this page believed — swallowing it would leave
      // the operator pressing a button that appears to do nothing.
      notify(cause instanceof Error ? cause.message : String(cause), 'error');
    } finally {
      setBusy(false);
    }
  }

  function claim() {
    void guard(async () => {
      await endpoints.disputes.assign(client, disputeId);
      notify('已接手此個案。');
      reload();
    });
  }

  function addMessage(isInternal: boolean) {
    let body = '';
    noteDialog.open({
      title: isInternal ? '新增內部備註' : '回覆',
      confirmLabel: isInternal ? '儲存備註' : '送出',
      body: (
        <div className="stack">
          <p className="dim" style={{ margin: 0 }}>
            {isInternal
              ? '內部備註不會向乘客或司機顯示，只供內部記錄。'
              : '此回覆會被記錄在個案討論串中。'}
          </p>
          <div className="field">
            <label className="field__label" htmlFor="dispute-message">
              內容
            </label>
            <textarea
              id="dispute-message"
              rows={5}
              onChange={(event) => (body = event.target.value)}
            />
          </div>
        </div>
      ),
      onSubmit: async () => {
        if (!body.trim()) throw new Error('內容不可為空。');
        await endpoints.disputes.addMessage(client, disputeId, body.trim(), isInternal);
        notify(isInternal ? '已新增內部備註。' : '已送出回覆。');
        reload();
      },
    });
  }

  function changeStatus(status: string) {
    void guard(async () => {
      await endpoints.disputes.setStatus(client, disputeId, status);
      notify(`狀態已改為「${disputeStatusLabel(status)}」。`);
      reload();
    });
  }

  function resolve() {
    const form = { resolution: 'NONE', note: '', close: false };
    resolveDialog.open({
      title: '裁決',
      confirmLabel: '確認裁決',
      // A money-moving resolution is the irreversible one, so it is the
      // destructive-styled button.
      body: <ResolveBody onChange={(patch) => Object.assign(form, patch)} role={user?.role ?? null} />,
      onSubmit: async () => {
        if (!form.note.trim()) {
          throw new Error('裁決必須填寫理由，否則一個月後無法回答「為什麼」。');
        }
        const result = await endpoints.disputes.resolve(client, disputeId, {
          resolution: form.resolution,
          note: form.note.trim(),
          close: form.close,
        });
        notify(
          result.moves_money
            ? `已裁決為「${disputeResolutionLabel(result.resolution)}」，需跟進帳目。`
            : `已裁決為「${disputeResolutionLabel(result.resolution)}」。`,
        );
        reload();
      },
    });
  }

  if (loading) return <LoadingState />;
  if (error) {
    return (
      <div>
        <div style={{ marginBottom: 12 }}>
          <button type="button" className="btn" onClick={() => navigate('/disputes')}>
            ← 返回爭議列表
          </button>
        </div>
        <ErrorState error={error} onRetry={reload} />
      </div>
    );
  }
  if (!data) return null;

  const closed = data.status === 'CLOSED' || data.status === 'RESOLVED';
  const canAct = hasRole('OPERATIONS');
  const canMoveMoney = hasRole('FINANCE');

  return (
    <div>
      <PageHead
        title="爭議詳情"
        actions={
          <button type="button" className="btn" onClick={() => navigate('/disputes')}>
            ← 返回列表
          </button>
        }
      />

      {data.safety_flag ? (
        <div className="message message--error" style={{ marginBottom: 16 }}>
          <div>
            <strong>安全標記</strong>
            <div>
              此個案涉及安全問題。系統刻意不會自動停權 —— 單一指控就可以令司機無法開工而無從申辯。
              請人手判斷是否需要即時行動。
            </div>
          </div>
        </div>
      ) : null}

      <Card className="card--pad">
        <div className="row-inline" style={{ marginBottom: 16 }}>
          <Chip tone={disputeStatusTone(data.status)}>{disputeStatusLabel(data.status)}</Chip>
          <Chip tone={disputeSeverityTone(data.severity)}>
            {disputeSeverityLabel(data.severity)}
          </Chip>
          <span className="dim">{disputeCategoryLabel(data.category)}</span>
          <span
            className="num"
            style={data.is_overdue ? { color: 'var(--danger)', fontWeight: 600 } : undefined}
          >
            {formatCountdown(data.seconds_until_due)}
          </span>
        </div>

        <Rows>
          <DetailRow label="摘要">{data.summary}</DetailRow>
          <DetailRow label="來源">{disputeSourceLabel(data.source)}</DetailRow>
          <DetailRow label="提出方">
            {data.raised_by_kind}
            {data.against_kind ? ` → ${data.against_kind}` : ''}
          </DetailRow>
          <DetailRow label="關聯訂單">
            {data.order_id ? (
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => navigate(`/orders/${data.order_id}`)}
              >
                檢視訂單 {shortId(data.order_id)}
              </button>
            ) : (
              <span className="dim">無關聯訂單（帳戶或應用程式層面）</span>
            )}
          </DetailRow>
          <DetailRow label="SLA 期限">
            {formatTime(data.sla_due_at)}
            <span className="dim"> （{data.sla_hours} 小時預算）</span>
          </DetailRow>
          <DetailRow label="負責人">
            {data.assigned_admin_id ? (
              <span className="mono">{shortId(data.assigned_admin_id)}</span>
            ) : (
              <span className="dim">未指派</span>
            )}
          </DetailRow>
          {data.resolution ? (
            <>
              <DetailRow label="裁決">
                <Chip tone="ok">{disputeResolutionLabel(data.resolution)}</Chip>
              </DetailRow>
              <DetailRow label="裁決理由">{data.resolution_note ?? '—'}</DetailRow>
              <DetailRow label="裁決人">
                <span className="mono">{shortId(data.resolved_by)}</span>
                <span className="dim"> · {formatTime(data.resolved_at)}</span>
              </DetailRow>
            </>
          ) : null}
          <DetailRow label="開立時間">{formatTime(data.created_at)}</DetailRow>
        </Rows>
      </Card>

      {!closed ? (
        <Card className="card--pad" >
          <h2 className="t-title3" style={{ margin: '0 0 12px' }}>
            操作
          </h2>
          {!canAct ? (
            <p className="dim" style={{ marginTop: 0 }}>
              你的權限只可閱讀個案。回覆討論串不受限制，但指派、改狀態與裁決需要營運權限。
            </p>
          ) : null}
          <div className="row" style={{ flexWrap: 'wrap' }}>
            <button type="button" className="btn btn--sm" disabled={busy || !canAct} onClick={claim}>
              接手個案
            </button>
            <button
              type="button"
              className="btn btn--sm"
              disabled={busy || !canAct}
              onClick={() => addMessage(false)}
            >
              回覆
            </button>
            <button
              type="button"
              className="btn btn--sm"
              disabled={busy}
              onClick={() => addMessage(true)}
            >
              新增內部備註
            </button>
            {DISPUTE_STATUSES.filter((s) => s !== 'CLOSED').map((value) => (
              <button
                key={value}
                type="button"
                className="btn btn--sm"
                disabled={busy || !canAct || value === data.status}
                onClick={() => changeStatus(value)}
              >
                改為{disputeStatusLabel(value)}
              </button>
            ))}
            <button
              type="button"
              className="btn btn--primary btn--sm"
              disabled={busy || !canAct}
              onClick={resolve}
            >
              裁決
            </button>
          </div>
          {/*
            The role split, stated rather than discovered by pressing a button
            that 403s. The server is still the authority — this is the page
            telling the operator what it already knows.
          */}
          {canAct && !canMoveMoney ? (
            <p className="dim" style={{ margin: '12px 0 0' }}>
              你的權限可作出「不作收費」的裁決。涉及收費或退款的裁決需要財務權限。
            </p>
          ) : null}
          {canAct && canMoveMoney ? (
            <p className="dim" style={{ margin: '12px 0 0' }}>
              你的權限可作出任何裁決，包括涉及金錢的裁決。
            </p>
          ) : null}
        </Card>
      ) : null}

      <h2>討論串</h2>
      <Card>
        {data.messages.length === 0 ? (
          <Empty title="還沒有任何訊息。" />
        ) : (
          <div className="stack">
            {data.messages.map((message) => (
              <div
                key={message.id}
                style={{
                  padding: '12px 0',
                  borderBottom: '1px solid var(--border)',
                }}
              >
                <div className="row-inline" style={{ marginBottom: 4 }}>
                  <strong>
                    {message.author_label ??
                      (message.author_kind === 'ADMIN' ? '管理員' : message.author_kind)}
                  </strong>
                  {message.is_internal ? <Chip tone="warn">內部備註</Chip> : null}
                  <span className="dim t-caption1">{formatTime(message.created_at)}</span>
                </div>
                {/* `whiteSpace: pre-wrap` so a multi-line report keeps its
                    paragraphs — collapsing it makes a structured account
                    unreadable, which is the one thing a thread must not be. */}
                <div style={{ whiteSpace: 'pre-wrap' }}>{message.body}</div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {resolveDialog.element}
      {noteDialog.element}
    </div>
  );
}

function OpenCaseBody({
  onChange,
}: {
  onChange: (patch: {
    category?: string;
    severity?: string;
    summary?: string;
    orderId?: string;
    againstKind?: string;
    againstId?: string;
  }) => void;
}) {
  return (
    <div className="stack">
      <div className="field">
        <label className="field__label" htmlFor="case-summary">
          摘要
        </label>
        <textarea
          id="case-summary"
          rows={4}
          placeholder="簡述發生什麼事，以及涉及什麼費用。"
          onChange={(event) => onChange({ summary: event.target.value })}
        />
      </div>
      <div className="field">
        <label className="field__label" htmlFor="case-category">
          類別
        </label>
        <select
          id="case-category"
          defaultValue="OTHER"
          onChange={(event) => onChange({ category: event.target.value })}
        >
          {DISPUTE_CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {disputeCategoryLabel(value)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="case-severity">
          級別
        </label>
        <select
          id="case-severity"
          defaultValue="NORMAL"
          onChange={(event) => onChange({ severity: event.target.value })}
        >
          {DISPUTE_SEVERITIES.map((value) => (
            <option key={value} value={value}>
              {disputeSeverityLabel(value)}
            </option>
          ))}
        </select>
        <div className="t-footnote dim">
          SLA 期限由級別決定，不由人手設定 —— 人手設定會令手動開立的安全個案比系統偵測的更寬鬆。
        </div>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="case-order">
          關聯訂單 ID（選填）
        </label>
        <input
          id="case-order"
          type="text"
          placeholder="留空則為帳戶／應用程式層面的個案"
          onChange={(event) => onChange({ orderId: event.target.value })}
        />
      </div>
      <div className="field">
        <label className="field__label" htmlFor="case-against">
          對象類型（選填）
        </label>
        <select
          id="case-against"
          defaultValue=""
          onChange={(event) => onChange({ againstKind: event.target.value })}
        >
          <option value="">未指定</option>
          <option value="PASSENGER">乘客</option>
          <option value="DRIVER">司機</option>
          <option value="PLATFORM">平台</option>
        </select>
      </div>
    </div>
  );
}

function ResolveBody({
  onChange,
  role,
}: {
  onChange: (patch: { resolution?: string; note?: string; close?: boolean }) => void;
  role: string | null;
}) {
  const moneyResolutions = new Set(['CHARGE_PASSENGER', 'CHARGE_DRIVER', 'REFUND_PLATFORM_FEE', 'WAIVED_PLATFORM_FEE']);
  const canMoveMoney = role === 'FINANCE' || role === 'SUPER_ADMIN';

  return (
    <div className="stack">
      <div className="field">
        <label className="field__label" htmlFor="resolve-kind">
          裁決
        </label>
        <select
          id="resolve-kind"
          defaultValue="NONE"
          onChange={(event) => onChange({ resolution: event.target.value })}
        >
          {DISPUTE_RESOLUTIONS.map((value) => (
            <option key={value} value={value} disabled={moneyResolutions.has(value) && !canMoveMoney}>
              {disputeResolutionLabel(value)}
              {moneyResolutions.has(value) && !canMoveMoney ? '（需要財務權限）' : ''}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="resolve-note">
          理由（必填）
        </label>
        <textarea
          id="resolve-note"
          rows={4}
          placeholder="解釋為何這樣裁決。此理由會寫入審計紀錄。"
          onChange={(event) => onChange({ note: event.target.value })}
        />
        <div className="t-footnote dim">
          即使裁決為「不作收費」亦必須填寫 —— 「已決定不作收費」與「尚未決定」必須可以區分。
        </div>
      </div>
      <label className="row" style={{ gap: 8 }}>
        <input type="checkbox" onChange={(event) => onChange({ close: event.target.checked })} />
        <span>同時結案</span>
      </label>
    </div>
  );
}
