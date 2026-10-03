"""Notification providers. Dev/test log; prod calls WhatsApp Cloud API for real.

P2-9: WhatsApp Cloud API is implemented over httpx (template or plain text).
Fail-closed: a prod OTP send with missing credentials raises — an OTP that
silently no-ops would lock the whole platform out. FCM v1 push needs a
service-account JWT (RS256); it stays an explicit stub until the credentials
land, and device-token wiring is owned by the driver/passenger apps.

P-2 adds email, for the registration verification link. Same seam as WhatsApp:
a dev implementation that logs, and an SMTP one that raises rather than
pretending when unconfigured.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

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
                "text": {"body": f"Your hkfastdc.com verification code is {code}"},
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
        # SEC-29: the token prefix used to be logged. A device token is a
        # credential — anyone holding it can push to that device — so log its
        # presence, not any part of its value.
        logger.info("[FCM:dev] %s / %s -> token=<redacted len=%d>", title, body, len(device_token))


def get_whatsapp_provider() -> WhatsAppProvider:
    if get_settings().app_env in ("dev", "test"):
        return DevWhatsAppProvider()
    return WhatsAppCloudProvider()


def get_fcm_provider() -> FcmProvider:
    if get_settings().app_env in ("dev", "test"):
        return DevFcmProvider()
    return FcmProvider()


# --------------------------------------------------------------------------- #
# Email (P-2)
# --------------------------------------------------------------------------- #


class EmailProvider:
    async def send_email(self, to: str, subject: str, body: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def send_verification_email(self, to: str, link: str) -> None:
        await self.send_email(
            to,
            "Verify your hkfastdc email address",
            f"Open this link to verify your email address:\n\n{link}\n\n"
            "If you did not create an account, ignore this message.",
        )
        logger.info("verification email sent to %s", _mask_email(to))


class DevEmailProvider(EmailProvider):
    """Logs instead of sending. Used in dev/test where there is no SMTP host."""

    async def send_email(self, to: str, subject: str, body: str) -> None:
        # The body carries the verification LINK, which is a bearer credential
        # for the address — but this branch only runs in dev/test, where the
        # link is also the only way to complete the flow by hand. Logged at
        # INFO with the recipient masked; never reachable when APP_ENV=prod
        # (`_fail_closed` requires SMTP_HOST there).
        logger.info("[EMAIL:dev] to=%s subject=%s\n%s", _mask_email(to), subject, body)


class SmtpEmailProvider(EmailProvider):
    """SMTP over implicit TLS or STARTTLS.

    Blocking `smtplib` inside a thread: the API is fully async and an SMTP
    handshake is a DNS lookup, a TCP connect, a TLS negotiation and up to five
    round-trips. Doing that on the event loop would stall every other request
    for the duration — the same reason the DB driver is async.
    """

    def _connect(self) -> smtplib.SMTP:
        s = get_settings()
        if not s.smtp_host or not s.smtp_from:
            raise RuntimeError("SMTP not configured: set SMTP_HOST and SMTP_FROM")
        if s.smtp_port == 465:
            # Implicit TLS: the socket is TLS from the first byte, so there is
            # no STARTTLS to negotiate.
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                s.smtp_host, s.smtp_port, timeout=10, context=ssl.create_default_context()
            )
        else:
            client = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10)
            if s.smtp_starttls:
                client.starttls(context=ssl.create_default_context())
        if s.smtp_username:
            client.login(s.smtp_username, s.smtp_password)
        return client

    def _send_blocking(self, to: str, subject: str, body: str) -> None:
        s = get_settings()
        message = EmailMessage()
        message["From"] = s.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with self._connect() as client:
            client.send_message(message)

    async def send_email(self, to: str, subject: str, body: str) -> None:
        import asyncio

        try:
            await asyncio.to_thread(self._send_blocking, to, subject, body)
        except (OSError, smtplib.SMTPException):
            # Log with the recipient masked and the address NOT included in the
            # message: a delivery failure is an operational event, and the
            # response to the user must not reveal whether the address exists.
            logger.exception("verification email delivery failed to=%s", _mask_email(to))
            raise


def _mask_email(address: str) -> str:
    """`dannison@example.com` -> `d*******@example.com`.

    The full address is PII under the PDPO and appears in logs that outlive the
    request, so keep the domain (useful for diagnosing a delivery problem) and
    reduce the local part to its first character.
    """
    local, _, domain = (address or "").partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}*******@{domain}"


def get_email_provider() -> EmailProvider:
    if get_settings().app_env in ("dev", "test"):
        return DevEmailProvider()
    return SmtpEmailProvider()
