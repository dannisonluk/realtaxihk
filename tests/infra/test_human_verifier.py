"""The human-verification provider: Cloudflare Turnstile, and its dev double.

Why this file exists
--------------------
`app/services/infra/human.py` fails **closed** on every error path, and that is
the property worth testing: a CAPTCHA that allows traffic when its verification
call breaks is not a CAPTCHA, it is a bypass with a nice error message. Each of
the refusal branches below is a separate way the outbound call can go wrong, and
each one has to answer "refuse".

The HTTP call is stubbed rather than mocked at the `verify` level. `verify` is
the unit under test, so replacing it would test nothing — what is replaced is the
*transport*, one layer down, which keeps every branch in `verify` on the path.

`DisabledHumanVerifier` is reachable only from a state `Settings` refuses to
construct (see `_StubSettings`), which is exactly why it needs a test: it is the
code that runs in a deployment that bypassed the fail-closed check.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.infra import human


class _FakeResponse:
    """The parts of an `httpx.Response` that `TurnstileVerifier` touches."""

    def __init__(self, *, status_code: int = 200, payload: object = None, bad_json: bool = False):
        self.status_code = status_code
        self._payload = payload
        self._bad_json = bad_json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)  # type: ignore[arg-type]

    def json(self) -> object:
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    """Stands in for `httpx.AsyncClient` — context manager plus one `post`."""

    def __init__(self, outcome: object, calls: list[dict]) -> None:
        self._outcome = outcome
        self._calls = calls

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, url: str, data: dict | None = None) -> _FakeResponse:
        self._calls.append({"url": url, "data": data or {}})
        if isinstance(self._outcome, Exception):
            raise self._outcome
        assert isinstance(self._outcome, _FakeResponse)
        return self._outcome


@pytest.fixture()
def transport(monkeypatch):
    """Install a fake transport and a fake settings object.

    Settings are stubbed as well as the transport because `TurnstileVerifier`
    reads its secret, URL, timeout and expected hostname in `__init__` — with the
    real (dev) settings every instance would carry an empty secret, and the
    assertion about what goes on the wire would be about nothing.

    `human.httpx` *is* the httpx module, so patching `AsyncClient` on it is
    process-wide for the duration of the test — `monkeypatch` restores it, and
    nothing else in the suite makes a real HTTP call inside this window.
    """

    def _install(outcome: object, *, expected_hostname: str = ""):
        calls: list[dict] = []
        monkeypatch.setattr(
            human,
            "get_settings",
            lambda: _StubSettings(
                app_env="prod",
                turnstile_secret_key="a-real-secret",
                expected_hostname=expected_hostname,
            ),
        )
        monkeypatch.setattr(
            human.httpx, "AsyncClient", lambda **kwargs: _FakeClient(outcome, calls)
        )
        return human.TurnstileVerifier(), calls

    return _install


class _StubSettings:
    """A settings object that can express a state `Settings` will not build.

    `Settings` raises for `app_env="prod"` with an empty Turnstile secret — that
    is `_fail_closed` doing its job — so `DisabledHumanVerifier` is unreachable
    through a real `Settings`. It still has to behave correctly, because it is
    what runs in a deployment that bypassed that check, and "unreachable" is not
    "correct".
    """

    def __init__(
        self,
        *,
        app_env: str,
        turnstile_secret_key: str = "",
        expected_hostname: str = "",
    ) -> None:
        self.app_env = app_env
        self.turnstile_secret_key = turnstile_secret_key
        self.turnstile_verify_url = "https://turnstile.invalid/siteverify"
        self.turnstile_expected_hostname = expected_hostname
        self.turnstile_timeout_s = 1.0


# --------------------------------------------------------------------------- #
# The factory
# --------------------------------------------------------------------------- #


def test_dev_and_test_get_the_accept_everything_double(monkeypatch):
    """A CAPTCHA in a test suite is a wall with no door.

    Both envs are covered because they are the two an automated harness runs in,
    and a double that only appeared in one of them would be a puzzle for whoever
    ran the other.
    """
    for env in ("dev", "test"):
        monkeypatch.setattr(human, "get_settings", lambda env=env: _StubSettings(app_env=env))
        assert isinstance(human.get_human_verifier(), human.DevHumanVerifier)


def test_a_configured_secret_outside_dev_gets_the_real_verifier(monkeypatch):
    monkeypatch.setattr(
        human,
        "get_settings",
        lambda: _StubSettings(app_env="prod", turnstile_secret_key="a-real-secret"),
    )
    assert isinstance(human.get_human_verifier(), human.TurnstileVerifier)


def test_an_unconfigured_secret_outside_dev_allows_but_warns(monkeypatch, caplog):
    """Fails **open**, deliberately — and the test pins that.

    Refusing every login because a key is missing turns a configuration gap into
    a total outage, and an outage nobody can sign in to diagnose is worse than
    the missing protection. The real gate is `_fail_closed`, which stops the
    process from starting at all; this branch is the backstop behind it, and it
    must be loud.
    """
    monkeypatch.setattr(
        human, "get_settings", lambda: _StubSettings(app_env="prod", turnstile_secret_key="")
    )
    verifier = human.get_human_verifier()
    assert isinstance(verifier, human.DisabledHumanVerifier)

    # The warning is per-process, so a second call in the same process is quiet —
    # a per-request warning on a hot path is noise, and noise gets ignored.
    assert _accepts(verifier) is True
    assert any("NOT configured" in r.message for r in caplog.records)


def _accepts(verifier: human.HumanVerifier) -> bool:
    import asyncio

    return asyncio.run(verifier.verify(None))


def test_the_dev_double_accepts_an_absent_token():
    """Otherwise every test that does not build a token would need one."""
    assert _accepts(human.DevHumanVerifier()) is True


# --------------------------------------------------------------------------- #
# TurnstileVerifier — the refusal branches
# --------------------------------------------------------------------------- #


def test_a_valid_token_is_accepted(transport):
    verifier, calls = transport(
        _FakeResponse(payload={"success": True, "hostname": "hkfastdc.com"})
    )
    assert _accepts_token(verifier, "a-token") is True
    assert calls[0]["data"]["response"] == "a-token"
    assert calls[0]["data"]["secret"]


def test_an_absent_token_is_refused_without_calling_out(transport):
    """Refused *locally*, and the difference is not cosmetic.

    A missing token is a client that was never updated; a rejected one is an
    attack. Both refuse, but only one of them should cost an outbound request —
    and a steady stream of the first would otherwise make us pay for every one.
    """
    verifier, calls = transport(_FakeResponse(payload={"success": True}))
    assert _accepts_token(verifier, None) is False
    assert calls == [], "an absent token must not reach Cloudflare"


def test_success_false_is_refused(transport):
    verifier, _ = transport(_FakeResponse(payload={"success": False, "error-codes": ["bad"]}))
    assert _accepts_token(verifier, "a-token") is False


def test_an_http_error_is_refused(transport):
    """The whole safety argument: a broken call must not become a free pass."""
    verifier, _ = transport(_FakeResponse(status_code=500, payload={"success": True}))
    assert _accepts_token(verifier, "a-token") is False


def test_a_transport_error_is_refused(transport):
    verifier, _ = transport(httpx.ConnectError("dns said no"))
    assert _accepts_token(verifier, "a-token") is False


def test_a_timeout_is_refused(transport):
    verifier, _ = transport(httpx.ReadTimeout("too slow"))
    assert _accepts_token(verifier, "a-token") is False


def test_a_non_json_body_is_refused(transport):
    verifier, _ = transport(_FakeResponse(bad_json=True))
    assert _accepts_token(verifier, "a-token") is False


def test_a_non_object_body_is_refused(transport):
    """A JSON *array* is not a dict, and `.get` on it would be an `AttributeError`
    inside the guard — a 500 on a login rather than a refusal."""
    verifier, _ = transport(_FakeResponse(payload=["success"]))
    assert _accepts_token(verifier, "a-token") is False


def test_the_remote_ip_is_forwarded_when_known(transport):
    verifier, calls = transport(_FakeResponse(payload={"success": True}))
    _accepts_token(verifier, "a-token", remote_ip="203.0.113.9")
    assert calls[0]["data"]["remoteip"] == "203.0.113.9"


def test_the_remote_ip_is_omitted_when_unknown(transport):
    """Sent only when we have it. An empty `remoteip` is a value Cloudflare would
    have to interpret, and the field is optional for exactly this reason."""
    verifier, calls = transport(_FakeResponse(payload={"success": True}))
    _accepts_token(verifier, "a-token")
    assert "remoteip" not in calls[0]["data"]


# --------------------------------------------------------------------------- #
# TurnstileVerifier — the hostname cross-check
# --------------------------------------------------------------------------- #


def test_a_hostname_mismatch_is_refused(transport):
    """A token is only meaningful for the site it was solved on.

    Without this check a widget on any other domain could mint tokens that our
    backend would accept, so the challenge would prove nothing about *our*
    visitor.
    """
    verifier, _ = transport(
        _FakeResponse(payload={"success": True, "hostname": "evil.example"}),
        expected_hostname="hkfastdc.com",
    )
    assert _accepts_token(verifier, "a-token") is False


def test_a_hostname_match_is_accepted(transport):
    verifier, _ = transport(
        _FakeResponse(payload={"success": True, "hostname": "hkfastdc.com"}),
        expected_hostname="hkfastdc.com",
    )
    assert _accepts_token(verifier, "a-token") is True


def test_a_missing_hostname_is_refused_when_one_is_expected(transport):
    """A body with `success: true` and no hostname must not pass a pinned check.

    `str(body.get("hostname") or "")` turns absent into empty, which compares
    unequal to any real hostname — the fail-closed direction. Reading it as
    "nothing to check" would make the pin opt-out-able by the response.
    """
    verifier, _ = transport(
        _FakeResponse(payload={"success": True}), expected_hostname="hkfastdc.com"
    )
    assert _accepts_token(verifier, "a-token") is False


def test_an_unset_expected_hostname_skips_the_check(transport):
    """Opt-in. Pinning a hostname the deployment has not told us about would
    refuse every legitimate token, which is a worse failure than not pinning."""
    verifier, _ = transport(_FakeResponse(payload={"success": True, "hostname": "anything"}))
    assert verifier._expected_hostname == ""
    assert _accepts_token(verifier, "a-token") is True


# --------------------------------------------------------------------------- #
# The published test keys
# --------------------------------------------------------------------------- #


def test_the_test_keys_are_the_published_cloudflare_values():
    """Pinned because they are the one thing that makes a staging deploy work
    end to end without a real widget — and because a "helpful" edit to either one
    would silently point staging at a key that rejects everything.

    They are published in Cloudflare's documentation and accept every token; they
    are useless as production credentials, which is the point of having them.
    """
    assert human.TURNSTILE_TEST_SITE_KEY == "1x00000000000000000000AA"
    assert human.TURNSTILE_TEST_SECRET_KEY == "1x0000000000000000000000000000000AA"


def _accepts_token(
    verifier: human.TurnstileVerifier, token: str | None, *, remote_ip: str | None = None
) -> bool:
    import asyncio

    return asyncio.run(verifier.verify(token, remote_ip))
