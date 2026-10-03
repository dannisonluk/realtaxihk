/**
 * The weekly service fee, run by hand.
 *
 * The scheduled job fires once at boot and then every 7 days; this is the ops
 * lever to re-run a specific ISO week after a failed batch. It is idempotent per
 * week, so re-running a settled period charges nobody twice and the counts below
 * are the truth about what this run did — not a delta.
 *
 * The four numbers that matter, and what each one means if it is not zero:
 *
 * * `charged`  — members actually debited.
 * * `skipped`  — already charged for this week. Expected on a re-run.
 * * `failed`   — one driver's debit raised; the rest of the run continued.
 * * `tampered` — the ledger reference for that week is held by an entry that is
 *   **not** this week's fee, so the fee was deliberately not collected
 *   (`SEC-13`). This needs a human. Retrying does not fix it.
 *
 * `fleet_managed` is reported, not charged: those drivers are on a fleet's active
 * roster and are billed by that fleet's own settlement at the discounted rate. It
 * is the visible half of the guard that stops a fleet member being charged twice —
 * the two jobs write different ledger references, so idempotency does not protect
 * anyone across them.
 */

import { api } from '../api.js';
import { currentPeriod, definitionList, el, errorState, field, money, PERIOD_PATTERN, toast } from '../dom.js';

export function SettlementView({ client, refreshBadges }) {
  const periodInput = el('input', {
    type: 'text',
    placeholder: currentPeriod(),
    value: '',
  });

  const resultHost = el('div', {});

  const runButton = el('button', {
    type: 'button',
    class: 'btn btn--primary',
    text: '執行結算',
    onClick: () => run(),
  });

  async function run() {
    const period = periodInput.value.trim();
    if (period !== '' && !PERIOD_PATTERN.test(period)) {
      toast('期間格式應為 YYYY-Www，例如 2026-W38。', 'error');
      return;
    }

    runButton.disabled = true;
    runButton.replaceChildren(el('span', { class: 'spinner' }), ' 執行中…');
    try {
      const result = await api.settlement.runWeekly(client, { period: period || undefined });
      resultHost.replaceChildren(resultCard(result));
      toast(`結算完成：已收費 ${result.charged} 位。`);
      refreshBadges?.();
    } catch (error) {
      resultHost.replaceChildren(errorState(error, run));
    } finally {
      runButton.disabled = false;
      runButton.textContent = '執行結算';
    }
  }

  function resultCard(run) {
    const anomaly = (run.failed ?? 0) > 0 || (run.tampered ?? 0) > 0;

    return el('div', { class: `card card--pad${anomaly ? ' card--alert' : ''}` }, [
      el('h2', { class: 'card__title', text: `結果 · ${run.period}` }),
      definitionList([
        ['服務費', money(run.fee_hkd)],
        ['合資格司機', String(run.eligible_drivers ?? 0)],
        [
          '車隊成員（未收費）',
          el('span', {}, [
            String(run.fleet_managed ?? 0),
            (run.fleet_managed ?? 0) > 0
              ? el('span', { class: 'dim', style: 'font-size:12.5px' }, [
                  '  — 由所屬車隊的結算以折扣價收費',
                ])
              : null,
          ]),
        ],
        ['已收費', String(run.charged ?? 0)],
        ['略過（已收過）', String(run.skipped ?? 0)],
        ['失敗', String(run.failed ?? 0)],
        ['帳目異常', String(run.tampered ?? 0)],
      ]),
      (run.tampered ?? 0) > 0
        ? el('p', { class: 'danger', style: 'margin:14px 0 0' }, [
            '帳目異常代表該週的 ledger reference 被其他帳目佔用，系統刻意未收費。' +
              '重試不會解決，需要人手核對該司機的帳目紀錄。',
          ])
        : null,
      (run.failed ?? 0) > 0
        ? el('p', { class: 'danger', style: 'margin:14px 0 0' }, [
            '有司機入帳失敗。可安全地重跑同一週：已成功的會計入「略過」，不會重複收費。',
          ])
        : null,
    ]);
  }

  return el('div', {}, [
    el('div', { class: 'card card--pad' }, [
      el('h2', { class: 'card__title', text: '手動執行每週服務費' }),
      el('p', { class: 'dim', style: 'margin:0 0 16px' }, [
        '系統每 7 天自動執行一次。此處可補跑指定週次；同一週重複執行不會重複收費。',
      ]),
      el('div', { style: 'max-width:360px' }, [
        field('ISO 週次（留空為本週）', periodInput, `本週為 ${currentPeriod()}。`),
      ]),
      runButton,
    ]),
    el('div', { style: 'margin-top:16px' }, [resultHost]),
  ]);
}
