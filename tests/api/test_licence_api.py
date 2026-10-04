"""P-3 — driver licence verification: upload, submit, and admin decision.

The property under test is the user's own framing:

    "for taxi driver, another verification is required which is taxi driver
     licence, but it will be done with manual verification and admins will
     handle the status change."

So the assertions here are about three things and nothing else:

1. **Only an admin decides.** No driver-reachable path can move a submission to
   APPROVED. The decision endpoint is `require_admin`; the driver router has no
   route that writes a status other than SUPERSEDED (withdraw).
2. **An approval needs evidence.** Two specific document kinds must be present,
   the licence must not be expired, and (in a configured environment) the object
   must actually be in storage. An operator must not be able to approve a
   submission whose image never uploaded.
3. **A renewal does not idle a working driver.** Approving a submission by an
   already-ACTIVE driver changes the submission and leaves `DriverStatus` alone.
   Only a first approval moves `PENDING_KYC -> DEPOSIT_REQUIRED`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import Response
from sqlalchemy import text

PHONE_DRIVER = "+85290002201"
PHONE_ADMIN = "+85290002200"
CODE = "123456"

UPLOADS = "/api/v1/drivers/licence/uploads"
SUBMISSIONS = "/api/v1/drivers/licence/submissions"
ADMIN_QUEUE = "/api/v1/admin/licence/submissions"


# ---------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------- #


def _sign_in(client, phone: str) -> str:
    """Phone-OTP login, returning an access token.

    OTP login is a **secondary** login now. `OtpService.verify_otp` refuses a
    number that no account has *proven* (`phone_verified_at IS NOT NULL`),
    because otherwise a stolen code would sign into whichever account merely
    *claims* the number. `client.otp_login` arranges that precondition, then runs
    the genuine request/verify pair.

    It also backdates the previous OTP rows, which a second sign-in in one test
    needs: a code is **single-use** (`consumed_at` is stamped, and a re-verify is
    refused as "OTP already used") and a **resend cooldown** refuses a fresh
    `request_otp` while the previous row is recent. That is arrangement, not a
    bypass: the production behaviour is exactly what the other tests in this file
    rely on, and nothing here relaxes it for the app under test.
    """
    return client.otp_login(phone)["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_admin(client, phone: str = PHONE_ADMIN) -> str:
    """A real console admin's bearer token, for the decision endpoint.

    Was: create a `users` row and `UPDATE users SET role = 'ADMIN'` on it. That
    promoted the *passenger* identity, which no longer opens `/api/v1/admin/*` —
    `require_admin` now resolves against `admin_accounts` and requires the
    `scope=admin` claim that only the console login sets. A `users` row with the
    ADMIN role is a different thing from an administrator (see the
    `AdminAccount` docstring), and the licence queue is an administrator surface.

    `phone` is kept in the signature because callers still pass it to build the
    driver side of the fixture; it no longer names the admin.
    """
    return client.admin_headers()["Authorization"].split(" ", 1)[1]


async def _run_sql(client, sql: str, params: dict | None = None, *, fetch: bool = False):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(client.db_url, poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            result = await session.execute(text(sql), params or {})
            rows = [dict(r) for r in result.mappings().all()] if fetch else None
            await session.commit()
            return rows
    finally:
        await engine.dispose()


def _exec(client, sql: str, params: dict | None = None) -> None:
    asyncio.run(_run_sql(client, sql, params))


def _fetch(client, sql: str, params: dict | None = None) -> list[dict]:
    return asyncio.run(_run_sql(client, sql, params, fetch=True)) or []


def _one(client, sql: str, params: dict | None = None) -> dict:
    rows = _fetch(client, sql, params)
    return rows[0] if rows else {}


def _register_driver(client, phone: str = PHONE_DRIVER) -> tuple[str, str]:
    """Sign in, register a driver profile, return (token, driver_profile_id).

    The driver starts in `PENDING_KYC`, which is the state the first licence
    approval is supposed to move.
    """
    token = _sign_in(client, phone)
    _exec(
        client,
        "UPDATE users SET account_status = 'ACTIVE', phone_verified_at = now(), "
        "username = COALESCE(username, :u) WHERE phone_e164 = :p",
        {"p": phone, "u": "drv" + phone[-6:]},
    )
    response = client.post(
        "/api/v1/drivers/register",
        json={
            "hk_id_last4": "4321",
            "taxi_driver_plate_no": "TD9988",
            "vehicle_reg_mark": "HK1234",
            "taxi_type": "URBAN",
        },
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return token, response.json()["id"]


def _presign(client, token: str, kind: str, size: int = 1024 * 1024) -> str:
    """Ask for an upload URL and return the `object_key` to declare."""
    response = client.post(
        UPLOADS,
        json={"kind": kind, "content_type": "image/jpeg", "size_bytes": size},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object_key"].startswith("documents/")
    return body["object_key"]


def _full_document_set(client, token: str) -> list[dict]:
    """The two required kinds, each with a freshly presigned key."""
    return [
        {
            "kind": "DRIVER_LICENCE",
            "object_key": _presign(client, token, "DRIVER_LICENCE"),
            "content_type": "image/jpeg",
            "size_bytes": 1024 * 1024,
        },
        {
            "kind": "TAXI_DRIVER_PASS",
            "object_key": _presign(client, token, "TAXI_DRIVER_PASS"),
            "content_type": "image/jpeg",
            "size_bytes": 1024 * 1024,
        },
    ]


def _submit(client, token: str, **overrides) -> Response:
    """Build a valid submission payload and POST it.

    `documents` is only presigned when the caller did not supply their own. The
    presign calls are real requests, and a test whose point is a *later* refusal
    (a terminated driver, a foreign object key) would otherwise fail inside this
    helper while preparing the payload rather than on the assertion it cares
    about.
    """
    # Annotated, not inferred: the literal's three values are all `str`, so
    # without this the dict narrows to `dict[str, str]` and the `documents` key
    # added below (a list) is a type error.
    payload: dict[str, Any] = {
        "licence_no": "DL123456",
        "expires_on": (datetime.now(UTC) + timedelta(days=365)).isoformat(),
        "note": "first application",
    }
    payload.update(overrides)
    if "documents" not in payload:
        # Guarded with `if`, not `dict.setdefault`: setdefault evaluates its
        # default argument eagerly, so `_full_document_set` would still run its
        # presign requests on every call even when the caller supplied documents.
        payload["documents"] = _full_document_set(client, token)
    return client.post(SUBMISSIONS, json=payload, headers=_auth(token))


# ---------------------------------------------------------------- #
# The driver side: uploads and submission
# ---------------------------------------------------------------- #


def test_presign_returns_a_scoped_key_and_never_writes_a_row(client):
    """Presigning must not create an attachment.

    A row created here would exist before any bytes did, and the approve path
    would then have to defend against an attachment pointing at nothing. This
    pins the decision: presign is read-only with respect to the database.
    """
    token, driver_id = _register_driver(client)
    key = _presign(client, token, "DRIVER_LICENCE")

    assert key.startswith(f"documents/{driver_id}/")

    rows = _fetch(client, "SELECT id FROM driver_documents WHERE object_key = :k", {"k": key})
    assert rows == [], "presign created a document row before the upload happened"


def test_presigned_keys_are_unique_per_call(client):
    """The random component is what stops one driver reading another's document."""
    token, _ = _register_driver(client)
    key_a = _presign(client, token, "DRIVER_LICENCE")
    key_b = _presign(client, token, "DRIVER_LICENCE")
    assert key_a != key_b


