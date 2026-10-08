"""Admin authentication schemas — the three-step login state machine.

The console authenticates with username + password + TOTP, a different flow from
the passenger app's phone OTP (`app/api/auth.py`). Two consequences show
up in the wire shapes below:

1. **Step 1 never returns an access token.** `POST /admin/auth/login` returns a
   *challenge* (`next` + `challenge_token`), and the challenge token carries
   neither `sub` nor `role`, so it cannot be used as a bearer token even if it
   leaks into an `Authorization` header. That is why `AdminLoginOut` and
   `AdminChallengeOut` are separate models from the session response — the type
   system states which of the three steps is allowed to mint a session.
2. **The refresh token is never in the body.** It travels in an `HttpOnly`
   cookie (`app/core/admin_cookies.py`), so `AdminSessionOut` carries only
   `access_token`, `token_type` and the admin block. There is no
   `refresh_token` field here on purpose — adding one would reintroduce the
   `sessionStorage` exposure the cookie exists to remove.

`EnrolmentOut` is returned **once**, on the first login, and never re-readable
from the account: the secret lives in Redis until a code generated from it is
proven, so an admin who closes the tab mid-setup is not locked out.
"""

from __future__ import annotations

from pydantic import BaseModel

__all__ = [
    "AdminLoginOut",
    "AdminSessionOut",
    "ChallengeOut",
    "EnrolmentOut",
    "TotpEnrolOut",
]


class EnrolmentOut(BaseModel):
    """The authenticator-setup material, handed over exactly once.

    `recovery_codes` is a list, not a single code: the console shows them as a
    sheet to be written down, and a caller that received one string would have to
    split it on a delimiter the server never promised.
    """

    secret: str
    otpauth_uri: str
    recovery_codes: list[str]


class AdminLoginOut(BaseModel):
    """Step 1 (`POST /admin/auth/login`).

    `next` is what the console branches on — `totp_required` shows the code
    field, `enrolment_required` shows the QR screen. It is a **string, not an
    enum**, because it arrives over the wire as one and pinning it to an enum
    here would turn an unknown value into a 500 instead of letting the console
    fall back to its "unsupported state" screen.

    `challenge_token` and `enrolment` are both optional and both absent on a
    fully-enrolled account that still owes its second factor — which is why
    neither is required.
    """

    next: str
    challenge_token: str | None = None
    enrolment: EnrolmentOut | None = None


class TotpEnrolOut(BaseModel):
    """`POST /admin/auth/totp/enrol` — re-issued setup material.

    Same fields as `AdminLoginOut`, but here `challenge_token` is always present
    and the route 409s rather than returning without an `enrolment` — its only
    job is to re-issue material. Declared separately so that guarantee is in the
    contract and a future change to `/login` cannot silently weaken it.
    """

    next: str
    challenge_token: str
    enrolment: EnrolmentOut


class ChallengeOut(BaseModel):
    """Step 2a (`POST /admin/auth/totp/verify`) — the **only** step-2 shape
    that is not a session, and only in the sense that it is never returned.

    Declared for symmetry with the enrolment challenge; the route actually
    returns `AdminSessionOut`. Kept so the challenge/session distinction lives
    in one file rather than in a comment.

    It exists to make the structural point: a challenge is not a session.
    """

    next: str
    challenge_token: str


class AdminSessionOut(BaseModel):
    """Steps 2a/2b/2c — `/totp/verify`, `/recovery`, `/totp/enrol/confirm`, and
    `POST /admin/auth/refresh`.

    `admin` is `AdminIdentityOut` (three fields) on the console's `/auth/me` and
    five fields here. The two are deliberately **different shapes**: refresh
    returns the account row the session belongs to (username, email, full_name,
    enrolment state), because the console uses it to render the shell on a
    restored session. See `app/api/schemas/identity.py::AdminMeOut` for the
    probe shape.
    """

    access_token: str
    token_type: str
    admin: AdminSessionAccountOut


class AdminSessionAccountOut(BaseModel):
    """The `admin` block inside `AdminSessionOut` — `_admin_out`.

    Narrower than `AdminAccountOut`: no `is_active`, `last_login_at` or
    `created_at`, because the session response is read by the login screen and
    the shell, neither of which needs account metadata. There is no
    `totp_secret` field and there never can be — the secret is write-only after
    enrolment.

    `admin_role` is the RBAC rank (`SUPPORT` … `SUPER_ADMIN`), which the console
    needs on first render to decide which navigation to build. It is **not**
    what authorises anything: `require_role` re-reads the live row, so a stale
    claim here only means the sidebar briefly shows a link the server will
    refuse. Naming it `admin_role` rather than `role` is deliberate — `role`
    already means the *identity type* (`AdminMeOut.role == "ADMIN"`), and
    collapsing the two into one field is how a console ends up treating "is an
    admin" as "may approve refunds".
    """

    id: str
    username: str
    email: str
    full_name: str | None
    totp_enrolled: bool
    admin_role: str


AdminSessionOut.model_rebuild()
