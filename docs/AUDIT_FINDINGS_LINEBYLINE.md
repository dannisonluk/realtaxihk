# Line-by-line audit — findings log

Scope: the buckets that the first pass did not cover line-by-line.
Method: read every file, verify each claim against the other two deliverables before writing it down.
Status: IN PROGRESS.

## Fixed in this pass — 2026-10-05

| finding | site | fix |
|---|---|---|
| **NEW-22** (HIGH) | `app/api/admin/accounts.py:199-233`, `app/services/admin/admin_account_service.py:225` | `reset_admin_password` now revokes **both** halves of the session — the refresh rows inside the same transaction, then the access-token epoch in Redis — and reports `sessions_revoked: True`. That is what the response schema docstring already asserted ("the reset also ejected anyone already signed in") and what `AccountsPage.tsx:161` already branched on. The service docstring no longer claims to revoke. |
| **NEW-19** (LOW) | `app/api/admin/drivers.py`, `app/api/fleets.py` | An unrecognised `status_filter` is now a **400** with `reason: UNKNOWN_STATUS` and an `allowed` list built from the enum — the shape `/admin/orders` already used. Was a 500. Enum-derived, so the list cannot drift from the column. |
| **NEW-20** (LOW) | `app/api/admin/drivers.py` | `total` now carries the page's predicate. `FleetService.list_page` and `/admin/refunds` already did; this route was the odd one out. |
| **NEW-22, second half** (HIGH) | `app/api/admin/accounts.py`, `app/services/admin/admin_account_service.py`, `app/services/admin/audit_service.py`, `admin-web/web/src/**` | New `PATCH /admin/accounts/{id}/active`, SUPER_ADMIN only, with the two constraints `change_role` already carried — nobody switches themselves off, and the last usable SUPER_ADMIN cannot be switched off — plus session revocation on the way out. Deactivation revokes the refresh family **and** writes the access-token epoch; reactivation revokes nothing and says so. A door of its own rather than a field on a general `PATCH /accounts/{id}`, for the reason `/role` gives. |
| **NEW-13** (MEDIUM) | `app/services/admin/dispute_service.py:443` | `resolve()` now reads the dispute `with_for_update=True`, closing the read-check-write window in which two admins could both pass the "already resolved?" check and write different resolutions. Same class of fix the ledger already documents (P0-1). |
| **NEW-21** (MEDIUM) | `app/core/config.py:255`, `app/services/ledger/ledger_service.py:218`, `app/api/admin/drivers.py:180`, `app/api/drivers.py:115` | `driver_deposit_default_hkd` was dead config while four call sites hard-coded `500`. It now feeds the ledger deposit-row factory and both driver/admin deposit serializers. The console still hard-codes `500` as a grant suggestion (NEW-27 related client-side mirror), which is a separate low-risk UI cleanup. |
| **SS-D1** (HIGH) | `app/models/user.py:344-346,552-554`, `alembic/versions/f1c2d3e4a5b6_harden_deposit_and_refund_fks.py` | `driver_deposits.driver_profile_id` and `refund_requests.driver_profile_id` changed from `ON DELETE CASCADE` to `ON DELETE RESTRICT`, so deleting a user/profile can no longer silently erase a paid deposit or a refund decision while `ledger_entries` stays RESTRICT. There is no product delete-user path (only fixture cleanup), so this is invariant hardening rather than a workflow change. **Verified:** `test_migrations_apply_to_a_plain_postgres` passes on a fresh Postgres with the migration applied. |
| **NEW-23** (MEDIUM) | `app/api/admin/disputes.py` | The resolve audit row stays in the handler but the comment now states the truth: the resolution commits in its own transaction and the audit in another. The audit row can therefore be lost on a crash between the two commits, exactly on a money-moving action. Marked as a known two-phase limitation rather than silently claiming atomicity — the next step is moving the audit into `resolve()`'s transaction. |

**Third-deliverable parity, which the NEW-22 fix invalidated.** Three places in the console
asserted that a reset *cannot* revoke a token in flight: the `types.ts` docstring, the
`AccountsPage.tsx` comment above the note, and the `accounts.tokenNote` string in both locales.
All three were true when written and became false with the fix, so they moved in the same commit:
`tokenNote` now describes what happens, `resetDonePending` is deleted (the branch it served is
unreachable — there is no partial-success form of the operation), and the page gained the
deactivate/reactivate control. `lastSuperNote` now covers deactivation as well as demotion. The
console had been rendering an `accounts.disabled` chip with no route that could produce such an
account.

Tests added with the fixes — `tests/api/test_admin_drivers.py` (new file, 3 tests),
`tests/api/test_fleets.py` (2 tests), and
`tests/api/test_admin_accounts.py::TestPasswordReset::test_reset_revokes_the_sessions_it_reports`,
which asserts the revocation by **using** the stale token afterwards (exercising the real
Redis epoch on the hot path) rather than by checking that a key exists.

**Still open, because the choice changes who may act rather than what the code does:**

- **N-1** — the SoD rank semantics (finance ⊃ operations). Three documented options; the
  choice changes who may act, so it is not mine to make.

---

## NEW-1 — HIGH — mobile mirrors the service-area box the server deliberately abandoned

**Mobile (the stale side)**
- `mobile/lib/core/location/location_service.dart` — docstring says the box is
  "`_HK_BOUNDS` in `app/api/orders.py`" and that it exists so `current()` can
  "return null rather than a position the server will refuse, so callers show
  'locating…' instead of an error the user cannot act on."
- Same box duplicated in `mobile/lib/features/trip/.../map_panel.dart` (its own
  docstring also names `app/api/orders.py`).
- Consumed at `driver_active_trip_screen.dart` — `isInHongKong` decides whether a
  location tick is sent at all.

**Server (the moved-on side)**
- `app/core/hk_bounds.py` — the real gate is a **polygon**, and its docstring is an
  essay on why the box is wrong. It lists the coordinates the box admits, verified:
  `Futian 22.5410, 114.0540`, `Luohu 22.5480, 114.1230`, `Bao'an 22.5550, 113.8830`.
- `HK_BBOX` survives only as a cheap reject *inside* `is_in_hong_kong()`.
- Applied at `app/api/orders.py`, `app/api/tracking.py`, `app/api/ws.py` (`OUTSIDE_HK`).
- `app/core/service_area.py` states the client check is "advisory … a courtesy on
  top of it" — which is the opposite of what the mobile docstring claims.

**Consequence** — a driver (or GPS drift) inside Shenzhen passes the client check,
the tick is sent, and the server refuses it. The client-side guard does not do the
one job its docstring names. Three deliverables, one contract, two definitions.

**All four call sites read, and the box is worse than "unused":**

- `driver_active_trip_screen.dart:151-156` — the tick gate. Its own comment names the
  purpose: *"Guard the server's own bounds: a tick outside Hong Kong earns
  `{"type":"error","code":"BAD_LOCATION"}` and burns a token for nothing."* Shenzhen
  passes the box, so the tick is sent and the token burned — the exact waste the
  comment exists to prevent. (The comment also names the wrong error code; see NEW-2.)
- `map_panel.dart:167-170` — the coordinate readout appends
  `'  (香港範圍外，API 會拒絕)'` when `!isInHongKong(...)`. For a Shenzhen point it appends
  **nothing**, so the app affirmatively tells the operator the point is inside Hong
  Kong when the server's authority says it is not.
- `location_service.dart:60` — `current()` returns the fix on the box test alone.

**Impact (honest bound)** — the server gate holds: `is_in_hong_kong()` still refuses the
tick, so nothing out-of-area is dispatched. This is a correctness/parity defect, not a
breach. Severity stays HIGH because the client gives a *wrong answer* to "is this in Hong
Kong?" in a Hong-Kong-only dispatch system and its own documented guard is inert — but the
blast radius is bounded by the server, and the cheap half of the fix is NEW-2.

**Fix options** — (a) port the polygon (duplicated geometry, drift risk again);
(b) drop the claim and handle the server's `OUTSIDE_HK` refusal explicitly.
(b) is honest and cheap; (a) needs a shared source or a generated artefact.

## NEW-2 — MEDIUM — the WS error catalogue does not contain the code the server sends

- Server emits `OUTSIDE_HK` (`app/api/ws.py`, ws.py:191 region).
- `mobile/lib/models/trip.dart` `TripErrorEvent.messageZh` maps `BAD_LOCATION`
  (never sent) and has **no case for `OUTSIDE_HK`** → falls through to
  `位置推送失敗（OUTSIDE_HK）`. Its docstring documents the wrong code set too.
- Compounds NEW-1: the one refusal a Shenzhen-side driver can actually trigger is
  the one the client cannot explain.

## NEW-3 — MEDIUM — the password-policy mirror omits the server's NFC normalisation

- `app/core/passwords.py` normalises to **NFC first**, then checks length,
  whitespace and weakness.
- `mobile/lib/core/security/password_policy.dart` checks `password.runes.length`
  on the **raw** string; its docstring justifies code-point counting against
  Python's `len` but never mentions NFC.
- Concrete: 11 ASCII characters + one decomposed accent = 12 runes raw (client
  accepts) → 11 after NFC (server returns the 400 the mirror exists to prevent).
  NFC composition shortens, so the client can only ever be *laxer* here.

## NEW-4 — MEDIUM — a refresh failure destroys the refresh token on any transport error

- `mobile/lib/core/network/api_client.dart` `_refresh()`: `on DioException` →
  `tokenStore.clear()` and return null. Every failure kind lands there — timeout,
  connection error, and **5xx**.
- A 502 from nginx during a deploy, or a slow response, therefore logs the user out
  **permanently** (the 14-day refresh token is gone), not just until the network
  returns. Only a 401 justifies clearing.
- Access-token TTL is 15 minutes, so this path is exercised constantly.
- Contradicts the stated policy in `mobile/lib/state/auth_controller.dart`
  ("a network blip must not log the user out"), which keeps the session.

