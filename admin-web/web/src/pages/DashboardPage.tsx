/**
 * Overview.
 *
 * Every number here is derived from a list endpoint that already exists — the
 * platform has no `/admin/dashboard` and no stored settlement history to read,
 * so this screen counts rather than invents. In particular the "fleet members"
 * figure is the sum of each fleet's `member_count`, which is exactly the set of
 * drivers the platform-wide weekly run will skip.
 */

import { endpoints } from '../api/endpoints';
import { Card, Stat } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { useApp } from '../app/AppContext';
import { inOrder, useLoad } from '../app/useLoad';
import { useI18n } from '../i18n';
import { Link } from 'react-router-dom';

interface DashboardData {
  driverTotal: number;
  pendingKyc: number;
  refundTotal: number;
  pendingRefunds: number;
  fleetTotal: number;
  activeFleets: number;
  fleetMembers: number;
}

export function DashboardPage() {
  const { client, setBadges } = useApp();
  const { t } = useI18n();

  const { data, error, loading, reload } = useLoad<DashboardData>(async (signal) => {
    // Six calls, **sequential**, not parallel — see `useLoad.ts`. On a healthy
    // network the whole screen still resolves in well under a second.
    const [drivers, pendingKyc, refunds, pendingRefunds, fleets, activeFleets] = await inOrder([
      (s) => endpoints.drivers.list(client, { limit: 1 }, s),
      (s) => endpoints.drivers.list(client, { status: 'PENDING_KYC', limit: 1 }, s),
      (s) => endpoints.refunds.list(client, { limit: 1 }, s),
      (s) => endpoints.refunds.list(client, { status: 'PENDING', limit: 1 }, s),
      (s) => endpoints.fleets.list(client, { limit: 100 }, s),
      (s) => endpoints.fleets.list(client, { status: 'ACTIVE', limit: 100 }, s),
    ] as const, signal);

    const result: DashboardData = {
      driverTotal: drivers.total ?? 0,
      pendingKyc: pendingKyc.total ?? 0,
      refundTotal: refunds.total ?? 0,
      pendingRefunds: pendingRefunds.total ?? 0,
      fleetTotal: fleets.total ?? 0,
      activeFleets: activeFleets.total ?? 0,
      fleetMembers: (activeFleets.items ?? []).reduce(
        (sum, fleet) => sum + (fleet.member_count ?? 0),
        0,
      ),
    };

    // Hand the two counters the sidebar needs to the shell, so it does not
    // re-request them — this screen already has the numbers.
    setBadges({ pendingKyc: result.pendingKyc, pendingRefunds: result.pendingRefunds });
    return result;
  }, [client, setBadges]);

  return (
    <>
      <div className="page-head">
        <div className="page-head__text">
          <h1>{t('dashboard.title')}</h1>
          <p className="page-head__sub">{t('dashboard.sub')}</p>
        </div>
      </div>

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {data ? (
        <>
          <div className="grid">
            <Link to="/kyc" className="stat--link">
              <Stat
                label={t('dashboard.pendingKyc')}
                value={data.pendingKyc}
                hint={t('dashboard.pendingKycHint', { total: data.driverTotal })}
              />
            </Link>
            <Link to="/refunds" className="stat--link">
              <Stat
                label={t('dashboard.pendingRefunds')}
                value={data.pendingRefunds}
                hint={t('dashboard.pendingRefundsHint', { total: data.refundTotal })}
              />
            </Link>
            <Link to="/fleets" className="stat--link">
              <Stat
                label={t('dashboard.activeFleets')}
                value={data.activeFleets}
                hint={t('dashboard.activeFleetsHint', { total: data.fleetTotal })}
              />
            </Link>
            <Stat
              label={t('dashboard.fleetMembers')}
              value={data.fleetMembers}
              hint={t('dashboard.fleetMembersHint')}
            />
          </div>

          <h2 className="t-title3" style={{ margin: '24px 0 12px' }}>
            {t('dashboard.noteTitle')}
          </h2>
          <Card>
            <p style={{ margin: '0 0 10px' }}>{t('dashboard.noteBody')}</p>
            <p className="dim" style={{ margin: 0 }}>
              {t('dashboard.noteFootnote')}
            </p>
          </Card>
        </>
      ) : null}
    </>
  );
}
