/**
 * The fleet register (的士車隊).
 *
 * A fleet is a **licensed operator** — the Transport Department grants the
 * licence — so fleets are created here, by the platform, and never
 * self-service. This is the onboarding step: the operator supplies a name and
 * licence number, and the platform sets the weekly fee discount they are
 * entitled to.
 *
 * The discount is the whole commercial point. A member of a fleet is charged
 * the platform's flat weekly fee **less this percentage**, and is excluded from
 * the platform-wide run so they are never charged both. See
 * `FleetDetailPage` for the roster and settlement levers.
 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApp } from '../app/AppContext';
import { useFormDialog } from '../app/useDialogs';
import { useLoad } from '../app/useLoad';
import { PageHead } from '../app/Shell';
import { endpoints } from '../api/endpoints';
import type { FleetStatus } from '../api/types';
import { Card, Chip, Empty, Percent } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { formatTime, useLabels } from '../lib/labels';
import { useI18n } from '../i18n';

/**
 * `null` is a real filter value here — "all" — so it is spelled as a sentinel
 * rather than an empty string, which would be ambiguous with a missing param.
 */
const FILTERS: { value: FleetStatus | null; labelKey: string }[] = [
  { value: null, labelKey: 'fleets.filterAll' },
  { value: 'ACTIVE', labelKey: 'enum.fleetStatus.ACTIVE' },
  { value: 'SUSPENDED', labelKey: 'enum.fleetStatus.SUSPENDED' },
  { value: 'DISSOLVED', labelKey: 'enum.fleetStatus.DISSOLVED' },
];

export function FleetsPage() {
  const { client, refreshBadges, notify } = useApp();
  const { t, formatLocale } = useI18n();
  const labels = useLabels();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<FleetStatus | null>(null);
  const dialog = useFormDialog();

  const { data, error, loading, reload } = useLoad(
    () => endpoints.fleets.list(client, { status: filter ?? undefined, limit: 100 }),
    [client, filter],
  );

  // Badge counts come from the register, so it has to re-read them once the
  // list has landed. In an effect, never during render — this sets state up in
  // the provider and would loop otherwise.
  useEffect(() => {
    if (data) void refreshBadges();
  }, [data, refreshBadges]);

  function create() {
    // The dialog owns the submit button and the in-flight state; the body only
    // reports field values up. Same shape as the KYC dialogs.
    const form = { name: '', license: '', discount: '0', contactName: '', contactPhone: '', note: '' };
    dialog.open({
      title: t('fleets.createTitle'),
      confirmLabel: t('fleets.createConfirm'),
      body: <CreateFleetBody onChange={(patch) => Object.assign(form, patch)} />,
      onSubmit: async () => {
        const name = form.name.trim();
        const license = form.license.trim();
        if (!name || !license) {
          throw new Error(t('fleets.errRequired'));
        }
        const discount = Number(form.discount);
        if (!Number.isFinite(discount) || discount < 0 || discount > 100) {
          throw new Error(t('fleets.errDiscount'));
        }

        const created = await endpoints.fleets.create(client, {
          name,
          license_no: license,
          weekly_fee_discount_percent: String(discount),
          contact_name: form.contactName.trim() || null,
          contact_phone: form.contactPhone.trim() || null,
          note: form.note.trim() || null,
        });
        notify(t('fleets.created', { name: created.name }));
        reload();
      },
    });
  }

  const items = data?.items ?? [];

  return (
    <div>
      <PageHead
        title={t('fleets.title')}
        subtitle={t('fleets.sub')}
        actions={
          <button type="button" className="btn btn--primary" onClick={create}>
            {t('fleets.add')}
          </button>
        }
      />

      <div className="filters">
        {FILTERS.map((option) => (
          <button
            key={option.labelKey}
            type="button"
            className={option.value === filter ? 'chip chip--brand' : 'chip'}
            aria-pressed={option.value === filter}
            onClick={() => setFilter(option.value)}
          >
            {t(option.labelKey)}
          </button>
        ))}
      </div>

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {!loading && !error ? (
        <Card>
          {items.length === 0 ? (
            <Empty title={filter ? t('fleets.emptyFiltered') : t('fleets.empty')} />
          ) : (
            <div className="table-wrap">
              <table className="data">
              <thead>
                <tr>
                  <th>{t('fleets.colName')}</th>
                  <th>{t('fleets.colLicence')}</th>
                  <th>{t('fleets.colDiscount')}</th>
                  <th className="num">{t('fleets.colMembers')}</th>
                  <th>{t('fleets.colStatus')}</th>
                  <th>{t('fleets.colCreated')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((fleet) => (
                  <tr key={fleet.id}>
                    <td>{fleet.name}</td>
                    <td className="mono">{fleet.license_no ?? '—'}</td>
                    <td>
                      <Percent value={fleet.weekly_fee_discount_percent} />
                    </td>
                    <td className="num">{fleet.member_count ?? 0}</td>
                    <td>
                      <Chip tone={labels.fleetStatusTone(fleet.status)}>
                        {labels.fleetStatus(fleet.status)}
                      </Chip>
                    </td>
                    <td>{formatTime(fleet.created_at, formatLocale)}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn--sm"
                        onClick={() => navigate(`/fleets/${fleet.id}`)}
                      >
                        {t('fleets.manage')}
                      </button>
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

/**
 * The create fields.
 *
 * Presentational: it holds no request state, because the dialog's `onSubmit`
 * and the error surface belong to `useFormDialog`. It reports each keystroke up
 * through `onChange`, which is the same shape the KYC dialogs use.
 */
interface FleetDraft {
  name: string;
  license: string;
  discount: string;
  contactName: string;
  contactPhone: string;
  note: string;
}

function CreateFleetBody({ onChange }: { onChange: (patch: Partial<FleetDraft>) => void }) {
  const { t } = useI18n();
  return (
    <>
      <label className="field">
        <span>{t('fleets.fieldName')}</span>
        <input
          type="text"
          placeholder={t('fleets.fieldNamePlaceholder')}
          onChange={(e) => onChange({ name: e.target.value })}
        />
      </label>
      <label className="field">
        <span>{t('fleets.fieldLicence')}</span>
        <input
          type="text"
          placeholder={t('fleets.fieldLicencePlaceholder')}
          onChange={(e) => onChange({ license: e.target.value })}
        />
        <span className="field__hint">{t('fleets.fieldLicenceHint')}</span>
      </label>
      <label className="field">
        <span>{t('fleets.fieldDiscount')}</span>
        <input
          type="number"
          min="0"
          max="100"
          step="0.5"
          defaultValue="0"
          onChange={(e) => onChange({ discount: e.target.value })}
        />
        <span className="field__hint">{t('fleets.fieldDiscountHint')}</span>
      </label>
      <label className="field">
        <span>{t('fleets.fieldContact')}</span>
        <input
          type="text"
          placeholder={t('fleets.fieldContactPlaceholder')}
          onChange={(e) => onChange({ contactName: e.target.value })}
        />
      </label>
      <label className="field">
        <span>{t('fleets.fieldPhone')}</span>
        <input
          type="tel"
          placeholder={t('fleets.fieldPhonePlaceholder')}
          onChange={(e) => onChange({ contactPhone: e.target.value })}
        />
      </label>
      <label className="field">
        <span>{t('fleets.fieldNote')}</span>
        <textarea
          rows={2}
          placeholder={t('fleets.fieldContactPlaceholder')}
          onChange={(e) => onChange({ note: e.target.value })}
        />
      </label>
    </>
  );
}
