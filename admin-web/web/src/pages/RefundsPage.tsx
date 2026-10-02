/**
 * The refund queue.
 *
 * Requesting a refund **holds** the driver's whole remaining balance and
 * suspends them; no money moves until a decision here. Approving is the only
 * path that pays out: it writes a `REFUND` ledger entry keyed `refund:{id}` — so
 * a double-click cannot pay twice — and terminates the driver. Rejecting releases
 * the hold and returns them to `ACTIVE`.
 *
 * A request can be decided once. The actions are therefore only offered on
 * `PENDING` rows; the server would refuse a second decision anyway.
 */

import { useState } from 'react';
import type { RefundRow, RefundStatus } from '../api/types';
import { endpoints } from '../api/endpoints';
import { Chip, Money } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { useApp } from '../app/AppContext';
import { useFormDialog } from '../app/useDialogs';
import { useLoad } from '../app/useLoad';
import { formatTime, shortId, useLabels } from '../lib/labels';
import { useI18n } from '../i18n';
import { PageHead } from '../app/Shell';

const FILTERS: { value: RefundStatus | null; labelKey: string }[] = [
  { value: 'PENDING', labelKey: 'refunds.filterPending' },
  { value: null, labelKey: 'refunds.filterAll' },
  { value: 'APPROVED', labelKey: 'refunds.filterApproved' },
  { value: 'REJECTED', labelKey: 'refunds.filterRejected' },
];

export function RefundsPage() {
  const { client, notify, refreshBadges } = useApp();
  const { t, formatLocale } = useI18n();
  const labels = useLabels();
  const [filter, setFilter] = useState<RefundStatus | null>('PENDING');
  const dialog = useFormDialog();

  const { data, error, loading, reload } = useLoad(
    async () => {
      const page = await endpoints.refunds.list(client, {
        status: filter ?? undefined,
        limit: 100,
      });
      void refreshBadges();
      return page.items ?? [];
    },
    [client, filter],
  );

  function decide(refund: RefundRow, approve: boolean) {
    let note = '';
    dialog.open({
      title: approve ? t('refunds.approveTitle') : t('refunds.rejectTitle'),
      confirmLabel: approve ? t('refunds.approveConfirm') : t('refunds.rejectConfirm'),
      // Approving moves money out and terminates the account — irreversibly.
      danger: approve,
      body: (
        <div className="stack">
          <div>
            <div>
              {t('refunds.amount')} <Money value={refund.amount_hkd} />
            </div>
            <div className="dim t-caption1">
              {t('refunds.driver')} <span className="mono">{shortId(refund.driver_profile_id)}</span>
            </div>
          </div>
          {approve ? (
            <p style={{ margin: 0, color: 'var(--danger)' }}>
              {t('refunds.approveWarning')}
            </p>
          ) : (
            <p className="dim" style={{ margin: 0 }}>
              {t('refunds.rejectNote')}
            </p>
          )}
          {refund.note ? (
            <p className="dim" style={{ margin: 0 }}>
              {t('refunds.driverNote', { note: refund.note })}
            </p>
          ) : null}
          <div className="field">
            <label className="field__label" htmlFor="refund-note">
              {t('refunds.reviewNote')}
            </label>
            <textarea
              id="refund-note"
              rows={3}
              placeholder={t('refunds.reviewNotePlaceholder')}
              onChange={(event) => (note = event.target.value)}
            />
          </div>
        </div>
      ),
      onSubmit: async () => {
        const result = await endpoints.refunds.decide(client, refund.id, {
          approve,
          note: note.trim(),
        });
        notify(t('refunds.done', { status: labels.refundStatus(result.status) }));
        reload();
      },
    });
  }

  return (
    <>
      <PageHead
        title={t('refunds.title')}
        subtitle={t('refunds.sub')}
      />

      <div className="row" style={{ marginBottom: 16 }}>
        {FILTERS.map((item) => (
          <button
            key={item.labelKey}
            type="button"
            className={item.value === filter ? 'btn btn--primary btn--sm' : 'btn btn--sm'}
            onClick={() => setFilter(item.value)}
          >
            {t(item.labelKey)}
          </button>
        ))}
      </div>

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {data ? (
        data.length === 0 ? (
          <div className="card">
            <div className="empty">{t('refunds.empty')}</div>
          </div>
        ) : (
          <div className="card">
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>{t('refunds.colId')}</th>
                    <th>{t('refunds.colDriver')}</th>
                    <th>{t('refunds.colAmount')}</th>
                    <th>{t('refunds.colStatus')}</th>
                    <th>{t('refunds.colRequested')}</th>
                    <th>{t('refunds.colNote')}</th>
                    <th>{t('refunds.colActions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((refund) => (
                    <tr key={refund.id}>
                      <td className="mono">{shortId(refund.id)}</td>
                      <td className="mono">{shortId(refund.driver_profile_id)}</td>
                      <td>
                        <Money value={refund.amount_hkd} />
                      </td>
                      <td>
                        <Chip tone={labels.refundStatusTone(refund.status)}>
                          {labels.refundStatus(refund.status)}
                        </Chip>
                      </td>
                      <td>{formatTime(refund.created_at, formatLocale)}</td>
                      <td>{refund.note || <span className="dim">—</span>}</td>
                      <td>
                        {refund.status === 'PENDING' ? (
                          <div className="row">
                            <button
                              type="button"
                              className="btn btn--primary btn--sm"
                              onClick={() => decide(refund, true)}
                            >
                              {t('refunds.approve')}
                            </button>
                            <button
                              type="button"
                              className="btn btn--sm"
                              onClick={() => decide(refund, false)}
                            >
                              {t('refunds.reject')}
                            </button>
                          </div>
                        ) : (
                          <span className="dim">
                            {refund.decided_at ? formatTime(refund.decided_at, formatLocale) : '—'}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      ) : null}

      {dialog.element}
    </>
  );
}
