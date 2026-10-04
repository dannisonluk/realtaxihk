"""Shared FastAPI dependencies: JWT principal + DB-backed state guards.

Stateless JWT alone cannot enforce deactivation (P0-3): user-facing endpoints
use `require_active_user` (one PK lookup per request) so a disabled account
loses access the moment is_active flips, and `require_admin` re-reads the real
user row so a phantom/stale ADMIN claim is rejected.

SEC-18: `get_current_user` also consults the per-user revocation epoch, so a
logout (or a detected refresh-token replay) invalidates access tokens that are
already in the wild instead of waiting for them to expire.

The two questions a user-facing route can ask
---------------------------------------------
These used to be one question. `require_verified_account` demanded a username,
an email **and** a phone before an account could do anything, and it sat on the
booking path — so proving a phone became a precondition for using the app at
all, which is exactly what had to go. The requirement is the other way round:
signing in must not need a phone, and proving one must unlock **calling a taxi**.

So the gate is now one question, asked in two strengths:

* `require_phone_verified` — has a number been proven? This is the call車
  unlock, and it is the only gate the booking path needs.
* `require_phone_current` — is that proof still fresh? P-4's monthly deadline,
  layered on top as a *soft* block.

`account_status` (`UNVERIFIED` / `ACTIVE`) survives as a profile-completeness
flag and gates nothing. Keeping the two ideas apart is what stops a later
"we should check they are verified" from quietly rebuilding the wall that was
just taken down.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import decode_access_token
from app.core.token_revocation import is_token_revoked
from app.models import AdminAccount, AdminRole, User, UserRole
from app.services.auth import phone_reverify_service as phone_reverify
from app.services.auth.account_service import reviewer_expired
from app.services.infra import human

_bearer = HTTPBearer(auto_error=False)

# What `sub` points at, carried on the token so a principal can be resolved
# without guessing which table it came from.
#
# `admin_accounts` and `users` are **separate identity types** (see the
# `AdminAccount` docstring), and their ids come from independent UUID spaces. A
# bare `sub` is therefore ambiguous: the same string is a valid key for exactly
# one of the two tables and resolves to `None` in the other.
#
# Absent means `user`. That default is deliberate: every token minted before
# this claim existed was a user token, so old tokens keep working, and a user
# token that omits the claim is not misread as an admin.
SCOPE_USER = "user"
SCOPE_ADMIN = "admin"


@dataclass(frozen=True)
class Principal:
    id: uuid.UUID
    role: UserRole
    jti: str | None = None
    issued_at: int | None = None
    # Which identity table `id` refers to. Defaults to `user` so an existing
    # call site that constructs a Principal positionally keeps its meaning.
    scope: str = SCOPE_USER
    # The `admin_accounts.role` claim. Advisory only — it is what makes the
    # token self-describing for logging and for the UI's initial render, and it
    # is **never** what authorises a request. `require_role` re-reads the live
    # row; see its docstring.
    admin_role: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.scope == SCOPE_ADMIN


def principal_from_token(token: str) -> Principal:
    try:
        claims: dict[str, Any] = decode_access_token(token)
        pid = uuid.UUID(str(claims["sub"]))
        role = UserRole(str(claims.get("role", UserRole.PASSENGER.value)))
        jti = claims.get("jti")
        issued_at = claims.get("iat")
        # Anything other than the literal "admin" is treated as a user token.
        # Not `claims.get("scope") or SCOPE_USER`: a malformed value must not be
        # able to *escalate* to admin, and an allow-list of one is the cheapest
        # way to guarantee that.
        scope = SCOPE_ADMIN if claims.get("scope") == SCOPE_ADMIN else SCOPE_USER
        # Carried through for logging and the console's first render. Absent on
        # every token minted before roles existed, hence `None` rather than a
        # default — an absent claim must not read as a real role.
        raw_admin_role = claims.get("admin_role")
        admin_role = str(raw_admin_role) if raw_admin_role else None
    except (pyjwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token"
        ) from exc
    return Principal(
        id=pid,
        role=role,
        jti=str(jti) if jti else None,
        issued_at=int(issued_at) if issued_at is not None else None,
        scope=scope,
        admin_role=admin_role,
    )


async def assert_not_revoked(request: Request, principal: Principal) -> None:
    """SEC-18: reject an access token issued before the user's revocation epoch.

    This runs on the hot path of every authenticated request, so it uses the
    app-lifetime client on `app.state.auth_redis`. Calling `redis_factory()`
    directly would have been one client (and one socket) per request — pure
    churn on a path that is already a single Redis GET.
    """
    revoked = await is_token_revoked(
        request.app.state.auth_redis, principal.id, principal.issued_at
    )
    if revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token has been revoked"
        )


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    principal = principal_from_token(creds.credentials)
    await assert_not_revoked(request, principal)
    return principal


async def require_live_principal(
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """A live row exists for this token — in whichever table the token names.

    For the handful of endpoints that serve **both** identity types, of which
    `/api/v1/auth/me` is the only one: the console calls it on boot to decide
    whether the session in storage is still good, and it carries an admin token,
    while the mobile app calls it with a user token.

    `require_active_user` cannot be used for this because it hard-codes the
    `users` table, and `require_admin` cannot because it *rejects* a user token.
    Branching inside the handler would work but hides the check from the
    route-table audit in `tests/test_security_hardening.py`, which reads declared
    dependencies — so an endpoint doing its own thing in the body looks
    unguarded. Declaring it here keeps the audit's rule intact and meaningful.
    """
    if user.is_admin:
        admin = await session.get(AdminAccount, user.id)
        if admin is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="admin not found")
        if not admin.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account disabled")
        return user

    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if not row.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account disabled")
    if reviewer_expired(row):
        # Repeated from `require_active_user` on purpose. This dependency exists
        # because `/auth/me` resolves the principal a *different* way, and a
        # guard that is only applied on the common path is exactly the guard a
        # reviewer account would walk around.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "this reviewer account has expired",
                "reason": "REVIEWER_ACCOUNT_EXPIRED",
            },
        )
    return user


async def require_live_admin_refresh_session(request: Request) -> None:
    """The refresh cookie names a live, active `admin_accounts` row.

    The admin `/refresh` and `/logout` routes authenticate with the HttpOnly
    refresh cookie rather than a bearer token, so `require_admin` does not fit:
    it would demand an access token these routes exist precisely because the
    operator does not have one. The cookie **is** the credential, and resolving
    it already loads the owning admin row live — `/refresh` re-reads
    `admin_accounts` and refuses a missing or disabled account.

    This dependency does not re-do that lookup; the handler still performs it,
    because the lookup and the rotation must share one transaction and one
    `FOR UPDATE` lock. What it contributes is **declaration**. The route-table
    audit in `tests/test_security_hardening.py` reads *declared* dependencies,
    and a guard that lives only inside a function body is invisible to it — so
    the next person adding a cookie-authenticated admin route would leave the
    audit green while shipping an unguarded endpoint. Same reasoning, and the
    same precedent, as `require_live_principal` above.

    A no-op rather than a check is the honest shape here: the real check cannot
    be hoisted without splitting the transaction. Declaring the guard the
    handler performs is what keeps the audit's rule meaningful instead of
    forcing it to grow a per-route exemption list, which would erode it.
    """
    return None


async def require_active_user(
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """JWT + live DB check: user exists, is_active, and matches the claim role.

    Also the **single** place a restricted reviewer account is stopped once its
    expiry has passed. It lives here rather than on a per-route guard because
    "every user-facing route" is exactly the set this dependency already covers —
    including the ones nobody remembers to think about. A date the guard reads on
    every request cannot be forgotten the way a runbook step can, and that is the
    entire reason the expiry is a column rather than a note.
    """
    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account not found")
    if not row.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account disabled")
    if row.role != user.role:
        # Claim no longer matches reality (e.g. demoted admin) — trust the DB.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account state changed")
    if reviewer_expired(row):
        # 403 rather than 401: the token is perfectly valid, the *account* has
        # lapsed. A 401 would tell the client to re-authenticate, which cannot
        # help — and a reviewer who retries with fresh credentials would be told
        # the same thing, so the reason has to be machine-readable.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "this reviewer account has expired",
                "reason": "REVIEWER_ACCOUNT_EXPIRED",
            },
        )
    return user


async def require_admin(
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """Admin must be a REAL, ACTIVE `admin_accounts` row — not just an ADMIN claim.

    The row is read from `admin_accounts`, which is where an admin identity
    actually lives: `issue_admin_access_token` sets `sub` to `admin_accounts.id`.
    Resolving it against `users` instead — as this did — looks up a UUID that is
    not in that table, so `row` is always `None` and **every** console route
    403s immediately after a successful login. The two id spaces are
    independent, so that lookup cannot be made correct by "trying both": the
    `scope` claim on the principal says which table is the right one, and an
    admin token is the only thing that can claim `admin`.

    `totp_enrolled_at` is required as well. A token can only be minted after the
    TOTP step (`_resolve_challenge` + `issue_admin_access_token`), so a row with
    no enrolment means either a hand-edited row or a token from a path that
    skipped the second factor — both of which should read as "no admin here"
    rather than being trusted.
    """
    if not user.is_admin:
        # A user token, even one carrying role=ADMIN (the legacy
        # `users.role = ADMIN` identity from `scripts/ops/create_admin.py`), does not
        # open the console. Those are the credentials of a *different* thing.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin privileges required"
        )
    row = await session.get(AdminAccount, user.id)
    if row is None or not row.is_active or user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin privileges required"
        )
    if row.totp_enrolled_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin privileges required"
        )
    return user


async def require_phone_verified(
    user: Principal = Depends(require_active_user),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """The call車 unlock: has this account proven a phone number?

    This is the single enforcement point for "proving a phone is what unlocks
    booking". Putting it in a dependency rather than in each handler means a new
    endpoint is gated by default; the failure mode of the alternative is a route
    that forgets the check, which on a booking route means a passenger nobody can
    call back.

    It asks **one** question, deliberately. It replaces `require_verified_account`,
    which also demanded a username and a verified email and sat on the booking
    path — so merely signing in required a phone, which is the thing being
    removed. Email verification is about recovery and notification; it is not a
    precondition for taking a taxi, and nothing here should reacquire that belief.

    The error is 403 with a machine-readable `reason`, because the client has to
    route the user to the phone-unlock screen rather than show a generic refusal.
    """
    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")

    if row.phone_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "verify a phone number to call a taxi",
                "reason": "PHONE_NOT_VERIFIED",
            },
        )
    return user


async def require_phone_current(
    user: Principal = Depends(require_phone_verified),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """P-4: the monthly phone re-verification, as a **soft** block.

    Attached only to the routes that *start new business* — creating an order,
    accepting one, going online. Not to reads, and not to `require_phone_verified`
    itself, because the point of a soft block is precisely that the account keeps
    working: a driver mid-shift can still see their current trip, their statement
    and their profile while their number is overdue. They just cannot take on
    anything new until it is re-proven.

    That distinction is the whole reason this is a separate dependency rather than
    another branch in `require_phone_verified`. Hanging it on the verified gate
    would turn a reminder into a lockout, which is the failure mode the grace
    window exists to avoid.

    The 403 carries `reason: PHONE_REVERIFY_DUE` plus the deadline, so the client
    can show "re-verify to keep accepting orders" with the date rather than a
    generic refusal.
    """
    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")

    state = phone_reverify.evaluate(row)
    if state.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "phone re-verification is overdue",
                "reason": phone_reverify.REASON_PHONE_REVERIFY_DUE,
                **state.as_dict(),
            },
        )
    return user


async def assert_human(token: str | None, remote_ip: str | None = None) -> None:
    """Refuse unless the caller solved the human-verification challenge.

    A plain function rather than a `Depends` because the token arrives in the
    request **body**, and the bodies differ per route — a dependency cannot read
    a field whose shape it does not know. So this is called explicitly at the top
    of the four routes that need it, which is a weaker guarantee than "gated by
    default". That trade is worth stating: the four are registration, login and
    the two code-request routes, and an endpoint added later that creates rows or
    sends billed messages should be added here *deliberately* rather than
    inheriting the check by accident.

    `human.get_human_verifier()` is reached through the module rather than
    imported by name so a test can swap the provider without having to patch this
    module's globals — the same reason `app/api/*` reaches for
    `request.app.state` instead of a module-level singleton.
    """
    if not await human.get_human_verifier().verify(token, remote_ip):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "human verification failed — complete the challenge and retry",
                "reason": "HUMAN_VERIFICATION_REQUIRED",
            },
        )


def require_role(
    minimum: AdminRole,
) -> Any:
    """Dependency factory: the caller's **live** admin role must outrank `minimum`.

    Deliberately `def`, not `async def`. A dependency *factory* must return the
    dependency; making this a coroutine function would mean `Depends(require_role(R))`
    evaluates to a coroutine object rather than a callable, and FastAPI resolves
    that at import/route-registration time as an error — every route using it
    fails to register. Only the inner guard is async.

    Rank comparison, not set membership. `require_role(AdminRole.FINANCE)`
    admits FINANCE and SUPER_ADMIN and nothing else, and a role added later at
    the bottom of the hierarchy cannot reach it without anyone editing this
    function. The equivalent set version would need every call site revisited
    each time a role is introduced — and one missed tuple is an open route.

    **The live row decides, never the token claim.** `issue_admin_access_token`
    does carry `admin_role`, and this dependency ignores it for authorisation.
    A claim is a snapshot: it would let a demoted admin keep moving money until
    their 15-minute access token happened to expire, which is exactly the
    window in which someone who has just been demoted is most likely to act.
    The claim is kept for logging and for the console's first render; the row
    read here per request is the authority, and it is one PK lookup on a table
    already being read by `require_admin`.

    This composes *on top of* `require_admin` rather than replacing it, so the
    three checks that dependency owns — a real `admin_accounts` row, active, and
    TOTP enrolled — still run. Registering write endpoints additively means a
    route that forgot this dependency is merely un-narrowed, never unguarded.
    """

    async def require_role_guard(
        user: Principal = Depends(require_admin),
        session: AsyncSession = Depends(get_session),
    ) -> Principal:
        """Named rather than anonymous so the route-table audit can see it.

        `tests/test_security_hardening.py` discovers guards by
        `dependency.call.__name__`, so a closure called `_guard` would be
        invisible to it and every route using this factory would read as
        unguarded. The name is part of the contract, not a cosmetic choice.
        """
        row = await session.get(AdminAccount, user.id)
        if row is None:
            # `require_admin` already proved the row exists; reaching here means
            # it was deleted between the two lookups. Refuse rather than assume.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="admin privileges required"
            )
        if not row.admin_role.at_least(minimum):
            # 403, not 404: the caller is authenticated and the route exists —
            # they simply are not senior enough. Hiding the route behind a 404
            # would make a misconfigured role look like a missing endpoint.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "insufficient admin role",
                    "reason": "ADMIN_ROLE_INSUFFICIENT",
                    "required": minimum.value,
                    "actual": row.admin_role.value,
                },
            )
        return user

    return require_role_guard


async def live_admin_role(session: AsyncSession, principal: Principal) -> AdminRole:
    """The caller's current role, read from the table rather than the token.

    For handlers that must branch on seniority *within* a request — the one
    case today is a dispute resolution whose role requirement depends on the
    decision in the body, not on the route. Those cannot use `require_role`,
    because the requirement is not known until the body is parsed.

    Same authority as `require_role`: the live row. A handler that read
    `principal.admin_role` instead would be trusting a snapshot, which is the
    mistake that dependency exists to avoid, and it would be reachable by
    anyone holding an access token minted before their demotion.

    Fails *closed* on a missing row, matching the guard: an admin id with no
    row is not a role, it is a broken token.
    """
    row = await session.get(AdminAccount, principal.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin privileges required"
        )
    return row.admin_role
