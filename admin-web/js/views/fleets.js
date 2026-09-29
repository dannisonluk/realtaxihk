/**
 * The fleet register (的士車隊).
 *
 * A fleet is a **licensed operator** — the Transport Department grants the
 * licence — so fleets are created here, by the platform, and never self-service.
 * This is the onboarding step: the operator supplies a name and licence number,
 * and the platform sets the weekly fee discount they are entitled to.
 *
 * The discount is the whole commercial point. A member of a fleet is charged the
 * platform's flat weekly fee **less this percentage**, and is excluded from the
 * platform-wide run so they are never charged both. See `fleetDetail.js` for the
 * roster and settlement levers.
 */

import { api } from '../api.js';
import { badge, chipRow, date, el, errorState, field, loading, openDialog, percent, table, toast } from '../dom.js';

const STATUS_TONE = { ACTIVE: 'ok', SUSPENDED: 'bad', DISSOLVED: 'neutral' };
const STATUS_LABEL = { ACTIVE: '營運中', SUSPENDED: '已停權', DISSOLVED: '已解散' };

const FILTERS = [
  { value: null, label: '全部' },
  { value: 'ACTIVE', label: '營運中' },
  { value: 'SUSPENDED', label: '已停權' },
  { value: 'DISSOLVED', label: '已解散' },
];

export async function FleetsView({ client, navigate, refreshBadges }) {
  let filter = null;

  const listHost = el('div', {}, [loading()]);
  const chipHost = el('div', {});

  async function load() {
    listHost.replaceChildren(loading());
    try {
      const page = await api.fleets.list(client, { status: filter, limit: 100 });
      const items = page.items ?? [];

      listHost.replaceChildren(
        table(
          [
            { label: '車隊名稱' },
            { label: '車隊牌照' },
            { label: '每週費用折扣' },
            { label: '成員人數', numeric: true },
            { label: '狀態' },
            { label: '建立日期' },
            { label: '' },
          ],
          items.map((fleet) => [
            fleet.name,
            el('span', { class: 'mono', text: fleet.license_no }),
            percent(fleet.weekly_fee_discount_percent),
            String(fleet.member_count ?? 0),
            badge(STATUS_LABEL[fleet.status] ?? fleet.status, STATUS_TONE[fleet.status] ?? 'neutral'),
            date(fleet.created_at),
            el('button', {
              type: 'button',
              class: 'btn btn--sm',
              text: '管理',
              onClick: () => navigate(`/fleets/${fleet.id}`),
            }),
          ]),
          filter ? '沒有符合條件的車隊。' : '還沒有任何車隊。',
        ),
      );
      refreshBadges?.();
    } catch (error) {
      listHost.replaceChildren(errorState(error, load));
    }
  }

  function renderChips() {
    chipHost.replaceChildren(
      chipRow(FILTERS, filter, (value) => {
        filter = value;
        renderChips();
        load();
      }),
    );
  }

  function create() {
    const name = el('input', { type: 'text', placeholder: '星群的士' });
    const license = el('input', { type: 'text', placeholder: '由運輸署發出' });
    const discount = el('input', { type: 'number', min: '0', max: '100', step: '0.5', value: '0' });
    const contactName = el('input', { type: 'text', placeholder: '選填' });
    const contactPhone = el('input', { type: 'tel', placeholder: '選填' });
    const note = el('textarea', { rows: '2', placeholder: '選填' });

    openDialog({
      title: '新增車隊',
      confirmLabel: '建立',
      body: [
        field('車隊名稱', name),
        field('車隊牌照號碼', license, '車隊名稱及牌照號碼皆不可重複。'),
        field('每週費用折扣（%）', discount, '0–100。成員按折扣後的車隊費用收費。'),
        field('聯絡人', contactName),
        field('聯絡電話', contactPhone),
        field('備註', note),
      ],
      onSubmit: async (close) => {
        const trimmedName = name.value.trim();
        const trimmedLicense = license.value.trim();
        if (!trimmedName || !trimmedLicense) {
          throw new Error('車隊名稱及牌照號碼為必填。');
        }
        const value = Number(discount.value);
        if (!Number.isFinite(value) || value < 0 || value > 100) {
          throw new Error('折扣須為 0 至 100 之間的數字。');
        }

        const created = await api.fleets.create(client, {
          name: trimmedName,
          license_no: trimmedLicense,
          weekly_fee_discount_percent: String(value),
          contact_name: contactName.value.trim() || null,
          contact_phone: contactPhone.value.trim() || null,
          note: note.value.trim() || null,
        });
        toast(`已新增車隊 ${created.name}。`);
        close();
        await load();
      },
    });
  }

  renderChips();
  await load();

  return el('div', {}, [
    el('div', { class: 'filters' }, [
      chipHost,
      el('button', { type: 'button', class: 'btn btn--primary', text: '新增車隊', onClick: create }),
    ]),
    listHost,
  ]);
}
