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
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import OrderOut, OrderPageOut
from app.core.db import get_session, get_session_factory
from app.core.deps import Principal, require_active_user, require_phone_current
from app.core.exceptions import BusinessRuleError
from app.core.region import ALL_AREAS, is_valid_area
from app.core.service_area import require_in_hong_kong
from app.models import (
    DriverProfile,
    DriverStatus,
    LedgerEntryType,
    Order,
    OrderFareMode,
    OrderStatus,
    PaymentMethod,
    UserRole,
)
from app.services.ledger.ledger_service import LedgerService, reference_for_fixed_ride
from app.services.order.geo_service import GeoService
from app.services.order.grab_service import GrabService
from app.services.order.order_service import OrderService, order_out
from app.services.order.state_machine import assert_order_transition

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


def _redis(request: Request):
    # Request-scoped handle for `GrabService`, which is the one thing here that
    # needs a Redis client of its own (grab runs in a session-factory session
    # and takes its lock through this handle). `state.redis_factory()` builds
    # per call on purpose: a client is bound to the event loop that created it,
    # so caching one on `app.state` would break the moment uvicorn runs more
    # than one loop — see `app/core/db.py`.
    return request.app.state.redis_factory()


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
    reason: str = Field(default="", max_length=500)


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
                    f"unknown {name}: {unknown}; expected one of "
                    f"{sorted(_ALL_REQUIREMENT_KEYS)}"
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
            # an object, and "carries one" is the only sensible reading.
            q = q.where(Order.requirements_json[_ANIMAL_KEY].is_not(None))
        else:
            # `requirements_json` is NULL for an order with no requirements, so
            # the cast yields NULL and the predicate is not satisfied —
            # correct: such an order cannot promise a requirement it never
            # recorded. An explicit `false` is likewise not a promise.
            q = q.where(Order.requirements_json[key].as_boolean().is_(True))
    for key in avoids:
        if key == _ANIMAL_KEY:
            # Missing key -> the JSONB extraction is NULL, `IS NULL` holds, so
            # the order stays in the result. That is the wanted reading:
            # "no pets in my car" must not hide orders that never mentioned one.
            q = q.where(Order.requirements_json[_ANIMAL_KEY].is_(None))
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
    return order_out(order)


async def _assigned_driver_guard(
    session: AsyncSession, order: Order, user: Principal
) -> DriverProfile:
    profile = await DriverProfile.for_user(session, user.id)
    if profile is None or order.driver_id is None or order.driver_id != profile.id:
        raise HTTPException(status_code=403, detail="not the assigned driver")
    return profile


@router.post("/{order_id}/arrive", response_model=OrderOut)
async def order_arrive(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    order = await _get_order(session, order_id, for_update=True)
    await _assigned_driver_guard(session, order, user)
    await OrderService(session).transition(order, OrderStatus.DRIVER_ARRIVED)
    return order_out(order)


@router.post("/{order_id}/start", response_model=OrderOut)
async def order_start(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    order = await _get_order(session, order_id, for_update=True)
    await _assigned_driver_guard(session, order, user)
    await OrderService(session).transition(order, OrderStatus.IN_TRIP)
    return order_out(order)


@router.post("/{order_id}/complete", response_model=OrderOut)
async def order_complete(
    order_id: str,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    order = await _get_order(session, order_id, for_update=True)
    await _assigned_driver_guard(session, order, user)
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

    return order_out(order)


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def order_cancel(
    order_id: str,
    payload: CancelIn,
    request: Request,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    # Locked, because the penalty below is decided from this read and the
    # transition is written after it: two concurrent cancels would otherwise
    # both see ACCEPTED and both charge. See `_get_order`.
    order = await _get_order(session, order_id, for_update=True)
    profile = await DriverProfile.for_user(session, user.id)
    is_passenger = order.passenger_id == user.id
    is_assigned_driver = profile is not None and order.driver_id == profile.id
    if not (is_passenger or is_assigned_driver):
        raise HTTPException(status_code=403, detail="not a party of this order")

    # legality first — never charge a penalty for an impossible transition
    assert_order_transition(order.status, OrderStatus.CANCELLED)

    if order.status == OrderStatus.BROADCASTING:
        await GeoService(request.app.state.redis_factory()).remove_order(str(order.id))

    # `profile is not None` is implied by `is_assigned_driver` and is here only
    # so the checker can narrow `profile` at the `profile.id` below.
    if (
        is_assigned_driver
        and profile is not None
        and order.status in (OrderStatus.ACCEPTED, OrderStatus.DRIVER_ARRIVED)
    ):
        from app.core.config import get_settings

        penalty = Decimal(get_settings().no_show_penalty_hkd)
        await LedgerService(session).append(
            driver_profile_id=profile.id,
            entry_type=LedgerEntryType.PENALTY_DEDUCTION,
            amount_hkd=-penalty,
            note=f"driver cancellation after acceptance: {payload.reason}"[:200],
            order_id=order.id,
            created_by=user.id,
        )

    await OrderService(session).transition(order, OrderStatus.CANCELLED)
    return order_out(order)