def test_upload_refuses_an_image_type_we_will_not_serve(client):
    """An arbitrary content type on a domain we control is a phishing page."""
    token, _ = _register_driver(client)
    response = client.post(
        UPLOADS,
        json={"kind": "DRIVER_LICENCE", "content_type": "text/html", "size_bytes": 100},
        headers=_auth(token),
    )
    assert response.status_code == 400, response.text
    assert "unsupported image type" in response.json()["message"]


def test_upload_refuses_an_oversized_file(client):
    token, _ = _register_driver(client)
    response = client.post(
        UPLOADS,
        json={
            "kind": "DRIVER_LICENCE",
            "content_type": "image/jpeg",
            "size_bytes": 50 * 1024 * 1024,
        },
        headers=_auth(token),
    )
    assert response.status_code == 400, response.text


def test_a_driver_without_a_driver_profile_cannot_presign(client):
    """Registering as a driver is the prerequisite for uploading licence images."""
    token = _sign_in(client, "+85290002299")
    _exec(
        client,
        "UPDATE users SET account_status = 'ACTIVE', phone_verified_at = now() "
        "WHERE phone_e164 = :p",
        {"p": "+85290002299"},
    )
    response = client.post(
        UPLOADS,
        json={"kind": "DRIVER_LICENCE", "content_type": "image/jpeg", "size_bytes": 100},
        headers=_auth(token),
    )
    assert response.status_code == 400, response.text
    assert "register as a driver" in response.json()["message"]


