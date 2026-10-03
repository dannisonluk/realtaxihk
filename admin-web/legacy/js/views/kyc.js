/**
 * The KYC queue.
 *
 * `GET /admin/drivers` is oldest-first, so the drivers who have been waiting
 * longest surface at the top. Two decisions live here and they are different in
 * kind:
 *
 * * **Review** moves the profile through the state machine. `approve` goes to
 *   `DEPOSIT_REQUIRED` — it does *not* put the driver on the road; that happens
 *   when the deposit is fulfilled.
 * * **Grant** credits the deposit ledger. When the grant fulfils the requirement
 *   the server flips `DEPOSIT_REQUIRED -> ACTIVE` in the same call, which is what
 *   actually activates dispatch.
 *
 * The review response is `{id, status}` only — not a profile — so the row is
 * patched from the response rather than refetched.
 */

import { api } from '../api.js';
import { badge, chipRow, el, emptyState, errorState, field, loading, openDialog, table, toast } from '../dom.js';

const STATUS_TONE = {
  PENDING_KYC: 'wait',
  DEPOSIT_REQUIRED: 'info',
  ACTIVE: 'ok',
  SUSPENDED: 'bad',
  TERMINATED: 'neutral',
};

const STATUS_LABEL = {
  PENDING_KYC: '審核中',
  DEPOSIT_REQUIRED: '待繳按金',
  ACTIVE: '已啟用',
  SUSPENDED: '已停權',
  TERMINATED: '已終止',
};

const FILTERS = [
  { value: null, label: '全部' },
  { value: 'PENDING_KYC', label: '審核中' },
  { value: 'DEPOSIT_REQUIRED', label: '待繳按金' },
  { value: 'ACTIVE', label: '已啟用' },
  { value: 'SUSPENDED', label: '已停權' },
  { value: 'TERMINATED', label: '已終止' },
];

const TAXI_LABEL = { URBAN: '市區的士', NT: '新界的士', LANTAU: '大嶼山的士' };

