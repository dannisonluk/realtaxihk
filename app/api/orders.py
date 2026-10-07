"""Order API: create (fare snapshot), grab, lifecycle, cancel, detail, history.

Hardening wave (docs/PRODUCTION_READINESS.md):
- P0-3 all user-facing routes run require_active_user (deactivation is live);
- B3  tip capped (Numeric(10,2) overflow guard);
- P1-10 order snapshots now carry route tolls (tunnels/crosses_harbour);
- P2-1 GET /{order_id} (participants) + GET "" history (passenger/driver view);
- P2-10 nearby degrades to an empty page when Redis is down (fail-open
  dispatch), never 500s the driver's map.

Security wave (docs/SECURITY_AUDIT.md):
- SEC-09 `tunnels` is capped at the 8 real tunnel values. Unbounded, it was both
  a response-amplification vector (100k invalid entries echoed back as 32.8 MB)
  and a storage/egress one (a 200k-entry order wrote 3.4 MB of fare_json that
  every subsequent list call re-sent);
- SEC-26 the `before_id` keyset cursor is scoped to the caller's own orders, so
  it can no longer be used to probe whether an arbitrary order id exists.

P-4 monthly phone re-verification, applied as a **soft** block:
- `create_order` and `grab_order` run `require_phone_current`, not
  `require_active_user`. These are the two routes that *start new business*, and
  an overdue phone number refuses exactly those.
- The lifecycle routes (`arrive`/`start`/`complete`) and `cancel` deliberately
  keep the looser guard. A driver who is already on a trip must be able to finish
  it, and a passenger must always be able to cancel. Blocking either would strand
  a real journey to enforce a reminder, which is not a trade worth making.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import OrderOut, OrderPageOut, PassengerDisputeOut
from app.api.schemas.order import OrderDisputeIn
from app.core import cooldown as cooldown_mod
from app.core.config import get_settings
from app.core.db import get_session, get_session_factory
from app.core.deps import Principal, require_active_user, require_phone_current
from app.core.exceptions import BusinessRuleError
from app.core.money import money_str
from app.core.region import ALL_AREAS, is_valid_area
from app.core.service_area import require_in_hong_kong
from app.models import (
    INTERRUPTION_REASONS_BY_PARTY,
    SAFETY_INTERRUPTION_REASONS,
    DisputeCategory,
    DisputePartyKind,
    DisputeSeverity,
    DisputeSource,
    DisputeStatus,
    DriverDeposit,
    DriverProfile,
    DriverStatus,
    InterruptionReason,
    LedgerEntryType,
    Order,
    OrderDispute,
    OrderEventType,
    OrderFareMode,
    OrderKind,
    OrderParty,
    OrderStatus,
    PaymentMethod,
    User,
    UserRole,
)
from app.services.admin.dispute_service import DisputeService
from app.services.ledger.ledger_service import (
    LedgerService,
    reference_for_cancellation_penalty,
    reference_for_fixed_ride,
    reference_for_trip_fee,
)
from app.services.order.fare_calculator import TaxiType, Tunnel, calculate_fare
from app.services.order.geo_service import GeoService
from app.services.order.grab_service import GrabService
from app.services.order.order_event_service import record_order_event
from app.services.order.order_service import (
    OrderService,
    _point_wkt,
    fare_snapshot,
    order_out,
)
from app.services.order.state_machine import (
    CANCEL_LOCKED_STATUSES,
    INTERRUPTIBLE_STATUSES,
    assert_order_transition,
)
from app.services.order.trip_event_service import publish_lifecycle

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])

_ORDER_RATE_LIMIT = 5  # per 60s per passenger
_ORDER_WINDOW_S = 60
# There are exactly 8 tunnels in the fare engine's Tunnel enum; anything beyond
# that is either a mistake or an attack.
_MAX_TUNNELS = 8

# Redis candidate window for `GET /orders/nearby`. Unfiltered, the nearest 50 is
# the whole answer and the SQL pass only re-validates them. Filtered, the window
# is the *input* to the predicates, so it has to be wide enough that a filter
# does not starve on its own cap — 200 keeps a dense-area poll bounded while
# leaving a filtered query able to find its rows.
_GEO_CANDIDATES = 50
_GEO_CANDIDATES_FILTERED = 200

# A party report is after-the-fact: the trip must already be terminal. In-trip
# complaints go through `/interrupt`, which opens a case in the same transaction.
_PARTY_DISPUTE_ORDER_STATUSES = frozenset(
    {OrderStatus.COMPLETED, OrderStatus.INTERRUPTED, OrderStatus.CANCELLED}
)
_PARTY_OPEN_DISPUTE_STATUSES = (
    DisputeStatus.OPEN.value,
    DisputeStatus.INVESTIGATING.value,
    DisputeStatus.AWAITING_PARTY.value,
    DisputeStatus.ESCALATED.value,
)


def _redis(request: Request):
    # Request-scoped handle for `GrabService`, which is the one thing here that
    # needs a Redis client of its own (grab runs in a session-factory session
    # and takes its lock through this handle). `state.redis_factory()` builds
    # per call on purpose: a client is bound to the event loop that created it,
    # so caching one on `app.state` would break the moment uvicorn runs more
    # than one loop — see `app/core/db.py`.
    return request.app.state.redis_factory()


async def _cooldown_guard(redis, party: str, account_id) -> None:
    """Refuse a defaulting party with 429 + `Retry-After` (P4 DECISION-5).

    **Fails open on a Redis error, deliberately.** A cool-down is a soft
    anti-abuse measure; the alternative — refusing everyone when Redis blinks —
    would turn a cache outage into a platform-wide booking outage, which is a
    strictly worse failure than one defaulting party slipping through the
    window. The error is logged, so it is visible without being fatal.
    """
    try:
        remaining = await cooldown_mod.cooldown_remaining(redis, party, account_id)
    except Exception:
        import logging

        logging.getLogger("realtaxihk.orders").exception("cool-down check unavailable")
        return
    if remaining > 0:
        raise HTTPException(
            status_code=429,
            detail={"reason": "COOLDOWN", "retry_after_s": remaining},
            headers={"Retry-After": str(remaining)},
        )


class AnimalDetailIn(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    # Approximate size so a driver can judge before accepting. Open ranges are
    # bounded to keep a passenger from stuffing absurd values into JSONB.
    height_cm: Decimal = Field(ge=1, le=200)
    weight_kg: Decimal = Field(ge=0.1, le=100)


class RideRequirementsIn(BaseModel):
    silent_ride: bool = False
    no_radio_music: bool = False
    no_smoke: bool = False
    no_perfume: bool = False
    animal: AnimalDetailIn | None = None


# The filterable requirement keys, derived from the schema above rather than
# restated, so a new requirement cannot be added to `RideRequirementsIn` and
# silently stay unfilterable. The four flags are queried as booleans; `animal`
# is a structured detail, so "carrying one" is answered by presence instead of
# by truthiness — casting an object to boolean would raise at runtime rather
# than return a row.
_ANIMAL_KEY = "animal"
_REQUIREMENT_KEYS = frozenset(RideRequirementsIn.model_fields) - {_ANIMAL_KEY}
_ALL_REQUIREMENT_KEYS = frozenset(RideRequirementsIn.model_fields)


def _split_keys(values: list[str] | None) -> list[str]:
    """Flatten `?k=a,b` and `?k=a&k=b` into one ordered, de-duplicated list.

    Both spellings are in the wild: a form-built query joins with `&`, a
    hand-built one tends to use commas. Accepting only one of them would make
    the other mean "filter on the literal string 'a,b'", i.e. a filter that
    silently matches nothing — the exact failure this endpoint refuses.
    """
    out: list[str] = []
    for raw in values or ():
        for part in raw.split(","):
            key = part.strip()
            if key and key not in out:
                out.append(key)
    return out


class OrderCreateIn(BaseModel):
    pickup_lat: float = Field(ge=22.1, le=22.6)
    pickup_lng: float = Field(ge=113.8, le=114.5)
    dropoff_lat: float = Field(ge=22.1, le=22.6)
    dropoff_lng: float = Field(ge=113.8, le=114.5)
    pickup_address: str = Field(min_length=3, max_length=255)
    dropoff_address: str = Field(min_length=3, max_length=255)
    distance_km: Decimal = Field(gt=0, le=100)
    waiting_min: Decimal = Field(default=Decimal("0"), ge=0, le=600)
    taxi_type: str = Field(pattern=r"^(URBAN|NT|LANTAU)$")
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    tip: Decimal = Field(default=Decimal("0"), ge=0, le=10000)  # B3: Numeric(10,2) guard
    # P1-10: route tolls join the snapshot (previously hard-coded empty).
    # SEC-09: bounded — see _MAX_TUNNELS.
    tunnels: list[str] = Field(default_factory=list, max_length=_MAX_TUNNELS)
    crosses_harbour: bool = False
    pickup_at_cross_harbour_stand: bool = False
    # Pre-booking: scheduled orders are valid only with a landmark in the
    # 2h..3d window; the order service enforces the temporal and landmark rules.
    order_kind: str = "ON_DEMAND"
    scheduled_pickup_at: datetime | None = None
    dropoff_landmark_id: str | None = None

    @field_validator("order_kind")
    @classmethod
    def validate_order_kind(cls, v: str) -> str:
        try:
            OrderKind(v)
        except ValueError as exc:
            raise ValueError("unknown order_kind") from exc
        return v

    @field_validator("scheduled_pickup_at", mode="before")
    @classmethod
    def validate_scheduled_pickup(cls, v):
        if v is None:
            return v
        parsed = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        min_dt = now + timedelta(hours=2)
        max_dt = now + timedelta(days=3)
        if parsed < min_dt:
            raise ValueError("scheduled_pickup_at must be at least 2 hours ahead")
        if parsed > max_dt:
            raise ValueError("scheduled_pickup_at must be within 3 days")
        return parsed

    @field_validator("dropoff_landmark_id")
    @classmethod
    def validate_dropoff_landmark_id(cls, v):
        if v is None:
            return v
        try:
            UUID(v)
        except ValueError as exc:
            raise ValueError("invalid dropoff_landmark_id") from exc
        return v

    # Phase 1: what the passenger needs the assigned driver to see/agree to.
    requirements: RideRequirementsIn | None = None
    payment_preference: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("tunnels", mode="before")
    @classmethod
    def cap_tunnels(cls, v):
        """Reject an over-long tunnel list before item validation (SEC-09).

        See the identical guard in `app/api/fare.py`: a `before` validator turns an
        oversized request into a single small error instead of one error object per
        element.
        """
        if isinstance(v, (list, tuple)) and len(v) > _MAX_TUNNELS:
            raise ValueError(f"at most {_MAX_TUNNELS} tunnels may be listed")
        return v

    @field_validator("distance_km", "waiting_min", "discount_percent", "tip")
    @classmethod
    def finite(cls, v: Decimal) -> Decimal:
        if not v.is_finite():
            raise ValueError("must be a finite number")
        return v

    @field_validator("payment_preference")
    @classmethod
    def validate_payment_preference(cls, v: list[str]) -> list[str]:
        for m in v:
            try:
                PaymentMethod(m)
            except ValueError as exc:
                raise ValueError(f"unknown payment method: {m}") from exc
        return v


class CancelIn(BaseModel):
    """A cancellation. `reason_code` becomes **required** from ACCEPTED onward.

    Before P4 a cancel carried only free text, unvalidated. That is fine while a
    cancellation is free (nobody is harmed by "changed my mind"), but from
    ACCEPTED the driver has committed and the cancel carries a penalty — and an
    unvalidated free-text field on a penalised action is a bypass, because
    typing "passenger was drunk" exempted the driver. So the structured reason
    is mandatory exactly where the money is, and optional where it is not.
    """

    reason: str = Field(default="", max_length=500)
    reason_code: InterruptionReason | None = None


class ArrivalClaimIn(BaseModel):
    """The driver's "I have arrived". Coordinates are optional and untrusted.

    When supplied they must be in Hong Kong (`require_in_hong_kong`) and must
    not disagree with the driver's last server-recorded GPS tick by more than
    `arrival_gps_max_disagreement_m` — a body coordinate that contradicts the
    WebSocket-verified position is the signature of a spoofing attempt. The
    authoritative value is always the DB's `current_location`.
    """

    driver_lat: float | None = Field(default=None, ge=22.1, le=22.6)
    driver_lng: float | None = Field(default=None, ge=113.8, le=114.5)


class ArrivalConfirmIn(BaseModel):
    """The passenger's confirmation, by the last 4 digits of *their own* number.

    Option A from `docs/IN_TRIP_REDESIGN.md` §5.1.3: the passenger reads out
    their own number's tail. The driver cannot know it, so the only way the
    claim succeeds is if a real passenger is present to say it — which is the
    whole point of the second factor.
    """

    phone_last4: str = Field(pattern=r"^\d{4}$")


class ChangeDestinationIn(BaseModel):
    """A new dropoff for a trip already under way (P4 §4.1).

    `distance_km` is the **client's routed** distance for the whole new route
    (pickup → new dropoff), matching how `OrderCreateIn.distance_km` is
    supplied at creation. It is optional: the platform has no routing engine of
    its own, so when the client omits it the server falls back to the PostGIS
    straight-line distance between the frozen pickup and the new dropoff. That
    fallback is a *lower bound* and the new `fare` snapshot says so
    (`distance_source`), so nobody reads a straight line as a road distance.
    """

    dropoff_lat: float = Field(ge=22.1, le=22.6)
    dropoff_lng: float = Field(ge=113.8, le=114.5)
    dropoff_address: str = Field(min_length=3, max_length=255)
    distance_km: Decimal | None = Field(default=None, gt=0, le=100)

    @field_validator("distance_km")
    @classmethod
    def finite(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and not v.is_finite():
            raise ValueError("must be a finite number")
        return v


class InterruptIn(BaseModel):
    """Ending a trip early. `reason_code` is mandatory; `note` is mandatory for
    `OTHER` (the enum classifies, the note explains)."""

    reason_code: InterruptionReason
    note: str = Field(default="", max_length=500)


async def _get_order(session: AsyncSession, order_id: str, *, for_update: bool = False) -> Order:
    """Load one order, optionally taking a row lock on it.

    `for_update` belongs on every route that will *change* the row. Without the
    lock, two concurrent requests both read the pre-transition status, both pass
    `assert_order_transition`, and both apply their side effect — the read and
    the write are not one atomic step. `POST /{order_id}/cancel` is where that
    costs money: the no-show penalty is appended from the stale read, so a
    double-tapped cancel charges the driver twice and writes two ledger rows.

    Two details worth stating plainly:

    * `populate_existing` — the locking SELECT is emitted either way, but an
      instance already sitting in this session's identity map would *not* be
      overwritten by it, so the guard could still evaluate against a pre-lock
      read. It costs nothing and removes that whole class of surprise.
    * the lock is taken *before* any other row, so this path's lock order is
      `orders -> driver_profiles`. `LedgerService.append` takes the deposit row
      and never touches `orders`, so the reverse order does not exist and no
      deadlock cycle is introduced.
    """
    from uuid import UUID

    try:
        oid = UUID(order_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="order not found") from exc

    if for_update:
        order = (
            (
                await session.execute(
                    select(Order)
                    .where(Order.id == oid)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .first()
        )
    else:
        order = await session.get(Order, oid)

    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@router.post("", status_code=201, response_model=OrderOut)
async def create_order(
    payload: OrderCreateIn,
    request: Request,
    user: Principal = Depends(require_phone_current),
    session: AsyncSession = Depends(get_session),
):
    limiter = request.app.state.rate_limiter
    if not await limiter.allow(f"order:create:{user.id}", _ORDER_RATE_LIMIT, _ORDER_WINDOW_S):
        raise HTTPException(status_code=429, detail="too many orders, slow down")
    # P4 DECISION-5: a passenger who defaulted is barred from starting new
    # business for 15 minutes. Checked before any other work so a cool-down
    # costs one Redis round trip, not a fare calculation.
    await _cooldown_guard(request.app.state.redis_factory(), cooldown_mod.PASSENGER, user.id)
    # Service area: only Hong Kong may create orders. The pydantic bounds above
    # are a cheap first pass, but they are a *box*, and the box contains
    # Shenzhen — see `app/core/hk_bounds.py` for the measurements. Checked after
    # the limiter so a caller spraying out-of-area coordinates still spends
    # rate-limit budget rather than being handed a free path.
    require_in_hong_kong(payload.pickup_lat, payload.pickup_lng, field="pickup")
    require_in_hong_kong(payload.dropoff_lat, payload.dropoff_lng, field="dropoff")
    try:
        order = await OrderService(session).create(user.id, payload)
    except BusinessRuleError:
        # Already carries machine-readable `details`; `BusinessRuleError`
        # subclasses `ValueError`, so the branch below would discard them.
        raise
    except ValueError as exc:
        raise BusinessRuleError(str(exc)) from exc
    if order.order_kind == OrderKind.ON_DEMAND:
        await GeoService(request.app.state.redis_factory()).index_order(
            str(order.id), payload.pickup_lat, payload.pickup_lng
        )
    return order_out(order)


@router.get("/nearby", response_model=OrderPageOut)
async def nearby_orders(
    request: Request,
    lat: Annotated[float, Query(ge=22.1, le=22.6)],
    lng: Annotated[float, Query(ge=113.8, le=114.5)],
    radius_km: Annotated[float, Query(ge=0.5, le=10)] = 3.0,
    fare_mode: Annotated[OrderFareMode | None, Query()] = None,
    destination_area: Annotated[str | None, Query(max_length=24)] = None,
    premium_destination_id: Annotated[uuid.UUID | None, Query()] = None,
    requires: Annotated[list[str] | None, Query()] = None,
    excludes: Annotated[list[str] | None, Query()] = None,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Open orders near the driver, optionally narrowed (Phase 1 filters).

    Every filter is optional and AND-combined; with none supplied the behaviour
    is byte-for-byte what it was before, so existing clients are unaffected.
    `requires` and `excludes` accept either `a,b` or repeated `a&b=` and read
    the passenger's frozen requirements — never the caller's own declared
    environment.

    Two rules govern the whole set, and both are deliberate:

    * **A filter is a predicate on the order, not on the driver.** `requires`
      matches the requirements the *passenger* froze onto the order — it does
      not consult the caller's declared `in_car_environment_json`. The server
      therefore answers "which orders asked for X", and the client decides
      whether the driver can honour X. Inferring driver capability here would
      silently hide orders from a driver who could have taken them.
    * **An unanswerable filter is a 422, never an empty page.** An unknown area
      code or requirement key cannot match any row, so accepting it would make
      a typo indistinguishable from "nothing nearby" — the one reading a driver
      must be able to trust.

    Filters run in SQL, against the authoritative rows. Redis only supplies the
    distance window, and because that window is applied *before* the predicates
    it is widened whenever a filter is present: at the default cap of 50, a
    driver filtering for airport runs could be handed an empty list purely
    because the nearest 50 happened to be somewhere else. The SQL `status`
    predicate remains the real gate, exactly as before.
    """
    import logging

    wants = _split_keys(requires)
    avoids = _split_keys(excludes)
    if destination_area is not None and not is_valid_area(destination_area):
        raise HTTPException(
            status_code=422,
            detail=f"unknown destination_area; expected one of {sorted(ALL_AREAS)}",
        )
    for name, keys in (("requires", wants), ("excludes", avoids)):
        unknown = sorted(set(keys) - _ALL_REQUIREMENT_KEYS)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unknown {name}: {unknown}; expected one of {sorted(_ALL_REQUIREMENT_KEYS)}"
                ),
            )

    filtering = any(
        (
            fare_mode is not None,
            destination_area is not None,
            premium_destination_id is not None,
            bool(wants),
            bool(avoids),
        )
    )
    try:
        ids = await GeoService(request.app.state.redis_factory()).nearby_order_ids(
            lat,
            lng,
            radius_km,
            count=_GEO_CANDIDATES_FILTERED if filtering else _GEO_CANDIDATES,
        )
    except Exception:  # P2-10: Redis down -> fail-open dispatch, not a 500
        logging.getLogger("realtaxihk.orders").exception("nearby: geo index unavailable")
        return {"items": [], "degraded": True}
    if not ids:
        return {"items": []}
    q = (
        select(Order)
        .where(Order.id.in_([uuid.UUID(i) for i in ids]))
        .where(Order.status == OrderStatus.BROADCASTING)
    )
    if fare_mode is not None:
        q = q.where(Order.fare_mode == fare_mode)
    if destination_area is not None:
        q = q.where(Order.destination_area == destination_area)
    if premium_destination_id is not None:
        q = q.where(Order.premium_destination_id == premium_destination_id)
    for key in wants:
        if key == _ANIMAL_KEY:
            # `IS NOT NULL` on the JSONB key, not a boolean cast: an animal is
            # an object, and "carries one" is the only sensible reading. A JSON
            # `null` (what `model_dump()` writes for an absent animal) is a
            # present *value*, so it satisfies `IS NOT NULL` and must not: only
            # a real object can be promised.
            q = q.where(Order.requirements_json[key].astext.is_not(None))
        else:
            # `requirements_json` is NULL for an order with no requirements, so
            # the cast yields NULL and the predicate is not satisfied —
            # correct: such an order cannot promise a requirement it never
            # recorded. An explicit `false` is likewise not a promise.
            q = q.where(Order.requirements_json[key].as_boolean().is_(True))
    for key in avoids:
        if key == _ANIMAL_KEY:
            # `astext` is what makes this correct. `RideRequirementsIn` has a
            # default for every field, so `model_dump()` serialises `animal:
            # null` onto orders that never carried one — and in JSONB a `null`
            # is a value, so a bare `-> 'animal' IS NULL` is *false* for it and
            # the driver's 「可載寵物」 chip would hide orders that merely asked
            # for a silent ride. Casting to text collapses both the absent key
            # and the JSON null to SQL NULL, which is the reading wanted here:
            # "no pets in my car" must hide exactly the orders that announced
            # an animal, and nothing else.
            q = q.where(Order.requirements_json[key].astext.is_(None))
        else:
            q = q.where(Order.requirements_json[key].as_boolean().is_not(True))
    orders = (await session.execute(q)).scalars().all()
    # Two filters, not one, and both are needed. Redis holds the *candidate*
    # set ranked by distance, but it is not authoritative: an id can linger
    # after the order was grabbed or cancelled (the ZREM in GrabService is
    # best-effort), and the count=50 cap means a dense area can return ids whose
    # rows no longer qualify. The SQL `status == BROADCASTING` predicate is the
    # real gate; without it a driver could be shown an order that is already
    # someone else's, and tapping it would produce a 409.
    #
    # Re-ordering by `ids` after the dict lookup preserves Redis's
    # nearest-first order, which the SQL `IN (...)` would otherwise lose.
    by_id = {str(o.id): o for o in orders}
    return {"items": [order_out(by_id[i]) for i in ids if i in by_id]}


