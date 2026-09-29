"""Notification providers. Dev/test log; prod calls WhatsApp Cloud API for real.

P2-9: WhatsApp Cloud API is implemented over httpx (template or plain text).
Fail-closed: a prod OTP send with missing credentials raises — an OTP that
silently no-ops would lock the whole platform out. FCM v1 push needs a
service-account JWT (RS256); it stays an explicit stub until the credentials
land, and device-token wiring is owned by the driver/passenger apps.
"""
from __future__ import annotations

import logging

import httpx

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
    """Meta WhatsApp Cloud API — credentials via env (P2-9)."""

    def _require_config(self) -> tuple[str, str, str]:
        s = get_settings()
        if not s.whatsapp_business_token or not s.whatsapp_phone_number_id:
            raise RuntimeError(
                "WhatsApp Cloud API not configured: set WHATSAPP_BUSINESS_TOKEN "
                "and WHATSAPP_PHONE_NUMBER_ID"
            )
        return s.whatsapp_business_token, s.whatsapp_phone_number_id, s.whatsapp_api_version

    async def _send(self, phone_e164: str, payload: dict) -> None:
        token, phone_id, version = self._require_config()
        url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code >= 400:
                logger.error(
                    "whatsapp send failed: status=%d body=%s", resp.status_code, resp.text[:300]
                )
                resp.raise_for_status()

    async def send_otp(self, phone_e164: str, code: str) -> None:
        s = get_settings()
        to = phone_e164.replace("+", "")
        if s.whatsapp_otp_template:
            payload = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "template",
                "template": {
                    "name": s.whatsapp_otp_template,
                    "language": {"code": "en"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": code}],
                        }
                    ],
                },
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": f"Your realtaxihk.com verification code is {code}"},
            }
        await self._send(phone_e164, payload)

    async def send_trip_update(self, phone_e164: str, message: str) -> None:
        await self._send(
            phone_e164,
            {
                "messaging_product": "whatsapp",
                "to": phone_e164.replace("+", ""),
                "type": "text",
                "text": {"body": message},
            },
        )


class FcmProvider:
    async def push(self, device_token: str, title: str, body: str) -> None:  # pragma: no cover
        raise NotImplementedError(
            "FCM v1 push requires a service account (FCM_CREDENTIALS_JSON) — "
            "wire google-auth JWT signing before enabling"
        )


class DevFcmProvider(FcmProvider):
    async def push(self, device_token: str, title: str, body: str) -> None:
        logger.info("[FCM:dev] %s / %s -> token=%s…", title, body, device_token[:8])


def get_whatsapp_provider() -> WhatsAppProvider:
    if get_settings().app_env in ("dev", "test"):
        return DevWhatsAppProvider()
    return WhatsAppCloudProvider()


def get_fcm_provider() -> FcmProvider:
    if get_settings().app_env in ("dev", "test"):
        return DevFcmProvider()
    return FcmProvider()
