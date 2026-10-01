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
import { useFormDialog } from '../app/useDialogs';
import { useLoad } from '../app/useLoad';
import { formatTime } from '../lib/labels';
import { PageHead, roleLabel } from '../app/Shell';

export function AccountsPage() {
  const { client, notify, user } = useApp();
  const createDialog = useFormDialog();
  const resetDialog = useFormDialog();
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
          `${account.username} 權限已由 ${roleLabel(result.previous_role)} 改為 ${roleLabel(result.admin_role)}。`,
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
      title: '新增管理員',
      confirmLabel: '建立',
      body: <CreateAccountBody onChange={(patch) => Object.assign(form, patch)} />,
      onSubmit: async () => {
        if (!form.username.trim() || !form.email.trim() || !form.password) {
          throw new Error('帳號、電郵與密碼皆為必填。');
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
            ? `已建立 ${created.username}。該帳戶首次登入時需要綁定驗證器，綁定前無法登入。`
            : `已建立 ${created.username}。`,
        );
        reload();
      },
    });
  }

  function resetPassword(account: AdminAccount) {
    let password = '';
    let confirm = '';
    resetDialog.open({
      title: `重設 ${account.username} 的密碼`,
      confirmLabel: '重設密碼',
      danger: true,
      body: (
        <div className="stack">
          <p className="dim" style={{ margin: 0 }}>
            此操作不需要對方目前的密碼，用於「唯一的管理員被鎖在外面」的情況，並會清除鎖定計數。
          </p>
          <div className="field">
            <label className="field__label" htmlFor="reset-pw">
              新密碼
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
              確認新密碼
            </label>
            <input
              id="reset-pw2"
              type="password"
              autoComplete="new-password"
              onChange={(event) => (confirm = event.target.value)}
            />
          </div>
          {/*
            The reset cannot revoke a token already in flight — the access token
            is a signed JWT with no server-side session store. Saying so is the
            honest answer; a claim the system cannot make would train the
            operator to trust it.
          */}
          <p className="dim" style={{ margin: 0 }}>
            注意：已簽發的存取權杖無法即時撤銷，最多 15 分鐘後才失效。
          </p>
        </div>
      ),
      onSubmit: async () => {
        if (password.length < 12) throw new Error('密碼至少需要 12 個字元。');
        if (password !== confirm) throw new Error('兩次輸入的密碼不一致。');
        const result = await endpoints.accounts.resetPassword(client, account.id, password);
        notify(
          result.sessions_revoked
            ? `已重設 ${account.username} 的密碼，並已登出其所有工作階段。`
            : `已重設 ${account.username} 的密碼。（現有存取權杖仍會在 15 分鐘內有效）`,
        );
        reload();
      },
    });
  }

  if (loading) return <LoadingState />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return null;

  const lastSuperAdmin = data.super_admin_count;

  return (
    <>
      <PageHead
        title="管理員帳戶"
        subtitle="只有超級管理員可以新增帳戶或更改權限。更改權限是唯一可以令其他所有防線失效的操作。"
        actions={
          <button type="button" className="btn btn--primary" onClick={create}>
            新增管理員
          </button>
        }
      />

      <div className="card card--pad" style={{ marginBottom: 16 }}>
        <div className="row-inline">
          <strong>目前有 {lastSuperAdmin} 位啟用中的超級管理員。</strong>
          <span className="dim">
            系統不允許移除最後一位超級管理員的權限 —— 否則將沒有人可以再授權。
          </span>
        </div>
      </div>

      <Card>
        {data.items.length === 0 ? (
          <Empty title="沒有任何管理員帳戶。" />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>帳號</th>
                  <th>姓名</th>
                  <th>電郵</th>
                  <th>權限</th>
                  <th>驗證器</th>
                  <th>最後登入</th>
                  <th>操作</th>
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
                  return (
                    <tr key={account.id}>
                      <td>
                        <span className="mono">{account.username}</span>
                        {isSelf ? (
                          <span className="dim t-caption1"> （你）</span>
                        ) : null}
                        {!account.is_active ? (
                          <div>
                            <Chip tone="danger">已停用</Chip>
                          </div>
                        ) : null}
                      </td>
                      <td>{account.full_name || <span className="dim">—</span>}</td>
                      <td className="mono" style={{ fontSize: 12 }}>
                        {account.email}
                      </td>
                      <td>
                        <Chip tone={account.admin_role === 'SUPER_ADMIN' ? 'brand' : 'neutral'}>
                          {roleLabel(account.admin_role)}
                        </Chip>
                      </td>
                      <td>
                        {account.totp_enrolled ? (
                          <Chip tone="ok">已綁定</Chip>
                        ) : (
                          <Chip tone="warn">待綁定</Chip>
                        )}
                      </td>
                      <td>
                        {account.last_login_at ? formatTime(account.last_login_at) : <span className="dim">從未登入</span>}
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
                                      ? '不能移除最後一位超級管理員'
                                      : `改為${roleLabel(next)}`
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
                            重設密碼
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
    </>
  );
}

function CreateAccountBody({
  onChange,
}: {
  onChange: (patch: { username?: string; email?: string; password?: string; fullName?: string; role?: string }) => void;
}) {
  return (
    <div className="stack">
      <div className="field">
        <label className="field__label" htmlFor="acc-username">
          帳號
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
          電郵
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
          姓名（選填）
        </label>
        <input
          id="acc-name"
          type="text"
          onChange={(event) => onChange({ fullName: event.target.value })}
        />
      </div>
      <div className="field">
        <label className="field__label" htmlFor="acc-password">
          初始密碼
        </label>
        <input
          id="acc-password"
          type="password"
          autoComplete="new-password"
          onChange={(event) => onChange({ password: event.target.value })}
        />
        <div className="t-footnote dim">
          密碼政策由伺服器強制（長度、空白、可預測性）。
        </div>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="acc-role">
          權限
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
        <div className="t-footnote dim">
          新帳戶首次登入時必須綁定驗證器，綁定之前無法登入。
        </div>
      </div>
    </div>
  );
}
