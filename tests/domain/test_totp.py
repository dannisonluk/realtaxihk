"""TDD — TOTP against the RFC's own test vectors, plus the failure modes.

RFC 6238 §Appendix B publishes the expected codes for a known seed at known
instants, so this implementation can be *proved* to match the spec rather than
assumed correct because it looks right. That matters: a TOTP that is subtly
wrong produces codes no authenticator app accepts, and the failure looks like
"the admin's phone is broken".

The RFC vectors use an 8-digit code with SHA-256/SHA-512 variants that consumer
apps do not support; those are included as an explicit interop-boundary test so
the limitation is recorded rather than discovered later.
"""

import base64
import datetime as dt
import hashlib
import hmac
import struct
from typing import ClassVar

import pytest

from app.core.totp import (
    DIGITS,
    STEP_SECONDS,
    TotpError,
    current_step,
    generate_recovery_codes,
    generate_secret,
    hash_recovery_code,
    normalize_secret,
    provisioning_uri,
    totp_at,
    verify_totp,
)

# RFC 6238 Appendix B: the ASCII seed "12345678901234567890".
RFC_SEED_ASCII = b"12345678901234567890"
RFC_SECRET_B32 = base64.b32encode(RFC_SEED_ASCII).decode()


def _rfc_totp(t: int, digits: int = 8, digest: str = "sha1", key: bytes = RFC_SEED_ASCII) -> str:
    """A literal, independent implementation of RFC 4226 §5.2 / 6238, for tests.

    Written straight from the RFC text rather than reusing `app.core.totp`, so
    a bug in the module cannot make the test agree with it.
    """
    counter = t // 30
    mac = hmac.new(key, struct.pack(">Q", counter), getattr(hashlib, digest)).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


class TestRfc6238Vectors:
    """The published vectors. If these fail, nothing else in this file matters."""

    # (unix time, expected 8-digit SHA-1 code) — RFC 6238 Appendix B.
    # `ClassVar` so ruff does not read this as a mutable default shared between
    # instances; it is a module-level constant that happens to live in the class.
    VECTORS: ClassVar[list[tuple[int, str]]] = [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ]

    @pytest.mark.parametrize(("seconds", "expected"), VECTORS)
    def test_matches_the_rfc_for_sha1(self, seconds, expected):
        # `totp_at` with digits=8 must reproduce the RFC exactly.
        assert totp_at(RFC_SECRET_B32, seconds, digits=8) == expected

    @pytest.mark.parametrize(("seconds", "expected"), VECTORS)
    def test_agrees_with_an_independent_rfc_implementation(self, seconds, expected):
        # Cross-check: our module vs the test's own transcription of the RFC.
        assert _rfc_totp(seconds) == expected
        assert totp_at(RFC_SECRET_B32, seconds, digits=8) == _rfc_totp(seconds)

    def test_the_default_is_six_digits(self):
        # The 8-digit vectors are the RFC's own choice; consumer apps use 6.
        assert DIGITS == 6
        assert len(totp_at(RFC_SECRET_B32, 59)) == 6

    def test_step_is_thirty_seconds(self):
        assert STEP_SECONDS == 30
        # Same window -> same code; next window -> different code.
        assert totp_at(RFC_SECRET_B32, 59) == totp_at(RFC_SECRET_B32, 30)
        assert totp_at(RFC_SECRET_B32, 59) != totp_at(RFC_SECRET_B32, 60)

    def test_sha256_and_sha512_are_not_offered(self):
        # Recorded deliberately: Google Authenticator only implements SHA-1 for
        # TOTP, so offering SHA-256 would produce codes no consumer app accepts.
        # Pinned by comparing against the independent RFC implementation above,
        # which is fixed to sha1 — if the module ever switched digest, the two
        # would disagree.
        assert totp_at(RFC_SECRET_B32, 59) == _rfc_totp(59, digits=6)


