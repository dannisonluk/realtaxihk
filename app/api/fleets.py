"""Fleet API — management, rosters and fleet settlement (的士車隊).

Two routers, one domain. The admin surface lives here rather than in
`admin.py` because a fleet endpoint is nearly always paired with its
member-facing counterpart (`POST /admin/fleets/{id}/members` and
`GET /fleets/{id}/members`), and splitting them by audience would mean editing
two files for every change to one concept.

Authorisation
-------------
* `/api/v1/admin/fleets/*` — `require_admin`.
* `/api/v1/fleets/*` — `require_active_user`, then membership is checked
  per-request: a driver may read the fleet they are on and nothing else. There
  is no route that lets a driver enumerate fleets, because the roster is
  commercially sensitive between competing operators.

A fleet is created by an admin and never by a driver: HK fleets are licensed
operators, so onboarding is an operator action on the platform side.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas import (
    FleetMemberListOut,
    FleetMemberRowOut,
    FleetOut,
    FleetPageOut,
    FleetSettlementListOut,
    FleetSettlementRunOut,
    FleetViewOut,
)
from app.core.config import get_settings
from app.core.db import get_session, get_session_factory
from app.core.deps import Principal, require_active_user, require_admin
from app.core.money import MoneyInput, money_str
from app.models import (
    DriverProfile,
    Fleet,
    FleetMemberRole,
    FleetMembership,
    FleetSettlementRun,
    FleetStatus,
)
from app.services.fleet_service import FleetService, FleetSettlementService

router = APIRouter(prefix="/api/v1/fleets", tags=["fleets"])
admin_router = APIRouter(prefix="/api/v1/admin/fleets", tags=["admin"])


def _money(value: MoneyInput) -> str:
    """Fleet money renders to the cent, half-up.

    Routed through `money_str()` rather than a local `quantize` call. The local
    version omitted the rounding mode, so it inherited `Decimal`'s
    `ROUND_HALF_EVEN` default — meaning a fleet fee that landed on a half-cent
    rounded the opposite way from the same figure in the platform-wide
    settlement response. `money_str` is the project's one half-up 2-dp rule and
    is already imported by every other money-emitting module.

    The stale comment this replaced claimed `money_str()` "quantises to one
    decimal", which was true before that function was corrected to 2 dp and is
    exactly the kind of note that survives a fix and then justifies a bug.
    """
    return money_str(value)


def _fleet_out(fleet: Fleet, *, member_count: int | None = None) -> dict:
    out = {
        "id": str(fleet.id),
        "name": fleet.name,
        "license_no": fleet.license_no,
        "status": fleet.status.value,
        "weekly_fee_discount_percent": str(Decimal(fleet.weekly_fee_discount_percent)),
        "contact_name": fleet.contact_name,
        "contact_phone": fleet.contact_phone,
        "note": fleet.note,
        "created_at": fleet.created_at.isoformat() if fleet.created_at else None,
    }
    if member_count is not None:
        out["member_count"] = member_count
    return out


def _member_out(membership: FleetMembership, driver: DriverProfile) -> dict:
    """Roster row.

    Deliberately excludes the driver's HK ID fragment, driver's licence number
    and vehicle registration: an operator needs to know *who is on the roster*,
    not to read back the identity documents they supplied to the platform. The
    driver profile id is enough to correlate with the ledger.
    """
    return {
        "driver_profile_id": str(driver.id),
        "taxi_type": driver.taxi_type,
        "driver_status": driver.status.value,
        "member_role": membership.member_role.value,
        "status": membership.status.value,
        "joined_at": membership.joined_at.isoformat() if membership.joined_at else None,
        "left_at": membership.left_at.isoformat() if membership.left_at else None,
    }


def _settlement_out(run: FleetSettlementRun) -> dict:
    return {
        "period": run.period,
        "fee_hkd": _money(run.fee_hkd),
        "discount_percent": str(Decimal(run.discount_percent)),
        "member_count": run.member_count,
        "charged": run.charged,
        "skipped": run.skipped,
        "failed": run.failed,
        "tampered": run.tampered,
        "collected_hkd": _money(run.collected_hkd),
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class FleetCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    license_no: str = Field(min_length=1, max_length=40)
    weekly_fee_discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    contact_name: str | None = Field(default=None, max_length=80)
    contact_phone: str | None = Field(default=None, max_length=20)
    note: str | None = None


class FleetUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: str | None = Field(default=None, pattern=r"^(ACTIVE|SUSPENDED|DISSOLVED)$")
    weekly_fee_discount_percent: Decimal | None = Field(default=None, ge=0, le=100)
    contact_name: str | None = Field(default=None, max_length=80)
    contact_phone: str | None = Field(default=None, max_length=20)
    note: str | None = None


class FleetMemberIn(BaseModel):
    driver_profile_id: uuid.UUID
    member_role: str = Field(default="MEMBER", pattern=r"^(OWNER|MANAGER|MEMBER)$")
    note: str | None = None


async def _require_membership(session: AsyncSession, fleet_id, user_id) -> FleetMembership:
    """A driver may only read the fleet they are actually on.

    Returns 404 rather than 403 for a fleet that exists but is not theirs, so the
    endpoint cannot be used to enumerate which fleets exist.
    """
    driver = await DriverProfile.for_user(session, user_id)
    if driver is None:
        raise HTTPException(status_code=404, detail="fleet not found")
    membership = await FleetService(session).membership_of(driver.id)
    if membership is None or str(membership.fleet_id) != str(fleet_id):
        raise HTTPException(status_code=404, detail="fleet not found")
    return membership


# ---------------------------------------------------------------------------
# Driver-facing
# ---------------------------------------------------------------------------


@router.get("/me", response_model=FleetViewOut)
async def my_fleet(
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """The fleet the caller drives for. `{"fleet": null}` when not on one."""
    driver = await DriverProfile.for_user(session, user.id)
    if driver is None:
        return {"fleet": None, "membership": None}

    service = FleetService(session)
    membership = await service.membership_of(driver.id)
    if membership is None:
        return {"fleet": None, "membership": None}

    fleet = await service.require(membership.fleet_id)
    return {
        "fleet": _fleet_out(fleet, member_count=await service.active_member_count(fleet.id)),
        "membership": {
            "member_role": membership.member_role.value,
            "joined_at": membership.joined_at.isoformat() if membership.joined_at else None,
        },
    }


@router.get("/{fleet_id}", response_model=FleetOut)
async def fleet_detail(
    fleet_id: uuid.UUID,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    await _require_membership(session, fleet_id, user.id)
    service = FleetService(session)
    fleet = await service.require(fleet_id)
    return _fleet_out(fleet, member_count=await service.active_member_count(fleet.id))


@router.get("/{fleet_id}/members", response_model=FleetMemberListOut)
async def fleet_members(
    fleet_id: uuid.UUID,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    await _require_membership(session, fleet_id, user.id)
    roster = await FleetService(session).roster(fleet_id)
    return {"items": [_member_out(m, d) for m, d in roster]}


@router.get("/{fleet_id}/settlement", response_model=FleetSettlementListOut)
async def fleet_settlement(
    fleet_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 52,
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
):
    """The fleet's own settlement history — newest week first."""
    await _require_membership(session, fleet_id, user.id)
    runs = await FleetService(session).settlement_history(fleet_id, limit=limit)
    return {"items": [_settlement_out(run) for run in runs]}


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


