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
import { useLabels } from '../lib/labels';
import { useI18n } from '../i18n';
import { PageHead } from '../app/Shell';

const FILTERS: { value: DriverStatus | null; labelKey: string }[] = [
  { value: null, labelKey: 'kyc.filterAll' },
  { value: 'PENDING_KYC', labelKey: 'enum.driverStatus.PENDING_KYC' },
  { value: 'DEPOSIT_REQUIRED', labelKey: 'enum.driverStatus.DEPOSIT_REQUIRED' },
  { value: 'ACTIVE', labelKey: 'enum.driverStatus.ACTIVE' },
  { value: 'SUSPENDED', labelKey: 'enum.driverStatus.SUSPENDED' },
  { value: 'TERMINATED', labelKey: 'enum.driverStatus.TERMINATED' },
];

export function KycPage() {
  const { t } = useI18n();
  const labels = useLabels();
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
      confirmLabel: t('kyc.reviewConfirm'),
      danger: destructive,
      body: <ReviewBody driver={driver} decision={decision} onChange={(value) => (note = value)} />,
      onSubmit: async () => {
        const result = await endpoints.drivers.review(client, driver.id, {
          decision,
          note: note.trim(),
        });
        notify(t('kyc.reviewed', { status: labels.driverStatus(result.status) }));
        reload();
      },
    });
  }

  function grant(driver: AdminDriverRow) {
    const form = { amount: '500', note: '', reference: '' };
    dialog.open({
      title: t('kyc.depositTitle'),
      confirmLabel: t('kyc.depositConfirm'),
      body: <GrantBody driver={driver} onChange={(patch) => Object.assign(form, patch)} />,
      onSubmit: async () => {
        const value = Number(form.amount);
        if (!Number.isFinite(value) || value <= 0) {
          throw new Error(t('kyc.depositAmountErr'));
        }
        const result = await endpoints.drivers.grantDeposit(client, driver.id, {
          amountHkd: String(value),
          note: form.note.trim(),
          ...(form.reference.trim() ? { reference: form.reference.trim() } : {}),
        });
        notify(t('kyc.deposited', { balance: result.balance_hkd }));
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
        {t('kyc.view')}
      </button>,
    ];

    if (driver.status === 'PENDING_KYC') {
      actions.push(
        <button
          key="approve"
          type="button"
          className="btn btn--primary btn--sm"
          onClick={() => review(driver, 'approve', t('kyc.approveTitle'))}
        >
          {t('kyc.approve')}
        </button>,
        <button
          key="reject"
          type="button"
          className="btn btn--danger btn--sm"
          onClick={() => review(driver, 'reject', t('kyc.rejectTitle'))}
        >
          {t('kyc.reject')}
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
          {t('kyc.deposit')}
        </button>,
      );
    }

    if (driver.status === 'ACTIVE') {
      actions.push(
        <button
          key="suspend"
          type="button"
          className="btn btn--sm"
          onClick={() => review(driver, 'suspend', t('kyc.suspendTitle'))}
        >
          {t('kyc.suspend')}
        </button>,
        <button
          key="terminate"
          type="button"
          className="btn btn--danger btn--sm"
          onClick={() => review(driver, 'terminate', t('kyc.terminateTitle'))}
        >
          {t('kyc.terminate')}
        </button>,
      );
    }

    if (driver.status === 'SUSPENDED') {
      actions.push(
        <button
          key="restore"
          type="button"
          className="btn btn--sm"
          onClick={() => review(driver, 'restore', t('kyc.restoreTitle'))}
        >
          {t('kyc.restore')}
        </button>,
        <button
          key="terminate"
          type="button"
          className="btn btn--danger btn--sm"
          onClick={() => review(driver, 'terminate', t('kyc.terminateTitle'))}
        >
          {t('kyc.terminate')}
        </button>,
      );
    }

    if (actions.length === 1) return <span className="dim">—</span>;
    return <div className="row">{actions}</div>;
  }

  return (
    <>
      <PageHead
        title={t('kyc.title')}
        subtitle={t('kyc.sub')}
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
            <div className="empty">{t('kyc.empty')}</div>
          </div>
        ) : (
          <div className="card">
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>{t('kyc.colPlate')}</th>
                    <th>{t('kyc.colTaxiType')}</th>
                    <th>{t('kyc.colLicence')}</th>
                    <th>{t('kyc.colStatus')}</th>
                    <th>{t('kyc.colActions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((driver) => (
                    <tr key={driver.id}>
                      <td className="mono">{driver.vehicle_reg_mark ?? '—'}</td>
                      <td>{labels.taxiType(driver.taxi_type)}</td>
                      <td className="mono">{driver.taxi_driver_plate_no ?? '—'}</td>
                      <td>
                        <Chip tone={labels.driverStatusTone(driver.status)}>
                          {labels.driverStatus(driver.status)}
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
  const { t } = useI18n();
  const labels = useLabels();
  return (
    <div className="stack">
      <p style={{ margin: 0 }}>
        {t('kyc.dialogPlate', { plate: '' })}
        <span className="mono">{driver.vehicle_reg_mark ?? '—'}</span>
        {t('common.parenOpen')}
        {labels.taxiType(driver.taxi_type)}
        {t('common.parenClose')}
      </p>
      {decision === 'approve' && driver.status === 'PENDING_KYC' ? (
        <p className="dim" style={{ margin: 0 }}>
          {t('kyc.dialogApproveNote')}
        </p>
      ) : null}
      {decision === 'restore' && driver.status === 'SUSPENDED' ? (
        <p className="dim" style={{ margin: 0 }}>
          {t('kyc.dialogRestoreNote')}
        </p>
      ) : null}
      {decision === 'terminate' ? (
        <p style={{ margin: 0, color: 'var(--danger)' }}>{t('kyc.dialogTerminateNote')}</p>
      ) : null}
      <div className="field">
        <label className="field__label" htmlFor="review-note">
          {t('kyc.dialogNote')}
        </label>
        <textarea
          id="review-note"
          rows={3}
          placeholder={t('kyc.dialogNotePlaceholder')}
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
  const { t } = useI18n();
  return (
    <div className="stack">
      <p className="dim" style={{ margin: 0 }}>
        <span className="mono">
          {t('kyc.depositPlate', { plate: driver.vehicle_reg_mark ?? '—' })}
        </span>
        {t('kyc.depositNote')}
      </p>
      <div className="field">
        <label className="field__label" htmlFor="grant-amount">
          {t('kyc.depositAmount')}
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
          {t('kyc.depositNoteLabel')}
        </label>
        <input
          id="grant-note"
          type="text"
          placeholder={t('kyc.depositNotePlaceholder')}
          onChange={(event) => onChange({ note: event.target.value })}
        />
      </div>
      <div className="field">
        <label className="field__label" htmlFor="grant-ref">
          {t('kyc.depositRef')}
        </label>
        <input
          id="grant-ref"
          type="text"
          placeholder={t('kyc.depositRefPlaceholder')}
          onChange={(event) => onChange({ reference: event.target.value })}
        />
        <div className="t-footnote dim">
          {t('kyc.depositRefHint')}
        </div>
      </div>
    </div>
  );
}