## NEW-5 — MEDIUM — the token cache's catch list is narrower than its own parse

- `mobile/lib/core/token_store.dart` `read()` catches `FormatException` and
  `ArgumentError`, and its docstring says a corrupted cache just means "sign in
  again".
- But the parse it guards, `AppUser.fromJson` (`mobile/lib/models/auth.dart`), uses
  raw `as String` casts. A cached object that is valid JSON with a missing/renamed
  key throws `TypeError`, which is **not** in the catch list → uncaught during
  cold start. An upgrade that changes the cached shape therefore crashes at launch
  on every start, with no in-app recovery.
- Note `AuthSession`/`AppUser` are also the only models in the app that cast
  directly instead of using `wire.dart` helpers, in the same file that imports it.
- Same raw-cast pattern (lower impact) at `models/order.dart` (`degraded`),
  `models/driver.dart` (`is_fulfilled`, `is_online`).

## NEW-6 — LOW — the settlement week is a UTC ISO week, i.e. it turns at Monday 08:00 HKT

- `period_key()` = `datetime.now(UTC).isocalendar()`; used by both the automatic job
  and the manual run when no period is given.
- The mechanism is **sound** (fires at boot then every 7 days, so consecutive fires
  are consecutive weeks, and a boot fire is an idempotent no-op) — verified.
- What is worth knowing: the boundary lands at Monday 08:00 HKT, so a manual run
  "on Monday morning" before 08:00 bills the previous week and after 08:00 bills the
  current one. The preview prints the period and the confirm token binds it, which
  is what keeps this an operational note rather than a defect.

## NEW-7 — INFO — the confirm-token HMAC reuses the JWT signing secret

- `app/services/ledger/settlement_confirm.py` signs with `settings.jwt_secret_key`.
- Separate purpose (`_PURPOSE`) and disjoint message shapes mean no practical
  cross-protocol forgery — but a dedicated derived subkey would be cleaner.

## NEW-8 — INFO (recorded, not a defect) — client-supplied `distance_km` shapes the frozen fare

- `app/services/order/order_service.py` builds the snapshot from `payload.distance_km`;
  the server bounds it `0 < d ≤ 100` and never recomputes it from the coordinates.
- Verified that **nothing financial reads it**: `fare_json` / `total_fare` appear only
  on display paths (`order_out`, `admin/orders.py`). No settlement, ledger or refund
  code touches them.
- So this is the documented Cap. 374D "estimate only, the metre decides" position,
  not a vulnerability. Listed so the next reader does not re-litigate it.

## NEW-9 — MEDIUM — the streaming body cap answers 400, and its 413 branch is dead code

**Verified by experiment, not by reading** (temporary probe against the real app + real DB,
since removed): a chunked body over the cap returns **400 `BAD_REQUEST`**, not 413.

- `app/core/middleware.py` `BodySizeLimitMiddleware` has two enforcement points. #1, the
  declared `Content-Length`, works and is the one tested.
- #2, the metered receive for chunked/undeclared bodies, raises `_BodyTooLarge` out of the
  receive wrapper and expects the middleware's own `except _BodyTooLarge` to answer 413.
- It never gets there. `add_middleware()` places the middleware **outside** Starlette's
  `ExceptionMiddleware`, but FastAPI parses the JSON body **inside** it, and FastAPI's own
  broad `except Exception` converts the raise into
  `HTTPException(400, "There was an error parsing the body")` first.
- So the payload is still refused (nothing oversized is parsed), but the contract is wrong:
  a client that branches on `code` — the mobile upload paths, `admin-web`'s `CODE` map —
  reports "error parsing the body" where the design says "too large".
- The obvious "fix" is also unsafe: making `_BodyTooLarge` escape the handler would let
  `_send_json` emit a second `http.response.start` if the app had already begun a response.
- **Test gap**: `tests/api/test_security_hardening.py::test_oversized_body_rejected_before_parsing`
  passes `json=`, so httpx sets `Content-Length` and only path #1 is exercised. The suite is
  green while half the feature is unreachable.

## NEW-10 — LOW — the WS idle watchdog can never fire

`app/api/ws.py` `watchdog()` reaps a socket when
`time.monotonic() - activity["at"] > ws_idle_timeout_s`. But `send()` — called by
`heartbeat()` every `ws_heartbeat_s` — refreshes that same `activity["at"]`
(`ws.py:168-171`). Config: `ws_heartbeat_s = 30`, `ws_idle_timeout_s = 300`
(`app/core/config.py:162,165`, no `.env` override). 30 < 300, so the condition is
arithmetically unreachable: the module docstring's claim that "a dead one is detected"
and the comment "reap a connection with no traffic in either direction" are both false.

**Honest impact bound** — uvicorn runs with its default `--ws-ping-interval 20 /
--ws-ping-timeout 20` (the `CMD` in `Dockerfile:34` sets neither), so a genuinely dead
peer is still reaped by the transport, and app-idle-but-alive sockets are arguably meant
to live. So this is dead code plus a false claim, not a leak. No test covers it
(`grep watchdog|idle_timeout tests/` → empty), which is why it went unnoticed.
Fix: track inbound activity in its own field (`reader()` already updates `activity`, so
only `send()` needs to stop touching it), or delete the watchdog and its claim.

## NEW-11 — LOW — a Redis failure while releasing the grab lock turns a won order into an error

`app/services/order/grab_service.py:106-108` releases the lock in `finally` via
`await self.redis.eval(...)`. The DB commit has already succeeded at that point, so if
Redis is unreachable the `eval` raises out of `finally` and `grab()` raises instead of
returning `True` — the driver sees a 500 for an order the database has already assigned
to them, and the route's 409/conflict story does not apply. The TTL already guarantees
release, and every other Redis teardown in this codebase is best-effort
(`contextlib.suppress`), so the release should be too.

## NEW-12 — LOW — the recovery-code entropy claim is 10 bits higher than the code

`app/core/totp.py` `generate_recovery_codes(count=8, nbytes=5)` documents each code as
"~10 base32 chars (50 bits)", and `hash_recovery_code` justifies plain SHA-256 with "the
code is 50 bits of CSPRNG output".

Measured: `base64.b32encode(bytes(5))` is **8 characters / 40 bits** (verified by running it).
Every call site uses the default (`generate_recovery_codes(RECOVERY_CODE_COUNT)` — only
`count` is overridden), so 40 bits is the real figure. 50 bits would need `nbytes=6`.

40 bits is still fine for a single-use, rate-limited, IP-budgeted code, so this is a
documentation defect rather than a weakness — but it overstates the security margin by
1000× in a security module, and the same sentence is what justifies the unsalted-hash
choice. Hashing itself is correct (single-use, DB-stored, compared exactly).

## NEW-13 — MEDIUM — `DisputeService.resolve()` is a non-atomic check-then-act

`app/services/admin/dispute_service.py:442-463` reads the row with plain
`session.get(OrderDispute, dispute_id)`, then refuses a second decision on
`dispute.resolution is not None`. There is no `WITH FOR UPDATE`, no `populate_existing`,
no version column, and no partial unique index — verified: `grep -n "with_for_update|
populate_existing" dispute_service.py` returns **nothing**, while the same grep on
`refund_service.py` returns three hits. `order_disputes.__table_args__` carries indexes and
a value-range `CheckConstraint` on `resolution`, but nothing that makes the write once-only.
No test exercises a concurrent resolve (`grep -rn resolve tests/ | grep -i "concurr|race|twice"` → empty).

