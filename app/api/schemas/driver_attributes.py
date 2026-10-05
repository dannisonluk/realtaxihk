"""Driver attribute schemas — payment methods and in-car environment flags."""

from __future__ import annotations

from pydantic import BaseModel

__all__ = [
    "DriverEnvironmentOut",
    "DriverPaymentMethodsOut",
    "InCarEnvironmentIn",
    "PaymentMethodsIn",
]


class PaymentMethodsIn(BaseModel):
    methods: list[str]


class DriverPaymentMethodsOut(BaseModel):
    methods: list[str]


class InCarEnvironmentIn(BaseModel):
    silent_ride: bool = False
    no_radio_music: bool = False
    no_smoke: bool = False
    no_perfume: bool = False


class DriverEnvironmentOut(InCarEnvironmentIn):
    pass
