# Feature Expansion Backlog — 2026-10-05

> This document is the integrated design for the next product wave. It replaces
> earlier in-chat ideas (premium customer groups, corporate monthly billing).
> The project remains an information intermediary under Cap. 374D: fare
> estimates are reference-only, every order freezes a fare snapshot, and the
> platform never acts as a carrier.

## Integrated Direction

1. **Premium destinations, not premium customers.** Admin manages a curated
   list of places such as Cathay City, Hong Kong Airlines base, Airport T1/T2,
   and CAD. Each destination carries an avatar pin shown on the map.
2. **Driver-priced flat fares (一口價).** A driver inputs an ideal flat fare
   for a route. When a passenger creates a matching order, the matching
   algorithm surfaces the offer. The difference between passenger price and
   driver payout is disclosed as the platform service fee.
3. **In-car environment.** Passengers can require a silent ride, no radio or
   music, no perfume, and no smoke. Drivers declare which of these their car
   can provide.
4. **Small animals.** Before a driver accepts, the passenger must state the
   species and approximate height/weight. This is visible on the job card and
   map pin.
5. **Payment methods are driver-declared.** Drivers list the methods they
   accept; passengers see them before the trip starts.
6. **No corporate monthly billing.** Every trip is settled individually.
7. **Recurring rides are built from past trips.** A history item becomes both
   "book again" and "repeat weekly".

## Phase 1 — Foundation and Visibility

Goal: all the data the passenger and driver need to make an informed match,
without yet introducing the fixed-fare matching algorithm.

### Data model draft

```text
premium_destinations
├─ id UUID PK
├─ code str unique            # e.g. CATHAY_CITY, HKA_BASE, HKG_T1, HKG_T2, CAD
├─ name_zh str, name_en str
├─ lat float, lng float       # centre point
├─ radius_m int               # geofence for order detection
├─ avatar_key str | null      # R2 object key, never a URL
├─ status enum ACTIVE/HIDDEN
├─ created_by admin_account_id | null
├─ created_at, updated_at
└─ PostGIS point + gist index

driver_payment_methods
├─ driver_profile_id FK
├─ method enum CASH/OCTOPUS/CARD/ALIPAY/WECHAT_PAY/TAP_AND_GO
├─ updated_at
└─ PK (driver_profile_id, method)

orders (new columns)
├─ requirements_json JSONB | null
│   ├─ silent_ride bool
│   ├─ no_radio_music bool
│   ├─ no_smoke bool
│   ├─ no_perfume bool
│   └─ animal { type, height_cm, weight_kg } | null
├─ payment_preference[] | null
├─ premium_destination_id FK | null   # auto-detected at creation
└─ destination_area str | null        # server-derived HK_ISLAND/KOWLOON/NT/AIRPORT
```

### API draft

```text
Admin:
POST   /api/v1/admin/destinations            # create premium destination
GET    /api/v1/admin/destinations            # list (admin view)
PATCH  /api/v1/admin/destinations/{id}       # edit name/status/radius
POST   /api/v1/admin/destinations/{id}/avatar  # upload avatar (R2)

Driver:
GET    /api/v1/drivers/me                    # + payment_methods[]
PUT    /api/v1/drivers/me/payment-methods    # set accepted methods
GET    /api/v1/drivers/me/environment        # in-car capability flags
PUT    /api/v1/drivers/me/environment        # update capability flags

Passenger / shared:
GET    /api/v1/destinations                  # active premium destinations + pins
POST   /api/v1/orders                        # + requirements / payment_preference
GET    /api/v1/orders/nearby                 # + fare_mode/premium_destination/destination_area filters
```

### Mobile mirror draft

- `mobile/lib/models/order.dart`
  - `OrderCreateRequest`: add `requirements`, `paymentPreference`
  - `Order`: add `premiumDestination`, `destinationArea`, `requirements`
  - `FareSnapshot`: add `fareMode` (`METER`/`FIXED`), plus later fixed-price fields
- `mobile/lib/models/driver.dart`
  - `DriverProfileOut`: add `paymentMethods`
- `mobile/lib/features/passenger/request_ride_screen.dart`
  - add environment chips, animal detail fields, payment preference chips
- `mobile/lib/features/driver/driver_jobs_screen.dart`
  - render requirement badges and destination pins before grab

### Fixture and contract updates

- Add `mobile/test/fixtures/order_with_requirements.json` and
  `driver_payment_methods.json`.
