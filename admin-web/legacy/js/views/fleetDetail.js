/**
 * One fleet: the roster, the settlement lever, and the weekly history.
 *
 * Two things here need care.
 *
 * **The roster is the billing boundary.** Adding a member takes them off the
 * platform-wide weekly run from the next settlement and puts them on this fleet's
 * discounted rate instead; removing them does the reverse. A driver may be on at
 * most one ACTIVE roster — the server enforces it with a partial unique index —
 * so a second add is a 409 rather than a silent double-bill.
 *
 * **Settlement is idempotent per (fleet, ISO week).** Re-running a week charges
 * nobody twice and updates the stored aggregate instead of appending, so the
 * button is safe to press twice. `tampered` above zero means the ledger reference
 * for that week is held by a different entry and the fee was deliberately *not*
 * collected — that needs a human, not a retry.
 *
 * A fleet that is not ACTIVE is refused: a suspended operator is not dispatching,
 * so it is not billing.
 */

import { api } from '../api.js';
import {
  badge,
  currentPeriod,
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
  PERIOD_PATTERN,
  shortId,
  table,
  toast,
} from '../dom.js';

const STATUS_TONE = { ACTIVE: 'ok', SUSPENDED: 'bad', DISSOLVED: 'neutral' };
const STATUS_LABEL = { ACTIVE: '營運中', SUSPENDED: '已停權', DISSOLVED: '已解散' };
const ROLE_LABEL = { OWNER: '車主', MANAGER: '管理員', MEMBER: '成員' };
const DRIVER_STATUS_LABEL = {
  PENDING_KYC: '審核中',
  DEPOSIT_REQUIRED: '待繳按金',
  ACTIVE: '已啟用',
  SUSPENDED: '已停權',
  TERMINATED: '已終止',
};
const TAXI_LABEL = { URBAN: '市區的士', NT: '新界的士', LANTAU: '大嶼山的士' };

