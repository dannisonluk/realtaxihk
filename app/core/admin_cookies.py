"""Admin session cookies: set, read, clear — in one place.

Why this module exists rather than four lines in each endpoint
-------------------------------------------------------------
Three endpoints issue the cookie pair (`/totp/verify`, `/totp/enrol/confirm`,
`/recovery`), one rotates it (`/refresh`) and one clears it (`/logout`). That is
five call sites, and every one of them has to agree on the same five attributes
or the security properties silently differ between them. A helper that is the
only thing that writes a cookie means they cannot drift.

The attributes, and what each one buys
--------------------------------------
`HttpOnly`  — script cannot read it. This is the whole reason the refresh token
              is in a cookie instead of `sessionStorage`; without it the design
              is strictly worse than a bearer token, because a cookie is also
              sent automatically.
`Secure`    — HTTPS only, so the cookie cannot be lifted off a plaintext
              request. Tied to the environment: a `Secure` cookie on an http
              origin is discarded by the browser, which would look like a broken
              refresh path rather than a config problem.
`SameSite`  — `Strict` in production, which stops the cookie being attached to a
              cross-site request at all. `Lax` in dev, where the console and API
              are on different ports.
`Path`      — `/api/v1/admin/auth`, so the credential is not attached to every
              unrelated request the console makes. Narrower is better: it
              reduces where the value can leak to. The `__Host-` prefix would
              require `/`, so the two are alternatives; the path is chosen here
              because it is enforceable in dev too, whereas `__Host-` is not.
`Max-Age`   — the server's `refresh_token_expire_days`, so the cookie's life and
              the stored row's life are the same number read from the same
              setting. A cookie that outlives its row just produces 401s.

The CSRF cookie is deliberately **not** `HttpOnly`. That looks like a mistake
and is not: the double-submit defence requires a script on our origin to read
the value and echo it in a header, and a script cannot read an `HttpOnly`
cookie. Its confidentiality is not what protects it — an attacker who can read
it is already same-origin and therefore already has everything. What the flag
protects is the *refresh* token, and that one has it.
"""

from __future__ import annotations

from fastapi import Request, Response

from app.core.config import get_settings
from app.services.admin_refresh_service import (
    cookies_are_secure,
    csrf_cookie_name,
    refresh_cookie_name,
    same_site_policy,
)

# Shared by both cookies, and the reason the refresh cookie is not sent on every
# console request: only the auth endpoints need it.
_COOKIE_PATH = "/api/v1/admin/auth"


def _max_age_seconds() -> int:
    return get_settings().refresh_token_expire_days * 24 * 3600


def set_session_cookies(response: Response, *, refresh_token: str, csrf_token: str) -> None:
    """Attach the refresh cookie (HttpOnly) and the CSRF cookie (readable).

    Both are set on every successful issuance and rotation, so a rotation
    replaces the pair atomically from the client's point of view.
    """
    secure = cookies_are_secure()
    max_age = _max_age_seconds()
    response.set_cookie(
        key=refresh_cookie_name(secure=secure),
        value=refresh_token,
        max_age=max_age,
        path=_COOKIE_PATH,
        httponly=True,
        secure=secure,
        samesite=same_site_policy(),
    )
    response.set_cookie(
        key=csrf_cookie_name(secure=secure),
        # Not HttpOnly — see the module docstring. The double-submit pattern
        # requires the page's own script to read this and echo it.
        value=csrf_token,
        max_age=max_age,
        path=_COOKIE_PATH,
        httponly=False,
        secure=secure,
        samesite=same_site_policy(),
    )


def clear_session_cookie_headers() -> dict[str, list[str]]:
    """The same expiry as `clear_session_cookies`, as headers to raise with.

    Needed because a `raise HTTPException(...)` never reaches the injected
    `Response`: `app/core/exceptions.py` builds a *fresh* `JSONResponse` for the
    error and carries over only `exc.headers`. So calling `clear_session_cookies(
    response)` immediately before a `raise` set headers on a response that was
    then thrown away — the cookies survived the refusal, and the console would
    retry a dead cookie on every boot.

    Passing these through `HTTPException(headers=...)` is the one route that
    reaches the client, because that is the attribute the handler preserves.

    The value is a **list of one `Set-Cookie` string per cookie**, not a single
    comma-joined string. Folding is not safe here: each value carries an
    `expires` attribute, and `Thu, 01 Jan 1970 ...` contains a comma that a
    client parsing the fold would split on, mangling both cookies. The handler
    in `app/core/exceptions.py` recognises the list form and emits one header
    per entry, which is what RFC 6265 requires for multiple cookies.

    The deletions are built by applying `clear_session_cookies` to a throwaway
    `Response` and reading back its headers, rather than by formatting the
    attributes a second time. That keeps one definition of "how a cookie is
    expired": a second copy here could drift, and a mismatch in `Path` or
    `Secure` makes the browser treat it as a *different* cookie and keep the
    original — a refusal that looks right and changes nothing.
    """
    scratch = Response()
    clear_session_cookies(scratch)
    return {
        "set-cookie": [
            value.decode("latin-1")
            for key, value in scratch.raw_headers
            if key.lower() == b"set-cookie"
        ]
    }


def clear_session_cookies(response: Response) -> None:
    """Expire both cookies.

    The `secure`/`samesite`/`path` values must match what was set, or the
    browser treats this as a *different* cookie and leaves the original in
    place — a logout that looks successful and changes nothing. Deriving them
    from the same helpers as `set_session_cookies` is what makes that impossible
    to get wrong.
    """
    secure = cookies_are_secure()
    response.delete_cookie(
        key=refresh_cookie_name(secure=secure),
        path=_COOKIE_PATH,
        httponly=True,
        secure=secure,
        samesite=same_site_policy(),
    )
    response.delete_cookie(
        key=csrf_cookie_name(secure=secure),
        path=_COOKIE_PATH,
        httponly=False,
        secure=secure,
        samesite=same_site_policy(),
    )


def read_refresh_cookie(request: Request) -> str | None:
    """The refresh token from the cookie, or None.

    Both the prefixed and unprefixed names are checked. The prefix is chosen by
    environment, but a cookie set before a config change (or by a deployment
    mid-migration) would carry the other name, and a refresh that fails only for
    operators who signed in before a deploy is a bad failure. Reading both costs
    one dict lookup.
    """
    for secure in (True, False):
        value = request.cookies.get(refresh_cookie_name(secure=secure))
        if value:
            return value
    return None


def read_csrf_header(request: Request) -> str | None:
    """The CSRF token the page echoed back in the header."""
    return request.headers.get("x-csrf-token")


def read_csrf_cookie(request: Request) -> str | None:
    for secure in (True, False):
        value = request.cookies.get(csrf_cookie_name(secure=secure))
        if value:
            return value
    return None
