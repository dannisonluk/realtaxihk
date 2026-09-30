"""Password hashing and policy.

argon2id via `argon2-cffi`. The choice is not free: **the KDF must be
memory-hard**. A GPU or ASIC farm cracks bcrypt/PBKDF2 at a rate that makes a
fast hash a rounding error, whereas argon2id's 64 MiB working set per hash
makes parallel hardware expensive — which is the whole point of a password
hash in 2026.

argon2id specifically (not argon2i or argon2d) is the hybrid: `i` resists
side-channel attacks, `d` resists GPU/ASIC, and `id` uses the data-independent
first pass then data-dependent later passes. It is the RFC 9106 recommendation
and the OWASP first choice.

The parameters below are argon2-cffi's defaults, stated explicitly rather than
inherited so an upgrade cannot silently change them: m=64 MiB, t=3, p=4. OWASP
minimums are m=19 MiB / t=2 / p=1, so this is comfortably above.

**`verify` upgrades the hash on login.** `check_needs_rehash` + `ph.hash` means
the parameter cost can be raised later and every active user is silently
migrated as they log in — no forced reset, no flag day.

**Timing.** A failed login for a *nonexistent* username still has to do the
work, or the response time tells an attacker which usernames exist. `verify`
against a dummy hash (below) is how the API layer equalises that.
"""

from __future__ import annotations

import contextlib
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Explicit, not inherited defaults — an upgrade must not change the cost.
_TIME_COST = 3
_MEMORY_COST_KIB = 64 * 1024  # 64 MiB
_PARALLELISM = 4
_HASH_LEN = 32
_SALT_LEN = 16

_hasher = PasswordHasher(
    time_cost=_TIME_COST,
    memory_cost=_MEMORY_COST_KIB,
    parallelism=_PARALLELISM,
    hash_len=_HASH_LEN,
    salt_len=_SALT_LEN,
)

# A password policy that is defensible without being hostile.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256  # argon2 has no truncation, but an unbounded input is a DoS

# Unused by the hasher; exists so a login against an unknown username performs
# the same work as a real one (see `verify_password` usage in the API layer).
_DUMMY_HASH = _hasher.hash("dummy-password-for-constant-time-comparison")


class PasswordPolicyError(ValueError):
    """The password does not meet policy."""


def hash_password(password: str) -> str:
    """Hash a password for storage. Returns the full encoded argon2 string.

    The encoded form carries the algorithm, parameters and salt, so parameters
    can change per-row and `check_needs_rehash` can detect it — a hash that
    stores only the digest cannot be upgraded in place.
    """
    _assert_policy(password)
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """True if `password` matches. Never raises on a mismatch.

    Also returns False for a malformed stored hash rather than propagating:
    a corrupted row must read as "wrong password", not as a 500 that tells an
    attacker the row is interesting.
    """
    if not stored_hash or not password:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the stored hash predates the current parameters.

    Call on successful login: `verify` then `needs_rehash` then re-hash is how
    the cost is raised over time without a password reset.
    """
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, VerificationError):
        # An unreadable hash should be replaced, not trusted.
        return True


def burn_password_time() -> None:
    """Spend one hash's worth of time on a login that cannot succeed.

    Called when the username does not exist. Without it, "unknown user" returns
    in microseconds while a real user takes ~50 ms, and that difference is a
    username oracle — measurable over the network, no tooling required.
    """
    # `suppress`, not `try/except/pass`: the exceptions are the expected
    # outcome here, not an error path, and ruff flags the bare-pass form as
    # something a reviewer has to re-read to confirm nothing was swallowed.
    with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
        _hasher.verify(_DUMMY_HASH, "not-the-password")


def _assert_policy(password: str) -> None:
    if not isinstance(password, str):
        raise PasswordPolicyError("password must be a string")
    # NFC first: without normalisation, two visually identical passwords can
    # hash differently, so a user who types the same password on a different
    # keyboard (composed vs decomposed accents) cannot log in.
    normalized = unicodedata.normalize("NFC", password)
    if len(normalized) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(normalized) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"password must be at most {MAX_PASSWORD_LENGTH} characters")
    if normalized.strip() != normalized:
        # Leading/trailing whitespace is almost always a paste accident and a
        # support burden; rejecting is kinder than silently trimming.
        raise PasswordPolicyError("password must not begin or end with whitespace")
    if _is_obviously_weak(normalized):
        raise PasswordPolicyError("password is too predictable")
    return None


_WEAK_FRAGMENTS = (
    "password",
    "123456",
    "qwerty",
    "letmein",
    "welcome",
    "admin",
    "realtaxi",
    "iloveyou",
    "monkey",
    "dragon",
    "abc123",
)


def _is_obviously_weak(password: str) -> bool:
    """Reject the passwords that top every breach list.

    This is a floor, not a strength meter: length is enforced above, and the
    real defence against credential stuffing is rate limiting plus lockout
    (both at the API layer). Blocking a handful of universally-guessed strings
    is cheap and stops the worst one-shot attempts. A full dictionary check
    would need a wordlist dependency and is deliberately out of scope.
    """
    lowered = password.lower()
    if any(fragment in lowered for fragment in _WEAK_FRAGMENTS):
        return True
    # A single repeated character, or a straight run of digits.
    if len(set(lowered)) <= 3:
        return True
    return lowered in {"0123456789ab", "abcdefghijkl", "aaaaaaaaaaaa"}
