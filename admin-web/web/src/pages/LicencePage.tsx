/**
 * P-3 — the taxi driver licence review queue.
 *
 * This is a queue of **submissions**, not of drivers, and the distinction is the
 * whole design. A driver who is `ACTIVE` and trading can have a *renewal* sitting
 * here; approving it changes only the submission, so nobody is taken offline to
 * swap a document. The list therefore shows both the submission's state and the
 * driver's own state, because an operator needs to know whether the person in
 * front of them is currently on the road.
 *
 * Two things this page deliberately does NOT do:
 *
 * 1. **It does not render image URLs in the list.** The queue carries counts and
 *    a boolean (`has_required_documents`); signed links are minted only in the
 *    detail view, so a working link to someone's identity document does not sit
 *    in every poll of the queue or in whatever that response is logged into.
 * 2. **It does not offer an approve button on an incomplete row.** The server
 *    refuses such an approval anyway, and a button that always errors teaches an
 *    operator to ignore errors.
 */

import { useState } from 'react';
import { endpoints } from '../api/endpoints';
import type {
  LicenceReviewStatus,
  LicenceSubmissionDetail,
  LicenceSubmissionRow,
} from '../api/types';
import { Chip } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { PageHead } from '../app/Shell';
import { useApp } from '../app/AppContext';
import { useFormDialog } from '../app/useDialogs';
import { useLoad } from '../app/useLoad';
import {
  formatBytes,
  formatDate,
  formatTime,
  shortId,
  useLabels,
} from '../lib/labels';
import { useI18n } from '../i18n';

const FILTERS: { value: LicenceReviewStatus | 'all'; labelKey: string }[] = [
  { value: 'PENDING', labelKey: 'licence.filterPending' },
  { value: 'APPROVED', labelKey: 'licence.filterApproved' },
  { value: 'REJECTED', labelKey: 'licence.filterRejected' },
  { value: 'all', labelKey: 'licence.filterAll' },
];

/** Signed URLs are trusted only when the scheme is a browser-safe download. */
function isSafeDownloadUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