export async function KycView({ client, refreshBadges, navigate }) {
  let filter = 'PENDING_KYC';

  const listHost = el('div', {}, [loading()]);
  const chipHost = el('div', {});

  async function load() {
    listHost.replaceChildren(loading());
    try {
      const page = await api.drivers.list(client, { status: filter, limit: 100 });
      const items = page.items ?? [];

      if (items.length === 0) {
        listHost.replaceChildren(emptyState('沒有符合條件的司機。'));
      } else {
        listHost.replaceChildren(
          table(
            [
              { label: '車牌' },
              { label: '的士類型' },
              { label: '的士證號' },
              { label: '身份證末四位' },
              { label: '狀態' },
              { label: '操作' },
            ],
            items.map((driver) => [
              el('span', { class: 'mono', text: driver.vehicle_reg_mark ?? '—' }),
              TAXI_LABEL[driver.taxi_type] ?? driver.taxi_type ?? '—',
              el('span', { class: 'mono', text: driver.taxi_driver_plate_no ?? '—' }),
              el('span', { class: 'mono', text: driver.hk_id_last4 ? `****${driver.hk_id_last4}` : '—' }),
              badge(STATUS_LABEL[driver.status] ?? driver.status, STATUS_TONE[driver.status] ?? 'neutral'),
              rowActions(driver),
            ]),
            '沒有符合條件的司機。',
          ),
        );
      }
      refreshBadges?.();
    } catch (error) {
      listHost.replaceChildren(errorState(error, load));
    }
  }

  function rowActions(driver) {
    const actions = [];

    // The detail page first, on every row. The state-machine buttons below are
    // the *decision*; this is the "let me look before I press anything" step, and
    // it is the only way to reach the statement, the deposit and the fleet from
    // the queue.
    actions.push(
      el('button', {
        type: 'button',
        class: 'btn btn--sm',
        text: '檢視',
        onClick: () => navigate(`/drivers/${driver.id}`),
      }),
    );

    if (driver.status === 'PENDING_KYC') {
      actions.push(
        el('button', {
          type: 'button',
          class: 'btn btn--primary btn--sm',
          text: '通過審核',
          onClick: () => review(driver, 'approve', '通過 KYC 審核'),
        }),
        el('button', {
          type: 'button',
          class: 'btn btn--danger btn--sm',
          text: '拒絕',
          onClick: () => review(driver, 'reject', '拒絕 KYC 審核'),
        }),
      );
    }

    if (driver.status === 'DEPOSIT_REQUIRED') {
      actions.push(
        el('button', {
          type: 'button',
          class: 'btn btn--primary btn--sm',
          text: '存入按金',
          onClick: () => grant(driver),
        }),
      );
    }

    if (driver.status === 'ACTIVE') {
      actions.push(
        el('button', {
          type: 'button',
          class: 'btn btn--sm',
          text: '停權',
          onClick: () => review(driver, 'suspend', '暫停司機帳戶'),
        }),
        el('button', {
          type: 'button',
          class: 'btn btn--danger btn--sm',
          text: '終止',
          onClick: () => review(driver, 'terminate', '終止司機帳戶'),
        }),
      );
    }

    if (driver.status === 'SUSPENDED') {
      actions.push(
        el('button', {
          type: 'button',
          class: 'btn btn--sm',
          text: '恢復',
          onClick: () => review(driver, 'approve', '恢復司機帳戶'),
        }),
        el('button', {
          type: 'button',
          class: 'btn btn--danger btn--sm',
          text: '終止',
          onClick: () => review(driver, 'terminate', '終止司機帳戶'),
        }),
      );
    }

    if (actions.length === 0) {
      return el('span', { class: 'dim', text: '—' });
    }
    return el('div', { style: 'display:flex;gap:6px' }, actions);
  }

  function review(driver, decision, title) {
    const note = el('textarea', { rows: '3', placeholder: '備註（會記錄在審核紀錄）' });

    openDialog({
      title,
      confirmLabel: '確認',
      danger: decision === 'reject' || decision === 'terminate',
      body: [
        el('p', { style: 'margin:0 0 12px' }, [
          '車牌 ',
          el('span', { class: 'mono', text: driver.vehicle_reg_mark ?? '—' }),
          '（',
          TAXI_LABEL[driver.taxi_type] ?? driver.taxi_type ?? '—',
          '）',
        ]),
        decision === 'approve' && driver.status === 'PENDING_KYC'
          ? el('p', { class: 'dim', style: 'margin:0 0 12px' }, [
              '通過後狀態會變成「待繳按金」，需存入按金才會正式啟用接單。',
            ])
          : null,
        decision === 'approve' && driver.status === 'SUSPENDED'
          ? el('p', { class: 'dim', style: 'margin:0 0 12px' }, [
              '恢復後狀態會回到「待繳按金」或「已啟用」，視按金餘額而定。',
            ])
          : null,
        decision === 'terminate'
          ? el('p', { class: 'danger', style: 'margin:0 0 12px' }, [
              '終止後司機無法再接單，且不可回復。',
            ])
          : null,
        field('備註', note),
      ],
      onSubmit: async (close) => {
        const result = await api.drivers.review(client, driver.id, {
          decision,
          note: note.value.trim(),
        });
        toast(`已更新狀態：${STATUS_LABEL[result.status] ?? result.status}`);
        close();
        await load();
      },
    });
  }

  function grant(driver) {
    const amount = el('input', { type: 'number', min: '1', step: '0.01', value: '500' });
    const note = el('input', { type: 'text', placeholder: '例如：現金存入' });
    const reference = el('input', { type: 'text', placeholder: '留空則自動產生' });

    openDialog({
      title: '存入按金',
      confirmLabel: '存入',
      body: [
        el('p', { class: 'dim', style: 'margin:0 0 12px' }, [
          '車牌 ',
          el('span', { class: 'mono', text: driver.vehicle_reg_mark ?? '—' }),
          '。存入後按金達標即會自動啟用接單。',
        ]),
        field('金額（HKD）', amount),
        field('備註', note),
        field(
          '冪等鍵（reference）',
          reference,
          '帶入相同的鍵重試不會重複入帳；伺服器會加上 grant: 前綴。',
        ),
      ],
      onSubmit: async (close) => {
        const value = Number(amount.value);
        if (!Number.isFinite(value) || value <= 0) {
          throw new Error('金額必須大於 0。');
        }
        const result = await api.drivers.grantDeposit(client, driver.id, {
          amountHkd: value,
          note: note.value.trim(),
          reference: reference.value.trim() || undefined,
        });
        toast(
          result.is_fulfilled
            ? `已存入，按金達標，司機已啟用。餘額 ${result.balance_hkd}。`
            : `已存入，餘額 ${result.balance_hkd}，尚未達標。`,
        );
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

  return el('div', {}, [
    el('div', { class: 'filters' }, chipHost),
    listHost,
  ]);
}

export { STATUS_LABEL as DRIVER_STATUS_LABEL };
