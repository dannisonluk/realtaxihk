/**
 * Admin account administration. **SUPER_ADMIN only, all four routes.**
 *
 * These are the routes that decide who may do everything else, so this is the
 * one place where the RBAC hierarchy has to hold without help. The server
 * applies `require_role` at the route *and* re-checks in `AdminAccountService`,
 * because an account promoting itself is the single move that makes every other
 * guard meaningless.
 *
 * Two things the page must not get wrong, and both are cases where a plausible
 * client-side rule diverges from the server's:
 *
 * 1. **The last super admin cannot be demoted.** The server refuses it, and the
 *    page disables the control using `super_admin_count` — which is a property
 *    of the *whole set*, returned on the page rather than on each row for
 *    exactly this reason. Without it the UI either offers an action that will be
 *    refused or re-derives the rule client-side and gets it wrong the first time
 *    the rule changes.
 * 2. **A new account cannot log in yet.** It has no TOTP secret until first
 *    login, so the response says `totp_enrolment_pending: true` and the page
 *    states it rather than implying it. An operator who reads "created" as
 *    "usable" hands over credentials that do not work.
 *
 * 3. **The last super admin cannot be deactivated either.** Same rule, same
 *    source (`super_admin_count`) — the server owns the count and the rule
 *    together, and the page must not re-derive either.
 *
 * The role control is a small set of explicit buttons rather than a free select.
 * A `PATCH` is one call per change and it is the dangerous transition, so it
 * gets a deliberate press with the destination spelled out — not a dropdown
 * whose change event fires on a stray arrow key.
 */

import { useState } from 'react';
import { endpoints } from '../api/endpoints';
import type { AdminAccount } from '../api/types';
import { ADMIN_ROLES } from '../api/types';
import { Card, Chip, Empty } from '../components/primitives';
import { ErrorState, LoadingState } from '../components/states';
import { useApp } from '../app/AppContext';
import { useFormDialog, useConfirmDialog } from '../app/useDialogs';
import { useLoad } from '../app/useLoad';
import { formatTime } from '../lib/labels';
import { useI18n } from '../i18n';
import { PageHead, useRoleLabel } from '../app/Shell';