export async function FleetDetailView({ client, fleetId, navigate, refreshBadges }) {
  const root = el('div', {}, [loading()]);
  let includeLeft = false;
  /**
   * The last run's totals, held across reloads. Running a settlement refreshes
   * the header counts and the history, and that reload rebuilds the whole view —
   * without this the result card would vanish the instant it appeared.
   */
  let lastRun = null;

  async function load() {
    root.replaceChildren(loading());
    try {
      const [fleet, members, history] = await Promise.all([
        api.fleets.list(client, { limit: 200 }).then((page) => {
          const found = (page.items ?? []).find((item) => item.id === fleetId);
          if (!found) throw new Error('找不到此車隊。它可能已被移除，或不在目前的分頁內。');
          return found;
        }),
        api.fleets.members(client, fleetId, { includeLeft }),
        api.fleets.settlementHistory(client, fleetId, { limit: 52 }),
      ]);

      root.replaceChildren(
        header(fleet),
        el('h2', { text: '每週結算' }),
        settlementSection(fleet),
        el('h2', { text: '結算紀錄' }),
        historyCard(history.items ?? []),
        el('h2', { text: '成員名單' }),
        rosterSection(members.items ?? [], fleet),
      );
      refreshBadges?.();
    } catch (error) {
      root.replaceChildren(
        el('div', {}, [
          el('div', { style: 'margin-bottom:12px' }, [
            el('button', { type: 'button', text: '← 返回車隊列表', onClick: () => navigate('/fleets') }),
          ]),
          errorState(error, load),
        ]),
      );
    }
  }

  // ---------------------------------------------------------------- header

  function header(fleet) {
    return el('div', { class: 'card card--pad' }, [
      el('div', { style: 'display:flex;align-items:flex-start;gap:14px;flex-wrap:wrap' }, [
        el('div', { style: 'flex:1 1 260px;min-width:0' }, [
          el('div', { style: 'display:flex;align-items:center;gap:10px;flex-wrap:wrap' }, [
            el('h1', { style: 'margin:0', text: fleet.name }),
            badge(STATUS_LABEL[fleet.status] ?? fleet.status, STATUS_TONE[fleet.status] ?? 'neutral'),
          ]),
          el('div', { class: 'dim', style: 'margin-top:4px' }, [
            el('span', { class: 'mono', text: fleet.license_no }),
          ]),
        ]),
        el('div', { class: 'actions' }, [
          el('button', { type: 'button', text: '編輯車隊', onClick: () => edit(fleet) }),
          el('button', { type: 'button', text: '← 返回列表', onClick: () => navigate('/fleets') }),
        ]),
      ]),
      el('div', { style: 'margin-top:16px' }, [
        definitionList([
          ['每週費用折扣', percent(fleet.weekly_fee_discount_percent)],
          ['成員人數', String(fleet.member_count ?? 0)],
          ['聯絡人', fleet.contact_name],
          ['聯絡電話', fleet.contact_phone],
          ['備註', fleet.note],
          ['建立日期', date(fleet.created_at)],
        ]),
      ]),
      fleet.status !== 'ACTIVE'
        ? el('p', { class: 'danger', style: 'margin:14px 0 0' }, [
            '車隊非營運中，結算會被拒絕。',
          ])
        : null,
    ]);
  }

  function edit(fleet) {
    const name = el('input', { type: 'text', value: fleet.name });
    const discount = el('input', {
      type: 'number',
      min: '0',
      max: '100',
      step: '0.5',
      value: fleet.weekly_fee_discount_percent,
    });
    const status = el(
      'select',
      {},
      Object.entries(STATUS_LABEL).map(([value, label]) =>
        el('option', { value, text: label, selected: value === fleet.status }),
      ),
    );

    openDialog({
      title: '編輯車隊',
      confirmLabel: '儲存',
      body: [
        field('車隊名稱', name),
        field('每週費用折扣（%）', discount, '由下一次結算起生效。'),
        field('營運狀態', status, '停權或解散後，車隊不會再產生每週收費。'),
      ],
      onSubmit: async (close) => {
        const trimmed = name.value.trim();
        if (!trimmed) throw new Error('車隊名稱不可留空。');
        const value = Number(discount.value);
        if (!Number.isFinite(value) || value < 0 || value > 100) {
          throw new Error('折扣須為 0 至 100 之間的數字。');
        }

        await api.fleets.update(client, fleet.id, {
          name: trimmed,
          weekly_fee_discount_percent: String(value),
          status: status.value,
        });
        toast('已更新車隊設定。');
        close();
        await load();
      },
    });
  }

  // ------------------------------------------------------------ settlement

  function settlementSection(fleet) {
    const periodInput = el('input', { type: 'text', placeholder: currentPeriod() });
    const button = el('button', {
      type: 'button',
      class: 'btn btn--primary',
      text: '執行本週車隊結算',
      onClick: () => runSettlement(fleet, periodInput, button),
    });

    return el('div', {}, [
      el('div', { class: 'card card--pad' }, [
        el('p', { class: 'dim', style: 'margin:0 0 16px' }, [
          '同一週重複執行不會重複收費；已收過的成員會計入「略過」。車隊必須為營運中才會收費。',
        ]),
        el('div', { style: 'max-width:360px' }, [
          field('ISO 週次（留空為本週）', periodInput, `本週為 ${currentPeriod()}。`),
        ]),
        button,
      ]),
      el('div', { style: 'margin-top:14px' }, [lastRun ? runCard(lastRun) : null]),
    ]);
  }

  async function runSettlement(fleet, periodInput, button) {
    const period = periodInput.value.trim();
    if (period !== '' && !PERIOD_PATTERN.test(period)) {
      toast('期間格式應為 YYYY-Www，例如 2026-W38。', 'error');
      return;
    }

    button.disabled = true;
    button.replaceChildren(el('span', { class: 'spinner' }), ' 執行中…');
    try {
      lastRun = await api.fleets.runSettlement(client, fleet.id, {
        period: period || undefined,
      });
      toast(`車隊結算完成：已收費 ${lastRun.charged} 位。`);
      // The header's member count and the history have both moved.
      await load();
    } catch (error) {
      button.disabled = false;
      button.textContent = '執行本週車隊結算';
      // Shown in place, below the lever, rather than replacing the whole page —
      // the roster and history above it are still valid and worth reading.
      const host = button.closest('.card')?.nextElementSibling;
      if (host) host.replaceChildren(errorState(error));
      else toast(error?.message || '結算失敗。', 'error');
    }
  }

  function runCard(run) {
    const anomaly = (run.failed ?? 0) > 0 || (run.tampered ?? 0) > 0;
    const gross = run.gross_fee_hkd;
    const saving =
      gross !== undefined && gross !== null
        ? Number(gross) - Number(run.fee_hkd)
        : null;

    return el('div', { class: `card card--pad${anomaly ? ' card--alert' : ''}` }, [
      el('h2', { class: 'card__title', text: `結果 · ${run.period}` }),
      definitionList([
        ['平台劃一費用', gross ? money(gross) : null],
        ['車隊每位費用', money(run.fee_hkd)],
        [
          '每位節省',
          saving !== null && saving !== 0
            ? `${money(saving)} · 折扣 ${percent(run.discount_percent)}`
            : null,
        ],
        ['計費成員', String(run.member_count ?? 0)],
        ['已收費', String(run.charged ?? 0)],
        ['略過（已收過）', String(run.skipped ?? 0)],
        ['失敗', String(run.failed ?? 0)],
        ['帳目異常', String(run.tampered ?? 0)],
        ['實收總額', money(run.collected_hkd)],
      ]),
      (run.tampered ?? 0) > 0
        ? el('p', { class: 'danger', style: 'margin:14px 0 0' }, [
            '帳目異常代表該週的 ledger reference 被其他帳目佔用，系統刻意未收費，' +
              '需要人手核對；重試不會解決。',
          ])
        : null,
    ]);
  }

  function historyCard(runs) {
    if (runs.length === 0) {
      return el('div', { class: 'card' }, [el('div', { class: 'empty', text: '還沒有結算紀錄。' })]);
    }
    return el(
      'div',
      { class: 'card' },
      table(
        [
          { label: '週次' },
          { label: '每位費用', numeric: true },
          { label: '折扣', numeric: true },
          { label: '成員', numeric: true },
          { label: '已收', numeric: true },
          { label: '略過', numeric: true },
          { label: '異常', numeric: true },
          { label: '實收', numeric: true },
          { label: '執行時間' },
        ],
        runs.map((run) => [
          el('span', { class: 'mono', text: run.period }),
          money(run.fee_hkd),
          percent(run.discount_percent),
          String(run.member_count ?? 0),
          String(run.charged ?? 0),
          String(run.skipped ?? 0),
          (run.tampered ?? 0) > 0
            ? el('span', { class: 'danger', text: String(run.tampered) })
            : '0',
          money(run.collected_hkd),
          dateTime(run.created_at),
        ]),
      ),
    );
  }

  // ----------------------------------------------------------------- roster

  function rosterSection(members, fleet) {
    const toggle = el('button', {
      type: 'button',
      class: 'btn btn--sm',
      text: includeLeft ? '只顯示在隊成員' : '包含已離隊成員',
      onClick: () => {
        includeLeft = !includeLeft;
        load();
      },
    });

    const add = el('button', {
      type: 'button',
      class: 'btn btn--primary btn--sm',
      text: '加入成員',
      onClick: () => addMember(fleet),
    });

    return el('div', {}, [
      el('div', { class: 'filters' }, [add, toggle]),
      el(
        'div',
        { class: 'card' },
        table(
          [
            { label: '司機' },
            { label: '的士類型' },
            { label: '司機狀態' },
            { label: '角色' },
            { label: '名單狀態' },
            { label: '加入日期' },
            { label: '離隊日期' },
            { label: '計費' },
            { label: '' },
          ],
          members.map((member) => [
            el('span', { class: 'mono', text: shortId(member.driver_profile_id) }),
            TAXI_LABEL[member.taxi_type] ?? member.taxi_type ?? '—',
            DRIVER_STATUS_LABEL[member.driver_status] ?? member.driver_status ?? '—',
            ROLE_LABEL[member.member_role] ?? member.member_role ?? '—',
            badge(
              member.status === 'ACTIVE' ? '在隊' : '已離隊',
              member.status === 'ACTIVE' ? 'ok' : 'neutral',
            ),
            date(member.joined_at),
            member.left_at ? date(member.left_at) : el('span', { class: 'dim', text: '—' }),
            member.status === 'ACTIVE' && member.driver_status === 'ACTIVE'
              ? badge('會收費', 'info')
              : el('span', { class: 'dim', text: '不計費' }),
            member.status === 'ACTIVE'
              ? el('button', {
                  type: 'button',
                  class: 'btn btn--danger btn--sm',
                  text: '移出',
                  onClick: () => removeMember(member),
                })
              : el('span', { class: 'dim', text: '—' }),
          ]),
          '名單上還沒有成員。',
        ),
      ),
    ]);
  }

  /**
   * Pick a driver from the admin register.
   *
   * Only ACTIVE profiles are offered: a driver still in KYC can be rostered, but
   * they are not billable — there is no deposit account to debit — so offering
   * them here would invite a settlement that silently collects nothing.
   */
  function addMember(fleet) {
    const host = el('div', {}, [loading('載入司機名單…')]);
    let selected = null;
    const role = el(
      'select',
      {},
      Object.entries(ROLE_LABEL).map(([value, label]) =>
        el('option', { value, text: label, selected: value === 'MEMBER' }),
      ),
    );

    api.drivers
      .list(client, { status: 'ACTIVE', limit: 200 })
      .then((page) => {
        const items = page.items ?? [];
        if (items.length === 0) {
          host.replaceChildren(
            el('div', { class: 'empty', text: '沒有已啟用的司機。司機需先通過審核並繳足按金。' }),
          );
          return;
        }
        host.replaceChildren(
          el(
            'div',
            { style: 'max-height:280px;overflow-y:auto;border:1px solid var(--border);border-radius:10px' },
            items.map((driver) =>
              el('label', {
                style:
                  'display:flex;gap:10px;align-items:center;padding:9px 12px;border-bottom:1px solid var(--border);cursor:pointer',
              }, [
                el('input', {
                  type: 'radio',
                  name: 'roster-driver',
                  onChange: () => {
                    selected = driver.id;
                  },
                }),
                el('span', {}, [
                  el('span', { class: 'mono', text: driver.vehicle_reg_mark ?? '—' }),
                  el('span', { class: 'dim', style: 'font-size:12.5px' }, [
                    `  ·  ${TAXI_LABEL[driver.taxi_type] ?? driver.taxi_type ?? '—'}`,
                    `  ·  ${shortId(driver.id)}`,
                  ]),
                ]),
              ]),
            ),
          ),
        );
      })
      .catch((error) => {
        host.replaceChildren(errorState(error));
      });

    openDialog({
      title: '加入車隊成員',
      confirmLabel: '加入',
      body: [
        el('p', { class: 'dim', style: 'margin:0 0 12px' }, [
          '加入後，該司機會由下一次結算起改按車隊折扣價收費，' +
            '不再計入平台劃一收費。一位司機同時只能屬於一個車隊名單。',
        ]),
        host,
        el('div', { style: 'margin-top:14px;max-width:220px' }, [field('名單角色', role)]),
      ],
      onSubmit: async (close) => {
        if (!selected) {
          throw new Error('請先選擇一位司機。');
        }
        await api.fleets.addMember(client, fleet.id, {
          driverProfileId: selected,
          memberRole: role.value,
        });
        toast('已加入車隊名單。');
        close();
        await load();
      },
    });
  }

  function removeMember(member) {
    openDialog({
      title: '移出車隊名單？',
      confirmLabel: '移出',
      danger: true,
      body: [
        el('p', { style: 'margin:0' }, [
          el('span', { class: 'mono', text: shortId(member.driver_profile_id) }),
          ' 將由下一次結算起，回復按平台劃一費用收費。',
        ]),
        el('p', { class: 'dim', style: 'margin:12px 0 0' }, [
          '紀錄會保留為「已離隊」，不會刪除，方便日後核對該週的名單。',
        ]),
      ],
      onSubmit: async (close) => {
        await api.fleets.removeMember(client, fleetId, member.driver_profile_id);
        toast('已移出車隊名單。');
        close();
        await load();
      },
    });
  }

  await load();
  return root;
}
