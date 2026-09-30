/**
 * One fleet: the roster, the settlement lever, and the weekly history.
 *
 * Two things here need care.
 *
 * **The roster is the billing boundary.** Adding a member takes them off the
 * platform-wide weekly run from the next settlement and puts them on this
 * fleet's discounted rate instead; removing them does the reverse. A driver may
 * be on at most one ACTIVE roster — the server enforces it with a partial
 * unique index — so a second add is a 409 rather than a silent double-bill.
 *
 * **Settlement is idempotent per (fleet, ISO week).** Re-running a week charges
 * nobody twice and updates the stored aggregate instead of appending, so the
 * button is safe to press twice. `tampered` above zero means the ledger
 * reference for that week is held by a different entry and the fee was
 * deliberately *not* collected — that needs a human, not a retry.
 *
 * A fleet that is not ACTIVE is refused: a suspended operator is not
 * dispatching, so it is not billing.
 */

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useApp } from '../app/AppContext';
import { useFormDialog } from '../app/useDialogs';
import { useLoad } from '../app/useLoad';
import { endpoints } from '../api/endpoints';
import type {
  AdminDriverRow,
  FleetDetail,
  FleetMember,
  FleetRow,
  FleetSettlementRunResult,
} from '../api/types';
import { Card, Chip, DetailRow, Empty, Message, Money, Percent, Rows } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import {
  currentIsoWeek,
  driverStatusLabel,
  fleetStatusLabel,
  fleetStatusTone,
  formatTime,
  memberRoleLabel,
  PERIOD_PATTERN,
  shortId,
  taxiTypeLabel,
  MEMBER_ROLE_LABEL,
} from '../lib/labels';

const STATUS_LABEL = { ACTIVE: '營運中', SUSPENDED: '已停權', DISSOLVED: '已解散' } as const;

