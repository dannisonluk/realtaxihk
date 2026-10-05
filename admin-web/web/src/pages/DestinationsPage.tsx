/**
 * Premium destinations (特選目的地) — admin-managed avatar-pinned map places.
 *
 * The console creates and edits these places (Airport T1/T2, Cathay City,
 * Civil Aviation Department, etc.). The public map reads the same rows through
 * `/api/v1/destinations`; HIDDEN rows never leave this admin list.
 *
 * The page follows the fleet register pattern: one list, one create dialog,
 * and inline edit/hide actions. No avatar upload is wired yet — that is the
 * next slice (presigned R2 upload), so `avatar_key` stays optional here.
 */

import { useEffect, useState } from 'react';
import { useApp } from '../app/AppContext';
import { useFormDialog } from '../app/useDialogs';
import { useLoad } from '../app/useLoad';
import { PageHead } from '../app/Shell';
import { endpoints } from '../api/endpoints';
import type { PremiumDestination } from '../api/types';
import { Card, Chip, Empty } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { formatTime } from '../lib/labels';
import { useI18n } from '../i18n';

interface DestinationDraft {
  code: string;
  nameZh: string;
  nameEn: string;
  lat: string;
  lng: string;
  radiusM: string;
}

function emptyDraft(): DestinationDraft {
  return { code: '', nameZh: '', nameEn: '', lat: '', lng: '', radiusM: '200' };
}

function validateDraft(draft: DestinationDraft): {
  code: string;
  name_zh: string;
  name_en: string;
  lat: number;
  lng: number;
  radius_m: number;
} {
  const code = draft.code.trim();
  const nameZh = draft.nameZh.trim();
  const nameEn = draft.nameEn.trim();
  const lat = Number(draft.lat);
  const lng = Number(draft.lng);
  const radius = Number(draft.radiusM);
  if (!code || !nameZh || !nameEn) {
    throw new Error('Code, Chinese name and English name are required.');
  }
  if (!Number.isFinite(lat) || lat < -90 || lat > 90) {
    throw new Error('Latitude must be a number between -90 and 90.');
  }
  if (!Number.isFinite(lng) || lng < -180 || lng > 180) {
    throw new Error('Longitude must be a number between -180 and 180.');
  }
  if (!Number.isFinite(radius) || radius < 50 || radius > 5000) {
    throw new Error('Radius must be a number between 50 and 5000 metres.');
  }
  return { code, name_zh: nameZh, name_en: nameEn, lat, lng, radius_m: Math.round(radius) };
}

