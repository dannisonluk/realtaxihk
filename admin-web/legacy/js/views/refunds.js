/**
 * The refund queue.
 *
 * Requesting a refund **holds** the driver's whole remaining balance and
 * suspends them; no money moves until a decision here. Approving is the only
 * path that pays out: it writes a `REFUND` ledger entry keyed `refund:{id}` — so
 * a double-click cannot pay twice — and terminates the driver. Rejecting releases
 * the hold and returns them to `ACTIVE`.
 *
 * A request can be decided once. The actions are therefore only offered on
 * `PENDING` rows; the server would refuse a second decision anyway.
 */

import { api } from '../api.js';
import { badge, chipRow, dateTime, el, errorState, field, loading, money, openDialog, shortId, table, toast } from '../dom.js';

const STATUS_TONE = { PENDING: 'wait', APPROVED: 'ok', REJECTED: 'bad' };
const STATUS_LABEL = { PENDING: '待審批', APPROVED: '已批准', REJECTED: '已拒絕' };

const FILTERS = [
  { value: 'PENDING', label: '待審批' },
  { value: null, label: '全部' },
  { value: 'APPROVED', label: '已批准' },
  { value: 'REJECTED', label: '已拒絕' },
];

export async function RefundsView({ client, refreshBadges }) {
  let filter = 'PENDING';

  const listHost = el('div', {}, [loading()]);
  const chipHost = el('div', {});

  async function load() {
    listHost.replaceChildren(loading());
    try {
      const page = await api.refunds.list(client, { status: filter, limit: 100 });
      const items = page.items ?? [];

      listHost.replaceChildren(
        table(
          [
            { label: '申請編號' },
            { label: '司機' },
            { label: '金額', numeric: true },
            { label: '狀態' },
            { label: '申請時間' },
            { label: '申請備註', wrap: true },
            { label: '操作' },
          ],
          items.map((refund) => [
            el('span', { class: 'mono', text: shortId(refund.id) }),
            el('span', { class: 'mono', text: shortId(refund.driver_profile_id) }),
            money(refund.amount_hkd),
            badge(STATUS_LABEL[refund.status] ?? refund.status, STATUS_TONE[refund.status] ?? 'neutral'),
            dateTime(refund.created_at),
            refund.note || el('span', { class: 'dim', text: '—' }),
            refund.status === 'PENDING'
              ? el('div', { style: 'display:flex;gap:6px' }, [
                  el('button', {
                    type: 'button',
                    class: 'btn btn--primary btn--sm',
                    text: '批准',
                    onClick: () => decide(refund, true),
                  }),
                  el('button', {
                    type: 'button',
                    class: 'btn btn--sm',
                    text: '拒絕',
                    onClick: () => decide(refund, false),
                  }),
                ])
              : el('span', {
                  class: 'dim',
                  text: refund.decided_at ? dateTime(refund.decided_at) : '—',
                }),
          ]),
          '沒有符合條件的退款申請。',
        ),
      );
      refreshBadges?.();
    } catch (error) {
      listHost.replaceChildren(errorState(error, load));
    }
  }

  function decide(refund, approve) {
    const note = el('textarea', { rows: '3', placeholder: '批核備註（會記錄在申請上）' });

    openDialog({
      title: approve ? '批准退款' : '拒絕退款',
      confirmLabel: approve ? '批准並退款' : '拒絕',
      danger: approve,
      body: [
        el('div', { style: 'margin-bottom:12px' }, [
          el('div', [
            '金額 ',
            el('strong', { text: money(refund.amount_hkd) }),
          ]),
          el('div', { class: 'dim', style: 'font-size:12.5px' }, [
            '司機 ',
            el('span', { class: 'mono', text: shortId(refund.driver_profile_id) }),
          ]),
        ]),
        approve
          ? el('p', { class: 'danger', style: 'margin:0 0 12px' }, [
              '批准會實際付出按金餘額，並終止該司機帳戶。此操作不可回復，' +
                '而且同一筆申請只能批核一次。',
            ])
          : el('p', { class: 'dim', style: 'margin:0 0 12px' }, [
              '拒絕會解除按金凍結，司機帳戶回復啟用。',
            ]),
        refund.note ? el('p', { class: 'dim', style: 'margin:0 0 12px' }, [
          '司機備註：',
          refund.note,
        ]) : null,
        field('批核備註', note),
      ],
      onSubmit: async (close) => {
        const result = await api.refunds.decide(client, refund.id, {
          approve,
          note: note.value.trim(),
        });
        toast(`退款申請已${STATUS_LABEL[result.status] ?? result.status}。`);
        close();
        await load();
      },
    });
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

  renderChips();
  await load();

  return el('div', {}, [el('div', { class: 'filters' }, chipHost), listHost]);
}