export function FleetDetailPage() {
  const { fleetId = '' } = useParams();
  const { client, notify, refreshBadges } = useApp();
  const [includeLeft, setIncludeLeft] = useState(false);
  /**
   * The last run's totals, held across reloads. Running a settlement refreshes
   * the header counts and the history, and that reload rebuilds the whole page —
   * without this the result card would vanish the instant it appeared.
   */
  const [lastRun, setLastRun] = useState<FleetSettlementRunResult | null>(null);
  const [runError, setRunError] = useState<Error | null>(null);
  const [running, setRunning] = useState(false);
  const editDialog = useFormDialog();
  const addDialog = useFormDialog();
  const removeDialog = useFormDialog();

  const { data, error, loading, reload } = useLoad(async (): Promise<FleetDetail> => {
    const [page, members, history] = await Promise.all([
      endpoints.fleets.list(client, { limit: 200 }),
      endpoints.fleets.members(client, fleetId, { includeLeft }),
      endpoints.fleets.settlementHistory(client, fleetId, { limit: 52 }),
    ]);
    const fleet = (page.items ?? []).find((item) => item.id === fleetId);
    if (!fleet) {
      throw new Error('找不到此車隊。它可能已被移除，或不在目前的分頁內。');
    }
    return { fleet, members: members.items ?? [], settlement: history.items ?? [] };
  }, [client, fleetId, includeLeft]);

  async function runSettlement(period: string) {
    if (period !== '' && !PERIOD_PATTERN.test(period)) {
      notify('期間格式應為 YYYY-Www，例如 2026-W38。', 'error');
      return;
    }
    setRunning(true);
    setRunError(null);
    try {
      const result = await endpoints.fleets.runSettlement(client, fleetId, {
        period: period || undefined,
      });
      setLastRun(result);
      notify(`車隊結算完成：已收費 ${result.charged} 位。`);
      // The header's member count and the history have both moved.
      reload();
      void refreshBadges();
    } catch (cause) {
      // Shown in place, below the lever, rather than replacing the whole page —
      // the roster and history above it are still valid and worth reading.
      setRunError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setRunning(false);
    }
  }

  if (loading) return <LoadingState />;
  if (error) {
    return (
      <div>
        <div style={{ marginBottom: 12 }}>
          <Link className="btn" to="/fleets">
            ← 返回車隊列表
          </Link>
        </div>
        <ErrorState error={error} onRetry={reload} />
      </div>
    );
  }
  if (!data) return null;

  const { fleet, members, settlement } = data;

  function edit(target: FleetRow) {
    const form = {
      name: target.name,
      discount: target.weekly_fee_discount_percent,
      status: target.status,
    };
    editDialog.open({
      title: '編輯車隊',
      confirmLabel: '儲存',
      body: <FleetEditBody fleet={target} onChange={(patch) => Object.assign(form, patch)} />,
      onSubmit: async () => {
        const name = form.name.trim();
        if (!name) throw new Error('車隊名稱不可留空。');
        const discount = Number(form.discount);
        if (!Number.isFinite(discount) || discount < 0 || discount > 100) {
          throw new Error('折扣須為 0 至 100 之間的數字。');
        }
        await endpoints.fleets.update(client, target.id, {
          name,
          weekly_fee_discount_percent: String(discount),
          status: form.status,
        });
        notify('已更新車隊設定。');
        reload();
      },
    });
  }

  function addMember() {
    const form = { driverId: '', role: 'MEMBER' };
    addDialog.open({
      title: '加入車隊成員',
      confirmLabel: '加入',
      body: <AddMemberBody onChange={(patch) => Object.assign(form, patch)} />,
      onSubmit: async () => {
        if (!form.driverId) throw new Error('請先選擇一位司機。');
        await endpoints.fleets.addMember(client, fleetId, {
          driverProfileId: form.driverId,
          memberRole: form.role,
        });
        notify('已加入車隊名單。');
        reload();
      },
    });
  }

  function removeMember(member: FleetMember) {
    removeDialog.open({
      title: '移出車隊名單？',
      confirmLabel: '移出',
      danger: true,
      body: (
        <div>
          <p style={{ margin: 0 }}>
            <span className="mono">{shortId(member.driver_profile_id)}</span>
            {' 將由下一次結算起，回復按平台劃一費用收費。'}
          </p>
          <p className="dim" style={{ margin: '12px 0 0' }}>
            紀錄會保留為「已離隊」，不會刪除，方便日後核對該週的名單。
          </p>
        </div>
      ),
      onSubmit: async () => {
        await endpoints.fleets.removeMember(client, fleetId, member.driver_profile_id);
        notify('已移出車隊名單。');
        reload();
      },
    });
  }

  return (
    <div>
      {/* ------------------------------------------------------------ header */}
      <Card className="card--pad">
        <div className="spread">
          <div style={{ minWidth: 0 }}>
            <div className="row-inline">
              <h1 style={{ margin: 0 }}>{fleet.name}</h1>
              <Chip tone={fleetStatusTone(fleet.status)}>{fleetStatusLabel(fleet.status)}</Chip>
            </div>
            <div className="dim" style={{ marginTop: 4 }}>
              <span className="mono">{fleet.license_no ?? '—'}</span>
            </div>
          </div>
          <div className="actions">
            <button type="button" className="btn" onClick={() => edit(fleet)}>
              編輯車隊
            </button>
            <Link className="btn" to="/fleets">
              ← 返回列表
            </Link>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <Rows>
            <DetailRow label="每週費用折扣">
              <Percent value={fleet.weekly_fee_discount_percent} />
            </DetailRow>
            <DetailRow label="成員人數">{String(fleet.member_count ?? 0)}</DetailRow>
            <DetailRow label="聯絡人">{fleet.contact_name ?? '—'}</DetailRow>
            <DetailRow label="聯絡電話">{fleet.contact_phone ?? '—'}</DetailRow>
            <DetailRow label="備註">{fleet.note ?? '—'}</DetailRow>
            <DetailRow label="建立日期">{formatTime(fleet.created_at)}</DetailRow>
          </Rows>
        </div>
        {fleet.status !== 'ACTIVE' ? (
          <p className="danger" style={{ margin: '14px 0 0' }}>
            車隊非營運中，結算會被拒絕。
          </p>
        ) : null}
      </Card>

      {/* -------------------------------------------------------- settlement */}
      <h2>每週結算</h2>
      <Card className="card--pad">
        <p className="dim" style={{ margin: '0 0 16px' }}>
          同一週重複執行不會重複收費；已收過的成員會計入「略過」。車隊必須為營運中才會收費。
        </p>
        <SettlementLever running={running} onRun={(period) => void runSettlement(period)} />
      </Card>
      <div style={{ marginTop: 14 }}>
        {runError ? <ErrorState error={runError} /> : null}
        {lastRun ? <RunCard run={lastRun} /> : null}
      </div>

      {/* ----------------------------------------------------------- history */}
      <h2>結算紀錄</h2>
      <Card>
        {settlement.length === 0 ? (
          <Empty title="還沒有結算紀錄。" />
        ) : (
          <div className="table-wrap">
              <table className="data">
            <thead>
              <tr>
                <th>週次</th>
                <th className="num">每位費用</th>
                <th className="num">折扣</th>
                <th className="num">成員</th>
                <th className="num">已收</th>
                <th className="num">略過</th>
                <th className="num">異常</th>
                <th className="num">實收</th>
                <th>執行時間</th>
              </tr>
            </thead>
            <tbody>
              {settlement.map((run) => (
                <tr key={run.period}>
                  <td className="mono">{run.period}</td>
                  <td className="num">
                    <Money value={run.fee_hkd} />
                  </td>
                  <td className="num">
                    <Percent value={run.discount_percent} />
                  </td>
                  <td className="num">{run.member_count ?? 0}</td>
                  <td className="num">{run.charged ?? 0}</td>
                  <td className="num">{run.skipped ?? 0}</td>
                  <td className="num">
                    {run.tampered > 0 ? <span className="danger">{run.tampered}</span> : '0'}
                  </td>
                  <td className="num">
                    <Money value={run.collected_hkd} />
                  </td>
                  <td>{formatTime(run.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
            </div>
        )}
      </Card>

      {/* ------------------------------------------------------------ roster */}
      <h2>成員名單</h2>
      <div className="filters">
        <button type="button" className="btn btn--primary btn--sm" onClick={addMember}>
          加入成員
        </button>
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => setIncludeLeft((current) => !current)}
        >
          {includeLeft ? '只顯示在隊成員' : '包含已離隊成員'}
        </button>
      </div>
      <Card>
        {members.length === 0 ? (
          <Empty title="名單上還沒有成員。" />
        ) : (
          <div className="table-wrap">
              <table className="data">
            <thead>
              <tr>
                <th>司機</th>
                <th>的士類型</th>
                <th>司機狀態</th>
                <th>角色</th>
                <th>名單狀態</th>
                <th>加入日期</th>
                <th>離隊日期</th>
                <th>計費</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <tr key={`${member.driver_profile_id}-${member.joined_at ?? ''}`}>
                  <td className="mono">{shortId(member.driver_profile_id)}</td>
                  <td>{taxiTypeLabel(member.taxi_type)}</td>
                  <td>{driverStatusLabel(member.driver_status)}</td>
                  <td>{memberRoleLabel(member.member_role)}</td>
                  <td>
                    <Chip tone={member.status === 'ACTIVE' ? 'ok' : 'neutral'}>
                      {member.status === 'ACTIVE' ? '在隊' : '已離隊'}
                    </Chip>
                  </td>
                  <td>{formatTime(member.joined_at)}</td>
                  <td>{member.left_at ? formatTime(member.left_at) : <span className="dim">—</span>}</td>
                  <td>
                    {member.status === 'ACTIVE' && member.driver_status === 'ACTIVE' ? (
                      <Chip tone="brand">會收費</Chip>
                    ) : (
                      <span className="dim">不計費</span>
                    )}
                  </td>
                  <td>
                    {member.status === 'ACTIVE' ? (
                      <button
                        type="button"
                        className="btn btn--danger btn--sm"
                        onClick={() => removeMember(member)}
                      >
                        移出
                      </button>
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

      {editDialog.element}
      {addDialog.element}
      {removeDialog.element}
    </div>
  );
}

/** The period field and its run button, kept together so the state is local. */
function SettlementLever({
  running,
  onRun,
}: {
  running: boolean;
  onRun: (period: string) => void;
}) {
  const [period, setPeriod] = useState('');
  return (
    <>
      <div style={{ maxWidth: 360 }}>
        <label className="field">
          <span>ISO 週次（留空為本週）</span>
          <input
            type="text"
            value={period}
            placeholder={currentIsoWeek()}
            onChange={(e) => setPeriod(e.target.value)}
          />
          <span className="field__hint">本週為 {currentIsoWeek()}。</span>
        </label>
      </div>
      <button
        type="button"
        className="btn btn--primary"
        disabled={running}
        onClick={() => onRun(period.trim())}
      >
        {running ? '執行中…' : '執行本週車隊結算'}
      </button>
    </>
  );
}

function RunCard({ run }: { run: FleetSettlementRunResult }) {
  const anomaly = (run.failed ?? 0) > 0 || (run.tampered ?? 0) > 0;
  const gross = run.gross_fee_hkd;
  const saving = gross !== undefined && gross !== null ? Number(gross) - Number(run.fee_hkd) : null;

  return (
    <Card className={anomaly ? 'card--pad card--alert' : 'card--pad'}>
      <h2 className="card__title">結果 · {run.period}</h2>
      <Rows>
        <DetailRow label="平台劃一費用">
          {gross ? <Money value={gross} /> : '—'}
        </DetailRow>
        <DetailRow label="車隊每位費用">
          <Money value={run.fee_hkd} />
        </DetailRow>
        <DetailRow label="每位節省">
          {saving !== null && saving !== 0 ? (
            <>
              <Money value={saving} /> · 折扣 <Percent value={run.discount_percent} />
            </>
          ) : (
            '—'
          )}
        </DetailRow>
        <DetailRow label="計費成員">{String(run.member_count ?? 0)}</DetailRow>
        <DetailRow label="已收費">{String(run.charged ?? 0)}</DetailRow>
        <DetailRow label="略過（已收過）">{String(run.skipped ?? 0)}</DetailRow>
        <DetailRow label="失敗">{String(run.failed ?? 0)}</DetailRow>
        <DetailRow label="帳目異常">{String(run.tampered ?? 0)}</DetailRow>
        <DetailRow label="實收總額">
          <Money value={run.collected_hkd} />
        </DetailRow>
      </Rows>
      {(run.tampered ?? 0) > 0 ? (
        <p className="danger" style={{ margin: '14px 0 0' }}>
          帳目異常代表該週的 ledger reference 被其他帳目佔用，系統刻意未收費，需要人手核對；重試不會解決。
        </p>
      ) : null}
    </Card>
  );
}

interface FleetEditDraft {
  name: string;
  discount: string;
  status: string;
}

function FleetEditBody({
  fleet,
  onChange,
}: {
  fleet: FleetRow;
  onChange: (patch: Partial<FleetEditDraft>) => void;
}) {
  return (
    <>
      <label className="field">
        <span>車隊名稱</span>
        <input
          type="text"
          defaultValue={fleet.name}
          onChange={(e) => onChange({ name: e.target.value })}
        />
      </label>
      <label className="field">
        <span>每週費用折扣（%）</span>
        <input
          type="number"
          min="0"
          max="100"
          step="0.5"
          defaultValue={fleet.weekly_fee_discount_percent}
          onChange={(e) => onChange({ discount: e.target.value })}
        />
        <span className="field__hint">由下一次結算起生效。</span>
      </label>
      <label className="field">
        <span>營運狀態</span>
        <select
          defaultValue={fleet.status}
          onChange={(e) => onChange({ status: e.target.value })}
        >
          {Object.entries(STATUS_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <span className="field__hint">停權或解散後，車隊不會再產生每週收費。</span>
      </label>
    </>
  );
}

/**
 * Pick a driver from the admin register.
 *
 * Only ACTIVE profiles are offered: a driver still in KYC can be rostered, but
 * they are not billable — there is no deposit account to debit — so offering
 * them here would invite a settlement that silently collects nothing.
 */
function AddMemberBody({
  onChange,
}: {
  onChange: (patch: { driverId?: string; role?: string }) => void;
}) {
  const { client } = useApp();
  const { data, error, loading } = useLoad(
    () => endpoints.drivers.list(client, { status: 'ACTIVE', limit: 200 }),
    [client],
  );

  if (loading) return <LoadingState label="載入司機名單…" />;
  if (error) return <ErrorState error={error} />;

  const items = data?.items ?? [];

  return (
    <>
      <p className="dim" style={{ margin: '0 0 12px' }}>
        加入後，該司機會由下一次結算起改按車隊折扣價收費，不再計入平台劃一收費。一位司機同時只能屬於一個車隊名單。
      </p>
      {items.length === 0 ? (
        <Message tone="warn">沒有已啟用的司機。司機需先通過審核並繳足按金。</Message>
      ) : (
        <div className="pick-list">
          {items.map((driver: AdminDriverRow) => (
            <label key={driver.id} className="pick-row">
              <input
                type="radio"
                name="roster-driver"
                onChange={() => onChange({ driverId: driver.id })}
              />
              <span>
                <span className="mono">{driver.vehicle_reg_mark ?? '—'}</span>
                <span className="dim" style={{ fontSize: '12.5px' }}>
                  {`  ·  ${taxiTypeLabel(driver.taxi_type)}  ·  ${shortId(driver.id)}`}
                </span>
              </span>
            </label>
          ))}
        </div>
      )}
      <div style={{ marginTop: 14, maxWidth: 220 }}>
        <label className="field">
          <span>名單角色</span>
          <select defaultValue="MEMBER" onChange={(e) => onChange({ role: e.target.value })}>
            {Object.entries(MEMBER_ROLE_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
    </>
  );
}