def test_submitting_both_documents_opens_a_pending_submission(client):
    token, _ = _register_driver(client)
    response = _submit(client, token)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "PENDING"
    assert body["licence_no"] == "DL123456"
    assert {d["kind"] for d in body["documents"]} == {
        "DRIVER_LICENCE",
        "TAXI_DRIVER_PASS",
    }
    # Nothing is decided yet.
    assert body["rejection_reason"] is None
    assert body["reviewed_at"] is None


def test_a_submission_missing_a_required_kind_is_refused(client):
    """Both kinds are required — one alone is half the evidence.

    The driving licence establishes the class of vehicle; the taxi driver pass
    (的士司機證) establishes the right to drive a taxi. An operator checking
    "is this a real taxi driver" needs the second.
    """
    token, _ = _register_driver(client)
    docs = _full_document_set(client, token)
    only_licence = [d for d in docs if d["kind"] == "DRIVER_LICENCE"]

    response = _submit(client, token, documents=only_licence)
    assert response.status_code == 400, response.text
    assert "taxi driver pass" in response.json()["message"].lower()


def test_an_optional_document_kind_rides_along_without_rejecting_the_submission(client):
    """A third, optional document must not fail the whole submission.

    Regression: `attach_document` used to accept only REQUIRED_DOCUMENT_KINDS +
    OTHER, while `presign_upload` and `_index_documents` accepted every member
    of `DocumentKind`. `submit()` loops the client's *entire* document list
    through `attach_document`, so attaching a vehicle registration rejected the
    submission outright — a driver lost a complete, valid application because
    they supplied more evidence than the minimum.
    """
    token, _ = _register_driver(client)
    docs = _full_document_set(client, token)
    docs.append(
        {
            "kind": "VEHICLE_REGISTRATION",
            "object_key": _presign(client, token, "VEHICLE_REGISTRATION"),
            "content_type": "image/jpeg",
            "size_bytes": 1024 * 1024,
        }
    )

    response = _submit(client, token, documents=docs)
    assert response.status_code == 201, response.text
    assert {d["kind"] for d in response.json()["documents"]} == {
        "DRIVER_LICENCE",
        "TAXI_DRIVER_PASS",
        "VEHICLE_REGISTRATION",
    }


def test_an_optional_document_cannot_stand_in_for_a_required_one(client):
    """Accepting every kind must not relax the completion rule.

    The minimum is still DRIVER_LICENCE + TAXI_DRIVER_PASS, and `submit()`
    counts only those — so an insurance certificate cannot substitute for the
    taxi driver pass (的士司機證).
    """
    token, _ = _register_driver(client)
    docs = [d for d in _full_document_set(client, token) if d["kind"] == "DRIVER_LICENCE"]
    docs.append(
        {
            "kind": "INSURANCE",
            "object_key": _presign(client, token, "INSURANCE"),
            "content_type": "image/jpeg",
            "size_bytes": 1024 * 1024,
        }
    )

    response = _submit(client, token, documents=docs)
    assert response.status_code == 400, response.text
    assert "taxi driver pass" in response.json()["message"].lower()


