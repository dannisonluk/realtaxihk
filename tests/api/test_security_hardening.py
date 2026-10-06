"""Regression tests for the network security audit (docs/SECURITY_AUDIT.md).

Each class pins one finding so it cannot silently come back. These are
behavioural tests, not code-shape tests: they assert the exploit no longer works
(or the safe path now does), which is what the audit proved with live probes.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, cast

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app.core.config import Settings
from app.core.exceptions import BusinessRuleError

# A secret that satisfies the entropy check (>= 32 chars, >= 8 distinct chars).
_STRONG_SECRET = "Zx9q7Lm2Wp4Rt6Yk8Bn3Vc5Hj1Sd0Fg6"


def _phone() -> str:
    return f"+8529{uuid.uuid4().int % 10**7:07d}"


def _settings(**overrides: object) -> Settings:
    """`Settings` with the `.env` file switched off.

    `_env_file` is a real `BaseSettings` parameter, but a type checker cannot see
    it: pyright synthesises `Settings.__init__` from the pydantic *fields*
    (verified with `reveal_type`), and `_env_file` is not a field. Unpacking a
    dict keeps the runtime call byte-identical while leaving the checker a
    signature it can actually check. `_prod` below already leans on the same
    trick.
    """
    values: dict[str, Any] = {"_env_file": None, **overrides}
    return Settings(**values)


def _login(client, phone: str) -> dict:
    """The whole session body for `phone`, via the real OTP login.

    `client.otp_login` arranges the precondition an OTP login now needs — a number
    some account has already *proven* — and backdates the previous OTP rows so a
    second sign-in inside one test is possible. SEC-02 is asserted at the notify
    seam inside it: the code never appears in the response.
    """
    return client.otp_login(phone)


# --------------------------------------------------------------------------- #
# SEC-01~05 — config must fail closed
# --------------------------------------------------------------------------- #
class TestConfigFailClosed:
    def test_missing_app_env_refuses_to_boot(self, monkeypatch):
        """SEC-01: no default. An unset APP_ENV used to mean app_env='dev', which
        served a fixed OTP code in the response on a host with no .env."""
        monkeypatch.delenv("APP_ENV", raising=False)
        with pytest.raises(ValidationError, match="APP_ENV is not set"):
            _settings(jwt_secret_key=_STRONG_SECRET)

    def test_non_whitelisted_app_env_rejected(self):
        """SEC-04: `== 'prod'` let 'production'/'PROD'/'staging' skip prod checks."""
        with pytest.raises(ValidationError, match="must be one of"):
            _settings(app_env="production", jwt_secret_key=_STRONG_SECRET)

    def test_prod_rejects_dev_jwt_secret(self):
        with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
            _settings(
                app_env="prod",
                jwt_secret_key="dev-only-secret-change-in-prod-0123456789abcdef",
                postgres_password="a-real-password",
            )

    def test_prod_rejects_dev_otp_switch(self):
        """SEC-02: the dev OTP shortcut is not a prod option."""
        with pytest.raises(ValidationError, match="ALLOW_DEV_OTP"):
            _settings(
                app_env="prod",
                jwt_secret_key=_STRONG_SECRET,
                postgres_password="a-real-password",
                allow_dev_otp=True,
            )

    def test_short_secret_rejected(self):
        with pytest.raises(ValidationError, match="at least"):
            _settings(app_env="dev", jwt_secret_key="abc123")

    def test_low_entropy_secret_rejected(self):
        """Length alone is not entropy: 64 identical characters is one guess."""
        with pytest.raises(ValidationError, match="entropy"):
            _settings(app_env="dev", jwt_secret_key="x" * 64)

    # --- prod CORS / proxy-trust (found by the 2026-10-12 review) --------- #

    @staticmethod
    def _prod(**over):
        """A minimal prod config that passes every check except the one under
        test. Named so a failure here reads as "the prod baseline broke", not as
        the assertion being wrong.

        Every prod-required setting has to be present here, which is the point:
        adding a requirement without adding it to the baseline turns this into a
        failing test rather than a silent extra refusal on some other assertion.
        """
        base: dict = {
            "_env_file": None,
            "app_env": "prod",
            "jwt_secret_key": _STRONG_SECRET,
            "postgres_password": "a-real-password",
            "smtp_host": "smtp.example.com",
            "smtp_from": "noreply@example.com",
            "public_base_url": "https://api.example.com",
            "trusted_proxy_count": 1,
            "cors_origins": ["https://admin.example.com"],
            "turnstile_secret_key": "a-real-turnstile-secret",
        }
        base.update(over)
        return base

    def test_prod_baseline_is_accepted(self):
        """Guards the tests below: if the baseline itself stopped passing, every
        other assertion in this block would 'pass' for the wrong reason."""
        assert Settings(**self._prod()).app_env == "prod"

    def test_prod_rejects_a_missing_turnstile_secret(self):
        """The one missing setting that is invisible *and* expensive.

        `DisabledHumanVerifier` fails **open** at runtime — it logs once and
        allows everything — so a deploy that forgot the key looks healthy while
        registration, login and every billed `otp/request` are open to a script.
        Refusing to start is the only way that gap surfaces before an attacker
        finds it.
        """
        with pytest.raises(ValidationError, match="TURNSTILE_SECRET_KEY"):
            Settings(**self._prod(turnstile_secret_key=""))

    def test_prod_rejects_default_cors_origins(self):
        """The four loopback origins are a dev convenience. Leaving them in prod
        is worse than useless: `http://localhost:8081` is an origin anyone can
        serve, so a page on a laptop could reach the production API with the
        operator's cookies."""
        with pytest.raises(ValidationError, match="CORS_ORIGINS"):
            Settings(**self._prod(cors_origins=[]))

    def test_prod_rejects_wildcard_cors_origin(self):
        """A wildcard plus `allow_credentials=True` lets any site read
        authenticated responses."""
        with pytest.raises(ValidationError, match="must not contain"):
            Settings(**self._prod(cors_origins=["*"]))

    def test_prod_rejects_plain_http_cors_origin(self):
        """A plain-http origin admits a network attacker into a session that
        carries cookies."""
        with pytest.raises(ValidationError, match="https://"):
            Settings(**self._prod(cors_origins=["http://admin.example.com"]))

    def test_prod_rejects_zero_trusted_proxies(self):
        """SEC-07: with 0, X-Forwarded-For is ignored entirely — behind the
        documented nginx deploy that collapses every per-IP rate limit into one
        shared bucket, so one abuser throttles every user."""
        with pytest.raises(ValidationError, match="TRUSTED_PROXY_COUNT"):
            Settings(**self._prod(trusted_proxy_count=0))

    def test_dev_is_unaffected_by_the_prod_cors_checks(self, monkeypatch):
        """The dev defaults must keep working — this is the whole point of
        scoping the new checks to `app_env == 'prod'`."""
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        s = _settings(app_env="dev", jwt_secret_key=_STRONG_SECRET)
        assert s.trusted_proxy_count == 0
        assert len(s.cors_origins) == 4

    def test_dev_otp_needs_the_explicit_switch(self, monkeypatch):
        # The suite does NOT set ALLOW_DEV_OTP (it reads codes from the notify
        # seam), so "switch off" is already the ambient state; clear it anyway
        # to keep the assertion independent of the developer's own environment.
        monkeypatch.delenv("ALLOW_DEV_OTP", raising=False)
        on = _settings(app_env="dev", jwt_secret_key=_STRONG_SECRET, allow_dev_otp=True)
        off = _settings(app_env="dev", jwt_secret_key=_STRONG_SECRET)
        assert on.dev_otp_enabled is True
        assert off.dev_otp_enabled is False


