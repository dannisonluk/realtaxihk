"""The account doors: `POST /auth/register` and `POST /auth/login`.

What this file is about
-----------------------
The credential model. Signing in *was* proving a phone — `POST /auth/otp/verify`
both registered and logged in, so the phone number was simultaneously the
identifier and the only secret, and losing the number meant losing the account
with no recovery path. Now:

    register / login   email + password. No phone proof needed.
    otp/*              a *secondary* login, and the send that feeds it.

Three properties are load-bearing here and each has its own section:

1. **Registration is not an enumeration oracle for phone numbers.** A taken
   *email* has to be disclosed — the address is the credential — but a taken
   phone must not be, because checking it would turn sign-up into a "is this
   number on hkfastdc?" lookup. Two accounts may claim one number; exactly one
   may verify it.
2. **A refusal says nothing.** One generic message for "no such account", "wrong
   password" and "no password set", and `burn_password_time()` on each, so the
   three are indistinguishable by body *and* by response time.
3. **A lockout is a 401, not a 429.** 429 would separate "this account is locked"
   from "this IP is throttled", which confirms the account exists and has been
   attacked. `AccountLocked` subclasses `AccountThrottled` precisely so this
   mapping has to be deliberate.

Rate limits are asserted at the boundary (limit, not limit+1) because the
constant is the contract — see `app/services/auth/account_service.py`.
"""

from __future__ import annotations

import hashlib

import pytest

from app.services.auth import account_service
from app.services.infra import human

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
OTP_REQUEST = "/api/v1/auth/otp/request"
OTP_VERIFY = "/api/v1/auth/otp/verify"
ME = "/api/v1/auth/me"

PHONE = "+85290006001"
EMAIL = "door@example.hk"
PASSWORD = "Correct-Horse-Battery-9"

# Distinct per attempt, so a rate limit on one address never trips another test
# in the same process. The fixture clears the counters per test, but a limit that
# is *about* repetition is easier to read when the inputs differ anyway.
_WRONG = "definitely-not-the-password"


def _login(client, email: str = EMAIL, password: str = PASSWORD):
    return client.post(LOGIN, json={"email": email, "password": password})


def _register(client, *, email: str = EMAIL, phone: str = PHONE, password: str = PASSWORD):
    return client.post(REGISTER, json={"email": email, "password": password, "phone_e164": phone})


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _RefusingVerifier(human.HumanVerifier):
    """A provider that rejects everything, so `assert_human` has to refuse."""

    async def verify(self, token: str | None, remote_ip: str | None = None) -> bool:
        return False


@pytest.fixture()
def refusing_human(monkeypatch):
    """Swap the verifier for one that always says no.

    Patches `human.get_human_verifier` rather than the module's import in
    `app.core.deps`, because `assert_human` reaches for it through the module on
    every call — the same seam `otp_inbox` uses for the WhatsApp provider. The
    class itself is the factory, so it is installed directly.
    """
    monkeypatch.setattr(human, "get_human_verifier", _RefusingVerifier)


# --------------------------------------------------------------------------- #
# 1. Registration
# --------------------------------------------------------------------------- #


def test_register_returns_a_session_and_reports_creation(client):
    """201 rather than 200, so a client can tell "signed up" from "signed in"
    without parsing the body — and `created: true` says the same thing in-band."""
    response = _register(client)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["created"] is True
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "PASSENGER"
    assert body["user"]["phone_masked"] == "+852****6001"


def test_the_claimed_phone_is_not_proven_by_registering(client):
    """The whole point of the split, asserted at the door.

    Registration records a *claim*. `phone_verified_at` stays NULL, so the new
    account can sign in, read and fill in its profile, and still cannot call a
    taxi until it proves a number — which is a separate act on
    `/identity/phone/*`.
    """
    body = _register(client).json()
    token = body["access_token"]

    me = client.get(ME, headers=_auth(token))
    assert me.status_code == 200, me.text
    assert me.json()["phone_masked"] == "+852****6001"


