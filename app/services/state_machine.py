"""Order/driver status state machines — illegal transitions raise."""

from __future__ import annotations

from app.core.exceptions import BusinessRuleError
from app.models import DriverStatus, OrderStatus

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
