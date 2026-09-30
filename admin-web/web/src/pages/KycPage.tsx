/**
 * The KYC queue.
 *
 * `GET /admin/drivers` is oldest-first, so the drivers who have been waiting
 * longest surface at the top. Two decisions live here and they are different in
 * kind:
 *
 * * **Review** moves the profile through the state machine. `approve` goes to
 *   `DEPOSIT_REQUIRED` — it does *not* put the driver on the road; that happens
 *   when the deposit is fulfilled.
 * * **Grant** credits the deposit ledger. When the grant fulfils the requirement
 *   the server flips `DEPOSIT_REQUIRED -> ACTIVE` in the same call, which is what
 *   actually activates dispatch.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { AdminDriverRow, DriverStatus } from '../api/types';
import { endpoints } from '../api/endpoints';
import { Chip } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { useApp } from '../app/AppContext';
import { useFormDialog } from '../app/useDialogs';
import { useLoad } from '../app/useLoad';
import { driverStatusLabel, driverStatusTone, taxiTypeLabel } from '../lib/labels';
import { PageHead } from '../app/Shell';

const FILTERS: { value: DriverStatus | null; label: string }[] = [
  { value: null, label: '全部' },
  { value: 'PENDING_KYC', label: '審核中' },
  { value: 'DEPOSIT_REQUIRED', label: '待繳按金' },
  { value: 'ACTIVE', label: '已啟用' },
  { value: 'SUSPENDED', label: '已停權' },
  { value: 'TERMINATED', label: '已終止' },
];

export function KycPage() {
  const { client, notify, refreshBadges } = useApp();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<DriverStatus | null>('PENDING_KYC');
  const dialog = useFormDialog();

  const { data, error, loading, reload } = useLoad(
    async () => {
      const page = await endpoints.drivers.list(client, { status: filter ?? undefined, limit: 100 });
      // Keep the sidebar count honest after a review moves a driver out of the
      // queue. Best-effort: a failure here must not break the table.
      void refreshBadges();
      return page.items ?? [];
    },
    [client, filter],
  );

  function review(driver: AdminDriverRow, decision: string, title: string) {
    const destructive = decision === 'reject' || decision === 'terminate';
    let note = '';
    dialog.open({
      title,
      confirmLabel: '確認',
      danger: destructive,
      body: <ReviewBody driver={driver} decision={decision} onChange={(value) => (note = value)} />,
      onSubmit: async () => {
        const result = await endpoints.drivers.review(client, driver.id, {
          decision,
          note: note.trim(),
        });
        notify(`已更新狀態：${driverStatusLabel(result.status)}`);
        reload();
      },
    });
  }

  function grant(driver: AdminDriverRow) {
    const form = { amount: '500', note: '', reference: '' };
    dialog.open({
      title: '存入按金',
      confirmLabel: '存入',
      body: <GrantBody driver={driver} onChange={(patch) => Object.assign(form, patch)} />,
      onSubmit: async () => {
        const value = Number(form.amount);
        if (!Number.isFinite(value) || value <= 0) {
          throw new Error('金額必須大於 0。');
        }
        const result = await endpoints.drivers.grantDeposit(client, driver.id, {
          amountHkd: String(value),
          note: form.note.trim(),
          ...(form.reference.trim() ? { reference: form.reference.trim() } : {}),
        });
        notify(`已存入，餘額 ${result.balance_hkd}。`);
        reload();
      },
    });
  }

  function rowActions(driver: AdminDriverRow) {
    // The detail page first, on every row. The state-machine buttons are the
    // *decision*; this is the "let me look before I press anything" step, and it
    // is the only way to reach the statement, the deposit and the fleet.
    //
    // A real `<button>` rather than a `<Link>`, so it keeps the button role the
    // row's other actions have — and because the UI verifier reaches the detail
    // page by clicking it, which is what exercises the navigate path.
    const actions: React.ReactNode[] = [
      <button
        key="view"
        type="button"
        className="btn btn--sm"
        onClick={() => navigate(`/drivers/${driver.id}`)}
      >
        檢視
      </button>,
    ];

    if (driver.status === 'PENDING_KYC') {
      actions.push(
        <button
          key="approve"
          type="button"
          className="btn btn--primary btn--sm"
          onClick={() => review(driver, 'approve', '通過 KYC 審核')}
        >
          通過審核
        </button>,
        <button
          key="reject"
          type="button"
          className="btn btn--danger btn--sm"
          onClick={() => review(driver, 'reject', '拒絕 KYC 審核')}
        >
          拒絕
        </button>,
      );
    }

    if (driver.status === 'DEPOSIT_REQUIRED') {
      actions.push(
        <button
          key="grant"
          type="button"
          className="btn btn--primary btn--sm"
          onClick={() => grant(driver)}
        >
          存入按金
        </button>,
      );
    }

    if (driver.status === 'ACTIVE') {
      actions.push(
        <button
          key="suspend"
          type="button"
          className="btn btn--sm"
          onClick={() => review(driver, 'suspend', '暫停司機帳戶')}
        >
          停權
        </button>,
        <button
          key="terminate"
          type="button"
          className="btn btn--danger btn--sm"
          onClick={() => review(driver, 'terminate', '終止司機帳戶')}
        >
          終止
        </button>,
      );
    }

    if (driver.status === 'SUSPENDED') {
      actions.push(
        <button
          key="restore"
          type="button"
          className="btn btn--sm"
          onClick={() => review(driver, 'approve', '恢復司機帳戶')}
        >
          恢復
        </button>,
        <button
          key="terminate"
          type="button"
          className="btn btn--danger btn--sm"
          onClick={() => review(driver, 'terminate', '終止司機帳戶')}
        >
          終止
        </button>,
      );
    }

    if (actions.length === 1) return <span className="dim">—</span>;
    return <div className="row">{actions}</div>;
  }

  return (
    <>
      <PageHead
        title="司機審核"
        subtitle="通過審核後司機進入「待繳按金」；存入按金達標才會正式啟用接單。"
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
            <div className="empty">沒有符合條件的司機。</div>
          </div>
        ) : (
          <div className="card">
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>車牌</th>
                    <th>的士類型</th>
                    <th>的士證號</th>
                    <th>狀態</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((driver) => (
                    <tr key={driver.id}>
                      <td className="mono">{driver.vehicle_reg_mark ?? '—'}</td>
                      <td>{taxiTypeLabel(driver.taxi_type)}</td>
                      <td className="mono">{driver.taxi_driver_plate_no ?? '—'}</td>
                      <td>
                        <Chip tone={driverStatusTone(driver.status)}>
                          {driverStatusLabel(driver.status)}
                        </Chip>
                      </td>
                      <td>{rowActions(driver)}</td>
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

function ReviewBody({
  driver,
  decision,
  onChange,
}: {
  driver: AdminDriverRow;
  decision: string;
  onChange: (note: string) => void;
}) {
  return (
    <div className="stack">
      <p style={{ margin: 0 }}>
        車牌 <span className="mono">{driver.vehicle_reg_mark ?? '—'}</span>（
        {taxiTypeLabel(driver.taxi_type)}）
      </p>
      {decision === 'approve' && driver.status === 'PENDING_KYC' ? (
        <p className="dim" style={{ margin: 0 }}>
          通過後狀態會變成「待繳按金」，需存入按金才會正式啟用接單。
        </p>
      ) : null}
      {decision === 'approve' && driver.status === 'SUSPENDED' ? (
        <p className="dim" style={{ margin: 0 }}>
          恢復後狀態會回到「待繳按金」或「已啟用」，視按金餘額而定。
        </p>
      ) : null}
      {decision === 'terminate' ? (
        <p style={{ margin: 0, color: 'var(--danger)' }}>終止後司機無法再接單，且不可回復。</p>
      ) : null}
      <div className="field">
        <label className="field__label" htmlFor="review-note">
          備註
        </label>
        <textarea
          id="review-note"
          rows={3}
          placeholder="備註（會記錄在審核紀錄）"
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
    </div>
  );
}

function GrantBody({
  driver,
  onChange,
}: {
  driver: AdminDriverRow;
  onChange: (patch: { amount?: string; note?: string; reference?: string }) => void;
}) {
  return (
    <div className="stack">
      <p className="dim" style={{ margin: 0 }}>
        車牌 <span className="mono">{driver.vehicle_reg_mark ?? '—'}</span>
        。存入後按金達標即會自動啟用接單。
      </p>
      <div className="field">
        <label className="field__label" htmlFor="grant-amount">
          金額（HKD）
        </label>
        <input
          id="grant-amount"
          type="number"
          min={1}
          step="0.01"
          defaultValue="500"
          onChange={(event) => onChange({ amount: event.target.value })}
        />
      </div>
      <div className="field">
        <label className="field__label" htmlFor="grant-note">
          備註
        </label>
        <input
          id="grant-note"
          type="text"
          placeholder="例如：現金存入"
          onChange={(event) => onChange({ note: event.target.value })}
        />
      </div>
      <div className="field">
        <label className="field__label" htmlFor="grant-ref">
          冪等鍵（reference）
        </label>
        <input
          id="grant-ref"
          type="text"
          placeholder="留空則自動產生"
          onChange={(event) => onChange({ reference: event.target.value })}
        />
        <div className="t-footnote dim">
          帶入相同的鍵重試不會重複入帳；伺服器會加上 grant: 前綴。
        </div>
      </div>
    </div>
  );
}
