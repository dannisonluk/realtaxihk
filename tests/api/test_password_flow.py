"""Changing a password, and recovering an account when it is forgotten.

Two flows with two different authorisation stories, and the tests are shaped
around the difference:

* **Change** is made by someone already authenticated, and requires the current
  password. A stolen session must not be enough to take the account over
  permanently — otherwise "someone had my phone for two minutes" escalates from
  "they read my trips" to "they own my account".
* **Reset** is made by someone who knows neither the password nor a session. The
  emailed link *is* the credential, which is why it is single-use, short-lived,
  stored as a digest, and why every failure mode answers with the same sentence.

The property that both share, and that most of these tests exist to pin, is:
**every session dies.** A password change is either routine hygiene or the
response to "I think someone else is in my account", and in the second case
leaving the other party's tokens alive defeats the point.
"""

import asyncio
import hashlib
import re

# Long enough for the 12-character policy, and not on the "obviously weak" list.
PW = "correct horse battery"
PW2 = "a different battery staple"


async def _fetch(client, sql: str, params: dict | None = None) -> list[dict]:
    """Run a read on its own session. Mirrors the other API test modules."""
    from sqlalchemy import text

    async with client.db_factory() as session:
        result = await session.execute(text(sql), params or {})
        return [dict(row) for row in result.mappings()]


CHANGE = "/api/v1/auth/password/change"
FORGOT = "/api/v1/auth/password/forgot"
RESET = "/api/v1/auth/password/reset"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _account(client, *, email: str, phone: str, password: str = PW) -> dict:
    """Register through the real endpoint; returns the session body."""
    r = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "phone_e164": phone},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _login(client, email: str, password: str):
    return client.post(LOGIN, json={"email": email, "password": password})


