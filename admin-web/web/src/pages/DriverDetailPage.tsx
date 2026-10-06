/**
 * One driver, in full.
 *
 * The KYC queue (`KycPage`) shows a row per driver — enough to move a status
 * forward, not enough to *decide*. This page is the decision surface: the
 * identity documents, the deposit against its threshold, the statement, the
 * refund history, and the fleet the driver is billed under today.
 *
 * **One call, not five.** The whole page comes from `GET /admin/drivers/{id}`,
 * composed server-side. A page that fired five independent requests could render
 * a half-true driver — statement loaded, deposit not — and would have no way to
 * say so. See `app/api/admin.py`.
 *
 * **The actions are the same as the queue's, and for the same reason.** Review
 * moves the state machine (`approve` lands on `DEPOSIT_REQUIRED`, not on the
 * road); a deposit grant is what actually activates dispatch. They live here too
 * because an operator who has just read the whole history should not have to go
 * back to the list to act on it.
 */

import { Link, useParams } from 'react-router-dom';
import { useApp } from '../app/AppContext';
import { useFormDialog } from '../app/useDialogs';
import { useLoad } from '../app/useLoad';
import { endpoints } from '../api/endpoints';
import type { DriverProfileDetail } from '../api/types';
import { Card, Chip, DetailRow, Empty, Money, Percent, Rows } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { useI18n } from '../i18n';
import { formatTime, shortId, useLabels } from '../lib/labels';

