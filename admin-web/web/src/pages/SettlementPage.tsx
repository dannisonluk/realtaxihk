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

import { useEffect, useRef, useState } from 'react';
import { endpoints } from '../api/endpoints';
import type { SettlementPreview, SettlementRunResult } from '../api/types';
import { Card, DetailRow, Money, Rows } from '../components/primitives';
import { ErrorState } from '../components/states';
import { useApp } from '../app/AppContext';
import { normaliseError } from '../app/useLoad';
import { formatTime, PERIOD_PATTERN } from '../lib/labels';
import { useI18n } from '../i18n';
import { PageHead } from '../app/Shell';

export function SettlementPage() {
  const { t, formatLocale } = useI18n();
  const { client, notify, refreshBadges } = useApp();
  const [period, setPeriod] = useState('');
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [preview, setPreview] = useState<SettlementPreview | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [result, setResult] = useState<SettlementRunResult | null>(null);
  const [error, setError] = useState<Error | null>(null);

  function checkPeriod(): string | null {
    const trimmed = period.trim();
    if (trimmed !== '' && !PERIOD_PATTERN.test(trimmed)) {
      notify(t('settlement.errPeriod'), 'error');
      return null;
    }
    return trimmed;
  }

  function previewSettlement() {
    const wanted = checkPeriod();
    if (wanted === null) return;
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    setResult(null);
    void (async () => {
      try {
        const outcome = await endpoints.settlement.previewWeekly(client, {
          period: wanted || undefined,
        });
        setPreview(outcome);
        setSecondsLeft(outcome.confirm_expires_in_seconds);
      } catch (cause) {
        setError(normaliseError(cause));
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    })();
  }

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
      notify(t('settlement.errPreviewFirst'), 'error');
      return;
    }
    if (secondsLeft <= 0) {
      notify(t('settlement.errTokenExpired'), 'error');
      setPreview(null);
      return;
    }
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      const outcome = await endpoints.settlement.runWeekly(client, {
        period: preview.period,
        confirmToken: preview.confirm_token,
      });
      setResult(outcome);
      setPreview(null);
      notify(t('settlement.done', { count: outcome.charged }));
      void refreshBadges();
    } catch (cause) {
      setError(normaliseError(cause));
    } finally {
      busyRef.current = false;
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
      notify(t('settlement.errNeedWeek'), 'error');
      return;
    }
    if (busyRef.current) return;
    busyRef.current = true;
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
      notify(t('settlement.exported', { week: wanted }));
    } catch (cause) {
      setError(normaliseError(cause));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  const anomaly = result ? result.failed > 0 || result.tampered > 0 : false;

  return (
    <>
      <PageHead
        title={t('settlement.title')}
        subtitle={t('settlement.sub')}
      />

      <Card>
        <h2 className="t-title3" style={{ margin: '0 0 8px' }}>
          {t('settlement.manualTitle')}
        </h2>
        <p className="dim" style={{ margin: '0 0 16px' }}>
          {t('settlement.manualNote')}
        </p>
        <div className="field" style={{ maxWidth: 360 }}>
          <label className="field__label" htmlFor="settle-period">
            {t('settlement.fieldWeek')}
          </label>
          <input
            id="settle-period"
            type="text"
            placeholder={t('settlement.weekPlaceholder')}
            value={period}
            onChange={(event) => {
              setPeriod(event.target.value);
              // Any edit invalidates the preview: the token is bound to the
              // numbers that were shown, and those numbers were for a different
              // period. A stale result/error for another period is just as wrong.
              setPreview(null);
              setResult(null);
              setError(null);
            }}
          />
          <div className="t-footnote dim">{t('settlement.fieldWeekHint')}</div>
        </div>
        <div className="row" style={{ marginTop: 16 }}>
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy}
            onClick={() => void previewSettlement()}
          >
            {busy ? t('common.processing') : t('settlement.preview')}
          </button>
          <button type="button" className="btn" disabled={busy} onClick={() => void exportCsv()}>
            {t('settlement.exportCsv')}
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
              {t('settlement.previewHeading', { period: preview.period })}
            </h2>
            <p className="dim" style={{ margin: '0 0 16px' }}>
              {t('settlement.previewNote')}
            </p>

            <Rows>
              <DetailRow label={t('settlement.fee')}>
                <Money value={preview.fee_hkd} />
              </DetailRow>
              <DetailRow label={t('settlement.eligible')}>{preview.eligible_drivers}</DetailRow>
              <DetailRow label={t('settlement.willCharge')}>
                <strong>{preview.would_charge}</strong>
              </DetailRow>
              {preview.would_go_negative > 0 ? (
                <DetailRow label={t('settlement.becomeDebt')}>
                  <span style={{ color: 'var(--danger)' }}>{preview.would_go_negative}</span>
                  <span className="dim t-caption1">{t('settlement.becomeDebtNote')}</span>
                </DetailRow>
              ) : null}
              <DetailRow label={t('settlement.alreadyCharged')}>{preview.already_charged}</DetailRow>
              <DetailRow label={t('settlement.skippedNoAccount')}>{preview.skipped_no_deposit_account}</DetailRow>
              <DetailRow label={t('settlement.fleetManaged')}>
                {preview.fleet_managed}
                {preview.fleet_managed > 0 ? (
                  <span className="dim t-caption1">{t('settlement.fleetManagedNote')}</span>
                ) : null}
              </DetailRow>
              <DetailRow label={t('settlement.tampered')}>
                <span style={preview.tampered > 0 ? { color: 'var(--danger)' } : undefined}>
                  {preview.tampered}
                </span>
              </DetailRow>
              <DetailRow label={t('settlement.estTotal')}>
                <Money value={preview.total_charge_hkd} />
              </DetailRow>
              <DetailRow label={t('settlement.debtTotal')}>
                <Money value={preview.shortfall_total_hkd} />
              </DetailRow>
            </Rows>

            {preview.tampered > 0 ? (
              <p style={{ margin: '14px 0 0', color: 'var(--danger)' }}>
                {t('settlement.tamperedNote', { count: preview.tampered })}
              </p>
            ) : null}

            <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
              <div className="spread">
                <div className="dim">
                  {t('settlement.tokenNote', {
                    seconds: secondsLeft,
                    at: formatTime(new Date(Date.now() + secondsLeft * 1000).toISOString(), formatLocale),
                  })}
                </div>
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={busy || secondsLeft <= 0}
                  onClick={() => void run()}
                >
                  {busy ? t('settlement.running') : t('settlement.confirmRun', { count: preview.would_charge })}
                </button>
              </div>
              <p className="dim t-footnote" style={{ marginBottom: 0 }}>
                {t('settlement.tokenFootnote')}
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
                {t('settlement.anomalyNote')}
              </div>
            ) : null}

            <h2 className="t-title3" style={{ margin: '0 0 8px' }}>
              {t('settlement.resultHeading', { period: result.period })}
            </h2>

            <Rows>
              <DetailRow label={t('settlement.fee')}>
                <Money value={result.fee_hkd} />
              </DetailRow>
              <DetailRow label={t('settlement.eligible')}>{result.eligible_drivers}</DetailRow>
              <DetailRow label={t('settlement.fleetManaged')}>
                {result.fleet_managed}
                {result.fleet_managed > 0 ? (
                  <span className="dim t-caption1">{t('settlement.fleetManagedNote')}</span>
                ) : null}
              </DetailRow>
              <DetailRow label={t('settlement.charged')}>{result.charged}</DetailRow>
              <DetailRow label={t('settlement.skipped')}>{result.skipped}</DetailRow>
              <DetailRow label={t('settlement.failed')}>
                <span style={result.failed > 0 ? { color: 'var(--danger)' } : undefined}>
                  {result.failed}
                </span>
              </DetailRow>
              <DetailRow label={t('settlement.tampered')}>
                <span style={result.tampered > 0 ? { color: 'var(--danger)' } : undefined}>
                  {result.tampered}
                </span>
              </DetailRow>
            </Rows>

            {result.tampered > 0 ? (
              <p style={{ margin: '14px 0 0', color: 'var(--danger)' }}>
                {t('settlement.tamperedResultNote')}
              </p>
            ) : null}
            {result.failed > 0 ? (
              <p style={{ margin: '14px 0 0', color: 'var(--danger)' }}>
                {t('settlement.failedNote')}
              </p>
            ) : null}
          </Card>
        </div>
      ) : null}
    </>
  );
}