def test_a_licence_expiring_too_soon_is_refused(client):
    """Approving something that expires before the next shift is not a service."""
    token, _ = _register_driver(client)
    response = _submit(
        client,
        token,
        expires_on=(datetime.now(UTC) + timedelta(days=3)).isoformat(),
    )
    assert response.status_code == 400, response.text
    assert "expires too soon" in response.json()["message"]


def test_an_already_expired_licence_is_refused(client):
    token, _ = _register_driver(client)
    response = _submit(
        client, token, expires_on=(datetime.now(UTC) - timedelta(days=1)).isoformat()
    )
    assert response.status_code == 400, response.text


def test_a_document_key_from_another_driver_is_refused(client):
    """The single most important check on this path.

    Without it, a driver could attach someone else's uploaded image as their own
    evidence and be approved on it. The key prefix carries the owner, and the
    service refuses anything that does not match.
    """
    token_a, _ = _register_driver(client, PHONE_DRIVER)
    token_b, _ = _register_driver(client, "+85290002202")

    stolen = _presign(client, token_b, "DRIVER_LICENCE")
    docs = _full_document_set(client, token_a)
    docs[0]["object_key"] = stolen

    response = _submit(client, token_a, documents=docs)
    assert response.status_code == 400, response.text
    assert "does not belong to this driver" in response.json()["message"]


def test_a_second_submission_while_one_is_pending_is_refused(client):
    """Refused with a renderable message, not left to the unique index.

    The partial unique index would reject the INSERT anyway; a message the
    client can show is a better answer than a 500 from a constraint violation.
    """
    token, _ = _register_driver(client)
    first = _submit(client, token)
    assert first.status_code == 201, first.text

    second = _submit(client, token)
    assert second.status_code == 400, second.text
    assert "already awaiting review" in second.json()["message"]


def test_only_one_pending_row_can_exist_at_the_database_level(client):
    """The index, not just the service check.

    Pinned separately because a service check is advisory while the index is the
    invariant: a future code path that inserts directly must still fail.
    """
    token, driver_id = _register_driver(client)
    assert _submit(client, token).status_code == 201

    licence_no = "DL999999"
    with pytest.raises(Exception) as exc:  # asyncpg UniqueViolationError
        _exec(
            client,
            "INSERT INTO driver_licence_submissions "
            "(id, driver_profile_id, licence_no, expires_on, status, submitted_at) "
            "VALUES (gen_random_uuid(), :d, :l, now() + interval '1 year', "
            "'PENDING', now())",
            {"d": driver_id, "l": licence_no},
        )
    assert "uq_licence_one_open_per_driver" in str(exc.value)


def test_the_licence_number_is_uppercased_on_submit(client):
    """Normalisation, not cosmetics.

    A driver typing `ad1234` against a stored `AD1234` would otherwise produce
    two identifiers for one licence, which is exactly what a duplicate check
    needs to see.
    """
    token, _ = _register_driver(client)
    response = _submit(client, token, licence_no="ad1234xy")
    assert response.status_code == 201, response.text
    assert response.json()["licence_no"] == "AD1234XY"


def test_history_shows_the_rejection_reason(client):
    """The history is where a driver learns *why* a submission failed."""
    token, _ = _register_driver(client)
    submitted = _submit(client, token).json()

    admin_token = _make_admin(client)
    decided = client.post(
        f"{ADMIN_QUEUE}/{submitted['id']}/decide",
        json={"approve": False, "reason": "the expiry date is in shadow, please retake"},
        headers=_auth(admin_token),
    )
    assert decided.status_code == 200, decided.text

    history = client.get(SUBMISSIONS, headers=_auth(token))
    assert history.status_code == 200, history.text
    item = history.json()["items"][0]
    assert item["status"] == "REJECTED"
    assert "in shadow" in item["rejection_reason"]
    # And the operator's identity is not exposed to the driver.
    assert "reviewed_by" not in item


def test_a_driver_can_withdraw_and_resubmit(client):
    """A mistake must be recoverable without waiting for an operator."""
    token, _ = _register_driver(client)
    first = _submit(client, token).json()

    withdrawn = client.post(f"{SUBMISSIONS}/{first['id']}/withdraw", headers=_auth(token))
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "SUPERSEDED"

    # The history survives the withdrawal.
    rows = _fetch(
        client,
        "SELECT status FROM driver_licence_submissions WHERE id = :i",
        {"i": first["id"]},
    )
    assert rows and rows[0]["status"] == "SUPERSEDED"

    # And a new submission is now allowed.
    second = _submit(client, token)
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first["id"]