@admin_router.post("", status_code=status.HTTP_201_CREATED, response_model=FleetOut)
async def create_fleet(
    payload: FleetCreateIn,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    fleet = await FleetService(session).create(
        name=payload.name,
        license_no=payload.license_no,
        discount_percent=payload.weekly_fee_discount_percent,
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        note=payload.note,
    )
    await session.commit()
    return _fleet_out(fleet, member_count=0)


@admin_router.get("", response_model=FleetPageOut)
async def list_fleets(
    status_filter: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    fleet_status = FleetStatus(status_filter) if status_filter else None
    service = FleetService(session)
    rows, total = await service.list_page(status=fleet_status, limit=limit, offset=offset)
    # One grouped COUNT for the whole page. A `active_member_count` per fleet is
    # an N+1 — see `FleetService.active_member_counts` for why that mattered.
    counts = await service.active_member_counts([fleet.id for fleet in rows])
    return {
        "items": [_fleet_out(fleet, member_count=counts.get(fleet.id, 0)) for fleet in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@admin_router.get("/{fleet_id}", response_model=FleetOut)
async def admin_fleet_detail(
    fleet_id: uuid.UUID,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    service = FleetService(session)
    fleet = await service.require(fleet_id)
    return _fleet_out(fleet, member_count=await service.active_member_count(fleet.id))


@admin_router.patch("/{fleet_id}", response_model=FleetOut)
async def update_fleet(
    fleet_id: uuid.UUID,
    payload: FleetUpdateIn,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    service = FleetService(session)
    fleet = await service.update(
        fleet_id,
        name=payload.name,
        status=FleetStatus(payload.status) if payload.status else None,
        discount_percent=payload.weekly_fee_discount_percent,
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        note=payload.note,
    )
    await session.commit()
    return _fleet_out(fleet, member_count=await service.active_member_count(fleet.id))


@admin_router.get("/{fleet_id}/members", response_model=FleetMemberListOut)
async def admin_fleet_members(
    fleet_id: uuid.UUID,
    include_left: bool = False,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    service = FleetService(session)
    await service.require(fleet_id)
    roster = await service.roster(fleet_id, active_only=not include_left)
    return {"items": [_member_out(m, d) for m, d in roster]}


@admin_router.post(
    "/{fleet_id}/members", status_code=status.HTTP_201_CREATED, response_model=FleetMemberRowOut
)
async def add_fleet_member(
    fleet_id: uuid.UUID,
    payload: FleetMemberIn,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    service = FleetService(session)
    membership = await service.add_member(
        fleet_id,
        payload.driver_profile_id,
        member_role=FleetMemberRole(payload.member_role),
        note=payload.note,
    )
    await session.commit()
    driver = await session.get(DriverProfile, payload.driver_profile_id)
    return _member_out(membership, driver)


@admin_router.delete("/{fleet_id}/members/{driver_profile_id}", response_model=FleetMemberRowOut)
async def remove_fleet_member(
    fleet_id: uuid.UUID,
    driver_profile_id: uuid.UUID,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Take a driver off the roster.

    The membership row is kept and marked REMOVED rather than deleted, so the
    week in which a driver left stays reconstructible from the roster — which
    matters when an operator disputes a settlement.
    """
    membership = await FleetService(session).remove_member(fleet_id, driver_profile_id)
    await session.commit()
    driver = await session.get(DriverProfile, driver_profile_id)
    return _member_out(membership, driver)


@admin_router.get("/{fleet_id}/settlement", response_model=FleetSettlementListOut)
async def admin_fleet_settlement(
    fleet_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 52,
    admin: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    await FleetService(session).require(fleet_id)
    runs = await FleetService(session).settlement_history(fleet_id, limit=limit)
    return {"items": [_settlement_out(run) for run in runs]}


@admin_router.post("/{fleet_id}/settlement/run", response_model=FleetSettlementRunOut)
async def run_fleet_settlement(
    fleet_id: uuid.UUID,
    period: Annotated[str | None, Query(pattern=r"^\d{4}-W\d{2}$")] = None,
    admin: Principal = Depends(require_admin),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Charge this fleet's members their weekly fee.

    Idempotent per (fleet, ISO week): a re-run charges nobody twice, and the
    aggregate row is updated rather than duplicated. Rejects a fleet that is not
    ACTIVE — a suspended operator is not dispatching, so it is not billing.

    The session comes from `Depends(get_session_factory)`, not a direct call to
    `get_session_factory()`: this job opens its own sessions per member (one
    member must never abort the fleet's run), so it cannot borrow the request
    session. Taking the factory through the dependency is what lets tests
    substitute theirs — a direct call silently bypasses the override and the
    route ends up reading the production database.
    """
    settings = get_settings()
    service = FleetSettlementService(session_factory)
    return await service.run_weekly(fleet_id, settings.weekly_fee_hkd, period)
