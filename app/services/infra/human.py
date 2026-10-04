"""Human verification (CAPTCHA) at the front door of the auth surfaces.

What it defends
---------------
Registration, login, and every "send me a code" endpoint. Those are the three
things worth automating against this platform, for three different reasons:

* **registration** creates rows and sends email — a script can fill the table
  and burn the sending reputation of the domain;
* **login** is where a credential-stuffing list is spent, and every attempt
  costs an argon2 verify at 64 MiB, so it is also a cheap way to make *us* pay
  for someone else's CPU;
* **`otp/request`** costs money per call. A WhatsApp message is billed, and the
  classic abuse is a script turning a stranger's phone into a pager.

Why Cloudflare Turnstile
------------------------
It is the one that normally asks the user to do nothing: a background check
returns a token and a challenge appears only when the score is bad. A visible
image grid on the login screen of a taxi app costs conversions, and the abuse
being defended against here does not justify that price.

The provider shape
------------------
The same seam as `notify.py`: a dev double that accepts everything, a real
implementation that calls out, and a factory. `verify` returns a plain `bool`
rather than raising, because "the challenge failed" and "Cloudflare is
unreachable" are the same answer to the caller — refuse — and only the log needs
to tell them apart.

Where the HTTP refusal lives
----------------------------
Not here. `assert_human` in `app/core/deps.py` turns a `False` into a 403, so
this module stays a plain outbound call with no FastAPI import, and a script or
a job could use it.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger("realtaxihk.human")

# Turnstile's documented test keys. They are published in Cloudflare's docs,
# always pass, and exist so an integration can be exercised end to end without a
# real widget — which is exactly what a staging deploy wants. They are useless
# as production credentials, which is the point.
TURNSTILE_TEST_SITE_KEY = "1x00000000000000000000AA"
# The value below trips the "hardcoded password" lint, which it is by name only:
# it is published in Cloudflare's documentation and accepts every token — that is
# what it is for. Renaming it to dodge the rule would be worse, because the name
# is how an operator recognises it as the test key.
TURNSTILE_TEST_SECRET_KEY = "1x0000000000000000000000000000000AA"  # noqa: S105

_warned_unconfigured = False


class HumanVerifier:
    async def verify(self, token: str | None, remote_ip: str | None = None) -> bool:
        raise NotImplementedError  # pragma: no cover


class DevHumanVerifier(HumanVerifier):
    """Accepts everything. Reachable only when `app_env` is dev or test."""

    async def verify(self, token: str | None, remote_ip: str | None = None) -> bool:
        logger.debug("[HUMAN:dev] accepting token=%s", "present" if token else "absent")
        return True


class DisabledHumanVerifier(HumanVerifier):
    """No provider configured: allow, and say so once, loudly.

    Deliberately **not** a fail-closed branch. Refusing every login because a
    CAPTCHA key is missing converts a configuration gap into a total outage, and
    an outage nobody can sign in to diagnose is a worse outcome than the missing
    protection it would be defending. The real gate is
    `Settings._fail_closed`, which refuses to *start* in prod without a secret —
    so this class is reachable only in a deployment that has already bypassed
    that check, and there a log line an operator will see is the useful thing.

    Warns once per process. A per-request warning on a hot path is noise, and
    noise is what gets a real warning ignored.
    """

    async def verify(self, token: str | None, remote_ip: str | None = None) -> bool:
        global _warned_unconfigured
        if not _warned_unconfigured:
            _warned_unconfigured = True
            logger.warning(
                "human verification is NOT configured (TURNSTILE_SECRET_KEY is empty) — "
                "registration, login and OTP requests are open to scripted abuse"
            )
        return True


class TurnstileVerifier(HumanVerifier):
    """Cloudflare Turnstile `siteverify`.

    **Fails closed on every error path** — a non-2xx, a timeout, a body that is
    not JSON. Allowing on error would hand an attacker a trivial bypass: break
    the verification call and walk in. The only thing that can break it is
    Cloudflare being unreachable, and a CAPTCHA that cannot be checked is not a
    CAPTCHA, so refusing is the honest answer.

    The token is single-use and expires after 300 seconds. Cloudflare enforces
    both, so there is nothing to track on this side.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._secret = settings.turnstile_secret_key
        self._verify_url = settings.turnstile_verify_url
        self._expected_hostname = settings.turnstile_expected_hostname
        self._timeout = settings.turnstile_timeout_s

    async def verify(self, token: str | None, remote_ip: str | None = None) -> bool:
        if not token:
            # Absent, not invalid. Both refuse, but the log distinguishes them:
            # a steady stream of missing tokens is a client that was never
            # updated, while a stream of rejected ones is an attack.
            logger.info("turnstile: no token presented")
            return False

        payload = {"secret": self._secret, "response": token}
        if remote_ip:
            # Optional. Passed so Cloudflare can cross-check the address the
            # challenge was solved from; never a decision on its own.
            payload["remoteip"] = remote_ip

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._verify_url, data=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError):
            # `exception` rather than `error`: the traceback is the only way to
            # tell a DNS failure from a TLS failure from a 500, and this is a
            # refusal path where the reason is otherwise invisible.
            logger.exception("turnstile verification failed — refusing")
            return False

        if not isinstance(body, dict) or not body.get("success"):
            logger.warning(
                "turnstile rejected a token: %s",
                body.get("error-codes") if isinstance(body, dict) else body,
            )
            return False

        if self._expected_hostname:
            hostname = str(body.get("hostname") or "")
            if hostname != self._expected_hostname:
                # A token is only meaningful for the site it was solved on. A
                # mismatch means a widget on another domain minted it, so it
                # proves nothing about *our* visitor.
                logger.warning(
                    "turnstile hostname mismatch: got %r, expected %r",
                    hostname,
                    self._expected_hostname,
                )
                return False

        return True


def get_human_verifier() -> HumanVerifier:
    """The verifier for this deployment.

    A factory rather than a module-level singleton so a test can swap the class
    and so the settings are read per call, matching `get_whatsapp_provider`.
    """
    settings = get_settings()
    if settings.app_env in ("dev", "test"):
        return DevHumanVerifier()
    if not settings.turnstile_secret_key:
        return DisabledHumanVerifier()
    return TurnstileVerifier()
