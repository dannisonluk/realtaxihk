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

export async function DashboardView({ client }) {
  const root = el('div', {}, [loading()]);

  async function load() {
    root.replaceChildren(loading());
    try {
      const [drivers, pendingKyc, refunds, pendingRefunds, fleets, activeFleets] = await Promise.all([
        api.drivers.list(client, { limit: 1 }),
        api.drivers.list(client, { status: 'PENDING_KYC', limit: 1 }),
        api.refunds.list(client, { limit: 1 }),
        api.refunds.list(client, { status: 'PENDING', limit: 1 }),
        api.fleets.list(client, { limit: 100 }),
        api.fleets.list(client, { status: 'ACTIVE', limit: 100 }),
      ]);

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
