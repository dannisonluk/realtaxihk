/**
 * The live fleet map.
 *
 * Answers the one question the console could not answer before: **where are the
 * cars right now**. Support needs it while a passenger is on the line ("your
 * driver is two streets away"), and operations needs it to notice that a whole
 * zone has gone quiet.
 *
 * ## Why this is polled and not a websocket
 *
 * `GET /admin/live/drivers` is a *snapshot*. The passenger and driver apps do
 * stream positions, because for them a moving marker is the product. Here it is
 * not: an operations view tolerates fifteen seconds of lag, and polling buys
 * three things a second fan-out channel would have to re-earn — the page owns
 * the refresh rate, it can stop asking when the tab is hidden, and there is no
 * half-applied delta stream to reconcile after a reconnect.
 *
 * ## Two clocks, and they are not interchangeable
 *
 * `generated_at` is the server clock at the moment of the read; `last_location_at`
 * is per car. The header shows the first, each row shows the second, and a car
 * whose fix is older than three poll intervals is drawn greyed and labelled
 * stale. Without that split a car that stopped reporting ten minutes ago is
 * drawn exactly like one that moved a second ago, which is the most misleading
 * thing a live map can do.
 *
 * ## Why the map is not the only representation
 *
 * A map is not readable by a screen reader and cannot be sorted. The table below
 * it carries the same rows, ordered by staleness, and is the accessible
 * equivalent rather than a duplicate.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { endpoints } from '../api/endpoints';
import type { AdminLiveDriver, AdminLiveDrivers } from '../api/types';
import { Card, Chip, Stat } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { useApp } from '../app/AppContext';
import { normaliseError } from '../app/useLoad';
import { useI18n } from '../i18n';
import { formatTime, useLabels } from '../lib/labels';
import { PageHead } from '../app/Shell';

/** How often the snapshot is re-fetched while auto-refresh is on. */
const POLL_MS = 15_000;

/** Hong Kong. The map opens on the whole territory, not on a single car. */
const HK_CENTRE: [number, number] = [22.3375, 114.16];
const HK_ZOOM = 11;

/**
 * A car is "stale" when its last fix is older than this.
 *
 * Three poll intervals: one missed tick is normal on a phone in a tunnel, three
 * means the car has stopped reporting. Drawing the two the same way is how an
 * operator concludes the map is broken rather than that a driver's phone died.
 */
const STALE_AFTER_MS = POLL_MS * 3;

/**
 * OpenStreetMap's public tiles.
 *
 * Chosen over the Maps JavaScript API because it needs **no API key and no
 * billing account**: web map rendering is the one part of Google's pricing that
 * is billed, while the mobile SDKs are free. An internal console that would be
 * the only consumer of a billed key is the wrong place to spend one.
 *
 * The usage policy is for light, human-paced use, which is what this is: a
 * handful of operators, one page, one tile fetch per pan. If this ever becomes a
 * wall display in an office, move to a self-hosted tile server or a paid tile
 * plan — hammering the public endpoint from an unattended page is exactly what
 * the policy forbids.
 */
const OSM_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const OSM_ATTRIBUTION = '&copy; OpenStreetMap contributors';

/** A fix's age in milliseconds, or null when there is no usable timestamp. */
function ageMs(iso: string | null, now: number): number | null {
  if (!iso) return null;
  const at = Date.parse(iso);
  return Number.isFinite(at) ? now - at : null;
}

interface LiveSnapshot {
  data: AdminLiveDrivers | null;
  error: Error | null;
  loading: boolean;
  refreshing: boolean;
  reload: () => void;
}

/**
 * Fetch the live snapshot, and keep fetching it while `enabled`.
 *
 * Deliberately **not** `useLoad`. That hook sets `loading` at the start of every
 * run, which is right for a page whose content is replaced wholesale but wrong
 * here: a fifteen-second poll would blank the map and tear Leaflet down on every
 * tick, so the operator would watch the map rebuild itself four times a minute.
 * `loading` therefore belongs to the first load only; later runs set
 * `refreshing`.
 *
 * A failed refresh **keeps the previous snapshot**. A stale map with a visible
 * error is more useful than an empty one, and `generated_at` already says how
 * old the picture is.
 *
 * The interval is not armed while `enabled` is false or the document is hidden.
 * A console left open on a wall display is exactly the case where an unattended
 * poll keeps hitting the database for a screen nobody is looking at.
 */