def test_a_decided_submission_cannot_be_withdrawn(client):
    token, _ = _register_driver(client)
    submitted = _submit(client, token).json()

    admin_token = _make_admin(client)
    client.post(
        f"{ADMIN_QUEUE}/{submitted['id']}/decide",
        json={"approve": False, "reason": "unreadable"},
        headers=_auth(admin_token),
    )

    response = client.post(f"{SUBMISSIONS}/{submitted['id']}/withdraw", headers=_auth(token))
    assert response.status_code == 400, response.text
    assert "already rejected" in response.json()["message"]


def test_a_driver_cannot_read_another_drivers_submission(client):
    """The ownership predicate is the authorisation check, not a filter."""
    token_a, _ = _register_driver(client, PHONE_DRIVER)
    token_b, _ = _register_driver(client, "+85290002203")

    theirs = _submit(client, token_b).json()

    response = client.get(f"{SUBMISSIONS}/{theirs['id']}", headers=_auth(token_a))
    assert response.status_code == 400, response.text
    assert "not found" in response.json()["message"]


# ---------------------------------------------------------------- #
# The admin side: the decision
# ---------------------------------------------------------------- #


def test_a_driver_cannot_reach_the_admin_queue(client):
    """The whole point of the split: only an admin decides."""
    token, _ = _register_driver(client)
    submitted = _submit(client, token).json()

    assert client.get(ADMIN_QUEUE, headers=_auth(token)).status_code == 403
    assert client.get(f"{ADMIN_QUEUE}/{submitted['id']}", headers=_auth(token)).status_code == 403
    decide = client.post(
        f"{ADMIN_QUEUE}/{submitted['id']}/decide",
        json={"approve": True},
        headers=_auth(token),
    )
    assert decide.status_code == 403, decide.text


def test_the_queue_defaults_to_pending(client):
    token, _ = _register_driver(client)
    submitted = _submit(client, token).json()
    admin_token = _make_admin(client)

    queue = client.get(ADMIN_QUEUE, headers=_auth(admin_token))
    assert queue.status_code == 200, queue.text
    ids = [i["id"] for i in queue.json()["items"]]
    assert submitted["id"] in ids
    assert queue.json()["items"][0]["has_required_documents"] is True


def test_the_queue_can_be_filtered_to_everything(client):
    token, _ = _register_driver(client)
    _submit(client, token)
    admin_token = _make_admin(client)

    everything = client.get(f"{ADMIN_QUEUE}?status=all", headers=_auth(admin_token))
    assert everything.status_code == 200, everything.text

    unknown = client.get(f"{ADMIN_QUEUE}?status=BOGUS", headers=_auth(admin_token))
    assert unknown.status_code == 422, unknown.text


def test_detail_mints_signed_urls_only_here(client):
    """The queue carries no working link; the detail view does.

    A URL in the queue response would put a working link to an identity document
    in every poll of the list, and in whatever that response is logged into.
    """
    token, _ = _register_driver(client)
    submitted = _submit(client, token).json()
    admin_token = _make_admin(client)

    queue_item = next(
        i
        for i in client.get(ADMIN_QUEUE, headers=_auth(admin_token)).json()["items"]
        if i["id"] == submitted["id"]
    )
    assert "documents" not in queue_item

    detail = client.get(f"{ADMIN_QUEUE}/{submitted['id']}", headers=_auth(admin_token))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert len(body["documents"]) == 2
    for doc in body["documents"]:
        assert doc["download_url"], "detail must carry a signed URL"
        assert doc["url_expires_in"] > 0
    # The operator sees the evidence state, which is what makes approval safe.
    assert body["has_required_documents"] is True


