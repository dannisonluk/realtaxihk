/**
 * Unified subject search — the console's front door.
 *
 * The problem this page solves is the phone call. A passenger rings support and
 * says "I'm at the airport and the driver hasn't arrived"; the operator has a
 * phone number, a name, or a plate, and needs the account. Before this, support
 * could not locate an account while the caller was still on the line.
 *
 * Three things the UI has to get right, and each is a fact the server sends
 * rather than something the page could work out:
 *
 * 1. **`truncated` is displayed, not inferred.** `len(items) === limit` does not
 *    answer "is there more" — a result set exactly `limit` long may or may not
 *    have been cut off. Rendering "showing 20" when there are 400 is the
 *    difference between narrowing the search and believing you have seen
 *    everyone, and no client-side arithmetic can tell them apart.
 * 2. **`query_too_short` is a different message from "no results".** Otherwise
 *    a one-character search and a three-character search with no hits produce
 *    the same empty list, and the operator concludes the search is broken.
 * 3. **The search is not submitted per keystroke.** The endpoint is not audited
 *    per call precisely because keystroke-rate searching makes an audit trail
 *    worthless, and the same argument applies here: a request per character is a
 *    request per character against the one endpoint support depends on.
 */

import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { endpoints } from '../api/endpoints';
import type { SearchResponse, SearchResult } from '../api/types';
import { Card, Chip, Empty } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { useApp } from '../app/AppContext';
import { normaliseError } from '../app/useLoad';
import { useI18n } from '../i18n';
import { shortId, useLabels } from '../lib/labels';
import { PageHead } from '../app/Shell';

/** How long the box waits after the last keystroke before asking the server. */
const DEBOUNCE_MS = 250;

export function SearchPage() {
  const { client } = useApp();
  const navigate = useNavigate();
  const { t } = useI18n();
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  /**
   * The request sequence number.
   *
   * A debounced search races by construction — a slow reply for `cha` can land
   * after a fast reply for `chan`, and the list then shows results for a query
   * the box no longer contains. Comparing the token on arrival is the only
   * reliable guard; an `AbortController` would also work, but the client owns
   * its own fetch and cancelling mid-flight would look like a network failure.
   */
  const seq = useRef(0);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed === '') {
      seq.current += 1;
      setResult(null);
      setError(null);
      setLoading(false);
      return;
    }

    const token = ++seq.current;
    setLoading(true);
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const answer = await endpoints.search.query(client, trimmed);
          if (token !== seq.current) return;
          setResult(answer);
          setError(null);
        } catch (cause) {
          if (token !== seq.current) return;
          setError(normaliseError(cause));
        } finally {
          if (token === seq.current) setLoading(false);
        }
      })();
    }, DEBOUNCE_MS);

    return () => window.clearTimeout(timer);
  }, [client, query]);

  return (
    <>
      <PageHead
        title={t('search.title')}
        subtitle={t('search.sub')}
      />

      <Card className="card--pad">
        <div className="field">
          <label className="field__label" htmlFor="search-q">
            {t('search.keyword')}
          </label>
          <input
            id="search-q"
            type="search"
            // `autoFocus` because this page exists to be typed into the instant
            // it opens — the caller is already on the line.
            autoFocus
            placeholder={t('search.placeholder')}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <div className="t-footnote dim">
            {t('search.hint', { min: result?.min_query_length ?? 2 })}
          </div>
        </div>
      </Card>

      <div style={{ marginTop: 16 }}>
        {loading ? <LoadingState label={t('search.searching')} /> : null}
        {error ? <ErrorState error={error} onRetry={() => setQuery((q) => q)} /> : null}
      </div>

      {/*
        The "you did not search" case. Rendered *instead of* the results area,
        because "no matches" and "nothing to match against" must not look alike.
      */}
      {result?.query_too_short ? (
        <Card>
          <Empty
            title={t('search.tooShortTitle')}
            hint={t('search.tooShortHint', { min: result.min_query_length, len: result.query.length })}
          />
        </Card>
      ) : null}

      {result && !result.query_too_short ? (
        <Card>
          {result.items.length === 0 ? (
            <Empty title={t('search.noMatch', { query: result.query })} hint={t('search.noMatchHint')} />
          ) : (
            <>
              {/*
                The truncation notice sits *above* the table so it is read before
                the rows, not after — a warning below a full-looking table is a
                warning nobody sees.
              */}
              {result.truncated ? (
                <div className="message message--warn" style={{ marginBottom: 16 }}>
                  {t('search.truncated')}
                </div>
              ) : null}
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>{t('search.colKind')}</th>
                      <th>{t('search.colHolder')}</th>
                      <th>{t('search.colPhone')}</th>
                      <th>{t('search.colUsername')}</th>
                      <th>{t('search.colPlate')}</th>
                      <th>{t('search.colStatus')}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {result.items.map((item) => (
                      <ResultRow
                        key={`${item.kind}-${item.id}`}
                        item={item}
                        onOpen={() => {
                          // A driver opens the driver detail page; a passenger
                          // has no such page yet, so the row is informational
                          // for them. Navigating to a route that does not exist
                          // would be worse than not navigating.
                          if (item.driver_profile_id) {
                            navigate(`/drivers/${item.driver_profile_id}`);
                          }
                        }}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Card>
      ) : null}

      {!query.trim() ? (
        <Card>
          <Empty
            title={t('search.idleTitle')}
            hint={t('search.idleHint')}
          />
        </Card>
      ) : null}
    </>
  );
}

function ResultRow({ item, onOpen }: { item: SearchResult; onOpen: () => void }) {
  const isDriver = item.kind === 'DRIVER';
  const { t } = useI18n();
  const labels = useLabels();
  return (
    <tr>
      <td>
        <Chip tone={isDriver ? 'brand' : 'neutral'}>{isDriver ? t('search.driver') : t('search.passenger')}</Chip>
      </td>
      <td>{item.display_name || <span className="dim">—</span>}</td>
      <td className="mono">{item.phone_e164}</td>
      <td>
        {item.username ? <span className="mono">{item.username}</span> : <span className="dim">—</span>}
      </td>
      <td className="mono">{item.plate ?? <span className="dim">—</span>}</td>
      <td>
        {item.driver_status ? (
          <Chip tone={labels.driverStatusTone(item.driver_status)}>
            {labels.driverStatus(item.driver_status)}
          </Chip>
        ) : (
          <span className="dim">{item.is_active ? t('search.active') : t('search.inactive')}</span>
        )}
      </td>
      <td>
        {item.driver_profile_id ? (
          <button type="button" className="btn btn--sm" onClick={onOpen}>
            {t('search.viewDriver')}
          </button>
        ) : (
          <span className="dim t-caption1">{shortId(item.id)}</span>
        )}
      </td>
    </tr>
  );
}
