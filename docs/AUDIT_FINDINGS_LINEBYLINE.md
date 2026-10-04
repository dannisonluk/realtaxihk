# Line-by-line audit — findings log

Scope: the buckets that the first pass did not cover line-by-line.
Method: read every file, verify each claim against the other two deliverables before writing it down.
Status: IN PROGRESS.

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

**Owner decision still needed** (unchanged from v4): accept the hierarchy and rewrite the two
docstrings, switch `require_role` to set semantics, or keep rank and add an explicit
mutual-exclusion check on this one handler. The pinning test in
`tests/api/test_admin_role_separation.py` currently encodes the **rank** behaviour, so it must
change with whichever option is chosen.

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

## Open
- `app/core/hk_bounds.py` rest of the polygon (only the docstring + gate read so far).
- `app/core/{config,middleware,exceptions,logging,db,totp,admin_cookies}.py`
- mobile `data/`, `state/`, `features/`, `widgets/`
- admin-web `src/` (47 files) — AC-06…13 unverified
- `tests/` (45 files, 17,439 lines)
- `alembic/versions/`