function useLiveSnapshot(includeOffline: boolean, enabled: boolean): LiveSnapshot {
  const { client } = useApp();
  const [data, setData] = useState<AdminLiveDrivers | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [nonce, setNonce] = useState(0);

  // Read the filter through a ref so the interval callback cannot capture a
  // stale value. The effect is keyed on `includeOffline` too, so a filter change
  // refetches at once rather than waiting for the next tick.
  const includeOfflineRef = useRef(includeOffline);
  includeOfflineRef.current = includeOffline;

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;
    const controller = new AbortController();
    /**
     * A slow poll must not stack up behind itself: without this, a request that
     * takes longer than the interval produces an unbounded queue of identical
     * reads, and the map gets *less* responsive the slower the API becomes.
     *
     * **Local, not a ref, and that is load-bearing.** Under `StrictMode` — which
     * `main.tsx` enables, so this is the shipped path — React mounts, unmounts
     * and re-mounts every effect. A ref-based guard is set by the first
     * instance's request, and the second instance's immediate `tick()` then
     * returns early because the flag is still up; meanwhile the first request
     * resolves into a `cancelled` closure and never clears `loading`. The page
     * sits on **Loading…** forever. Scoping the flag to the effect instance
     * means each mount owns its own guard, and a superseded request is discarded
     * by `cancelled` rather than by blocking its replacement.
     */
    let busy = false;

    const tick = async () => {
      if (cancelled || busy) return;
      busy = true;
      setRefreshing(true);
      try {
        const snapshot = await endpoints.live.drivers(client, {
          includeOffline: includeOfflineRef.current,
          signal: controller.signal,
        });
        if (cancelled) return;
        setData(snapshot);
        setError(null);
      } catch (cause) {
        if (cancelled) return;
        setError(normaliseError(cause));
      } finally {
        busy = false;
        if (!cancelled) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    };

    const arm = () => {
      if (!enabled || timer !== null) return;
      timer = setInterval(() => void tick(), POLL_MS);
    };
    const disarm = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        disarm();
        return;
      }
      // Catch up at once: the picture on screen is at least as old as the time
      // the tab spent hidden, and the operator is looking at it again now.
      void tick();
      arm();
    };

    void tick();
    if (document.visibilityState !== 'hidden') arm();
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      cancelled = true;
      controller.abort();
      disarm();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [client, includeOffline, enabled, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return { data, error, loading, refreshing, reload };
}