export function LicencePage() {
  const { t, formatLocale } = useI18n();
  const labels = useLabels();
  const { client, notify } = useApp();
  const [filter, setFilter] = useState<LicenceReviewStatus | 'all'>('PENDING');
  const [openId, setOpenId] = useState<string | null>(null);
  const dialog = useFormDialog();

  const { data, error, loading, reload } = useLoad(
    async () => {
      const page = await endpoints.licence.list(client, { status: filter, limit: 100 });
      return page.items ?? [];
    },
    [client, filter],
  );

  function decide(row: LicenceSubmissionRow, approve: boolean) {
    let reason = '';
    dialog.open({
      title: approve ? t('licence.approveTitle') : t('licence.rejectTitle'),
      confirmLabel: approve ? t('licence.approveConfirm') : t('licence.rejectConfirm'),
      // A rejection is destructive in the sense that matters here: it is the
      // driver's only feedback. Marking it red keeps the operator's eye on it.
      danger: !approve,
      body: <DecideBody row={row} approve={approve} onChange={(v) => (reason = v)} />,
      onSubmit: async () => {
        if (!approve && !reason.trim()) {
          // The server refuses this too; catching it here avoids a round trip
          // and points at the field rather than at a banner.
          throw new Error(t('licence.errRejectReason'));
        }
        const result = await endpoints.licence.decide(client, row.id, {
          approve,
          reason: reason.trim() || undefined,
        });
        notify(
          approve
            ? t('licence.approved', { status: result.driver_status ?? '' })
            : t('licence.rejected'),
        );
        setOpenId(null);
        reload();
      },
    });
  }

  return (
    <>
      <PageHead
        title={t('licence.title')}
        subtitle={t('licence.sub')}
      />

      <div className="row" style={{ marginBottom: 16 }}>
        {FILTERS.map((item) => (
          <button
            key={item.labelKey}
            type="button"
            className={item.value === filter ? 'btn btn--primary btn--sm' : 'btn btn--sm'}
            onClick={() => setFilter(item.value)}
          >
            {t(item.labelKey)}
          </button>
        ))}
      </div>

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {data ? (
        data.length === 0 ? (
          <div className="card">
            <div className="empty">
              {filter === 'PENDING' ? t('licence.emptyPending') : t('licence.empty')}
            </div>
          </div>
        ) : (
          <div className="card">
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th scope="col">{t('licence.colSubmitted')}</th>
                    <th scope="col">{t('licence.colLicenceNo')}</th>
                    <th scope="col">{t('licence.colExpires')}</th>
                    <th scope="col">{t('licence.colDocs')}</th>
                    <th scope="col">{t('licence.colDriverStatus')}</th>
                    <th scope="col">{t('licence.colReviewStatus')}</th>
                    <th scope="col">{t('licence.colActions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((row) => (
                    <tr key={row.id}>
                      <td>{formatTime(row.submitted_at, formatLocale)}</td>
                      <td className="mono">{row.licence_no}</td>
                      <td>{formatDate(row.expires_on, formatLocale)}</td>
                      <td>
                        {/* The evidence state, not the documents. `false` here is
                            the row that must not be approved. */}
                        {row.has_required_documents ? (
                          <span className="dim">
                            {t('licence.docsCount', { count: row.document_count })}
                          </span>
                        ) : (
                          <Chip tone="danger">{t('licence.docsMissing')}</Chip>
                        )}
                      </td>
                      <td>
                        {row.driver_status ? (
                          <Chip tone={row.driver_status === 'ACTIVE' ? 'ok' : 'neutral'}>
                            {labels.driverStatus(row.driver_status)}
                          </Chip>
                        ) : (
                          <span className="dim">—</span>
                        )}
                      </td>
                      <td>
                        <Chip tone={labels.licenceStatusTone(row.status)}>
                          {labels.licenceStatus(row.status)}
                        </Chip>
                      </td>
                      <td>
                        <div className="row">
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => setOpenId(openId === row.id ? null : row.id)}
                          >
                            {openId === row.id ? t('licence.collapse') : t('common.view')}
                          </button>
                          {row.status === 'PENDING' ? (
                            <>
                              <button
                                type="button"
                                className="btn btn--primary btn--sm"
                                // Disabled rather than hidden: the operator needs
                                // to see *why* they cannot approve this row.
                                disabled={!row.has_required_documents}
                                title={
                                  row.has_required_documents
                                    ? undefined
                                    : t('licence.cannotApproveMissing')
                                }
                                onClick={() => decide(row, true)}
                              >
                                {t('licence.approve')}
                              </button>
                              <button
                                type="button"
                                className="btn btn--danger btn--sm"
                                onClick={() => decide(row, false)}
                              >
                                {t('licence.reject')}
                              </button>
                            </>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {openId ? (
              <SubmissionDetail client={client} submissionId={openId} />
            ) : null}
          </div>
        )
      ) : null}

      {dialog.element}
    </>
  );
}

/**
 * The detail panel, loaded on demand.
 *
 * Kept as its own component with its own load so opening one row does not
 * re-fetch the whole queue, and so a slow image-URL mint cannot delay the table.
 * It also means the signed URLs exist only while a row is expanded — the link
 * lifetime is bounded by the operator's attention, not by the page's.
 */
function SubmissionDetail({
  client,
  submissionId,
}: {
  client: ReturnType<typeof useApp>['client'];
  submissionId: string;
}) {
  const { data, error, loading, reload } = useLoad(
    () => endpoints.licence.detail(client, submissionId, { ttl: 300 }),
    [client, submissionId],
  );

  if (loading) return <LoadingState />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return null;

  return <DetailBody detail={data} />;
}

function DetailBody({ detail }: { detail: LicenceSubmissionDetail }) {
  const { t, formatLocale } = useI18n();
  const labels = useLabels();
  // A missing document is the state that makes approval unsafe, so it is called
  // out above the images rather than left to be noticed.
  const notStored = detail.documents.filter((doc) => !doc.stored);
  const missingKinds = ['DRIVER_LICENCE', 'TAXI_DRIVER_PASS'].filter(
    (kind) => !detail.documents.some((doc) => doc.kind === kind),
  );

  return (
    <div style={{ padding: 16, borderTop: '1px solid var(--border)' }}>
      <h3 style={{ marginTop: 0 }}>{t('licence.submitContent')}</h3>

      <div className="grid grid--2" style={{ gap: 12, marginBottom: 16 }}>
        <Field label={t('licence.fieldLicenceNo')} value={detail.licence_no} mono />
        <Field label={t('licence.fieldExpires')} value={formatDate(detail.expires_on, formatLocale)} />
        <Field
          label={t('licence.fieldTaxiPlate')}
          value={`${labels.taxiType(detail.driver_taxi_type)} · ${detail.driver_vehicle_reg_mark ?? '—'}`}
        />
        <Field label={t('licence.fieldPassNo')} value={detail.driver_plate_no ?? '—'} mono />
        <Field label={t('licence.fieldSubmitted')} value={formatTime(detail.submitted_at, formatLocale)} />
        <Field label={t('licence.fieldReviewed')} value={formatTime(detail.reviewed_at, formatLocale)} />
      </div>

      {detail.submitted_note ? (
        <div className="field">
          <div className="field__label">{t('licence.driverNote')}</div>
          <p style={{ margin: 0 }}>{detail.submitted_note}</p>
        </div>
      ) : null}

      {detail.rejection_reason ? (
        <div className="field">
          <div className="field__label">{t('licence.rejectReason')}</div>
          <p style={{ margin: 0, color: 'var(--danger)' }}>{detail.rejection_reason}</p>
        </div>
      ) : null}

      {missingKinds.length > 0 ? (
        <p style={{ color: 'var(--danger)', marginTop: 0 }}>
          {t('licence.missingKinds', {
            kinds: missingKinds.map((kind) => labels.documentKind(kind)).join(t('common.listSeparator')),
          })}
        </p>
      ) : null}
      {notStored.length > 0 ? (
        <p style={{ color: 'var(--danger)' }}>
          {t('licence.declaredNotUploaded')}
          {notStored.map((doc) => labels.documentKind(doc.kind)).join(t('common.listSeparator'))}
        </p>
      ) : null}

      <h3>{t('licence.docsHeading')}</h3>
      <div className="grid grid--2" style={{ gap: 16 }}>
        {detail.documents.map((doc) => (
          <div key={doc.id} className="card" style={{ padding: 12 }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <strong>{labels.documentKind(doc.kind)}</strong>
              {doc.stored ? (
                <Chip tone="ok">{t('licence.docUploaded')}</Chip>
              ) : (
                <Chip tone="danger">{t('licence.docMissing')}</Chip>
              )}
            </div>
            <div className="dim t-caption1" style={{ marginTop: 6 }}>
              {doc.content_type} · {formatBytes(doc.stored_size_bytes ?? doc.size_bytes)}
              {doc.stored_size_bytes !== null && doc.stored_size_bytes !== doc.size_bytes ? (
                <span>{t('licence.docDeclared', { size: formatBytes(doc.size_bytes) })}</span>
              ) : null}
            </div>
            {doc.stored && isSafeDownloadUrl(doc.download_url) ? (
              // `_blank` with `noreferrer`: the signed URL is short-lived and
              // must not leak through the Referer header to whatever it loads.
              <a
                href={doc.download_url}
                target="_blank"
                rel="noreferrer"
                className="btn btn--sm"
                style={{ marginTop: 8, display: 'inline-block' }}
              >
                {t('licence.docOpen', { minutes: Math.round(doc.url_expires_in / 60) })}
              </a>
            ) : doc.stored ? (
              <div className="dim t-caption1" style={{ marginTop: 8 }}>
                {t('licence.docUnavailable')}
              </div>
            ) : (
              <div className="dim t-caption1" style={{ marginTop: 8 }}>
                {t('licence.docPending')}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="dim t-caption1" style={{ marginTop: 12 }}>
        {t('licence.submitId', { id: '' })}<span className="mono">{shortId(detail.id)}</span>
        {detail.reviewed_by ? (
          <>
            {' '}
            {t('licence.reviewer', { id: '' })}<span className="mono">{shortId(detail.reviewed_by)}</span>
          </>
        ) : null}
      </div>
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="field">
      <div className="field__label">{label}</div>
      <div className={mono ? 'mono' : undefined}>{value}</div>
    </div>
  );
}

/**
 * The dialog body.
 *
 * The reason field is required for a rejection and the copy says why, because
 * "rejected" with no explanation is unactionable for the driver: they cannot
 * tell whether to retake the photo, fix the number, or give up.
 */
function DecideBody({
  row,
  approve,
  onChange,
}: {
  row: LicenceSubmissionRow;
  approve: boolean;
  onChange: (reason: string) => void;
}) {
  const { t, formatLocale } = useI18n();
  const labels = useLabels();
  return (
    <div className="stack">
      <p style={{ margin: 0 }}>
        {t('licence.dialogLicence', { no: '' })}<span className="mono">{row.licence_no}</span>
        {formatDate(row.expires_on, formatLocale)}
        {t('common.sentenceEnd')}
      </p>

      {approve ? (
        <p className="dim" style={{ margin: 0 }}>
          {row.driver_status === 'PENDING_KYC'
            ? t('licence.dialogApproveNoteFirst')
            : t('licence.dialogApproveNoteRenew', {
                status: labels.driverStatus(row.driver_status ?? ''),
              })}
        </p>
      ) : (
        <div className="field">
          <label className="field__label" htmlFor="licence-reject-reason">
            {t('licence.dialogRejectLabel')}
          </label>
          <textarea
            id="licence-reject-reason"
            rows={3}
            placeholder={t('licence.dialogRejectPlaceholder')}
            onChange={(event) => onChange(event.target.value)}
          />
          <div className="t-footnote dim">
            {t('licence.dialogRejectHint')}
          </div>
        </div>
      )}
    </div>
  );
}
