# realtaxihk.com Backend

Hong Kong taxi matching platform — **information intermediary** (Cap. 374D compliant).
FastAPI (async) + PostgreSQL 16/PostGIS + Redis 7 + Alembic. Money math is exact
(`Decimal`, never float); all fare responses carry bilingual Cap. 374D disclaimers.

## Quick start

```bash
# 1. infra (PostGIS :15433, Redis :16379 — ports chosen to avoid sibling projects)
docker compose up -d db redis

# 2. app
uv venv && uv pip install -e ".[dev]"
cp .env.example .env  # adjust if needed

# 3. schema
.venv/Scripts/python -m alembic upgrade head

# 4. run + verify
.venv/Scripts/python scripts/serve_and_probe.py   # or: uvicorn app.main:app --port 8000
.venv/Scripts/python scripts/verify_api.py        # one-shot smoke test
.venv/Scripts/python -m pytest tests/ -q          # 106 tests
```

## Layout

```
app/
  api/          # HTTP layer (request/response models, thin)
  core/         # config, db session mgmt, exceptions, security (JWT), rate_limit
  models/       # SQLAlchemy 2.0 declarative models (Users, Drivers, Deposits,
                #   Orders, LedgerEntry append-only, OtpCodes)
  services/     # domain logic — fare_calculator.py (TDD'd, tariff-versioned),
                #   order/grab (SETNX), ledger, otp, geo dispatch, trip hub
alembic/        # async migrations (include_object filter guards postgis tables)
scripts/        # dev helpers (serve_and_probe, verify_api, stop_server)
tests/          # pytest — unit (fares) + module tests + WS streaming
```

## Fare engine (verified sources)

- Meter tariffs effective **2024-07-14** (TD press release; Cap. 374D schedule):
  Urban $29/2km → $2.1/200m (to $102.5) → $1.4; NT $25.5 → $1.9 (to $82.5) → $1.4;
  Lantau $24 → $1.9 (to $195) → $1.6. Waiting: per 1 min or part.
- Tolls: cross-harbour $25 (+$25 return fee, waived at cross-harbour stands / same-side
  destination), Tai Lam $28 (2025-05-31), Tates Cairn $20, Lion Rock / Eagle's Nest /
  Shing Mun / Aberdeen $8 (Aberdeen & Shing Mun 2025-09-21), Lantau Link $30.
- Other: baggage $6, animal $5, advance booking $5. Discount applies to meter only.

Every estimate embeds `tariff_version` (`meter:2024-07-14;tolls:2025-09-21`) so
historical orders stay auditable.

## Modules (all live)

- **A — Auth & KYC**: WhatsApp OTP (sha256-stored, TTL, cooldown, 5 attempts),
  JWT HS256; driver KYC `PENDING_KYC → DEPOSIT_REQUIRED → ACTIVE → SUSPENDED/TERMINATED`.
- **B — Orders & dispatch**: fare snapshot frozen into `fare_json` at creation;
  Redis `GEOSEARCH` nearby broadcast; `SETNX` atomic grab (exactly-once, 6-way
  concurrency tested); lifecycle `BROADCASTING → ACCEPTED → DRIVER_ARRIVED →
  IN_TRIP → COMPLETED/CANCELLED`; $50 no-show penalty (negative ledger = arrears).
- **C-mini — Ledger**: append-only `ledger_entries` with `balance_after` chain;
  HKD 500 deposit grant gates activation.
- **D — Live tracking**: `WS /ws/trip/{order_id}?token=…` (passenger subscribes,
  assigned driver pushes; driver status re-checked per tick); ticks persist to
  PostGIS, fan out via Redis Pub/Sub (`realtaxi:trip:{order_id}`); REST snapshot
  `GET /api/v1/trips/{order_id}/location` for reconnects; drivers receive direct
  acks (no self-echo).
- **Rate limiting**: Redis fixed-window counters (order creation capped per user
  → 429; namespace `realtaxi:`).

## Roadmap (next)

1. Weekly settlement job (ledger ready; cron + service-fee deduction entries)
2. FCM/WhatsApp production providers (env-driven stubs already in place)
3. Nginx TLS + deploy packaging