@router.get("", response_model=OrderPageOut)
async def my_orders(
    role: Annotated[str, Query(pattern=r"^(passenger|driver)$")] = "passenger",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before_id: uuid.UUID | None = Query(default=None),
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """P2-1: order history (newest first, keyset via before_id).

    SEC-26: the cursor is resolved inside the caller's own scope. Looking the
    anchor up by id alone turned `before_id` into an existence/timestamp oracle
    for arbitrary orders belonging to other users.
    """
    q = select(Order).order_by(Order.created_at.desc(), Order.id.desc()).limit(limit)
    if role == "driver":
        profile = await DriverProfile.for_user(session, user.id)
        if profile is None:
            return {"items": []}
        scope = Order.driver_id == profile.id
    else:
        scope = Order.passenger_id == user.id
    q = q.where(scope)

    if before_id is not None:
        anchor = (
            await session.execute(
                select(Order.created_at, Order.id).where(Order.id == before_id, scope)
            )
        ).first()
        if anchor is None:
            # Not the caller's order (or nonexistent) — indistinguishable by design.
            raise HTTPException(status_code=404, detail="cursor not found")
        # A composite cursor needs a composite comparison. `tuple_()` emits the
        # row-wise `(created_at, id) < (:ts, :id)`. Written as a plain Python
        # tuple comparison it silently degraded to `created_at < :ts`: Python's
        # tuple `<` tests element equality first, and `bool(Order.created_at ==
        # ts)` is `False` for a SQLAlchemy column rather than an error, so the
        # `id` tie-breaker never reached the query. Any order sharing the
        # anchor's `created_at` then became unreachable -- and `created_at` is
        # `server_default=func.now()`, which is the *transaction* timestamp, so
        # rows written together share it exactly.
        q = q.where(tuple_(Order.created_at, Order.id) < tuple_(anchor[0], anchor[1]))

    rows = (await session.execute(q)).scalars().all()
    return {"items": [order_out(o) for o in rows]}


@router.get("/{order_id}", response_model=OrderOut)
async def order_detail(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """P2-1: participants (or admin) can read one order."""
    order = await _get_order(session, order_id)
    profile = await DriverProfile.for_user(session, user.id)
    is_party = order.passenger_id == user.id or (
        profile is not None and order.driver_id == profile.id
    )
    if not is_party and user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="not a party of this order")
    return order_out(order)


@router.post("/{order_id}/grab", response_model=OrderOut)
async def grab_order(
    order_id: str,
    user: Principal = Depends(require_phone_current),
    session: AsyncSession = Depends(get_session),
    factory=Depends(get_session_factory),
    redis=Depends(_redis),
):
    profile = await DriverProfile.for_user(session, user.id)
    if profile is None or profile.status != DriverStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="only ACTIVE drivers can grab orders")

    # P4 §4.0.6 — two independent gates, both *before* `GrabService`. The order
    # matters: running either after the atomic grab would lock the order for a
    # driver who then cannot take it, and no other driver could take it either.
    #
    # 1) Cool-down (DECISION-5): a driver who defaulted waits 15 minutes. 429,
    #    not 403 — "wait" and "you may not" are different answers.
    await _cooldown_guard(redis, cooldown_mod.DRIVER, user.id)
    # 2) Deposit in arrears (DECISION-3): 423, never 403. This is a state the
    #    driver can clear by topping up, whereas 403 is a permission verdict;
    #    conflating them would make "top up and retry" indistinguishable from
    #    "you are banned". Only *new* grabs are gated — an in-flight trip is
    #    unaffected, which is why this lives here and not on `/complete`.
    deposit = (
        (
            await session.execute(
                select(DriverDeposit).where(DriverDeposit.driver_profile_id == profile.id)
            )
        )
        .scalars()
        .first()
    )
    if deposit is not None and (
        (deposit.balance_hkd + deposit.held_hkd) < 0 or deposit.acceptance_unlocked_at is None
    ):
        raise HTTPException(
            status_code=423,
            detail={
                "reason": "DEPOSIT_INSUFFICIENT",
                "balance_hkd": money_str(deposit.balance_hkd),
                "acceptance_locked": deposit.acceptance_unlocked_at is None,
            },
        )

    order = await _get_order(session, order_id)  # existence pre-check
    grab = GrabService(redis, factory)
    won = await grab.grab(order_id=str(order.id), driver_user_id=str(user.id))
    if not won:
        raise HTTPException(status_code=409, detail="order was taken by another driver or is gone")
    # `GrabService` mutated the row in its *own* session (it opens a
    # session_factory session and commits there), so this request's identity map
    # still holds the pre-grab copy — status BROADCASTING, driver_id None.
    # Without this refresh the response would tell the winning driver they lost.
    # `refresh` re-SELECTs by primary key, which is also what makes it safe to
    # call on an object the other session's commit has already changed.
    await session.refresh(order)  # grab service committed in its own session
    # The passenger's "waiting for a driver" screen is where a poll delay is
    # most visible — it is the first screen after booking, and until this
    # existed the match only arrived on the next 10 s tick. `GrabService`
    # committed in its own session, so the event cannot outrun the row.
    await publish_lifecycle(session, order, "GRABBED", driver_profile_id=str(profile.id))
    return order_out(order)