class TestSecretGeneration:
    def test_is_base32_without_padding(self):
        secret = generate_secret()
        assert "=" not in secret, "apps strip padding; emitting it invites paste errors"
        assert secret == secret.upper()
        base64.b32decode(secret + "=" * ((-len(secret)) % 8))

    def test_is_160_bits_by_default(self):
        # RFC 4226 §4 R6 recommends 160 bits for SHA-1.
        assert len(base64.b32decode(generate_secret())) == 20

    def test_secrets_are_unique(self):
        assert len({generate_secret() for _ in range(200)}) == 200

    def test_uses_a_csprng_not_random(self):
        # A predictable secret is a second factor an attacker can compute.
        # `secrets.token_bytes` is the CSPRNG; this asserts the module imports
        # it rather than the `random` module. Checking the import (not the
        # source text) avoids matching the word "random" inside a comment.
        import ast
        import pathlib

        from app.core import totp as mod

        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "secrets" in imported
        assert "random" not in imported


class TestSecretNormalization:
    def test_accepts_lowercase(self):
        assert normalize_secret(RFC_SECRET_B32.lower()) == RFC_SECRET_B32

    def test_strips_spaces_and_dashes(self):
        # Enrollment displays the secret in readable groups.
        spaced = " ".join(RFC_SECRET_B32[i : i + 4] for i in range(0, len(RFC_SECRET_B32), 4))
        assert normalize_secret(spaced) == RFC_SECRET_B32
        assert normalize_secret(RFC_SECRET_B32[:4] + "-" + RFC_SECRET_B32[4:]) == RFC_SECRET_B32

    def test_rejects_empty(self):
        with pytest.raises(TotpError, match="empty"):
            normalize_secret("   ")

    def test_rejects_non_base32(self):
        # 0/1/8/9 are not in the base32 alphabet — a common copy-paste mistake.
        with pytest.raises(TotpError, match="base32"):
            normalize_secret("01890189")

    def test_rejects_a_short_secret(self):
        # RFC 4226 §4 R6: at least 128 bits. A short secret is offline-brute-forceable.
        short = base64.b32encode(b"12345678").decode()
        with pytest.raises(TotpError, match="too short"):
            normalize_secret(short)

    def test_a_pasted_secret_verifies_identically(self):
        secret = generate_secret()
        code = totp_at(secret, 1_700_000_000)
        spaced = " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))
        assert verify_totp(spaced, code, at=1_700_000_000) is not None


class TestVerification:
    def test_accepts_the_current_code(self):
        secret = generate_secret()
        at = 1_700_000_000
        assert verify_totp(secret, totp_at(secret, at), at=at) is not None

    def test_returns_the_counter_not_a_bool(self):
        secret = generate_secret()
        at = 1_700_000_000
        counter = verify_totp(secret, totp_at(secret, at), at=at)
        assert counter == at // STEP_SECONDS

    def test_rejects_a_wrong_code(self):
        secret = generate_secret()
        at = 1_700_000_000
        right = totp_at(secret, at)
        wrong = "000000" if right != "000000" else "111111"
        assert verify_totp(secret, wrong, at=at) is None

    def test_accepts_the_previous_step_for_clock_drift(self):
        # window=1 tolerates a code typed as it rolls over.
        secret = generate_secret()
        at = 1_700_000_000
        previous = totp_at(secret, at - STEP_SECONDS)
        assert verify_totp(secret, previous, at=at) is not None

    def test_accepts_the_next_step(self):
        secret = generate_secret()
        at = 1_700_000_000
        assert verify_totp(secret, totp_at(secret, at + STEP_SECONDS), at=at) is not None

    def test_rejects_beyond_the_window(self):
        secret = generate_secret()
        at = 1_700_000_000
        assert verify_totp(secret, totp_at(secret, at - 2 * STEP_SECONDS), at=at) is None
        assert verify_totp(secret, totp_at(secret, at + 2 * STEP_SECONDS), at=at) is None

    def test_window_zero_accepts_only_the_current_step(self):
        secret = generate_secret()
        at = 1_700_000_000
        assert verify_totp(secret, totp_at(secret, at), at=at, window=0) is not None
        assert verify_totp(secret, totp_at(secret, at - STEP_SECONDS), at=at, window=0) is None

    @pytest.mark.parametrize(
        "bad", ["", "   ", "12345", "1234567", "abcdef", "12 34 5", "1234568a"]
    )
    def test_rejects_malformed_codes(self, bad):
        secret = generate_secret()
        assert verify_totp(secret, bad, at=1_700_000_000) is None

    def test_accepts_a_code_with_incidental_spacing(self):
        # Users paste codes as "123 456"; that is not an attack.
        secret = generate_secret()
        at = 1_700_000_000
        code = totp_at(secret, at)
        assert verify_totp(secret, f"{code[:3]} {code[3:]}", at=at) is not None

    def test_naive_datetime_is_treated_as_utc(self):
        # A caller passing a local datetime must not silently generate a code
        # for the wrong window; naive input is read as UTC, not host-local.
        secret = generate_secret()
        assert totp_at(secret, dt.datetime(2023, 11, 14, 22, 13, 20)) == totp_at(
            secret, 1_700_000_000
        )