export function LiveMapPage() {
  const { t, formatLocale } = useI18n();
  const labels = useLabels();
  const navigate = useNavigate();
  const [includeOffline, setIncludeOffline] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const { data, error, loading, refreshing, reload } = useLiveSnapshot(
    includeOffline,
    autoRefresh,
  );

  const drivers = useMemo(() => data?.drivers ?? [], [data]);

  /**
   * The table is ordered by **staleness, most stale first**.
   *
   * Newest-first is the reflex and it is the wrong answer for this page: the
   * rows an operator needs to see are the ones that have stopped moving, and a
   * recency sort buries them under every car that is behaving.
   */
  const rows = useMemo(() => {
    const now = Date.now();
    return [...drivers].sort((a, b) => {
      const ageA = ageMs(a.last_location_at, now) ?? Number.MAX_SAFE_INTEGER;
      const ageB = ageMs(b.last_location_at, now) ?? Number.MAX_SAFE_INTEGER;
      return ageB - ageA;
    });
  }, [drivers]);

  const running = drivers.filter((d) => d.order_id !== null).length;
  const online = drivers.filter((d) => d.is_online).length;

  const openDriver = useCallback(
    (driver: AdminLiveDriver) => navigate(`/drivers/${driver.driver_profile_id}`),
    [navigate],
  );

  return (
    <>
      <PageHead
        title={t('live.title')}
        subtitle={t('live.sub')}
        actions={
          <>
            <button
              type="button"
              className={autoRefresh ? 'chip chip--action chip--brand' : 'chip chip--action'}
              aria-pressed={autoRefresh}
              onClick={() => setAutoRefresh((on) => !on)}
            >
              {autoRefresh ? t('live.autoOn') : t('live.autoOff')}
            </button>
            <button
              type="button"
              className={includeOffline ? 'chip chip--action chip--brand' : 'chip chip--action'}
              aria-pressed={includeOffline}
              onClick={() => setIncludeOffline((on) => !on)}
            >
              {t('live.includeOffline')}
            </button>
            <button type="button" className="btn btn--sm" disabled={refreshing} onClick={reload}>
              {t('live.refresh')}
            </button>
          </>
        }
      />

      {/*
        Pausing is not the same as unmounting: it stops the interval and keeps
        the snapshot on screen, which is what an operator wants while reading a
        row without the table reordering underneath them.
      */}
      {!autoRefresh ? <div className="message message--warn">{t('live.paused')}</div> : null}

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {!loading ? (
        <>
          <div className="grid">
            <Stat label={t('live.statShown')} value={drivers.length} />
            <Stat label={t('live.statOnline')} value={online} />
            <Stat label={t('live.statRunning')} value={running} />
            <Stat
              label={t('live.statSnapshot')}
              value={formatTime(data?.generated_at, formatLocale)}
            />
          </div>

          {/*
            `truncated` is the server's own statement, not a guess from
            `drivers.length === limit`. A map that silently drops the 501st car
            is indistinguishable from a fleet that shrank.
          */}
          {data?.truncated ? (
            <div className="message message--warn">{t('live.truncated')}</div>
          ) : null}

          <LiveMapCanvas drivers={drivers} onOpenDriver={openDriver} />

          <Card>
            {rows.length === 0 ? (
              <div className="empty">
                {includeOffline ? t('live.empty') : t('live.emptyOnlineOnly')}
              </div>
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th scope="col">{t('live.colPlate')}</th>
                      <th scope="col">{t('common.status')}</th>
                      <th scope="col">{t('live.colTaxi')}</th>
                      <th scope="col">{t('live.colTrip')}</th>
                      <th scope="col">{t('live.colUpdated')}</th>
                      <th scope="col" />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((driver) => (
                      <LiveRow
                        key={driver.driver_profile_id}
                        driver={driver}
                        onOpen={() => openDriver(driver)}
                        statusLabel={labels.driverStatus(driver.status)}
                        statusTone={labels.driverStatusTone(driver.status)}
                        taxiLabel={labels.taxiType(driver.taxi_type)}
                        tripLabel={
                          driver.order_status
                            ? labels.orderStatus(driver.order_status)
                            : t('live.idle')
                        }
                        tripTone={driver.order_id ? 'brand' : 'neutral'}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      ) : null}
    </>
  );
}

function LiveRow({
  driver,
  onOpen,
  statusLabel,
  statusTone,
  taxiLabel,
  tripLabel,
  tripTone,
}: {
  driver: AdminLiveDriver;
  onOpen: () => void;
  statusLabel: string;
  statusTone: 'neutral' | 'ok' | 'warn' | 'danger' | 'brand';
  taxiLabel: string;
  tripLabel: string;
  tripTone: 'neutral' | 'ok' | 'warn' | 'danger' | 'brand';
}) {
  const { t, formatLocale } = useI18n();
  const age = ageMs(driver.last_location_at, Date.now());
  const stale = age !== null && age > STALE_AFTER_MS;

  return (
    <tr>
      <td className="mono">{driver.vehicle_reg_mark || <span className="dim">—</span>}</td>
      <td>
        <Chip tone={statusTone}>{statusLabel}</Chip>
      </td>
      <td>{taxiLabel}</td>
      <td>
        <Chip tone={tripTone}>{tripLabel}</Chip>
      </td>
      <td>
        <div>{formatTime(driver.last_location_at, formatLocale)}</div>
        {stale ? <div className="t-caption1 dim">{t('live.stale')}</div> : null}
      </td>
      <td>
        <button type="button" className="btn btn--sm" onClick={onOpen}>
          {t('live.viewDriver')}
        </button>
      </td>
    </tr>
  );
}

/**
 * The Leaflet canvas.
 *
 * ## `circleMarker`, not `marker`
 *
 * `L.marker`'s default icon is a PNG resolved *relative to the stylesheet* at
 * runtime (`leaflet/dist/images/marker-icon.png`). Under a bundler that path
 * does not exist in the built output, so every marker renders as a broken image
 * — a failure that appears only in a production build, never in `vitest`, and
 * that is usually "fixed" by hard-coding an icon URL which then breaks again on
 * the next base-path change. A vector circle has no such dependency.
 *
 * ## Colour lives in CSS, not in these options
 *
 * The marker is given a `className` and *no* colour options, so `styles.css`
 * decides what it looks like. That is what makes the pin follow the theme: a
 * theme switch rewrites the custom properties on `<html>` and the browser
 * repaints, with no JavaScript involved. Colours passed here would become SVG
 * presentation attributes, and a stylesheet rule overrides those anyway — so
 * this is the arrangement that works, not merely a preference.
 */
function LiveMapCanvas({
  drivers,
  onOpenDriver,
}: {
  drivers: AdminLiveDriver[];
  onOpenDriver: (driver: AdminLiveDriver) => void;
}) {
  const { t } = useI18n();
  const labels = useLabels();
  const hostRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);

  // The click handler is read through a ref. `onOpenDriver` is an inline arrow
  // at the call site, so depending on it directly would rebuild every marker on
  // every parent render — and the parent renders on every poll.
  const onOpenRef = useRef(onOpenDriver);
  onOpenRef.current = onOpenDriver;

  useEffect(() => {
    const host = hostRef.current;
    if (!host || mapRef.current) return;

    const map = L.map(host, { center: HK_CENTRE, zoom: HK_ZOOM });
    L.tileLayer(OSM_TILE_URL, { maxZoom: 19, attribution: OSM_ATTRIBUTION }).addTo(map);
    mapRef.current = map;
    layerRef.current = L.layerGroup().addTo(map);

    return () => {
      // Leaflet registers window and document listeners and holds a reference to
      // the container. A map that is not removed leaks all of them, and this
      // page can be mounted and unmounted many times in one session.
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const layer = layerRef.current;
    if (!layer) return;

    layer.clearLayers();
    const now = Date.now();

    for (const driver of drivers) {
      const running = driver.order_id !== null;
      const age = ageMs(driver.last_location_at, now);
      const stale = age !== null && age > STALE_AFTER_MS;
      const variant = stale ? 'stale' : running ? 'running' : 'idle';

      const marker = L.circleMarker([driver.lat, driver.lng], {
        radius: running ? 8 : 6,
        className: `livemap__pin livemap__pin--${variant}`,
      });
      marker.bindTooltip(`${driver.vehicle_reg_mark} · ${labels.driverStatus(driver.status)}`);
      marker.on('click', () => onOpenRef.current(driver));
      marker.addTo(layer);
    }
  }, [drivers, labels]);

  return (
    <div className="card livemap">
      <div
        className="livemap__canvas"
        ref={hostRef}
        role="region"
        aria-label={t('live.mapLabel')}
      />
      <div className="livemap__legend">
        <span className="livemap__key">
          <span className="livemap__swatch livemap__pin--running" aria-hidden="true" />
          {t('live.legendRunning')}
        </span>
        <span className="livemap__key">
          <span className="livemap__swatch livemap__pin--idle" aria-hidden="true" />
          {t('live.legendIdle')}
        </span>
        <span className="livemap__key">
          <span className="livemap__swatch livemap__pin--stale" aria-hidden="true" />
          {t('live.legendStale')}
        </span>
      </div>
    </div>
  );
}