class _CaptureEmail:
    """Stands in for the SMTP provider and records what was sent.

    The app's logging layer redacts anything token-shaped, so the link cannot be
    read out of the logs — that is the masking working, not an inconvenience to
    route around. The provider is the seam instead, the same way `otp_inbox` is
    the seam for WhatsApp.
    """

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    async def send_email(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    async def send_verification_email(self, to: str, link: str) -> None:
        self.sent.append((to, "verify", link))

    def token(self) -> str:
        """The raw token out of the most recent message's link."""
        _to, _subject, body = self.sent[-1]
        match = re.search(r"token=([A-Za-z0-9_\-]+)", body)
        assert match, f"no token in the message: {body!r}"
        return match.group(1)


def _capture(monkeypatch) -> _CaptureEmail:
    """Patch the provider **in the module that imported it**.

    `password_service` does `from app.services.infra.notify import
    get_email_provider`, which binds the function object into its own namespace
    — so patching `notify.get_email_provider` would leave this reference
    untouched. Same trap `otp_inbox` documents.
    """
    from app.services.auth import password_service

    capture = _CaptureEmail()
    monkeypatch.setattr(password_service, "get_email_provider", lambda: capture)
    return capture


# --------------------------------------------------------------------------- #
# Change
# --------------------------------------------------------------------------- #


class TestChangePassword:
    def test_a_wrong_current_password_is_refused_and_changes_nothing(self, client):
        session = _account(client, email="c1@example.hk", phone="+85291700001")
        token = session["access_token"]

        r = client.post(
            CHANGE,
            headers=_h(token),
            json={"current_password": "not my password", "new_password": PW2},
        )
        assert r.status_code == 401, r.text
        assert "current password is incorrect" in r.json()["message"].lower()

        # The old password still works, and the new one does not.
        assert _login(client, "c1@example.hk", PW).status_code == 200
        assert _login(client, "c1@example.hk", PW2).status_code == 401

    def test_the_current_password_is_required_even_with_a_valid_token(self, client):
        """A stolen session must not be enough to take the account over.

        Without this, "someone had my phone for two minutes" escalates from
        "they read my trips" to "they own my account permanently".
        """
        session = _account(client, email="c2@example.hk", phone="+85291700002")
        r = client.post(
            CHANGE,
            headers=_h(session["access_token"]),
            json={"new_password": PW2},
        )
        assert r.status_code == 422, r.text

    def test_a_successful_change_replaces_the_password(self, client):
        session = _account(client, email="c3@example.hk", phone="+85291700003")
        r = client.post(
            CHANGE,
            headers=_h(session["access_token"]),
            json={"current_password": PW, "new_password": PW2},
        )
        assert r.status_code == 200, r.text

        assert _login(client, "c3@example.hk", PW).status_code == 401
        assert _login(client, "c3@example.hk", PW2).status_code == 200

    def test_a_successful_change_revokes_every_session_including_the_caller(self, client):
        """The caller is signed out too, and that is the design, not a bug.

        The revocation epoch is per-user and the refresh token is not presented
        on this route, so there is no per-session identity to spare. "Sign out
        everywhere" is the only honest behaviour — and the caller's next request
        answers 401, which is what the client acts on.
        """
        session = _account(client, email="c4@example.hk", phone="+85291700004")
        token, refresh = session["access_token"], session["refresh_token"]

        r = client.post(
            CHANGE,
            headers=_h(token),
            json={"current_password": PW, "new_password": PW2},
        )
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert r.json()["revoked"] == 1, "the refresh row was not revoked"

        # The access token the caller was holding is dead…
        assert client.get("/api/v1/auth/me", headers=_h(token)).status_code == 401
        # …and the refresh token cannot mint a replacement.
        assert client.post(REFRESH, json={"refresh_token": refresh}).status_code != 200

    def test_the_new_password_must_meet_the_policy(self, client):
        """The policy sentence reaches the client, not pydantic's "too short"."""
        session = _account(client, email="c5@example.hk", phone="+85291700005")
        r = client.post(
            CHANGE,
            headers=_h(session["access_token"]),
            json={"current_password": PW, "new_password": "short"},
        )
        assert r.status_code == 400, r.text
        assert "at least 12 characters" in r.json()["message"].lower()
        # And nothing changed.
        assert _login(client, "c5@example.hk", PW).status_code == 200

    def test_the_new_password_must_differ_from_the_current_one(self, client):
        session = _account(client, email="c6@example.hk", phone="+85291700006")
        r = client.post(
            CHANGE,
            headers=_h(session["access_token"]),
            json={"current_password": PW, "new_password": PW},
        )
        assert r.status_code == 400, r.text
        assert r.json()["details"]["reason"] == "PASSWORD_UNCHANGED"

    def test_a_wrong_current_password_counts_towards_the_login_lockout(self, client):
        """Otherwise a stolen session is an unmetered brute-force oracle.

        The login route is the one being watched; a second counter on the change
        route would let someone holding a session guess the current password at
        leisure without ever tripping it.
        """
        from app.services.auth.account_service import MAX_FAILED_LOGINS

        session = _account(client, email="c7@example.hk", phone="+85291700007")
        token = session["access_token"]

        for _ in range(MAX_FAILED_LOGINS):
            r = client.post(
                CHANGE,
                headers=_h(token),
                json={"current_password": "wrong", "new_password": PW2},
            )
            assert r.status_code == 401, r.text

        # The account is now locked: even the correct password is refused, and
        # the refusal is a 401 rather than a 429 — a 429 would confirm the
        # account exists and has been attacked.
        locked = _login(client, "c7@example.hk", PW)
        assert locked.status_code == 401, locked.text
        assert "locked" in locked.json()["message"].lower()

    def test_a_successful_change_clears_a_lockout(self, client):
        """A user who resets out of a lockout must actually be out of it.

        Leaving the lock in place reads as "the reset did not work", which is a
        support ticket rather than a fix.
        """
        from app.services.auth.account_service import MAX_FAILED_LOGINS

        session = _account(client, email="c8@example.hk", phone="+85291700008")
        for _ in range(MAX_FAILED_LOGINS - 1):
            _login(client, "c8@example.hk", "wrong")

        r = client.post(
            CHANGE,
            headers=_h(session["access_token"]),
            json={"current_password": PW, "new_password": PW2},
        )
        assert r.status_code == 200, r.text
        assert _login(client, "c8@example.hk", PW2).status_code == 200

    def test_an_anonymous_caller_cannot_change_anything(self, client):
        assert (
            client.post(CHANGE, json={"current_password": PW, "new_password": PW2}).status_code
            == 401
        )


# --------------------------------------------------------------------------- #
# Forgot
# --------------------------------------------------------------------------- #


class TestForgotPassword:
    def test_the_answer_does_not_reveal_whether_the_address_has_an_account(self, client):
        """This route is anonymous, so a difference here is a public lookup.

        `POST /auth/login` goes out of its way to avoid exactly this disclosure;
        a reset form that answered differently would hand it back.
        """
        _account(client, email="f1@example.hk", phone="+85291700101")

        known = client.post(FORGOT, json={"email": "f1@example.hk"})
        unknown = client.post(FORGOT, json={"email": "nobody@example.hk"})
        assert known.status_code == 200, known.text
        assert unknown.status_code == 200, unknown.text
        assert known.json() == unknown.json()

    def test_the_response_carries_no_token(self, client):
        _account(client, email="f2@example.hk", phone="+85291700102")
        body = client.post(FORGOT, json={"email": "f2@example.hk"}).json()
        assert body["sent"] is True
        assert body["expires_in"] > 0
        assert "token" not in body
        assert "token=" not in str(body)

    def test_the_address_is_matched_case_insensitively(self, client, monkeypatch):
        """A person who registered `F3@…` and types `f3@…` expects the link."""
        _account(client, email="f3@example.hk", phone="+85291700103")
        capture = _capture(monkeypatch)
        assert client.post(FORGOT, json={"email": "F3@EXAMPLE.HK"}).status_code == 200
        assert capture.sent, "the link was not sent for a differently-cased address"

    def test_the_link_is_emailed_to_the_account_address(self, client, monkeypatch):
        _account(client, email="f4@example.hk", phone="+85291700104")
        capture = _capture(monkeypatch)
        client.post(FORGOT, json={"email": "f4@example.hk"})

        assert len(capture.sent) == 1, capture.sent
        to, subject, body = capture.sent[0]
        assert to == "f4@example.hk"
        assert "reset-password?token=" in body
        assert "密碼" in subject or "password" in subject.lower()

    def test_no_link_is_sent_for_an_unknown_address(self, client, monkeypatch):
        capture = _capture(monkeypatch)
        client.post(FORGOT, json={"email": "nobody-here@example.hk"})
        assert capture.sent == []

    def test_the_token_is_stored_as_a_digest_not_in_the_clear(self, client, monkeypatch):
        """A leaked backup must not yield working links.

        SHA-256 rather than argon2 is deliberate: the input is 256 bits of
        CSPRNG, so there is no dictionary to attack, while a memory-hard KDF
        would run on every click of a reset link — a trivial way to make this
        endpoint a resource-exhaustion lever.
        """
        _account(client, email="f5@example.hk", phone="+85291700105")
        capture = _capture(monkeypatch)
        client.post(FORGOT, json={"email": "f5@example.hk"})
        raw = capture.token()

        digest = hashlib.sha256(raw.encode()).hexdigest()
        assert digest != raw, "sanity: the digest must not be the token"

        stored = asyncio.run(
            _fetch(
                client,
                "SELECT token_hash FROM password_reset_tokens WHERE token_hash = :h",
                {"h": digest},
            )
        )
        assert len(stored) == 1, stored
        assert stored[0]["token_hash"] == digest

        raw_rows = asyncio.run(
            _fetch(
                client,
                "SELECT count(*) AS n FROM password_reset_tokens WHERE token_hash = :h",
                {"h": raw},
            )
        )
        assert raw_rows[0]["n"] == 0, "the raw token is in the table"


# --------------------------------------------------------------------------- #
# Reset
# --------------------------------------------------------------------------- #


class TestResetPassword:
    def _issue(self, client, monkeypatch, *, email: str, phone: str) -> str:
        _account(client, email=email, phone=phone)
        capture = _capture(monkeypatch)
        assert client.post(FORGOT, json={"email": email}).status_code == 200
        return capture.token()

    def test_a_real_link_sets_the_password_and_revokes_every_session(self, client, monkeypatch):
        session = _account(client, email="r1@example.hk", phone="+85291700201")
        token, refresh = session["access_token"], session["refresh_token"]

        capture = _capture(monkeypatch)
        client.post(FORGOT, json={"email": "r1@example.hk"})
        raw = capture.token()

        r = client.post(RESET, json={"token": raw, "new_password": PW2})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert r.json()["revoked"] == 1

        assert _login(client, "r1@example.hk", PW2).status_code == 200
        assert _login(client, "r1@example.hk", PW).status_code == 401
        # The party who was already inside is ejected — usually why the
        # password is being reset at all.
        assert client.get("/api/v1/auth/me", headers=_h(token)).status_code == 401
        assert client.post(REFRESH, json={"refresh_token": refresh}).status_code != 200

    def test_the_token_is_single_use(self, client, monkeypatch):
        raw = self._issue(client, monkeypatch, email="r2@example.hk", phone="+85291700202")
        assert client.post(RESET, json={"token": raw, "new_password": PW2}).status_code == 200
        again = client.post(RESET, json={"token": raw, "new_password": "another battery here"})
        assert again.status_code == 400, again.text
        # And the second attempt did not overwrite the first one's password.
        assert _login(client, "r2@example.hk", PW2).status_code == 200

    def test_an_unknown_token_is_refused(self, client):
        r = client.post(RESET, json={"token": "x" * 43, "new_password": PW2})
        assert r.status_code == 400, r.text
        assert "invalid or has expired" in r.json()["message"].lower()

    def test_an_expired_token_is_refused_with_the_same_sentence(self, client, monkeypatch):
        """One message for every failure mode.

        Distinguishing "expired" from "never existed" tells a holder of a stolen
        token whether it was ever real, and tells nobody else anything useful.
        """
        raw = self._issue(client, monkeypatch, email="r3@example.hk", phone="+85291700203")
        client.exec_sql(
            "UPDATE password_reset_tokens SET expires_at = now() - interval '1 minute' "
            "WHERE token_hash = :h",
            {"h": hashlib.sha256(raw.encode()).hexdigest()},
        )
        r = client.post(RESET, json={"token": raw, "new_password": PW2})
        assert r.status_code == 400, r.text
        assert "invalid or has expired" in r.json()["message"].lower()

    def test_a_policy_rejection_leaves_the_link_usable(self, client, monkeypatch):
        """Hash the new password *before* consuming the token.

        Otherwise a user who picks a 6-character password has to request a fresh
        link to try again — a small thing that turns into a support ticket.
        """
        raw = self._issue(client, monkeypatch, email="r4@example.hk", phone="+85291700204")
        bad = client.post(RESET, json={"token": raw, "new_password": "short"})
        assert bad.status_code == 400, bad.text
        assert "at least 12 characters" in bad.json()["message"].lower()

        good = client.post(RESET, json={"token": raw, "new_password": PW2})
        assert good.status_code == 200, good.text
        assert _login(client, "r4@example.hk", PW2).status_code == 200

    def test_a_reset_clears_a_lockout(self, client, monkeypatch):
        from app.services.auth.account_service import MAX_FAILED_LOGINS

        raw = self._issue(client, monkeypatch, email="r5@example.hk", phone="+85291700205")
        for _ in range(MAX_FAILED_LOGINS):
            _login(client, "r5@example.hk", "wrong")
        assert _login(client, "r5@example.hk", PW).status_code == 401  # locked

        assert client.post(RESET, json={"token": raw, "new_password": PW2}).status_code == 200
        assert _login(client, "r5@example.hk", PW2).status_code == 200

    def test_a_second_request_issues_a_different_token(self, client, monkeypatch):
        """Requesting again must not hand back the same secret.

        A deterministic token would mean an old link keeps working for the whole
        TTL, and a leaked one could never be invalidated by asking for a new one.
        """
        _account(client, email="r6@example.hk", phone="+85291700206")
        capture = _capture(monkeypatch)

        client.post(FORGOT, json={"email": "r6@example.hk"})
        first = capture.token()
        client.post(FORGOT, json={"email": "r6@example.hk"})
        second = capture.token()
        assert first != second

        assert client.post(RESET, json={"token": second, "new_password": PW2}).status_code == 200
        assert _login(client, "r6@example.hk", PW2).status_code == 200