# --------------------------------------------------------------------------- #
# SEC-07 — X-Forwarded-For must not be client-controlled
# --------------------------------------------------------------------------- #
class TestForwardedFor:
    def test_rotating_xff_does_not_reset_the_ip_bucket(self, client):
        """With no trusted proxy configured the header is ignored entirely, so
        rotating it cannot mint a fresh rate-limit bucket per request."""
        from app.core.config import get_settings

        limit = get_settings().otp_ip_rate_limit
        statuses = [
            client.post(
                "/api/v1/auth/otp/request",
                json={"phone_e164": _phone()},
                headers={"X-Forwarded-For": f"203.0.113.{i}"},
            ).status_code
            for i in range(limit + 3)
        ]
        assert statuses.count(429) >= 3, statuses

    def test_rightmost_hop_is_used_when_a_proxy_is_trusted(self, monkeypatch):
        """With one trusted hop, the last element is the peer nginx actually saw."""
        # Imported from its own module, not from a route module: `_client_ip` was
        # five byte-identical copies and is now one function in
        # `app/core/client_ip.py`. The test reads it there so it keeps testing the
        # implementation rather than one endpoint's import of it.
        from app.core.client_ip import client_ip

        class _Req:
            def __init__(self):
                self.headers = {"x-forwarded-for": "1.2.3.4, 5.6.7.8, 9.9.9.9"}
                self.client = type("C", (), {"host": "10.0.0.1"})()

        monkeypatch.setattr(
            "app.core.client_ip.get_settings",
            lambda: _settings(
                app_env="dev",
                jwt_secret_key=_STRONG_SECRET,
                trusted_proxy_count=1,
            ),
        )
        assert client_ip(cast(Request, _Req())) == "9.9.9.9"

    def test_the_attacker_supplied_prefix_is_never_returned(self, monkeypatch):
        """The specific SEC-07 defect: `[0]` is the part a client controls.

        This is the assertion that would have failed against the original
        implementation, where `x_forwarded_for.split(',')[0]` returned `1.2.3.4`
        — an address the caller chose.
        """
        from app.core.client_ip import client_ip

        class _Req:
            def __init__(self):
                # nginx APPENDS the real peer, so the client's own value sits
                # first and the trustworthy one sits last.
                self.headers = {"x-forwarded-for": "6.6.6.6, 198.51.100.7"}
                self.client = type("C", (), {"host": "10.0.0.1"})()

        monkeypatch.setattr(
            "app.core.client_ip.get_settings",
            lambda: _settings(
                app_env="dev",
                jwt_secret_key=_STRONG_SECRET,
                trusted_proxy_count=1,
            ),
        )
        assert client_ip(cast(Request, _Req())) == "198.51.100.7"

    def test_exact_hop_count_returns_the_real_peer(self, monkeypatch):
        """The standard single-proxy deployment appends exactly one hop.

        `docker-compose.prod.yml` sets `TRUSTED_PROXY_COUNT=1` behind nginx,
        whose `$proxy_add_x_forwarded_for` appends the peer it actually saw. A
        client cannot shrink that to one hop: it would have to send an XFF the
        proxy does not append to, which nginx always does. So an exactly-one-hop
        header is the real peer, not a client-minted identity — and refusing it
        would collapse every per-IP rate limit onto the proxy's address.
        """
        from app.core.client_ip import client_ip

        class _Req:
            def __init__(self):
                self.headers = {"x-forwarded-for": "198.51.100.7"}
                self.client = type("C", (), {"host": "10.0.0.1"})()

        monkeypatch.setattr(
            "app.core.client_ip.get_settings",
            lambda: _settings(
                app_env="dev",
                jwt_secret_key=_STRONG_SECRET,
                trusted_proxy_count=1,
            ),
        )
        assert client_ip(cast(Request, _Req())) == "198.51.100.7"

    def test_fewer_hops_than_proxies_falls_back_to_x_real_ip(self, monkeypatch):
        """A header with fewer hops than the configured chain is not trusted —
        an upstream proxy that did not forward one leaves a client-controlled
        leftmost value. nginx's `X-Real-IP` is then used, and only if that too
        is absent does the transport peer answer.
        """
        from app.core.client_ip import client_ip

        class _Req:
            def __init__(self):
                self.headers = {
                    "x-forwarded-for": "6.6.6.6",
                    "x-real-ip": "198.51.100.7",
                }
                self.client = type("C", (), {"host": "10.0.0.1"})()

        monkeypatch.setattr(
            "app.core.client_ip.get_settings",
            lambda: _settings(
                app_env="dev",
                jwt_secret_key=_STRONG_SECRET,
                trusted_proxy_count=2,
            ),
        )
        assert client_ip(cast(Request, _Req())) == "198.51.100.7"

    def test_no_header_falls_back_to_x_real_ip_then_peer(self, monkeypatch):
        """Without any X-Forwarded-For, `X-Real-IP` (overwritten by nginx with
        the peer it saw) is the safe answer; without that either, the transport
        peer is used rather than a made-up value.
        """
        from app.core.client_ip import client_ip

        class _Req:
            def __init__(self):
                self.headers = {"x-real-ip": "198.51.100.7"}
                self.client = type("C", (), {"host": "10.0.0.1"})()

        monkeypatch.setattr(
            "app.core.client_ip.get_settings",
            lambda: _settings(
                app_env="dev",
                jwt_secret_key=_STRONG_SECRET,
                trusted_proxy_count=1,
            ),
        )
        assert client_ip(cast(Request, _Req())) == "198.51.100.7"

        class _NoRealIp:
            def __init__(self):
                self.headers = {}
                self.client = type("C", (), {"host": "10.0.0.1"})()

        monkeypatch.setattr(
            "app.core.client_ip.get_settings",
            lambda: _settings(
                app_env="dev",
                jwt_secret_key=_STRONG_SECRET,
                trusted_proxy_count=1,
            ),
        )
        assert client_ip(cast(Request, _NoRealIp())) == "10.0.0.1"

    def test_every_module_shares_one_implementation(self):
        """Five byte-identical copies is five places to reintroduce SEC-07."""
        from app.api import admin_auth, auth, fare, identity, licence
        from app.core.client_ip import client_ip

        for module in (auth, admin_auth, fare, identity, licence):
            assert module.client_ip is client_ip, module.__name__


