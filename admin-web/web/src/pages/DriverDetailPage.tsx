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
import { Card, Chip, DetailRow, Empty, Money, Rows } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import {
  driverStatusLabel,
  driverStatusTone,
  entryLabel,
  entryTone,
  formatTime,
  memberRoleLabel,
  refundStatusLabel,
  refundStatusTone,
  shortId,
  taxiTypeLabel,
} from '../lib/labels';

export function DriverDetailPage() {
  const { driverId = '' } = useParams();
  const { client, notify, refreshBadges } = useApp();
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
      confirmLabel: '確認',
      danger: destructive,
      body: <ReviewBody driver={driver} decision={decision} onChange={(v) => (note = v)} />,
      onSubmit: async () => {
        const result = await endpoints.drivers.review(client, driver.id, {
          decision,
          note: note.trim(),
        });
        notify(`已更新狀態：${driverStatusLabel(result.status)}`);
        void reloadAll();
      },
    });
  }

  function grant(driver: DriverProfileDetail) {
    const shortfall = driver.deposit?.shortfall_hkd;
    const suggested = shortfall && Number(shortfall) > 0 ? String(Number(shortfall)) : '500';
    const form = { amount: suggested, note: '', reference: '' };
    grantDialog.open({
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
        notify(
          result.is_fulfilled
            ? `已存入，按金達標，司機已啟用。餘額 ${result.balance_hkd}。`
            : `已存入，餘額 ${result.balance_hkd}，尚未達標。`,
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
            ← 返回司機審核
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
              <Chip tone={driverStatusTone(driver.status)}>{driverStatusLabel(driver.status)}</Chip>
              {driver.is_online ? <Chip tone="ok">上線中</Chip> : null}
            </div>
            <div className="dim" style={{ marginTop: 4 }}>
              <span className="mono">{shortId(driver.id)}</span>
            </div>
          </div>
          <div className="actions">
            <DriverActions driver={driver} onReview={review} onGrant={grant} />
            <Link className="btn" to="/kyc">
              ← 返回審核
            </Link>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <Rows>
            <DetailRow label="的士類型">{taxiTypeLabel(driver.taxi_type)}</DetailRow>
            <DetailRow label="的士司機證號">
              <span className="mono">{driver.taxi_driver_plate_no ?? '—'}</span>
            </DetailRow>
            <DetailRow label="身份證末四位">
              <span className="mono">
                {driver.hk_id_last4 ? `****${driver.hk_id_last4}` : '—'}
              </span>
            </DetailRow>
            <DetailRow label="司機檔案 ID">
              <span className="mono">{driver.id ?? '—'}</span>
            </DetailRow>
            <DetailRow label="帳戶 ID">
              <span className="mono">{driver.user_id ?? '—'}</span>
            </DetailRow>
            <DetailRow label="KYC 審核時間">
              {driver.kyc_reviewed_at ? formatTime(driver.kyc_reviewed_at) : '尚未審核'}
            </DetailRow>
            <DetailRow label="建立日期">{formatTime(driver.created_at)}</DetailRow>
            <DetailRow label="最後更新">{formatTime(driver.updated_at)}</DetailRow>
          </Rows>
        </div>
      </Card>

      {/* ----------------------------------------------------------- deposit */}
      <h2>按金</h2>
      <DepositSection driver={driver} />

      {/* ------------------------------------------------------------- fleet */}
      <h2>車隊</h2>
      {driver.fleet ? (
        <Card className="card--pad">
          <Rows>
            <DetailRow label="車隊名稱">{driver.fleet.name}</DetailRow>
            <DetailRow label="車隊牌照">
              <span className="mono">{driver.fleet.license_no ?? '—'}</span>
            </DetailRow>
            <DetailRow label="車隊狀態">{driver.fleet.status}</DetailRow>
            <DetailRow label="名單角色">{memberRoleLabel(driver.fleet.member_role)}</DetailRow>
            <DetailRow label="加入日期">{formatTime(driver.fleet.joined_at)}</DetailRow>
            <DetailRow label="每週費用折扣">{driver.fleet.weekly_fee_discount_percent}%</DetailRow>
          </Rows>
          <p className="dim" style={{ margin: '14px 0 0' }}>
            成員按車隊折扣價收費，不再計入平台劃一收費。
          </p>
        </Card>
      ) : (
        <Card>
          <Empty title="不在任何車隊名單上，按平台劃一費用收費。" />
        </Card>
      )}

      {/* ------------------------------------------------------------ ledger */}
      <h2>帳目</h2>
      <Card>
        {ledger.length === 0 ? (
          <Empty title="還沒有任何帳目紀錄。" />
        ) : (
          <div className="table-wrap">
              <table className="data">
            <thead>
              <tr>
                <th>時間</th>
                <th>類型</th>
                <th className="num">金額</th>
                <th className="num">結餘</th>
                <th>備註</th>
                <th>參考</th>
              </tr>
            </thead>
            <tbody>
              {ledger.map((entry) => (
                <tr key={entry.id}>
                  <td>{formatTime(entry.created_at)}</td>
                  <td>
                    <Chip tone={entryTone(entry.entry_type)}>{entryLabel(entry.entry_type)}</Chip>
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
                      <span className="mono" style={{ fontSize: 12 }}>
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
      <h2>退款紀錄</h2>
      <Card>
        {refunds.length === 0 ? (
          <Empty title="沒有退款紀錄。" />
        ) : (
          <div className="table-wrap">
              <table className="data">
            <thead>
              <tr>
                <th>申請時間</th>
                <th className="num">金額</th>
                <th>狀態</th>
                <th>申請原因</th>
                <th>審批備註</th>
                <th>審批時間</th>
              </tr>
            </thead>
            <tbody>
              {refunds.map((refund) => (
                <tr key={refund.id}>
                  <td>{formatTime(refund.created_at)}</td>
                  <td className="num">
                    <Money value={refund.amount_hkd} />
                  </td>
                  <td>
                    <Chip tone={refundStatusTone(refund.status)}>
                      {refundStatusLabel(refund.status)}
                    </Chip>
                  </td>
                  <td>{refund.note || <span className="dim">—</span>}</td>
                  <td>{refund.decision_note || <span className="dim">—</span>}</td>
                  <td>
                    {refund.decided_at ? formatTime(refund.decided_at) : <span className="dim">—</span>}
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
  const buttons: React.ReactNode[] = [];

  if (driver.status === 'PENDING_KYC') {
    buttons.push(
      <button
        key="approve"
        type="button"
        className="btn btn--primary"
        onClick={() => onReview(driver, 'approve', '通過 KYC 審核')}
      >
        通過審核
      </button>,
      <button
        key="reject"
        type="button"
        className="btn btn--danger"
        onClick={() => onReview(driver, 'reject', '拒絕 KYC 審核')}
      >
        拒絕
      </button>,
    );
  }

  if (driver.status === 'DEPOSIT_REQUIRED') {
    buttons.push(
      <button key="grant" type="button" className="btn btn--primary" onClick={() => onGrant(driver)}>
        存入按金
      </button>,
    );
  }

  if (driver.status === 'ACTIVE') {
    buttons.push(
      <button
        key="suspend"
        type="button"
        className="btn"
        onClick={() => onReview(driver, 'suspend', '暫停司機帳戶')}
      >
        停權
      </button>,
      <button
        key="terminate"
        type="button"
        className="btn btn--danger"
        onClick={() => onReview(driver, 'terminate', '終止司機帳戶')}
      >
        終止
      </button>,
    );
  }

  if (driver.status === 'SUSPENDED') {
    buttons.push(
      <button
        key="restore"
        type="button"
        className="btn"
        onClick={() => onReview(driver, 'approve', '恢復司機帳戶')}
      >
        恢復
      </button>,
      <button
        key="terminate"
        type="button"
        className="btn btn--danger"
        onClick={() => onReview(driver, 'terminate', '終止司機帳戶')}
      >
        終止
      </button>,
    );
  }

  return <>{buttons}</>;
}

function DepositSection({ driver }: { driver: DriverProfileDetail }) {
  const deposit = driver.deposit;
  const required = Number(deposit?.required_hkd ?? 0);
  const balance = Number(deposit?.balance_hkd ?? 0);
  const progress = required > 0 ? Math.min(100, Math.round((balance / required) * 100)) : 0;
  const fulfilled = deposit?.is_fulfilled ?? false;

  return (
    <Card className="card--pad">
      <Rows>
        <DetailRow label="可用餘額">
          <Money value={deposit?.balance_hkd ?? '0.00'} />
        </DetailRow>
        <DetailRow label="凍結金額">
          <Money value={deposit?.held_hkd ?? '0.00'} />
        </DetailRow>
        <DetailRow label="要求金額">
          <Money value={deposit?.required_hkd ?? '0.00'} />
        </DetailRow>
        <DetailRow label="距達標">
          {fulfilled ? (
            <Chip tone="ok">已達標</Chip>
          ) : (
            <span className="danger">
              <Money value={deposit?.shortfall_hkd ?? '0.00'} />
            </span>
          )}
        </DetailRow>
        <DetailRow label="按金帳戶">
          {deposit?.has_account ? '已建立' : <span className="dim">尚未建立（從未存入）</span>}
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
        <Money value={deposit?.balance_hkd ?? '0.00'} /> /{' '}
        <Money value={deposit?.required_hkd ?? '0.00'} /> · {progress}%
      </div>
    </Card>
  );
}

/** A ledger amount, signed so a deduction reads as a deduction. */
function SignedMoney({ value }: { value: string }) {
  const negative = value.trim().startsWith('-');
  return (
    <span className={negative ? 'loss' : 'gain'}>
      <Money value={value} />
    </span>
  );
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
  return (
    <>
      <p style={{ margin: '0 0 12px' }}>
        車牌 <span className="mono">{driver.vehicle_reg_mark ?? '—'}</span>（
        {taxiTypeLabel(driver.taxi_type)}）
      </p>
      {decision === 'approve' && driver.status === 'PENDING_KYC' ? (
        <p className="dim" style={{ margin: '0 0 12px' }}>
          通過後狀態會變成「待繳按金」，需存入按金才會正式啟用接單。
        </p>
      ) : null}
      {decision === 'approve' && driver.status === 'SUSPENDED' ? (
        <p className="dim" style={{ margin: '0 0 12px' }}>
          恢復後狀態會回到「待繳按金」或「已啟用」，視按金餘額而定。
        </p>
      ) : null}
      {decision === 'terminate' ? (
        <p className="danger" style={{ margin: '0 0 12px' }}>
          終止後司機無法再接單，且不可回復。
        </p>
      ) : null}
      <label className="field">
        <span>備註</span>
        <textarea
          rows={3}
          placeholder="備註（會記錄在審核紀錄）"
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
  const shortfall = driver.deposit?.shortfall_hkd;
  const suggested = shortfall && Number(shortfall) > 0 ? String(Number(shortfall)) : '500';

  return (
    <>
      <p className="dim" style={{ margin: '0 0 12px' }}>
        車牌 <span className="mono">{driver.vehicle_reg_mark ?? '—'}</span>。存入後按金達標即會自動啟用接單。
      </p>
      <label className="field">
        <span>金額（HKD）</span>
        <input
          type="number"
          min="1"
          step="0.01"
          defaultValue={suggested}
          onChange={(e) => onChange({ amount: e.target.value })}
        />
      </label>
      <label className="field">
        <span>備註</span>
        <input
          type="text"
          placeholder="例如：現金存入"
          onChange={(e) => onChange({ note: e.target.value })}
        />
      </label>
      <label className="field">
        <span>冪等鍵（reference）</span>
        <input
          type="text"
          placeholder="留空則自動產生"
          onChange={(e) => onChange({ reference: e.target.value })}
        />
        <span className="field__hint">帶入相同的鍵重試不會重複入帳；伺服器會加上 grant: 前綴。</span>
      </label>
    </>
  );
}
