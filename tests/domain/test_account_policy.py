"""Account policy helpers: the two pure predicates the guards depend on.

Why these get their own file
----------------------------
`reviewer_expired` and `is_locked` are the only places a *time* decision is made
about an account, and both are called from inside a request guard — which is the
worst place to discover a `TypeError` from comparing an aware datetime to a naive
one. Postgres returns aware values; SQLite and some drivers do not, and the
normalisation branches below exist for that difference.

Those branches cannot be reached through the API on this stack, which is exactly
why they are tested here: "unreachable on Postgres today" is not the same as
"correct", and the alternative is a 500 on a login the day a driver changes.

The class hierarchy is pinned too. `AccountLocked` subclasses `AccountThrottled`
so that a caller which only knows about throttling keeps working, and the router
must therefore catch the *subclass* first — the order is a security decision (a
lockout answers 401, not 429) and it is invisible at the call site.
"""

from __future__ import annotations

import datetime as dt

from app.services.auth.account_service import (
    AccountAuthService,
    AccountLocked,
    AccountThrottled,
    normalize_login_email,
    reviewer_expired,
)


class _Row:
    """The two columns the predicates read, and nothing else.

    A stand-in rather than a real `User` because these functions are documented to
    read one field each, and building a row would drag in the mapper and a dozen
    NOT NULL columns none of the cases touch.
    """

    def __init__(self, **fields: object) -> None:
        self.reviewer_expires_at = fields.get("reviewer_expires_at")
        self.locked_until = fields.get("locked_until")


NOW = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.UTC)


# --------------------------------------------------------------------------- #
# reviewer_expired — "is this a reviewer, and has it lapsed" in one predicate
# --------------------------------------------------------------------------- #


def test_an_account_without_an_expiry_is_not_a_reviewer():
    """`reviewer_expires_at IS NOT NULL` **is** the reviewer marker.

    There is deliberately no separate boolean, so "this account expires" and
    "this account is a reviewer" cannot drift apart — and every ordinary account
    has to be unaffected by a check that runs on every authenticated request.
    """
    assert reviewer_expired(_Row(), now=NOW) is False


def test_a_future_expiry_is_still_valid():
    assert reviewer_expired(_Row(reviewer_expires_at=NOW + dt.timedelta(days=1)), now=NOW) is False


def test_a_past_expiry_has_lapsed():
    assert (
        reviewer_expired(_Row(reviewer_expires_at=NOW - dt.timedelta(seconds=1)), now=NOW) is True
    )


def test_the_boundary_itself_counts_as_expired():
    """`<=`, not `<`. The expiry is the moment the account stops working, so an
    account expiring *now* must already be refused — the other direction leaves a
    window of exactly zero width but a window nonetheless."""
    assert reviewer_expired(_Row(reviewer_expires_at=NOW), now=NOW) is True


def test_a_naive_expiry_is_read_as_utc():
    """The branch a non-Postgres driver would hit.

    Comparing a naive datetime to an aware one raises `TypeError`, and the
    failure would surface as a 500 on whichever request happened to be first —
    not as a comparison bug. Assuming UTC is also the only choice that makes two
    hosts in different zones agree about the same row.
    """
    naive_past = dt.datetime(2026, 6, 1, 11, 59)
    naive_future = dt.datetime(2026, 6, 1, 12, 1)
    assert reviewer_expired(_Row(reviewer_expires_at=naive_past), now=NOW) is True
    assert reviewer_expired(_Row(reviewer_expires_at=naive_future), now=NOW) is False


# --------------------------------------------------------------------------- #
# is_locked — the brute-force window, on the row rather than in Redis
# --------------------------------------------------------------------------- #


def test_an_account_that_has_never_failed_is_not_locked():
    assert AccountAuthService.is_locked(_Row(), now=NOW) is False


def test_a_lockout_in_the_future_holds():
    assert (
        AccountAuthService.is_locked(_Row(locked_until=NOW + dt.timedelta(minutes=1)), now=NOW)
        is True
    )


def test_a_lockout_in_the_past_has_cleared_itself():
    """Self-clearing by design: a brute-force lock must not need an admin to
    unban it, which is the whole reason it is separate from `is_active`."""
    assert (
        AccountAuthService.is_locked(_Row(locked_until=NOW - dt.timedelta(seconds=1)), now=NOW)
        is False
    )


def test_the_lockout_boundary_is_exclusive():
    """`>`, not `>=`. At exactly the expiry instant the window has passed, so the
    account is usable again — the opposite convention from `reviewer_expired`,
    and both are deliberate."""
    assert AccountAuthService.is_locked(_Row(locked_until=NOW), now=NOW) is False


def test_a_naive_lockout_is_read_as_utc():
    assert (
        AccountAuthService.is_locked(_Row(locked_until=dt.datetime(2026, 6, 1, 12, 1)), now=NOW)
        is True
    )


# --------------------------------------------------------------------------- #
# The exception hierarchy, and the mapping it exists for
# --------------------------------------------------------------------------- #


def test_a_lockout_is_a_kind_of_throttle():
    """So a caller that only knows about `AccountThrottled` keeps working.

    The subclass relationship is the reason the router must order its `except`
    clauses carefully: catching `AccountThrottled` first would turn every lockout
    into a 429, which tells an attacker the account exists and has been guessed
    at enough to trip the lock.
    """
    assert issubclass(AccountLocked, AccountThrottled)


def test_the_router_maps_the_lockout_before_the_throttle():
    """The ordering, asserted on the source of the handler.

    A behavioural test would need to arrange a lockout and check the status, and
    `tests/api/test_auth_api.py` does exactly that — but it would still pass if
    someone later reordered the clauses *and* changed the expected status, which
    is the change this is here to make visible. Reading the order out of the
    function is the only way to assert it directly.
    """
    import inspect

    from app.api import auth

    source = inspect.getsource(auth._run)
    assert source.index("except AccountLocked") < source.index("except AccountThrottled"), (
        "AccountLocked must be caught before AccountThrottled, or a lockout becomes a 429"
    )


# --------------------------------------------------------------------------- #
# normalize_login_email
# --------------------------------------------------------------------------- #


def test_login_lookup_folds_case_and_whitespace():
    """Lookup-only, and deliberately different from the *storage* rule.

    `normalize_email` lower-cases only the domain, on the RFC 5321 §2.3.11
    argument that the local part is case-sensitive — the right rule for delivery
    and the wrong one for sign-in, since every consumer mail service treats
    `Dan.Nison@` and `dan.nison@` as one mailbox.
    """
    assert normalize_login_email("  Dan.Nison@Example.HK ") == "dan.nison@example.hk"


def test_an_empty_login_email_stays_empty():
    """`find_by_email` short-circuits on this rather than running a query that
    would match every row with a NULL email."""
    assert normalize_login_email("") == ""
    assert normalize_login_email("   ") == ""
