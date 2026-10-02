# realtaxihk.com Backend

Hong Kong taxi matching platform — **information intermediary** (Cap. 374D compliant).
FastAPI (async) + PostgreSQL 16/PostGIS + Redis 7 + Alembic, SQLAlchemy 2.0 async.

Money math is exact (`Decimal`, never float); every fare response carries bilingual
Cap. 374D disclaimers; every estimate embeds a `tariff_version` so historical orders
stay auditable.

**Status: production-hardened.** 887 backend tests green (+ 93 mobile, 54 contract
fixtures, browser UI verifier PASS). Start with
[`docs/WORK_SUMMARY.md`](docs/WORK_SUMMARY.md) for the whole picture — what's built,
what's verified, and what still needs credentials or a deployment target.
Full audit + fix log: [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md)
(4 bugs, 7 P0, 10 P1, 10 P2 — all closed). Lint gate + cleanup log:
[`docs/LINTING.md`](docs/LINTING.md). Security: [`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md).

## Quick start

```bash
# 1. infra (PostGIS :15433, Redis :16379 — ports avoid sibling projects)
docker compose up -d db redis

# 2. app (uv manages venv + deps; --all-extras pulls dev tools: pytest, ruff)
uv sync --all-extras
cp .env.example .env          # adjust if needed; see Configuration below

# 3. schema
.venv/Scripts/python -m alembic upgrade head

# 4. run + verify
.venv/Scripts/python scripts/dev/serve_and_probe.py   # detached uvicorn + health wait
.venv/Scripts/python scripts/verify/verify_api.py        # one-shot API smoke
.venv/Scripts/python -m pytest -q                 # 120 tests
uv run ruff check . && uv run ruff format --check .
```

On Linux/macOS use `.venv/bin/python` instead of `.venv/Scripts/python`.

## Layout

```
app/
  api/            # HTTP layer — auth, drivers, fare, orders, tracking, trips, admin,
                  #   fleets, ws
  core/           # config (fail-fast prod validator), db, deps (DB-backed guards),
                  #   exceptions, logging (JSON + request-ID), money, masking,
                  #   rate_limit (Redis fixed-window), security (JWT)
  models/         # SQLAlchemy 2.0 declarative — users, drivers, deposits, orders,
                  #   ledger (append-only), otp_codes, refresh_tokens,
                  #   fleets + fleet_memberships + fleet_settlement_runs
  services/       # domain logic — fare_calculator (tariff-versioned, TDD'd),
                  #   order/grab (SETNX + Lua release), ledger (row-locked),
                  #   otp, geo dispatch, trip hub (Redis Pub/Sub), maintenance jobs,
                  #   refresh (rotating tokens), notify (WhatsApp Cloud API, fail-closed),
                  #   fleet + settlement (the roster is the billing boundary)
alembic/          # async migrations (postgis tables filtered via include_object)
scripts/          # tooling, grouped by what you are doing (see scripts/README.md)
  ops/            #   operate a real environment — db_backup, create_admin,
                  #   create_admin_account, enrol_admin_totp
  verify/         #   produce a pass/fail verdict — audit_response_models, live_smoke,
                  #   security_probe + security_verify, prod_boot_drill, verify_api,
                  #   bench_location_pipeline, the three connection probes
  dev/            #   local workflow glue — serve_and_probe, serve_and_run_browser,
                  #   api_supervisor, stop_server, run_against_api, gen_mobile_fixtures
                  #   (pins the mobile wire format from the real API)
tests/            # pytest — unit + module + WS streaming + hardening regression
mobile/           # Flutter client (Android first) — driver, passenger and admin surfaces
admin-web/        # zero-build ES-module console for the management and admin teams
docs/             # WORK_SUMMARY.md (overview), PRODUCTION_READINESS.md (audit),
                  #   SECURITY_AUDIT.md (SEC-01..31), LINTING.md, PROJECT_UNDERSTANDING.md
```

## API

All routes under `/api/v1` unless noted. Auth = `Authorization: Bearer <access JWT>`.

| Area | Endpoints |
|---|---|
| **Auth** | `POST /auth/otp/request` · `POST /auth/otp/verify` · `POST /auth/refresh` · `POST /auth/logout` · `GET /auth/me` |
| **Drivers** | `POST /drivers/register` · `GET /drivers/me` · `GET /drivers/me/ledger` |
| **Fare** | `POST /fare/estimate` |
| **Orders** | `POST /orders` · `GET /orders/nearby` · `GET /orders` · `GET /orders/{id}` · `POST /orders/{id}/grab` · `.../arrive` · `.../start` · `.../complete` · `.../cancel` |
| **Driver GPS** | `POST /drivers/location` |
| **Trips** | `GET /trips/{order_id}/location` (REST snapshot for WS reconnects) |
| **Admin — KYC** | `GET /admin/drivers` · `POST /admin/drivers/{id}/review` · `POST /admin/drivers/{id}/deposit/grant` · `POST /admin/drivers/{id}/deposit/adjust` |
| **Admin — refunds** | `GET /admin/refunds` · `POST /admin/refunds/{id}/decision` |
| **Admin — settlement** | `POST /admin/settlement/weekly/run` (idempotent per ISO week) |
| **Admin — fleets** | `GET /admin/fleets` · `POST /admin/fleets` · `PATCH /admin/fleets/{id}` · `GET|POST /admin/fleets/{id}/members` · `DELETE /admin/fleets/{id}/members/{driver_id}` · `GET /admin/fleets/{id}/settlement` · `POST /admin/fleets/{id}/settlement/run` |
| **Fleets (driver)** | `GET /fleets/me` · `GET /fleets/{id}` · `GET /fleets/{id}/members` · `GET /fleets/{id}/settlement` |
| **Live** | `WS /ws/trip/{order_id}?token=<access JWT>` |
| **Ops** | `GET /health` (pings DB + Redis, 503 on failure) · `GET /metrics` (when `PROMETHEUS_ENABLED`) |

Live socket close codes: `4401` unauthenticated, `4403` forbidden, `4404` unknown order.

## Modules & domain rules

- **A — Auth & KYC**: WhatsApp OTP (sha256-hashed, TTL, resend cooldown, 5-attempt cap,
  per-IP + global rate limits), JWT HS256 with rotating refresh tokens (hashed at rest,
  single-use). Driver lifecycle `PENDING_KYC → DEPOSIT_REQUIRED → ACTIVE → SUSPENDED/TERMINATED`.
  Deactivation takes effect immediately: every request re-checks the DB (`require_active_user`),
  not just the JWT.
- **B — Orders & dispatch**: fare snapshot frozen into `fare_json` at creation;
  Redis `GEOSEARCH` nearby broadcast; `SETNX` + Lua-release lock for atomic grab
  (exactly-once, 6-way concurrency tested); lifecycle `BROADCASTING → ACCEPTED →
  DRIVER_ARRIVED → IN_TRIP → COMPLETED/CANCELLED`; $50 no-show penalty (negative
  ledger = arrears). Order creation is rate-limited (5/60s per passenger → 429).
- **C-mini — Ledger**: append-only `ledger_entries` with `balance_after` chain,
  `with_for_update` row locks + reference-idempotency index; HKD 500 deposit grant
  gates activation. Five entry types, each with its own reference namespace so
  they cannot collide (`SEC-13`): `DEPOSIT_TOPUP` (`grant:`), `WEEKLY_FEE_DEDUCTION`
  (`weekly:`/`fleet:`), `PENALTY_DEDUCTION`, `REFUND` (`refund:`), and
  `ADJUSTMENT` (`adj:`) — an operator's signed manual correction (±HK$5,000,
  reason required, attributed via `created_by`), for when the books need fixing
  for something no automated flow covers.
- **D — Live tracking**: WS channel above (passenger subscribes, assigned driver pushes,
  driver ACTIVE re-checked per tick); ticks persist to PostGIS and fan out via Redis
  Pub/Sub (`realtaxi:trip:{order_id}`); server pings every `WS_HEARTBEAT_S`.
- **E — Fleets & settlement**: a fleet is a **licensed operator** — the Transport
  Department grants the licence — so it is created by an admin and never
  self-service; drivers join via a roster. The roster is the billing boundary: an
  ACTIVE member leaves the platform-wide weekly run and is charged the fleet's
  discounted rate instead. A partial unique index keeps a driver on at most one
  ACTIVE roster, so a second add is a 409 rather than a silent double-bill.
  Settlement is idempotent per (fleet, ISO week) and writes a `fleet:` ledger
  reference, while the platform run writes `weekly:` — so neither idempotency
  check protects a driver across the two. The platform run therefore excludes
  rostered drivers and reports the count as `fleet_managed`.
- **Background jobs**: geo ghost-order sweeper (auto-cancels stale `BROADCASTING` after
  `MAX_BROADCAST_MINUTES`) + PDPO retention purges; both run in lifespan tasks and are
  cancelled cleanly on shutdown.

## Fare engine (verified sources)

- Meter tariffs effective **2024-07-14** (TD press release; Cap. 374D schedule):
  Urban $29 flagfall → $2.1/200m (to $102.5) → $1.4; NT $25.5 → $1.9 (to $82.5) → $1.4;
  Lantau $24 → $1.9 (to $195) → $1.6. Waiting charged per minute or part.
- Tolls: cross-harbour $25 (+$25 return fee, waived at cross-harbour stands / same-side
  destination), Tai Lam $28, Tates Cairn $20, Lion Rock / Eagle's Nest / Shing Mun /
  Aberdeen $8, Lantau Link $30. Surcharge codes: `tunnel_cross_harbour`,
  `cross_harbour_return`.
- Extras: baggage $6, animal $5, advance booking $5; discounts apply to the meter only.

`tariff_version` = `meter:2024-07-14;tolls:2025-09-21`.

## Configuration

`.env.example` documents every knob. The config layer is **fail-closed**:

- `APP_ENV` has **no default** and is whitelisted (`dev` | `test` | `prod`). Unset, or
  a value like `production`/`PROD`, is a startup error — the old `app_env = "dev"`
  default turned any host without a `.env` into a dev-mode server that returned a fixed
  OTP code in the response body.
- `JWT_SECRET_KEY` has no default and must carry real entropy (≥ 32 characters and
  ≥ 8 distinct characters — `"x" * 64` is rejected). Generate with `openssl rand -hex 32`.
- With `APP_ENV=prod` the app **refuses to boot** if the JWT secret or
  `POSTGRES_PASSWORD` still hold dev defaults, or if `ALLOW_DEV_OTP` is set.
- The deterministic dev OTP (`123456`, echoed in the response) needs **both** a non-prod
  env and an explicit `ALLOW_DEV_OTP=true`. It is never available in prod.
- `TRUSTED_PROXY_COUNT` controls how `X-Forwarded-For` is read (`0` = ignore it
  entirely, `1` = trust one nginx hop). A client-supplied prefix can never set its own
  source address.
- `METRICS_TOKEN` must be set for `/metrics` to be mounted at all.

(`scripts/verify/prod_boot_drill.py` verifies the prod rails.)

Key groups: DB/Redis connection, JWT + token lifetimes, OTP limits, request-body and
WebSocket caps, background-job intervals + retention windows, security headers,
external providers (WhatsApp / FCM / Google Maps — providers fail closed when
unconfigured), Sentry/Prometheus (optional).

## Tests & CI

```bash
.venv/Scripts/python -m pytest -q        # needs db+redis containers up
```

- 887 tests over 32 files: fare unit tests, per-module API tests, WS streaming,
  fleet management / roster / settlement, backup retention and restore-drill
  guards, and `test_hardening.py` (14 regression tests for every fixed finding).
- Per-test isolated Postgres databases (template clone) — no cross-test state.
- CI (`.github/workflows/ci.yml`): ruff check → format check → full pytest, with
  PostGIS + Redis service containers.

Both clients are verified against the **real** API rather than mocks:

```bash
.venv/Scripts/python scripts/dev/gen_mobile_fixtures.py    # capture the real wire format
.venv/Scripts/python mobile/tool/dart_check.py mobile  # analyzer, over LSP
# `playwright` is not a top-level package here: it is nested under the Playwright
# CLI. The path is version-specific — substitute the one under
# `~/.workbuddy-ai/binaries/node/versions/<ver>/node_modules/@playwright/cli/node_modules`.
NODE_PATH="$HOME/.workbuddy-ai/binaries/node/versions/22.22.2-3/node_modules/@playwright/cli/node_modules" \
  node admin-web/tool/verify_ui.mjs --base http://127.0.0.1:8081 \
    --username ops-admin --password "$ADMIN_PASSWORD" --totp-secret "$ADMIN_TOTP_SECRET"
```

`mobile/tool/verify_contract.dart` decodes all 54 fixtures with the real Dart
models, `mobile/tool/run_tests.dart` runs the unit assertions, and the admin
console is driven in a real browser. `dart analyze` / `flutter test` cannot spawn
piped subprocesses on Windows (`ERROR_PIPE_BUSY` 231), which is why the analyzer
is driven over LSP by a Python harness. See `mobile/README.md` and
`admin-web/README.md`.

## Docker / deploy

`docker compose up -d` runs the full stack: `db` (PostGIS 16-3.4), `redis` (AOF on),
`api` (auto-runs `alembic upgrade head` before serving, `restart: unless-stopped`).

## Ops quick reference

```bash
.venv/Scripts/python scripts/verify/live_smoke.py       # 9-check end-to-end (needs server up)
.venv/Scripts/python scripts/dev/stop_server.py      # stop detached uvicorn
.venv/Scripts/python scripts/verify/prod_boot_drill.py  # verify prod fail-fast guard
.venv/Scripts/python scripts/verify/security_verify.py  # re-run every audit finding against a live server
.venv/Scripts/python scripts/verify/security_probe.py all   # the original attack probe (boots its own server)
```

Both security scripts boot their own uvicorn on :8000, so stop anything already
listening there first. They only create throwaway users/orders. See
`docs/SECURITY_AUDIT.md` for what each check corresponds to.

### Backups (`scripts/ops/db_backup.py`)

The ledger is the financial record, so a dump nobody has restored is not a
backup. `verify` is the part that matters: it restores the newest archive into
a scratch database and compares exact row counts, table by table.

```bash
# the nightly run
.venv/Scripts/python scripts/ops/db_backup.py backup

# the drill — restores, compares, drops the scratch db
.venv/Scripts/python scripts/ops/db_backup.py verify

# what is on disk, and what retention would prune
.venv/Scripts/python scripts/ops/db_backup.py list
```

Off-host copying is deliberately the operator's command, not a hard-coded
provider — the deploy target is not decided yet, and a backup script that
assumes S3 is a backup script that breaks on the next host:

```bash
.venv/Scripts/python scripts/ops/db_backup.py backup \
  --upload-cmd        'rclone copy {file} remote:realtaxi-backups/' \
  --upload-verify-cmd 'rclone lsf remote:realtaxi-backups/'
```

`--upload-verify-cmd` is worth setting. Without it, "upload ok" only means the
command exited 0 — `true` passes. With it, the remote is actually asked whether
the archive is there.

Cron line (note it does **not** need the API running):

```
17 3 * * *  cd /srv/realtaxihk && .venv/bin/python scripts/ops/db_backup.py backup
```

Retention defaults to 7 daily + 4 weekly, counted by **calendar distance**, so
the weekly tier survives a problem that takes days to notice. **Redis is not
backed up on purpose** — rate-limit counters, grab locks and Pub/Sub are all
ephemeral; the database is the truth. `--via auto` uses local `pg_*` tools when
present and falls back to `docker exec realtaxi-db` when they are not, which is
the case on this machine.

## Roadmap (next)

1. Weekly settlement job (ledger ready; cron + service-fee entries)
2. FCM push provider (WhatsApp already wired); Google Maps distance integration
3. Nginx TLS + CORS lockdown + pg_dump backup cron (pre-launch checklist)
