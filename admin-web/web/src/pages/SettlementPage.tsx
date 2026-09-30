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

import { useState } from 'react';
import type { SettlementRunResult } from '../api/types';
import { endpoints } from '../api/endpoints';
import { Card, DetailRow, Money, Rows } from '../components/primitives';
import { ErrorState } from '../components/states';
import { useApp } from '../app/AppContext';
import { normaliseError } from '../app/useLoad';
import { currentIsoWeek } from '../lib/labels';
import { PageHead } from '../app/Shell';

const PERIOD_PATTERN = /^\d{4}-W\d{2}$/;

export function SettlementPage() {
  const { client, notify, refreshBadges } = useApp();
  const [period, setPeriod] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SettlementRunResult | null>(null);
  const [error, setError] = useState<Error | null>(null);

  async function run() {
    const trimmed = period.trim();
    if (trimmed !== '' && !PERIOD_PATTERN.test(trimmed)) {
      notify('期間格式應為 YYYY-Www，例如 2026-W38。', 'error');
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const outcome = await endpoints.settlement.runWeekly(client, {
        period: trimmed || undefined,
      });
      setResult(outcome);
      notify(`結算完成：已收費 ${outcome.charged} 位。`);
      void refreshBadges();
    } catch (cause) {
      setError(normaliseError(cause));
    } finally {
      setBusy(false);
    }
  }

  const anomaly = result ? result.failed > 0 || result.tampered > 0 : false;

  return (
    <>
      <PageHead title="每週結算" subtitle="平台劃一服務費的手動執行槓桿。" />

      <Card>
        <h2 className="t-title3" style={{ margin: '0 0 8px' }}>
          手動執行每週服務費
        </h2>
        <p className="dim" style={{ margin: '0 0 16px' }}>
          系統每 7 天自動執行一次。此處可補跑指定週次；同一週重複執行不會重複收費。
        </p>
        <div className="field" style={{ maxWidth: 360 }}>
          <label className="field__label" htmlFor="settle-period">
            ISO 週次（留空為本週）
          </label>
          <input
            id="settle-period"
            type="text"
            placeholder={currentIsoWeek()}
            value={period}
            onChange={(event) => setPeriod(event.target.value)}
          />
          <div className="t-footnote dim">本週為 {currentIsoWeek()}。</div>
        </div>
        <div style={{ marginTop: 16 }}>
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy}
            onClick={() => void run()}
          >
            {busy ? '執行中…' : '執行結算'}
          </button>
        </div>
      </Card>

      {error ? (
        <div style={{ marginTop: 16 }}>
          <ErrorState error={error} onRetry={() => void run()} />
        </div>
      ) : null}

      {result ? (
        <div style={{ marginTop: 16 }}>
          <Card>
            {/* The card is not merely informational when a run went wrong:
                `failed` is recoverable by re-running, `tampered` is not. */}
            {anomaly ? (
              <div className="message message--warn" style={{ marginBottom: 16 }}>
                此次執行有異常數字，請先看下方說明再決定是否重跑。
              </div>
            ) : null}

            <h2 className="t-title3" style={{ margin: '0 0 8px' }}>
              結果 · {result.period}
            </h2>

            <Rows>
              <DetailRow label="服務費">
                <Money value={result.fee_hkd} />
              </DetailRow>
              <DetailRow label="合資格司機">{result.eligible_drivers}</DetailRow>
              <DetailRow label="車隊成員（未收費）">
                {result.fleet_managed}
                {result.fleet_managed > 0 ? (
                  <span className="dim t-caption1"> — 由所屬車隊的結算以折扣價收費</span>
                ) : null}
              </DetailRow>
              <DetailRow label="已收費">{result.charged}</DetailRow>
              <DetailRow label="略過（已收過）">{result.skipped}</DetailRow>
              <DetailRow label="失敗">
                <span style={result.failed > 0 ? { color: 'var(--danger)' } : undefined}>
                  {result.failed}
                </span>
              </DetailRow>
              <DetailRow label="帳目異常">
                <span style={result.tampered > 0 ? { color: 'var(--danger)' } : undefined}>
                  {result.tampered}
                </span>
              </DetailRow>
            </Rows>

            {result.tampered > 0 ? (
              <p style={{ margin: '14px 0 0', color: 'var(--danger)' }}>
                帳目異常代表該週的 ledger reference 被其他帳目佔用，系統刻意未收費。重試不會解決，
                需要人手核對該司機的帳目紀錄。
              </p>
            ) : null}
            {result.failed > 0 ? (
              <p style={{ margin: '14px 0 0', color: 'var(--danger)' }}>
                有司機入帳失敗。可安全地重跑同一週：已成功的會計入「略過」，不會重複收費。
              </p>
            ) : null}
          </Card>
        </div>
      ) : null}
    </>
  );
}
