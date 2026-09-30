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
import { formatTime, refundStatusLabel, refundStatusTone } from '../lib/labels';
import { PageHead } from '../app/Shell';

const FILTERS: { value: RefundStatus | null; label: string }[] = [
  { value: 'PENDING', label: '待審批' },
  { value: null, label: '全部' },
  { value: 'APPROVED', label: '已批准' },
  { value: 'REJECTED', label: '已拒絕' },
];

/** A UUID's first segment — enough to correlate a row with a ledger entry. */
function shortId(id: string): string {
  return id.slice(0, 8);
}

export function RefundsPage() {
  const { client, notify, refreshBadges } = useApp();
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
      title: approve ? '批准退款' : '拒絕退款',
      confirmLabel: approve ? '批准並退款' : '拒絕',
      // Approving moves money out and terminates the account — irreversibly.
      danger: approve,
      body: (
        <div className="stack">
          <div>
            <div>
              金額 <Money value={refund.amount_hkd} />
            </div>
            <div className="dim t-caption1">
              司機 <span className="mono">{shortId(refund.driver_profile_id)}</span>
            </div>
          </div>
          {approve ? (
            <p style={{ margin: 0, color: 'var(--danger)' }}>
              批准會實際付出按金餘額，並終止該司機帳戶。此操作不可回復，而且同一筆申請只能批核一次。
            </p>
          ) : (
            <p className="dim" style={{ margin: 0 }}>
              拒絕會解除按金凍結，司機帳戶回復啟用。
            </p>
          )}
          {refund.note ? (
            <p className="dim" style={{ margin: 0 }}>
              司機備註：{refund.note}
            </p>
          ) : null}
          <div className="field">
            <label className="field__label" htmlFor="refund-note">
              批核備註
            </label>
            <textarea
              id="refund-note"
              rows={3}
              placeholder="批核備註（會記錄在申請上）"
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
        notify(`退款申請已${refundStatusLabel(result.status)}。`);
        reload();
      },
    });
  }

  return (
    <>
      <PageHead
        title="退款申請"
        subtitle="申請會凍結司機全部按金並暫停帳戶；批准是唯一會實際付款的操作，並會終止該帳戶。"
      />

      <div className="row" style={{ marginBottom: 16 }}>
        {FILTERS.map((item) => (
          <button
            key={item.label}
            type="button"
            className={item.value === filter ? 'btn btn--primary btn--sm' : 'btn btn--sm'}
            onClick={() => setFilter(item.value)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {data ? (
        data.length === 0 ? (
          <div className="card">
            <div className="empty">沒有符合條件的退款申請。</div>
          </div>
        ) : (
          <div className="card">
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>申請編號</th>
                    <th>司機</th>
                    <th>金額</th>
                    <th>狀態</th>
                    <th>申請時間</th>
                    <th>申請備註</th>
                    <th>操作</th>
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
                        <Chip tone={refundStatusTone(refund.status)}>
                          {refundStatusLabel(refund.status)}
                        </Chip>
                      </td>
                      <td>{formatTime(refund.created_at)}</td>
                      <td>{refund.note || <span className="dim">—</span>}</td>
                      <td>
                        {refund.status === 'PENDING' ? (
                          <div className="row">
                            <button
                              type="button"
                              className="btn btn--primary btn--sm"
                              onClick={() => decide(refund, true)}
                            >
                              批准
                            </button>
                            <button
                              type="button"
                              className="btn btn--sm"
                              onClick={() => decide(refund, false)}
                            >
                              拒絕
                            </button>
                          </div>
                        ) : (
                          <span className="dim">
                            {refund.decided_at ? formatTime(refund.decided_at) : '—'}
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
