# Fresh Backend Audit — realtaxihk (source/working tree)

> Resolution Log 2026-10-07: F2–F4 and F6–F7 are fixed and covered by the full pytest run (`1285 passed`). F1 (Redis fail-open) remains an explicit production-design decision tracked in `docs/archive/AUDIT_2026-10-06.md`.

Level: source-only audit of `app/`, Alembic migrations, `pyproject.toml`, `Dockerfile`, `tests/`.
Scope: auth, sessions, DB lifecycle, security, API contracts, stale comments, and observable test gaps.
Method: findings were produced from the working tree source, not from prior audit reports.

## Executive Summary

1. The backend is well-engineered around authentication: hashed single-use refresh rows, row-locked rotation with replay detection, committed-before-send reset/OTP issuance, prod-fail-closed secret and CORS validation, and a central exception envelope.
2. The five supplied leads do not reproduce as suspected in this source: XFF is chosen from the trusted hop count and protected by tests, the admin cookie intentionally uses a non-`__Host-` path, email/WhatsApp sends happen after explicit commits, and `PROMETHEUS_ENABLED`/`METRICS_TOKEN` are declared by `Settings`.
3. The most material risk is fail-open access-token revocation: a Redis outage silently accepts a revoked token for its 15-minute lifetime rather than failing closed.
4. Session issuance and revocation ordering is the main async/session weakness: login/refresh return tokens or set cookies while the DB rows are only flushed, and logout/password flows can update the Redis epoch before the refresh-row DB commit is final.
5. Contract and test gaps are narrow but real: two public authenticated service-area routes have no `response_model`, one comment claims a purge job is “later” although the job exists, and no observed test exercises Redis outage or forced commit failure on the revocation/issuance paths.

## Findings

| ID | Severity | Title | file:line | Evidence | Concrete fix |
|---|---|---|---|---|---|
| F1 | Medium | Access-token revocation fails open when Redis is unavailable | `app/core/token_revocation.py:14-19`, `app/core/token_revocation.py:54-59` | The module docstring states Redis outages “fail open”; `is_token_revoked()` catches every exception and returns `False` (not revoked). | For auth-critical paths, separate “not revoked” from “revocation state unknown”: return a tri-state or raise a 503 on Redis failure. At minimum record a Redis-down metric and reject high-value admin routes until the epoch read succeeds. |
| F2 | Medium | Session issuance returns tokens/cookies before DB commit | `app/api/auth.py:148-170`, `app/services/auth/refresh_service.py:57-69`, `app/services/admin/admin_refresh_service.py:149-169`, `app/api/admin_auth.py:345-352`, `app/core/db.py:63-72` | `_issue_session()` calls `RefreshService.issue()`, which only flushes the new row; admin `_issue_session` does the same and `set_session_cookies()` immediately. The shared dependency commits after the handler returns. A final commit failure can hand out a refresh token/cookie that was never persisted. | Explicitly `session.commit()` in the session-issuing service/route before constructing the response body or setting cookies; make `get_session` only an idempotent/rollback safety net. |
| F3 | Medium | Redis revocation can run before refresh-row DB commit on logout/password change/reset | `app/api/auth.py:427-437`, `app/services/auth/password_service.py:148-153`, `app/services/auth/password_service.py:250-260`, `app/services/auth/password_service.py:266-278`, `app/services/auth/refresh_service.py:122-129`, `app/core/db.py:69` | `revoke_all_for_user()` only flushes; the caller then writes the Redis epoch and returns success. If the dependency commit later fails, access tokens are dead but refresh rows are still live. | Commit the refresh-row revocation first, then write the Redis epoch (and log/alert if the epoch write fails). Do not treat Redis and DB work as one best-effort transaction. |
| F4 | Low | `get_session` performs a second commit even after a service already committed | `app/core/db.py:63-72`, `app/services/auth/password_service.py:185-191`, `app/services/auth/otp_service.py:160-165`, `app/services/auth/identity_service.py:203-215` | `get_session` always `await session.commit()` after a successful handler; several services intentionally commit before returning. The dependency commit repeats on every request and makes transaction ownership ambiguous. | Track whether the handler already committed (or make services the sole committers), and have the dependency only `rollback` on exception and close. |
| F5 | Low | `require_live_admin_refresh_session` is a declared no-op guard | `app/core/deps.py:194-218`, `app/api/admin_auth.py:291-312` | The dependency literally returns `None`; the real cookie/liveness work is performed inside handlers. The comment explains the design for route-table audits, but the dependency does not authenticate anything by itself. | Replace the no-op with a dependency that at least reads and returns the refresh cookie (and optionally a live `AdminAccount` check); have handlers still do the locked rotate in the same transaction, but ensure the guard has real behavior for future cookie-authenticated routes. |
| F6 | Low | Public authenticated routes return untyped JSON | `app/api/service_area_route.py:26`, `app/api/service_area_route.py:58` | `GET /api/v1/service-area/check` and `/bounds` have no `response_model`, so OpenAPI documents them as unrestricted JSON even though the handlers return concrete dicts. | Add Pydantic response models (e.g. `ServiceAreaCheckOut`, `ServiceAreaBoundsOut`) and set `response_model` on both routes. |
| F7 | Low | “Purge job later” comment is stale; purge job already exists | `app/services/auth/otp_service.py:24`, `app/main.py:87-91`, `app/services/infra/maintenance.py:122-167` | The OTP module docstring says stale rows need a “purge job later”; `main.py` starts `pdpo_purge`, and `MaintenanceService.purge_expired_rows()` deletes `OtpCode` rows. | Update the module comment to point at the running purge job/settings, or remove the stale sentence. |

