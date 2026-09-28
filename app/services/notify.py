"""Notification providers. Dev builds log; prod wires WhatsApp Cloud API / FCM.

Selection is env-driven (app_env): dev uses a logging provider so tests and
local runs never hit external APIs; prod providers are implemented when the
Meta/Firebase credentials land (Module D integration).
"""
from __future__ import annotations

import logging

from app.core.config import get_settings
from app.core.masking import mask_phone

logger = logging.getLogger("realtaxihk.notify")


class WhatsAppProvider:
    async def send_otp(self, phone_e164: str, code: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def send_trip_update(self, phone_e164: str, message: str) -> None:  # pragma: no cover
        raise NotImplementedError


class DevWhatsAppProvider(WhatsAppProvider):
    async def send_otp(self, phone_e164: str, code: str) -> None:
        logger.info("[WHATSAPP:dev] OTP %s -> %s", code, mask_phone(phone_e164))

    async def send_trip_update(self, phone_e164: str, message: str) -> None:
        logger.info("[WHATSAPP:dev] %s -> %s", message, mask_phone(phone_e164))


class WhatsAppCloudProvider(WhatsAppProvider):
    """Meta WhatsApp Cloud API — credentials via env in prod deployment."""

    async def send_otp(self, phone_e164: str, code: str) -> None:  # pragma: no cover
        raise NotImplementedError("WhatsApp Cloud API not configured in this env")

    async def send_trip_update(self, phone_e164: str, message: str) -> None:  # pragma: no cover
        raise NotImplementedError("WhatsApp Cloud API not configured in this env")


class FcmProvider:
    async def push(self, device_token: str, title: str, body: str) -> None:  # pragma: no cover
        raise NotImplementedError


class DevFcmProvider(FcmProvider):
    async def push(self, device_token: str, title: str, body: str) -> None:
        logger.info("[FCM:dev] %s / %s -> token=%s…", title, body, device_token[:8])


def get_whatsapp_provider() -> WhatsAppProvider:
    if get_settings().app_env == "dev":
        return DevWhatsAppProvider()
    return WhatsAppCloudProvider()


def get_fcm_provider() -> FcmProvider:
    return DevFcmProvider()
