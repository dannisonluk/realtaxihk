/**
 * The admin console shell.
 *
 * Hash routing, because the console is served as static files with no rewrite
 * rule: a deep link to `/fleets/<id>` would 404 before the app loaded, whereas
 * `#/fleets/<id>` is resolved entirely client-side.
 *
 * Boot sequence, and why it is this order:
 *  1. If there is no stored session, render login.
 *  2. If there is one, re-validate it against `GET /auth/me` **before** rendering
 *     anything. The cached user object is not trusted: an admin whose role was
 *     revoked server-side would otherwise see a console that 403s on every
 *     request. `require_admin` re-reads the live user row, so the token alone is
 *     not proof of anything.
 *  3. Only then build the shell.
 */

import { api, ApiClient } from './api.js';
import { el, mount, toast } from './dom.js';
import { session } from './session.js';
import { DashboardView } from './views/dashboard.js';
import { DriverDetailView } from './views/driverDetail.js';
import { FleetDetailView } from './views/fleetDetail.js';
import { FleetsView } from './views/fleets.js';
import { KycView } from './views/kyc.js';
import { LoginView } from './views/login.js';
import { RefundsView } from './views/refunds.js';
import { SettlementView } from './views/settlement.js';

const NAV = [
  { path: '/', label: '總覽', badge: null },
  { path: '/kyc', label: '司機審核', badge: 'pendingKyc' },
  { path: '/refunds', label: '退款', badge: 'pendingRefunds' },
  { path: '/settlement', label: '每週結算', badge: null },
  { path: '/fleets', label: '車隊', badge: null },
];

const root = document.getElementById('app');

const client = new ApiClient({
  onSessionExpired: () => {
    session.clear();
    toast('登入已失效，請重新登入。', 'error');
    renderLogin();
  },
});

/** Counts for the sidebar badges. Best-effort: a failure must not break the shell. */
const badges = { pendingKyc: null, pendingRefunds: null };

/** Apply badge counts handed over by a view that already fetched them. */
function setBadges(next) {
  if (next.pendingKyc !== undefined) badges.pendingKyc = next.pendingKyc;
  if (next.pendingRefunds !== undefined) badges.pendingRefunds = next.pendingRefunds;
  renderNavCounts();
}

async function refreshBadges() {
  try {
    const [kyc, refunds] = await Promise.all([
      api.drivers.list(client, { status: 'PENDING_KYC', limit: 1 }),
      api.refunds.list(client, { status: 'PENDING', limit: 1 }),
    ]);
    badges.pendingKyc = kyc.total ?? 0;
    badges.pendingRefunds = refunds.total ?? 0;
    renderNavCounts();
  } catch {
    // Leave the previous numbers; the pages themselves report real errors.
  }
}

// ---------------------------------------------------------------------- login

function renderLogin() {
  mount(
    root,
    LoginView({
      client,
      onSignedIn: () => {
        navigate(currentPath(), { replace: true });
        boot();
      },
    }),
  );
}

// ---------------------------------------------------------------------- shell

