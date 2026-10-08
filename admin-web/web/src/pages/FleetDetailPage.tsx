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
import { useConfirmDialog, useFormDialog } from '../app/useDialogs';
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
import { useI18n } from '../i18n';
import {
  currentIsoWeek,
  formatTime,
  PERIOD_PATTERN,
  shortId,
  useLabels,
} from '../lib/labels';

const FLEET_STATUS_VALUES = ['ACTIVE', 'SUSPENDED', 'DISSOLVED'] as const;
const MEMBER_ROLE_VALUES = ['OWNER', 'MANAGER', 'MEMBER'] as const;

export function FleetDetailPage() {
  const { fleetId = '' } = useParams();
  const { client, notify, refreshBadges } = useApp();
  const { t, formatLocale } = useI18n();
  const labels = useLabels();
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
  const settleDialog = useConfirmDialog();

  const { data, error, loading, reload } = useLoad(async (signal): Promise<FleetDetail> => {
    // Sequential — see `useLoad.ts`. This page previously fired all three
    // together, which is three chances to land on the connection that the
    // server accepts and then never answers.
    const page = await endpoints.fleets.list(client, { limit: 200 }, signal);
    const members = await endpoints.fleets.members(client, fleetId, { includeLeft }, signal);
    const history = await endpoints.fleets.settlementHistory(client, fleetId, { limit: 52 }, signal);
    const fleet = (page.items ?? []).find((item) => item.id === fleetId);
    if (!fleet) {
      throw new Error(t('fleetDetail.notFound'));
    }
    return { fleet, members: members.items ?? [], settlement: history.items ?? [] };
  }, [client, fleetId, includeLeft]);

  function requestSettlement(period: string) {
    if (period !== '' && !PERIOD_PATTERN.test(period)) {
      notify(t('fleetDetail.errPeriod'), 'error');
      return;
    }
    // This button moves money; the platform-wide settlement page requires a
    // preview token for exactly this reason. The fleet route is idempotent per
    // (fleet, ISO week) — it cannot double-bill — but an accidental run still
    // charges the roster and overwrites the stored aggregate, so the operator
    // gets a deliberate second press instead of a one-click money movement.
    //
    // A modal rather than `window.confirm`: the native dialog cannot carry the
    // danger tone, renders outside the app's styling, and blocks the whole tab.
    // `useConfirmDialog` states the consequence in the body and marks the
    // confirm button `--danger`, like every other irreversible action here.
    settleDialog.open({
      title: t('fleetDetail.settlementTitle'),
      message: t('fleetDetail.settlementNote'),
      confirmLabel: t('fleetDetail.runNow'),
      onConfirm: async () => {
        setRunning(true);
        setRunError(null);
        try {
          const result = await endpoints.fleets.runSettlement(client, fleetId, {
            period: period || undefined,
          });
          setLastRun(result);
          notify(t('fleetDetail.settleDone', { count: result.charged }));
          // The header's member count and the history have both moved.
          reload();
          void refreshBadges();
        } catch (cause) {
          // Shown in place, below the lever, rather than inside the modal — the
          // roster and history above it are still valid and worth reading.
          // Swallowed rather than rethrown so the modal closes and the error
          // lands below the lever, where it has always been reported.
          setRunError(cause instanceof Error ? cause : new Error(String(cause)));
        } finally {
          setRunning(false);
        }
      },
    });
  }

  if (loading) return <LoadingState />;
  if (error) {
    return (
      <div>
        <div style={{ marginBottom: 12 }}>
          <Link className="btn" to="/fleets">
            {t('fleetDetail.back')}
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
      title: t('fleetDetail.edit'),
      confirmLabel: t('fleetDetail.editConfirm'),
      body: <FleetEditBody fleet={target} onChange={(patch) => Object.assign(form, patch)} />,
      onSubmit: async () => {
        const name = form.name.trim();
        if (!name) throw new Error(t('fleetDetail.errName'));
        const discount = Number(form.discount);
        if (!Number.isFinite(discount) || discount < 0 || discount > 100) {
          throw new Error(t('fleetDetail.errDiscount'));
        }
        await endpoints.fleets.update(client, target.id, {
          name,
          weekly_fee_discount_percent: String(discount),
          status: form.status,
        });
        notify(t('fleetDetail.updated'));
        reload();
      },
    });
  }

  function addMember() {
    const form = { driverId: '', role: 'MEMBER' };
    addDialog.open({
      title: t('fleetDetail.addMember'),
      confirmLabel: t('fleetDetail.addMemberConfirm'),
      body: <AddMemberBody onChange={(patch) => Object.assign(form, patch)} />,
      onSubmit: async () => {
        if (!form.driverId) throw new Error(t('fleetDetail.errDriver'));
        await endpoints.fleets.addMember(client, fleetId, {
          driverProfileId: form.driverId,
          memberRole: form.role,
        });
        notify(t('fleetDetail.added'));
        reload();
      },
    });
  }

  function removeMember(member: FleetMember) {
    removeDialog.open({
      title: t('fleetDetail.removeTitle'),
      confirmLabel: t('fleetDetail.removeConfirm'),
      danger: true,
      body: (
        <div>
          <p style={{ margin: 0 }}>
            <span className="mono">{shortId(member.driver_profile_id)}</span>
            {t('fleetDetail.removeNote')}
          </p>
          <p className="dim" style={{ margin: '12px 0 0' }}>
            {t('fleetDetail.removeKeep')}
          </p>
        </div>
      ),
      onSubmit: async () => {
        await endpoints.fleets.removeMember(client, fleetId, member.driver_profile_id);
        notify(t('fleetDetail.removed'));
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
              <Chip tone={labels.fleetStatusTone(fleet.status)}>{labels.fleetStatus(fleet.status)}</Chip>
            </div>
            <div className="dim" style={{ marginTop: 4 }}>
              <span className="mono">{fleet.license_no ?? '—'}</span>
            </div>
          </div>
          <div className="actions">
            <button type="button" className="btn" onClick={() => edit(fleet)}>
              {t('fleetDetail.edit')}
            </button>
            <Link className="btn" to="/fleets">
              {t('fleetDetail.backShort')}
            </Link>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <Rows>
            <DetailRow label={t('fleetDetail.colDiscount')}>
              <Percent value={fleet.weekly_fee_discount_percent} />
            </DetailRow>
            <DetailRow label={t('fleetDetail.colMembers')}>{String(fleet.member_count ?? 0)}</DetailRow>
            <DetailRow label={t('fleetDetail.colContact')}>{fleet.contact_name ?? '—'}</DetailRow>
            <DetailRow label={t('fleetDetail.colPhone')}>{fleet.contact_phone ?? '—'}</DetailRow>
            <DetailRow label={t('fleetDetail.colNote')}>{fleet.note ?? '—'}</DetailRow>
            <DetailRow label={t('fleetDetail.colCreated')}>{formatTime(fleet.created_at, formatLocale)}</DetailRow>
          </Rows>
        </div>
        {fleet.status !== 'ACTIVE' ? (
          <p className="danger" style={{ margin: '14px 0 0' }}>
            {t('fleetDetail.notActive')}
          </p>
        ) : null}
      </Card>

      {/* -------------------------------------------------------- settlement */}
      <h2>{t('fleetDetail.settlementTitle')}</h2>
      <Card className="card--pad">
        <p className="dim" style={{ margin: '0 0 16px' }}>
          {t('fleetDetail.settlementNote')}
        </p>
        <SettlementLever running={running} onRun={requestSettlement} />
      </Card>
      <div style={{ marginTop: 14 }}>
        {runError ? <ErrorState error={runError} /> : null}
        {lastRun ? <RunCard run={lastRun} /> : null}
      </div>

      {/* ----------------------------------------------------------- history */}
      <h2>{t('fleetDetail.historyTitle')}</h2>
      <Card>
        {settlement.length === 0 ? (
          <Empty title={t('fleetDetail.historyEmpty')} />
        ) : (
          <div className="table-wrap">
              <table className="data">
            <thead>
              <tr>
                <th scope="col">{t('fleetDetail.colPeriod')}</th>
                <th scope="col" className="num">{t('fleetDetail.colPerMember')}</th>
                <th scope="col" className="num">{t('fleetDetail.colDiscountCol')}</th>
                <th scope="col" className="num">{t('fleetDetail.colMembers')}</th>
                <th scope="col" className="num">{t('fleetDetail.colCharged')}</th>
                <th scope="col" className="num">{t('fleetDetail.colSkipped')}</th>
                <th scope="col" className="num">{t('fleetDetail.colTampered')}</th>
                <th scope="col" className="num">{t('fleetDetail.colNet')}</th>
                <th scope="col">{t('fleetDetail.colRunAt')}</th>
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
                  <td>{formatTime(run.created_at, formatLocale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
            </div>
        )}
      </Card>

      {/* ------------------------------------------------------------ roster */}
      <h2>{t('fleetDetail.rosterTitle')}</h2>
      <div className="filters">
        <button type="button" className="btn btn--primary btn--sm" onClick={addMember}>
          {t('fleetDetail.addMember')}
        </button>
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => setIncludeLeft((current) => !current)}
        >
          {includeLeft ? t('fleetDetail.toggleLeft') : t('fleetDetail.toggleLeftOpposite')}
        </button>
      </div>
      <Card>
        {members.length === 0 ? (
          <Empty title={t('fleetDetail.rosterEmpty')} />
        ) : (
          <div className="table-wrap">
              <table className="data">
            <thead>
              <tr>
                <th scope="col">{t('fleetDetail.colDriver')}</th>
                <th scope="col">{t('fleetDetail.colTaxiType')}</th>
                <th scope="col">{t('fleetDetail.colDriverStatus')}</th>
                <th scope="col">{t('fleetDetail.colMemberRole')}</th>
                <th scope="col">{t('fleetDetail.colMemberStatus')}</th>
                <th scope="col">{t('fleetDetail.colJoined')}</th>
                <th scope="col">{t('fleetDetail.colLeft')}</th>
                <th scope="col">{t('fleetDetail.colBilling')}</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <tr key={`${member.driver_profile_id}-${member.joined_at ?? ''}`}>
                  <td className="mono">{shortId(member.driver_profile_id)}</td>
                  <td>{labels.taxiType(member.taxi_type)}</td>
                  <td>{labels.driverStatus(member.driver_status)}</td>
                  <td>{labels.memberRole(member.member_role)}</td>
                  <td>
                    <Chip tone={member.status === 'ACTIVE' ? 'ok' : 'neutral'}>
                      {labels.memberStatus(member.status)}
                    </Chip>
                  </td>
                  <td>{formatTime(member.joined_at, formatLocale)}</td>
                  <td>{member.left_at ? formatTime(member.left_at, formatLocale) : <span className="dim">—</span>}</td>
                  <td>
                    {member.status === 'ACTIVE' && member.driver_status === 'ACTIVE' ? (
                      <Chip tone="brand">{t('fleetDetail.billable')}</Chip>
                    ) : (
                      <span className="dim">{t('fleetDetail.unbillable')}</span>
                    )}
                  </td>
                  <td>
                    {member.status === 'ACTIVE' ? (
                      <button
                        type="button"
                        className="btn btn--danger btn--sm"
                        onClick={() => removeMember(member)}
                      >
                        {t('fleetDetail.remove')}
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
      {settleDialog.element}
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
  const { t } = useI18n();
  const [period, setPeriod] = useState('');
  return (
    <>
      <div style={{ maxWidth: 360 }}>
        <label className="field">
          <span>{t('fleetDetail.fieldWeek')}</span>
          <input
            type="text"
            value={period}
            placeholder={currentIsoWeek()}
            onChange={(e) => setPeriod(e.target.value)}
          />
          <span className="field__hint">{t('fleetDetail.fieldWeekHint', { week: currentIsoWeek() })}</span>
        </label>
      </div>
      <button
        type="button"
        className="btn btn--primary"
        disabled={running}
        onClick={() => onRun(period.trim())}
      >
        {running ? t('fleetDetail.running') : t('fleetDetail.runNow')}
      </button>
    </>
  );
}

function RunCard({ run }: { run: FleetSettlementRunResult }) {
  const { t } = useI18n();
  const anomaly = (run.failed ?? 0) > 0 || (run.tampered ?? 0) > 0;
  const gross = run.gross_fee_hkd;
  const saving = gross !== undefined && gross !== null ? Number(gross) - Number(run.fee_hkd) : null;

  return (
    <Card className={anomaly ? 'card--pad card--alert' : 'card--pad'}>
      <h2 className="card__title">{t('fleetDetail.result', { period: run.period })}</h2>
      <Rows>
        <DetailRow label={t('fleetDetail.resPlatform')}>
          {gross ? <Money value={gross} /> : '—'}
        </DetailRow>
        <DetailRow label={t('fleetDetail.resFleet')}>
          <Money value={run.fee_hkd} />
        </DetailRow>
        <DetailRow label={t('fleetDetail.resSaving')}>
          {saving !== null && saving !== 0 ? (
            <>
              <Money value={saving} />
              {t('fleetDetail.resSavingNote', { saving: '', discount: '' })}
              <Percent value={run.discount_percent} />
            </>
          ) : (
            '—'
          )}
        </DetailRow>
        <DetailRow label={t('fleetDetail.resBilled')}>{String(run.member_count ?? 0)}</DetailRow>
        <DetailRow label={t('fleetDetail.resCharged')}>{String(run.charged ?? 0)}</DetailRow>
        <DetailRow label={t('fleetDetail.resSkipped')}>{String(run.skipped ?? 0)}</DetailRow>
        <DetailRow label={t('fleetDetail.resFailed')}>{String(run.failed ?? 0)}</DetailRow>
        <DetailRow label={t('fleetDetail.resTampered')}>{String(run.tampered ?? 0)}</DetailRow>
        <DetailRow label={t('fleetDetail.resNet')}>
          <Money value={run.collected_hkd} />
        </DetailRow>
      </Rows>
      {(run.tampered ?? 0) > 0 ? (
        <p className="danger" style={{ margin: '14px 0 0' }}>
          {t('fleetDetail.tamperedNote')}
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
  const { t } = useI18n();
  const labels = useLabels();
  return (
    <>
      <label className="field">
        <span>{t('fleetDetail.fieldName')}</span>
        <input
          type="text"
          defaultValue={fleet.name}
          onChange={(e) => onChange({ name: e.target.value })}
        />
      </label>
      <label className="field">
        <span>{t('fleetDetail.fieldDiscount')}</span>
        <input
          type="number"
          min="0"
          max="100"
          step="0.5"
          defaultValue={fleet.weekly_fee_discount_percent}
          onChange={(e) => onChange({ discount: e.target.value })}
        />
        <span className="field__hint">{t('fleetDetail.fieldDiscountHint')}</span>
      </label>
      <label className="field">
        <span>{t('fleetDetail.fieldStatus')}</span>
        <select
          defaultValue={fleet.status}
          onChange={(e) => onChange({ status: e.target.value })}
        >
          {FLEET_STATUS_VALUES.map((value) => (
            <option key={value} value={value}>
              {labels.fleetStatus(value)}
            </option>
          ))}
        </select>
        <span className="field__hint">{t('fleetDetail.fieldStatusHint')}</span>
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
  const { t } = useI18n();
  const labels = useLabels();
  const { data, error, loading, reload } = useLoad(
    (signal) => endpoints.drivers.list(client, { status: 'ACTIVE', limit: 200 }, signal),
    [client],
  );

  if (loading) return <LoadingState label={t('fleetDetail.loadingRoster')} />;
  if (error) return <ErrorState error={error} onRetry={reload} />;

  const items = data?.items ?? [];

  return (
    <>
      <p className="dim" style={{ margin: '0 0 12px' }}>
        {t('fleetDetail.pickIntro')}
      </p>
      {items.length === 0 ? (
        <Message tone="warn">{t('fleetDetail.noDrivers')}</Message>
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
                  {`  ·  ${labels.taxiType(driver.taxi_type)}  ·  ${shortId(driver.id)}`}
                </span>
              </span>
            </label>
          ))}
        </div>
      )}
      <div style={{ marginTop: 14, maxWidth: 220 }}>
        <label className="field">
          <span>{t('fleetDetail.fieldMemberRole')}</span>
          <select defaultValue="MEMBER" onChange={(e) => onChange({ role: e.target.value })}>
            {MEMBER_ROLE_VALUES.map((value) => (
              <option key={value} value={value}>
                {labels.memberRole(value)}
              </option>
            ))}
          </select>
        </label>
      </div>
    </>
  );
}