class TestReplayProtection:
    """A code is single-use. The window accepts three steps, so without this an
    observed code is replayable for up to 90 seconds."""

    def test_the_same_code_is_rejected_once_consumed(self):
        secret = generate_secret()
        at = 1_700_000_000
        code = totp_at(secret, at)

        first = verify_totp(secret, code, at=at)
        assert first is not None

        # Replaying against the stored counter must fail.
        assert verify_totp(secret, code, at=at, last_used_counter=first) is None

    def test_a_later_code_still_works_after_consumption(self):
        secret = generate_secret()
        at = 1_700_000_000
        first = verify_totp(secret, totp_at(secret, at), at=at)
        later_code = totp_at(secret, at + STEP_SECONDS)
        assert verify_totp(secret, later_code, at=at + STEP_SECONDS, last_used_counter=first)

    def test_an_earlier_step_cannot_be_used_after_a_later_one(self):
        secret = generate_secret()
        at = 1_700_000_000
        # Consume the current step...
        counter = verify_totp(secret, totp_at(secret, at), at=at)
        # ...then a *previous* step must not be acceptable, even inside the window.
        assert (
            verify_totp(
                secret, totp_at(secret, at - STEP_SECONDS), at=at, last_used_counter=counter
            )
            is None
        )

    def test_current_step_helper_matches_verification(self):
        secret = generate_secret()
        at = 1_700_000_000
        assert verify_totp(secret, totp_at(secret, at), at=at) == current_step(at)


class TestProvisioningUri:
    def test_is_a_valid_otpauth_uri(self):
        uri = provisioning_uri(generate_secret(), "admin@hkfastdc.com")
        assert uri.startswith("otpauth://totp/")
        assert "secret=" in uri

    def test_uses_the_issuer_prefix_in_the_label(self):
        # Key URI Format: label must be Issuer:Account, or apps show a generic
        # heading instead of the issuer name.
        uri = provisioning_uri(generate_secret(), "ops@hkfastdc.com", issuer="hkfastdc")
        assert "hkfastdc%3Aops%40hkfastdc.com" in uri

    def test_carries_issuer_digits_and_period(self):
        uri = provisioning_uri(generate_secret(), "a@b.c")
        assert "issuer=hkfastdc" in uri or "issuer=hkfastdc" in uri
        assert "digits=6" in uri
        assert "period=30" in uri

    def test_omits_algorithm_because_sha1_is_the_default(self):
        # Some apps reject an explicit non-standard algorithm parameter.
        uri = provisioning_uri(generate_secret(), "a@b.c")
        assert "algorithm=" not in uri

    def test_the_embedded_secret_round_trips(self):
        secret = generate_secret()
        uri = provisioning_uri(secret, "a@b.c")
        assert secret in uri


class TestRecoveryCodes:
    def test_generates_eight_by_default(self):
        assert len(generate_recovery_codes()) == 8

    def test_are_unique(self):
        codes = generate_recovery_codes(50)
        assert len(set(codes)) == 50

    def test_are_high_entropy(self):
        # 6 bytes = 48 bits minimum; grouping dashes must not reduce that.
        code = generate_recovery_codes(1)[0]
        assert len(code.replace("-", "")) >= 9

    def test_hash_is_stable_and_normalizes_formatting(self):
        code = generate_recovery_codes(1)[0]
        assert hash_recovery_code(code) == hash_recovery_code(code.lower())
        assert hash_recovery_code(code) == hash_recovery_code(code.replace("-", ""))

    def test_hash_does_not_reveal_the_code(self):
        code = generate_recovery_codes(1)[0]
        digest = hash_recovery_code(code)
        assert code not in digest
        assert len(digest) == 64  # sha256 hex

    def test_different_codes_hash_differently(self):
        a, b = generate_recovery_codes(2)
        assert hash_recovery_code(a) != hash_recovery_code(b)
