/**
 * The weekly service fee — previewed, then run.
 *
 * The scheduled job fires once at boot and then every 7 days; this is the ops
 * lever to re-run a specific ISO week after a failed batch. It is idempotent per
 * week, so re-running a settled period charges nobody twice.
 *
 * **The run is gated behind a preview, and the gate is structural rather than a
 * runbook line.** The button charges every eligible driver at once, and because
 * it is idempotent per ISO week the *first* accidental press is not something
 * you get to undo — the money is gone and the reference is spent. The server
 * therefore refuses a run without a `confirm_token`, and the token is issued by
 * the preview and bound to that preview's **period and fee**.
 *
 * The page keeps the preview's result in state rather than re-fetching it for
 * the run, because the token is bound to what the operator actually saw. A fresh
 * preview between looking and pressing would produce a token for numbers that
 * had moved — which is the exact failure the gate exists to prevent.
 *
 * The four outcome numbers, and what each means when it is not zero:
 *
 * * `charged`   — members actually debited.
 * * `skipped`   — already charged for this week. Expected on a re-run.
 * * `failed`    — one driver's debit raised; the rest of the run continued.
 * * `tampered`  — the ledger reference for that week is held by an entry that is
 *   **not** this week's fee, so the fee was deliberately not collected
 *   (`SEC-13`). This needs a human. Retrying does not fix it.
 *
 * `fleet_managed` is reported, not charged: those drivers are on a fleet's
 * active roster and are billed by that fleet's own settlement at the discounted
 * rate. It is the visible half of the guard that stops a fleet member being
 * charged twice — the two jobs write different ledger references, so idempotency
 * does not protect anyone across them.
 */

import { useCallback, useEffect, useState } from 'react';
import { endpoints } from '../api/endpoints';
import type { SettlementPreview, SettlementRunResult } from '../api/types';
import { Card, DetailRow, Money, Rows } from '../components/primitives';
import { ErrorState } from '../components/states';
import { useApp } from '../app/AppContext';
import { normaliseError } from '../app/useLoad';
import { formatTime, PERIOD_PATTERN } from '../lib/labels';
import { PageHead } from '../app/Shell';

