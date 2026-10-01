"""Response schemas shared by every route.

These exist for one reason: **the OpenAPI document did not describe the wire
format.** Before this package, 29 `BaseModel`s lived inside `app/api/*.py` and
exactly one route used `response_model=`. FastAPI therefore published `{}` as
the schema for almost every response, and the real contract lived only in the
Dart models — which is why `mobile/tool/verify_contract.dart` and a directory of
54 captured fixtures had to exist at all.

That is not a documentation nicety. A response model is a *filter*: FastAPI
validates the returned value against it and **silently drops any key the model
does not declare**. So the schemas here are the contract, and they are derived
from the captured fixtures rather than from reading the handler code — a
fixture is a real response, and the fixture set is enforced against the Dart
decoders in both directions.

Rules for everything in this package
------------------------------------
1. **Every field a fixture actually contains must be declared.** A missing
   field is not a cosmetic omission; it deletes data from the response.
2. **Money and meter figures are `str`, never `Decimal`.** The wire format is a
   JSON *string* (pydantic serialises `Decimal` as a string anyway, in whatever
   precision it happens to hold) — declaring `str` makes the contract explicit
   and stops a future `Decimal` field from quietly changing shape. Two distinct
   rules apply: `money_str` = 2 dp for stored balances, `meter_str` = 1 dp for
   meter figures. The fixture files pin which one each field uses.
3. **Do not add fields the fixtures do not show.** A response model is the
   published contract; inventing a field documents something that is not sent.

Where a shape is genuinely polymorphic (the two fare blocks), see the note in
`app/api/schemas/fare.py`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "ErrorEnvelope",
    "ListEnvelope",
    "OkLogoutOut",
    "OkOut",
    "OkRevokedOut",
    "PageEnvelope",
]


class ErrorEnvelope(BaseModel):
    """The body of every 4xx/5xx response — `app/core/exceptions.py::_error`.

    Declared even though Starlette's exception handlers build it outside the
    route-signature machinery (so `response_model=` cannot be attached to it
    directly). Keeping it as a model means `GET /openapi.json` can reference it
    from the generic `responses=` block on the routers, and — more usefully —
    that the fixture `error_*.json` files have a Python counterpart to be
    checked against instead of only a Dart one.

    `details` is `{}` rather than absent when there is nothing to say, and its
    keys are per-error-code: `retry_after_seconds` on a cooldown,
    `attempts_remaining` on a bad OTP, `from`/`to` on an illegal transition,
    `errors` on a 422. It was being discarded for four endpoints because
    `BusinessRuleError` subclasses `ValueError` and the routes caught
    `ValueError` first — see the header of `mobile/tool/verify_contract.dart`.
    """

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class OkOut(BaseModel):
    """`{"ok": true}` — the acknowledgement for fire-and-forget writes.

    `POST /driver/location` is called every 3-5 s per online driver, so its body
    is deliberately a single boolean rather than the stored row.
    """

    ok: bool = True


class ListEnvelope(BaseModel):
    """`{"items": [...]}` — an unpaginated list response.

    Three different list shapes exist and they are **not** interchangeable;
    conflating them was one of the defects `verify_contract.dart` caught:

    - `ListEnvelope` (this): `items` only. `GET /orders`, `/orders/nearby`,
      `/fleets/{id}/members`.
    - `PageEnvelope`: `items` + `total`/`limit`/`offset` for the admin console.
    - `{"items", "next_cursor"}`: keyset-paginated, used by the ledger.

    The client infers "is there another page" differently for each, so the
    distinction is load-bearing rather than stylistic.
    """

    items: list[Any]


class OkRevokedOut(OkOut):
    """`{"ok": true, "revoked": <n>}` — `POST /auth/logout` (the **app** side).

    `revoked` is an **int**: the number of refresh tokens killed.
    `RefreshService.revoke_all_for_user` returns a count, and the app surfaces it
    so a driver can see that more than one device was signed out.

    Contrast `OkLogoutOut` below — the admin route reuses the same key name with
    a *boolean* meaning. They are deliberately separate models: collapsing them
    would let pydantic coerce `False` to `0` (or a count to `True`) and the
    caller would read a success flag where it expected a tally. That is not
    hypothetical — sharing one model here turned an admin logout's
    `revoked: false` into `revoked: 1` and failed
    `test_logout_revokes_server_side_not_just_the_cookie`.
    """

    revoked: int


class OkLogoutOut(OkOut):
    """`{"ok": true, "revoked": <bool>}` — `POST /admin/auth/logout`.

    A boolean, not a count: the admin route revokes the whole family at once and
    only reports whether it *did* (the CSRF token gates the revocation but not
    the cookie clearing — see `app/api/admin_auth.py`), so `False` means "the
    cookie was cleared but the server-side session could not be revoked", which
    is a different and more useful fact than "zero tokens".
    """

    revoked: bool


class PageEnvelope(BaseModel):
    """`{"items", "total", "limit", "offset"}` — admin list responses.

    Offset-paginated on purpose: the console shows a count ("18 drivers") and
    lets an operator jump to a page, which a keyset cursor cannot express.
    """

    items: list[Any]
    total: int
    limit: int
    offset: int
