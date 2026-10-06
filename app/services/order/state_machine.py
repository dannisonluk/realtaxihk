"""Order/driver status state machines — illegal transitions raise.

Every mutation of `Order.status` is supposed to go through
`assert_order_transition` first. The tables below are therefore a *security*
artefact as much as a domain one: they are what makes "a CANCELLED order cannot
be completed" an invariant rather than a convention.

The properties the tables hold, and which `tests/api/test_orders_module.py`
(`TestStateMachineInvariants`) asserts directly — they are cheap to check and
expensive to get wrong:

  1. **Terminal states have no outgoing edges.** `COMPLETED`, `INTERRUPTED` and
     `CANCELLED` are `set()`. A non-empty terminal row would let a finished trip
     be reopened, which corrupts every earnings figure derived from `orders` —
     and those figures are what the weekly settlement and the analytics API read.
  2. **`CANCELLED` is reachable only before the trip is confirmed started** —
     from `CREATED`, `BROADCASTING`, `ACCEPTED`, `PENDING_ARRIVAL_CONFIRM`, but
     never from `DRIVER_ARRIVED` or later. `DRIVER_ARRIVED` means arrival was
     proven by GPS *and* the passenger's own confirmation, so the cancel right
     is locked from that point on: the only ways out are `COMPLETED` and
     `INTERRUPTED`. P4 (`docs/IN_TRIP_REDESIGN.md` §2.2) tightened this — before
     it, `DRIVER_ARRIVED -> CANCELLED` was legal.

Two more properties live in the data rather than the graph, and are asserted by
the same test class because they are the same class of mistake:

  3. An order with `completed_at` set is `COMPLETED` — a completed timestamp on
     any other status is how a report counts a trip twice.
  4. An order in `DRIVER_ARRIVED` or later has `arrival_confirmed_at` — arrival
     is a two-sided fact, and a status that claims it without the proof is the
     exact defect the two-step arrival flow exists to prevent.
"""

from __future__ import annotations

from app.core.exceptions import BusinessRuleError
from app.models import DriverStatus, OrderStatus

# Read the module docstring before editing. The invariants it names are
# asserted by tests; adding a status without updating ORDER_TRANSITIONS leaves
# it unreachable, and removing one leaves a dangling edge.
#
# P4 note: `PENDING_ARRIVAL_CONFIRM` still allows `CANCELLED` (arrival is
# *claimed*, not proven, so the cancel right is not yet locked) but at
# defaulting cost — see `app/api/orders.py`. `DRIVER_ARRIVED` does not.
ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {OrderStatus.BROADCASTING, OrderStatus.CANCELLED},
    OrderStatus.BROADCASTING: {OrderStatus.ACCEPTED, OrderStatus.CANCELLED},
    OrderStatus.ACCEPTED: {
        OrderStatus.PENDING_ARRIVAL_CONFIRM,
        OrderStatus.CANCELLED,
    },
    # Waiting for the passenger to confirm the last 4 digits. A failed
    # confirmation returns the order to ACCEPTED (three strikes), which is why
    # that edge exists in both directions.
    OrderStatus.PENDING_ARRIVAL_CONFIRM: {
        OrderStatus.DRIVER_ARRIVED,
        OrderStatus.ACCEPTED,
        OrderStatus.CANCELLED,
    },
    # Arrival proven: the cancel right is locked here.
    OrderStatus.DRIVER_ARRIVED: {
        OrderStatus.IN_TRIP,
        # A trip can end before it starts (the passenger is taken ill getting
        # in). Without this edge the pair would be locked with no way out.
        OrderStatus.INTERRUPTED,
    },
    OrderStatus.IN_TRIP: {
        OrderStatus.COMPLETED,
        OrderStatus.DESTINATION_CHANGED,
        OrderStatus.INTERRUPTED,
    },
    # A destination change is not an end: the trip continues, so the normal
    # exit is back to IN_TRIP — but arriving immediately, or breaking down
    # mid-change, are both possible.
    OrderStatus.DESTINATION_CHANGED: {
        OrderStatus.IN_TRIP,
        OrderStatus.COMPLETED,
        OrderStatus.INTERRUPTED,
    },
    OrderStatus.INTERRUPTED: set(),
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

# Statuses from which the cancel right is locked (P4). Kept here, beside the
# table, so "who may cancel" is read off the same artefact as the graph itself
# rather than re-derived at each call site.
CANCEL_LOCKED_STATUSES = frozenset(
    {
        OrderStatus.DRIVER_ARRIVED,
        OrderStatus.IN_TRIP,
        OrderStatus.DESTINATION_CHANGED,
    }
)

# Statuses that are "under way" — the ones an interruption is a legal exit from.
INTERRUPTIBLE_STATUSES = frozenset(
    {
        OrderStatus.DRIVER_ARRIVED,
        OrderStatus.IN_TRIP,
        OrderStatus.DESTINATION_CHANGED,
    }
)


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