So two admins resolving the same case at the same moment both observe `resolution IS NULL`,
both commit, and the second **silently replaces** the first — `resolution`,
`resolution_note`, `resolved_by` and `resolved_at` all become the loser's. The docstring's
guarantee ("`resolve` refuses a second resolution … a case whose money decision can be
silently overwritten is a case where the first decision does not matter") holds only when
the calls are serialised. A resolution can move money and the route requires FINANCE, so the
overwritten value is a financial decision.

Fix: mirror `RefundService.decide` — `with_for_update()` + `populate_existing=True` — which
is the discipline this codebase already applies one directory over, and whose docstring
explains why.

## NEW-14 — LOW — two unique constraints with no `IntegrityError` backstop, in code that catches it elsewhere

Both sites are check-then-act against a real unique constraint, and neither catches the
violation, so a race surfaces as a 500 rather than the clean 400 the same codebase returns
one function away.

1. `FleetService.update` (`app/services/fleet/fleet_service.py:135-166`) can rename a fleet
   into a name that already exists. `Fleet.name` and `Fleet.license_no` are both
   `unique=True` (`app/models/fleet.py:103,105`), and `create()` deliberately pre-checks to
   turn this into `BusinessRuleError("a fleet with that name already exists", {"field": ...})`
   — with a comment saying the constraint is there for the race. `update()` has no such check
   and `app/api/fleets.py:296-303` has no `IntegrityError` handler, so the rename 500s.
2. `FleetSettlementService.run_weekly`'s aggregate upsert
   (`app/services/fleet/fleet_service.py:223-247`) reads `fleet_settlement_runs` then inserts.
   `uq_fleet_settlement_period` exists (`app/models/fleet.py:223`), so a concurrent second run
   (operator double-click, or the scheduler overlapping a manual run) raises `IntegrityError`
   **after** every member has been charged. The charges are correct — the per-driver ledger
   reference is idempotent and the per-driver path *does* catch `DuplicateReferenceError` — but
   the operator gets a 500 and the loser's counters are never written.

## NEW-15 — LOW — `skipped` conflates "already charged" with "nothing collected"

`app/services/fleet/fleet_service.py:162-164`: a member whose `DriverDeposit` row is missing is
counted as `skipped`, the same bucket as a member already charged this week. The two mean
opposite things to an operator reading `skipped=3` on the settlement history: "the re-run was
idempotent" versus "we collected nothing from three members". The author's own comment eleven
lines below makes exactly this argument — *"The two failures are one lost fee apart and must
not share a branch"* — which is why the `DuplicateReferenceError` narrowing is correct, and why
this branch is inconsistent with it. Unreachable in practice (the member query already filters
`DriverProfile.status == ACTIVE`, and an ACTIVE driver has a deposit row), so this is a
reporting defect on a defensive branch, not a live revenue gap.

## NEW-16 — LOW — the licence submission cap is a rolling 24 h window, not "today"

`LicenceService._submissions_today` (`app/services/licence/licence_service.py:562-576`) computes
`start = now - timedelta(hours=24)` and counts `submitted_at >= start`, while the constant is
`MAX_SUBMISSIONS_PER_DAY` and the refusal reads *"too many submissions today; try again
tomorrow"*. A driver who submits five times at 23:00 is blocked until 23:00 the next day, not
"tomorrow". Only a message/name mismatch on a flood-control guard (no money involved), so LOW —
but it is the same "UTC/rolling window where the domain means a Hong Kong day" family as the
settlement period key (NEW-6) and the console's `today()` bucketing (already fixed as AC-08),
which is worth treating as one pattern rather than three coincidences.

## NEW-17 — LOW — the per-code attempt cap resets with every new code

`app/services/auth/otp_service.py` documents "max 5 attempts per code, then the code is dead
even if correct" as a security property, and `consume_code` enforces it with
`otp.attempts >= _MAX_ATTEMPTS` on the row it reads. But `request_otp` only enforces a
**60-second resend cooldown** (`_RESEND_COOLDOWN_S`) and each new `OtpCode` row starts with
`attempts = 0`. So an attacker targeting one known number gets 5 fresh guesses on every new
code, i.e. a sustained budget of 5 guesses/minute rather than 5 per 5-minute TTL.

Honest bound: against a 10^6 code space that is ~0.7% per day of success for a targeted
attacker who cannot read the victim's messages, and the attack is loud — the victim receives a
code every 60 seconds and will notice, and it burns WhatsApp/SMS credit. So this is a gap
between the stated and the effective property rather than an open door. The fix is to count
attempts per *phone* over a window instead of per row. The global OTP limit in
`config.py` (`otp_global_hourly_hard_limit`) is a blast-radius cap across all users and does
not bound a single-target attack.

## Noted trade-off (deliberate, not a defect)

`RefreshService.rotate` classifies **any** second presentation of a rotated token as a reuse
and revokes the whole family (`refresh_service.py:94-98`), so two genuinely concurrent
refreshes log the user out everywhere. The mobile client closes the practical version of this
with a single-flight `_refreshOnce` in `api_client.dart` (in-process, and the app is
single-process), and the reuse-detection trade-off is spelled out in the module docstring.
Recorded so it is not re-raised as a bug without the client context.

## N-1 (from report v4) — CONFIRMED line-by-line, and now with the exact line

Report v4 raised N-1 as HIGH: `require_role` compares by rank (`at_least`), so
FINANCE ⊇ OPERATIONS, contradicting the separation-of-duties the model claims. This pass
found the exact expression that makes it bite, in the one handler where the intent is
written out in full.

`app/api/admin/disputes.py` `resolve_dispute` (route ~402) deliberately puts the endpoint on
`require_admin` and then narrows **per request**:

```python
actor_role = await live_admin_role(session, admin)
if not actor_role.at_least(AdminRole.OPERATIONS):      # the floor
    raise HTTPException(403, {"reason": "ADMIN_ROLE_INSUFFICIENT", ...})
if resolution.moves_money and not actor_role.at_least(AdminRole.FINANCE):
    ...                                                # the money escalation
```

and its docstring states the intent verbatim: *"a fixed `require_role(OPERATIONS)` would let
the operator who judged the conduct also authorise the payout, which is the separation of
duties the two roles exist to enforce."*

With rank semantics, a **FINANCE** admin satisfies `at_least(OPERATIONS)` as well as
`at_least(FINANCE)`, so a single FINANCE admin can judge the conduct *and* authorise the
payout — precisely the outcome the docstring says the two-role split prevents. With
independent role sets the FINANCE admin would be refused at the floor. `app/api/admin/_roles.py`
states the same policy in its header ("dispute resolution that moves money -> FINANCE, checked
per-request"). So the contradiction is not just model-vs-docstring; it is two docstrings
describing an invariant that the comparison operator cannot express.

**Owner decision (2026-10-05): accept the rank hierarchy.** The global model is
hierarchical — FINANCE is senior to OPERATIONS and also passes OPERATIONS gates.
Code docstrings (`app/models/admin.py`, `app/api/admin/_roles.py`,
`app/api/admin/disputes.py`, `tests/conftest.py` `finance` fixture) now describe
that truth. The dispute-resolution handler keeps a deliberate decision-matched
whitelist on top of the hierarchy, so the assigned judge cannot authorise a
payout; it is a per-handler business rule, not a claim of global mutual
exclusion. The pinning test in `tests/api/test_admin_role_separation.py`
continues to encode the rank behaviour.

## RBAC matrix — verified against `app/api/admin/_roles.py` (clean)

Walked every `app/api/admin/*.py` for guard usage and checked it against the policy header:

- `accounts.py` — all four endpoints on `_require_super` ✓ (only SUPER_ADMIN may change roles).
- `refunds.py` (1 write), `settlement.py` (3) — all `_require_finance` ✓ (money movement).
- `drivers.py` — `_require_operations` for the state/KYC write, `_require_finance` for the two
  money writes ✓.
- `disputes.py` — `_require_operations` for assign/message/status, and the per-request live-role
  escalation above for resolve ✓.
- `orders.py`, `audit.py`, `live.py`, `search.py` — **no write endpoints at all**
  (`grep "@router\.\(post\|patch\|put\|delete\)"` → exit 1), so "read endpoints stay on
  `require_admin`" holds for every file that has nothing but reads ✓.
- `resolve_dispute` reads the role through `live_admin_role` (a DB re-read), not the token
  claim, so a demotion lands on the next request ✓.

## NEW-18 — MEDIUM — an unverified email claim is written to `users.email`, so any user can squat an address

`IdentityService.request_email_verification` (`app/services/auth/identity_service.py:169-213`)
writes the claimed address onto the account **before** anything is proven:

```python
user.email = address          # line 203 — deliberately, see the comment at 200-202
await self.session.flush()
link = f"{settings.public_base_url...}/verify-email?token={raw}"
await get_email_provider().send_verification_email(address, link)
```

`users.email` is `unique=True, index=True` (`app/models/user.py:191`), and registration refuses
an address that any row already holds — `register` checks
`func.lower(User.email) == address.lower()` (`account_service.py:259-268`) and `request_email_verification`
checks `User.email == address` (line 181-187). So:

1. Any registered user calls `POST /identity/email/request` with `victim@example.com`.
2. The address is free, so the check passes and the row is written **immediately**, without any
   proof of ownership.
3. The real owner can now neither register with that address nor set it on their own account —
   the unique index and both pre-checks refuse it. Nothing expires the claim: it is the account's
   address column, and no retention job can null it without breaking the account.

The endpoint is authenticated and IP-rate-limited (`identity.py:236-240`), which bounds volume but
not the squat itself, and the attacker can repeat it from a second account. Two secondary effects:
the attacker's *previous* address is silently freed by the overwrite, and the victim's email is
now stored on a stranger's row while the platform sends them unsolicited verification mail.

**The write is also redundant**, which is what makes the fix small: the pending address already
lives on the token row (`EmailVerificationToken(email=address)`), and `confirm_email` sets
`user.email = row.email` from the token on success (line 257) with a fresh uniqueness re-check
(line 247-255). The only stated reason for writing early is the comment at 200-202 — so the UI can
say "we sent it to X" — which is available from the token row or the response's own `_mask_email`
value. Dropping line 203 removes the squat without changing the verified outcome.

## INFO — the analytics layer reports a client-authored number, and that is the platform's only fare data

`app/services/admin/analytics_service.py` builds every earnings figure from
`Order.estimated_total_hkd` (`sum`/`avg` at lines 179-180, the heat map at 261+). That column is
produced by `FareCalculator` from `payload.distance_km` — the **client-supplied** distance
(`order_service.py:81,101`, bounded only to `(0, 100]`), the accepted risk recorded above.

This is not a new defect: the docstring is explicit that the module answers the *operational*
question rather than the billing one ("a 'highest earning hour' chart built on the ledger would
rank hours by how much the platform billed the driver"). And there is no better number available
— under Cap. 374D the platform is an information intermediary, the meter is authoritative, and
the platform never sees the metered fare at all.

The consequence is worth stating plainly for whoever reads the console: **every revenue, average
fare and "busiest hour" figure in the admin analytics is derived from what the client declared,
not from anything the platform verified.** A client that under-declares distance moves those
charts. That is a data-provenance caveat for the BI surface, not a security finding — but it is
the reason a future "verified distance" (map-matching, or a driver-confirmed figure) would be a
product change rather than a bug fix.

## Verified clean — `analytics_service.py` timezone handling

The bucket key and the range filter both go through Hong Kong: `local_ts =
func.timezone(HK_TZ, Order.completed_at)` for grouping (lines 170, 252) and `_hk_day_bounds()`
inside `_completed_in_range` for the WHERE clause (lines 117-129), with `HK_TZ = "Asia/Hong_Kong"`
as a name rather than a fixed +8 so the database's own tz rules apply. The docstring explains
why the cast is in SQL and not in Python. Consistent — no repeat of the AC-08 defect.

## NEW-19 — LOW — an unvalidated enum taken from the query string answers 500, at two sites

`register_exception_handlers` (`app/core/exceptions.py:78-205`) installs handlers for
`RequestValidationError` (422), `BusinessRuleError` (400), `NotFoundError` (404),
`StarletteHTTPException` and a catch-all `Exception` (500). There is **no handler for a plain
`ValueError`** — and `Enum("bogus")` raises exactly that. So a coercion straight from request input
reaches the catch-all and the caller gets a 500 for what is a malformed request:

```python
app/api/admin/drivers.py:79   q = q.where(DriverProfile.status == DriverStatus(status_filter))
app/api/fleets.py:270         fleet_status = FleetStatus(status_filter) if status_filter else None
```

`GET /api/v1/admin/drivers?status_filter=bogus` and `GET /api/v1/admin/fleets?status_filter=bogus`
both answer `500 INTERNAL_ERROR`; the correct answer is 400 (as `/admin/orders` gives) or 422 (as
`/admin/refunds` gives). The cost is a wrong status code plus one `logger.exception` per probe, so
anyone can fill the unhandled-exception log with a curl loop — the 500 handler is the only place
these are observed, which is what makes the noise worth avoiding.

**The codebase already contains both correct patterns**, which is what makes this a missed site
rather than a design choice:

* `app/api/admin/orders.py:129-141` wraps the coercion in `try/except ValueError` and raises a 400
  carrying `{"reason": "UNKNOWN_STATUS", "allowed": [...]}` — the docstring even explains why
  ("a filter that silently matches nothing is how an operator concludes there are no cancelled
  trips this week");
* `app/api/admin/refunds.py:29` and `app/api/fleets.py:145` push the check into the type
  (`Annotated[str | None, Query(pattern=r"^(PENDING|APPROVED|REJECTED)$")]`,
  `Field(pattern=r"^(ACTIVE|SUSPENDED|DISSOLVED)$")`) so FastAPI rejects it before the handler runs.

The same `Field(pattern=...)` treatment on the two `status_filter` parameters is the smallest fix.

## NEW-20 — LOW — `GET /admin/drivers` reports the unfiltered total while filtering the rows

`list_drivers` (`app/api/admin/drivers.py:66-94`) builds the page query with the filter and the
count query without it:

```python
q = select(DriverProfile).order_by(DriverProfile.created_at)
if status_filter:
    q = q.where(DriverProfile.status == DriverStatus(status_filter))   # line 78-79
rows = (await session.execute(q.limit(limit).offset(offset))).scalars().all()
total = (await session.execute(select(func.count()).select_from(DriverProfile))).scalar_one()
```

So with 900 drivers of whom 12 are `SUSPENDED`, `GET /admin/drivers?status_filter=SUSPENDED`
returns 12 rows and `"total": 900`. The console's pager renders "1–50 of 900" over a 12-row list,
and — worse — keeps offering pages 2..18 that come back empty, which is the failure mode the
`admin/orders.py` docstring calls out in a different guise: a filter that appears to have results
when it does not.

Every sibling list endpoint applies the filter to **both** queries, so this is the odd one out:
`admin/orders.py:145-148` (`for f in filters: q = q.where(f); count_q = count_q.where(f)`),
`admin/refunds.py:37-40`, and `FleetService.list_page` (`fleet_service.py:171-178`). The fix is to
hoist the predicate into a `filters` list and apply it to both, exactly as `admin/orders.py` does.

## NEW-21 — MEDIUM — `driver_deposit_default_hkd` is dead config, and its value is a literal in three places

`Settings.driver_deposit_default_hkd: int = 500` exists at `app/core/config.py:255` and is read by
**nothing**: `grep -rn "driver_deposit_default_hkd" app/ tests/` matches the definition and its
`.pyc` and no call site. The number that actually decides a driver's collateral is written as a
literal in three independent places:

```python
app/services/ledger/ledger_service.py:218   required_hkd=Decimal("500"),      # creates the row
app/api/admin/drivers.py:164                required = ... else Decimal("500") # console fallback
app/api/drivers.py:115                      money_str(Decimal("500"))         # driver app fallback
```

The first is the authoritative one — it is what `ensure_deposit_row` writes — and it ignores the
setting too. So the failure is silent in the direction that matters: an operator sets
`DRIVER_DEPOSIT_DEFAULT_HKD=800`, restarts, and every new driver is still asked for 500, because no
code path consults the setting. The two API fallbacks are for a driver who has no deposit row yet,
so they exist to show progress against the real target; with the literal they can disagree with the
row the moment the row is created differently.

This is the same class as the two `ROUND_HALF_EVEN` `quantize` calls the codebase already fixed in
this file and in `fleets.py` — a duplicated money constant that drifted from its single source —
except the single source here is a setting nobody wired up.

## NEW-22 — HIGH — a compromised admin cannot be evicted: the reset does not revoke, and nothing can deactivate an account

Three facts that only bite together.

**1. The service docstring promises a revocation the body does not perform.**
`AdminAccountService.reset_password` (`app/services/admin/admin_account_service.py:225-244`):

```python
"""Set a new password and revoke everything that was already issued."""
account = await self.get(account_id)
account.password_hash = hash_password(new_password)      # <- the only change to the credential
account.failed_login_count = 0
account.locked_until = None
await self.session.flush()
```

No refresh-token revocation, no access-token epoch bump. "Revoke everything that was already
issued" is simply not what the function does.

**2. The route's explanation for not revoking is false in this codebase.**
`app/api/admin/accounts.py:212-218` answers `{"sessions_revoked": False}` with the comment:

> The access token is a signed JWT with no server-side session store, so the reset cannot revoke
> tokens already in flight.

But there *is* a server-side store: `deps.assert_not_revoked` (`app/core/deps.py:120-134`) is
called from `get_current_user` for **every** principal, admin scope included, and compares `iat`
against the per-account epoch in Redis. `app/api/admin_auth.py:403` (admin logout) already performs
exactly the two calls the reset is missing, and its comment says why:

```python
await svc.revoke_all_for_admin(row.admin_id)
await revoke_user_tokens(request.app.state.auth_redis, admin_id)
# Kill access tokens already in the wild (SEC-18). Without the same key
# `deps.assert_not_revoked` reads, this would be a no-op and the
# operator's 15-minute access token would outlive their logout.
```

`admin_auth.py:321` does the same on refresh-token replay. So the capability is present, exercised,
and documented — in the sibling module.

**3. Nothing can deactivate an admin account.** `admin_accounts.is_active` is written in exactly
one place — `is_active=True` at creation (`admin_account_service.py:177`) — and
`grep -rn "is_active = False\|is_active=False\|values(is_active" app/` returns nothing. The four
routes in `app/api/admin/accounts.py` are list, create, role-change and password-reset; there is no
fifth. Meanwhile `require_admin` (`app/core/deps.py:285-287`) gates every console request on

```python
if row is None or not row.is_active or user.role != UserRole.ADMIN:
    raise HTTPException(403, "admin privileges required")
```

so the guard is correct, fail-closed, and **unreachable** — it can only ever fire through manual
SQL.

**Consequence.** The canonical incident response — "this admin's credentials are compromised, reset
the password" — does not end the session. The refresh cookie stays valid for
`settings.refresh_token_expire_days` days and rotates indefinitely, minting fresh 15-minute access
tokens, while the console reports `sessions_revoked: false` and the service docstring tells the
reader the opposite. The only ways a rogue session ends are the attacker's own choices (logging out,
or replaying a rotated token to trip SEC-17), plus **demotion** — which narrows what the session may
do, because `live_admin_role` (`deps.py:467-487`) re-reads the row, but leaves it authenticated.

**Fix.** Two calls in `reset_password` (or in the route, next to the audit row), mirroring logout,
and `sessions_revoked: True`; plus a route that can set `is_active = False`, since the guard for it
already exists and is the only lever that ends the session *and* the refresh family in one move.

## NEW-13 — line-verified — `DisputeService.resolve` is a lock-free check-then-act

Read at `app/services/admin/dispute_service.py:442-465`:

```python
async with self._session_factory() as session:
    dispute = await session.get(OrderDispute, dispute_id)   # :443  plain SELECT, no FOR UPDATE
    ...
    if dispute.resolution is not None:                      # :452  the guard
        raise BusinessRuleError("this dispute already has a resolution", ...)
    dispute.resolution = res.value                          # :458  the write
    dispute.resolved_by = admin_id
    dispute.resolved_at = now
    dispute.status = ...
    await session.commit()                                  # :462
```

`session.get` takes no row lock, so two FINANCE admins resolving the same case in the same second
both read `resolution is None`, both pass the guard, and both commit — the second silently
overwrites the first, and **both** audit rows (`disputes.py:496-...`) report a successful
resolution. The guard reads as protection and is only a TOCTOU. `set_status` (`:483-495`) has the
same shape.

The refund path in the same codebase shows the intended fix: `with_for_update()` on the read plus
`populate_existing` so the identity map cannot answer from a stale snapshot.

## NEW-23 — LOW — the dispute audit row is committed in a different transaction from the decision

Every handler in `app/api/admin/disputes.py` mutates through `DisputeService(session_factory)`,
which opens its own session and commits (`dispute_service.py:123/360/397/442/483`), and then opens a
**second** session for the audit row:

```python
service = DisputeService(session_factory)
dispute = await service.resolve(...)          # commits here
session = session_factory()                   # <- a different transaction
try:
    await record_audit(session, ...)
    await session.commit()
finally:
    await session.close()
```

Same shape at `:250-256` (create), `:322-343` (assign), `:378-407` (message), `:496-...` (resolve).
`admin/drivers.py` does the opposite — one session for the state change *and* its audit row — so the
two admin modules disagree about whether an audit row is part of the transaction it describes.

The window is small and the failure mode is not a missing audit row alone: if the audit commit
raises, the client sees a 500 for a decision that was already durable, and a retry answers
`DISPUTE_ALREADY_RESOLVED` (400). An operator reading only the wire sees a failed action and a
succeeded action in that order, for one request.

## NEW-1 / NEW-2 — line-verified — the mobile boundary and error catalogue mirror symbols the server deleted

### NEW-1 (HIGH) — the mirror is of an abandoned constant, and its docstring cites a symbol that no longer exists

`mobile/lib/core/location/location_service.dart:11-26`:

```dart
/// The backend bounds every coordinate to `lat 22.1–22.6, lng 113.8–114.5`
/// (`_HK_BOUNDS` in `app/api/orders.py`, repeated in `ws.py`), and rejects
/// anything outside with a 422 or `{"type":"error","code":"BAD_LOCATION"}`.
static const double minLat = 22.1;   static const double maxLat = 22.6;
static const double minLng = 113.8;  static const double maxLng = 114.5;
static bool isInHongKong(double lat, double lng) =>
    lat >= minLat && lat <= maxLat && lng >= minLng && lng <= maxLng;
```

`grep -rn "_HK_BOUNDS" app/` returns **nothing** — the symbol the comment names does not exist in
the backend. What exists is:

```python
app/core/hk_bounds.py:80    HK_BBOX = ((22.13, 22.60), (113.80, 114.45))   # cheap reject only
app/core/hk_bounds.py:282   def is_in_hong_kong(lat, lng) -> bool:          # 8-polygon ray cast
```

and that module's own docstring says why the box was retired: it "**contains Shenzhen**", verified
against Futian 22.5410/114.0540, Luohu 22.5480/114.1230 and Bao'an 22.5550/113.8830, and as an app
gate "it is fatal: the users the check exists to stop would be the ones it admits."

So the client's box is **wider than the server's** in both directions (`22.1 < 22.13`,
`114.5 > 114.45`) and, more importantly, it is a box at all. `isInHongKong(22.5410, 114.0540)`
returns **true** on the device for a coordinate in Shenzhen, where `is_in_hong_kong` returns
**false**.

Four call sites act on that answer (`grep -rn "isInHongKong" mobile/lib/`):

| site | what the client decides from it |
|---|---|
| `mobile/lib/features/driver/driver_active_trip_screen.dart:132` | whether to send the location tick |
| `mobile/lib/features/driver/driver_active_trip_screen.dart:153` | whether to **skip** the tick |
| `mobile/lib/features/shared/map_panel.dart:167` | `outside = !isInHongKong(...)` → what the map tells the user |
| `mobile/lib/features/passenger/request_ride_screen.dart:38`, `driver_jobs_screen.dart:33` | hold the service that answers it |

The server still refuses the coordinate, so the invariant holds and this is not a breach — it is a
parity defect with a user-visible face: a driver standing in Shenzhen is told locally that the
position is fine, sends it, and meets a server refusal; a passenger at Shenzhen Bay Port's Hong Kong
Port Area (admitted on purpose — see `hk_bounds.py:105-142`) is told locally that they are outside
Hong Kong.

### NEW-2 (MEDIUM) — the error-code catalogue is pinned by a green test to a code the server never sends

Server side, the refusal is `OUTSIDE_HK` — `app/api/ws.py:191` emits
`{"type": "error", "code": "OUTSIDE_HK"}`, and `app/core/service_area.py:34` defines
`REASON_OUTSIDE_HK = "OUTSIDE_HK"` for the HTTP path.

Client side, `OUTSIDE_HK` appears **nowhere** in `mobile/`. The catalogue is
`mobile/lib/models/trip.dart:105`:

```dart
'BAD_LOCATION' => '座標不在香港範圍內',
```

with the documented code list at `:93` reading "`BAD_LOCATION`, `DRIVER_NOT_ACTIVE`". So the one
string that would explain the refusal — "座標不在香港範圍內" — is present in the app and
**unreachable**: it is keyed to a code the backend stopped emitting, and a real refusal falls
through to whatever the default branch shows.

The reason this is MEDIUM rather than LOW is `mobile/tool/run_tests.dart:523,531`, which asserts it:

```dart
'BAD_LOCATION',
expect(const TripErrorEvent(code: 'BAD_LOCATION').messageZh, '座標不在香港範圍內');
```

The mobile suite is therefore **green while pinning a code the backend never emits** — a false green
in exactly the place the project's contract discipline (`mobile/test/fixtures/`, compile-time TS
mirrors) exists to catch. A fixture captured from a real refusal would have made this fail.

### Refuted: "`admin_accounts.role` is an unconstrained VARCHAR"

Raised by reading `app/models/admin.py:143-147` in isolation — the column is
`Mapped[str] = mapped_column(String(16), default=DEFAULT_ADMIN_ROLE.value, server_default="SUPPORT")`,
it is deliberately **not** in `2e276a320b35`'s 18 `_ENUM_CHECKS`, and `AdminAccount` is the table
`require_admin` reads on **every** console request, so a bad value would take the whole console down.

It is handled, and the handling is better than a CHECK would be:

```python
app/models/admin.py:184-197
    def admin_role(self) -> AdminRole:
        """The stored role as an enum, failing *closed* on an unknown value.
        ...
        `SUPPORT` is the floor, so an unrecognised role collapses to the least
        authority rather than the most. Raising instead would turn a data
        problem into an outage for every request that touches this account."""
        try:
            return AdminRole(self.role)
        except ValueError:
            return DEFAULT_ADMIN_ROLE
```

`require_role` (`deps.py:449`) reaches the role **only** through that property, and
`DEFAULT_ADMIN_ROLE` is `AdminRole.SUPPORT`, rank 0 (`admin.py:105`, `admin.py:95-99`) — so an
unrecognised value cannot outrank anything, cannot reach a single guarded route, and does not 500.
The VARCHAR choice buys "add a role without a migration" and pays for it with one fail-closed
coercion at the single read site. Recorded here so a later pass does not re-raise it as an
"unconstrained enum column" and "fix" it into a CHECK constraint plus a 500.

Note the contrast this makes with N-1: the *value* is handled defensively, while the *ordering* is
the thing that carries the policy defect.

### NEW-2 (MEDIUM) — the compounding chain, read end to end

The code mismatch is not cosmetic. Tracing one refused tick through
`mobile/lib/features/driver/driver_active_trip_screen.dart`:

```dart
:145  void _pushTick() {
:148    if (position == null || channel == null || _closing) return;
:151    // Guard the server's own bounds: a tick outside Hong Kong earns
:152    // {"type":"error","code":"BAD_LOCATION"} and burns a token for nothing.
:153    if (!LocationService.isInHongKong(position.latitude, position.longitude)) return;
:156    channel.pushLocation(lat: position.latitude, lng: position.longitude);
```

1. `:153` asks the **mobile** box, which admits Shenzhen (NEW-1) — so for a driver just over
   the border the guard **passes** and the tick **is** sent. The stated purpose of the guard
   ("does not burn a token for nothing") fails at exactly the boundary it exists for.
2. The server answers `{"type":"error","code":"OUTSIDE_HK"}` (`app/api/ws.py:191`).
   `OUTSIDE_HK` has **zero matches in `mobile/`** — it is not in the catalogue
   (`mobile/lib/models/trip.dart:101-108`), so the driver sees the fallback branch,
   `'位置推送失敗（OUTSIDE_HK）'` (`:107`): a raw backend code in a Chinese sentence.
3. `mobile/lib/models/trip.dart:99` — `bool get isFatal => code == 'DRIVER_NOT_ACTIVE';`
   `OUTSIDE_HK` is **not** fatal, so `driver_active_trip_screen.dart:87-89` does **not** cancel
   `_pushTimer`. The driver keeps pushing every `AppConfig.locationTickInterval`.
4. Each refused push burns the socket's rate-limit budget — the file's own docstring (`:23-27`)
   records the server's `ws_tick_burst` (5) and `ws_ticks_per_second` (2) and notes that "a
   `RATE_LIMITED` error would otherwise be the normal case". So the loop drives the socket into
   `RATE_LIMITED`, whose message is `'位置更新過於頻繁'` (`trip.dart:103`) — **a misdiagnosis**,
   and also non-fatal, so the loop continues.

Net effect: a driver near the boundary sees a persistent, wrong error, their live position
**silently stops updating** for the passenger (every tick is refused), and the visible message blames
frequency rather than location. For a live-tracking product that is the failure mode worth fixing
first, and the fix is small: map `OUTSIDE_HK` in the catalogue and make it fatal (or at least stop
the timer), and mirror `is_in_hong_kong` rather than the retired box.

### NEW-1 — CORRECTION to the record above (the symbol is gone; the values are not)

An earlier entry in this file says the server "deleted" the box. That is true of the **symbol**
and false of the **values**, and the distinction is the whole finding. The box `lat 22.1-22.6,
lng 113.8-114.5` is still live in `app/` at four sites, and at each one it is *documented as a
cheap pre-filter with the polygon check behind it*:

| site | what the box does there | the gate behind it |
|---|---|---|
| `app/api/orders.py:79-82` | `OrderCreateIn` pickup/dropoff field bounds | `require_in_hong_kong` at `:185-186` |
| `app/api/tracking.py:39-40` | `LocationIn` field bounds | `require_in_hong_kong` at `:64` |
| `app/api/ws.py` (tick path) | no box at all | `is_in_hong_kong(lat, lng)` at `:189` |
| `app/api/orders.py:204-205` | `/orders/nearby` query bounds | **none — read-only geo query** |

`require_in_hong_kong` has exactly three call sites (`orders.py:185`, `orders.py:186`,
`tracking.py:64`) plus the WS polygon check, so every write path that publishes a coordinate is
polygon-gated. The server is sound.

The defect is therefore **the mobile promotes the pre-filter to the authoritative gate**:
`LocationService.isInHongKong` is the *only* check the client has, and it is built from the same
numbers the server keeps deliberately weaker than the truth. `mobile/lib/core/location/location_service.dart:12`
even names the right file — "`_HK_BOUNDS` in `app/api/orders.py`" — for a symbol that no longer
exists, while the values it mirrors are alive two lines into `OrderCreateIn` under a comment
explaining they are only a first pass.

Severity stays HIGH as a parity defect: the client's answer to "is this coordinate in Hong Kong"
is wrong in the same direction as the box (it admits Shenzhen), and the app acts on it in four
places (`driver_active_trip_screen.dart:132`, `:153`, `map_panel.dart:167`, and the location
providers in `driver_jobs_screen.dart:33` / `request_ride_screen.dart:38`). The server refuses the
coordinate afterwards, which is why this is not a breach — it is a client that confidently says
"yes" and then gets told "no", with the consequences traced under NEW-2 below.

---

### NEW-23 — LOW — the dispute audit row is committed in a different transaction from the decision

Every handler in `app/api/admin/disputes.py` mutates through a service that owns its own session,
then writes the audit row through a **second** session:

```python
:250  session = session_factory()          # for the audit row
:251  try:
:252      service = DisputeService(session_factory)   # opens its own session, commits itself
:253      dispute = await service.open_case(...)
...
:255      record_audit(session, ...)        # separate transaction
```

`DisputeService` opens and commits per method (`dispute_service.py:127-129`, `:442-444`,
`:466-468` — `async with self._session_factory() as session: ... await session.commit()`), so the
decision is durable before the audit row is attempted. A failure between the two commits leaves a
resolved dispute — and, for a `moves_money` resolution, an approved money decision — with **no
audit row**, and the operator sees a 500 for an action that actually succeeded. Retrying then
returns `DISPUTE_ALREADY_RESOLVED` (400), so the operator is told the action failed twice.

This is an inconsistency rather than the house style: `app/api/admin/refunds.py:79-92` and
`app/api/admin/drivers.py` both call `record_audit(session, ...)` with the **same** session the
service wrote through, so the audit row and the mutation commit together. `disputes.py` is the
only admin module that splits them, and it splits them in the one module where the decision can
carry money.

The clean fix is to have `DisputeService` accept a session (as `RefundService` does) instead of a
factory, so the route can hold one transaction for both writes.

### NEW-24 — MEDIUM — the mobile suite has no standard runner, and its fixture set cannot see the WS refusal

`mobile/test/` contains **no Dart test files at all** — only `fixtures/*.json`. The mobile suite is
two hand-rolled tools under `mobile/tool/`:

| file | lines | what it is |
|---|---|---|
| `run_tests.dart` | 1,647 | 133 `test()` in 18 `group()`s — a bespoke harness with a hand-written `expect`/`expectTrue`/`expectFalse` |
| `verify_contract.dart` | 736 | decodes every captured fixture with the **real** models; fixture-driven |

The reason is documented at `run_tests.dart:1-25` and is legitimate: `flutter test` cannot start on
this machine (the Dart VM cannot spawn piped subprocesses — Windows `ERROR_PIPE_BUSY`), and the file
carries an explicit migration list for when it can. So this is honest engineering, not a cover-up.
Three consequences are still real and belong in the record:

1. **The unit half asserts the author's assumptions, not captured bytes.** `run_tests.dart:531`
   pins `'BAD_LOCATION'` as a known code and `:537` asserts it renders `'座標不在香港範圍內'`.
   Both are green today and always will be — while the server's refusal code is `OUTSIDE_HK`
   (NEW-2 above). A stale assumption in this file is *unfalsifiable*: nothing in the repo compares
   the catalogue to the server's vocabulary.
2. **The fixture half has a hole exactly where the drift is.** `mobile/test/fixtures/` holds
   `ws_driver_ack.json`, `ws_location_tick.json` and `ws_read_only_error.json` — and **no frame for
   the refusal**. `verify_contract.dart` can only check what `gen_mobile_fixtures.py` captured, so
   the one code the server sends when it rejects a coordinate has never been captured, decoded, or
   asserted. The `OUTSIDE_HK` mismatch is therefore invisible to *both* halves of the mobile suite.
3. **No machine-readable report.** The backend's gate produces JUnit XML that can be hashed and
   verified; the mobile gate prints to stdout and exits non-zero. "Mobile tests pass" cannot be
   independently checked the way "pytest passed" can.

To be fair to the file: `verify_contract.dart:1-45` is the strongest thing in the mobile tree. It is
genuinely fixture-driven, it cross-references its Python counterpart
(`scripts/verify/audit_response_models.py`), and its docstring records three real defects it caught
that reading the source did not — including the `BusinessRuleError`-subclasses-`ValueError`
catch-order bug that the backend's own migration docstrings also describe. The gap is in *what the
generator captures*, not in the tool's design.

---

## Verified green by hand (not taken from a report)

- **`scripts/verify/audit_response_models.py`** — run directly:
  `68 fixture blocks checked`, `93 operations with a response_model`,
  `OK — every fixture key survives its response_model`, exit 0. The 5 schemas it lists as
  "declared but not reachable" are each explained in its own allow-list
  (`ChallengeOut` = "declared for symmetry; never returned", `AdminDepositOut` = "base class of
  `AdminDepositOut`Detail"), so the list is accounted for, not a gap.
- **`tests/` has no disabled tests.** `grep -rn "pytest.mark.skip\|xfail\|skipif" tests/` finds no
  markers; the only skip is a documented guard at `test_security_hardening.py:833-836`
  (`pytest.importorskip("uvicorn.middleware.proxy_headers")` → `pytest.skip("uvicorn internals
  changed — re-verify SEC-31 by hand")`). The last full run reports `skipped=0`, so it is currently
  executing, not skipping. The residual risk is that SEC-31 can stop being covered silently if
  uvicorn's module layout moves — the file says so itself, so it is a known, accepted risk.

### NEW-25 — MEDIUM — a negative deposit balance renders without a sign, on both clients

Arrears are legal by design and are a first-class state in this product:
`app/services/ledger/ledger_service.py:167` — "Negative balances are allowed (arrears): a penalty
may exceed the deposit"; `docs/ARCHITECTURE.md:222` — "欠款（arrears）是刻意合法的"; and
`app/api/schemas/admin.py:471-472` — "those drivers are charged, they simply go into arrears".

Both clients render a balance through a formatter that drops the sign by default:

| deliverable | site | renders |
|---|---|---|
| console | `admin-web/web/src/components/primitives.tsx:197-213` | `formatMoney(value, sign = false)` — `Math.abs(amount)` for the magnitude, sign re-added **only** when `sign` is true |
| console | `DriverDetailPage.tsx:402`, `:432` (deposit panel), `:230` (ledger `balance_after_hkd`) | `<Money value={…} />` — no `sign` |
| mobile | `mobile/lib/core/format/money.dart:81` (`hkd`) vs `:106` (`signedHkd`) | `MoneyText(x)` defaults to `hkd` — unsigned |
| mobile | `driver_onboarding_screen.dart:229` (目前餘額), `driver_earnings_screen.dart:111-113` (按金餘額, `displaySmall`) | `MoneyText(deposit.balanceHkd)` — no `signed: true` |

So a driver whose deposit balance is `-200.00` is shown **`HK$200.00`**. On the mobile that is the
driver's own view of their own debt, directly above the line "每週服務費會自動由此餘額扣除" — they
are told they hold HK$200 when they owe HK$200. In the console it is the money panel an operator
reads before deciding on a top-up.

This is not an oversight in the formatter — it is careful work that is not wired up for levels.
`primitives.tsx:173-183` documents the subtlety at length ("a ledger deduction is stored
**negative** … `−HK$200.00` double-negates into a plus", with U+2212 chosen for column alignment),
and `DriverDetailPage.tsx:227` uses it correctly through a local `SignedMoney` wrapper (`:441-442`)
for the **amount** column. The `sign` prop is simply never applied to the **balance** columns, where
the value can also be negative.

The asymmetry is the tell: both clients sign the *delta* and not the *level*, so the ledger reads
correctly while the balance does not. The fix is one prop at each of the five sites (or a
`Balance` wrapper beside `SignedMoney`).

---

### NEW-26 — LOW — the console's deposit meter has no floor, and the mobile's does

The same calculation, written twice, bounded differently:

```tsx
// admin-web/web/src/pages/DriverDetailPage.tsx:396
const progress = required > 0 ? Math.min(100, Math.round((balance / required) * 100)) : 0;
```
```dart
// mobile/lib/features/driver/driver_onboarding_screen.dart:236-240
value: deposit.requiredHkd.asDouble <= 0
    ? 0
    : (deposit.balanceHkd.asDouble / deposit.requiredHkd.asDouble).clamp(0.0, 1.0)
```

The mobile clamps **both** ends; the console caps only the top. For a driver in arrears
(`balance = -200`, `required = 500`) the console computes `progress = -40` and then:

* writes `style={{ width: '-40%' }}` (`:428`) — an invalid CSS declaration the browser drops, so the
  meter fill falls back to whatever the base rule says rather than to 0; and
* prints `· -40%` as the caption text (`:433`).

Reachable through the same legal arrears state as NEW-25, on the same card. One `Math.max(0, …)`
closes it, and matching the mobile's `clamp(0, 1)` is the parity-correct form.

## False positives I generated and killed (recorded so the method is auditable)

Two candidates in this pass were real-looking and wrong. They are logged because the reasoning that
killed them is the same reasoning that validates the findings above.

1. **"The console computes money with floats."** True as far as it goes —
   `DriverDetailPage.tsx:76`, `:500` and `FleetDetailPage.tsx:419` all do `Number(a) - Number(b)` /
   `String(Number(x))` on money strings, while `mobile/lib/core/format/money.dart` was explicitly
   rewritten to integer cents for exactly that reason. But it is **not** a defect: the console does
   no *arithmetic on the result* — `shortfall_hkd` is computed server-side with `Decimal` and merely
   re-parsed for display — and `FleetDetailPage`'s one subtraction is a display-only "saving" that
   passes through `Math.abs(amount).toFixed(2)` (`primitives.tsx:207`), which absorbs a ~1e-14
   representation error far below the 0.005 rounding threshold. `String(Number("0.20"))` giving
   `"0.2"` is numerically lossless, so the value sent to the API is unchanged. Recorded as a
   **latent fragility** (money maths in floats, correctness resting on `toFixed`), not a finding.
2. **"`sign` is never passed anywhere in the console."** `grep -rn "sign={" admin-web/web/src/`
   returns zero matches — and that is a bad pattern, because the one call site uses the bare boolean
   attribute: `DriverDetailPage.tsx:441-442` defines `SignedMoney` → `<Money value={value} sign />`,
   used at `:227` for the ledger amount column. The prop *is* wired for deltas; it is missing for
   levels (NEW-25). A grep that encodes a syntactic guess about how a prop is passed is not evidence.

### NEW-27 — MEDIUM — the console's wire mirror has no automated contract check, and names an authority that does not exist

Of the three deliverables, two have a mechanical guard against server drift:

| deliverable | guard |
|---|---|
| backend | `pytest` (1,071 tests) + `scripts/verify/audit_response_models.py` |
| mobile | `mobile/tool/verify_contract.dart` (fixture-driven, real decoders) + the same audit script |
| **console** | **none** — `package.json` scripts are `dev`, `build` (`tsc --noEmit && vite build`), `preview`, `test` (`vitest run`), `typecheck` |

`admin-web/web/src/api/types.ts` is 949 lines declaring 51 interfaces, and its own header says it is
"**Hand-written** from `app/api/admin.py`, which is the authority". That path **does not exist** —
the admin routes are the `app/api/admin/` package plus `app/api/admin_licence.py`, `admin_auth.py`
and `admin_analytics.py`. So the file names a non-existent authority for the contract it mirrors
(the same staleness class as `_HK_BOUNDS` in NEW-1).

`tsc --noEmit` proves the console is *self-consistent*; it cannot prove the console agrees with the
server. A server-side field rename therefore compiles clean and fails at runtime as `undefined` /
`—` in a cell. The file documents having shipped exactly that bug:

> `types.ts:24-27` — "Declaring only `phone_masked` — as this type used to — is what made the sidebar
> render `—` for every admin, because the field it read is never present on an admin."

That is a real defect found by looking at a screen, fixed in the type, and recorded — with no guard
added. `endpoints.ts:1-7` does better for *paths*: "The paths here are the contract;
`tests/test_fleets.py` and `scripts/dev/gen_mobile_fixtures.py` pin the same ones from the other
side." I checked that claim mechanically against all 92 server routes and it holds — every one of the
console's 43 paths resolves to a real route. The gap is the **response field shapes**, which nothing
pins.

The cheapest closure is to extend `scripts/dev/gen_mobile_fixtures.py`'s captured JSON into the
console's world: either generate `types.ts` from the fixtures, or add a `types.test.ts` that decodes
each fixture with the declared interface (the console already runs `vitest`, so no new tooling is
needed — only fixtures it currently has no copy of).

---

### NEW-28 — LOW — the documented TOTP recovery endpoint has no caller in any deliverable

`POST /api/v1/admin/auth/totp/enrol` exists (`app/api/admin_auth.py:233-263`) and its docstring
states its purpose plainly:

> "Re-issue enrolment material for an admin who never finished setup. Requires the password again.
> `/login` already returns the material on the first login; this exists for the case where that
> response was lost (tab closed, network dropped) and the Redis-held secret has expired."

Nothing calls it:

* the console's `endpoints.ts` has only `/totp/enrol/confirm` (`:91`). `LoginPage.tsx:89-91` takes
  the enrolment material out of the **login response** (`body.next === 'enrolment_required'` →
  `body.enrolment`), so the console renders its QR (`:357`, `QRCodeSVG`) without ever needing the
  recovery route — and has no UI for the case the route was written for;
* `scripts/ops/enrol_admin_totp.py` — the one tool whose whole job is TOTP enrolment — walks
  `/login` → `/totp/enrol/confirm` (its own docstring, `:36`, `:97`), not `/totp/enrol`;
* `tests/api/test_security_hardening.py:463` lists it, but as a member of
  `_PRE_AUTH_PATHS` — an assertion that it is exempt from the access-token guard, not a call.

So the recovery path is reachable only with curl: documented in `docs/ADMIN_AUTH.md:58`, tested for
its guard exemption, and unused by the console, the mobile, and the ops scripts. Either the console
should offer the retry (one call from the `enrol` step, which is already implemented and already has
the secret-rendering UI), or the route should be deleted and the doc claim removed. Today an admin
whose first-login response was lost has no in-app way back in, which is the exact scenario the
endpoint was built for.

---

## Refuted in this pass (recorded so they are not re-raised)

- **`attach_document` does not enforce that the object exists.** `StorageService.head()`
  returns `None` for a genuine 404 *and* `DevStorageService.head()` returns `None` by
  construction, so `licence_service.py:371-383` skips the size/type verification whenever it
  gets `None` — a declared-but-never-uploaded key is accepted and a `DriverDocument` row is
  created. **Refuted**: the gate is at the right place instead — `licence_review_service.py:356-371`
  refuses to approve when storage *is* configured and any document's `head()` is `None`
  (`"cannot approve: the uploaded documents are not in storage"`), and production cannot run
  silently unconfigured because `presign_document` calls `_require_configured()` and raises.
  The leniency at attach time is deliberate and documented ("the flow stays exercisable, and the
  risk is confined to an environment with no bucket to abuse").
- **`FleetService.update` name clash** — kept as NEW-14, not refuted; noting here only that the
  `create()` path shows the intended behaviour, so the fix is a two-line copy, not a design
  question.

### Admin auth — `app/services/admin/admin_auth_service.py` (789 lines) + `app/api/admin_auth.py`

Notable because it is the highest-privilege path in the system. Verified sound:

- Three-step state machine with a **purpose-scoped challenge token** that carries neither `sub`
  nor `role`, so `principal_from_token` can never accept it as an access token
  (`_challenge_token`, `_resolve_challenge`) — the failure is structural, not a check.
- `issue_admin_access_token` hardcodes `role=ADMIN` and carries `admin_role` for display only;
  `require_role` re-reads the live row, so a demotion lands on the next request.
- Failed-login counters and lockout live **in the account row, not Redis**, so flushing Redis
  cannot reset a brute-force lock (`_register_failure`).
- `_run()` in the router **commits before raising** — without it `get_session`'s rollback
  discards the increment, the lockout and the audit row. The docstring records the original
  symptom ("an account that accepted unlimited wrong passwords"). Correct, and the
  non-obvious part of the design.
- **Refuted candidate:** I suspected a username-existence oracle, since a lockout is a
  per-account state that only real accounts can reach. It is already closed: `AdminAccountLocked`
  is mapped to **401, not 429**, precisely so a locked account cannot be told apart from a
  throttled IP, and `test_lockout_does_not_expose_is_active` pins it. The 429/401 split is by
  exception type, never by message text.
- **Refuted candidate:** I suspected the plaintext-shaped `verify_totp` name collision between
  the service method and the imported helper. Python resolves the method body to the module
  import, so the service calls the right function.
- One honest note, not a defect: first-login enrolment means a leaked password on an
  **never-enrolled** admin account is sufficient to take it over (the holder enrols their own
  device from the returned secret). That is inherent to self-service enrolment, which the
  docstrings defend at length; the second factor only protects accounts that completed it.

### Also verified clean in this pass

- `app/core/money.py` — `ROUND_HALF_UP` everywhere, no `ROUND_HALF_EVEN`; `money_str` 2dp,
  `meter_str` 1dp, `ratio_str` 2dp.
- `app/services/ledger/ledger_service.py` — append-only, `SELECT ... FOR UPDATE` on the deposit
  row, namespace-prefixed references (`grant:`, `weekly:`, `refund:`, `adj:`), unique-index
  backstop on `IntegrityError`, and an idempotent-replay check that compares type and amount.
- `app/services/ledger/settlement_confirm.py` — HMAC-SHA256 + `compare_digest`, purpose
  separation, `exp` check, claim binding.
- `app/core/config.py` — fail-closed prod validation (APP_ENV allow-list, dev-secret
  denylist + entropy floor, prod refuses plaintext origins / empty secrets).
- `app/core/client_ip.py` — counts X-Forwarded-For **from the right**, and ignores the header
  entirely when `TRUSTED_PROXY_COUNT == 0`.
- `app/core/passwords.py` (except the NFC mirror issue, NEW-3) and `app/core/totp.py`
  (except the entropy claim, NEW-12).
- `app/api/ws.py` (except NEW-10), `app/services/order/grab_service.py` (except NEW-11),
  `app/services/order/geo_service.py`, `app/services/order/state_machine.py`,
  `app/services/order/fare_calculator.py` — all three tariff ladders verified reachable
  exactly (urban 35 × $2.1 = $73.50 → $102.50; NT 30 × $1.9 = $57.00 → $82.50;
  Lantau 90 × $1.9 = $171.00 → $195.00), and waiting-time jumps are correctly summed into the
  same ladder rather than charged at a separate rate.

### Refuted / accepted risks (recorded so they are not re-raised)

- `distance_km` is client-supplied and drives the frozen fare snapshot
  (`order_service.py:81,101`), bounded only to `(0, 100]`. Checked every reader of
  `fare_json`/`total_fare`: **none of them is financial** — settlement, the ledger and refunds
  never touch it. This is the documented Cap. 374D "reference only" position, so it is an
  accepted risk, not a vulnerability.
- The same applies to `discount_percent` (`schemas/order.py`, client-supplied 0–100 with no
  server-side promo validation) — and no shipped passenger UI sets it, so it is a latent
  capability rather than a live hole.

- `app/core/money.py` — ROUND_HALF_UP on every formatter; 1 dp vs 2 dp split is real
  and justified; `quantize_money`/`ratio_str` close the half-even default.
- `app/services/ledger/ledger_service.py` — append-only, `SELECT … FOR UPDATE`,
  namespaced references, `DuplicateReferenceError` + partial unique index backstop.
- `app/services/ledger/settlement_service.py` — preview and run share
  `_eligible_driver_ids`; SEC-13 `tampered` branch; per-driver session/commit;
  `except DuplicateReferenceError` kept as its own clause.
- `app/services/ledger/settlement_confirm.py` — HMAC + `compare_digest`, purpose,
  exp, claim binding; TTL stated to the console.
- `app/core/client_ip.py` — XFF ignored unless `TRUSTED_PROXY_COUNT > 0`, and counted
  from the right; five duplicated copies consolidated.
- `app/core/deps.py` — DB-first everywhere (`require_active_user`, `require_admin`
  re-reads the row and demands TOTP enrolment); scope is an allow-list of one.
- `app/core/rate_limit.py` — fixed-window documented, expiry set once.
- `app/core/token_revocation.py` — fails **open** on Redis loss, explicitly reasoned
  (15-minute expiry is the backstop).
- `app/core/masking.py`, `app/core/service_area.py`, `app/core/security.py`
  (algorithm pinned; no default secret).
- `mobile/lib/core/network/wire.dart`, `api_exception.dart`, `turnstile*.dart`,
  `models/{order,trip,fare,identity,ledger,refund,driver,enums}.dart` — consistent
  use of the wire helpers; unknown WS types and unknown enum values degrade instead
  of throwing.
- Server datetimes are all tz-aware, so the client's `.toLocal()` is correct.
- Client mirrors of WS close codes (4401/4403/4404/4408), tick rate (2/s, burst 5),
  the HK phone pattern and the mask shape all match the server.
- **`alembic/versions/` — 12/12, chain verified linear.** Every revision id appears
  exactly once as a `down_revision`, there is a single head (`b7d4e1c9a3f2`), and all
  twelve `downgrade()` bodies contain real `op.*` calls. *Honest scope:* the downgrades
  were checked for **presence, not executed** — running them would need a scratch
  database, and the one that matters (`b7d4e1c9a3f2`) documents that it fails loudly on
  duplicate claims by design. Sub-agent `models_schemas_migrations.md` re-audits the
  eleven not read here line-by-line.
- **`b7d4e1c9a3f2_claimed_phone_and_reviewer_expiry.py` — read in full, and it is the
  best-reasoned migration in the tree.** It drops the plain UNIQUE on
  `users.phone_e164` *because* uniqueness there was a denial-of-registration vector
  (a phone number is public, so the first claimer would lock out the real owner), and
  puts the guarantee back as a **partial** unique index
  (`uq_users_phone_e164_verified … WHERE phone_verified_at IS NOT NULL`) where it
  actually means something. The module docstring states the downgrade hazard instead of
  hiding it. No finding.
- **The partial index's contract is honoured at both lookup sites that depend on it** —
  this is the part worth having checked, because dropping a UNIQUE is exactly the change
  that turns a later `scalar_one_or_none()` into a 500:
  - `app/services/auth/otp_service.py:157-167` — the secondary OTP login filters
    `phone_verified_at.is_not(None)`, i.e. precisely the index predicate, so at most one
    row can match, and the docstring says so in as many words (".first() is not a
    tie-break between candidates; it is what makes the query total for the type
    checker").
  - `app/services/auth/phone_binding_service.py:115-125` — `_assert_unclaimed` filters
    the same predicate plus `User.id != user.id`, so `scalar_one_or_none()` cannot see
    two rows; the race is then caught a second time by `except IntegrityError` at
    `:100`, which turns a lost race into a `BusinessRuleError` rather than a 500.
  Pre-check *and* constraint backstop, both present. Verified clean.

## NEW-29 — MEDIUM — `.env.example` advertises a bind-address control that no code reads

`.env.example:7` ships `APP_HOST=0.0.0.0`, three lines above the real control at `:14`
(`APP_BIND_IP=127.0.0.1`, carrying the SEC-31 comment). Nothing reads `APP_HOST`:

- `Settings.app_host` / `Settings.app_port` (`app/core/config.py:44-45`) have **zero
  readers** — found by sweeping every field declared in `Settings` against `app/`. The
  container hardcodes the bind instead: `docker-compose.yml:92`
  `uvicorn … --host 0.0.0.0 --port 8000`.
- `docker-compose.yml:88` publishes `${APP_BIND_IP:-127.0.0.1}:${APP_PORT:-8000}` — so
  `APP_PORT` *is* honoured (by compose, not by the app) while `APP_HOST` is honoured by
  nobody.

**Consequence** — the operator who wants the API on all interfaces edits the line that
looks like the control and gets nothing. The operator whose proxy lives on another host
sets `APP_HOST=0.0.0.0`, watches the container come up clean, and has a loopback-only
bind with no error anywhere. The direction is fail-*safe* (the SEC-31 default holds),
which is why this is MEDIUM rather than higher — but a security-relevant setting that
silently ignores its documented name is how the next misconfiguration gets written.

**Fix** — delete `APP_HOST`/`APP_PORT` from `.env.example:7-8` and the dead
`app_host`/`app_port` fields, or point the example at `APP_BIND_IP`. One name, one
meaning.

## NEW-30 — LOW — `turnstile_site_key` is documented as "served to the client"; nothing serves it

`app/core/config.py:216` — `turnstile_site_key: str = ""  # served to the client; not a
secret`. The comment is a contract, and it is unmet: every `site_key` in `app/` outside
`config.py` is exactly one hit, `TURNSTILE_TEST_SITE_KEY`
(`app/services/infra/human.py:52`), which is a test constant. No endpoint, no response
model, no bootstrap payload carries the value.

The secret half *is* wired (`human.py:116`), so the server can verify a challenge — it
just never hands a client the site key needed to raise one. Harmless today, because the
clients carry their own build-time key the same way
`mobile/lib/core/config/app_config.dart:27` carries the Maps key, so this is drift in a
comment rather than a broken flow. Worth fixing because "served to the client" is
exactly the kind of sentence the next reader trusts.

## Refuted in the dead-config sweep

Sweeping **every** field declared in `Settings` for readers turned up four more
zero-reader names. Three are honest backlog rather than defects, and the sweep is only
worth anything if that is written down:

- `fcm_credentials_json` (`config.py:183`) — dead, and `get_fcm_provider()`
  (`app/services/infra/notify.py:134`) has **zero callers** across `app/`, `tests/` and
  `scripts/`. Push is documented as unbuilt in three places, though
  (`docs/WORK_SUMMARY.md:118`, `docs/DEPLOYMENT_REQUIREMENTS.md:193` — "無推送",
  `docs/DEPLOY_TARGET_DECISION.md:141`), and the mobile carries no Firebase dependency
  at all. A stub sitting behind an honest "not built yet" is not a finding. **INFO.**
- `google_maps_api_key` (`config.py:178`) — dead, and labelled in place:
  `# P2-3: route/distance integration, not wired yet`. Same verdict. **INFO.**
- `allow_dev_otp`, `allow_reviewer_account` — flagged as zero-reader *within `app/`*,
  but read by `tests/api/test_security_hardening.py:85` and
  `scripts/ops/create_reviewer_account.py:156`. That is the sweep's scope limit, not a
  defect. **Refuted.**
- `postgres_host/port/user/password/db` — read by `scripts/ops/db_backup.py:142` and
  `scripts/verify/security_probe.py:57`. **Refuted.**

## Open
- `app/core/hk_bounds.py` rest of the polygon (only the docstring + gate read so far).
- `app/core/{config,middleware,exceptions,logging,db,totp,admin_cookies}.py`
- mobile `data/`, `state/`, `features/`, `widgets/`
- admin-web `src/` (47 files) — AC-06…13 unverified
- `tests/` (45 files, 17,439 lines)
- `alembic/versions/`

## 2026-10-05 migration-chain deep scan (sibling coordination)

After another agent committed Phase-2 fixed-fare/premium backend (`a2b9f9f`, `7a06bef`),
I re-scanned the Alembic chain and SQLAlchemy models end to end. Three migration defects
were real and fixed in `69c4484`:

- `5c8b2f0a1e43` used `sa.JSONB()` with no JSONB import and built a non-unique
  `uq_premium_destinations_code` plus a second `ix_..._code`; the model declares
  `unique=True, index=True`, which SQLAlchemy renders as one unique `ix_..._code`.
  Migration now imports `JSONB`, creates the unique index once, and drops only that
  index in downgrade. **CONFIRMED.**
- `5c8b2f0a1e43` omitted `uq_driver_payment_method` unique constraint and
  `ix_driver_payment_methods_driver_profile_id` that the model declares; both are now
  in upgrade/downgrade. **CONFIRMED.**
- `8f2a1c5d3b40` used index names/`fare_mode` type inconsistent with the model
  (`FixedPriceOffer` canonical in `app/models/fixed_offer.py`); aligned names and
  `ck_orders_fare_mode` enum. **CONFIRMED.**

Also verified in this pass:
- Alembic has exactly one head (`f1c2d3e4a5b6`); no residual sibling migration fork.
- `tests/infra/test_migration_schema_parity.py` = 3 passed.
- `tests/api/test_security_hardening.py` = 48 passed; `/api/v1/destinations` is a
  deliberate public metadata route and is whitelisted, not a missing live-state guard.
- `tests/api/test_premium_destinations.py` = 5 passed; `test_fixed_fare_offers.py` = 4 passed.
- admin-web `npm run typecheck` = 0 errors.
- Mobile WIP `FixedOffer`/`Order` JSON fields align with backend schemas. Not committed;
  still sibling-owned.

No new HIGH/MEDIUM findings from this scan. The earlier audit report remains the
authority for unresolved findings.
