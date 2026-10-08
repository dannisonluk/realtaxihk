/**
 * One driver, in full.
 *
 * The KYC queue (`kyc.js`) shows a row per driver — enough to move a status
 * forward, not enough to *decide*. This page is the decision surface: the
 * identity documents, the deposit against its threshold, the statement, the
 * refund history, and the fleet the driver is billed under today.
 *
 * **One call, not five.** The whole page comes from `GET
 * /admin/drivers/{id}`, composed server-side. A page that fired five
 * independent requests could render a half-true driver — statement loaded,
 * deposit not — and would have no way to say so. See the `app/api/admin/`
 * package (`GET /admin/drivers/{id}` lives in the admin drivers router).
 *
 * **The actions are the same as the queue's, and for the same reason.** Review
 * moves the state machine (`approve` lands on `DEPOSIT_REQUIRED`, not on the
 * road); a deposit grant is what actually activates dispatch. They live here too
 * because an operator who has just read the whole history should not have to go
 * back to the list to act on it.
 */

import { api } from '../api.js';
import {
  badge,
  date,
  dateTime,
  definitionList,
  el,
  errorState,
  field,
  loading,
  money,
  openDialog,
  percent,
  shortId,
  table,
  toast,
} from '../dom.js';
import { DRIVER_STATUS_LABEL } from './kyc.js';

const STATUS_TONE = {
  PENDING_KYC: 'wait',
  DEPOSIT_REQUIRED: 'info',
  ACTIVE: 'ok',
  SUSPENDED: 'bad',
  TERMINATED: 'neutral',
};

const TAXI_LABEL = { URBAN: '市區的士', NT: '新界的士', LANTAU: '大嶼山的士' };

const ENTRY_LABEL = {
  DEPOSIT_TOPUP: '存入按金',
  WEEKLY_FEE_DEDUCTION: '每週費用',
  PENALTY_DEDUCTION: '罰款',
  REFUND: '退款',
  ADJUSTMENT: '調整',
};

const ENTRY_TONE = {
  DEPOSIT_TOPUP: 'ok',
  WEEKLY_FEE_DEDUCTION: 'neutral',
  PENALTY_DEDUCTION: 'bad',
  REFUND: 'info',
  ADJUSTMENT: 'wait',
};

const REFUND_STATUS_LABEL = { PENDING: '待審批', APPROVED: '已批准', REJECTED: '已拒絕' };
const REFUND_STATUS_TONE = { PENDING: 'wait', APPROVED: 'ok', REJECTED: 'neutral' };

const ROLE_LABEL = { OWNER: '車主', MANAGER: '管理員', MEMBER: '成員' };