## What Is Already Well Done

- Password reset/OTP/verification email services commit before sending the external message and delete + commit the token again on send failure (`app/services/auth/password_service.py:185-211`, `app/services/auth/otp_service.py:154-166`, `app/services/auth/identity_service.py:203-215`).
- Refresh tokens are random, stored as SHA-256 digests, rotated with `FOR UPDATE`, and replay detection revokes the family before raising (`app/services/auth/refresh_service.py:71-102`, `app/api/auth.py:394-407`; admin equivalent at `app/services/admin/admin_refresh_service.py:196-228`).
- JWT secret is mandatory and fails closed in prod with dev-secret and entropy checks (`app/core/security.py:1-33`, `app/core/config.py:112-115`, `app/core/config.py:320-425`).
- Prod CORS rejects empty/wildcard/default origins and uses explicit allow-lists with credentials (`app/main.py:336-342`, `app/core/config.py:366-389`).
- Error responses are standardized, validation rejects echoed `input`, and the generic 500 handler hides internal exception text (`app/core/exceptions.py:79-96`, `app/core/exceptions.py:188-199`).
- Client IP trusts X-Forwarded-For only when a proxy chain is configured and selects the hop from the trusted edge; the tests explicitly cover rightmost-hop use and attacker-prefix fallback (`app/core/client_ip.py:42-55`, `tests/api/test_security_hardening.py:207-260`).

## Async/Session Correctness

`get_session` is a one-session-per-request dependency with `expire_on_commit=False`, `autoflush=False`, commit on success, rollback on exception, and the session closed through `async with` (`app/core/db.py:56-72`). Background jobs are created in named asyncio tasks and cancelled/gathered on shutdown (`app/main.py:57-127`), and Redis/DB resources are closed with deduplication (`app/main.py:130-155`). No publish-before-commit path was found in the audited source.

The ordering vulnerabilities are F2/F3/F4. F2 means successful login/refresh responses can be built against rows that have only been flushed; F3 means the Redis epoch can be written before the DB revocation commit; F4 makes explicit service commits coexist with an unconditional dependency commit. The supplied lead about emails/WhatsApp after flush is not present: the send paths explicitly commit first. OTP verify also does not send anything, so a “no-authenticated-token mismatch” from a send-after-flush bug did not reproduce.

## Auth/Security

