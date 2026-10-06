"""The two HTML pages an email link can land on.

Two flows send the user to a *web* page rather than back into the app:

* `PasswordService.request_reset` mails `{public_base_url}/reset-password?token=…`
* `IdentityService.request_email` mails `{public_base_url}/verify-email?token=…`

Until this module existed, **nothing served either path**. The links were
generated, the emails were sent, and the user who followed one got a 404 — so a
password reset and an email verification could not be completed at all. The
mobile app has no manual token-entry screen either, which made both flows a dead
end rather than a degraded one.

Design notes
------------
**The HTML lives in `app/web/*.html`, not in a Python string.** These pages are
the only surface an unauthenticated visitor ever sees, and the wording has to be
maintainable by whoever owns it. `app/web/` ships with the image because the
Dockerfile copies `app/` wholesale.

**Not a `StaticFiles` mount.** A mount would sit in front of the API's own route
table, and would serve anything that ever lands in the directory. These are two
exact paths carrying a query parameter, which is a router's job.

**Both pages POST.** A `GET` form submit would put the password or the token
into the URL, where it survives in history and any proxy log. The pages
therefore submit with `fetch` and no `action`; the CSP below keeps even a
JS-disabled submit from turning into a GET.

**The initial `GET` still carries the token in its query string**, and that is
unavoidable — the link is what the mail contains, so the credential is in the
request line of the first request before any page can act on it. The page's
`no-store` and the label's path-only access log cover this app; a reverse proxy
or CDN in front is out of reach of this code and must be configured not to
record query strings for these two paths. The point of POSTing from here is that
the token appears **once**, not that it never appears.

**Why `'unsafe-inline'` is acceptable here.** The pages are self-contained with
no build step, and they render **no untrusted markup**: the only server-supplied
value ever written to the DOM (`email`) is set with `textContent`. A nonce
scheme would add per-request templating to protect a page that has no injection
sink.
"""

from __future__ import annotations

import pathlib

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["web"], include_in_schema=False)

_WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"

# Read once at import. These are static, and a per-request read would put the
# filesystem on the path of an anonymous, internet-reachable route. A missing
# file fails the import, which is what we want: a silent 500 per request would
# only be discovered by a user who could not reset their password.
_PAGES = {
    "/reset-password": (_WEB_DIR / "reset_password.html").read_text(encoding="utf-8"),
    "/verify-email": (_WEB_DIR / "verify_email.html").read_text(encoding="utf-8"),
}

# `form-action 'none'` is load-bearing, not decoration: it is what stops a
# submit from being turned into a GET with the password in the query string.
_PAGE_CSP = (
    "default-src 'none'; "
    "script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)

_HEADERS = {
    # The URL carries a single-use credential. It must not sit in a shared
    # cache or a browser's back-forward cache.
    "Cache-Control": "no-store",
    "Content-Security-Policy": _PAGE_CSP,
    # A link that arrived by email should not be indexed if it is ever pasted
    # somewhere public.
    "X-Robots-Tag": "noindex, nofollow",
}


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page() -> HTMLResponse:
    """Where the password-reset email points."""
    return HTMLResponse(_PAGES["/reset-password"], headers=_HEADERS)


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email_page() -> HTMLResponse:
    """Where the email-verification mail points."""
    return HTMLResponse(_PAGES["/verify-email"], headers=_HEADERS)
