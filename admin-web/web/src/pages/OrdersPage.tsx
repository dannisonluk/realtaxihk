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
import { ORDER_STATUSES, FARE_MODES } from '../api/types';
import { Card, Chip, Money } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { useApp } from '../app/AppContext';
import { useLoad } from '../app/useLoad';
import { useI18n } from '../i18n';
import { formatTime, shortId, useLabels } from '../lib/labels';
import { PageHead } from '../app/Shell';

const PAGE_SIZE = 50;

export function OrdersPage() {
  const { client } = useApp();
  const navigate = useNavigate();
  const { t } = useI18n();
  const labels = useLabels();
  /**
   * `'open'` is the default view for a reason: the common question is not
   * "show me cancelled trips" but "show me everything still moving".
   */
  const [status, setStatus] = useState<string>('open');
  const [fareMode, setFareMode] = useState<string>('');
  const [offset, setOffset] = useState(0);

  const { data, error, loading, reload } = useLoad(
    (signal) =>
      endpoints.orders.list(client, {
        status: status === 'open' || status === '' ? undefined : status,
        openOnly: status === 'open',
        fareMode: fareMode === '' ? undefined : fareMode,
        limit: PAGE_SIZE,
        offset,
      }, signal),
    [client, status, fareMode, offset],
  );

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const hasMore = offset + PAGE_SIZE < total;

  return (
    <>
      <PageHead
        title={t('orders.title')}
        subtitle={t('orders.sub')}
      />

      <div className="filters">
        <button
          type="button"
          className={status === 'open' ? 'chip chip--action chip--brand' : 'chip chip--action'}
          aria-pressed={status === 'open'}
          onClick={() => {
            setStatus('open');
            setOffset(0);
          }}
        >
          {t('orders.tabOpen')}
        </button>
        <button
          type="button"
          className={status === '' ? 'chip chip--action chip--brand' : 'chip chip--action'}
          aria-pressed={status === ''}
          onClick={() => {
            setStatus('');
            setOffset(0);
          }}
        >
          {t('orders.tabAll')}
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
            className={status === value ? 'chip chip--action chip--brand' : 'chip chip--action'}
            aria-pressed={status === value}
            onClick={() => {
              setStatus(value);
              setOffset(0);
            }}
          >
            {labels.orderStatus(value)}
          </button>
        ))}
      </div>

      {/*
        The fare-mode filter, on its own row. It is a *second* axis: an operator
        reviewing agreed prices wants "fixed-price trips in any status", which
        the status chips above cannot express, and folding the two into one row
        would read as a single mutually-exclusive set.
      */}
      <div className="filters">
        <button
          type="button"
          className={fareMode === '' ? 'chip chip--action chip--brand' : 'chip chip--action'}
          aria-pressed={fareMode === ''}
          onClick={() => {
            setFareMode('');
            setOffset(0);
          }}
        >
          {t('orders.fareAll')}
        </button>
        {FARE_MODES.map((value) => (
          <button
            key={value}
            type="button"
            className={fareMode === value ? 'chip chip--action chip--brand' : 'chip chip--action'}
            aria-pressed={fareMode === value}
            onClick={() => {
              setFareMode(value);
              setOffset(0);
            }}
          >
            {labels.fareMode(value)}
          </button>
        ))}
      </div>

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {!loading && !error ? (
        <Card>
          {items.length === 0 ? (
            <div className="empty">
              {status === 'open' ? t('orders.emptyOpen') : t('orders.empty')}
            </div>
          ) : (
            <>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th scope="col">{t('orders.colCreated')}</th>
                      <th scope="col">{t('common.status')}</th>
                      <th scope="col">{t('orders.colPickup')}</th>
                      <th scope="col">{t('orders.colDropoff')}</th>
                      <th scope="col" className="num">{t('orders.colFare')}</th>
                      <th scope="col">{t('orders.colDriver')}</th>
                      <th scope="col">{t('orders.colPassenger')}</th>
                      <th scope="col" />
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
                    {t('common.count', { from: offset + 1, to: offset + items.length, total })}
                  </div>
                  <div className="row">
                    <button
                      type="button"
                      className="btn btn--sm"
                      disabled={loading || offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                    >
                      {t('common.prevPage')}
                    </button>
                    <button
                      type="button"
                      className="btn btn--sm"
                      disabled={loading || !hasMore}
                      onClick={() => setOffset(offset + PAGE_SIZE)}
                    >
                      {t('common.nextPage')}
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
  const { t, formatLocale } = useI18n();
  const labels = useLabels();
  return (
    <tr>
      <td>{formatTime(order.created_at, formatLocale)}</td>
      <td>
        <Chip tone={labels.orderStatusTone(order.status)}>{labels.orderStatus(order.status)}</Chip>
      </td>
      <td>
        <div className="truncate" style={{ maxWidth: 220 }} title={order.pickup_address}>
          {order.pickup_address || <span className="dim">—</span>}
        </div>
        <div className="dim t-caption1">
          {labels.taxiType(order.taxi_type)}
          {/* Only worth a word when it is not the default: a metered trip needs
              no annotation, a fixed price changes how the whole trip reads. */}
          {order.fare_mode === 'FIXED' ? ` · ${labels.fareMode(order.fare_mode)}` : ''}
        </div>
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
        {order.driver_id ? shortId(order.driver_id) : <span className="dim">{t('common.unassigned')}</span>}
      </td>
      <td className="mono">{shortId(order.passenger_id)}</td>
      <td>
        <button type="button" className="btn btn--sm" onClick={onOpen}>
          {t('orders.view')}
        </button>
      </td>
    </tr>
  );
}
