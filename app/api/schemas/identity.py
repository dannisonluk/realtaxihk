"""Cabinet auth and identity schemas — tokens, the user block, the profile view.

`UserOut` is intentionally minimal (`id`, `phone_masked`, `role`) and is the
**only** shape a token-issuing route returns for a user. It is not the profile:
a profile carries an email, a name and verification state, and is served from
`/identity/profile` behind an authenticated session. Keeping the token response
small means the login path cannot leak profile fields into a log line.

`phone_masked` rather than `phone_e164` — the masking happens server-side
(`app/core/masking.py`) so a client cannot be handed the raw number by mistake.
"""

from __future__ import annotations

from pydantic import BaseModel

__all__ = [
    "AdminMeOut",
    "AuthMeOut",
    "AvatarPresignOut",
    "EmailConfirmOut",
    "EmailRequestOut",
    "OtpRequestOut",
    "PhoneReverifyOut",
    "ProfileOut",
    "TokenPairOut",
    "UserOut",
    "UsernameCheckOut",
]


class UserOut(BaseModel):
    """The user block embedded in every token response."""

    id: str
    phone_masked: str
    role: str


class TokenPairOut(BaseModel):
    """`POST /auth/otp/verify` and `POST /auth/refresh`.

    `created` is `true` only on the verify that registered a brand-new account,
    so the client can show onboarding exactly once. It is **absent** from the
    refresh response — a refresh never creates anything — which is why
    `auth_refresh.json` has no `created` key while `auth_verify.json` does.
    Hence: declared here as optional rather than required.
    """

    access_token: str
    token_type: str
    refresh_token: str
    created: bool | None = None
    user: UserOut


class OtpRequestOut(BaseModel):
    """`POST /auth/otp/request`.

    `expires_in` is seconds, an integer — not a timestamp, so a client with a
    skewed clock still renders the right countdown.
    """

    sent: bool
    expires_in: int


class ProfileOut(BaseModel):
    """`GET /identity/profile` — the passenger's own profile.

    **This route has no entry in `mobile/test/fixtures/`** — the app does not
    currently call it. It is therefore the one schema in this package not backed
    by a captured response, and it is derived field-for-field from
    `app/api/identity.py::_profile_out` instead. When the app starts using it,
    add it to `scripts/gen_mobile_fixtures.py` so it joins the enforced set.

    `phone_reverify_*` is sent as **both** the raw deadline and the derived
    decision. A reminder and a soft block are different moments about a week
    apart, and the UI shows a banner for one and a modal for the other; a client
    that only received the deadline would have to re-implement the policy and
    would get it wrong the first time the policy changed. The five fields come
    from `PhoneReverifyState.as_dict()`, so they are spread rather than listed
    individually in the handler — which is exactly why they are easy to miss
    when reading the route.

    `avatar_key` is an object key, not a URL — the bucket and CDN host are
    deployment config, so a URL here would bake a hostname into the contract
    (see `app/models/user.py`).
    """

    id: str
    username: str | None
    given_name: str | None
    family_name: str | None
    gender: str | None
    avatar_key: str | None
    email: str | None
    email_verified: bool
    phone_masked: str
    phone_verified: bool
    # --- from PhoneReverifyState.as_dict()
    phone_reverify_due_at: str | None
    phone_reverify_grace_ends_at: str | None
    phone_reverify_due: bool
    phone_reverify_blocked: bool
    phone_reverify_days_remaining: int | None
    # --- account state
    account_status: str
    role: str


class AuthMeOut(UserOut):
    """`GET /auth/me` **when the token is a passenger/driver token**.

    Same three fields as `UserOut`; declared as its own name because the route is
    genuinely polymorphic — an admin token is answered by `AdminMeOut` below —
    and because it is the *identity probe* the console shell calls on boot, a
    different job from carrying a `user` block inside a token response.
    """


class AdminMeOut(BaseModel):
    """`GET /auth/me` **when the token is an admin token**.

    Admin sessions have no `phone_masked`: an `AdminAccount` is identified by a
    username and an email (`app/models/admin.py`), a different identity space
    from a passenger's phone. The route branches on `principal.is_admin` and
    returns one shape or the other, which is why `GET /auth/me` cannot declare a
    single model — see the `Union` on the route decorator.

    `email_masked` rather than the address, for the same PDPO reason
    `phone_masked` exists: masking is done server-side so a client cannot be
    handed the raw value by accident.
    """

    id: str
    username: str
    email_masked: str
    role: str
    # The RBAC rank, kept separate from `role` above. `role` answers "what kind
    # of principal is this" (always `ADMIN` here) and is what the middleware
    # already understood; `admin_role` answers "how senior". Two different
    # questions — the console's navigation depends on the second, and every
    # authorisation decision is made from the live row, not from either field.
    admin_role: str


class UsernameCheckOut(BaseModel):
    """`GET /identity/username-check` — availability for live form feedback.

    `username` is echoed **normalised** (stripped and lower-cased) rather than as
    typed, so a client comparing the response to its input box cannot conclude
    the wrong thing about a name the server would accept.
    """

    username: str
    available: bool


class EmailRequestOut(BaseModel):
    """`POST /identity/email/request` — the send acknowledgement.

    **This is the one shape in this module derived from the service, not from a
    fixture** — there is no `identity_email_request.json`. It mirrors
    `IdentityService.request_email_verification` exactly:
    `{"sent", "expires_in_hours", "email"}`.

    Two things that are easy to get wrong here and are load-bearing:

    - the TTL is `expires_in_hours`, **not** `expires_in` — unlike `OtpRequestOut`,
      whose `expires_in` is *seconds*. The two units differ on purpose (an email
      link lives hours, an OTP minutes) and a client that assumed one for the
      other would render a countdown off by 3600×.
    - `email` is echoed **masked** (`_mask_email`), so the UI can say "we sent it
      to o***r@example.com" without the server having to trust the client to
      mask it. It is not the raw address the caller submitted.
    """

    sent: bool
    expires_in_hours: int
    email: str


class EmailConfirmOut(BaseModel):
    """`POST /identity/email/confirm` — what consuming the link changed.

    `account_status` is returned because verifying the email is often the step
    that promotes a pending account to `ACTIVE`; a client that only received
    `verified: true` would not know to re-render the gate it is sitting behind.
    """

    verified: bool
    email: str | None
    account_status: str


class PhoneReverifyOut(ProfileOut):
    """`POST /identity/phone/reverify` — the refreshed profile plus a flag.

    `created` reports whether the OTP that cleared the block **registered a new
    account** (it goes through the same `OtpService.verify_otp_for_user` as
    login). It cannot be true on this route — the caller is already
    authenticated — but the field is spread from that shared return value, so it
    is declared here rather than dropped. The rest of the body is the profile,
    which is why this inherits.
    """

    verified: bool
    created: bool


class AvatarPresignOut(BaseModel):
    """`POST /identity/avatar/uploads` — where to PUT an avatar.

    There was a hole here: `users.avatar_key` existed and `complete_profile`
    accepted one, but nothing ever *minted* a key — so the client had to
    construct the path itself, and the server-side rule that keys are
    server-generated was being enforced only by the storage layer rejecting
    whatever it was sent.

    `object_key` is what the client must send to `POST /identity/profile` to
    claim the upload, and it is generated here so the client never chooses it.
    `headers` are the ones that were signed: the bucket rejects a PUT whose
    `Content-Type` differs from the signed one, which is what makes the image
    allowlist on the storage service actually enforceable.
    """

    upload_url: str
    object_key: str
    expires_in: int
    headers: dict[str, str]