# --------------------------------------------------------------------------- #
# SEC-08 — no single shared OTP counter may lock the whole platform
# --------------------------------------------------------------------------- #
class TestOtpGlobalCap:
    def test_crossing_the_soft_cap_does_not_lock_the_platform(self, client, monkeypatch):
        """SEC-08: the old code gated on a single shared key, so once the cap was
        crossed EVERY user got 429 — a one-attacker login DoS. With the cap at 2,
        the third distinct number is now still served (the counter only tightens
        the per-phone budget)."""
        from app.core.config import get_settings

        s = get_settings()
        monkeypatch.setattr(s, "otp_global_hourly_limit", 2, raising=False)
        monkeypatch.setattr(s, "otp_global_hourly_hard_limit", 5000, raising=False)
        monkeypatch.setattr(s, "otp_phone_rate_limit_strict", 1, raising=False)
        monkeypatch.setattr(s, "otp_ip_rate_limit", 100, raising=False)

        statuses = [
            client.post("/api/v1/auth/otp/request", json={"phone_e164": _phone()}).status_code
            for _ in range(3)
        ]
        # Under the old shared-counter gate the third call returned 429.
        assert statuses == [200, 200, 200], statuses

    def test_hard_ceiling_sheds_load_with_503(self, client, monkeypatch):
        """Past the hard ceiling we shed load explicitly (503), not with a 429 that
        reads like "you personally are rate-limited"."""
        from app.core.config import get_settings

        s = get_settings()
        monkeypatch.setattr(s, "otp_global_hourly_hard_limit", 0, raising=False)
        r = client.post("/api/v1/auth/otp/request", json={"phone_e164": _phone()})
        assert r.status_code == 503
        assert r.json()["code"] == "SERVICE_UNAVAILABLE"


