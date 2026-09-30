/**
 * Overview.
 *
 * Every number here is derived from a list endpoint that already exists — the
 * platform has no `/admin/dashboard` and no stored settlement history to read,
 * so this screen counts rather than invents. In particular the "fleet members"
 * figure is the sum of each fleet's `member_count`, which is exactly the set of
 * drivers the platform-wide weekly run will skip.
 */

import { api } from '../api.js';
import { el, errorState, loading, stat } from '../dom.js';

/**
 * @param {{client: object, setBadges?: (b: {pendingKyc?: number, pendingRefunds?: number}) => void}} ctx
 *
 * `setBadges` is how the shell's sidebar counters get filled without a second
 * round trip. This screen already fetches the two totals the badges show, and
 * `boot()` used to call `refreshBadges()` immediately after it — so the dashboard
 * ran eight parallel API calls where six carry all the information, with two of
 * them duplicates. On a healthy network that is waste; where the transport drops
 * a connection out of every burst (`admin-web/README.md`) it is a made-to-order
 * failure, because the extra pair is exactly what fills the budget.
 */
export async function DashboardView({ client, setBadges }) {
  const root = el('div', {}, [loading()]);

  async function load() {
    root.replaceChildren(loading());
    try {
      // Six calls, **sequential**, not parallel.
      //
      // The counts are independent, so firing them together is the obvious shape
      // — and it was, until this ran on a machine that intermittently accepts a
      // connection to the API and then never answers it (`admin-web/README.md`).
      // Every parallel call is another connection that can land on that path,
      // and the hazard grows with the width of the burst: six at once lost one
      // about as reliably as not.
      //
      // Sequential calls each open their connection only after the previous one
      // has closed, so there is never more than one in flight and the transport
      // has nothing concurrent to interpose on. Each call is ~50-150ms, so the
      // whole screen still resolves in well under a second on a healthy API —
      // the user-visible cost is nil, and the failure mode goes from "blank
      // dashboard" to "nothing to see".
      const drivers = await api.drivers.list(client, { limit: 1 });
      const pendingKyc = await api.drivers.list(client, { status: 'PENDING_KYC', limit: 1 });
      const refunds = await api.refunds.list(client, { limit: 1 });
      const pendingRefunds = await api.refunds.list(client, { status: 'PENDING', limit: 1 });
      const fleets = await api.fleets.list(client, { limit: 100 });
      const activeFleets = await api.fleets.list(client, { status: 'ACTIVE', limit: 100 });

      // Hand the two counters the sidebar needs to the shell, so it does not
      // re-request them.
      setBadges?.({
        pendingKyc: pendingKyc.total ?? 0,
        pendingRefunds: pendingRefunds.total ?? 0,
      });

      const fleetMembers = (activeFleets.items ?? []).reduce(
        (sum, fleet) => sum + (fleet.member_count ?? 0),
        0,
      );

      root.replaceChildren(
        el('div', { class: 'grid' }, [
          stat('待審核司機', pendingKyc.total ?? 0, {
            hint: `全部司機 ${drivers.total ?? 0} 位`,
            href: '#/kyc',
          }),
          stat('待處理退款', pendingRefunds.total ?? 0, {
            hint: `全部申請 ${refunds.total ?? 0} 宗`,
            href: '#/refunds',
          }),
          stat('營運中車隊', activeFleets.total ?? 0, {
            hint: `車隊總數 ${fleets.total ?? 0}`,
            href: '#/fleets',
          }),
          stat('車隊成員', fleetMembers, {
            hint: '由車隊結算收費，不在平台劃一收費之內',
          }),
        ]),
        el('h2', { text: '營運備註' }),
        el('div', { class: 'card card--pad' }, [
          el('p', { style: 'margin:0 0 10px' }, [
            '車隊成員由所屬車隊的每週結算以折扣價收費，平台劃一收費會自動略過他們，' +
              '避免同一週被收費兩次。',
          ]),
          el('p', { class: 'dim', style: 'margin:0' }, [
            '每週結算畫面會顯示該次執行略過了多少位車隊成員（',
            el('span', { class: 'mono', text: 'fleet_managed' }),
            '）；若該數字與車隊自身的結算不符，代表名單或期間有出入，需要人手核對。',
          ]),
        ]),
      );
    } catch (error) {
      root.replaceChildren(errorState(error, load));
    }
  }

  await load();
  return root;
}