def test_register_discloses_a_taken_email(client):
    """Unavoidable, and accepted here and nowhere else.

    The address *is* the credential, so refusing the sign-up without saying why
    leaves the user with no path forward. Login keeps its single generic message;
    this is the one place the platform admits an address is in use.
    """
    assert _register(client).status_code == 201
    clash = _register(client, phone="+85290006002")
    assert clash.status_code == 400, clash.text
    assert clash.json()["code"] == "BUSINESS_RULE_VIOLATION"
    assert "already registered" in clash.json()["message"]


def test_register_discloses_a_taken_email_case_insensitively(client):
    """`Dan@example.hk` and `dan@example.hk` are one mailbox everywhere except
    RFC 5321's letter, and a sign-up that accepted both would produce two
    accounts that `login` resolves to the same row."""
    assert _register(client, email="Dan.Nison@example.hk").status_code == 201
    clash = _register(client, email="dan.nison@example.hk", phone="+85290006002")
    assert clash.status_code == 400, clash.text
    assert "already registered" in clash.json()["message"]


def test_register_does_not_disclose_a_taken_phone(client):
    """The denial-of-membership control, and the reason the plain UNIQUE on
    `users.phone_e164` had to go.

    Two accounts may *claim* the same number. Checking it here would answer "is
    this number registered?" for anyone who asks — a PDPO problem with no
    functional gain, since a duplicate claim is harmless: exactly one account may
    ever *verify* it, and that refusal happens at binding time where the answer
    has to be given anyway.
    """
    assert _register(client, email="first@example.hk", phone=PHONE).status_code == 201
    second = _register(client, email="second@example.hk", phone=PHONE)

    assert second.status_code == 201, second.text
    assert second.json()["created"] is True


def test_register_rejects_a_password_below_policy(client):
    """400 with the policy's own sentence, not a 500 and not a bare 422.

    `PasswordPolicyError` is a `ValueError` but not a `BusinessRuleError`, so
    without the explicit conversion in the router it would reach the catch-all
    handler as a 500 — the least useful possible answer to "your password is too
    short".
    """
    response = _register(client, password="short")
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "BUSINESS_RULE_VIOLATION"
    assert "12" in response.json()["message"]


def test_register_rejects_a_malformed_email_with_a_sentence(client):
    """The same rule `identity_service` uses for a change-of-address request —
    one regex, not two that can drift apart."""
    response = _register(client, email="not-an-address")
    assert response.status_code == 400, response.text
    assert "email address" in response.json()["message"]


def test_register_rejects_a_non_hk_phone(client):
    """422, from the pydantic pattern — the shape of the field is wrong, which is
    a different answer from "that number is taken"."""
    response = _register(client, phone="91234567")
    assert response.status_code == 422, response.text


# --------------------------------------------------------------------------- #
# 2. Sign-in
# --------------------------------------------------------------------------- #


def test_login_with_the_right_password_returns_a_session(client):
    _register(client)
    response = _login(client)
    assert response.status_code == 200, response.text
    body = response.json()
    # `created` is a definite False here rather than absent: the field means "did
    # this request register an account", and the answer is no.
    assert body["created"] is False
    assert body["access_token"] and body["refresh_token"]


def test_login_is_case_insensitive_on_the_email(client):
    """A person who registered `Dan.Nison@` and types `dan.nison@` expects to get
    in. The stored value keeps its case; the comparison folds both sides."""
    _register(client, email="Mixed.Case@example.hk")
    response = _login(client, email="mixed.case@example.hk")
    assert response.status_code == 200, response.text


def test_a_wrong_password_and_an_unknown_account_are_indistinguishable(client):
    """The account oracle, closed on both the body and the status.

    If these differed — in wording or in status — the endpoint would answer "does
    this address have an account here", which is the first half of a credential
    stuffing run. The timing half is `burn_password_time()`; see
    `tests/domain/test_passwords.py` for the primitive.
    """
    _register(client)

    wrong = _login(client, password=_WRONG)
    unknown = _login(client, email="nobody@example.hk")

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["message"] == unknown.json()["message"] == "invalid credentials"
    assert wrong.json()["code"] == unknown.json()["code"] == "UNAUTHORIZED"