# --------------------------------------------------------------------------- #
# SEC-09~11 — input size caps
# --------------------------------------------------------------------------- #
class TestInputCaps:
    def test_too_many_tunnels_rejected(self, client):
        r = client.post(
            "/api/v1/fare/estimate",
            json={
                "taxi_type": "URBAN",
                "distance_km": "5",
                "tunnels": ["cross_harbour"] * 9,
            },
        )
        assert r.status_code == 422

    def test_oversized_body_rejected_before_parsing(self, client):
        """SEC-10: a 63 MB body used to be accepted (HTTP 200)."""
        from app.core.config import get_settings

        padding = "x" * (get_settings().max_request_body_bytes + 4096)
        r = client.post(
            "/api/v1/fare/estimate",
            json={"taxi_type": "URBAN", "distance_km": "5", "pickup_address": padding},
        )
        assert r.status_code == 413
        assert r.json()["code"] == "PAYLOAD_TOO_LARGE"

    def test_oversized_chunked_body_rejected_with_413(self, client):
        """SEC-09~11: a chunked body (no `Content-Length`) must also answer 413.

        `json=` makes httpx compute a Content-Length, so the test above only
        exercises enforcement point #1. Passing an iterator instead makes httpx
        use `Transfer-Encoding: chunked`, which reaches the metered receive and
        is the path that used to answer 400 `BAD_REQUEST` — FastAPI's broad
        `except Exception` around body parsing converted the raise before the
        middleware's own handler could see it (NEW-9). The cap is now enforced
        by replacing the body with an empty final chunk and rewriting the
        response to 413, so nothing oversized is parsed at all.
        """
        from app.core.config import get_settings

        cap = get_settings().max_request_body_bytes
        chunk = b"x" * (cap // 2 + 4096)

        def body():
            yield chunk
            yield chunk

        r = client.post(
            "/api/v1/fare/estimate",
            content=body(),
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 413
        assert r.json()["code"] == "PAYLOAD_TOO_LARGE"

    def test_normal_body_still_accepted(self, client):
        r = client.post("/api/v1/fare/estimate", json={"taxi_type": "URBAN", "distance_km": "5"})
        assert r.status_code == 200


class TestHttpErrorCodeMap:
    """Every status the API raises must have a stable machine-readable `code`.

    The client branches on `code`, not on `status` (`admin-web/web/src/api/
    client.ts`). An unmapped status silently degrades to the generic
    `HTTP_ERROR`, so a refusal the caller is supposed to *handle differently*
    becomes indistinguishable from any other.

    `423` is the case that matters: P4 returns it for a driver whose deposit is
    in arrears (`DEPOSIT_INSUFFICIENT`), deliberately not 403, because 403 means
    "you may not" and 423 means "not while this is true" — the driver can fix it
    by topping up. Before this test the constant was simply absent from
    `code_map`, so the whole distinction was lost on the wire while every
    backend test stayed green.
    """

    @staticmethod
    def _probe(status_code: int, detail):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.exceptions import HTTPException as StarletteHTTPException

        from app.core.exceptions import register_exception_handlers

        probe = FastAPI()
        register_exception_handlers(probe)

        @probe.get("/probe")
        async def _raise():
            raise StarletteHTTPException(status_code=status_code, detail=detail)

        with TestClient(probe, raise_server_exceptions=False) as c:
            return c.get("/probe")

    def test_locked_maps_to_locked_not_the_generic_code(self):
        r = self._probe(423, {"reason": "DEPOSIT_INSUFFICIENT"})
        assert r.status_code == 423
        assert r.json()["code"] == "LOCKED"

    def test_structured_detail_without_message_promotes_reason(self):
        """The guards send `{"reason": ...}` and no `message`.

        Without the fallback the operator saw the literal "Request failed."
        while the actual reason sat unread in `details`.
        """
        r = self._probe(423, {"reason": "DEPOSIT_INSUFFICIENT", "balance_hkd": "-120.00"})
        body = r.json()
        assert body["message"] == "DEPOSIT_INSUFFICIENT"
        assert body["details"]["balance_hkd"] == "-120.00"

    def test_explicit_message_still_wins_over_reason(self):
        r = self._probe(403, {"message": "account disabled", "reason": "PHONE_REVERIFY_DUE"})
        assert r.json()["message"] == "account disabled"

    def test_rate_limited_keeps_retry_after(self):
        """429 is a *time* verdict, so the header is part of the contract."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.exceptions import HTTPException as StarletteHTTPException

        from app.core.exceptions import register_exception_handlers

        probe = FastAPI()
        register_exception_handlers(probe)

        @probe.get("/probe")
        async def _raise():
            # `detail` is `str | None` on Starlette's `HTTPException` but `Any`
            # on FastAPI's, and this test uses the Starlette one on purpose: the
            # point is that the *handler* copes with a raw starlette exception.
            # A structured detail is what the handler serialises, so the dict is
            # correct at runtime and only the stub disagrees.
            raise StarletteHTTPException(
                status_code=429,
                detail={"reason": "COOLDOWN"},  # pyright: ignore[reportArgumentType]
                headers={"Retry-After": "900"},
            )

        with TestClient(probe, raise_server_exceptions=False) as c:
            r = c.get("/probe")
        assert r.status_code == 429
        assert r.json()["code"] == "RATE_LIMITED"
        assert r.headers["retry-after"] == "900"

    def test_413_uses_the_non_deprecated_constant(self):
        """Guards against a silent regression to the deprecated spelling.

        `HTTP_413_REQUEST_ENTITY_TOO_LARGE` still *works* but emits a
        `StarletteDeprecationWarning` on every access, which buries real
        warnings in the suite output. The mapping must survive the rename —
        only the constant moves, not the wire value.
        """
        from starlette import status

        assert status.HTTP_413_CONTENT_TOO_LARGE == 413
        r = self._probe(413, "too large")
        assert r.json()["code"] == "PAYLOAD_TOO_LARGE"


# --------------------------------------------------------------------------- #
# SEC-12 — every non-public /api/v1 route must enforce live account state
# --------------------------------------------------------------------------- #
_PUBLIC_PATHS = {
    "/api/v1/fare/estimate",
    "/api/v1/auth/otp/request",
    "/api/v1/auth/otp/verify",
    "/api/v1/auth/refresh",
    # The two account doors. Pre-authentication **by definition** — there is no
    # session yet, so there is no live row for a guard to read, which is why they
    # cannot carry one. What substitutes is the chain both handlers run before
    # touching an account: human verification (Turnstile), a per-IP budget, a
    # per-email budget, and a failed-attempt lockout on the row. A lockout answers
    # **401 rather than 429**, so it cannot be used to confirm that an account
    # exists. `register` is additionally the only writer of new `users` rows, and
    # it discloses a taken email (unavoidable — the address is the credential)
    # while deliberately *not* checking whether a phone is taken, which would turn
    # registration into a "is this number registered?" oracle.
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    # Admin sign-in. Pre-authentication by definition — there is no account
    # state to read yet, and the second factor is what substitutes for the live
    # guard. Each of these is protected by the challenge token instead:
    # `/login` rate-limits per account and per IP before touching the database;
    # the other four all resolve a short-lived, single-purpose challenge that
    # `_resolve_challenge` re-checks `is_active` and `is_locked` against on every
    # call. An expired or replayed challenge is a 401, so a disabled admin cannot
    # reach the authenticated surface through them.
    "/api/v1/admin/auth/login",
    "/api/v1/admin/auth/totp/verify",
    "/api/v1/admin/auth/recovery",
    "/api/v1/admin/auth/totp/enrol",
    "/api/v1/admin/auth/totp/enrol/confirm",
    # P-2: the email verification link is opened from an inbox, usually in a
    # different browser or on a different device from the app session, so it
    # cannot require a bearer token. The 256-bit single-use token in the link is
    # the credential instead — stored only as a SHA-256 digest, expiring, and
    # stamped on use. It grants exactly one thing: marking one address verified.
    "/api/v1/identity/email/confirm",
    # Password recovery. Both are pre-authentication **by definition** — the
    # account cannot sign in, so there is no session and no live row for a guard
    # to read. What substitutes differs by route:
    #
    # * `/auth/password/forgot` — human verification (Turnstile), a per-IP budget
    #   (`_FORGOT_IP_RATE_LIMIT`), and an answer that is **identical** for a
    #   registered and an unregistered address. It reads the account only to
    #   email it, and the disclosure it avoids is the same one login avoids.
    # * `/auth/password/reset` — the credential is the 256-bit single-use token
    #   from the email, stored only as a SHA-256 digest, expiring, and stamped on
    #   use: the same pattern as `/identity/email/confirm` above. It grants no
    #   session, so it is not a way around a suspension — signing in afterwards
    #   still passes `require_active_user`, which refuses a disabled row. Every
    #   failure mode answers one identical 400 sentence, so it is not an oracle
    #   either.
    "/api/v1/auth/password/forgot",
    "/api/v1/auth/password/reset",
    # Public metadata: premium destination pins. Places, not people, and the
    # row set is already filtered to ACTIVE; the admin write surface is where
    # the live-state guard lives.
    "/api/v1/destinations",
}


def _iter_api_routes(app):
    """Yield every APIRoute, including those behind `include_router`.

    This FastAPI version (0.141) inserts a lazy `_IncludedRouter` wrapper instead
    of copying the router's routes onto the app, so a plain walk of `app.routes`
    finds nothing. The wrapper keeps the source router on `original_router`.
    """
    from fastapi.routing import APIRoute

    seen: set[int] = set()
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        if id(route) in seen:
            continue
        seen.add(id(route))
        if isinstance(route, APIRoute):
            yield route
            continue
        for attr in ("routes", "original_router", "router"):
            child = getattr(route, attr, None)
            if child is None:
                continue
            nested = getattr(child, "routes", None)
            if nested:
                stack.extend(nested)
            elif isinstance(child, APIRoute):
                stack.append(child)


def _dependency_names(route) -> set[str]:
    names: set[str] = set()

    def walk(dep) -> None:
        call = getattr(dep, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", str(call)))
        for sub in getattr(dep, "dependencies", ()) or ():
            walk(sub)

    walk(route.dependant)
    return names


class TestRouteAuthzCoverage:
    def test_all_non_public_api_routes_require_live_account_state(self, client):
        """SEC-12: 6 routes used JWT-only auth, so a disabled account kept working.
        This walks the real route table so a new route cannot reintroduce the gap."""
        from app.main import create_app

        app = create_app()
        offenders = []
        checked = 0
        for route in _iter_api_routes(app):
            if not route.path.startswith("/api/v1"):
                continue
            if route.path in _PUBLIC_PATHS:
                continue
            checked += 1
            deps = _dependency_names(route)
            # `require_live_principal` counts: it is the scope-aware variant of
            # `require_active_user`, used by the one route that serves both an
            # admin and a user token (`/api/v1/auth/me`). It loads a live row
            # from whichever table the token names and refuses a missing or
            # disabled one, so it is a genuine live-state guard rather than a
            # bypass — not an exemption, which is why it is added to the set
            # rather than to `_PUBLIC_PATHS`.
            #
            # `require_live_admin_refresh_session` is the same shape for the two
            # cookie-authenticated admin routes (`/admin/auth/refresh` and
            # `/admin/auth/logout`). They carry no bearer token by design — the
            # HttpOnly refresh cookie is the credential — and the handler reads
            # the live `admin_accounts` row before it will rotate or revoke, so
            # a disabled admin is refused. The lookup cannot be hoisted into the
            # dependency without splitting the rotation's transaction, so the
            # dependency *declares* the check the handler performs; that is what
            # keeps this audit meaningful for cookie-authenticated routes
            # instead of forcing them onto an exemption list.
            live_guards = {
                "require_active_user",
                "require_admin",
                "require_live_principal",
                "require_live_admin_refresh_session",
                # The guard returned by `require_role(...)`. It composes on top
                # of `require_admin`, so a route carrying only this one is still
                # fully guarded; the name is here so the audit recognises it
                # rather than flagging every role-narrowed route as bare.
                "require_role_guard",
            }
            if not (live_guards & deps):
                offenders.append(f"{sorted(route.methods or ())} {route.path}")
        assert checked >= 15, f"route discovery looks wrong (only found {checked})"
        assert offenders == [], f"routes missing a live-state guard: {offenders}"


# --------------------------------------------------------------------------- #
# SEC-13 — ledger reference collisions must not silently no-op a charge
# --------------------------------------------------------------------------- #
class TestLedgerReferenceIntegrity:
    async def _driver_with_deposit(self, db_session, balance="500"):
        from app.models import DriverDeposit, DriverProfile, DriverStatus, User, UserRole

        user = User(phone_e164=_phone(), role=UserRole.PASSENGER)
        db_session.add(user)
        await db_session.flush()
        profile = DriverProfile(
            user_id=user.id,
            hk_id_last4="0000",
            taxi_driver_plate_no="TD90001",
            vehicle_reg_mark="ZZ9001",
            taxi_type="URBAN",
            status=DriverStatus.ACTIVE,
        )
        db_session.add(profile)
        await db_session.flush()
        db_session.add(
            DriverDeposit(
                driver_profile_id=profile.id,
                balance_hkd=Decimal(balance),
                held_hkd=Decimal("0"),
                required_hkd=Decimal("500"),
            )
        )
        await db_session.flush()
        return profile

    async def test_planted_reference_cannot_swallow_the_weekly_fee(self, db_session):
        """The exact SEC-13 attack: pre-plant `weekly:{driver}:{period}` carrying a
        different entry_type/amount, then let settlement run. Previously append()
        returned that row verbatim, the run reported `skipped`, and the HK$200 fee
        was silently never collected."""
        from sqlalchemy import select

        from app.models import DriverDeposit, LedgerEntryType
        from app.services.ledger.ledger_service import LedgerService, reference_for_weekly

        profile = await self._driver_with_deposit(db_session)
        svc = LedgerService(db_session)
        reference = reference_for_weekly(profile.id, "2099-W03")

        # The plant: a HK$1 top-up wearing the settlement's reference.
        await svc.append(
            driver_profile_id=profile.id,
            entry_type=LedgerEntryType.DEPOSIT_TOPUP,
            amount_hkd=Decimal("1"),
            reference=reference,
        )
        await db_session.flush()

        # The real charge now refuses instead of silently no-opping.
        with pytest.raises(BusinessRuleError, match="already used"):
            await svc.append(
                driver_profile_id=profile.id,
                entry_type=LedgerEntryType.WEEKLY_FEE_DEDUCTION,
                amount_hkd=Decimal("-200"),
                reference=reference,
            )

        deposit = (
            (
                await db_session.execute(
                    select(DriverDeposit).where(DriverDeposit.driver_profile_id == profile.id)
                )
            )
            .scalars()
            .first()
        )
        # Only the planted +1 applied; the fee was rejected, not silently dropped.
        assert Decimal(deposit.balance_hkd) == Decimal("501")

    async def test_genuine_replay_still_returns_the_same_entry(self, db_session):
        """Idempotency must survive the SEC-13 fix: same reference, same
        entry_type, same amount -> replay the original, do not double-charge."""
        from app.models import LedgerEntryType
        from app.services.ledger.ledger_service import LedgerService

        profile = await self._driver_with_deposit(db_session)
        svc = LedgerService(db_session)
        reference = f"grant:{profile.id}:retry-1"

        first = await svc.append(
            driver_profile_id=profile.id,
            entry_type=LedgerEntryType.DEPOSIT_TOPUP,
            amount_hkd=Decimal("500"),
            reference=reference,
        )
        again = await svc.append(
            driver_profile_id=profile.id,
            entry_type=LedgerEntryType.DEPOSIT_TOPUP,
            amount_hkd=Decimal("500"),
            reference=reference,
        )
        assert first.id == again.id

    async def test_grant_reference_is_namespaced_server_side(self):
        """SEC-13: a client-supplied key can never be shaped like another
        service's reference."""
        from app.services.ledger.ledger_service import reference_for_grant

        ref = reference_for_grant("driver-1", "weekly:driver-1:2099-W03")
        assert ref.startswith("grant:driver-1:")
        assert not ref.startswith("weekly:")


# --------------------------------------------------------------------------- #
# SEC-17/18 — token lifecycle
# --------------------------------------------------------------------------- #
class TestTokenLifecycle:
    def test_replayed_refresh_token_revokes_the_whole_family(self, client):
        """SEC-17: a rotated token coming back means the family is compromised."""
        tokens = _login(client, _phone())
        r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == 200, r.text
        second = r.json()["refresh_token"]

        replay = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert replay.status_code == 401
        assert "reuse" in replay.json()["message"]

        # The successor token is dead too — the family was revoked.
        after = client.post("/api/v1/auth/refresh", json={"refresh_token": second})
        assert after.status_code == 401

    def test_logout_invalidates_the_access_token(self, client):
        """SEC-18: logout used to leave the access token usable until expiry."""
        tokens = _login(client, _phone())
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200

        after = client.get("/api/v1/auth/me", headers=headers)
        assert after.status_code == 401
        assert "revoked" in after.json()["message"]

    def test_access_token_lifetime_is_short(self):
        from app.core.config import get_settings

        assert get_settings().access_token_expire_minutes <= 30


# --------------------------------------------------------------------------- #
# SEC-23/24 — response hygiene
# --------------------------------------------------------------------------- #
class TestResponseHygiene:
    def test_health_does_not_leak_the_environment(self, client):
        """SEC-23: `env: dev` told an attacker which fixed OTP to try."""
        body = client.get("/health").json()
        assert "env" not in body
        assert body["status"] in ("ok", "degraded")

    def test_security_headers_present(self, client):
        r = client.get("/api/v1/auth/me")
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"
        assert r.headers.get("referrer-policy") == "no-referrer"
        assert "content-security-policy" in r.headers

    def test_hsts_only_in_prod(self, client):
        """HSTS on localhost would pin the developer's browser to HTTPS."""
        assert "strict-transport-security" not in client.get("/health").headers


# --------------------------------------------------------------------------- #
# SEC-26 — keyset cursor must not be a cross-tenant oracle
# --------------------------------------------------------------------------- #
class TestCursorScoping:
    def test_foreign_before_id_is_not_an_existence_oracle(self, client):
        # `client.activate` rather than `_login`: this test is about the cursor
        # being scoped, and the P-2/P-4 gates would refuse the order first and
        # hide the behaviour under test. `_login` stays as-is for the tests that
        # genuinely exercise the auth routes.
        owner = {"access_token": client.activate(_phone())}
        other = {"access_token": client.activate(_phone())}
        created = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
            json={
                "pickup_lat": 22.284,
                "pickup_lng": 114.158,
                "dropoff_lat": 22.315,
                "dropoff_lng": 114.219,
                "pickup_address": "Statue Square, Central",
                "dropoff_address": "Harbour North, North Point",
                "distance_km": "4.2",
                "taxi_type": "URBAN",
            },
        )
        assert created.status_code == 201, created.text
        order_id = created.json()["id"]

        r = client.get(
            "/api/v1/orders",
            params={"before_id": order_id},
            headers={"Authorization": f"Bearer {other['access_token']}"},
        )
        assert r.status_code == 404

        # The owner can still page with their own cursor.
        ok = client.get(
            "/api/v1/orders",
            params={"before_id": order_id},
            headers={"Authorization": f"Bearer {owner['access_token']}"},
        )
        assert ok.status_code == 200


# --------------------------------------------------------------------------- #
# SEC-31 — the ASGI server must not re-trust X-Forwarded-For below the app
# --------------------------------------------------------------------------- #
class TestProxyHeaderTrust:
    """Found while re-running the post-fix attack probe: SEC-07 was fixed in the
    app, but uvicorn put it back.

    uvicorn's ProxyHeadersMiddleware is ON by default with
    `forwarded_allow_ips=127.0.0.1`, and it overwrites `scope["client"]` with the
    client-supplied X-Forwarded-For. That happens BELOW the application, so
    `_client_ip()` receives an already-spoofed address and its
    `TRUSTED_PROXY_COUNT == 0` guard never gets a chance to reject it. Measured:
    `POST /otp/request` with `X-Forwarded-For: 198.51.100.78` created the
    rate-limit bucket `rl:realtaxi:otp:ip:198.51.100.78` — a fresh bucket per
    request, i.e. no IP limit at all.

    This is a launch-configuration defect, so the regression test asserts on the
    launch configuration. An in-process TestClient test cannot catch it, because
    TestClient does not run uvicorn's middleware.
    """

    def _launch_files(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent.parent
        return [
            root / "Dockerfile",
            root / "docker-compose.yml",
            *sorted((root / "scripts").glob("*.py")),
        ]

    def test_every_uvicorn_launch_point_disables_proxy_headers(self):
        offenders = []
        for path in self._launch_files():
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            if "app.main:app" not in text:
                continue  # not a launch point
            if "--no-proxy-headers" in text or "proxy_headers=False" in text:
                continue
            offenders.append(path.name)
        assert offenders == [], (
            "these files launch the app without disabling uvicorn's proxy headers, so "
            f"X-Forwarded-For can spoof the client IP below the app: {offenders}"
        )

    def test_premise_uvicorn_trusts_the_header_from_loopback(self):
        """Pins the premise, so the reason for the flag stays verifiable rather
        than becoming folklore. If uvicorn changes this, re-check SEC-31."""
        ph = pytest.importorskip("uvicorn.middleware.proxy_headers")
        cls = getattr(ph, "_TrustedHosts", None)
        if cls is None:  # pragma: no cover — uvicorn internals moved
            pytest.skip("uvicorn internals changed — re-verify SEC-31 by hand")
        trusted = cls("127.0.0.1")
        assert "127.0.0.1" in trusted
        # A single attacker-supplied value is returned verbatim.
        assert trusted.get_trusted_client_address("203.0.113.7")[0] == "203.0.113.7"
        # And a value that is not a trusted host is not silently accepted as one.
        assert "203.0.113.7" not in trusted


# --------------------------------------------------------------------------- #
# SEC-02 (third round) — the OTP must never come back over HTTP
# --------------------------------------------------------------------------- #
class TestOtpIsNeverEchoed:
    """The code used to be returned as `dev_code` whenever ALLOW_DEV_OTP was on.

    The response goes to the caller, so that made the OTP prove nothing — the
    whole point of a second factor is that the requester does not learn it. The
    suite reads codes from the notify seam instead (`otp_inbox`), which is also a
    stronger assertion: it proves the code reached the notification layer, which
    a response-body check cannot.
    """

    def test_otp_request_returns_no_code(self, client):
        r = client.post("/api/v1/auth/otp/request", json={"phone_e164": _phone()})
        assert r.status_code == 200
        assert set(r.json()) == {"sent", "expires_in"}, (
            "the OTP response must carry no code — a caller who receives it can "
            "complete the very login it is supposed to be proving"
        )
        assert "123456" not in r.text

    def test_the_code_still_reaches_the_notify_layer(self, client):
        """Not merely "no code in the response" — the code must actually be sent,
        otherwise the fix would be indistinguishable from a broken OTP.

        The verify step needs an account that has already proven the number (an
        OTP login is a *secondary* login now), so `client.sign_in` arranges that
        and runs the whole request -> notify -> verify path, asserting 200 on the
        last step. What is asserted here is the middle step: a real six-digit code
        arrived at the notify layer, which a response-body check could never
        establish.
        """
        phone = _phone()
        client.sign_in(phone)
        code = client.otp_inbox[phone]
        assert len(code) == 6 and code.isdigit()

    def test_the_code_is_random_not_a_constant(self, client):
        """The suite no longer sets ALLOW_DEV_OTP, so codes must be random."""
        codes = set()
        for _ in range(3):
            phone = _phone()
            client.post("/api/v1/auth/otp/request", json={"phone_e164": phone})
            codes.add(client.otp_inbox[phone])
        assert len(codes) == 3, f"OTP codes repeated across phones: {codes}"


# --------------------------------------------------------------------------- #
# P2-7 (third round) — one Redis client per event loop, not per call
# --------------------------------------------------------------------------- #
class TestRedisClientCaching:
    """`get_redis()` used to return a brand-new client on every call — a design
    the module docstring justified by test-loop safety, at the cost of opening a
    Redis socket per request on paths that include the auth hot path.

    It now caches one client per running loop, which keeps the test isolation and
    removes the churn. These pin both halves of that trade.
    """

    def test_same_loop_returns_the_same_client(self):
        import asyncio

        from app.core.db import get_redis

        async def run():
            return get_redis() is get_redis()

        assert asyncio.run(run()) is True

    def test_a_different_loop_gets_a_different_client(self):
        import asyncio

        from app.core.db import get_redis

        async def run():
            return get_redis()

        first = asyncio.run(run())
        second = asyncio.run(run())
        assert first is not second, (
            "a redis-py asyncio client binds pooled connections to the loop that "
            "created it, so it must never be handed to a second loop"
        )

    def test_outside_a_loop_gets_a_fresh_client(self):
        """Import-time wiring has no running loop to key on."""
        from app.core.db import get_redis

        assert get_redis() is not get_redis()


class TestSharedRedisClientOwnership:
    """The app owns the per-loop client and closes it at shutdown.

    Two call sites (`_revoke_access_tokens` on logout, the WS handshake) used to
    do `redis = redis_factory()` … `finally: await redis.aclose()`. That was
    correct while `get_redis()` returned a fresh client per call, and became a
    bug the moment it started caching: every logout and every WS connect tore
    down the client every other request was sharing.

    It cannot be caught behaviourally — redis-py simply reconnects on the next
    command, so the symptom is churn, not an error. So this asserts on the call
    sites, the same way the SEC-31 test asserts on the launch configuration.
    """

    def test_no_call_site_closes_a_client_it_borrowed(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent.parent
        offenders = []
        for path in sorted((root / "app").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "redis_factory()" not in text:
                continue
            for match in re.finditer(r"^\s*(\w+)\s*=\s*[\w.]*redis_factory\(\)", text, re.M):
                name = match.group(1)
                if re.search(rf"\b{re.escape(name)}\.aclose\(\)", text):
                    offenders.append(f"{path.relative_to(root)}: {name}.aclose()")
        assert offenders == [], (
            "these close a client obtained from redis_factory(), which is the app's "
            f"shared per-loop client — closing it disconnects every other user: {offenders}"
        )
