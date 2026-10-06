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
import { formatCountdown, formatTime, shortId, useLabels } from '../lib/labels';
import { useI18n } from '../i18n';
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
  const { t } = useI18n();
  const labels = useLabels();
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
      title: t('disputes.create'),
      confirmLabel: t('disputes.createConfirm'),
      body: <OpenCaseBody onChange={(patch) => Object.assign(form, patch)} />,
      onSubmit: async () => {
        if (!form.summary.trim()) throw new Error(t('disputes.errSummary'));
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
        title={t('disputes.title')}
        subtitle={t('disputes.sub')}
        actions={
          <button type="button" className="btn btn--primary" onClick={openCase}>
            {t('disputes.create')}
          </button>
        }
      />

      {data ? <StatsStrip stats={data.stats} /> : null}

      <div className="filters">
        <button
          type="button"
          className={openOnly ? 'chip chip--action chip--brand' : 'chip chip--action'}
          aria-pressed={openOnly}
          onClick={() => setOpenOnly(!openOnly)}
        >
          {t('disputes.filterOpen')}
        </button>
        <button
          type="button"
          className={overdueOnly ? 'chip chip--action chip--brand' : 'chip chip--action'}
          aria-pressed={overdueOnly}
          onClick={() => setOverdueOnly(!overdueOnly)}
        >
          {t('disputes.filterOverdue')}
        </button>
        <button
          type="button"
          className={unassignedOnly ? 'chip chip--action chip--brand' : 'chip chip--action'}
          aria-pressed={unassignedOnly}
          onClick={() => setUnassignedOnly(!unassignedOnly)}
        >
          {t('disputes.filterUnassigned')}
        </button>
        <button
          type="button"
          className={severity === '' ? 'chip chip--action chip--brand' : 'chip chip--action'}
          aria-pressed={severity === ''}
          onClick={() => setSeverity('')}
        >
          {t('disputes.filterAllSeverity')}
        </button>
        {DISPUTE_SEVERITIES.map((value) => (
          <button
            key={value}
            type="button"
            className={severity === value ? 'chip chip--action chip--brand' : 'chip chip--action'}
            aria-pressed={severity === value}
            onClick={() => setSeverity(value)}
          >
            {labels.disputeSeverity(value)}
          </button>
        ))}
      </div>

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {!loading && !error ? (
        <Card>
          {(data?.items ?? []).length === 0 ? (
            <Empty
              title={openOnly ? t('disputes.emptyOpen') : t('disputes.empty')}
              hint={t('disputes.emptyHint')}
            />
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th scope="col">{t('disputes.colDue')}</th>
                    <th scope="col">{t('disputes.colSeverity')}</th>
                    <th scope="col">{t('disputes.colCategory')}</th>
                    <th scope="col">{t('disputes.colSummary')}</th>
                    <th scope="col">{t('disputes.colStatus')}</th>
                    <th scope="col">{t('disputes.colAssignee')}</th>
                    <th scope="col" />
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
                          {formatCountdown(row.seconds_until_due, t)}
                        </div>
                        <div className="dim t-caption1">{t('disputes.slaBudget', { hours: row.sla_hours })}</div>
                      </td>
                      <td>
                        <Chip tone={labels.disputeSeverityTone(row.severity)}>
                          {labels.disputeSeverity(row.severity)}
                        </Chip>
                        {row.safety_flag ? (
                          <div style={{ marginTop: 4 }}>
                            <Chip tone="danger">{t('disputes.safety')}</Chip>
                          </div>
                        ) : null}
                      </td>
                      <td>{labels.disputeCategory(row.category)}</td>
                      <td>
                        <div className="truncate" style={{ maxWidth: 320 }} title={row.summary}>
                          {row.summary}
                        </div>
                        <div className="dim t-caption1">
                          {labels.disputeSource(row.source)}
                          {row.order_id ? (
                            <>
                              {t('disputes.orderRef')}
                              <span className="mono">{shortId(row.order_id)}</span>
                            </>
                          ) : (
                            t('disputes.noOrder')
                          )}
                        </div>
                      </td>
                      <td>
                        <Chip tone={labels.disputeStatusTone(row.status)}>
                          {labels.disputeStatus(row.status)}
                        </Chip>
                      </td>
                      <td className="mono">
                        {row.assigned_admin_id ? (
                          shortId(row.assigned_admin_id)
                        ) : (
                          <span className="dim">{t('disputes.unassigned')}</span>
                        )}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn btn--sm"
                          onClick={() => navigate(`/disputes/${row.id}`)}
                        >
                          {t('common.view')}
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
  const { t } = useI18n();
  return (
    <div className="grid" style={{ marginBottom: 16 }}>
      <div className="stat">
        <div className="stat__label">{t('disputes.statOpen')}</div>
        <div className="stat__value num">{stats.open}</div>
        <div className="stat__hint">{t('disputes.statOpenHint', { total: stats.total })}</div>
      </div>
      <div className="stat">
        <div className="stat__label">{t('disputes.statOverdue')}</div>
        <div className="stat__value num" style={stats.overdue > 0 ? { color: 'var(--danger)' } : undefined}>
          {stats.overdue}
        </div>
        <div className="stat__hint">{t('disputes.statOverdueHint')}</div>
      </div>
      <div className="stat">
        <div className="stat__label">{t('disputes.statUnassigned')}</div>
        <div className="stat__value num">{stats.unassigned}</div>
        <div className="stat__hint">{t('disputes.statUnassignedHint')}</div>
      </div>
      <div className="stat">
        <div className="stat__label">{t('disputes.statSafety')}</div>
        <div className="stat__value num" style={stats.safety_flag > 0 ? { color: 'var(--danger)' } : undefined}>
          {stats.safety_flag}
        </div>
        <div className="stat__hint">{t('disputes.statSafetyHint')}</div>
      </div>
    </div>
  );
}

function DisputeDetailView({ disputeId }: { disputeId: string }) {
  // `user` is deliberately not read here. The page used to pass `user.role` to
  // the resolve dialog, which is the **principal kind** (always `'ADMIN'`) and
  // therefore ranked nobody as FINANCE — see the note on `canMoveMoney` below.
  const { client, notify, hasRole } = useApp();
  const navigate = useNavigate();
  const { t, formatLocale } = useI18n();
  const labels = useLabels();
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
      notify(t('disputes.claimed'));
      reload();
    });
  }

  function addMessage(isInternal: boolean) {
    let body = '';
    noteDialog.open({
      title: isInternal ? t('disputes.noteTitle') : t('disputes.replyTitle'),
      confirmLabel: isInternal ? t('disputes.noteConfirm') : t('disputes.replyConfirm'),
      body: (
        <div className="stack">
          <p className="dim" style={{ margin: 0 }}>
            {isInternal
              ? t('disputes.noteInternal')
              : t('disputes.noteReply')}
          </p>
          <div className="field">
            <label className="field__label" htmlFor="dispute-message">
              {t('disputes.fieldContent')}
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
        if (!body.trim()) throw new Error(t('disputes.errEmptyContent'));
        await endpoints.disputes.addMessage(client, disputeId, body.trim(), isInternal);
        notify(isInternal ? t('disputes.noteAdded') : t('disputes.replySent'));
        reload();
      },
    });
  }

  function changeStatus(status: string) {
    void guard(async () => {
      await endpoints.disputes.setStatus(client, disputeId, status);
      notify(t('disputes.statusChanged', { status: labels.disputeStatus(status) }));
      reload();
    });
  }

  function resolve() {
    const form = { resolution: 'NONE', note: '', close: false };
    resolveDialog.open({
      title: t('disputes.resolveTitle'),
      confirmLabel: t('disputes.resolveConfirm'),
      // A money-moving resolution is the irreversible one, so it is the
      // destructive-styled button.
      body: (
        <ResolveBody
          onChange={(patch) => Object.assign(form, patch)}
          // `hasRole` reads `admin_role` (the RBAC rank). Passing `user.role`
          // here passed the **principal kind** — always the literal `'ADMIN'` —
          // which matches neither `'FINANCE'` nor `'SUPER_ADMIN'`, so every
          // money-moving option was permanently disabled even for a
          // SUPER_ADMIN. The gate three lines above (`hasRole('FINANCE')`) was
          // already correct; this is the same question and must use the same
          // answer. The server narrows per request (`admin.py` `resolve_dispute`
          // → `live_admin_role` → a whitelist matched to the decision), so a
          // forged value here yields a 403 rather than a charge.
          canMoveMoney={hasRole('FINANCE')}
        />
      ),
      onSubmit: async () => {
        if (!form.note.trim()) {
          throw new Error(t('disputes.errResolveReason'));
        }
        const result = await endpoints.disputes.resolve(client, disputeId, {
          resolution: form.resolution,
          note: form.note.trim(),
          close: form.close,
        });
        notify(
          result.moves_money
            ? t('disputes.resolvedMoney', { resolution: labels.disputeResolution(result.resolution) })
            : t('disputes.resolved', { resolution: labels.disputeResolution(result.resolution) }),
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
            {t('disputes.backToList')}
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
        title={t('disputes.detailTitle')}
        actions={
          <button type="button" className="btn" onClick={() => navigate('/disputes')}>
            {t('disputes.back')}
          </button>
        }
      />

      {data.safety_flag ? (
        <div className="message message--error" style={{ marginBottom: 16 }}>
          <div>
            <strong>{t('disputes.safetyBannerTitle')}</strong>
            <div>
              {t('disputes.safetyBanner')}
            </div>
          </div>
        </div>
      ) : null}

      <Card className="card--pad">
        <div className="row-inline" style={{ marginBottom: 16 }}>
          <Chip tone={labels.disputeStatusTone(data.status)}>{labels.disputeStatus(data.status)}</Chip>
          <Chip tone={labels.disputeSeverityTone(data.severity)}>
            {labels.disputeSeverity(data.severity)}
          </Chip>
          <span className="dim">{labels.disputeCategory(data.category)}</span>
          <span
            className="num"
            style={data.is_overdue ? { color: 'var(--danger)', fontWeight: 600 } : undefined}
          >
            {formatCountdown(data.seconds_until_due, t)}
          </span>
        </div>

        <Rows>
          <DetailRow label={t('disputes.fieldSummary')}>{data.summary}</DetailRow>
          <DetailRow label={t('disputes.fieldSource')}>{labels.disputeSource(data.source)}</DetailRow>
          <DetailRow label={t('disputes.fieldParty')}>
            {data.raised_by_kind}
            {data.against_kind ? ` → ${data.against_kind}` : ''}
          </DetailRow>
          <DetailRow label={t('disputes.fieldOrder')}>
            {data.order_id ? (
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => navigate(`/orders/${data.order_id}`)}
              >
                {t('disputes.viewOrder', { id: shortId(data.order_id) })}
              </button>
            ) : (
              <span className="dim">{t('disputes.noOrderAccount')}</span>
            )}
          </DetailRow>
          <DetailRow label={t('disputes.fieldSla')}>
            {formatTime(data.sla_due_at, formatLocale)}
            <span className="dim">{t('disputes.slaNote', { hours: data.sla_hours })}</span>
          </DetailRow>
          <DetailRow label={t('disputes.fieldAssignee')}>
            {data.assigned_admin_id ? (
              <span className="mono">{shortId(data.assigned_admin_id)}</span>
            ) : (
              <span className="dim">{t('disputes.unassigned')}</span>
            )}
          </DetailRow>
          {data.resolution ? (
            <>
              <DetailRow label={t('disputes.fieldResolution')}>
                <Chip tone="ok">{labels.disputeResolution(data.resolution)}</Chip>
              </DetailRow>
              <DetailRow label={t('disputes.fieldResolutionNote')}>{data.resolution_note ?? '—'}</DetailRow>
              <DetailRow label={t('disputes.fieldResolver')}>
                <span className="mono">{shortId(data.resolved_by)}</span>
                <span className="dim"> · {formatTime(data.resolved_at, formatLocale)}</span>
              </DetailRow>
            </>
          ) : null}
          <DetailRow label={t('disputes.fieldCreated')}>{formatTime(data.created_at, formatLocale)}</DetailRow>
        </Rows>
      </Card>

      {!closed ? (
        <Card className="card--pad" >
          <h2 className="t-title3" style={{ margin: '0 0 12px' }}>
            {t('disputes.opsTitle')}
          </h2>
          {!canAct ? (
            <p className="dim" style={{ marginTop: 0 }}>
              {t('disputes.opsReadOnly')}
            </p>
          ) : null}
          <div className="row" style={{ flexWrap: 'wrap' }}>
            <button type="button" className="btn btn--sm" disabled={busy || !canAct} onClick={claim}>
              {t('disputes.opsClaim')}
            </button>
            <button
              type="button"
              className="btn btn--sm"
              disabled={busy || !canAct}
              onClick={() => addMessage(false)}
            >
              {t('disputes.opsReply')}
            </button>
            <button
              type="button"
              className="btn btn--sm"
              disabled={busy}
              onClick={() => addMessage(true)}
            >
              {t('disputes.opsNote')}
            </button>
            {DISPUTE_STATUSES.filter((s) => s !== 'CLOSED').map((value) => (
              <button
                key={value}
                type="button"
                className="btn btn--sm"
                disabled={busy || !canAct || value === data.status || value === ('RESOLVED' as never)}
                onClick={() => changeStatus(value)}
              >
                {t('disputes.opsChangeTo', { status: labels.disputeStatus(value) })}
              </button>
            ))}
            <button
              type="button"
              className="btn btn--primary btn--sm"
              disabled={busy || !canAct}
              onClick={resolve}
            >
              {t('disputes.opsResolve')}
            </button>
          </div>
          {/*
            The role split, stated rather than discovered by pressing a button
            that 403s. The server is still the authority — this is the page
            telling the operator what it already knows.
          */}
          {canAct && !canMoveMoney ? (
            <p className="dim" style={{ margin: '12px 0 0' }}>
              {t('disputes.opsMoneyNone')}
            </p>
          ) : null}
          {canAct && canMoveMoney ? (
            <p className="dim" style={{ margin: '12px 0 0' }}>
              {t('disputes.opsMoneyAll')}
            </p>
          ) : null}
        </Card>
      ) : null}

      <h2>{t('disputes.threadTitle')}</h2>
      <Card>
        {data.messages.length === 0 ? (
          <Empty title={t('disputes.threadEmpty')} />
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
                      (message.author_kind === 'ADMIN' ? t('enum.partyKind.ADMIN') : message.author_kind)}
                  </strong>
                  {message.is_internal ? <Chip tone="warn">{t('disputes.internalNote')}</Chip> : null}
                  <span className="dim t-caption1">{formatTime(message.created_at, formatLocale)}</span>
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
  const { t } = useI18n();
  const labels = useLabels();
  return (
    <div className="stack">
      <div className="field">
        <label className="field__label" htmlFor="case-summary">
          {t('disputes.createSummary')}
        </label>
        <textarea
          id="case-summary"
          rows={4}
          placeholder={t('disputes.createSummaryPlaceholder')}
          onChange={(event) => onChange({ summary: event.target.value })}
        />
      </div>
      <div className="field">
        <label className="field__label" htmlFor="case-category">
          {t('disputes.createCategory')}
        </label>
        <select
          id="case-category"
          defaultValue="OTHER"
          onChange={(event) => onChange({ category: event.target.value })}
        >
          {DISPUTE_CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {labels.disputeCategory(value)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="case-severity">
          {t('disputes.createSeverity')}
        </label>
        <select
          id="case-severity"
          defaultValue="NORMAL"
          onChange={(event) => onChange({ severity: event.target.value })}
        >
          {DISPUTE_SEVERITIES.map((value) => (
            <option key={value} value={value}>
              {labels.disputeSeverity(value)}
            </option>
          ))}
        </select>
        <div className="t-footnote dim">
          {t('disputes.createSlaNote')}
        </div>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="case-order">
          {t('disputes.createOrderId')}
        </label>
        <input
          id="case-order"
          type="text"
          placeholder={t('disputes.createOrderIdPlaceholder')}
          onChange={(event) => onChange({ orderId: event.target.value })}
        />
      </div>
      <div className="field">
        <label className="field__label" htmlFor="case-against">
          {t('disputes.createPartyType')}
        </label>
        <select
          id="case-against"
          defaultValue=""
          onChange={(event) => onChange({ againstKind: event.target.value })}
        >
          <option value="">{t('disputes.createPartyUnspecified')}</option>
          <option value="PASSENGER">{t('enum.disputeParty.PASSENGER')}</option>
          <option value="DRIVER">{t('enum.disputeParty.DRIVER')}</option>
          <option value="PLATFORM">{t('enum.disputeParty.PLATFORM')}</option>
        </select>
      </div>
    </div>
  );
}

function ResolveBody({
  onChange,
  canMoveMoney,
}: {
  onChange: (patch: { resolution?: string; note?: string; close?: boolean }) => void;
  /**
   * Whether the operator's RBAC rank reaches FINANCE.
   *
   * A **boolean, not the role string**. The caller must hand over the answer to
   * the question `hasRole('FINANCE')` rather than a role name to be compared
   * here, because the server's rule is `at_least(FINANCE)` — a rank comparison.
   * Any `role === 'FINANCE' || role === 'SUPER_ADMIN'` written at this level
   * re-encodes the rank ladder, and a sixth admin role would silently fall
   * outside it.
   */
  canMoveMoney: boolean;
}) {
  const { t } = useI18n();
  const labels = useLabels();
  const moneyResolutions = new Set(['CHARGE_PASSENGER', 'CHARGE_DRIVER', 'REFUND_PLATFORM_FEE', 'WAIVED_PLATFORM_FEE']);

  return (
    <div className="stack">
      <div className="field">
        <label className="field__label" htmlFor="resolve-kind">
          {t('disputes.resolveOutcome')}
        </label>
        <select
          id="resolve-kind"
          defaultValue="NONE"
          onChange={(event) => onChange({ resolution: event.target.value })}
        >
          {DISPUTE_RESOLUTIONS.map((value) => (
            <option key={value} value={value} disabled={moneyResolutions.has(value) && !canMoveMoney}>
              {labels.disputeResolution(value)}
              {moneyResolutions.has(value) && !canMoveMoney ? t('disputes.resolveNeedFinance') : ''}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="resolve-note">
          {t('disputes.resolveReason')}
        </label>
        <textarea
          id="resolve-note"
          rows={4}
          placeholder={t('disputes.resolveReasonPlaceholder')}
          onChange={(event) => onChange({ note: event.target.value })}
        />
        <div className="t-footnote dim">
          {t('disputes.resolveReasonNote')}
        </div>
      </div>
      <label className="row" style={{ gap: 8 }}>
        <input type="checkbox" onChange={(event) => onChange({ close: event.target.checked })} />
        <span>{t('disputes.resolveCloseToo')}</span>
      </label>
    </div>
  );
}