- Pin new fields in `scripts/verify/audit_response_models.py`.
- Keep admin-web TS mirrors in sync: `admin-web/src/types/` and destination
  management screen.

### Test plan

- Premium destination CRUD and geofence detection.
- Order creation with requirements and animal bounds.
- Nearby filter by premium destination / destination area / environment.
- Driver payment method set/read and passenger visibility.
- Contract fixtures decode on both Flutter and admin-web.

## Phase 2 — Fixed Fare Matching (一口價)

Goal: driver-priced flat fares with a transparent platform fee.

### Data model draft

```text
fixed_price_offers
├─ id UUID PK
├─ driver_profile_id FK
├─ destination_premium_id FK | null
├─ destination_area enum | null
├─ pickup_area enum | null
├─ price_hkd Numeric(10,2)       # driver payout
├─ status ACTIVE/PAUSED/EXPIRED
├─ created_at, updated_at
└─ one active offer per (driver, route) via partial unique index

orders (Phase 2 additions)
├─ fare_mode enum METER/FIXED    # already on fare_json, promoted to column
├─ fixed_offer_id FK | null
├─ driver_price_hkd Numeric(10,2) | null
└─ platform_fee_hkd Numeric(10,2) | null

ledger_entries
└─ new entry type FIXED_RIDE_FEE, reference namespace fixed:{order_id}
```

### Matching rule

```
1. Passenger creates an order. Server computes the meter estimate and detects
   any premium destination geofence.
2. Query ACTIVE fixed_price_offers where:
   - route matches (destination premium/area + pickup area)
   - driver is ACTIVE and has not paused offers
   - driver_price_hkd + platform_fee_hkd <= meter_estimate
3. If a match exists:
   - fare_mode = FIXED
   - passenger_price = driver_price + platform_fee (frozen in fare_json)
   - broadcast to matching offer drivers only
4. If no match:
   - normal METER broadcast.
```

Platform fee rules live in admin config: a minimum fee and an optional cap.

### Financial invariants

- All fixed-fare fields are frozen in `fare_json` at order creation.
- `platform_fee_hkd` is deducted from the driver deposit after trip completion
  through `LedgerService.append` (single settlement, no monthly billing).
- The fee is disclosed to both passenger and driver; never a hidden spread.
- Existing `ROUND_HALF_UP` and money-string rules apply to every new amount.

### API draft

```text
Driver:
GET    /api/v1/drivers/me/fixed-offers
POST   /api/v1/drivers/me/fixed-offers        # create offer
PATCH  /api/v1/drivers/me/fixed-offers/{id}   # pause/price update (future orders only)
POST   /api/v1/orders                         # server auto-matches offer
GET    /api/v1/orders/nearby?fare_mode=FIXED&premium_destination=HKG_T1
```

### Test plan

- Match only when `driver price + fee <= estimate`.
- Fixed order frozen after grab; driver cannot change price.
- Fee ledger idempotency via `fixed:{order_id}` reference.
- Only matching offer drivers can grab a FIXED order.
- Fixture contract parity for `fare_mode` / `driver_price` / `platform_fee`.

## Phase 3 — Recurring Rides and Notifications

### Recurring rides from history

- Passenger trip history items gain two actions:
  - **Book again** — pre-fills the same route/requirements.
  - **Repeat weekly** — opens frequency and time-of-day selection.
- `recurring_rides` uses `next_run_at` as a DB cursor. Each scheduler run
  creates exactly one order and advances the cursor; it never fills a calendar
  ahead of time.
- Scheduled orders run through the same `OrderService.create` path so every
  fare snapshot and requirement rule applies.

### Notifications

- WebSocket/push badge when a premium-destination order or fixed-fare order
  appears, so subscribed drivers can react quickly.
- **已實作（2026-10-07，in-app scope）**：durable Postgres inbox +
  `GET/PATCH /drivers/me/notifications` + mobile badge/list；external push
  (WhatsApp / FCM) 明確留喺 range 外。

## Explicitly Removed

- Premium customer groups / customer badges requiring passenger verification.
- Corporate monthly billing / contract invoices.
- Platform collection at order creation; all money stays per-trip and
  per-ledger-append.

## 2026-10-08 Addendum — Next Candidate Features (Proposal Only)

