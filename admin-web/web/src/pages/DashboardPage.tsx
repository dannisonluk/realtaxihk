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

  const { data, error, loading, reload } = useLoad<DashboardData>(async () => {
    // Six calls, **sequential**, not parallel — see `useLoad.ts`. On a healthy
    // network the whole screen still resolves in well under a second.
    const [drivers, pendingKyc, refunds, pendingRefunds, fleets, activeFleets] = await inOrder([
      () => endpoints.drivers.list(client, { limit: 1 }),
      () => endpoints.drivers.list(client, { status: 'PENDING_KYC', limit: 1 }),
      () => endpoints.refunds.list(client, { limit: 1 }),
      () => endpoints.refunds.list(client, { status: 'PENDING', limit: 1 }),
      () => endpoints.fleets.list(client, { limit: 100 }),
      () => endpoints.fleets.list(client, { status: 'ACTIVE', limit: 100 }),
    ] as const);

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
          <h1>總覽</h1>
          <p className="page-head__sub">平台即時狀況。</p>
        </div>
      </div>

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {data ? (
        <>
          <div className="grid">
            <Link to="/kyc" style={{ textDecoration: 'none', color: 'inherit' }}>
              <Stat
                label="待審核司機"
                value={data.pendingKyc}
                hint={`全部司機 ${data.driverTotal} 位`}
              />
            </Link>
            <Link to="/refunds" style={{ textDecoration: 'none', color: 'inherit' }}>
              <Stat
                label="待處理退款"
                value={data.pendingRefunds}
                hint={`全部申請 ${data.refundTotal} 宗`}
              />
            </Link>
            <Link to="/fleets" style={{ textDecoration: 'none', color: 'inherit' }}>
              <Stat
                label="營運中車隊"
                value={data.activeFleets}
                hint={`車隊總數 ${data.fleetTotal}`}
              />
            </Link>
            <Stat
              label="車隊成員"
              value={data.fleetMembers}
              hint="由車隊結算收費，不在平台劃一收費之內"
            />
          </div>

          <h2 className="t-title3" style={{ margin: '24px 0 12px' }}>
            營運備註
          </h2>
          <Card>
            <p style={{ margin: '0 0 10px' }}>
              車隊成員由所屬車隊的每週結算以折扣價收費，平台劃一收費會自動略過他們，避免同一週被收費兩次。
            </p>
            <p className="dim" style={{ margin: 0 }}>
              每週結算畫面會顯示該次執行略過了多少位車隊成員（<span className="mono">fleet_managed</span>
              ）；若該數字與車隊自身的結算不符，代表名單或期間有出入，需要人手核對。
            </p>
          </Card>
        </>
      ) : null}
    </>
  );
}