export function SettlementPage() {
  const { client, notify, refreshBadges } = useApp();
  const [period, setPeriod] = useState('');
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<SettlementPreview | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [result, setResult] = useState<SettlementRunResult | null>(null);
  const [error, setError] = useState<Error | null>(null);

  function checkPeriod(): string | null {
    const trimmed = period.trim();
    if (trimmed !== '' && !PERIOD_PATTERN.test(trimmed)) {
      notify('期間格式應為 YYYY-Www，例如 2026-W38。', 'error');
      return null;
    }
    return trimmed;
  }

  const previewSettlement = useCallback(async () => {
    const wanted = checkPeriod();
    if (wanted === null) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const outcome = await endpoints.settlement.previewWeekly(client, {
        period: wanted || undefined,
      });
      setPreview(outcome);
      setSecondsLeft(outcome.confirm_expires_in_seconds);
    } catch (cause) {
      setError(normaliseError(cause));
    } finally {
      setBusy(false);
    }
    // `checkPeriod` reads `period` from the closure and is re-created each
    // render, so the only honest dependency here is `period` itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, period, notify]);

  /**
   * The token countdown.
   *
   * Shown rather than discovered: an operator who has just read a screenful of
   * numbers and reached for the button should not be surprised by an expired
   * token at that moment. The timer clears itself at zero instead of going
   * negative, so the UI never claims a token is valid for a negative time.
   */
  useEffect(() => {
    if (!preview || secondsLeft <= 0) return;
    const timer = window.setInterval(() => {
      setSecondsLeft((current) => Math.max(0, current - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [preview, secondsLeft]);

  async function run() {
    if (!preview) {
      notify('請先預覽，確認將要收費的內容。', 'error');
      return;
    }
    if (secondsLeft <= 0) {
      notify('確認權杖已過期，請重新預覽。', 'error');
      setPreview(null);
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const outcome = await endpoints.settlement.runWeekly(client, {
        period: preview.period,
        confirmToken: preview.confirm_token,
      });
      setResult(outcome);
      setPreview(null);
      notify(`結算完成：已收費 ${outcome.charged} 位。`);
      void refreshBadges();
    } catch (cause) {
      setError(normaliseError(cause));
    } finally {
      setBusy(false);
    }
  }

  /**
   * Download the week's CSV.
   *
   * The blob is handed to the browser via an object URL, and that URL is revoked
   * in a `finally`. A leaked object URL pins the whole blob in memory for the
   * life of the document, and this one is a week of billing.
   */
  async function exportCsv() {
    const wanted = checkPeriod();
    if (wanted === null) return;
    if (!wanted) {
      notify('匯出需要指定 ISO 週次。', 'error');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const blob = await endpoints.settlement.exportCsv(client, wanted);
      const url = URL.createObjectURL(blob);
      try {
        const link = document.createElement('a');
        link.href = url;
        link.download = `settlement-${wanted}.csv`;
        document.body.appendChild(link);
        link.click();
        link.remove();
      } finally {
        URL.revokeObjectURL(url);
      }
      notify(`已匯出 ${wanted} 的帳目 CSV。`);
    } catch (cause) {
      setError(normaliseError(cause));
    } finally {
      setBusy(false);
    }
  }

  const anomaly = result ? result.failed > 0 || result.tampered > 0 : false;

  return (
    <>
      <PageHead
        title="每週結算"
        subtitle="平台劃一服務費的手動執行槓桿。執行前必須先預覽 —— 第一次誤按無法回復。"
      />

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
            placeholder="2026-W38"
            value={period}
            onChange={(event) => {
              setPeriod(event.target.value);
              // Any edit invalidates the preview: the token is bound to the
              // numbers that were shown, and those numbers were for a different
              // period.
              setPreview(null);
            }}
          />
          <div className="t-footnote dim">格式為 YYYY-Www，例如 2026-W38。</div>
        </div>
        <div className="row" style={{ marginTop: 16 }}>
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy}
            onClick={() => void previewSettlement()}
          >
            {busy ? '處理中…' : '預覽（不會收費）'}
          </button>
          <button type="button" className="btn" disabled={busy} onClick={() => void exportCsv()}>
            匯出 CSV
          </button>
        </div>
      </Card>

      {error ? (
        <div style={{ marginTop: 16 }}>
          <ErrorState error={error} onRetry={() => void previewSettlement()} />
        </div>
      ) : null}

      {preview ? (
        <div style={{ marginTop: 16 }}>
          <Card>
            <h2 className="t-title3" style={{ margin: '0 0 8px' }}>
              預覽 · {preview.period}
            </h2>
            <p className="dim" style={{ margin: '0 0 16px' }}>
              以下為將會發生的收費。此步驟不會寫入任何資料。
            </p>

            <Rows>
              <DetailRow label="服務費">
                <Money value={preview.fee_hkd} />
              </DetailRow>
              <DetailRow label="合資格司機">{preview.eligible_drivers}</DetailRow>
              <DetailRow label="將收費">
                <strong>{preview.would_charge}</strong>
              </DetailRow>
              {preview.would_go_negative > 0 ? (
                <DetailRow label="其中將變成欠款">
                  <span style={{ color: 'var(--danger)' }}>{preview.would_go_negative}</span>
                  <span className="dim t-caption1"> — 會被收費，但餘額轉負</span>
                </DetailRow>
              ) : null}
              <DetailRow label="已收過（略過）">{preview.already_charged}</DetailRow>
              <DetailRow label="沒有按金帳戶（略過）">{preview.skipped_no_deposit_account}</DetailRow>
              <DetailRow label="車隊成員（未收費）">
                {preview.fleet_managed}
                {preview.fleet_managed > 0 ? (
                  <span className="dim t-caption1"> — 由所屬車隊的結算以折扣價收費</span>
                ) : null}
              </DetailRow>
              <DetailRow label="帳目異常">
                <span style={preview.tampered > 0 ? { color: 'var(--danger)' } : undefined}>
                  {preview.tampered}
                </span>
              </DetailRow>
              <DetailRow label="預計總收費">
                <Money value={preview.total_charge_hkd} />
              </DetailRow>
              <DetailRow label="欠款總額">
                <Money value={preview.shortfall_total_hkd} />
              </DetailRow>
            </Rows>

            {preview.tampered > 0 ? (
              <p style={{ margin: '14px 0 0', color: 'var(--danger)' }}>
                有 {preview.tampered} 個帳目異常：該週的 ledger reference 被其他帳目佔用，系統刻意未收費。
                重試不會解決，需要人手核對。
              </p>
            ) : null}

            <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
              <div className="spread">
                <div className="dim">
                  確認權杖於 <strong>{secondsLeft}</strong> 秒後失效（{formatTime(new Date(Date.now() + secondsLeft * 1000).toISOString())}）。
                </div>
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={busy || secondsLeft <= 0}
                  onClick={() => void run()}
                >
                  {busy ? '執行中…' : `確認執行（收費 ${preview.would_charge} 位）`}
                </button>
              </div>
              <p className="dim t-footnote" style={{ marginBottom: 0 }}>
                權杖綁定此預覽的週次與服務費，並非綁定操作人 —— 同一個事件可以由任何財務管理員接手執行。
              </p>
            </div>
          </Card>
        </div>
      ) : null}

      {result ? (
        <div style={{ marginTop: 16 }}>
          <Card>
            {}
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