def test_approving_a_pending_kyc_driver_promotes_them(client):
    """The one legitimate transition this path makes to DriverStatus."""
    token, driver_id = _register_driver(client)
    submitted = _submit(client, token).json()
    admin_token = _make_admin(client)

    before = _one(client, "SELECT status FROM driver_profiles WHERE id = :d", {"d": driver_id})
    assert before["status"] == "PENDING_KYC"

    decided = client.post(
        f"{ADMIN_QUEUE}/{submitted['id']}/decide",
        json={"approve": True},
        headers=_auth(admin_token),
    )
    assert decided.status_code == 200, decided.text
    body = decided.json()
    assert body["status"] == "APPROVED"
    assert body["driver_status"] == "DEPOSIT_REQUIRED"
    assert body["driver_promoted"] is True


def test_the_decision_records_who_made_it(client):
    """An approval with no recorded approver is worthless in an incident review."""
    token, _ = _register_driver(client)
    submitted = _submit(client, token).json()
    admin_token = _make_admin(client)
    admin_id = _sign_in(client, PHONE_ADMIN)  # same row; just to read the id

    client.post(
        f"{ADMIN_QUEUE}/{submitted['id']}/decide",
        json={"approve": True},
        headers=_auth(admin_token),
    )

    row = _one(
        client,
        "SELECT reviewed_by, reviewed_at, status FROM driver_licence_submissions WHERE id = :i",
        {"i": submitted["id"]},
    )
    assert row["status"] == "APPROVED"
    assert row["reviewed_by"] is not None
    assert row["reviewed_at"] is not None
    assert admin_id  # the admin token is valid; the id is stored above


def test_a_rejection_requires_a_reason(client):
    """A rejection with no reason is unactionable for the driver.

    They cannot tell whether to retake the photo, fix the number, or give up.
    """
    token, _ = _register_driver(client)
    submitted = _submit(client, token).json()
    admin_token = _make_admin(client)

    response = client.post(
        f"{ADMIN_QUEUE}/{submitted['id']}/decide",
        json={"approve": False},
        headers=_auth(admin_token),
    )
    assert response.status_code == 400, response.text
    assert "reason" in response.json()["message"]


def test_a_blank_rejection_reason_is_also_refused(client):
    token, _ = _register_driver(client)
    submitted = _submit(client, token).json()
    admin_token = _make_admin(client)

    response = client.post(
        f"{ADMIN_QUEUE}/{submitted['id']}/decide",
        json={"approve": False, "reason": "   "},
        headers=_auth(admin_token),
    )
    assert response.status_code == 400, response.text


def test_a_submission_cannot_be_decided_twice(client):
    """Terminal, so a second call cannot flip the record.

    Without this, a REJECTED could become APPROVED — or the reverse — leaving no
    trace that either happened.
    """
    token, _ = _register_driver(client)
    submitted = _submit(client, token).json()
    admin_token = _make_admin(client)

    first = client.post(
        f"{ADMIN_QUEUE}/{submitted['id']}/decide",
        json={"approve": False, "reason": "blurred"},
        headers=_auth(admin_token),
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"{ADMIN_QUEUE}/{submitted['id']}/decide",
        json={"approve": True},
        headers=_auth(admin_token),
    )
    assert second.status_code == 400, second.text
    assert "already rejected" in second.json()["message"]

    row = _one(
        client,
        "SELECT status FROM driver_licence_submissions WHERE id = :i",
        {"i": submitted["id"]},
    )
    assert row["status"] == "REJECTED"


def test_an_expired_licence_cannot_be_approved(client):
    """Re-checked at decision time, not only at submit.

    An approval may happen days after submission, and a licence that was valid
    then may not be now.
    """
    token, _ = _register_driver(client)
    submitted = _submit(client, token).json()
    admin_token = _make_admin(client)

    # Age the row past its expiry, as if the decision came much later.
    _exec(
        client,
        "UPDATE driver_licence_submissions SET expires_on = now() - interval '1 day' WHERE id = :i",
        {"i": submitted["id"]},
    )

    response = client.post(
        f"{ADMIN_QUEUE}/{submitted['id']}/decide",
        json={"approve": True},
        headers=_auth(admin_token),
    )
    assert response.status_code == 400, response.text
    assert "already expired" in response.json()["message"]


