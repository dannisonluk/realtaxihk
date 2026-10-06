# Fresh Audit 2026-10-06

Source-only audits produced by three independent subagents on 2026-10-06. The
reports were written from the working tree, not from prior audit files.

- [Backend audit](fresh-audit-2026-10-06/backend.md) — auth/session, security,
  contract/API, async correctness, stale comments, test gaps.
- [Mobile audit](fresh-audit-2026-10-06/mobile.md) — model contract drift,
  auth/session, realtime/location, a11y/theme/i18n, stale code, test gaps.
- [Admin console audit](fresh-audit-2026-10-06/admin-web.md) — async, error
  states, a11y, i18n, list/table correctness, state hooks, XSS, dead code.

## Actions taken from this audit round

| Area | Fix | Commit |
| --- | --- | --- |
| Backend | `client_ip.py` single-proxy XFF off-by-one: exact-hop case now uses `X-Real-IP`, preserving SEC-07 rightmost-hop trust | `034ab60` |
| Backend | Session/refresh rows committed before tokens/cookies/Redis epoch are issued | `1da4465` |
| Mobile | Transient refresh failures no longer clear tokens or fire `sessionExpired` | `844d4d0` |
| Admin | KYC/Analytics loading gating; safe licence doc URLs; detail retries; pagination disabled while loading; `scope="col"` on tables; locale parity | `4d4f6e3` |

## Remaining decisions (not code defects)

- Backend revocation remains fail-open while Redis is unavailable; documented
  tradeoff rather than a crash-all path.
- Mobile full i18n refactor and admin per-request `AbortController` were noted
  by the auditors as larger follow-ups, not blocking defects.