/**
 * The console shell: sidebar, navigation, page chrome.
 *
 * `NavLink` from react-router handles the active state, which removes the
 * hand-rolled `currentPath()` comparison the vanilla build needed.
 */

import { NavLink as RouterNavLink, Outlet } from 'react-router-dom';
import { useApp } from './AppContext';

const NAV = [
  { path: '/', label: '總覽', badge: null, end: true },
  { path: '/kyc', label: '司機審核', badge: 'pendingKyc' as const, end: false },
  { path: '/refunds', label: '退款', badge: 'pendingRefunds' as const, end: false },
  { path: '/settlement', label: '每週結算', badge: null, end: false },
  { path: '/fleets', label: '車隊', badge: null, end: false },
];

export function Shell() {
  const { user, badges, signOut } = useApp();

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

        {NAV.map((item) => {
          const count = item.badge ? badges[item.badge] : null;
          return (
            <RouterNavLink key={item.path} to={item.path} end={item.end} className="navlink">
              <span>{item.label}</span>
              {count ? <span className="navlink__count">{count}</span> : null}
            </RouterNavLink>
          );
        })}

        <div className="sidebar__foot">
          <div>{user?.phone_masked ?? '—'}</div>
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

/** A page heading, so every route has the same shape. */
export function PageHead({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
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
