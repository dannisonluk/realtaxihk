"""Signed confirmation tokens for money-moving actions.

Why a token rather than just a "yes I am sure" checkbox
------------------------------------------------------
`POST /admin/settlement/weekly/run` charges every eligible driver. It is
idempotent per ISO week, which means a *first* accidental press is not a
mistake you get to undo — the money is gone and the reference is spent. So
"look at the preview first" has to be a structural constraint rather than a
line in a runbook, and the thing that makes it structural is that the run
requires a token only the preview can issue.

What the token binds
--------------------
The token covers the **period and the fee**, and nothing else. Those two are
what the charge actually depends on, so binding them means the token cannot be
issued for one week's numbers and spent on another's. A preview that said
"HK$200 across 40 drivers" and a run that then charged HK$500 would be exactly
the mismatch the preview exists to prevent, and a token that did not bind the
fee would permit it.

It is deliberately *not* bound to the admin who requested the preview. Two
super admins working the same incident should not have to hand a token back and
forth, and binding an actor adds no safety: the run re-checks the caller's role
and both actions are audited with their own actor. Binding the actor would also
make the common case — preview, then run after a coffee — fail intermittently.

What this does not do
---------------------
It does not make the run safe against a *malicious* admin who previews and then
runs. Nothing can: they have the authority to run it anyway. It makes an
accidental run impossible and a rushed one deliberate, which is the actual
failure mode.

TTL is short and stated in the response, so the console can show "this preview
expires in N minutes" rather than failing the operator with a bare 400.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError

__all__ = [
    "PREVIEW_TTL_SECONDS",
    "issue_confirm_token",
    "verify_confirm_token",
]

# Ten minutes: long enough to read a table and make a phone call, short enough
# that a token cannot sit in a browser tab and be spent the next morning against
# numbers that have since changed.
PREVIEW_TTL_SECONDS = 600

# A constant, domain-separating prefix. Without it a token minted for one
# purpose could verify against another that happens to serialise the same
# payload — the classic cross-protocol confusion, and cheap to avoid.
_PURPOSE = "settlement.confirm.v1"


def _sign(secret: str, body: str) -> str:
    return hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_confirm_token(**claims: Any) -> str:
    """Mint a short-lived token over `claims`. Returns `<body>.<signature>`.

    The body is base64url-free JSON deliberately: it is not a secret, and a
    readable body makes a support log line useful ("the token was for
    2026-W40 at 200") without needing the secret to interpret it. The signature
    is what makes it unforgeable, and it is verified before the body is trusted.
    """
    payload = {
        "purpose": _PURPOSE,
        "exp": int(time.time()) + PREVIEW_TTL_SECONDS,
        **claims,
    }
    # `sort_keys` so the encoding is deterministic: two identical previews must
    # produce the same body, or a test asserting stability would flake.
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{body}.{_sign(get_settings().jwt_secret_key, body)}"


def verify_confirm_token(token: str, **expected: Any) -> dict[str, Any]:
    """Verify a token, returning its claims. Raises `BusinessRuleError` if bad.

    Every failure is a `BusinessRuleError` (HTTP 400) with a `reason` the
    console can branch on. Deliberately not a 401: the caller is authenticated
    and authorised; what they hold is a stale or mismatched token, and telling
    them "your session expired" would send them to re-login for no reason.

    The signature is checked with `hmac.compare_digest`, not `==`. A string
    comparison short-circuits on the first differing byte, which leaks the
    correct prefix — and a leaked prefix is a forgeable token given enough
    attempts.
    """
    if not token or "." not in token:
        raise BusinessRuleError(
            "confirmation token is required", {"reason": "CONFIRM_TOKEN_MISSING"}
        )
    body, _, signature = token.rpartition(".")
    expected_sig = _sign(get_settings().jwt_secret_key, body)
    if not hmac.compare_digest(signature, expected_sig):
        raise BusinessRuleError(
            "confirmation token is not valid", {"reason": "CONFIRM_TOKEN_INVALID"}
        )

    try:
        claims = json.loads(body)
    except ValueError as exc:
        # Unreachable for a token we signed, but a hand-crafted body with a
        # valid signature means the secret leaked, so this must not 500.
        raise BusinessRuleError(
            "confirmation token is not readable", {"reason": "CONFIRM_TOKEN_INVALID"}
        ) from exc

    if claims.get("purpose") != _PURPOSE:
        raise BusinessRuleError(
            "confirmation token was issued for a different action",
            {"reason": "CONFIRM_TOKEN_WRONG_PURPOSE"},
        )
    if int(claims.get("exp", 0)) < int(time.time()):
        raise BusinessRuleError(
            "confirmation token has expired — preview again",
            {"reason": "CONFIRM_TOKEN_EXPIRED"},
        )
    for key, want in expected.items():
        if claims.get(key) != want:
            # Report both sides. The caller passed `fee_hkd=str(fee)` where
            # `fee` is an int, so `expected` would otherwise surface as `200`
            # in a message sitting next to a preview that showed `200.00` —
            # and the operator's next move is to compare the two by eye.
            raise BusinessRuleError(
                f"confirmation token does not match the requested {key}",
                {
                    "reason": "CONFIRM_TOKEN_MISMATCH",
                    "field": key,
                    "expected": str(want),
                    "token": str(claims.get(key)),
                },
            )
    return claims