async def _assigned_driver_guard(
    session: AsyncSession, order: Order, user: Principal
) -> DriverProfile:
    profile = await DriverProfile.for_user(session, user.id)
    if profile is None or order.driver_id is None or order.driver_id != profile.id:
        raise HTTPException(status_code=403, detail="not the assigned driver")
    return profile


async def _arrival_distances(
    session: AsyncSession,
    order: Order,
    driver_profile_id,
    *,
    lat: float | None,
    lng: float | None,
) -> tuple[Decimal | None, Decimal | None]:
    """Metres from the driver's last GPS tick to (a) the pickup, (b) a body point.

    PostGIS, not a hand-rolled haversine: `ST_Distance` on two
    `geography(POINT,4326)` values already returns metres, and re-deriving that
    in Python is how the two answers drift apart.

    `body_gap` is the disagreement between the client-supplied coordinate and
    the server-recorded position — the spoofing signal. It is `None` when no
    body coordinate was supplied, and the SQL is built without the second
    expression in that case so asyncpg is never handed an untyped NULL.
    """
    from sqlalchemy import text as sa_text

    base = (
        "SELECT ST_Distance(dp.current_location, o.pickup_location) AS to_pickup"
        "{extra} "
        "FROM driver_profiles dp, orders o "
        "WHERE dp.id = CAST(:dp AS uuid) AND o.id = CAST(:oid AS uuid)"
    )
    params: dict = {"dp": str(driver_profile_id), "oid": str(order.id)}
    if lat is None or lng is None:
        sql = base.format(extra="")
    else:
        sql = base.format(
            extra=", ST_Distance(dp.current_location, "
            "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography) AS body_gap"
        )
        params["lat"] = lat
        params["lng"] = lng

    row = (await session.execute(sa_text(sql), params)).first()
    if row is None:
        return None, None
    to_pickup = Decimal(row[0]) if row[0] is not None else None
    body_gap = Decimal(row[1]) if len(row) > 1 and row[1] is not None else None
    return to_pickup, body_gap


