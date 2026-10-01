/**
 * The console shell: sidebar, navigation, page chrome.
 *
 * `NavLink` from react-router handles the active state, which removes the
 * hand-rolled `currentPath()` comparison the vanilla build needed.
 *
 * The navigation is **role-filtered**, and the filter is a statement about
 * affordance rather than authority. Every admin route re-reads the live
 * `admin_accounts` row (`require_admin` / `require_role`), so a link that
 * survives this filter and should not have produces a 403, not access. What the
 * filter prevents is the opposite and more common harm: showing a SUPPORT
 * operator a "每週結算" button that cannot work, which trains them to ignore
 * refusals.
 *
 * A page is therefore hidden when the operator cannot *use* it, not merely when
 * they cannot see it. `/audit` is reachable by every role by design — the log
 * records what was done by whom, and restricting its reading would mean the
 * people least able to change anything are also the least able to notice that
 * something was changed.
 */

import { NavLink as RouterNavLink, Outlet } from 'react-router-dom';
import type { ReactNode } from 'react';
import { useApp } from './AppContext';
import type { AdminRole } from '../api/types';

interface NavItem {
  path: string;
  label: string;
  badge: 'pendingKyc' | 'pendingRefunds' | null;
  end: boolean;
  /** The minimum role that can do anything useful on the page. */
  role: AdminRole;
}

/**
 * The sidebar, in the order an operator needs it.
 *
 * The role on each row is the **lowest role that can act**, taken from the
 * server's own guards, not the lowest that can read. `/disputes` is SUPPORT-
 * visible because a reply is not gated; `/settlement` is FINANCE because both
 * the preview and the run are, and a preview one role can obtain while another
 * acts on it is a workflow nobody can complete.
 */
const NAV: NavItem[] = [
  { path: '/', label: '總覽', badge: null, end: true, role: 'SUPPORT' },
  // First, and deliberately so: this is the endpoint support hits while a
  // passenger is on the line, and every second spent hunting is a second the
  // caller is still waiting.
  { path: '/search', label: '搜尋', badge: null, end: false, role: 'SUPPORT' },
  { path: '/orders', label: '訂單', badge: null, end: false, role: 'SUPPORT' },
  { path: '/disputes', label: '爭議', badge: null, end: false, role: 'SUPPORT' },
  // Sits next to KYC because the two are the same job — checking a driver's
  // paperwork — split only because the licence is a recurring, document-shaped
  // event while KYC is a one-off profile review.
  { path: '/kyc', label: '司機審核', badge: 'pendingKyc', end: false, role: 'OPERATIONS' },
  { path: '/licences', label: '的士證審核', badge: null, end: false, role: 'OPERATIONS' },
  { path: '/refunds', label: '退款', badge: 'pendingRefunds', end: false, role: 'FINANCE' },
  { path: '/settlement', label: '每週結算', badge: null, end: false, role: 'FINANCE' },
  { path: '/fleets', label: '車隊', badge: null, end: false, role: 'OPERATIONS' },
  // Last of the working pages, because it is the one entry nobody needs during
  // a shift: the review queues are work to be cleared, while this is a question
  // you go and ask.
  { path: '/analytics', label: '表現分析', badge: null, end: false, role: 'OPERATIONS' },
  // Readable by everyone — see the file docstring.
  { path: '/audit', label: '審計紀錄', badge: null, end: false, role: 'SUPPORT' },
  // The route that decides who may do everything else. SUPER_ADMIN only, and
  // the server enforces it on all four endpoints.
  { path: '/accounts', label: '管理員帳戶', badge: null, end: false, role: 'SUPER_ADMIN' },
];

export function Shell() {
  const { user, role, hasRole, badges, signOut } = useApp();

  const visible = NAV.filter((item) => hasRole(item.role));

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="主選單">
        <div className="brand">
          <div className="brand__mark" aria-hidden="true">
            R
          </div>
          <div className="brand__text">
            <div className="brand__title">RealTaxi HK</div>
            <div className="brand__sub">管理後台</div>
          </div>
        </div>

        {visible.map((item) => {
          const count = item.badge ? badges[item.badge] : null;
          return (
            <RouterNavLink key={item.path} to={item.path} end={item.end} className="navlink">
              <span>{item.label}</span>
              {count ? <span className="navlink__count">{count}</span> : null}
            </RouterNavLink>
          );
        })}

        <div className="sidebar__foot">
          {/*
            Scope-aware, because `/auth/me` answers differently per token scope:
            an admin has a `username` and a masked email, a passenger has a
            masked phone. Reading only `phone_masked` — as this did — left every
            admin staring at an em dash where their account should be.
          */}
          <div>
            <div>{user?.username ?? user?.phone_masked ?? '—'}</div>
            {/*
              The role, spelled out. An operator who cannot see 管理員帳戶 needs
              to know *why* — "SUPPORT" is the answer, and a missing menu entry
              on its own reads as a broken console rather than a permission.
            */}
            <div className="dim t-caption1">{role ? ROLE_LABEL[role] ?? role : '—'}</div>
          </div>
          <button type="button" className="btn btn--sm" onClick={() => void signOut()}>
            登出
          </button>
        </div>
      </nav>

      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}

const ROLE_LABEL: Record<string, string> = {
  SUPPORT: '客戶支援',
  OPERATIONS: '營運',
  FINANCE: '財務',
  SUPER_ADMIN: '超級管理員',
};

export function roleLabel(role: string | null | undefined): string {
  if (!role) return '—';
  return ROLE_LABEL[role] ?? role;
}

/** A page heading, so every route has the same shape. */
export function PageHead({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div className="page-head__text">
        <h1>{title}</h1>
        {subtitle ? <p className="page-head__sub">{subtitle}</p> : null}
      </div>
      {actions ? <div className="actions">{actions}</div> : null}
    </div>
  );
}

/**
 * A route guard that explains itself.
 *
 * This is **not** a security control and must not be treated as one — the
 * server re-reads the live role on every request, and a direct hash edit walks
 * straight past this. What it prevents is rendering a page whose every request
 * will 403 and then showing the operator a raw error, which reads as a broken
 * console.
 *
 * It keys off `role` only. An unknown role ranks below SUPPORT, so a session
 * the console cannot classify sees nothing rather than everything — the same
 * default-deny direction the server uses.
 */
export function RequireRole({
  role: minimum,
  children,
}: {
  role: AdminRole;
  children: ReactNode;
}) {
  const { role, hasRole } = useApp();

  if (hasRole(minimum)) return <>{children}</>;

  return (
    <>
      <div className="page-head">
        <div className="page-head__text">
          <h1>沒有存取權限</h1>
        </div>
      </div>
      <div className="card">
        <div className="empty">
          <div className="empty__title">
            此頁面需要 {roleLabel(minimum)} 或以上權限。
          </div>
          <div>
            你目前的權限是 {role ? roleLabel(role) : '未知'}。如需要使用此功能，
            請聯絡超級管理員調整帳戶權限。
          </div>
        </div>
      </div>
    </>
  );
}
