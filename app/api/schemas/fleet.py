"""Fleet schemas — the fleet block, membership, roster rows and settlements.

Note `weekly_fee_discount_percent` changes wire form between routes, and the
fixtures prove it rather than leaving it to chance:

- `fleet_created.json` (create, 201)  → `"25"`
- `fleet_me.json` / `fleet_detail.json` → `"25.00"`

Both are strings, so a client parsing to Decimal is unaffected, but the digits
differ. It is declared `str` here for exactly that reason: pinning it to
`Decimal` would make pydantic normalise one of the two and quietly change a
response. If the inconsistency is ever worth removing, it should be removed in
the serializers and then this comment deleted — not papered over here.

Two membership shapes exist as well, and they are not the same object:

- `FleetMembershipOut` — the **public/member** view, `{member_role, joined_at}`
  nested inside `GET /fleets/me` (fixture `fleet_me.json`).
- `FleetMemberRowOut` — the **roster row**, which joins in the driver's
  `taxi_type` and `status` so an operator can see who is on the roster without a
  second request (fixtures `fleet_members.json`, `admin_fleet_members.json`).

Sharing one model between those two would mean either padding the nested object
with fields it does not have or dropping the joined columns from the roster.
"""

from __future__ import annotations

from pydantic import BaseModel

__all__ = [
    "FleetMemberListOut",
    "FleetMemberRowOut",
    "FleetMembershipOut",
    "FleetOut",
    "FleetPageOut",
    "FleetSettlementListOut",
    "FleetSettlementRowOut",
    "FleetSettlementRunOut",
    "FleetViewOut",
    "SettlementRunOut",
]


class FleetOut(BaseModel):
    """The fleet block, shared by the admin console and the member view.

    `member_count` is a computed join, not a column — it is `0` on create
    (fixture `fleet_created.json`) and the true count on read.
    """

    id: str
    name: str
    license_no: str  # Transport Department fleet licence number
    status: str
    weekly_fee_discount_percent: str  # see the module docstring on the two forms
    contact_name: str | None
    contact_phone: str | None
    note: str | None
    created_at: str
    member_count: int


class FleetMembershipOut(BaseModel):
    """The nested membership object inside `GET /fleets/me`."""

    member_role: str
    joined_at: str


class FleetViewOut(BaseModel):
    """`{"fleet", "membership"}` — `GET /fleets/me`.

    **Both keys are nullable and both go null together** when the caller is not
    on a roster (fixture `fleet_me_none.json` is `{"fleet": null,
    "membership": null}`). The keys are still present, so a client can
    distinguish "not a member" from "request failed" without inspecting the
    status code.
    """

    fleet: FleetOut | None = None
    membership: FleetMembershipOut | None = None


class FleetMemberRowOut(BaseModel):
    """A roster row — the driver's identity joined onto the membership.

    `driver_profile_id` is the profile id, not a user id. `left_at` is set only
    when `status` has moved off ACTIVE; rows are never deleted on leave, so a
    past week's billing stays attributable (see `FleetMembership`).
    """

    driver_profile_id: str
    taxi_type: str
    driver_status: str
    member_role: str
    status: str
    joined_at: str
    left_at: str | None


class FleetSettlementRowOut(BaseModel):
    """One fleet's weekly settlement, as a **member** sees it.

    The per-driver ledger entries carry the authoritative money; this row is the
    aggregate the operator is shown. `collected_hkd` is therefore a summary, and
    `charged + skipped + failed + tampered` should account for `member_count` —
    a mismatch means the platform and the operator disagree.

    `created_at` is present **only** on this read shape. It is absent from
    `POST .../settlement/run`'s own response (fixture
    `admin_fleet_settlement_run.json`), because a run reports what it just did
    and the row's timestamp is read back from the history endpoint.
    """

    period: str
    fee_hkd: str
    discount_percent: str
    member_count: int
    charged: int
    skipped: int
    failed: int
    tampered: int
    collected_hkd: str
    created_at: str


class FleetSettlementRunOut(BaseModel):
    """`POST /admin/fleets/{id}/settlement/run` — one run, plus its context.

    Adds `fleet_id`/`fleet_name` and a `gross_fee_hkd`: the fee before the
    volume discount, which is what makes `discount_percent` legible to the
    operator receiving it. A re-run **updates** the (fleet, ISO-week) row rather
    than appending, so this is the current state of that week, not a history.

    Declared fresh rather than inheriting `FleetSettlementRowOut`, because it
    **does not carry `created_at`** — the run endpoint returns the totals it just
    computed (`app/services/fleet_service.py::run_weekly`), not the reloaded
    row. Inheriting would have published a field the route never sends, and a
    client trusting the schema would read `undefined` for it.
    """

    fleet_id: str
    fleet_name: str
    period: str
    gross_fee_hkd: str
    discount_percent: str
    fee_hkd: str
    member_count: int
    charged: int
    skipped: int
    failed: int
    tampered: int
    collected_hkd: str


class FleetMemberListOut(BaseModel):
    """`GET /fleets/{id}/members` and its admin twin — `{"items": [...]}`.

    Same shape for both audiences: the roster row is already the least an
    operator and a member can both be shown (`_member_out` excludes the identity
    documents), so there is nothing to redact on the member side. Typed rather
    than `ListEnvelope` so the element contract is published.
    """

    items: list[FleetMemberRowOut]


class FleetPageOut(BaseModel):
    """`GET /admin/fleets` — offset-paginated, unlike the member views.

    The console shows a count and lets an operator jump to a page, which is why
    this carries `total`/`limit`/`offset` where the roster and settlement lists
    do not.
    """

    items: list[FleetOut]
    total: int
    limit: int
    offset: int


class FleetSettlementListOut(BaseModel):
    """`GET /fleets/{id}/settlement` and the admin equivalent — `{"items": [...]}`.

    Typed rather than reusing the generic `ListEnvelope`: the items are a known
    model, and declaring that is what makes the element shape part of the
    published contract instead of `Any`. Both the member and the admin route
    return this exact shape.

    No pagination keys: the history is one row per ISO week per fleet, so it
    grows at a rate a fleet could never read out.
    """

    items: list[FleetSettlementRowOut]


class SettlementRunOut(BaseModel):
    """`POST /admin/settlement/weekly/run` — the platform-wide weekly run.

    `fleet_managed` is the count of drivers deliberately **skipped** by this run
    because their fleet bills them instead (fixtures: `fleet_managed: 1` with
    `charged: 0`). Without it, the skip would look like a silent failure; with
    it, the operator can see the platform did the right thing. The two services
    must stay mutually exclusive — see `FleetSettlementService.run_weekly` and
    `SettlementService.run_weekly`.
    """

    period: str
    fee_hkd: str
    eligible_drivers: int
    fleet_managed: int
    charged: int
    skipped: int
    failed: int
    tampered: int