def test_five_wrong_passwords_lock_the_account(client):
    """The lockout threshold is a constant, so it is asserted as one.

    Each of the first five is an ordinary credential refusal — the fifth *sets*
    the lock as a side effect rather than reporting it.
    """
    _register(client)
    for _ in range(account_service.MAX_FAILED_LOGINS):
        response = _login(client, password=_WRONG)
        assert response.status_code == 401, response.text
        assert response.json()["message"] == "invalid credentials"


def test_a_locked_account_answers_401_not_429(client):
    """The status is the security property, not a detail.

    429 would say "throttled", which is a *different* answer from "rejected" —
    and it is the one that tells an attacker the account exists and has been
    guessed at enough to trip the lock. 401 keeps a lockout indistinguishable
    from a wrong password.

    `AccountLocked` subclasses `AccountThrottled`, so a router that mapped by
    `isinstance(exc, AccountThrottled)` would answer 429 here. This is the test
    that catches that.
    """
    _register(client)
    for _ in range(account_service.MAX_FAILED_LOGINS):
        _login(client, password=_WRONG)

    # The correct password, from inside the lockout window.
    response = _login(client)
    assert response.status_code == 401, response.text
    assert response.json()["code"] == "UNAUTHORIZED"
    assert "locked" in response.json()["message"]


def test_a_successful_login_clears_the_failure_counter(client):
    """Otherwise a legitimate sign-in would leave the account one typo from a
    lock, and the counter would only ever ratchet."""
    _register(client)
    for _ in range(account_service.MAX_FAILED_LOGINS - 1):
        assert _login(client, password=_WRONG).status_code == 401
    assert _login(client).status_code == 200

    # Four more wrong attempts. Had the counter survived the successful login it
    # would now be at eight and the account would be locked, so "invalid
    # credentials" rather than "locked" is the assertion that the reset landed.
    for _ in range(account_service.MAX_FAILED_LOGINS - 1):
        response = _login(client, password=_WRONG)
        assert response.status_code == 401
        assert response.json()["message"] == "invalid credentials"


def test_login_against_a_row_without_a_password_is_a_generic_refusal(client):
    """A grandfathered phone-only row: it exists and has no password.

    It must refuse exactly like an unknown address — same status, same message —
    and it must burn the same time, or the three cases become distinguishable.
    """
    client.exec_sql(
        "INSERT INTO users (id, phone_e164, role, is_active, created_at, account_status) "
        "VALUES (gen_random_uuid(), :p, 'PASSENGER', true, now(), 'UNVERIFIED')",
        {"p": "+85290006099"},
    )
    client.exec_sql(
        "UPDATE users SET email = :e WHERE phone_e164 = :p",
        {"e": "legacy@example.hk", "p": "+85290006099"},
    )
    response = _login(client, email="legacy@example.hk")
    assert response.status_code == 401, response.text
    assert response.json()["message"] == "invalid credentials"


def test_a_disabled_account_is_refused_after_the_password_is_checked(client):
    """Order matters for the message, not for the security.

    The password is verified first, so "account disabled" is only ever told to
    someone who already holds the credential — it is not an oracle for anyone
    else.
    """
    _register(client)
    client.exec_sql("UPDATE users SET is_active = false WHERE email = :e", {"e": EMAIL})
    response = _login(client)
    assert response.status_code == 401, response.text
    assert response.json()["message"] == "account disabled"


# --------------------------------------------------------------------------- #
# 3. Rate limits
# --------------------------------------------------------------------------- #


def test_registration_is_rate_limited_per_address(client):
    """A creation event, so the budget is an order of magnitude tighter than
    login's: a legitimate person signs up once."""
    limit = account_service.REGISTER_IP_LIMIT
    for i in range(limit):
        response = _register(client, email=f"burst{i}@example.hk", phone=f"+8529000{i:04d}")
        assert response.status_code == 201, response.text

    over = _register(client, email="one-too-many@example.hk", phone="+85290009999")
    assert over.status_code == 429, over.text
    assert over.json()["code"] == "RATE_LIMITED"


