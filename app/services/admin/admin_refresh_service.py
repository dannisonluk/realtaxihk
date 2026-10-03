"""Admin refresh tokens: rotating, single-use, bound to a CSRF token.

The problem this closes
-----------------------
An admin access token lives 15 minutes (`SEC-18`). That is deliberate — it is
what makes a logout real rather than cosmetic and what bounds a stolen token.
But it only works if there is a way to *outlive* the access token without
lengthening it, and there was not: `/admin/auth/totp/verify` returned a bare
access token and nothing else, so every console session died at the 15-minute
mark, mid-form, with no recovery path. An operator typing an adjustment reason
lost the work.

Why the token travels in a cookie and not in the response body
--------------------------------------------------------------
The console is a browser application. Anything it holds in JavaScript —
`sessionStorage`, `localStorage`, a module variable — is readable by any script
that manages to run on the origin. The refresh token is the long-lived half of
the credential, so it is the half that most needs to be out of reach. An
`HttpOnly` cookie is not readable by script at all, which is the only way to
make that true from the server side.

The cost, stated plainly: a cookie is sent **automatically** by the browser on
every request to the origin, including one triggered by a page the operator did
not intend to visit. That is CSRF, and it is why this module also mints a CSRF
token and why `rotate()` refuses a request that does not present it. Two things
must line up — the cookie the browser attaches for us, and a header value a
script on our origin read from the CSRF cookie and echoed back. A cross-site
attacker can cause the first; they cannot cause the second, because they cannot
read the value.

SameSite is the first line (`Strict` in production), the double-submit token is
the second. Neither alone is sufficient: `SameSite` is a browser-enforced
policy with historical gaps, and a double-submit token with no `SameSite` is
still CSRF-defensible but has a far larger attack surface. Together the two are
what makes the cookie safe to use for a credential this powerful.

Replay detection
----------------
Identical to the user flow, and for the same reason. A rotated token presented
again means the cookie leaked: either the attacker replayed one the operator
already rotated, or the operator replayed one the attacker rotated. Either way
the set is compromised, so `rotate()` reports `reused=True` and the caller
revokes every admin refresh token plus the admin's access-token epoch.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import AdminRefreshToken

logger = logging.getLogger("realtaxihk.admin_auth.refresh")

# The two cookies. Names are namespaced so a future user-facing cookie cannot
# collide, and prefixed `__Host-` where the platform allows it: that prefix is
# browser-enforced to mean "Secure, path=/, no Domain attribute", which removes
# a whole family of cookie-tossing attacks by construction.
#
# `__Host-` requires `Secure`, which requires HTTPS, which is not available on
# `http://127.0.0.1`. Dev therefore uses the unprefixed names, and the prefix is
# applied only when the deployment is actually secure. This is a property of the
# cookie name, chosen at write time, not a runtime check in the request path.
REFRESH_COOKIE = "realtaxi_admin_refresh"
CSRF_COOKIE = "realtaxi_admin_csrf"
SECURE_PREFIX = "__Host-"


def refresh_cookie_name(*, secure: bool) -> str:
    return f"{SECURE_PREFIX}{REFRESH_COOKIE}" if secure else REFRESH_COOKIE


def csrf_cookie_name(*, secure: bool) -> str:
    return f"{SECURE_PREFIX}{CSRF_COOKIE}" if secure else CSRF_COOKIE


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def cookies_are_secure() -> bool:
    """Whether the browser will send these cookies back.

    `Secure` cookies are only stored over HTTPS, and `__Host-` mandates it. On a
    plain-http origin (local dev, the UI verifier) setting `Secure` produces a
    cookie the browser silently discards — which would look exactly like a
    broken refresh path, not like a misconfiguration. So the flag follows the
    environment: prod is https-only (enforced at startup in `config.py`), dev is
    not.

    `app_env` is whitelisted by the config validator, so this is not reading an
    attacker-influenced value.
    """
    return get_settings().app_env == "prod"


def same_site_policy() -> str:
    """`Strict` in prod, `Lax` in dev.

    `Strict` is the right production value: the console is a separate origin
    from the API in a split deploy, and a `Strict` cookie is not sent on a
    cross-site request at all, which stops the simplest CSRF before the CSRF
    token is even consulted.

    Dev uses `Lax` because the console and the API sit on different ports
    (`:8081` and `:8000`), which the browser treats as different *sites* for
    cookie purposes in some configurations — and a `Strict` cookie that is never
    sent makes the refresh path untestable locally while looking correct.
    """
    return "strict" if cookies_are_secure() else "lax"


@dataclass(frozen=True)
class AdminRotateOutcome:
    """Result of a rotation attempt.

    Mirrors `refresh_service.RotateOutcome`, deliberately — the two flows differ
    in storage but not in shape, and keeping the field names aligned means the
    API-layer handling reads the same way in both places.

    - `new_refresh`/`new_csrf` set -> success; write both cookies.
    - `reused` True                -> a revoked token was replayed; revoke all.
    - all unset, `reused` False    -> unknown or expired; plain 401.
    """

    admin_id: object | None = None
    new_refresh: str | None = None
    new_csrf: str | None = None
    reused: bool = False


class AdminRefreshService:
    """Issue, rotate and revoke admin refresh tokens."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def issue(self, admin_id) -> tuple[str, str]:
        """Create a refresh token and its CSRF partner.

        Returns `(raw_refresh, raw_csrf)`. The raw values are returned exactly
        once, to be written into cookies; only their digests are stored. There
        is no way to read them back — which is the point, but it also means a
        lost cookie is a re-login, not a lookup.
        """
        raw = secrets.token_urlsafe(48)
        csrf = secrets.token_urlsafe(32)
        settings = get_settings()
        self.session.add(
            AdminRefreshToken(
                admin_id=admin_id,
                token_hash=_hash(raw),
                csrf_hash=_hash(csrf),
                expires_at=_now() + timedelta(days=settings.refresh_token_expire_days),
            )
        )
        await self.session.flush()
        return raw, csrf

    async def rotate(self, raw_token: str, presented_csrf: str | None) -> AdminRotateOutcome:
        """Single-use rotation, with replay detection and a CSRF check.

        `with_for_update()` is load-bearing here for the same reason it is in
        the user flow: two concurrent requests presenting the same cookie would
        otherwise both read `revoked_at IS NULL`, both pass the check, and both
        mint a new pair — which defeats single-use and turns a replayed cookie
        into a free session. The row lock serializes them so the loser sees
        `revoked_at` set, and that is then classified as a replay.

        The CSRF comparison happens **before** the row is touched and uses
        `hmac.compare_digest`, so a wrong token neither rotates nor revokes
        anything: a failed CSRF check must not be usable to log the real
        operator out. It compares digests rather than raw values so the stored
        form is what is compared, and a timing signal on the digest is not a
        timing signal on the secret.

        Order of checks, and why: replay is decided *before* CSRF. A replayed
        token is evidence of theft and must revoke the family regardless of
        whether the replaying party also knew the CSRF value — deferring the
        revocation until a well-formed CSRF header arrived would let an attacker
        probe with the token and learn it is dead without ever tripping the
        alarm.
        """
        settings = get_settings()
        row = (
            (
                await self.session.execute(
                    select(AdminRefreshToken)
                    .where(AdminRefreshToken.token_hash == _hash(raw_token))
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if row is None or row.expires_at <= _now():
            return AdminRotateOutcome()
        if row.revoked_at is not None:
            if settings.refresh_reuse_detection:
                await self.revoke_all_for_admin(row.admin_id)
                logger.critical(
                    "admin refresh token replay detected for admin %s — revoking all sessions",
                    row.admin_id,
                )
                return AdminRotateOutcome(admin_id=row.admin_id, reused=True)
            return AdminRotateOutcome()

        if not presented_csrf or not hmac.compare_digest(row.csrf_hash, _hash(presented_csrf)):
            # Not a replay — a request that carried the cookie but not the
            # matching header. Refused without side effects.
            logger.warning("admin refresh refused: CSRF token missing or mismatched")
            return AdminRotateOutcome()

        row.revoked_at = _now()
        new_raw, new_csrf = await self.issue(row.admin_id)
        await self.session.flush()
        return AdminRotateOutcome(admin_id=row.admin_id, new_refresh=new_raw, new_csrf=new_csrf)

    async def revoke_raw(self, raw_token: str) -> bool:
        """Revoke one token. Returns whether it was live. Used on logout."""
        row = await self.lookup(raw_token)
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = _now()
        await self.session.flush()
        return True

    async def lookup(self, raw_token: str) -> AdminRefreshToken | None:
        """Find the row for a raw token, without locking or modifying it.

        A plain read, so a caller that only wants to inspect (logout checking
        the CSRF binding, for instance) does not take a row lock it will not
        use. `NO FOR UPDATE` deliberately: logout is not a race against another
        rotation in any way that matters — worst case both revoke the same row,
        and the second is a no-op.
        """
        row = (
            (
                await self.session.execute(
                    select(AdminRefreshToken).where(
                        AdminRefreshToken.token_hash == _hash(raw_token)
                    )
                )
            )
            .scalars()
            .first()
        )
        return row

    def csrf_matches(self, row: AdminRefreshToken, presented: str | None) -> bool:
        """Whether `presented` is the CSRF partner of `row`."""
        if not presented:
            return False
        return hmac.compare_digest(row.csrf_hash, _hash(presented))

    async def revoke_all_for_admin(self, admin_id) -> int:
        res = await self.session.execute(
            update(AdminRefreshToken)
            .where(
                AdminRefreshToken.admin_id == admin_id,
                AdminRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=_now())
        )
        await self.session.flush()
        return res.rowcount or 0