export function DestinationsPage() {
  const { client, notify } = useApp();
  const { t, formatLocale } = useI18n();
  const dialog = useFormDialog();
  const [items, setItems] = useState<PremiumDestination[]>([]);

  const { data, error, loading, reload } = useLoad(
    () => endpoints.destinations.list(client),
    [client],
  );

  useEffect(() => {
    setItems(data?.items ?? []);
  }, [data]);

  function create() {
    const draft = emptyDraft();
    dialog.open({
      title: t('destinations.createTitle'),
      confirmLabel: t('destinations.createConfirm'),
      body: (
        <DestinationBody
          draft={draft}
          onChange={(patch) => Object.assign(draft, patch)}
        />
      ),
      onSubmit: async () => {
        const payload = validateDraft(draft);
        const created = await endpoints.destinations.create(client, payload);
        notify(t('destinations.created', { code: created.code }));
        reload();
      },
    });
  }

  function edit(destination: PremiumDestination) {
    const draft: DestinationDraft = {
      code: destination.code,
      nameZh: destination.name_zh,
      nameEn: destination.name_en,
      lat: String(destination.lat),
      lng: String(destination.lng),
      radiusM: String(destination.radius_m),
    };
    dialog.open({
      title: t('destinations.editTitle', { code: destination.code }),
      confirmLabel: t('destinations.editConfirm'),
      body: (
        <DestinationBody
          draft={draft}
          onChange={(patch) => Object.assign(draft, patch)}
        />
      ),
      onSubmit: async () => {
        const payload = validateDraft(draft);
        await endpoints.destinations.update(client, destination.id, payload);
        notify(t('destinations.updated', { code: destination.code }));
        reload();
      },
    });
  }

  function toggleStatus(destination: PremiumDestination) {
    const nextStatus = destination.status === 'ACTIVE' ? 'HIDDEN' : 'ACTIVE';
    dialog.open({
      title: t('destinations.toggleTitle'),
      confirmLabel: t('destinations.toggleConfirm'),
      onSubmit: async () => {
        await endpoints.destinations.update(client, destination.id, { status: nextStatus });
        notify(t('destinations.toggled', { code: destination.code }));
        reload();
      },
      body: (
        <div>
          {t(
            nextStatus === 'HIDDEN'
              ? 'destinations.toggleHideNote'
              : 'destinations.toggleShowNote',
            { code: destination.code },
          )}
        </div>
      ),
    });
  }

  return (
    <div>
      <PageHead
        title={t('destinations.title')}
        subtitle={t('destinations.sub')}
        actions={
          <button type="button" className="btn btn--primary" onClick={create}>
            {t('destinations.add')}
          </button>
        }
      />

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {!loading && !error ? (
        <Card>
          {items.length === 0 ? (
            <Empty title={t('destinations.empty')} />
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>{t('destinations.colCode')}</th>
                    <th>{t('destinations.colNameZh')}</th>
                    <th>{t('destinations.colNameEn')}</th>
                    <th className="num">{t('destinations.colLat')}</th>
                    <th className="num">{t('destinations.colLng')}</th>
                    <th className="num">{t('destinations.colRadius')}</th>
                    <th>{t('destinations.colStatus')}</th>
                    <th>{t('destinations.colCreated')}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {items.map((destination) => (
                    <tr key={destination.id}>
                      <td className="mono">{destination.code}</td>
                      <td>{destination.name_zh}</td>
                      <td>{destination.name_en}</td>
                      <td className="num">{destination.lat.toFixed(6)}</td>
                      <td className="num">{destination.lng.toFixed(6)}</td>
                      <td className="num">{destination.radius_m} m</td>
                      <td>
                        <Chip tone={destination.status === 'ACTIVE' ? 'ok' : 'neutral'}>
                          {t(
                            destination.status === 'ACTIVE'
                              ? 'destinations.statusActive'
                              : 'destinations.statusHidden',
                          )}
                        </Chip>
                      </td>
                      <td>{formatTime(destination.created_at, formatLocale)}</td>
                      <td>
                        <div className="spread">
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => edit(destination)}
                          >
                            {t('common.edit')}
                          </button>
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => toggleStatus(destination)}
                          >
                            {destination.status === 'ACTIVE'
                              ? t('destinations.hide')
                              : t('destinations.show')}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      ) : null}

      {dialog.element}
    </div>
  );
}

function DestinationBody({
  draft,
  onChange,
}: {
  draft: DestinationDraft;
  onChange: (patch: Partial<DestinationDraft>) => void;
}) {
  const { t } = useI18n();
  return (
    <>
      <label className="field">
        <span>{t('destinations.fieldCode')}</span>
        <input
          type="text"
          value={draft.code}
          placeholder="HKG_T1"
          onChange={(e) => onChange({ code: e.target.value })}
        />
        <span className="field__hint">{t('destinations.fieldCodeHint')}</span>
      </label>
      <label className="field">
        <span>{t('destinations.fieldNameZh')}</span>
        <input
          type="text"
          value={draft.nameZh}
          onChange={(e) => onChange({ nameZh: e.target.value })}
        />
      </label>
      <label className="field">
        <span>{t('destinations.fieldNameEn')}</span>
        <input
          type="text"
          value={draft.nameEn}
          onChange={(e) => onChange({ nameEn: e.target.value })}
        />
      </label>
      <div className="spread">
        <label className="field">
          <span>{t('destinations.fieldLat')}</span>
          <input
            type="number"
            step="0.000001"
            value={draft.lat}
            onChange={(e) => onChange({ lat: e.target.value })}
          />
        </label>
        <label className="field">
          <span>{t('destinations.fieldLng')}</span>
          <input
            type="number"
            step="0.000001"
            value={draft.lng}
            onChange={(e) => onChange({ lng: e.target.value })}
          />
        </label>
      </div>
      <label className="field">
        <span>{t('destinations.fieldRadius')}</span>
        <input
          type="number"
          min="50"
          max="5000"
          step="10"
          value={draft.radiusM}
          onChange={(e) => onChange({ radiusM: e.target.value })}
        />
        <span className="field__hint">{t('destinations.fieldRadiusHint')}</span>
      </label>
    </>
  );
}