The phases above are implemented or partially implemented. The items below are
**proposals for the next product wave**, not started work. They are ordered by
expected value and by how cleanly they fit the existing Cap. 374D
information-intermediary boundaries.

### 1. Passenger saved places (常用地點)

Passengers repeat the same home/work/airport pickups and dropoffs more than
any other route. A saved-place list turns a multi-tap map form into a two-tap
repeat ride and reduces coordinate-entry mistakes.

- New `passenger_places` table: label, optional `is_home` / `is_work` flags,
  PostGIS point, address text, owner passenger id, `updated_at`.
- Endpoints: `GET/POST/PATCH/DELETE /me/places`; create/list is enough for v1.
- Mobile: saved-place chips on `request_ride_screen.dart`, prefilled via the
  existing `OrderCreateRequest` path; `trip_history_screen.dart` can offer
  "save this route" after completion.
- Privacy: places belong to one passenger, are never exposed to drivers in raw
  form, and are deleted with the account.
- Test surface: CRUD parity, max places per passenger, stale coordinate
  handling, contract fixtures in Flutter/admin-web.

### 2. Post-trip ratings and reviews (行程後評分)

The platform has dispute flow but no ongoing feedback signal. A bilateral,
post-completion rating gives matching and KYC review a usable quality signal
without turning every trip into a moderation burden.

- Backend: `trip_ratings` with one row per `(order_id, rater_role)`; rating
  `1..5`, optional bilingual comment, `created_at`, no edits after a short
  window.
- Both passenger and driver must rate for the profile aggregate to move;
  aggregates are anonymous and displayed as rounded averages only.
- Admin console gets a moderation queue for reported/flagged reviews, reusing
  the existing dispute/audit patterns.
- Deliberately **not** a pre-trip driver score shown in a broadcast; that
  would invite gaming before launch. Keep it aggregate-only at first.
- Test surface: duplicate prevention, role pairing, anonymous aggregation,
  admin flag/moderation, fixture parity.

### 3. Passenger in-trip status page and trip sharing (乘客在途狀態)

The driver already pushes location during an active trip. The passenger side
can consume that with a low-cost status page: driver name/plate, current
phase, ETA estimate, and a one-time share token for family/friends.

- Backend: `GET /orders/{id}/trip-status` (passenger only) plus a short-lived,
  revocable share token; no WebSocket change needed, so it follows the same
  cost model documented in `REALTIME_POSITION_COST.md`.
- Mobile: a passenger "in-trip" card that refreshes on a deliberate interval
  (not continuous map streaming), with a share button that opens a read-only
  URL.
- Security: share token expires after trip end or on revoke; it exposes plate,
  phase, and ETA but never passenger phone/name or payment data.
- Test surface: passenger-only auth, token expiry/revocation, cost budget,
  Flutter contract decode, share URL hardening.

### 4. Driver weekly earnings summary and reconciliation export

Driver earnings exist, but the next useful step is a driver-facing weekly
summary that mirrors the admin settlement logic and can be exported for
reconciliation.

- Backend: `GET /drivers/me/earnings/summary?from=&to=` and
  `GET /drivers/me/earnings/export.csv`; amounts reuse `app/core/money.py`
  and ledger entry types, never recompute frozen snapshots.
- Mobile: earnings screen already exists; add week selector, fare/fee/refund/
  dispute line items, and CSV export.
- This is deliberately **not** a payout channel. It is a read-only
  reconciliation view until a real payment provider is connected.
- Test surface: CSV escaping, date-bucket sums, ledger idempotency, response
  model parity, admin export consistency.

### Candidate ranking for launch

| Rank | Feature | Why now | Main risk |
|---|---|---|---|
| 1 | Passenger saved places | Highest repeat-use win, small data surface | Geocoding quality |
| 2 | Driver earnings summary/export | Uses existing ledger invariants, useful before payout | CSV/budget mismatch if not pinned |
| 3 | Passenger trip status + share | Safety/UX value with existing position data | Token lifecycle abuse |
| 4 | Post-trip ratings | Quality signal for matching | Moderation/gaming cost |

## Cross-Cutting Constraints

1. Backend Pydantic schema, Flutter model, admin-web TS mirror, and fixtures
   must stay in parity.
2. Redis remains a transient index; PostgreSQL/PostGIS is the source of truth.
3. Every new order field is nullable or defaulted until the corresponding UI
   ships, so existing orders and tests stay valid.
4. No new rounding rules; reuse `app/core/money.py`.
