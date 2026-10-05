"""TOTP (RFC 6238) and its base32 secret encoding, implemented on the stdlib.

Why not `pyotp`: this is ~60 lines of `hmac`/`struct`, it sits directly on the
authentication path, and RFC 6238 publishes official test vectors — so the
implementation can be *proved* correct against the spec rather than trusted
because a library is popular. One fewer dependency on the login path is one
fewer supply-chain and upgrade surface, which matters more here than anywhere
else in the codebase.

It is interoperable with every standard authenticator app (Google
Authenticator, Microsoft Authenticator, Authy, 1Password, Bitwarden, FreeOTP,
Aegis): they all implement the same RFC, and the `otpauth://` URI this module
emits is the format they scan.

The three parameters that decide whether codes actually match:

- **SHA-1 is the HMAC, not a weakness here.** RFC 6238's interoperable default
  is HMAC-SHA1 and Google Authenticator only supports SHA-1 for TOTP. SHA-1's
  collision weaknesses are irrelevant to HMAC (which needs only PRF security).
  Using SHA-256 would produce codes no consumer app accepts.
- **6 digits, 30-second step** — the RFC default and what apps assume.
- **`valid_window=1`** accepts the previous and next step, tolerating clock
  drift and the moment a code rolls over mid-typing. It widens the brute-force
  window from 1-in-10^6 to 3-in-10^6 per attempt, which is why the verify
  endpoint *must* be rate-limited and single-use — see `AdminTotpService`.

`check` is constant-time (`hmac.compare_digest`): a naive `==` leaks how many
leading digits were right, which is enough to defeat a 6-digit space.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse
from datetime import UTC, datetime

# RFC 6238 defaults. Changing DIGITS or STEP breaks every enrolled app.
DIGITS = 6
STEP_SECONDS = 30
DEFAULT_WINDOW = 1

SECRET_BYTES = 20  # 160 bits — the RFC 4226 recommended key length for SHA-1


class TotpError(ValueError):
    """Malformed secret, or a code that is not a well-formed TOTP."""


def generate_secret(nbytes: int = SECRET_BYTES) -> str:
    """A fresh base32 secret, no padding — the form authenticator apps expect.

    `secrets.token_bytes` (CSPRNG), never `random`: a predictable TOTP secret is
    a second factor an attacker can compute.
    """
    return base64.b32encode(secrets.token_bytes(nbytes)).decode("ascii").rstrip("=")


def normalize_secret(secret: str) -> str:
    """Accept a secret however the user pasted it; return canonical base32.

    Enrollment shows the secret in groups of four for readability, and people
    paste it with spaces, dashes, or in lower case. Rejecting those would make
    manual entry fail for a reason that is not a security property.
    """
    cleaned = "".join(secret.split()).replace("-", "").upper()
    if not cleaned:
        raise TotpError("empty TOTP secret")
    # Re-pad base32 to a multiple of 8 (apps strip padding, so most secrets
    # arrive unpadded — without this, a 32-char secret decodes fine but a
    # 26-char one raises binascii.Error).
    padding = (-len(cleaned)) % 8
    try:
        raw = base64.b32decode(cleaned + "=" * padding, casefold=True)
    except Exception as exc:  # binascii.Error, ValueError
        raise TotpError("secret is not valid base32") from exc
    if len(raw) < 10:
        # RFC 4226 §4 R6: the key must be at least 128 bits, and recommends 160.
        # A short secret makes the whole second factor brute-forceable offline.
        raise TotpError("secret is too short to be secure")
    return cleaned


def _hotp(key: bytes, counter: int, digits: int = DIGITS, digest: str = "sha1") -> str:
    """RFC 4226 §5.2 — the HMAC one-time password a TOTP is built from."""
    # The counter is a 64-bit big-endian integer; `struct` enforces the width.
    msg = struct.pack(">Q", counter)
    mac = hmac.new(key, msg, getattr(hashlib, digest)).digest()
    # Dynamic truncation: the low nibble of the last byte picks a 4-byte window.
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def totp_at(secret: str, at: datetime | int | float | None = None, *, digits: int = DIGITS) -> str:
    """The code valid at a given instant. `at` may be a datetime, epoch, or None.

    Any non-UTC datetime is converted, so a caller passing a local `datetime`
    cannot silently generate a code for the wrong 30-second window.
    """
    key = _decode_key(secret)
    counter = _counter_for(at)
    return _hotp(key, counter, digits)


def verify_totp(
    secret: str,
    code: str,
    *,
    at: datetime | int | float | None = None,
    window: int = DEFAULT_WINDOW,
    digits: int = DIGITS,
    last_used_counter: int | None = None,
) -> int | None:
    """Verify `code`, returning the matched counter, or None.

    Returns the *counter* rather than a bool so the caller can implement
    replay protection: storing the last accepted counter and refusing anything
    `<=` it makes a code single-use. Without that, an attacker who observes a
    code (shoulder-surf, a compromised proxy log) can replay it for the rest of
    its 30-second life — a real gap, since the window also accepts the previous
    step, giving up to 90 seconds of replay.

    `last_used_counter` enforces that here as well as at the call site, so a
    caller cannot forget the check and still be safe.
    """
    candidate = (code or "").strip().replace(" ", "")
    if not candidate.isdigit() or len(candidate) != digits:
        return None

    key = _decode_key(secret)
    now = _counter_for(at)

    matched: int | None = None
    # Iterate the whole window without an early return: a timing difference
    # between "matched the first candidate" and "matched the last" is a
    # (minor) oracle. Constant work per attempt is cheap here.
    for offset in range(-window, window + 1):
        counter = now + offset
        if hmac.compare_digest(_hotp(key, counter, digits), candidate):
            matched = counter
    if matched is None:
        return None
    if last_used_counter is not None and matched <= last_used_counter:
        # A code at or before the last accepted step is a replay.
        return None
    return matched


def _decode_key(secret: str) -> bytes:
    normalized = normalize_secret(secret)
    padding = (-len(normalized)) % 8
    return base64.b32decode(normalized + "=" * padding, casefold=True)


def _counter_for(at: datetime | int | float | None) -> int:
    if at is None:
        epoch = time.time()
    elif isinstance(at, datetime):
        moment = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
        epoch = moment.timestamp()
    else:
        epoch = float(at)
    # Unix time is counted in 30-second steps from the Unix epoch.
    return int(epoch // STEP_SECONDS)


def current_step(at: datetime | int | float | None = None) -> int:
    """The step number now — what gets persisted as `last_used_counter`."""
    return _counter_for(at)


def provisioning_uri(
    secret: str, account: str, issuer: str = "hkfastdc", *, digits: int = DIGITS
) -> str:
    """The `otpauth://` URI an authenticator app scans.

    The label MUST be `Issuer:Account` and `issuer` MUST also be a parameter —
    the Key URI Format requires the prefix or apps show the account under a
    generic heading, and Google Authenticator uses the parameter for its own
    grouping. `algorithm` is omitted deliberately: SHA1 is the spec default and
    some apps reject an explicit value they consider non-standard.
    """
    label = urllib.parse.quote(f"{issuer}:{account}", safe="")
    params = {
        "secret": normalize_secret(secret),
        "issuer": issuer,
        "digits": str(digits),
        "period": str(STEP_SECONDS),
    }
    return f"otpauth://totp/{label}?" + urllib.parse.urlencode(params)


def generate_recovery_codes(count: int = 8, nbytes: int = 6) -> list[str]:
    """Single-use codes for when the authenticator device is lost.

    Without these, a lost phone means a locked-out administrator and a manual
    database edit — which is how a security control turns into an operational
    incident. Each is ~9 base32 chars (48 bits from 6 bytes, padded), high-entropy
    but typeable.
    """
    codes = []
    for _ in range(count):
        raw = base64.b32encode(secrets.token_bytes(nbytes)).decode("ascii").rstrip("=")
        # Grouped for transcription; `hash_recovery_code` strips the dashes.
        codes.append("-".join(raw[i : i + 5] for i in range(0, len(raw), 5)))
    return codes


def hash_recovery_code(code: str) -> str:
    """Hash a recovery code for storage.

    A plain SHA-256 (not argon2) is the right choice *here*: the code is 48 bits
    of CSPRNG output, so there is no dictionary to attack and no need to be
    slow — while verification has to happen on the login path. The pepper is
    unnecessary for the same reason. Codes are single-use and stored one-way.
    """
    cleaned = "".join(code.split()).replace("-", "").upper()
    return hashlib.sha256(cleaned.encode("ascii")).hexdigest()