export function AccountsPage() {
  const { client, notify, user } = useApp();
  const { t, formatLocale } = useI18n();
  const roleLabel = useRoleLabel();
  const createDialog = useFormDialog();
  const resetDialog = useFormDialog();
  const deactivateDialog = useConfirmDialog();
  /**
   * Which account's role buttons are in flight.
   *
   * Per-row rather than a single boolean: a role change on one account must not
   * grey out the controls on every other row, and with a page-level flag a slow
   * response makes the whole table look frozen.
   */
  const [pendingId, setPendingId] = useState<string | null>(null);

  const { data, error, loading, reload } = useLoad(
    () => endpoints.accounts.list(client),
    [client],
  );

  function changeRole(account: AdminAccount, next: string) {
    void (async () => {
      setPendingId(account.id);
      try {
        const result = await endpoints.accounts.changeRole(client, account.id, next);
        notify(
          t('accounts.roleChanged', {
            username: account.username,
            from: roleLabel(result.previous_role),
            to: roleLabel(result.admin_role),
          }),
        );
        reload();
      } catch (cause) {
        notify(cause instanceof Error ? cause.message : String(cause), 'error');
      } finally {
        setPendingId(null);
      }
    })();
  }

  function create() {
    const form = { username: '', email: '', password: '', fullName: '', role: 'SUPPORT' };
    createDialog.open({
      title: t('accounts.add'),
      confirmLabel: t('accounts.createConfirm'),
      body: <CreateAccountBody onChange={(patch) => Object.assign(form, patch)} />,
      onSubmit: async () => {
        if (!form.username.trim() || !form.email.trim() || !form.password) {
          throw new Error(t('accounts.errRequired'));
        }
        const created = await endpoints.accounts.create(client, {
          username: form.username.trim(),
          email: form.email.trim(),
          password: form.password,
          full_name: form.fullName.trim() || null,
          admin_role: form.role,
        });
        notify(
          created.totp_enrolment_pending
            ? t('accounts.createdEnrol', { username: created.username })
            : t('accounts.created', { username: created.username }),
        );
        reload();
      },
    });
  }

  function resetPassword(account: AdminAccount) {
    let password = '';
    let confirm = '';
    resetDialog.open({
      title: t('accounts.resetTitle', { username: account.username }),
      confirmLabel: t('accounts.resetConfirm'),
      danger: true,
      body: (
        <div className="stack">
          <p className="dim" style={{ margin: 0 }}>
            {t('accounts.resetWarning')}
          </p>
          <div className="field">
            <label className="field__label" htmlFor="reset-pw">
              {t('accounts.newPassword')}
            </label>
            <input
              id="reset-pw"
              type="password"
              autoComplete="new-password"
              onChange={(event) => (password = event.target.value)}
            />
          </div>
          <div className="field">
            <label className="field__label" htmlFor="reset-pw2">
              {t('accounts.confirmPassword')}
            </label>
            <input
              id="reset-pw2"
              type="password"
              autoComplete="new-password"
              onChange={(event) => (confirm = event.target.value)}
            />
          </div>
          {/*
            The reset now revokes both halves of the session, so the note states
            what happens instead of warning about what does not: the refresh
            family is stamped immediately, and an access token already in flight
            is killed at the revocation epoch — at most one round-trip stale, not
            fifteen minutes.
          */}
          <p className="dim" style={{ margin: 0 }}>
            {t('accounts.tokenNote')}
          </p>
        </div>
      ),
      onSubmit: async () => {
        if (password.length < 12) throw new Error(t('accounts.errTooShort'));
        if (password !== confirm) throw new Error(t('accounts.errMismatch'));
        // The server always revokes now — `sessions_revoked` is `true` on every
        // success, there is no partial-success form of the operation — so the
        // message states the outcome instead of branching on a field that cannot
        // be false.
        await endpoints.accounts.resetPassword(client, account.id, password);
        notify(t('accounts.resetDone', { username: account.username }));
        reload();
      },
    });
  }

  /**
   * Deactivate or reactivate an account.
   *
   * Unlike a role change this ends access outright, and on the way out it revokes
   * the account's refresh family — so the control should not read like the
   * routine role moves beside it.
   */
  function changeActive(account: AdminAccount, next: boolean) {
    if (!next) {
      deactivateDialog.open({
        title: t('accounts.deactivateTitle'),
        message: t('accounts.deactivateConfirmNote', { username: account.username }),
        confirmLabel: t('accounts.deactivate'),
        onConfirm: async () => {
          await performActiveChange(account, false);
        },
      });
      return;
    }
    void performActiveChange(account, true);
  }

  async function performActiveChange(account: AdminAccount, next: boolean) {
    setPendingId(account.id);
    try {
      const result = await endpoints.accounts.setActive(client, account.id, next);
      notify(
        result.is_active
          ? t('accounts.reactivated', { username: account.username })
          : t('accounts.deactivated', { username: account.username }),
      );
      reload();
    } catch (cause) {
      notify(cause instanceof Error ? cause.message : String(cause), 'error');
    } finally {
      setPendingId(null);
    }
  }

  if (loading) return <LoadingState />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return null;

  const lastSuperAdmin = data.super_admin_count;

  return (
    <>
      <PageHead
        title={t('accounts.title')}
        subtitle={t('accounts.sub')}
        actions={
          <button type="button" className="btn btn--primary" onClick={create}>
            {t('accounts.add')}
          </button>
        }
      />

      <div className="card card--pad" style={{ marginBottom: 16 }}>
        <div className="row-inline">
          <strong>{t('accounts.lastSuperTitle', { count: lastSuperAdmin })}</strong>
          <span className="dim">{t('accounts.lastSuperNote')}</span>
        </div>
      </div>

      <Card>
        {data.items.length === 0 ? (
          <Empty title={t('accounts.empty')} />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">{t('accounts.colUsername')}</th>
                  <th scope="col">{t('accounts.colName')}</th>
                  <th scope="col">{t('accounts.colEmail')}</th>
                  <th scope="col">{t('accounts.colRole')}</th>
                  <th scope="col">{t('accounts.colTotp')}</th>
                  <th scope="col">{t('accounts.colLastLogin')}</th>
                  <th scope="col">{t('accounts.colActions')}</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((account) => {
                  const isSelf = account.id === user?.id;
                  // The last usable super admin cannot be moved off the top of
                  // the hierarchy. The server refuses it; the page does not
                  // pretend otherwise.
                  const demoteBlocked =
                    account.admin_role === 'SUPER_ADMIN' && lastSuperAdmin <= 1;
                  // Deactivating the last usable super admin is refused for the
                  // same reason demoting them is: a state with nobody able to
                  // grant a role again has no in-app repair.
                  const deactivateBlocked =
                    account.admin_role === 'SUPER_ADMIN' && lastSuperAdmin <= 1;
                  return (
                    <tr key={account.id}>
                      <td>
                        <span className="mono">{account.username}</span>
                        {isSelf ? <span className="dim t-caption1">{t('accounts.you')}</span> : null}
                        {!account.is_active ? (
                          <div>
                            <Chip tone="danger">{t('accounts.disabled')}</Chip>
                          </div>
                        ) : null}
                      </td>
                      <td>{account.full_name || <span className="dim">—</span>}</td>
                      <td className="mono t-caption1">
                        {account.email}
                      </td>
                      <td>
                        <Chip tone={account.admin_role === 'SUPER_ADMIN' ? 'brand' : 'neutral'}>
                          {roleLabel(account.admin_role)}
                        </Chip>
                      </td>
                      <td>
                        {account.totp_enrolled ? (
                          <Chip tone="ok">{t('accounts.enrolled')}</Chip>
                        ) : (
                          <Chip tone="warn">{t('accounts.notEnrolled')}</Chip>
                        )}
                      </td>
                      <td>
                        {account.last_login_at ? (
                          formatTime(account.last_login_at, formatLocale)
                        ) : (
                          <span className="dim">{t('accounts.neverLoggedIn')}</span>
                        )}
                      </td>
                      <td>
                        <div className="row" style={{ flexWrap: 'wrap' }}>
                          {ADMIN_ROLES.filter((r) => r !== account.admin_role)
                            .filter((r) => !(r === 'SUPER_ADMIN' && account.admin_role === 'SUPER_ADMIN'))
                            .map((next) => {
                              const blocked = next !== 'SUPER_ADMIN' && demoteBlocked;
                              return (
                                <button
                                  key={next}
                                  type="button"
                                  className={next === 'SUPER_ADMIN' ? 'btn btn--primary btn--sm' : 'btn btn--sm'}
                                  disabled={blocked || pendingId === account.id}
                                  title={
                                    blocked
                                      ? t('accounts.cannotRemoveLast')
                                      : t('accounts.changeTo', { role: roleLabel(next) })
                                  }
                                  onClick={() => changeRole(account, next)}
                                >
                                  {roleLabel(next)}
                                </button>
                              );
                            })}
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => resetPassword(account)}
                          >
                            {t('accounts.resetPassword')}
                          </button>
                          {/*
                            The server refuses both of these; the page does not
                            offer what will be rejected. Disabling yourself is
                            blocked because with a single super admin there is
                            nobody left to switch you back on.
                          */}
                          <button
                            type="button"
                            className="btn btn--sm"
                            disabled={
                              isSelf ||
                              pendingId === account.id ||
                              (account.is_active && deactivateBlocked)
                            }
                            title={
                              isSelf
                                ? t('accounts.cannotDisableSelf')
                                : account.is_active && deactivateBlocked
                                  ? t('accounts.cannotDisableLast')
                                  : account.is_active
                                    ? t('accounts.deactivate')
                                    : t('accounts.reactivate')
                            }
                            onClick={() => changeActive(account, !account.is_active)}
                          >
                            {account.is_active
                              ? t('accounts.deactivate')
                              : t('accounts.reactivate')}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {createDialog.element}
      {resetDialog.element}
      {deactivateDialog.element}
    </>
  );
}

function CreateAccountBody({
  onChange,
}: {
  onChange: (patch: { username?: string; email?: string; password?: string; fullName?: string; role?: string }) => void;
}) {
  const { t } = useI18n();
  const roleLabel = useRoleLabel();

  return (
    <div className="stack">
      <div className="field">
        <label className="field__label" htmlFor="acc-username">
          {t('accounts.fieldUsername')}
        </label>
        <input
          id="acc-username"
          type="text"
          autoComplete="off"
          onChange={(event) => onChange({ username: event.target.value })}
        />
      </div>
      <div className="field">
        <label className="field__label" htmlFor="acc-email">
          {t('accounts.fieldEmail')}
        </label>
        <input
          id="acc-email"
          type="email"
          autoComplete="off"
          onChange={(event) => onChange({ email: event.target.value })}
        />
      </div>
      <div className="field">
        <label className="field__label" htmlFor="acc-name">
          {t('accounts.fieldName')}
        </label>
        <input
          id="acc-name"
          type="text"
          onChange={(event) => onChange({ fullName: event.target.value })}
        />
      </div>
      <div className="field">
        <label className="field__label" htmlFor="acc-password">
          {t('accounts.fieldPassword')}
        </label>
        <input
          id="acc-password"
          type="password"
          autoComplete="new-password"
          onChange={(event) => onChange({ password: event.target.value })}
        />
        <div className="t-footnote dim">{t('accounts.fieldPasswordHint')}</div>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="acc-role">
          {t('accounts.fieldRole')}
        </label>
        <select
          id="acc-role"
          defaultValue="SUPPORT"
          onChange={(event) => onChange({ role: event.target.value })}
        >
          {ADMIN_ROLES.map((value) => (
            <option key={value} value={value}>
              {roleLabel(value)}
            </option>
          ))}
        </select>
        <div className="t-footnote dim">{t('accounts.enrolNote')}</div>
      </div>
    </div>
  );
}
