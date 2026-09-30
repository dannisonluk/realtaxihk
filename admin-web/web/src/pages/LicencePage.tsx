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
  documentKindLabel,
  driverStatusLabel,
  formatBytes,
  formatDate,
  formatTime,
  licenceStatusLabel,
  licenceStatusTone,
  shortId,
  taxiTypeLabel,
} from '../lib/labels';

const FILTERS: { value: LicenceReviewStatus | 'all'; label: string }[] = [
  { value: 'PENDING', label: '待審核' },
  { value: 'APPROVED', label: '已通過' },
  { value: 'REJECTED', label: '已拒絕' },
  { value: 'all', label: '全部' },
];

export function LicencePage() {
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
      title: approve ? '通過的士證審核' : '拒絕的士證審核',
      confirmLabel: approve ? '通過' : '拒絕',
      // A rejection is destructive in the sense that matters here: it is the
      // driver's only feedback. Marking it red keeps the operator's eye on it.
      danger: !approve,
      body: <DecideBody row={row} approve={approve} onChange={(v) => (reason = v)} />,
      onSubmit: async () => {
        if (!approve && !reason.trim()) {
          // The server refuses this too; catching it here avoids a round trip
          // and points at the field rather than at a banner.
          throw new Error('拒絕時必須填寫原因，否則司機無法知道要修正什麼。');
        }
        const result = await endpoints.licence.decide(client, row.id, {
          approve,
          reason: reason.trim() || undefined,
        });
        notify(
          approve
            ? `已通過（執照 ${result.driver_status ?? ''}）`
            : '已拒絕，原因已記錄',
        );
        setOpenId(null);
        reload();
      },
    });
  }

  return (
    <>
      <PageHead
        title="的士司機證審核"
        subtitle="人工審核的士司機證與駕駛執照。通過首次審核的司機會進入「待繳按金」；續期審核不會影響正在接單的司機。"
      />

      <div className="row" style={{ marginBottom: 16 }}>
        {FILTERS.map((item) => (
          <button
            key={item.label}
            type="button"
            className={item.value === filter ? 'btn btn--primary btn--sm' : 'btn btn--sm'}
            onClick={() => setFilter(item.value)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {data ? (
        data.length === 0 ? (
          <div className="card">
            <div className="empty">
              {filter === 'PENDING' ? '目前沒有待審核的的士司機證。' : '沒有符合條件的紀錄。'}
            </div>
          </div>
        ) : (
          <div className="card">
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>提交時間</th>
                    <th>執照號碼</th>
                    <th>到期日</th>
                    <th>文件</th>
                    <th>司機狀態</th>
                    <th>審核狀態</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((row) => (
                    <tr key={row.id}>
                      <td>{formatTime(row.submitted_at)}</td>
                      <td className="mono">{row.licence_no}</td>
                      <td>{formatDate(row.expires_on)}</td>
                      <td>
                        {/* The evidence state, not the documents. `false` here is
                            the row that must not be approved. */}
                        {row.has_required_documents ? (
                          <span className="dim">{row.document_count} 份</span>
                        ) : (
                          <Chip tone="danger">文件不足</Chip>
                        )}
                      </td>
                      <td>
                        {row.driver_status ? (
                          <Chip tone={row.driver_status === 'ACTIVE' ? 'ok' : 'neutral'}>
                            {driverStatusLabel(row.driver_status)}
                          </Chip>
                        ) : (
                          <span className="dim">—</span>
                        )}
                      </td>
                      <td>
                        <Chip tone={licenceStatusTone(row.status)}>
                          {licenceStatusLabel(row.status)}
                        </Chip>
                      </td>
                      <td>
                        <div className="row">
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => setOpenId(openId === row.id ? null : row.id)}
                          >
                            {openId === row.id ? '收起' : '檢視'}
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
                                    : '缺少必要文件，無法通過'
                                }
                                onClick={() => decide(row, true)}
                              >
                                通過
                              </button>
                              <button
                                type="button"
                                className="btn btn--danger btn--sm"
                                onClick={() => decide(row, false)}
                              >
                                拒絕
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
  const { data, error, loading } = useLoad(
    () => endpoints.licence.detail(client, submissionId, { ttl: 300 }),
    [client, submissionId],
  );

  if (loading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;
  if (!data) return null;

  return <DetailBody detail={data} />;
}

function DetailBody({ detail }: { detail: LicenceSubmissionDetail }) {
  // A missing document is the state that makes approval unsafe, so it is called
  // out above the images rather than left to be noticed.
  const notStored = detail.documents.filter((doc) => !doc.stored);
  const missingKinds = ['DRIVER_LICENCE', 'TAXI_DRIVER_PASS'].filter(
    (kind) => !detail.documents.some((doc) => doc.kind === kind),
  );

  return (
    <div style={{ padding: 16, borderTop: '1px solid var(--border)' }}>
      <h3 style={{ marginTop: 0 }}>提交內容</h3>

      <div className="grid grid--2" style={{ gap: 12, marginBottom: 16 }}>
        <Field label="執照號碼" value={detail.licence_no} mono />
        <Field label="到期日" value={formatDate(detail.expires_on)} />
        <Field
          label="的士類型 / 車牌"
          value={`${taxiTypeLabel(detail.driver_taxi_type)} · ${detail.driver_vehicle_reg_mark ?? '—'}`}
        />
        <Field label="的士證號" value={detail.driver_plate_no ?? '—'} mono />
        <Field label="提交時間" value={formatTime(detail.submitted_at)} />
        <Field label="審核時間" value={formatTime(detail.reviewed_at)} />
      </div>

      {detail.submitted_note ? (
        <div className="field">
          <div className="field__label">司機備註</div>
          <p style={{ margin: 0 }}>{detail.submitted_note}</p>
        </div>
      ) : null}

      {detail.rejection_reason ? (
        <div className="field">
          <div className="field__label">拒絕原因</div>
          <p style={{ margin: 0, color: 'var(--danger)' }}>{detail.rejection_reason}</p>
        </div>
      ) : null}

      {missingKinds.length > 0 ? (
        <p style={{ color: 'var(--danger)', marginTop: 0 }}>
          缺少必要文件：{missingKinds.map(documentKindLabel).join('、')}
        </p>
      ) : null}
      {notStored.length > 0 ? (
        <p style={{ color: 'var(--danger)' }}>
          以下文件已登記但未成功上傳，無法通過：
          {notStored.map((doc) => documentKindLabel(doc.kind)).join('、')}
        </p>
      ) : null}

      <h3>文件</h3>
      <div className="grid grid--2" style={{ gap: 16 }}>
        {detail.documents.map((doc) => (
          <div key={doc.id} className="card" style={{ padding: 12 }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <strong>{documentKindLabel(doc.kind)}</strong>
              {doc.stored ? (
                <Chip tone="ok">已上傳</Chip>
              ) : (
                <Chip tone="danger">未上傳</Chip>
              )}
            </div>
            <div className="dim" style={{ fontSize: 12, marginTop: 6 }}>
              {doc.content_type} · {formatBytes(doc.stored_size_bytes ?? doc.size_bytes)}
              {doc.stored_size_bytes !== null && doc.stored_size_bytes !== doc.size_bytes ? (
                <span> （申報 {formatBytes(doc.size_bytes)}）</span>
              ) : null}
            </div>
            {doc.stored ? (
              // `_blank` with `noreferrer`: the signed URL is short-lived and
              // must not leak through the Referer header to whatever it loads.
              <a
                href={doc.download_url}
                target="_blank"
                rel="noreferrer"
                className="btn btn--sm"
                style={{ marginTop: 8, display: 'inline-block' }}
              >
                開啟圖片（{Math.round(doc.url_expires_in / 60)} 分鐘內有效）
              </a>
            ) : (
              <div className="dim" style={{ marginTop: 8, fontSize: 12 }}>
                司機尚未完成上傳，請要求重新提交。
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="dim" style={{ fontSize: 12, marginTop: 12 }}>
        提交編號 <span className="mono">{shortId(detail.id)}</span>
        {detail.reviewed_by ? (
          <>
            {' '}
            · 審核人 <span className="mono">{shortId(detail.reviewed_by)}</span>
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
  return (
    <div className="stack">
      <p style={{ margin: 0 }}>
        執照 <span className="mono">{row.licence_no}</span>，到期日{' '}
        {formatDate(row.expires_on)}。
      </p>

      {approve ? (
        <p className="dim" style={{ margin: 0 }}>
          {row.driver_status === 'PENDING_KYC'
            ? '通過後司機會由「審核中」進入「待繳按金」，需存入按金才會正式啟用接單。'
            : `通過後只更新這份提交；司機目前的狀態（${driverStatusLabel(row.driver_status ?? '')}）不會改變。`}
        </p>
      ) : (
        <div className="field">
          <label className="field__label" htmlFor="licence-reject-reason">
            拒絕原因（必填）
          </label>
          <textarea
            id="licence-reject-reason"
            rows={3}
            placeholder="例如：到期日在照片中反光看不清，請重新拍攝"
            onChange={(event) => onChange(event.target.value)}
          />
          <div className="t-footnote dim">
            這段文字會直接顯示給司機，請寫清楚需要修正的地方。
          </div>
        </div>
      )}
    </div>
  );
}
