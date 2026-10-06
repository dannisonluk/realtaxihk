"""The two pages an email link can land on.

`PasswordService` mails `{public_base_url}/reset-password?token=…` and
`IdentityService` mails `{public_base_url}/verify-email?token=…`. Before
`app/api/web.py` existed, **nothing served either path**: the mail went out, the
user followed the link, and got a 404. Both flows were unfinishable rather than
degraded, and the mobile app has no manual token-entry screen to fall back on.

These tests pin the things that are easy to lose again:

* the served paths are **exactly** the ones the two services build, checked
  against the service sources rather than against a constant — a rename on one
  side and not the other is the regression this module exists for;
* the pages carry their own security headers and are deliberately **not** under
  `/api`, where the API's `default-src 'none'` policy would blank them;
* the token is never echoed into the HTML.
"""

import pathlib

RESET = "/reset-password"
VERIFY = "/verify-email"

#: `app/core/middleware._API_CSP`, as the header arrives over HTTP. Mirrored as
#: a literal on purpose: the assertion below is "the pages must *not* carry the
#: API policy", and importing the source constant would make the check pass
#: vacuously the day someone points both at the same object.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


class TestPagesAreServed:
    def test_reset_page_renders(self, client):
        r = client.get(RESET)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/html")
        assert "重設密碼" in r.text

    def test_verify_page_renders(self, client):
        r = client.get(VERIFY)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/html")
        assert "驗證電郵" in r.text

    def test_both_pages_stay_out_of_the_api_schema(self, client):
        """They are pages, not API surface. Publishing them under `/docs` would
        invite a client to depend on markup."""
        paths = client.get("/openapi.json").json()["paths"]
        assert RESET not in paths
        assert VERIFY not in paths

    def test_both_pages_answer_without_a_token(self, client):
        """A mangled or truncated link must produce an explanation, not a 422
        from a required query parameter."""
        assert client.get(RESET).status_code == 200
        assert client.get(VERIFY).status_code == 200


class TestPageHeaders:
    def test_the_credential_bearing_url_is_never_cached(self, client):
        for path in (RESET, VERIFY):
            assert client.get(path).headers["cache-control"] == "no-store", path

    def test_pages_carry_a_usable_policy(self, client):
        for path in (RESET, VERIFY):
            csp = client.get(path).headers["content-security-policy"]
            assert "default-src 'none'" in csp, path
            # The API's policy declares no `script-src`, so it would blank the
            # page; this one has to permit its own inline script.
            assert "script-src" in csp, path
            # A form with no action submits to the current URL, which would put
            # the new password in the query string. `form-action 'none'` is what
            # makes that impossible even with scripting off.
            assert "form-action 'none'" in csp, path

    def test_pages_are_not_indexable(self, client):
        for path in (RESET, VERIFY):
            assert "noindex" in client.get(path).headers["x-robots-tag"], path

    def test_the_api_csp_is_not_applied_to_the_pages(self, client):
        """Guards the reason `app/api/web.py` sits outside `/api`: if these
        routes were ever moved under the prefix, the API policy would silently
        render two blank pages."""
        for path in (RESET, VERIFY):
            csp = client.get(path).headers["content-security-policy"]
            assert csp != API_CSP, path


class TestTokenHandling:
    def test_the_token_is_not_echoed_into_the_html(self, client):
        """The page reads the token from `location.search` in the browser. If it
        were also interpolated server-side, the credential would be in the body
        — and in anything that caches or logs a body."""
        secret = "SENTINEL-TOKEN-MUST-NOT-BE-REFLECTED"
        for path in (RESET, VERIFY):
            r = client.get(path, params={"token": secret})
            assert r.status_code == 200, path
            assert secret not in r.text, path


def _registered_paths(routes) -> set[str]:
    """Every path in the route table, including nested `include_router` entries.

    FastAPI 0.141 keeps an included router behind a `_IncludedRouter` wrapper
    that has no `path` of its own — the paths live on `original_router.routes`.
    Walking only the top level sees five routes (`/health`, `/docs`, …) and
    misses the whole API, so a naive version of this assertion fails even though
    the pages are served. Recursing is what makes the check mean what it says.
    """
    out: set[str] = set()
    for route in routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            out.add(path)
        nested = getattr(route, "original_router", None)
        if nested is not None and hasattr(nested, "routes"):
            out |= _registered_paths(nested.routes)
    return out


class TestPagesMatchTheLinksTheServicesBuild:
    def test_the_mailed_paths_are_actually_served(self, client):
        served = _registered_paths(client.app.routes)
        for path in (RESET, VERIFY):
            assert path in served, f"{path} is mailed but nothing serves it"

    def test_the_service_sources_still_build_the_paths_we_serve(self):
        """Read the two call sites rather than a shared constant: the link is
        built by string interpolation in each service, so only the sources can
        say what URL a user will actually receive."""
        cases = [
            ("app/services/auth/password_service.py", "/reset-password?token="),
            ("app/services/auth/identity_service.py", "/verify-email?token="),
        ]
        for relative, needle in cases:
            text = pathlib.Path(relative).read_text(encoding="utf-8")
            assert needle in text, f"{relative} no longer builds {needle}"