export async function DriverDetailView({ client, driverId, navigate, refreshBadges }) {
  const root = el('div', {}, [loading()]);

  async function load() {
    root.replaceChildren(loading());
    try {
      const driver = await api.drivers.detail(client, driverId);
      root.replaceChildren(
        header(driver),
        el('h2', { text: '按金' }),
        depositSection(driver),
        el('h2', { text: '車隊' }),
        fleetSection(driver),
        el('h2', { text: '帳目' }),
        ledgerCard(driver.ledger?.items ?? []),
        el('h2', { text: '退款紀錄' }),
        refundCard(driver.refunds?.items ?? []),
      );
      refreshBadges?.();
    } catch (error) {
      root.replaceChildren(
        el('div', {}, [
          el('div', { style: 'margin-bottom:12px' }, [
            el('button', { type: 'button', text: '← 返回司機審核', onClick: () => navigate('/kyc') }),
          ]),
          errorState(error, load),
        ]),
      );
    }
  }

  // ---------------------------------------------------------------- header

  function header(driver) {
    return el('div', { class: 'card card--pad' }, [
      el('div', { style: 'display:flex;align-items:flex-start;gap:14px;flex-wrap:wrap' }, [
        el('div', { style: 'flex:1 1 260px;min-width:0' }, [
          el('div', { style: 'display:flex;align-items:center;gap:10px;flex-wrap:wrap' }, [
            el('h1', { style: 'margin:0' }, [
              el('span', { class: 'mono', text: driver.vehicle_reg_mark ?? '—' }),
            ]),
            badge(
              DRIVER_STATUS_LABEL[driver.status] ?? driver.status,
              STATUS_TONE[driver.status] ?? 'neutral',
            ),
            driver.is_online ? badge('上線中', 'ok') : null,
          ]),
          el('div', { class: 'dim', style: 'margin-top:4px' }, [
            el('span', { class: 'mono', text: shortId(driver.id) }),
          ]),
        ]),
        el('div', { class: 'actions' }, [
          ...actions(driver),
          el('button', { type: 'button', text: '← 返回審核', onClick: () => navigate('/kyc') }),
        ]),
      ]),
      el('div', { style: 'margin-top:16px' }, [
        definitionList([
          ['的士類型', TAXI_LABEL[driver.taxi_type] ?? driver.taxi_type ?? '—'],
          ['的士司機證號', el('span', { class: 'mono', text: driver.taxi_driver_plate_no ?? '—' })],
          [
            '身份證末四位',
            el('span', {
              class: 'mono',
              text: driver.hk_id_last4 ? `****${driver.hk_id_last4}` : '—',
            }),
          ],
          ['司機檔案 ID', el('span', { class: 'mono', text: driver.id ?? '—' })],
          ['帳戶 ID', el('span', { class: 'mono', text: driver.user_id ?? '—' })],
          ['KYC 審核時間', driver.kyc_reviewed_at ? dateTime(driver.kyc_reviewed_at) : '尚未審核'],
          ['建立日期', date(driver.created_at)],
          ['最後更新', date(driver.updated_at)],
        ]),
      ]),
    ]);
  }

  /**
   * The same transitions the queue offers, so the page is actionable.
   * `approve` from PENDING_KYC does **not** put the driver on the road — it moves
   * them to DEPOSIT_REQUIRED. Activation is the deposit's job.
   */
  function actions(driver) {
    const buttons = [];

    if (driver.status === 'PENDING_KYC') {
      buttons.push(
        el('button', {
          type: 'button',
          class: 'btn btn--primary',
          text: '通過審核',
          onClick: () => review(driver, 'approve', '通過 KYC 審核'),
        }),
        el('button', {
          type: 'button',
          class: 'btn btn--danger',
          text: '拒絕',
          onClick: () => review(driver, 'reject', '拒絕 KYC 審核'),
        }),
      );
    }

    if (driver.status === 'DEPOSIT_REQUIRED') {
      buttons.push(
        el('button', {
          type: 'button',
          class: 'btn btn--primary',
          text: '存入按金',
          onClick: () => grant(driver),
        }),
      );
    }

    if (driver.status === 'ACTIVE') {
      buttons.push(
        el('button', {
          type: 'button',
          class: 'btn',
          text: '停權',
          onClick: () => review(driver, 'suspend', '暫停司機帳戶'),
        }),
        el('button', {
          type: 'button',
          class: 'btn btn--danger',
          text: '終止',
          onClick: () => review(driver, 'terminate', '終止司機帳戶'),
        }),
      );
    }

    if (driver.status === 'SUSPENDED') {
      buttons.push(
        el('button', {
          type: 'button',
          class: 'btn',
          text: '恢復',
          onClick: () => review(driver, 'approve', '恢復司機帳戶'),
        }),
        el('button', {
          type: 'button',
          class: 'btn btn--danger',
          text: '終止',
          onClick: () => review(driver, 'terminate', '終止司機帳戶'),
        }),
      );
    }

    return buttons;
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
        toast(`已更新狀態：${DRIVER_STATUS_LABEL[result.status] ?? result.status}`);
        close();
        await load();
      },
    });
  }

  function grant(driver) {
    const shortfall = driver.deposit?.shortfall_hkd;
    const suggested = shortfall && Number(shortfall) > 0 ? String(Number(shortfall)) : '500';
    const amount = el('input', { type: 'number', min: '1', step: '0.01', value: suggested });
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

  // --------------------------------------------------------------- deposit

  function depositSection(driver) {
    const deposit = driver.deposit ?? {};
    const required = Number(deposit.required_hkd ?? 0);
    const balance = Number(deposit.balance_hkd ?? 0);
    const progress = required > 0 ? Math.min(100, Math.round((balance / required) * 100)) : 0;

    return el('div', { class: 'card card--pad' }, [
      definitionList([
        ['可用餘額', money(deposit.balance_hkd)],
        ['凍結金額', money(deposit.held_hkd)],
        ['要求金額', money(deposit.required_hkd)],
        [
          '距達標',
          deposit.is_fulfilled
            ? badge('已達標', 'ok')
            : el('span', { class: 'danger', text: money(deposit.shortfall_hkd ?? 0) }),
        ],
        ['按金帳戶', deposit.has_account ? '已建立' : el('span', { class: 'dim', text: '尚未建立（從未存入）' })],
      ]),
      el('div', { class: 'meter', style: 'margin-top:14px' }, [
        el('div', {
          class: 'meter__fill',
          style: `width:${progress}%;background:var(--${deposit.is_fulfilled ? 'loss' : 'warn'})`,
        }),
      ]),
      el('div', { class: 'dim', style: 'margin-top:6px;font-size:12.5px' }, [
        `${money(deposit.balance_hkd)} / ${money(deposit.required_hkd)} · ${progress}%`,
      ]),
    ]);
  }

  // ----------------------------------------------------------------- fleet

  function fleetSection(driver) {
    if (!driver.fleet) {
      return el('div', { class: 'card' }, [
        el('div', { class: 'empty', text: '不在任何車隊名單上，按平台劃一費用收費。' }),
      ]);
    }
    const fleet = driver.fleet;
    return el('div', { class: 'card card--pad' }, [
      definitionList([
        ['車隊名稱', fleet.name],
        ['車隊牌照', el('span', { class: 'mono', text: fleet.license_no ?? '—' })],
        ['車隊狀態', fleet.status],
        ['名單角色', ROLE_LABEL[fleet.member_role] ?? fleet.member_role],
        ['加入日期', date(fleet.joined_at)],
        ['每週費用折扣', percent(fleet.weekly_fee_discount_percent)],
      ]),
      el('p', { class: 'dim', style: 'margin:14px 0 0' }, [
        '成員按車隊折扣價收費，不再計入平台劃一收費。',
      ]),
    ]);
  }

  // ---------------------------------------------------------------- ledger

  function ledgerCard(entries) {
    if (entries.length === 0) {
      return el('div', { class: 'card' }, [
        el('div', { class: 'empty', text: '還沒有任何帳目紀錄。' }),
      ]);
    }
    return el(
      'div',
      { class: 'card' },
      table(
        [
          { label: '時間' },
          { label: '類型' },
          { label: '金額', numeric: true },
          { label: '結餘', numeric: true },
          { label: '備註' },
          { label: '參考' },
        ],
        entries.map((entry) => [
          dateTime(entry.created_at),
          badge(ENTRY_LABEL[entry.entry_type] ?? entry.entry_type, ENTRY_TONE[entry.entry_type] ?? 'neutral'),
          money(entry.amount_hkd, { sign: true }),
          money(entry.balance_after_hkd),
          entry.note || el('span', { class: 'dim', text: '—' }),
          entry.reference
            ? el('span', { class: 'mono', style: 'font-size:12px', text: entry.reference })
            : el('span', { class: 'dim', text: '—' }),
        ]),
      ),
    );
  }

  // --------------------------------------------------------------- refunds

  function refundCard(refunds) {
    if (refunds.length === 0) {
      return el('div', { class: 'card' }, [
        el('div', { class: 'empty', text: '沒有退款紀錄。' }),
      ]);
    }
    return el(
      'div',
      { class: 'card' },
      table(
        [
          { label: '申請時間' },
          { label: '金額', numeric: true },
          { label: '狀態' },
          { label: '申請原因' },
          { label: '審批備註' },
          { label: '審批時間' },
        ],
        refunds.map((refund) => [
          dateTime(refund.created_at),
          money(refund.amount_hkd),
          badge(
            REFUND_STATUS_LABEL[refund.status] ?? refund.status,
            REFUND_STATUS_TONE[refund.status] ?? 'neutral',
          ),
          refund.note || el('span', { class: 'dim', text: '—' }),
          refund.decision_note || el('span', { class: 'dim', text: '—' }),
          refund.decided_at ? dateTime(refund.decided_at) : el('span', { class: 'dim', text: '—' }),
        ]),
      ),
    );
  }

  await load();
  return root;
}
