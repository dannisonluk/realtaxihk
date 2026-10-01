/**
 * Order monitoring — the trip table.
 *
 * The console had **no order view at all** before this. `app/api/orders.py` is
 * entirely the passenger and driver view, so when a passenger called to say
 * their driver never arrived, there was no way to answer "what state is that
 * trip in, who is the driver, and how long ago did they accept".
 *
 * Every field on this page already existed on `orders` — nothing new is
 * measured. It is a surfacing, not a feature.
 *
 * Two choices worth stating:
 *
 * * **`open_only` is a server-side filter, rendered as a toggle.** The list of
 *   statuses that count as "moving" is defined next to the enum in
 *   `app/api/admin.py`, and re-deriving it here would produce a page that
 *   silently disagrees with the API the first time a status is added.
 * * **The row shows ids, not names.** Resolving a passenger id to a person is a
 *   separate, audited action — the search page is that action, and the driver
 *   detail page is the other. Inlining phones here would make every scroll a
 *   bulk PII read with no audit line.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { endpoints } from '../api/endpoints';
import type { AdminOrderRow } from '../api/types';
import { ORDER_STATUSES } from '../api/types';
import { Card, Chip, Money } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { useApp } from '../app/AppContext';
import { useLoad } from '../app/useLoad';
import { formatTime, orderStatusLabel, orderStatusTone, shortId, taxiTypeLabel } from '../lib/labels';
import { PageHead } from '../app/Shell';

const PAGE_SIZE = 50;

export function OrdersPage() {
  const { client } = useApp();
  const navigate = useNavigate();
  /**
   * `'open'` is the default view for a reason: the common question is not
   * "show me cancelled trips" but "show me everything still moving".
   */
  const [status, setStatus] = useState<string>('open');
  const [offset, setOffset] = useState(0);

  const { data, error, loading, reload } = useLoad(
    () =>
      endpoints.orders.list(client, {
        status: status === 'open' || status === '' ? undefined : status,
        openOnly: status === 'open',
        limit: PAGE_SIZE,
        offset,
      }),
    [client, status, offset],
  );

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const hasMore = offset + PAGE_SIZE < total;

  return (
    <>
      <PageHead
        title="訂單"
        subtitle="所有行程的即時狀態。乘客查詢時可在此確認司機、時間與當前狀態。"
      />

      <div className="filters">
        <button
          type="button"
          className={status === 'open' ? 'chip chip--brand' : 'chip'}
          aria-pressed={status === 'open'}
          onClick={() => {
            setStatus('open');
            setOffset(0);
          }}
        >
          進行中
        </button>
        <button
          type="button"
          className={status === '' ? 'chip chip--brand' : 'chip'}
          aria-pressed={status === ''}
          onClick={() => {
            setStatus('');
            setOffset(0);
          }}
        >
          全部
        </button>
        {/*
          The individual statuses, all from the shared `ORDER_STATUSES` list. A
          hand-written set here is how a filter starts sending a value the server
          rejects with a 400 — and the server *does* reject an unknown status
          rather than silently matching nothing, because a filter that quietly
          returns nothing is how an operator concludes there were no cancelled
          trips this week.
        */}
        {ORDER_STATUSES.map((value) => (
          <button
            key={value}
            type="button"
            className={status === value ? 'chip chip--brand' : 'chip'}
            aria-pressed={status === value}
            onClick={() => {
              setStatus(value);
              setOffset(0);
            }}
          >
            {orderStatusLabel(value)}
          </button>
        ))}
      </div>

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {!loading && !error ? (
        <Card>
          {items.length === 0 ? (
            <div className="empty">
              {status === 'open' ? '目前沒有進行中的行程。' : '沒有符合條件的訂單。'}
            </div>
          ) : (
            <>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>建立時間</th>
                      <th>狀態</th>
                      <th>上車</th>
                      <th>下車</th>
                      <th className="num">估價</th>
                      <th>司機</th>
                      <th>乘客</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((order) => (
                      <OrderTableRow
                        key={order.id}
                        order={order}
                        onOpen={() => navigate(`/orders/${order.id}`)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
              {total > PAGE_SIZE ? (
                <div className="spread" style={{ marginTop: 16 }}>
                  <div className="dim t-caption1">
                    顯示第 {offset + 1}–{offset + items.length} 筆，共 {total} 筆。
                  </div>
                  <div className="row">
                    <button
                      type="button"
                      className="btn btn--sm"
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                    >
                      上一頁
                    </button>
                    <button
                      type="button"
                      className="btn btn--sm"
                      disabled={!hasMore}
                      onClick={() => setOffset(offset + PAGE_SIZE)}
                    >
                      下一頁
                    </button>
                  </div>
                </div>
              ) : null}
            </>
          )}
        </Card>
      ) : null}
    </>
  );
}

function OrderTableRow({ order, onOpen }: { order: AdminOrderRow; onOpen: () => void }) {
  return (
    <tr>
      <td>{formatTime(order.created_at)}</td>
      <td>
        <Chip tone={orderStatusTone(order.status)}>{orderStatusLabel(order.status)}</Chip>
      </td>
      <td>
        <div className="truncate" style={{ maxWidth: 220 }} title={order.pickup_address}>
          {order.pickup_address || <span className="dim">—</span>}
        </div>
        <div className="dim t-caption1">{taxiTypeLabel(order.taxi_type)}</div>
      </td>
      <td>
        <div className="truncate" style={{ maxWidth: 220 }} title={order.dropoff_address}>
          {order.dropoff_address || <span className="dim">—</span>}
        </div>
      </td>
      <td className="num">
        <Money value={order.estimated_total_hkd} />
      </td>
      <td className="mono">
        {order.driver_id ? shortId(order.driver_id) : <span className="dim">未指派</span>}
      </td>
      <td className="mono">{shortId(order.passenger_id)}</td>
      <td>
        <button type="button" className="btn btn--sm" onClick={onOpen}>
          檢視
        </button>
      </td>
    </tr>
  );
}