export function DriverDetailPage() {
  const { driverId = '' } = useParams();
  const { client, notify, refreshBadges } = useApp();
  const { t, formatLocale } = useI18n();
  const labels = useLabels();
  const reviewDialog = useFormDialog();
  const grantDialog = useFormDialog();

  const { data, error, loading, reload } = useLoad(
    () => endpoints.drivers.detail(client, driverId),
    [client, driverId],
  );

  /**
   * A review or a grant can move a driver out of the KYC queue, so the sidebar
   * count is stale the moment either succeeds. Refreshed alongside the reload
   * rather than on a timer.
   */
  async function reloadAll() {
    reload();
    await refreshBadges();
  }

  function review(driver: DriverProfileDetail, decision: string, title: string) {
    const destructive = decision === 'reject' || decision === 'terminate';
    let note = '';
    reviewDialog.open({
      title,
      confirmLabel: t('driverDetail.reviewConfirm'),
      danger: destructive,
      body: <ReviewBody driver={driver} decision={decision} onChange={(v) => (note = v)} />,
      onSubmit: async () => {
        const result = await endpoints.drivers.review(client, driver.id, {
          decision,
          note: note.trim(),
        });
        notify(t('driverDetail.reviewed', { status: labels.driverStatus(result.status) }));
        void reloadAll();
      },
    });
  }

  function grant(driver: DriverProfileDetail) {
    const shortfall = driver.deposit?.shortfall_hkd;
    const suggested = shortfall && Number(shortfall) > 0 ? String(Number(shortfall)) : '500';
    const form = { amount: suggested, note: '', reference: '' };
    grantDialog.open({
      title: t('driverDetail.depositTitle2'),
      confirmLabel: t('driverDetail.depositConfirm'),
      body: <GrantBody driver={driver} onChange={(patch) => Object.assign(form, patch)} />,
      onSubmit: async () => {
        const value = Number(form.amount);
        if (!Number.isFinite(value) || value <= 0) {
          throw new Error(t('driverDetail.depositAmountErr'));
        }
        const result = await endpoints.drivers.grantDeposit(client, driver.id, {
          amountHkd: String(value),
          note: form.note.trim(),
          ...(form.reference.trim() ? { reference: form.reference.trim() } : {}),
        });
        notify(
          result.is_fulfilled
            ? t('driverDetail.depositedMet', { balance: result.balance_hkd })
            : t('driverDetail.deposited', { balance: result.balance_hkd }),
        );
        void reloadAll();
      },
    });
  }

  if (loading) return <LoadingState />;
  if (error) {
    return (
      <div>
        <div style={{ marginBottom: 12 }}>
          <Link className="btn" to="/kyc">
            {t('driverDetail.back')}
          </Link>
        </div>
        <ErrorState error={error} onRetry={reload} />
      </div>
    );
  }
  if (!data) return null;

  const driver = data;
  const ledger = driver.ledger?.items ?? [];
  const refunds = driver.refunds?.items ?? [];

  return (
    <div>
      {/* ------------------------------------------------------------ header */}
      <Card className="card--pad">
        <div className="spread">
          <div style={{ minWidth: 0 }}>
            <div className="row-inline">
              <h1 style={{ margin: 0 }}>
                <span className="mono">{driver.vehicle_reg_mark ?? '—'}</span>
              </h1>
              <Chip tone={labels.driverStatusTone(driver.status)}>{labels.driverStatus(driver.status)}</Chip>
              {driver.is_online ? <Chip tone="ok">{t('driverDetail.online')}</Chip> : null}
            </div>
            <div className="dim" style={{ marginTop: 4 }}>
              <span className="mono">{shortId(driver.id)}</span>
            </div>
          </div>
          <div className="actions">
            <DriverActions driver={driver} onReview={review} onGrant={grant} />
            <Link className="btn" to="/kyc">
              {t('driverDetail.backShort')}
            </Link>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <Rows>
            <DetailRow label={t('driverDetail.taxiType')}>{labels.taxiType(driver.taxi_type)}</DetailRow>
            <DetailRow label={t('driverDetail.taxiPass')}>
              <span className="mono">{driver.taxi_driver_plate_no ?? '—'}</span>
            </DetailRow>
            <DetailRow label={t('driverDetail.idLast4')}>
              <span className="mono">
                {driver.hk_id_last4 ? `****${driver.hk_id_last4}` : '—'}
              </span>
            </DetailRow>
            <DetailRow label={t('driverDetail.profileId')}>
              <span className="mono">{driver.id ?? '—'}</span>
            </DetailRow>
            <DetailRow label={t('driverDetail.accountId')}>
              <span className="mono">{driver.user_id ?? '—'}</span>
            </DetailRow>
            <DetailRow label={t('driverDetail.kycReviewed')}>
              {driver.kyc_reviewed_at ? formatTime(driver.kyc_reviewed_at, formatLocale) : t('driverDetail.kycNotReviewed')}
            </DetailRow>
            <DetailRow label={t('driverDetail.createdDate')}>{formatTime(driver.created_at, formatLocale)}</DetailRow>
            <DetailRow label={t('driverDetail.lastUpdate')}>{formatTime(driver.updated_at, formatLocale)}</DetailRow>
          </Rows>
        </div>
      </Card>

      {/* ----------------------------------------------------------- deposit */}
      <h2>{t('driverDetail.depositTitle')}</h2>
      <DepositSection driver={driver} />

      {/* ------------------------------------------------------------- fleet */}
      <h2>{t('driverDetail.fleetTitle')}</h2>
      {driver.fleet ? (
        <Card className="card--pad">
          <Rows>
            <DetailRow label={t('driverDetail.fleetName')}>{driver.fleet.name}</DetailRow>
            <DetailRow label={t('driverDetail.fleetLicence')}>
              <span className="mono">{driver.fleet.license_no ?? '—'}</span>
            </DetailRow>
            <DetailRow label={t('driverDetail.fleetStatus')}>{labels.fleetStatus(driver.fleet.status)}</DetailRow>
            <DetailRow label={t('driverDetail.rosterRole')}>{labels.memberRole(driver.fleet.member_role)}</DetailRow>
            <DetailRow label={t('driverDetail.joinedAt')}>{formatTime(driver.fleet.joined_at, formatLocale)}</DetailRow>
            <DetailRow label={t('driverDetail.fleetDiscount')}>
              <Percent value={driver.fleet.weekly_fee_discount_percent} />
            </DetailRow>
          </Rows>
          <p className="dim" style={{ margin: '14px 0 0' }}>
            {t('driverDetail.fleetNote')}
          </p>
        </Card>
      ) : (
        <Card>
          <Empty title={t('driverDetail.fleetEmpty')} />
        </Card>
      )}

      {/* ------------------------------------------------------------ ledger */}
      <h2>{t('driverDetail.ledgerTitle')}</h2>
      <Card>
        {ledger.length === 0 ? (
          <Empty title={t('driverDetail.ledgerEmpty')} />
        ) : (
          <div className="table-wrap">
              <table className="data">
            <thead>
              <tr>
                <th scope="col">{t('driverDetail.colTime')}</th>
                <th scope="col">{t('driverDetail.colType')}</th>
                <th scope="col" className="num">{t('driverDetail.colAmount')}</th>
                <th scope="col" className="num">{t('driverDetail.colBalance')}</th>
                <th scope="col">{t('driverDetail.colNote')}</th>
                <th scope="col">{t('driverDetail.colRef')}</th>
              </tr>
            </thead>
            <tbody>
              {ledger.map((entry) => (
                <tr key={entry.id}>
                  <td>{formatTime(entry.created_at, formatLocale)}</td>
                  <td>
                    <Chip tone={labels.entryTone(entry.entry_type)}>{labels.entry(entry.entry_type)}</Chip>
                  </td>
                  <td className="num">
                    <SignedMoney value={entry.amount_hkd} />
                  </td>
                  <td className="num">
                    <Money value={entry.balance_after_hkd} />
                  </td>
                  <td>{entry.note || <span className="dim">—</span>}</td>
                  <td>
                    {entry.reference ? (
                      <span className="mono t-caption1">
                        {entry.reference}
                      </span>
                    ) : (
                      <span className="dim">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
            </div>
        )}
      </Card>

      {/* ----------------------------------------------------------- refunds */}
      <h2>{t('driverDetail.refundTitle')}</h2>
      <Card>
        {refunds.length === 0 ? (
          <Empty title={t('driverDetail.refundEmpty')} />
        ) : (
          <div className="table-wrap">
              <table className="data">
            <thead>
              <tr>
                <th scope="col">{t('driverDetail.colRequested')}</th>
                <th scope="col" className="num">{t('driverDetail.colAmount')}</th>
                <th scope="col">{t('driverDetail.colStatus')}</th>
                <th scope="col">{t('driverDetail.colReason')}</th>
                <th scope="col">{t('driverDetail.colReviewNote')}</th>
                <th scope="col">{t('driverDetail.colReviewedAt')}</th>
              </tr>
            </thead>
            <tbody>
              {refunds.map((refund) => (
                <tr key={refund.id}>
                  <td>{formatTime(refund.created_at, formatLocale)}</td>
                  <td className="num">
                    <Money value={refund.amount_hkd} />
                  </td>
                  <td>
                    <Chip tone={labels.refundStatusTone(refund.status)}>
                      {labels.refundStatus(refund.status)}
                    </Chip>
                  </td>
                  <td>{refund.note || <span className="dim">—</span>}</td>
                  <td>{refund.decision_note || <span className="dim">—</span>}</td>
                  <td>
                    {refund.decided_at ? formatTime(refund.decided_at, formatLocale) : <span className="dim">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
            </div>
        )}
      </Card>

      {reviewDialog.element}
      {grantDialog.element}
    </div>
  );
}

/**
 * The same transitions the queue offers, so the page is actionable.
 * `approve` from PENDING_KYC does **not** put the driver on the road — it moves
 * them to DEPOSIT_REQUIRED. Activation is the deposit's job.
 */
function DriverActions({
  driver,
  onReview,
  onGrant,
}: {
  driver: DriverProfileDetail;
  onReview: (driver: DriverProfileDetail, decision: string, title: string) => void;
  onGrant: (driver: DriverProfileDetail) => void;
}) {
  const { t } = useI18n();
  const buttons: React.ReactNode[] = [];

  if (driver.status === 'PENDING_KYC') {
    buttons.push(
      <button
        key="approve"
        type="button"
        className="btn btn--primary"
        onClick={() => onReview(driver, 'approve', t('driverDetail.approveTitle'))}
      >
        {t('driverDetail.approve')}
      </button>,
      <button
        key="reject"
        type="button"
        className="btn btn--danger"
        onClick={() => onReview(driver, 'reject', t('driverDetail.rejectTitle'))}
      >
        {t('driverDetail.reject')}
      </button>,
    );
  }

  if (driver.status === 'DEPOSIT_REQUIRED') {
    buttons.push(
      <button key="grant" type="button" className="btn btn--primary" onClick={() => onGrant(driver)}>
        {t('driverDetail.deposit')}
      </button>,
    );
  }

  if (driver.status === 'ACTIVE') {
    buttons.push(
      <button
        key="suspend"
        type="button"
        className="btn"
        onClick={() => onReview(driver, 'suspend', t('driverDetail.suspendTitle'))}
      >
        {t('driverDetail.suspend')}
      </button>,
      <button
        key="terminate"
        type="button"
        className="btn btn--danger"
        onClick={() => onReview(driver, 'terminate', t('driverDetail.terminateTitle'))}
      >
        {t('driverDetail.terminate')}
      </button>,
    );
  }

  if (driver.status === 'SUSPENDED') {
    buttons.push(
      <button
        key="restore"
        type="button"
        className="btn"
        onClick={() => onReview(driver, 'approve', t('driverDetail.restoreTitle'))}
      >
        {t('driverDetail.restore')}
      </button>,
      <button
        key="terminate"
        type="button"
        className="btn btn--danger"
        onClick={() => onReview(driver, 'terminate', t('driverDetail.terminateTitle'))}
      >
        {t('driverDetail.terminate')}
      </button>,
    );
  }

  return <>{buttons}</>;
}

function DepositSection({ driver }: { driver: DriverProfileDetail }) {
  const { t } = useI18n();
  const deposit = driver.deposit;
  const required = Number(deposit?.required_hkd ?? 0);
  const balance = Number(deposit?.balance_hkd ?? 0);
  const progress = required > 0 ? Math.min(100, Math.max(0, Math.round((balance / required) * 100))) : 0;
  const fulfilled = deposit?.is_fulfilled ?? false;

  return (
    <Card className="card--pad">
      <Rows>
        <DetailRow label={t('driverDetail.depositAvailable')}>
          <Money value={deposit?.balance_hkd ?? '0.00'} sign />
        </DetailRow>
        <DetailRow label={t('driverDetail.depositFrozen')}>
          <Money value={deposit?.held_hkd ?? '0.00'} />
        </DetailRow>
        <DetailRow label={t('driverDetail.depositRequired')}>
          <Money value={deposit?.required_hkd ?? '0.00'} />
        </DetailRow>
        <DetailRow label={t('driverDetail.depositGap')}>
          {fulfilled ? (
            <Chip tone="ok">{t('driverDetail.depositMet')}</Chip>
          ) : (
            <span className="danger">
              <Money value={deposit?.shortfall_hkd ?? '0.00'} />
            </span>
          )}
        </DetailRow>
        <DetailRow label={t('driverDetail.depositAccount')}>
          {deposit?.has_account ? t('driverDetail.depositAccountYes') : <span className="dim">{t('driverDetail.depositAccountNo')}</span>}
        </DetailRow>
      </Rows>
      <div className="meter" style={{ marginTop: 14 }}>
        {/* Red-gain / green-loss is the HK convention; here the bar is a
            threshold meter, so it goes green only once the deposit is met. */}
        <div
          className="meter__fill"
          style={{ width: `${progress}%`, background: fulfilled ? 'var(--ok)' : 'var(--warn)' }}
        />
      </div>
      <div className="dim" style={{ marginTop: 6, fontSize: '12.5px' }}>
        <Money value={deposit?.balance_hkd ?? '0.00'} sign /> /{' '}
        <Money value={deposit?.required_hkd ?? '0.00'} /> · {progress}%
      </div>
    </Card>
  );
}

/** A ledger amount, signed. The server stores a deduction as negative, so the
 * sign comes from the value, not from a guess about the entry type. */
function SignedMoney({ value }: { value: string }) {
  return <Money value={value} sign />;
}

function ReviewBody({
  driver,
  decision,
  onChange,
}: {
  driver: DriverProfileDetail;
  decision: string;
  onChange: (value: string) => void;
}) {
  const { t } = useI18n();
  const labels = useLabels();
  return (
    <>
      <p style={{ margin: '0 0 12px' }}>
        {t('driverDetail.dialogPlate', { plate: '' })}<span className="mono">{driver.vehicle_reg_mark ?? '—'}</span>
        {t('common.parenOpen')}
        {labels.taxiType(driver.taxi_type)}
        {t('common.parenClose')}
      </p>
      {decision === 'approve' && driver.status === 'PENDING_KYC' ? (
        <p className="dim" style={{ margin: '0 0 12px' }}>
          {t('driverDetail.dialogApproveNote')}
        </p>
      ) : null}
      {decision === 'approve' && driver.status === 'SUSPENDED' ? (
        <p className="dim" style={{ margin: '0 0 12px' }}>
          {t('driverDetail.dialogRestoreNote')}
        </p>
      ) : null}
      {decision === 'terminate' ? (
        <p className="danger" style={{ margin: '0 0 12px' }}>
          {t('driverDetail.dialogTerminateNote')}
        </p>
      ) : null}
      <label className="field">
        <span>{t('driverDetail.dialogNote')}</span>
        <textarea
          rows={3}
          placeholder={t('driverDetail.dialogNotePlaceholder')}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
    </>
  );
}

function GrantBody({
  driver,
  onChange,
}: {
  driver: DriverProfileDetail;
  onChange: (patch: { amount?: string; note?: string; reference?: string }) => void;
}) {
  const { t } = useI18n();
  const shortfall = driver.deposit?.shortfall_hkd;
  const suggested = shortfall && Number(shortfall) > 0 ? String(Number(shortfall)) : '500';

  return (
    <>
      <p className="dim" style={{ margin: '0 0 12px' }}>
        {t('driverDetail.depositPlate', { plate: '' })}<span className="mono">{driver.vehicle_reg_mark ?? '—'}</span>{t('driverDetail.depositNote')}
      </p>
      <label className="field">
        <span>{t('driverDetail.depositAmount')}</span>
        <input
          type="number"
          min="1"
          step="0.01"
          defaultValue={suggested}
          onChange={(e) => onChange({ amount: e.target.value })}
        />
      </label>
      <label className="field">
        <span>{t('driverDetail.depositNoteLabel')}</span>
        <input
          type="text"
          placeholder={t('driverDetail.depositNotePlaceholder')}
          onChange={(e) => onChange({ note: e.target.value })}
        />
      </label>
      <label className="field">
        <span>{t('driverDetail.depositRef')}</span>
        <input
          type="text"
          placeholder={t('driverDetail.depositRefPlaceholder')}
          onChange={(e) => onChange({ reference: e.target.value })}
        />
        <span className="field__hint">{t('driverDetail.depositRefHint')}</span>
      </label>
    </>
  );
}
