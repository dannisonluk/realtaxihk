"""Order/driver status state machines — illegal transitions raise.

Every mutation of `Order.status` is supposed to go through
`assert_order_transition` first. The tables below are therefore a *security*
artefact as much as a domain one: they are what makes "a CANCELLED order cannot
be completed" an invariant rather than a convention.

Two properties the tables are expected to hold, and which
`tests/test_state_machine.py` asserts directly (they are cheap to check and
expensive to get wrong):

  1. **Terminal states have no outgoing edges.** `COMPLETED` and `CANCELLED`
     are `set()`. A non-empty terminal row would let a finished trip be
     reopened, which corrupts every earnings figure derived from `orders` — and
     those figures are what the weekly settlement and the analytics API read.
  2. **`CANCELLED` is reachable only before the trip starts** — from `CREATED`,
     `BROADCASTING`, `ACCEPTED`, `DRIVER_ARRIVED`, but never from `IN_TRIP`. A
     trip in progress is ended by completion, not cancellation.

`IN_TRIP: {COMPLETED}` is currently a dead end by design — the trip can only
finish. P4 (`docs/IN_TRIP_REDESIGN.md`) widens this row with
`DESTINATION_CHANGED` and `INTERRUPTED`; when that lands, both properties above
must be re-checked, because `INTERRUPTED` is terminal while
`DESTINATION_CHANGED` is not, and a hasty edit to this dict is exactly how
property 1 gets broken.
"""

from __future__ import annotations

from app.core.exceptions import BusinessRuleError
from app.models import DriverStatus, OrderStatus

# Read the module docstring before editing. The two invariants it names are
# asserted by tests; adding a status without updating ORDER_TRANSITIONS leaves
# it unreachable, and removing one leaves a dangling edge.
ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {OrderStatus.BROADCASTING, OrderStatus.CANCELLED},
    OrderStatus.BROADCASTING: {OrderStatus.ACCEPTED, OrderStatus.CANCELLED},
    OrderStatus.ACCEPTED: {OrderStatus.DRIVER_ARRIVED, OrderStatus.CANCELLED},
    OrderStatus.DRIVER_ARRIVED: {OrderStatus.IN_TRIP, OrderStatus.CANCELLED},
    OrderStatus.IN_TRIP: {OrderStatus.COMPLETED},
    OrderStatus.COMPLETED: set(),
    OrderStatus.CANCELLED: set(),
}

DRIVER_TRANSITIONS: dict[DriverStatus, set[DriverStatus]] = {
    DriverStatus.PENDING_KYC: {DriverStatus.DEPOSIT_REQUIRED, DriverStatus.TERMINATED},
    DriverStatus.DEPOSIT_REQUIRED: {DriverStatus.ACTIVE, DriverStatus.TERMINATED},
    DriverStatus.ACTIVE: {DriverStatus.SUSPENDED, DriverStatus.TERMINATED},
    DriverStatus.SUSPENDED: {DriverStatus.ACTIVE, DriverStatus.TERMINATED},
    DriverStatus.TERMINATED: set(),
}


def assert_order_transition(current: OrderStatus, target: OrderStatus) -> None:
    if target not in ORDER_TRANSITIONS.get(current, set()):
        raise BusinessRuleError(
            f"Illegal order transition {current.value} -> {target.value}",
            {"from": current.value, "to": target.value},
        )


def assert_driver_transition(current: DriverStatus, target: DriverStatus) -> None:
    if target not in DRIVER_TRANSITIONS.get(current, set()):
        raise BusinessRuleError(
            f"Illegal driver status transition {current.value} -> {target.value}",
            {"from": current.value, "to": target.value},
        )