@router.post("/{order_id}/arrival-claim", response_model=OrderOut)
async def order_arrival_claim(
    order_id: str,
    payload: ArrivalClaimIn,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Arrival, step 1: the driver claims it; GPS decides whether to believe.

    Success moves the order to **`PENDING_ARRIVAL_CONFIRM`**, not
    `DRIVER_ARRIVED`. The cancel right is deliberately *not* locked yet: a
    driver must not be able to remove the passenger's right to cancel from 500 m
    away. Only the passenger's confirmation (step 2) does that.
    """
    order = await _get_order(session, order_id, for_update=True)
    profile = await _assigned_driver_guard(session, order, user)
    if order.status != OrderStatus.ACCEPTED:
        raise HTTPException(
            status_code=409,
            detail={"reason": "WRONG_STATUS", "status": order.status.value},
        )

    if payload.driver_lat is not None and payload.driver_lng is not None:
        # A body coordinate is untrusted input, so it gets the same in-HK check
        # as an order's pickup.
        require_in_hong_kong(payload.driver_lat, payload.driver_lng, field="driver")

    settings = get_settings()
    distance, body_gap = await _arrival_distances(
        session,
        order,
        profile.id,
        lat=payload.driver_lat,
        lng=payload.driver_lng,
    )
    if distance is None:
        # No GPS tick on record. "Unknown" must never read as "close enough".
        raise HTTPException(status_code=422, detail={"reason": "NO_LOCATION"})
    if body_gap is not None and body_gap > settings.arrival_gps_max_disagreement_m:
        raise HTTPException(
            status_code=422,
            detail={"reason": "GPS_MISMATCH", "disagreement_m": float(body_gap)},
        )
    if distance > settings.arrival_radius_m:
        raise HTTPException(
            status_code=422,
            detail={"reason": "TOO_FAR", "distance_m": float(distance)},
        )

    from_status = order.status.value
    order.arrival_gps_distance_m = distance
    await OrderService(session).transition(order, OrderStatus.PENDING_ARRIVAL_CONFIRM)
    await record_order_event(
        session,
        order_id=order.id,
        event=OrderEventType.ARRIVAL_CLAIMED,
        from_status=from_status,
        to_status=OrderStatus.PENDING_ARRIVAL_CONFIRM.value,
        actor_kind=OrderParty.DRIVER.value,
        actor_id=user.id,
        payload={"distance_m": float(distance)},
    )
    # P4 §7: the passenger's confirm prompt must appear now, not on the next
    # poll. Committed inside the helper, so the event cannot outrun the row.
    await publish_lifecycle(session, order, "ARRIVAL_CLAIMED", distance_m=float(distance))
    return order_out(order)


@router.post("/{order_id}/arrival-confirm", response_model=OrderOut)
async def order_arrival_confirm(
    order_id: str,
    payload: ArrivalConfirmIn,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Arrival, step 2: the passenger confirms with their own number's last 4.

    Success moves the order to `DRIVER_ARRIVED` and locks the cancel right.
    Three failures return it to `ACCEPTED` and open a dispute: the usual causes
    are a driver at the wrong pickup or a passenger in the wrong car, and that
    needs a human, not a fourth guess.
    """
    order = await _get_order(session, order_id, for_update=True)
    if order.passenger_id != user.id:
        raise HTTPException(status_code=403, detail="not the passenger of this order")
    if order.status != OrderStatus.PENDING_ARRIVAL_CONFIRM:
        raise HTTPException(
            status_code=409,
            detail={"reason": "WRONG_STATUS", "status": order.status.value},
        )

    passenger = await session.get(User, order.passenger_id)
    expected = (passenger.phone_e164 or "")[-4:] if passenger is not None else ""
    settings = get_settings()

    if not expected or payload.phone_last4 != expected:
        order.arrival_pin_attempts = (order.arrival_pin_attempts or 0) + 1
        remaining = settings.arrival_pin_max_attempts - order.arrival_pin_attempts
        # Commit the increment before refusing: the request-scoped session rolls
        # back on exception, so without this the attempt counter would never
        # move and the 3-strike limit would never be reached.
        await session.commit()
        if remaining > 0:
            raise HTTPException(
                status_code=401,
                detail={"reason": "PIN_MISMATCH", "attempts_remaining": remaining},
            )
        # Out of attempts: back to ACCEPTED and hand it to an operator.
        from_status = order.status.value
        await OrderService(session).transition(order, OrderStatus.ACCEPTED)
        dispute = await DisputeService(session).open_case(
            category=DisputeCategory.OTHER,
            summary=(
                f"Arrival could not be confirmed for order {order.id} after "
                f"{order.arrival_pin_attempts} attempts."
            ),
            source=DisputeSource.PARTY_REPORT,
            order_id=order.id,
            raised_by_kind=OrderParty.PASSENGER.value,
            raised_by_id=user.id,
            against_kind=OrderParty.DRIVER.value,
            against_id=order.driver_id,
        )
        await record_order_event(
            session,
            order_id=order.id,
            event=OrderEventType.DISPUTE_OPENED,
            actor_kind="SYSTEM",
            payload={"dispute_id": str(dispute.id), "source": "ARRIVAL_CONFLICT"},
        )
        # The passenger failed to confirm three times; the order went back to
        # ACCEPTED and a dispute opened. Both parties need to see that now.
        await publish_lifecycle(session, order, "ARRIVAL_CONFLICT", dispute_id=str(dispute.id))
        return order_out(order)

    from_status = order.status.value
    await OrderService(session).transition(order, OrderStatus.DRIVER_ARRIVED)
    await record_order_event(
        session,
        order_id=order.id,
        event=OrderEventType.ARRIVAL_CONFIRMED,
        from_status=from_status,
        to_status=OrderStatus.DRIVER_ARRIVED.value,
        actor_kind=OrderParty.PASSENGER.value,
        actor_id=user.id,
    )
    # P4 §7: the driver's screen must leave the "waiting" state immediately.
    await publish_lifecycle(session, order, "ARRIVAL_CONFIRMED")
    return order_out(order)


@router.post("/{order_id}/arrive", response_model=OrderOut, deprecated=True)
async def order_arrive(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Deprecated alias for `arrival-claim` (no body coordinates).

    Kept so an older client does not hard-fail, but it can only reach
    `PENDING_ARRIVAL_CONFIRM` — the passenger's confirmation is what unlocks
    `DRIVER_ARRIVED`, and no alias can skip it.
    """
    return await order_arrival_claim(order_id, ArrivalClaimIn(), user, session)


@router.post("/{order_id}/start", response_model=OrderOut)
async def order_start(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    order = await _get_order(session, order_id, for_update=True)
    profile = await _assigned_driver_guard(session, order, user)
    from_status = order.status.value
    await OrderService(session).transition(order, OrderStatus.IN_TRIP)

    # P4 DECISION-1: the per-trip platform fee, charged to the driver's deposit
    # at departure. The fare itself never passes through the platform (Cap. 374D
    # intermediary), so this is the platform's actual revenue from the trip.
    # `reference_for_trip_fee` is unique per order, so a retried `/start`
    # replays the entry instead of charging twice.
    fee = Decimal(get_settings().platform_trip_fee_hkd)
    await LedgerService(session).append(
        driver_profile_id=profile.id,
        entry_type=LedgerEntryType.PLATFORM_TRIP_FEE,
        amount_hkd=-fee,
        note=f"platform trip fee: {order.id}",
        order_id=order.id,
        created_by=user.id,
        reference=reference_for_trip_fee(order.id),
    )
    order.platform_fee_charged_at = datetime.now(UTC)
    await record_order_event(
        session,
        order_id=order.id,
        event=OrderEventType.FEE_CHARGED,
        from_status=from_status,
        to_status=OrderStatus.IN_TRIP.value,
        actor_kind=OrderParty.DRIVER.value,
        actor_id=user.id,
        payload={"entry_type": LedgerEntryType.PLATFORM_TRIP_FEE.value, "amount_hkd": str(-fee)},
    )
    # P4 §7: departure is the other half of "stop waiting" — the passenger's
    # tracking screen has to switch from "driver is coming" to "trip in
    # progress" when the driver actually starts, not up to ten seconds later.
    await publish_lifecycle(session, order, "TRIP_STARTED")
    return order_out(order)


@router.post("/{order_id}/complete", response_model=OrderOut)
async def order_complete(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    order = await _get_order(session, order_id, for_update=True)
    await _assigned_driver_guard(session, order, user)
    from_status = order.status.value
    await OrderService(session).transition(order, OrderStatus.COMPLETED)

    # Fixed-fare service fee is a real ledger event: the passenger's price
    # includes a disclosed platform fee, and the driver owes that portion to
    # the platform. Reference is minted server-side so a retried completion
    # cannot double-debit through the unique reference backstop.
    if (
        order.fare_mode == OrderFareMode.FIXED
        and order.platform_fee_hkd is not None
        and order.platform_fee_hkd != 0
        and order.driver_id is not None
    ):
        await LedgerService(session).append(
            driver_profile_id=order.driver_id,
            entry_type=LedgerEntryType.FIXED_RIDE_FEE,
            amount_hkd=-order.platform_fee_hkd,
            note=f"fixed-fare platform fee: {order.id}",
            order_id=order.id,
            created_by=user.id,
            reference=reference_for_fixed_ride(order.id),
        )

    await record_order_event(
        session,
        order_id=order.id,
        event=OrderEventType.STATE_CHANGED,
        from_status=from_status,
        to_status=OrderStatus.COMPLETED.value,
        actor_kind=OrderParty.DRIVER.value,
        actor_id=user.id,
    )
    # P4 §7: completion ends the trip for both screens — the passenger's
    # receipt prompt and the driver's "awaiting next job" state should not wait
    # for the poll.
    await publish_lifecycle(session, order, "TRIP_COMPLETED")
    return order_out(order)


@router.post("/{order_id}/disputes", status_code=201, response_model=PassengerDisputeOut)
async def order_open_dispute(
    order_id: str,
    payload: OrderDisputeIn,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """File a passenger dispute against a finished trip (P4 §4.4).

    `PARTY_REPORT` is the source the admin queue distinguishes from
    `AUTO_INTERRUPTED`; it is the passenger's own account, so it starts at a
    normal SLA unless the category is safety-related. The case is opened on the
    request session so the order timeline and the dispute commit together, and
    `publish_lifecycle` makes the timeline event visible to both parties once
    the write is durable.
    """
    order = await _get_order(session, order_id, for_update=True)
    if order.passenger_id != user.id:
        raise HTTPException(status_code=403, detail="not a passenger of this order")

    if order.status not in _PARTY_DISPUTE_ORDER_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "WRONG_STATUS",
                "status": order.status.value,
            },
        )

    existing = await session.scalar(
        select(OrderDispute).where(
            OrderDispute.order_id == order.id,
            OrderDispute.raised_by_kind == DisputePartyKind.PASSENGER.value,
            OrderDispute.raised_by_id == user.id,
            OrderDispute.status.in_(_PARTY_OPEN_DISPUTE_STATUSES),
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "DISPUTE_ALREADY_OPEN",
                "dispute_id": str(existing.id),
            },
        )

    severity = (
        DisputeSeverity.HIGH
        if payload.category == DisputeCategory.SAFETY
        else DisputeSeverity.NORMAL
    )
    dispute = await DisputeService(session).open_case(
        category=payload.category,
        summary=payload.summary,
        source=DisputeSource.PARTY_REPORT,
        order_id=order.id,
        raised_by_kind=DisputePartyKind.PASSENGER.value,
        raised_by_id=user.id,
        against_kind=(
            DisputePartyKind.DRIVER.value
            if order.driver_id is not None
            else DisputePartyKind.PLATFORM.value
        ),
        against_id=order.driver_id,
        severity=severity,
    )
    await record_order_event(
        session,
        order_id=order.id,
        event=OrderEventType.DISPUTE_OPENED,
        actor_kind=DisputePartyKind.PASSENGER.value,
        actor_id=user.id,
        payload={
            "dispute_id": str(dispute.id),
            "source": DisputeSource.PARTY_REPORT.value,
            "category": payload.category.value,
            "severity": dispute.severity,
        },
    )
    # P4 §7: the counterparty and the app's own order screen learn about the
    # dispute immediately, not on the next poll.
    await publish_lifecycle(
        session,
        order,
        "DISPUTE_OPENED",
        dispute_id=str(dispute.id),
        source=DisputeSource.PARTY_REPORT.value,
    )
    return {
        "id": str(dispute.id),
        "order_id": str(order.id),
        "source": dispute.source,
        "category": dispute.category,
        "severity": dispute.severity,
        "status": dispute.status,
        "summary": dispute.summary,
        "raised_by_kind": dispute.raised_by_kind,
        "against_kind": dispute.against_kind,
        "safety_flag": dispute.safety_flag,
        "sla_due_at": dispute.sla_due_at.isoformat(),
        "created_at": dispute.created_at.isoformat(),
    }


async def _straight_line_km(session: AsyncSession, order: Order, lat: float, lng: float) -> Decimal:
    """Straight-line kilometres from the order's frozen pickup to a point.

    PostGIS for the same reason `_arrival_distances` uses it: `ST_Distance` on
    two `geography(POINT,4326)` values already returns metres, and re-deriving
    that in Python is how the two answers drift apart.

    Straight-line is a *lower bound* on the road distance, and the caller labels
    it as such. It is the fallback, not the preferred input: a client that has a
    routing engine sends `distance_km` and gets a real figure.
    """
    from sqlalchemy import text as sa_text

    row = (
        await session.execute(
            sa_text(
                "SELECT ST_Distance(o.pickup_location, "
                "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography) "
                "FROM orders o WHERE o.id = CAST(:oid AS uuid)"
            ),
            {"oid": str(order.id), "lat": lat, "lng": lng},
        )
    ).first()
    if row is None or row[0] is None:
        # `pickup_location` is NOT NULL, so this is a "cannot happen" — but
        # answering with 0 km would silently re-price the trip at the flagfall.
        raise HTTPException(status_code=422, detail={"reason": "NO_LOCATION"})
    return (Decimal(row[0]) / Decimal(1000)).quantize(Decimal("0.001"))


def _reestimate_fare(
    order: Order, *, distance_km: Decimal, distance_source: str
) -> tuple[dict, Decimal]:
    """Rebuild the fare snapshot for a changed destination (P4 §4.1 step 4).

    Every input except the distance comes off the **order or its existing
    snapshot**, never off the request. That is the point: a destination change
    re-prices the same trip, it does not let the caller rewrite the terms they
    already agreed to (`taxi_type`, `discount_percent`, tunnels, harbour
    crossing, tip).

    Two inputs are genuinely unrecoverable and are stated here rather than
    quietly defaulted:

    * `waiting_min` — waiting is a live meter quantity, not part of a pre-trip
      estimate, and the order never stored the creation-time figure. Re-pricing
      uses 0.
    * `pickup_at_cross_harbour_stand` — never persisted. Re-pricing uses False,
      so a stand surcharge drops out of the new estimate.

    Both only ever move the *estimate*, which the snapshot already labels
    `is_estimate: True` and which no charge is derived from (the fare itself
    never passes through the platform — Cap. 374D).
    """
    old = dict(order.fare_json or {})
    try:
        tunnels = [Tunnel(str(t)) for t in (old.get("tunnels") or [])]
    except ValueError:
        # A snapshot carrying a tunnel code this build no longer knows must not
        # 500 the change; the surcharge is dropped and the new snapshot simply
        # has no tunnels.
        tunnels = []
    crosses_harbour = bool(old.get("crosses_harbour"))

    bd = calculate_fare(
        taxi_type=TaxiType(order.taxi_type),
        distance_km=distance_km,
        waiting_min=Decimal("0"),
        tunnels=tunnels,
        crosses_harbour=crosses_harbour,
        pickup_at_cross_harbour_stand=False,
        discount_percent=Decimal(order.discount_percent or 0),
        tip=Decimal(old.get("tip") or 0),
    )
    snapshot = fare_snapshot(
        bd,
        tunnels=sorted({t.value for t in tunnels}),
        crosses_harbour=crosses_harbour,
    )
    snapshot["fare_mode"] = OrderFareMode.METER.value
    snapshot["distance_source"] = distance_source
    snapshot["is_destination_change"] = True
    return snapshot, Decimal(money_str(bd.total_fare))


@router.post("/{order_id}/change-destination", response_model=OrderOut)
async def order_change_destination(
    order_id: str,
    payload: ChangeDestinationIn,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Move the dropoff of a trip already under way (P4 §4.1).

    **No operator is involved.** Changing your mind about where you are going is
    a normal thing to do mid-journey; routing it through support would be a
    queue for something that needs no adjudication. What the change *does* need
    is to be recorded and re-priced, which is all this endpoint does.

    The order is left in `DESTINATION_CHANGED`, not returned to `IN_TRIP`. That
    state is deliberately observable — the passenger's client shows the new
    estimate off it, and every "a trip is still running" query in the codebase
    lists it (`admin/live`, `admin/orders`, `refund_service`). A further change
    while already in it re-stamps rather than looping through `IN_TRIP`, so the
    documented transition table needs no self-edge.
    """
    order = await _get_order(session, order_id, for_update=True)
    profile = await DriverProfile.for_user(session, user.id)
    is_passenger = order.passenger_id == user.id
    is_driver = profile is not None and order.driver_id == profile.id
    if not (is_passenger or is_driver):
        raise HTTPException(status_code=403, detail="not a party of this order")

    if order.status not in (OrderStatus.IN_TRIP, OrderStatus.DESTINATION_CHANGED):
        raise HTTPException(
            status_code=409,
            detail={"reason": "WRONG_STATUS", "status": order.status.value},
        )

    settings = get_settings()
    changes = order.destination_change_count or 0
    if changes >= settings.max_destination_changes:
        # 429, not 403: the cap is a rate limit on a legitimate action, and the
        # passenger's next step is to talk to the driver or interrupt — both of
        # which are still available. A permission verdict would be a lie.
        raise HTTPException(
            status_code=429,
            detail={
                "reason": "TOO_MANY_CHANGES",
                "limit": settings.max_destination_changes,
            },
        )
    require_in_hong_kong(payload.dropoff_lat, payload.dropoff_lng, field="dropoff")

    # 1. Preserve the original destination exactly once, and never overwrite it
    #    — "where did they originally ask to go" has to stay answerable for the
    #    life of the order, including after a dispute.
    #
    #    The **geography** half is copied server-side, in its own UPDATE. Reading
    #    `order.dropoff_location` hands back a geoalchemy2 `WKBElement` whose
    #    bind processing reaches for Shapely — and `pyproject.toml` depends on
    #    `geoalchemy2`, deliberately not on `geoalchemy2[shapely]`. A
    #    column-to-column assignment in the same statement avoids both the
    #    optional dependency and a round trip through WKT. The text half needs
    #    none of that and is a plain attribute copy.
    if changes == 0:
        order.original_dropoff_address = order.dropoff_address
        await session.execute(
            update(Order)
            .where(Order.id == order.id)
            .values(original_dropoff_location=Order.dropoff_location)
        )

    previous_address = order.dropoff_address
    previous_total = order.estimated_total_hkd

    # 2. Re-price. The client's routed distance when it sent one, else the
    #    PostGIS straight line — labelled either way.
    if payload.distance_km is not None:
        new_distance = Decimal(payload.distance_km)
        distance_source = "client_route"
    else:
        new_distance = await _straight_line_km(
            session, order, payload.dropoff_lat, payload.dropoff_lng
        )
        distance_source = "straight_line"

    order.dropoff_location = _point_wkt(payload.dropoff_lat, payload.dropoff_lng)
    order.dropoff_address = payload.dropoff_address
    order.distance_km = new_distance

    # A fixed fare (一口價) is a standing offer for **one route**. Once the
    # dropoff moves, that contract no longer describes the trip, so the order
    # reverts to a meter estimate rather than keeping a price for a journey
    # nobody is taking. Silently honouring the old fixed price would charge the
    # passenger for the wrong trip; refusing the change outright would strand
    # them. Recorded in the event payload below.
    was_fixed = order.fare_mode == OrderFareMode.FIXED
    if was_fixed:
        order.fare_mode = OrderFareMode.METER
        order.fixed_offer_id = None
        order.driver_price_hkd = None
        order.platform_fee_hkd = None
        order.passenger_price_hkd = None

    snapshot, new_total = _reestimate_fare(
        order, distance_km=new_distance, distance_source=distance_source
    )
    order.fare_json = snapshot
    order.estimated_total_hkd = new_total

    # 3. Count and move. `transition()` stamps `destination_changed_at`; when the
    #    order is already in DESTINATION_CHANGED the transition table has no
    #    self-edge, so the stamp is written here instead of asserting an edge
    #    that does not exist.
    from_status = order.status.value
    order.destination_change_count = changes + 1
    if order.status == OrderStatus.IN_TRIP:
        await OrderService(session).transition(order, OrderStatus.DESTINATION_CHANGED)
    else:
        order.destination_changed_at = datetime.now(UTC)

    await record_order_event(
        session,
        order_id=order.id,
        event=OrderEventType.DEST_CHANGED,
        from_status=from_status,
        to_status=OrderStatus.DESTINATION_CHANGED.value,
        actor_kind=(OrderParty.PASSENGER.value if is_passenger else OrderParty.DRIVER.value),
        actor_id=user.id,
        payload={
            "change_number": order.destination_change_count,
            "previous_address": previous_address,
            "new_address": payload.dropoff_address,
            "previous_estimated_total_hkd": money_str(Decimal(previous_total)),
            "new_estimated_total_hkd": money_str(new_total),
            "distance_km": str(new_distance),
            "distance_source": distance_source,
            "fixed_fare_downgraded": was_fixed,
        },
    )
    # P4 §7: the other party's estimate is stale the moment this lands, so the
    # new total goes out with the event instead of waiting for their next poll.
    await publish_lifecycle(
        session,
        order,
        "DESTINATION_CHANGED",
        estimated_total_hkd=money_str(new_total),
        change_number=order.destination_change_count,
    )
    return order_out(order)


@router.post("/{order_id}/interrupt", response_model=OrderOut)
async def order_interrupt(
    order_id: str,
    payload: InterruptIn,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """End a trip early. **Instant, and effective immediately** (P4 DECISION-2).

    Both parties may interrupt, and neither needs the other's agreement or an
    operator's. A trip that has gone wrong — an accident, a confrontation, a
    passenger taken ill — has to be stoppable by the person in the car, right
    now; a state called `INTERRUPT_PENDING` would mean asking permission to stop
    being in danger.

    Because it is instant, the *money* question is deferred rather than decided:
    the platform trip fee charged at `/start` is **not** refunded here (an
    instant refund would let either side use "interrupt" to dodge the fee), and
    a dispute is opened in this same transaction so the case cannot be lost to a
    crash between the two writes.
    """
    order = await _get_order(session, order_id, for_update=True)
    profile = await DriverProfile.for_user(session, user.id)
    is_passenger = order.passenger_id == user.id
    is_driver = profile is not None and order.driver_id == profile.id
    if not (is_passenger or is_driver):
        raise HTTPException(status_code=403, detail="not a party of this order")

    if order.status not in INTERRUPTIBLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"reason": "WRONG_STATUS", "status": order.status.value},
        )

    note = payload.note.strip()
    if payload.reason_code == InterruptionReason.OTHER and not note:
        # The enum classifies; for OTHER the note *is* the classification.
        raise HTTPException(status_code=422, detail={"reason": "NOTE_REQUIRED"})

    party = OrderParty.PASSENGER if is_passenger else OrderParty.DRIVER
    if payload.reason_code not in INTERRUPTION_REASONS_BY_PARTY[party]:
        # §6.1 — the client's menu is filtered by role, but a hidden option is
        # not a refused one. `PASSENGER_MISCONDUCT` filed by a passenger (or
        # `DRIVER_MISCONDUCT` by a driver) names the filer, so accepting it
        # writes a self-accusation into the evidence an operator judges from.
        #
        # 422 with a structured `reason`, matching `NOTE_REQUIRED` above: the
        # enum member is valid, the *value in this body* is not usable — which
        # is a bad request, not a permission verdict. 403 in this handler
        # already means "you are not a party of this order".
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "REASON_NOT_FOR_PARTY",
                "party": party.value,
                "reason_code": payload.reason_code.value,
            },
        )
    # The case is filed against the counterparty — a default, not a verdict.
    # The dispute table is explicitly allowed to change it (§4.2).
    against_id = order.driver_id if is_passenger else order.passenger_id
    safety = payload.reason_code in SAFETY_INTERRUPTION_REASONS

    from_status = order.status.value
    order.interruption_reason = payload.reason_code
    order.interrupted_by_kind = party
    await OrderService(session).transition(order, OrderStatus.INTERRUPTED)

    summary = f"{party.value} interrupted order {order.id}: {payload.reason_code.value}"
    if note:
        summary = f"{summary} — {note}"

    dispute = await DisputeService(session).open_for_interruption(
        session,
        order_id=order.id,
        interrupted_by_kind=party.value,
        against_id=against_id,
        safety=safety,
        summary=summary[:2000],
        raised_by_id=user.id,
    )
    await record_order_event(
        session,
        order_id=order.id,
        event=OrderEventType.INTERRUPTED,
        from_status=from_status,
        to_status=OrderStatus.INTERRUPTED.value,
        actor_kind=party.value,
        actor_id=user.id,
        payload={
            "reason_code": payload.reason_code.value,
            "note": note,
            "safety": safety,
            "dispute_id": str(dispute.id),
        },
    )
    await record_order_event(
        session,
        order_id=order.id,
        event=OrderEventType.DISPUTE_OPENED,
        # `OrderParty` is the two *parties*; the dispute is opened by the
        # platform, so the actor is the literal SYSTEM — the same value
        # `DisputeService.open_for_interruption` writes into `raised_by_kind`.
        actor_kind="SYSTEM",
        payload={
            "dispute_id": str(dispute.id),
            "source": (
                DisputeSource.AUTO_INTERRUPTED_SAFETY.value
                if safety
                else DisputeSource.AUTO_INTERRUPTED.value
            ),
        },
    )
    # P4 §7 / DECISION-2: the trip ends *now* for both parties. The counterparty
    # learns it from this message rather than from a poll ten seconds later.
    await publish_lifecycle(
        session,
        order,
        "INTERRUPTED",
        reason_code=payload.reason_code.value,
        interrupted_by=party.value,
        safety=safety,
        dispute_id=str(dispute.id),
    )
    return order_out(order)


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def order_cancel(
    order_id: str,
    payload: CancelIn,
    request: Request,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Cancel a trip that has not been *proven* to have started (P4 §5.2.1).

    Three regimes, and the boundary between them is an objective fact rather
    than a judgement call — which is precisely why it can be automated:

    * **`CREATED` / `BROADCASTING`** — nobody has committed to anything yet.
      Free.
    * **`ACCEPTED` / `PENDING_ARRIVAL_CONFIRM`** — the driver has committed and
      is on their way, so cancelling is still allowed but now costs: **100% of
      the estimate for a passenger, 50% for a driver**, charged immediately,
      plus a 15-minute cool-down (DECISION-5). A structured `reason_code`
      becomes mandatory here — an unvalidated free-text field on a penalised
      action is a bypass, because "passenger was drunk" exempted the driver.
      The penalty is priced off *this trip's* snapshot, not a flat fee: a $300
      airport run and a $40 hop cannot default for the same money.
    * **`DRIVER_ARRIVED` and later** — arrival is proven (GPS **and** the
      passenger's own confirmation), so the cancel right is locked: **409**.
      The ways out are `/complete` and `/interrupt`, and the driver's costs are
      adjudicated by an operator afterwards.

    **Known gap, stated rather than hidden.** The passenger half of the penalty
    is *recorded* but not *collected*: `LedgerEntry.driver_profile_id` is NOT
    NULL and there is no passenger wallet (`docs/IN_TRIP_REDESIGN.md` §1.3c —
    the fare never passes through the platform, Cap. 374D). So a defaulting
    passenger gets the cool-down and a `PENALTY_CHARGED` timeline entry with
    `settled: false`, and the amount is owed rather than debited. Closing it
    needs passenger-side money, which is future infrastructure, not a bug in
    this handler.
    """
    # Locked, because the penalty below is decided from this read and the
    # transition is written after it: two concurrent cancels would otherwise
    # both see ACCEPTED and both charge. See `_get_order`.
    order = await _get_order(session, order_id, for_update=True)
    profile = await DriverProfile.for_user(session, user.id)
    is_passenger = order.passenger_id == user.id
    is_assigned_driver = profile is not None and order.driver_id == profile.id
    if not (is_passenger or is_assigned_driver):
        raise HTTPException(status_code=403, detail="not a party of this order")

    # The lock comes first, and it is a 409 rather than the 400 that
    # `assert_order_transition` would raise: "you may not do this yet" and "this
    # transition does not exist" are different answers to the client.
    if order.status in CANCEL_LOCKED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"reason": "CANCEL_LOCKED", "status": order.status.value},
        )

    # Legality next — never charge a penalty for an impossible transition.
    assert_order_transition(order.status, OrderStatus.CANCELLED)

    defaulting = order.status in (
        OrderStatus.ACCEPTED,
        OrderStatus.PENDING_ARRIVAL_CONFIRM,
    )
    if defaulting and payload.reason_code is None:
        raise HTTPException(status_code=422, detail={"reason": "REASON_REQUIRED"})

    if order.status == OrderStatus.BROADCASTING:
        await GeoService(request.app.state.redis_factory()).remove_order(str(order.id))

    party = OrderParty.PASSENGER if is_passenger else OrderParty.DRIVER
    from_status = order.status.value
    penalty = Decimal("0")
    settled = False
    if defaulting:
        share = Decimal(100) if is_passenger else Decimal(50)
        penalty = (Decimal(order.estimated_total_hkd or 0) * share / Decimal(100)).quantize(
            Decimal("0.01")
        )

    if defaulting and penalty > 0 and is_assigned_driver and profile is not None:
        # Only the driver side has an account to debit. `ensure_deposit_row`
        # first: an ACTIVE driver is *expected* to have one, but if the row is
        # missing, `append` raises and the cancel — a legitimate action — fails
        # entirely. Creating the zero-balance row and letting the penalty push
        # it negative is exactly the arrears model the ledger already allows.
        await LedgerService.ensure_deposit_row(session, profile)
        await LedgerService(session).append(
            driver_profile_id=profile.id,
            entry_type=LedgerEntryType.CANCELLATION_PENALTY,
            amount_hkd=-penalty,
            note=(
                f"driver default at {from_status}: "
                f"{payload.reason_code.value if payload.reason_code else ''} {payload.reason}"
            )[:200],
            order_id=order.id,
            created_by=user.id,
            reference=reference_for_cancellation_penalty(order.id, party.value),
        )
        settled = True

    if defaulting and penalty > 0:
        await record_order_event(
            session,
            order_id=order.id,
            event=OrderEventType.PENALTY_CHARGED,
            from_status=from_status,
            to_status=OrderStatus.CANCELLED.value,
            actor_kind=party.value,
            actor_id=user.id,
            payload={
                "entry_type": LedgerEntryType.CANCELLATION_PENALTY.value,
                "amount_hkd": money_str(penalty),
                "share_percent": "100" if is_passenger else "50",
                "basis_hkd": money_str(Decimal(order.estimated_total_hkd or 0)),
                "reason_code": payload.reason_code.value if payload.reason_code else None,
                # False for a passenger: recorded, owed, not debited — see the
                # docstring. The timeline is where an operator finds it.
                "settled": settled,
            },
        )

    order.cancellation_reason = payload.reason or (
        payload.reason_code.value if payload.reason_code else None
    )
    await OrderService(session).transition(order, OrderStatus.CANCELLED)
    # Free cancellations previously left no timeline evidence, so operational
    # analytics could only attribute defaulting cancellations. Every cancel now
    # records who ended it; `PENALTY_CHARGED` remains the financial detail.
    await record_order_event(
        session,
        order_id=order.id,
        event=OrderEventType.STATE_CHANGED,
        from_status=from_status,
        to_status=OrderStatus.CANCELLED.value,
        actor_kind=party.value,
        actor_id=user.id,
        payload={
            "reason_code": payload.reason_code.value if payload.reason_code else None,
            "cancellation_reason": order.cancellation_reason,
            "penalty_hkd": money_str(penalty) if penalty else None,
        },
    )

    if defaulting:
        # Armed *after* the transition succeeds, so a cancel that fails on
        # legality does not leave the party in a cool-down for a trip they never
        # cancelled. Every default re-arms the full window.
        await cooldown_mod.set_cooldown(
            request.app.state.redis_factory(),
            cooldown_mod.PASSENGER if is_passenger else cooldown_mod.DRIVER,
            user.id,
        )
    # The other party must stop waiting: a cancelled order is not one a driver
    # should keep bidding on, and the passenger's map should stop showing it.
    await publish_lifecycle(
        session,
        order,
        "CANCELLED",
        cancelled_by=party.value,
        reason_code=payload.reason_code.value if payload.reason_code else None,
        penalty_hkd=money_str(penalty),
    )
    return order_out(order)