def test_login_is_rate_limited_per_email(client):
    """A targeted guess at one account. Separate from the per-IP budget because
    either alone leaves the other attack open."""
    _register(client)
    limit = account_service.LOGIN_EMAIL_LIMIT
    for _ in range(limit):
        assert _login(client, password=_WRONG).status_code == 401

    over = _login(client, password=_WRONG)
    assert over.status_code == 429, over.text
    assert over.json()["code"] == "RATE_LIMITED"


def test_the_per_email_budget_does_not_block_a_different_address(client):
    """A shared counter would let one attacker lock every account out of login by
    hammering them all — the per-account budget has to be per account."""
    _register(client)
    for _ in range(account_service.LOGIN_EMAIL_LIMIT):
        _login(client, password=_WRONG)

    _register(client, email="other@example.hk", phone="+85290006003")
    assert _login(client, email="other@example.hk").status_code == 200


# --------------------------------------------------------------------------- #
# 4. Human verification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (REGISTER, {"email": "hv@example.hk", "password": PASSWORD, "phone_e164": PHONE}),
        (LOGIN, {"email": EMAIL, "password": PASSWORD}),
        (OTP_REQUEST, {"phone_e164": PHONE}),
    ],
)
def test_the_three_doors_a_script_tries_first_require_human_verification(
    client, refusing_human, path, body
):
    """Registration creates rows and sends email; login is where a credential
    list is spent, and every attempt costs an argon2 verify at 64 MiB; `otp/request`
    costs a billed WhatsApp message. All three are worth automating against, for
    three different reasons.
    """
    response = client.post(path, json=body)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "FORBIDDEN"
    assert response.json()["details"]["reason"] == "HUMAN_VERIFICATION_REQUIRED"


def test_otp_verify_does_not_require_human_verification(client, refusing_human):
    """Deliberately excluded, and the exclusion is worth a test.

    The code there is already attempt-capped at five and rate-limited per IP, and
    a CAPTCHA between a person and their six digits is the most hostile possible
    place to put one — it is the step where they are already holding the proof.

    Arranged entirely in SQL, because every route that would normally establish
    this state (`otp/request`, `/identity/phone/request`) is itself behind the
    challenge — so a helper that used one of them would be testing the refusal it
    just installed.
    """
    phone = PHONE
    code = "424242"
    client.exec_sql(
        "INSERT INTO users (id, phone_e164, role, is_active, created_at, account_status, "
        "phone_verified_at, phone_reverify_due_at) "
        "VALUES (gen_random_uuid(), :p, 'PASSENGER', true, now(), 'ACTIVE', now(), "
        "now() + interval '30 days')",
        {"p": phone},
    )
    client.exec_sql(
        "INSERT INTO otp_codes (phone_e164, code_hash, expires_at, attempts) "
        "VALUES (:p, :h, now() + interval '5 minutes', 0)",
        {"p": phone, "h": hashlib.sha256(f"{phone}:{code}".encode()).hexdigest()},
    )

    response = client.post(OTP_VERIFY, json={"phone_e164": phone, "code": code})
    assert response.status_code == 200, response.text


def test_a_rejecting_challenge_never_reaches_the_database(client, monkeypatch):
    """The refusal is not cosmetic: with the challenge rejected, the account must
    not exist afterwards.

    Asserted on the *effect* rather than the status code, because a handler that
    created the row and refused afterwards would still return 403. The verifier is
    restored mid-test so the second call can prove the address is genuinely still
    free — which is the assertion.
    """
    original = human.get_human_verifier
    monkeypatch.setattr(human, "get_human_verifier", _RefusingVerifier)
    assert _register(client).status_code == 403

    monkeypatch.setattr(human, "get_human_verifier", original)
    body = _register(client)
    assert body.status_code == 201, body.text
    assert body.json()["created"] is True