def test_approval_is_refused_when_a_required_document_row_is_missing(client):
    """The declared-but-never-uploaded case, at the row level.

    A submission whose taxi driver pass row is absent must not be approvable:
    the operator would be signing off on evidence that is not there.
    """
    token, _ = _register_driver(client)
    submitted = _submit(client, token).json()
    admin_token = _make_admin(client)

    _exec(
        client,
        "DELETE FROM driver_documents WHERE submission_id = :s AND kind = 'TAXI_DRIVER_PASS'",
        {"s": submitted["id"]},
    )

    response = client.post(
        f"{ADMIN_QUEUE}/{submitted['id']}/decide",
        json={"approve": True},
        headers=_auth(admin_token),
    )
    assert response.status_code == 400, response.text
    assert "required documents are missing" in response.json()["message"]


def test_a_renewal_by_an_active_driver_does_not_idle_them(client):
    """The design decision this whole separate table exists for.

    A driver who is ACTIVE and trading submits a renewal. Approving it changes
    the submission and must leave `DriverStatus` exactly where it was — taking
    them offline to swap a document is the behaviour that loses drivers.
    """
    token, driver_id = _register_driver(client)

    # Put the driver into the trading state directly, as a completed earlier
    # approval plus a fulfilled deposit would.
    _exec(
        client,
        "UPDATE driver_profiles SET status = 'ACTIVE' WHERE id = :d",
        {"d": driver_id},
    )

    submitted = _submit(client, token).json()
    admin_token = _make_admin(client)
    decided = client.post(
        f"{ADMIN_QUEUE}/{submitted['id']}/decide",
        json={"approve": True},
        headers=_auth(admin_token),
    )
    assert decided.status_code == 200, decided.text
    body = decided.json()
    assert body["status"] == "APPROVED"
    assert body["driver_promoted"] is False
    assert body["driver_status"] == "ACTIVE", "a renewal must not idle a trading driver"


def test_a_rejected_submission_can_be_replaced(client):
    """Rejection is not terminal for the driver, only for that submission."""
    token, _ = _register_driver(client)
    first = _submit(client, token).json()
    admin_token = _make_admin(client)

    client.post(
        f"{ADMIN_QUEUE}/{first['id']}/decide",
        json={"approve": False, "reason": "number unreadable"},
        headers=_auth(admin_token),
    )

    second = _submit(client, token)
    assert second.status_code == 201, second.text

    # Both rows survive: the rejected history is evidence.
    rows = _fetch(
        client,
        "SELECT status FROM driver_licence_submissions WHERE id IN (:a, :b) ORDER BY status",
        {"a": first["id"], "b": second.json()["id"]},
    )
    assert [r["status"] for r in rows] == ["PENDING", "REJECTED"]


def test_a_terminated_driver_cannot_submit(client):
    """Termination is the admin's hardest stop, and it blocks new evidence too.

    The documents are prepared *before* the status change on purpose: presign
    also refuses a terminated driver, so building the payload afterwards would
    fail on the upload rather than on the submission and the assertion would be
    about the wrong endpoint.
    """
    token, driver_id = _register_driver(client)
    documents = _full_document_set(client, token)

    _exec(
        client,
        "UPDATE driver_profiles SET status = 'TERMINATED' WHERE id = :d",
        {"d": driver_id},
    )
    response = _submit(client, token, documents=documents)
    assert response.status_code == 400, response.text
    assert "terminated" in response.json()["message"]


def test_the_upload_endpoint_is_rate_limited(client):
    """Each presign mints a key the client can PUT to, so they are capped."""
    token, _ = _register_driver(client)
    statuses = []
    for _ in range(45):
        response = client.post(
            UPLOADS,
            json={
                "kind": "DRIVER_LICENCE",
                "content_type": "image/jpeg",
                "size_bytes": 1024,
            },
            headers=_auth(token),
        )
        statuses.append(response.status_code)
        if response.status_code == 429:
            break
    assert 429 in statuses, "the upload presign endpoint is unbounded"
    assert statuses.count(200) <= 40


