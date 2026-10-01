/**
 * One trip, in full.
 *
 * This is the page that answers the phone call. A passenger says the driver
 * never arrived; the operator needs the state, the driver, and how long each
 * step took — and the past-tense question ("how long did it sit in ACCEPTED
 * before somebody moved it") is answered by the timeline, not by the timestamps
 * alone.
 *
 * Three things are deliberately read from the server rather than computed here:
 *
 * * **`fare`** is the stored `fare_json` **snapshot**, not a recomputation. A
 *   fare recomputed today is a different number after any tariff change, and the
 *   disputed amount is always the one the passenger was actually quoted — which
 *   is also why `tariff_version` is shown beside it.
 * * **`timeline[].elapsed_seconds`** is computed server-side. Subtracting two
 *   ISO strings in the browser gets the DST case wrong; Hong Kong has no DST
 *   today, but a server-side subtraction is correct regardless and costs
 *   nothing. The UI must never re-derive it.
 * * **`broadcast_radius_km`** so "why did nobody take it" is answerable at all.
 */

import { Link, useParams } from 'react-router-dom';
import { endpoints } from '../api/endpoints';
import { Card, Chip, DetailRow, Money, Rows } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { useApp } from '../app/AppContext';
import { useLoad } from '../app/useLoad';
import {
  entryLabel,
  entryTone,
  formatTime,
  orderStatusLabel,
  orderStatusTone,
  shortId,
  taxiTypeLabel,
} from '../lib/labels';
import { PageHead } from '../app/Shell';

/** The five timeline steps, in the order the server emits them. */
const STEP_LABEL: Record<string, string> = {
  created: '建立訂單',
  accepted: '司機接單',
  driver_arrived: '司機到達',
  completed: '行程完成',
  cancelled: '行程取消',
};

export function OrderDetailPage() {
  const { orderId = '' } = useParams();
  const { client } = useApp();

  const { data, error, loading, reload } = useLoad(
    () => endpoints.orders.detail(client, orderId),
    [client, orderId],
  );

  if (loading) return <LoadingState />;
  if (error) {
    return (
      <div>
        <div style={{ marginBottom: 12 }}>
          <Link className="btn" to="/orders">
            ← 返回訂單列表
          </Link>
        </div>
        <ErrorState error={error} onRetry={reload} />
      </div>
    );
  }
  if (!data) return null;

  const ledger = data.ledger?.items ?? [];

  return (
    <div>
      <PageHead
        title="訂單詳情"
        actions={
          <Link className="btn" to="/orders">
            ← 返回訂單列表
          </Link>
        }
      />

      <Card className="card--pad">
        <div className="row-inline" style={{ marginBottom: 16 }}>
          <Chip tone={orderStatusTone(data.status)}>{orderStatusLabel(data.status)}</Chip>
          <span className="dim mono">{shortId(data.id)}</span>
          <span className="dim">{taxiTypeLabel(data.taxi_type)}</span>
        </div>

        <Rows>
          <DetailRow label="上車地點">{data.pickup_address || '—'}</DetailRow>
          <DetailRow label="下車地點">{data.dropoff_address || '—'}</DetailRow>
          <DetailRow label="估價">
            <Money value={data.estimated_total_hkd} />
            {Number(data.discount_percent) > 0 ? (
              <span className="dim"> （折扣 {data.discount_percent}%）</span>
            ) : null}
          </DetailRow>
          <DetailRow label="距離">{data.distance_km} km</DetailRow>
          <DetailRow label="廣播半徑">{data.broadcast_radius_km} km</DetailRow>
          <DetailRow label="司機">
            {data.driver_id ? (
              <Link className="mono" to={`/drivers/${data.driver_id}`}>
                {shortId(data.driver_id)}
              </Link>
            ) : (
              <span className="dim">未指派</span>
            )}
          </DetailRow>
          <DetailRow label="乘客">
            <span className="mono">{shortId(data.passenger_id)}</span>
            <span className="dim t-caption1"> — 如需聯絡資料請到搜尋頁</span>
          </DetailRow>
          {data.cancellation_reason ? (
            <DetailRow label="取消原因">{data.cancellation_reason}</DetailRow>
          ) : null}
        </Rows>
      </Card>

      {/*
        The timeline carries the whole diagnostic value of the page: the
        *deltas* answer "how long did it sit there", which the four raw
        timestamps do not. A step that never happened is rendered as missing
        rather than omitted, so the list keeps a fixed length and an absent step
        is visibly absent.
      */}
      <h2>時間軸</h2>
      <Card>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>步驟</th>
                <th>時間</th>
                <th className="num">距建立</th>
              </tr>
            </thead>
            <tbody>
              {data.timeline.map((step) => (
                <tr key={step.step}>
                  <td>{STEP_LABEL[step.step] ?? step.step}</td>
                  <td>{step.at ? formatTime(step.at) : <span className="dim">未發生</span>}</td>
                  <td className="num">
                    {step.elapsed_seconds === null ? (
                      <span className="dim">—</span>
                    ) : (
                      formatDuration(step.elapsed_seconds)
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <h2>費用快照</h2>
      <Card className="card--pad">
        <p className="dim" style={{ marginTop: 0 }}>
          以下為下單當時凍結的費用快照（<span className="mono">tariff_version {data.tariff_version}</span>
          ），並非以現行費率重新計算。爭議金額一律以此為準。
        </p>
        {Object.keys(data.fare ?? {}).length === 0 ? (
          <div className="empty">此訂單沒有費用快照。</div>
        ) : (
          <Rows>
            {Object.entries(data.fare).map(([key, value]) => (
              <DetailRow key={key} label={key}>
                <span className="mono">{formatFareValue(value)}</span>
              </DetailRow>
            ))}
          </Rows>
        )}
      </Card>

      <h2>相關帳目</h2>
      <Card>
        {ledger.length === 0 ? (
          <div className="empty">
            沒有相關帳目紀錄。<span className="dim">（車費本身不是帳目；只有罰款或人手調整才會出現。）</span>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>時間</th>
                  <th>類型</th>
                  <th className="num">金額</th>
                  <th className="num">結餘</th>
                  <th>備註</th>
                </tr>
              </thead>
              <tbody>
                {ledger.map((entry) => (
                  <tr key={entry.id}>
                    <td>{formatTime(entry.created_at)}</td>
                    <td>
                      <Chip tone={entryTone(entry.entry_type)}>{entryLabel(entry.entry_type)}</Chip>
                    </td>
                    <td className="num">
                      <Money value={entry.amount_hkd} sign />
                    </td>
                    <td className="num">
                      <Money value={entry.balance_after_hkd} />
                    </td>
                    <td>{entry.note || <span className="dim">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

/**
 * A duration, in the unit an operator would say it in.
 *
 * Seconds only below a minute, because "0.4 minutes" is not a thing anyone
 * says, and hours only above an hour so a long wait reads as `2 小時 5 分`
 * rather than `125 分`.
 */
function formatDuration(seconds: number): string {
  if (seconds < 0) return '—';
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    const rest = seconds % 60;
    return rest === 0 ? `${minutes} 分` : `${minutes} 分 ${rest} 秒`;
  }
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes === 0 ? `${hours} 小時` : `${hours} 小時 ${restMinutes} 分`;
}

/**
 * The `fare_json` snapshot has no declared schema — it is whatever the tariff
 * wrote at the time. Primitives render as themselves; anything nested is shown
 * as compact JSON rather than `[object Object]`.
 */
function formatFareValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return '—';
  }
}
