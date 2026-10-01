"""Driver schemas — profile, deposit, refund views.

The deposit is nested and **nullable**: `GET /drivers/me` returns
`"deposit": null` before a driver has one (fixture `driver_me_no_deposit.json`),
which is why `DepositOut | None` rather than a defaulted object. A defaulted
`{}` would make "no deposit row yet" and "a deposit row that is somehow empty"
indistinguishable, and the client renders different screens for those.

All three deposit amounts are `money_str` (2 dp): these are stored balances, not
meter readings.
"""

from __future__ import annotations

from pydantic import BaseModel

__all__ = [
    "DepositOut",
    "DriverProfileOut",
    "DriverProfileWithDepositOut",
    "RefundDecisionOut",
    "RefundOut",
    "RefundRequestOut",
    "RefundViewOut",
]


class DepositOut(BaseModel):
    """The driver's own deposit figures, at the canonical 2-dp wire form.

    `balance_hkd` is available money; `held_hkd` is locked pending a refund
    decision. They are separate columns rather than one "effective balance"
    because a refund request moves value from one to the other without the
    money having moved at all — see `RefundRequest` in `app/models/user.py`.

    **The two fields are optional, and the no-deposit path omits them
    entirely.** Before a driver has funded anything there is no balance and
    nothing held, so `GET /drivers/me` synthesises only
    `{required_hkd, is_fulfilled}` (fixture `driver_me_no_deposit.json`). The
    absence is load-bearing, not cosmetic:
    `test_refund_and_settlement.py::test_charges_active_drivers_and_skips_the_rest`
    asserts `"balance_hkd" not in <deposit>` to distinguish "never credited"
    from "credited, now at zero" — two states the UI renders differently.

    So the route carries `response_model_exclude_unset=True`, which drops any
    field the handler did not explicitly provide. That is why these two have
    defaults: the default exists so validation *succeeds* on the stub, and
    `exclude_unset` then ensures the key is *absent* rather than `"0.00"` or
    `null`. A default alone would have published a balance the driver does not
    have. The Dart client already tolerates the absence
    (`Money.parse(json['balance_hkd'] ?? '0.0')`), so both paths decode.
    """

    required_hkd: str
    is_fulfilled: bool
    balance_hkd: str = "0.00"
    held_hkd: str = "0.00"


class DriverProfileOut(BaseModel):
    """`POST /drivers/register` and the `driver` block of `GET /drivers/me`
    before any deposit exists.

    Note there is no `hk_id_last4` and no `taxi_driver_plate_no`-adjacent HKID
    material beyond what is listed: the API is the masking boundary, and the
    full HKID is never collected at all (only `hk_id_last4`, and not echoed).
    """

    id: str
    user_id: str
    status: str
    taxi_type: str
    taxi_driver_plate_no: str
    vehicle_reg_mark: str
    is_online: bool


class DriverProfileWithDepositOut(DriverProfileOut):
    """`GET /drivers/me` — the same profile plus the deposit block.

    Inheritance rather than a second flat model: the fields are genuinely the
    same ones, and duplicating them is how the two would drift apart. The only
    difference is the presence of `deposit`.
    """

    deposit: DepositOut | None = None


class RefundOut(BaseModel):
    """One refund request, as an admin sees it.

    `decided_by` is an **admin account** id, not a user id — different UUID
    space, different table (`app/models/admin.py`). It is absent from the
    driver-facing view on purpose: a driver has no way to act on it.
    """

    id: str
    driver_profile_id: str
    amount_hkd: str
    status: str
    note: str | None
    decision_note: str | None
    decided_by: str | None
    decided_at: str | None
    created_at: str


class RefundRequestOut(BaseModel):
    """`POST /drivers/me/refund/request` — the driver's own view of a new request.

    The same row as `RefundOut` minus `driver_profile_id` and `decided_by`.
    Kept separate rather than reusing `RefundOut` with optional fields, so the
    driver-facing contract cannot accidentally grow an admin-only field.
    """

    id: str
    amount_hkd: str
    status: str
    note: str | None
    decision_note: str | None
    decided_at: str | None
    created_at: str


class RefundViewOut(BaseModel):
    """`{"refund": <refund|null>}` — `GET /drivers/me/refund`.

    Wrapped in a key rather than returned bare so "no open refund" is
    distinguishable from an error, and so the shape can gain sibling keys
    (history, totals) without breaking clients.
    """

    refund: RefundRequestOut | None = None


class RefundDecisionOut(RefundOut):
    """`POST /admin/refunds/{id}/decision` — the decided refund.

    Identical to `RefundOut` today; declared as its own name so the two routes
    can diverge (a decision response gaining `ledger_entry_id`, say) without
    silently changing the list endpoint too.
    """