function currentPath() {
  const hash = window.location.hash.replace(/^#/, '');
  return hash === '' ? '/' : hash;
}

function navigate(path, { replace = false } = {}) {
  const target = `#${path}`;
  if (window.location.hash === target) {
    renderRoute();
    return;
  }
  if (replace) {
    window.history.replaceState(null, '', target);
    renderRoute();
  } else {
    window.location.hash = path;
  }
}

function navLink(item) {
  const active = currentPath() === item.path || (item.path !== '/' && currentPath().startsWith(item.path));
  const count = item.badge ? badges[item.badge] : null;

  return el(
    'a',
    {
      class: 'navlink',
      href: `#${item.path}`,
      'aria-current': active ? 'page' : null,
      dataset: { nav: item.path },
    },
    [
      el('span', { text: item.label }),
      count ? el('span', { class: 'navlink__count', text: String(count) }) : null,
    ],
  );
}

function renderNavCounts() {
  for (const item of NAV) {
    const link = document.querySelector(`[data-nav="${item.path}"]`);
    if (!link) continue;
    const existing = link.querySelector('.navlink__count');
    const count = item.badge ? badges[item.badge] : null;
    if (!count) {
      existing?.remove();
      continue;
    }
    if (existing) {
      existing.textContent = String(count);
    } else {
      link.append(el('span', { class: 'navlink__count', text: String(count) }));
    }
  }
}

function renderShell(children) {
  const user = session.user;

  mount(root, [
    el('div', { class: 'shell' }, [
      el('nav', { class: 'sidebar', 'aria-label': '主選單' }, [
        el('div', { class: 'brand' }, [
          el('div', { class: 'brand__mark', text: 'R' }),
          el('div', { class: 'brand__text' }, [
            el('div', { class: 'brand__title', text: 'hkfastdc' }),
            el('div', { class: 'brand__sub', text: '管理後台' }),
          ]),
        ]),
        NAV.map(navLink),
        el('div', { class: 'sidebar__foot' }, [
          el('div', { text: user?.phone_masked || '—' }),
          el('button', {
            type: 'button',
            class: 'btn btn--sm',
            text: '登出',
            onClick: signOut,
          }),
        ]),
      ]),
      el('main', { class: 'main' }, children),
    ]),
  ]);
}

async function signOut() {
  try {
    // Revokes every access and refresh token for this account, so signing out on
    // a shared machine is real rather than cosmetic.
    await api.auth.logout(client);
  } catch {
    // Even if the server call fails, drop the local session.
  }
  session.clear();
  toast('已登出。');
  renderLogin();
}

/** A page heading, so every route has the same shape. */
function pageHead(title, subtitle, actions) {
  return el('div', { class: 'page-head' }, [
    el('div', { class: 'page-head__text' }, [
      el('h1', { text: title }),
      subtitle ? el('p', { class: 'page-head__sub', text: subtitle }) : null,
    ]),
    actions?.length ? el('div', { class: 'actions' }, actions) : null,
  ]);
}

// ---------------------------------------------------------------------- router

const ROUTES = [
  {
    pattern: /^\/$/,
    render: async (ctx) => [pageHead('總覽', '平台即時狀況。'), await DashboardView({ ...ctx, setBadges })],
  },
  {
    // The driver detail page. Declared before `/kyc` is irrelevant — the two
    // patterns are disjoint — but it must come before any route that would
    // greedily swallow the `drivers/...` prefix.
    pattern: /^\/drivers\/([^/]+)$/,
    render: async (ctx, match) => [
      await DriverDetailView({ ...ctx, driverId: decodeURIComponent(match[1]) }),
    ],
  },
  {
    pattern: /^\/kyc$/,
    render: async (ctx) => [
      pageHead(
        '司機審核',
        '通過審核後司機進入「待繳按金」；存入按金達標才會正式啟用接單。',
      ),
      await KycView(ctx),
    ],
  },
  {
    pattern: /^\/refunds$/,
    render: async (ctx) => [
      pageHead(
        '退款申請',
        '申請會凍結司機全部按金並暫停帳戶；批准是唯一會實際付款的操作，並會終止該帳戶。',
      ),
      await RefundsView(ctx),
    ],
  },
  {
    pattern: /^\/settlement$/,
    render: async (ctx) => [
      pageHead('每週結算', '平台劃一服務費的手動執行槓桿。'),
      SettlementView(ctx),
    ],
  },
  {
    pattern: /^\/fleets\/([^/]+)$/,
    render: async (ctx, match) => [
      await FleetDetailView({ ...ctx, fleetId: decodeURIComponent(match[1]) }),
    ],
  },
  {
    pattern: /^\/fleets$/,
    render: async (ctx) => [
      pageHead('車隊', '的士車隊為持牌營運商，由平台開設，成員按車隊折扣價收費。'),
      await FleetsView(ctx),
    ],
  },
];

let renderToken = 0;

async function renderRoute() {
  const path = currentPath();
  const token = ++renderToken;

  const ctx = { client, navigate, refreshBadges };
  const route = ROUTES.find((candidate) => candidate.pattern.test(path));

  if (!route) {
    renderShell([
      pageHead('找不到頁面', null, [
        el('button', { type: 'button', text: '返回總覽', onClick: () => navigate('/') }),
      ]),
      el('div', { class: 'card' }, [
        el('div', { class: 'empty', text: `沒有對應 ${path} 的頁面。` }),
      ]),
    ]);
    return;
  }

  const match = route.pattern.exec(path);
  try {
    const children = await route.render(ctx, match);
    // A slow response must not overwrite a newer navigation.
    if (token !== renderToken) return;
    renderShell(children);
    window.scrollTo(0, 0);
  } catch (error) {
    if (token !== renderToken) return;
    renderShell([
      el('div', { class: 'error' }, [
        el('div', { text: error?.message || String(error) }),
      ]),
    ]);
  }
}

// ------------------------------------------------------------------------ boot

async function boot() {
  if (!session.isSignedIn) {
    renderLogin();
    return;
  }

  try {
    const me = await api.auth.me(client);
    if (me?.role !== 'ADMIN') {
      session.clear();
      renderLogin();
      return;
    }
    session.save({
      accessToken: session.accessToken,
      refreshToken: session.refreshToken,
      user: me,
    });
  } catch (error) {
    // A 401 already cleared the session in the interceptor. Anything else
    // (offline, 5xx) is not proof the session is bad, so show the login screen
    // with the reason rather than silently discarding a usable token.
    if (session.isSignedIn) {
      toast(error?.message || '無法驗證登入狀態。', 'error');
    }
    renderLogin();
    return;
  }

  window.addEventListener('hashchange', renderRoute);
  await renderRoute();
  // The dashboard route fills both counters from the fetches it already makes
  // (see `DashboardView`'s `setBadges`), so asking again would spend two more
  // parallel requests for numbers already on screen. Other routes render no
  // counts, so they still need this.
  if (badges.pendingKyc === null || badges.pendingRefunds === null) {
    refreshBadges();
  }
}

boot();