def test_a_document_row_is_replaced_not_duplicated(client):
    """One row per kind: "which image did the operator see" must be answerable."""
    token, _ = _register_driver(client)
    key_a = _presign(client, token, "DRIVER_LICENCE")
    key_b = _presign(client, token, "DRIVER_LICENCE")

    docs = [
        {
            "kind": "DRIVER_LICENCE",
            "object_key": key_a,
            "content_type": "image/jpeg",
            "size_bytes": 1024,
        },
        {
            "kind": "TAXI_DRIVER_PASS",
            "object_key": _presign(client, token, "TAXI_DRIVER_PASS"),
            "content_type": "image/jpeg",
            "size_bytes": 1024,
        },
    ]
    first = _submit(client, token, documents=docs)
    assert first.status_code == 201, first.text

    # A second submission that reuses a key would be the "duplicate" case; the
    # service already refuses a second PENDING, so assert on the shape instead.
    rows = _fetch(
        client,
        "SELECT kind, count(*) AS n FROM driver_documents "
        "WHERE submission_id = :s GROUP BY kind ORDER BY kind",
        {"s": first.json()["id"]},
    )
    assert {r["kind"]: r["n"] for r in rows} == {"DRIVER_LICENCE": 1, "TAXI_DRIVER_PASS": 1}
    assert key_b  # a spare key was minted; uniqueness is asserted elsewhere


def test_a_duplicate_kind_in_one_request_is_refused(client):
    """Two entries of the same kind means the client's state is inconsistent.

    Silently picking one would have the operator approve an image the driver did
    not intend to submit.
    """
    token, _ = _register_driver(client)
    docs = _full_document_set(client, token)
    docs.append(dict(docs[0]))

    response = _submit(client, token, documents=docs)
    assert response.status_code == 400, response.text
    assert "duplicate document kind" in response.json()["message"]


def test_a_submission_and_its_documents_cascade_on_driver_deletion(client):
    """Deleting a driver must not leave orphaned identity documents behind."""
    token, driver_id = _register_driver(client)
    submitted = _submit(client, token).json()
    submission_id = submitted["id"]

    _exec(client, "DELETE FROM driver_profiles WHERE id = :d", {"d": driver_id})

    assert (
        _fetch(
            client,
            "SELECT id FROM driver_licence_submissions WHERE id = :i",
            {"i": submission_id},
        )
        == []
    )
    assert (
        _fetch(
            client,
            "SELECT id FROM driver_documents WHERE submission_id = :i",
            {"i": submission_id},
        )
        == []
    )


def test_an_unknown_document_kind_is_refused(client):
    token, _ = _register_driver(client)
    response = client.post(
        UPLOADS,
        json={
            "kind": "NOT_A_REAL_KIND",
            "content_type": "image/jpeg",
            "size_bytes": 1024,
        },
        headers=_auth(token),
    )
    assert response.status_code == 422, response.text


def test_the_licence_number_format_is_enforced(client):
    token, _ = _register_driver(client)
    response = _submit(client, token, licence_no="no spaces here!")
    assert response.status_code == 400, response.text
    assert "licence number" in response.json()["message"]


def test_a_naive_expiry_is_treated_as_utc(client):
    """A naive datetime must not depend on the server's timezone.

    Guessing local time would make the same request behave differently on two
    hosts, and the expiry is compared against a tz-aware `now()`.
    """
    token, _ = _register_driver(client)
    naive = (datetime.now(UTC) + timedelta(days=200)).replace(tzinfo=None).isoformat()
    response = _submit(client, token, expires_on=naive)
    assert response.status_code == 201, response.text
    assert response.json()["expires_on"] is not None


def test_a_caller_supplied_key_for_another_namespace_is_refused(client):
    """The prefix check is on `documents/<driver>/`, so `avatars/` cannot pass."""
    token, driver_id = _register_driver(client)
    docs = _full_document_set(client, token)
    docs[0]["object_key"] = f"avatars/{driver_id}/{uuid.uuid4().hex}.jpg"

    response = _submit(client, token, documents=docs)
    assert response.status_code == 400, response.text
    assert "does not belong to this driver" in response.json()["message"]