- Token handling is sound: HS256, pinned algorithm in `decode_access_token`, `iat`/`exp`/`jti`, sub-namespaced principal, and an additional live DB role check (`app/core/security.py:16-33`, `app/core/deps.py:89-145`, `app/core/deps.py:221-254`).
- The main vulnerability is F1: every authenticated request calls fail-open `is_token_revoked()` (`app/core/deps.py:120-134`).
- Admin cookies intentionally use `Path=/api/v1/admin/auth` instead of `__Host-`; the source explains that `__Host-` requires `/`, so the non-root path is a deliberate compatibility choice and not the reported production break (`app/core/admin_cookies.py:24-28`, `app/core/admin_cookies.py:70-77`). Cookie names are stable by design (`app/core/admin_cookies.py:165-179`).
- Admin refresh is CSRF-protected via a readable cookie echoed as `X-CSRF-Token` and compared with `hmac.compare_digest` before rotation (`app/services/admin/admin_refresh_service.py:219-223`, `app/core/admin_cookies.py:33-38`, `app/core/admin_cookies.py:79-88`).
- Rate limiting uses Redis fix-window counters and a global OTP soft/hard cap that degrades rather than locks all users (`app/core/rate_limit.py:24-47`, `app/api/auth.py:299-336`). The fixed-window boundary permits up to two consecutive window budgets, which is documented.
- Error distinction is generally intentional: admin refresh collapses all failures to 401 (`app/api/admin_auth.py:302-335`), password reset returns one sentence for every failure (`app/services/auth/password_service.py:234-241`), and structured reasons are preserved in the envelope (`app/core/exceptions.py:148-162`). The one inconsistency is `require_active_user` answering 403 for a missing account while `require_live_principal` answers 404 (`app/core/deps.py:174-178`, `app/core/deps.py:234-236`).
- Secrets are fail-closed by config validation with no prod default (`app/core/config.py:320-425`); no hard-coded prod secret was observed.

## Contract/API Consistency

Most routes do declare `response_model`; auth session routes use `TokenPairOut`, admin auth uses `AdminSessionOut`, and `response_model_exclude_none=True` is used where `created` must be omitted (`app/api/auth.py:221`, `app/api/auth.py:386-424`). The exception envelope and 422 handler are centralized (`app/core/exceptions.py:79-102`). The main gap is F6: `service_area_route.py` has two endpoints without a response model. No duplicate or contradicting auth/admin response models were found in the routes inspected. `GET /api/v1/auth/me` intentionally uses a union of user/admin schemas (`app/api/auth.py:571`).

## Stale Code/Comments

- Stale: `app/services/auth/otp_service.py:24` says purge “later” while the purge job exists (`app/main.py:87-91`, `app/services/infra/maintenance.py:122-167`).
- Not stale: `app/core/admin_cookies.py:24-28` accurately explains why `__Host-` is not used.
- Not stale: `app/core/token_revocation.py:5` explains the old 120-minute risk and the current 15-minute `ACCESS_TOKEN_EXPIRE_MINUTES` (`app/core/config.py:115`).
- Not stale: the `_EPOCH_TTL_S` comment calls 30 days “enough”; the refresh token default is 14 days (`app/core/config.py:116`), so 30 days exceeds the current default and covers already-issued tokens.

## Observable Test Gaps

- No observed test simulates Redis being unavailable during `is_token_revoked()` / `_revoke_access_tokens()`. Existing tests cover the happy epoch path only (`tests/api/test_admin_accounts.py:622-653`, `tests/api/test_security_hardening.py:772`).
- No observed test forces a DB commit failure after login/refresh to verify the client is not given a flushed-only refresh token. The admin cookie tests assert normal rotation only (`tests/api/test_admin_session_cookie.py:365-372`).
- No observed service-area OpenAPI test checks that `/service-area/check` and `/bounds` expose a concrete response schema; behavior tests cover payloads only (`tests/api/test_service_area.py:195-235`).
- The XFF/proxy behavior is well covered (`tests/api/test_security_hardening.py:207-260`), so that risk does not need new tests.