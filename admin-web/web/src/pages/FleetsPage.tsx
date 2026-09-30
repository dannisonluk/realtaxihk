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
import { Card, Chip, Empty } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { fleetStatusLabel, fleetStatusTone, formatTime } from '../lib/labels';

/**
 * `null` is a real filter value here — "all" — so it is spelled as a sentinel
 * rather than an empty string, which would be ambiguous with a missing param.
 */
const FILTERS: { value: FleetStatus | null; label: string }[] = [
  { value: null, label: '全部' },
  { value: 'ACTIVE', label: '營運中' },
  { value: 'SUSPENDED', label: '已停權' },
  { value: 'DISSOLVED', label: '已解散' },
];

export function FleetsPage() {
  const { client, refreshBadges, notify } = useApp();
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
      title: '新增車隊',
      confirmLabel: '建立',
      body: <CreateFleetBody onChange={(patch) => Object.assign(form, patch)} />,
      onSubmit: async () => {
        const name = form.name.trim();
        const license = form.license.trim();
        if (!name || !license) {
          throw new Error('車隊名稱及牌照號碼為必填。');
        }
        const discount = Number(form.discount);
        if (!Number.isFinite(discount) || discount < 0 || discount > 100) {
          throw new Error('折扣須為 0 至 100 之間的數字。');
        }

        const created = await endpoints.fleets.create(client, {
          name,
          license_no: license,
          weekly_fee_discount_percent: String(discount),
          contact_name: form.contactName.trim() || null,
          contact_phone: form.contactPhone.trim() || null,
          note: form.note.trim() || null,
        });
        notify(`已新增車隊 ${created.name}。`);
        reload();
      },
    });
  }

  const items = data?.items ?? [];

  return (
    <div>
      <PageHead
        title="車隊"
        subtitle="已獲發牌的車隊營運商。成員按車隊折扣價收費，不計入平台劃一收費。"
        actions={
          <button type="button" className="btn btn--primary" onClick={create}>
            新增車隊
          </button>
        }
      />

      <div className="filters">
        {FILTERS.map((option) => (
          <button
            key={option.label}
            type="button"
            className={option.value === filter ? 'chip chip--brand' : 'chip'}
            aria-pressed={option.value === filter}
            onClick={() => setFilter(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>

      {loading ? <LoadingState /> : null}
      {error ? <ErrorState error={error} onRetry={reload} /> : null}

      {!loading && !error ? (
        <Card>
          {items.length === 0 ? (
            <Empty title={filter ? '沒有符合條件的車隊。' : '還沒有任何車隊。'} />
          ) : (
            <div className="table-wrap">
              <table className="data">
              <thead>
                <tr>
                  <th>車隊名稱</th>
                  <th>車隊牌照</th>
                  <th>每週費用折扣</th>
                  <th className="num">成員人數</th>
                  <th>狀態</th>
                  <th>建立日期</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((fleet) => (
                  <tr key={fleet.id}>
                    <td>{fleet.name}</td>
                    <td className="mono">{fleet.license_no ?? '—'}</td>
                    <td>{fleet.weekly_fee_discount_percent}%</td>
                    <td className="num">{fleet.member_count ?? 0}</td>
                    <td>
                      <Chip tone={fleetStatusTone(fleet.status)}>
                        {fleetStatusLabel(fleet.status)}
                      </Chip>
                    </td>
                    <td>{formatTime(fleet.created_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn--sm"
                        onClick={() => navigate(`/fleets/${fleet.id}`)}
                      >
                        管理
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
  return (
    <>
      <label className="field">
        <span>車隊名稱</span>
        <input
          type="text"
          placeholder="星群的士"
          onChange={(e) => onChange({ name: e.target.value })}
        />
      </label>
      <label className="field">
        <span>車隊牌照號碼</span>
        <input
          type="text"
          placeholder="由運輸署發出"
          onChange={(e) => onChange({ license: e.target.value })}
        />
        <span className="field__hint">車隊名稱及牌照號碼皆不可重複。</span>
      </label>
      <label className="field">
        <span>每週費用折扣（%）</span>
        <input
          type="number"
          min="0"
          max="100"
          step="0.5"
          defaultValue="0"
          onChange={(e) => onChange({ discount: e.target.value })}
        />
        <span className="field__hint">0–100。成員按折扣後的車隊費用收費。</span>
      </label>
      <label className="field">
        <span>聯絡人</span>
        <input
          type="text"
          placeholder="選填"
          onChange={(e) => onChange({ contactName: e.target.value })}
        />
      </label>
      <label className="field">
        <span>聯絡電話</span>
        <input
          type="tel"
          placeholder="選填"
          onChange={(e) => onChange({ contactPhone: e.target.value })}
        />
      </label>
      <label className="field">
        <span>備註</span>
        <textarea rows={2} placeholder="選填" onChange={(e) => onChange({ note: e.target.value })} />
      </label>
    </>
  );
}
