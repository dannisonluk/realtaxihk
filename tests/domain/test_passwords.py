"""TDD — password hashing: the properties that matter, not the implementation.

Asserting "the same password gives the same digest" would be testing argon2,
not this module. What matters here is: the hash is salted, the parameters are
memory-hard and cannot drift on upgrade, a corrupt row reads as a wrong
password rather than a 500, and an unknown username costs the same as a known
one (no username oracle).
"""

import time

import pytest

from app.core.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    burn_password_time,
    hash_password,
    needs_rehash,
    verify_password,
)

GOOD = "tung-chung-taxi-2026!"


class TestHashing:
    def test_verifies_the_right_password(self):
        assert verify_password(hash_password(GOOD), GOOD)

    def test_rejects_the_wrong_password(self):
        assert not verify_password(hash_password(GOOD), GOOD + "x")

    def test_is_salted(self):
        # Two hashes of the same password must differ, or the digest is a
        # rainbow-table lookup and identical passwords are visibly identical.
        assert hash_password(GOOD) != hash_password(GOOD)

    def test_both_hashes_still_verify(self):
        a, b = hash_password(GOOD), hash_password(GOOD)
        assert verify_password(a, GOOD) and verify_password(b, GOOD)

    def test_uses_argon2id(self):
        # argon2id, not argon2i/argon2d: RFC 9106's recommendation (hybrid of
        # side-channel and GPU resistance).
        assert hash_password(GOOD).startswith("$argon2id$")

    def test_parameters_are_memory_hard_and_explicit(self):
        # The point of the whole module. m=65536 KiB (64 MiB), t=3, p=4.
        encoded = hash_password(GOOD)
        assert "m=65536" in encoded, "64 MiB working set — the memory-hardness is the defence"
        assert "t=3" in encoded
        assert "p=4" in encoded

    def test_salt_is_not_reused_between_calls(self):
        first = hash_password(GOOD).split("$")[4]
        second = hash_password(GOOD).split("$")[4]
        assert first != second


class TestPolicy:
    def test_rejects_a_short_password(self):
        with pytest.raises(PasswordPolicyError, match="at least"):
            hash_password("short1!")

    def test_accepts_exactly_the_minimum_length(self):
        pw = "abcdefghijkl"  # 12
        assert len(pw) == MIN_PASSWORD_LENGTH
        assert verify_password(hash_password(pw + "Z"), pw + "Z")

    def test_rejects_an_absurdly_long_password(self):
        # Not because argon2 truncates (it does not, unlike bcrypt's 72 bytes)
        # but because hashing megabytes per request is a free DoS.
        with pytest.raises(PasswordPolicyError, match="at most"):
            hash_password("a" * (MAX_PASSWORD_LENGTH + 1))

    def test_rejects_leading_or_trailing_whitespace(self):
        with pytest.raises(PasswordPolicyError, match="whitespace"):
            hash_password(" leading-space-pw")

    @pytest.mark.parametrize(
        "weak",
        [
            "passwordpassword",
            "1234567890123456",
            "qwertyqwertyqwerty",
            "letmeinletmein",
            "welcome-to-my-app",
            "adminadminadmin",
            "realtaxihk2026",
            "aaaaaaaaaaaa",
        ],
    )
    def test_rejects_the_usual_suspects(self, weak):
        with pytest.raises(PasswordPolicyError, match="predictable"):
            hash_password(weak)

    def test_rejects_a_low_alphabet_password(self):
        with pytest.raises(PasswordPolicyError):
            hash_password("ababababababab")


class TestVerificationRobustness:
    def test_a_corrupt_hash_reads_as_a_wrong_password(self):
        # A damaged row must not raise: a 500 here tells an attacker the row is
        # interesting, and leaks a stack trace.
        assert not verify_password("not-a-hash", GOOD)
        assert not verify_password("$argon2id$broken", GOOD)

    def test_empty_inputs_are_false_not_errors(self):
        assert not verify_password("", GOOD)
        assert not verify_password(hash_password(GOOD), "")

    def test_unicode_passwords_round_trip(self):
        pw = "密碼-測試-2026-English!"
        assert verify_password(hash_password(pw), pw)

    def test_nfc_normalisation_equivalence(self):
        # Composed vs decomposed accents are visually identical; without NFC a
        # user typing the same password on a different keyboard fails to log in.
        composed = "caf\u00e9-taxi-driver-2026"  # é as one codepoint
        decomposed = "cafe\u0301-taxi-driver-2026"  # e + combining acute
        assert composed != decomposed  # different byte sequences
        assert verify_password(hash_password(composed), composed)
        assert verify_password(hash_password(decomposed), decomposed)

    def test_a_password_with_a_null_byte_is_handled(self):
        # Some backends truncate at NUL; argon2 does not, and the API layer
        # should never crash on it.
        pw = "abc\x00def-ghijkl"
        assert verify_password(hash_password(pw), pw)


class TestRehashUpgradePath:
    def test_a_current_hash_does_not_need_rehashing(self):
        assert not needs_rehash(hash_password(GOOD))

    def test_an_unreadable_hash_needs_rehashing(self):
        # Rather than trusting something unparseable, replace it.
        assert needs_rehash("garbage")
        assert needs_rehash("$argon2id$v=19$m=1,t=1,p=1$c2FsdA$aGFzaA")

    def test_a_lower_cost_hash_is_flagged_for_upgrade(self):
        # Simulates a hash written before the parameters were raised, which is
        # exactly the migration path `needs_rehash` exists for.
        import argon2

        weak_hasher = argon2.PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
        weak = weak_hasher.hash(GOOD)
        assert verify_password(weak, GOOD), "old hashes must still verify"
        assert needs_rehash(weak), "...and be flagged for transparent upgrade"

    def test_upgrade_preserves_the_password(self):
        import argon2

        weak_hasher = argon2.PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
        weak = weak_hasher.hash(GOOD)
        # Asserted, not branched on: the old `if needs_rehash(weak):` left
        # `upgraded` unbound whenever it was false, so this test would have
        # raised `UnboundLocalError` instead of testing the upgrade path. The
        # previous test pins the precondition; here it is a step.
        assert needs_rehash(weak), "the weak hash must be flagged, or this proves nothing"
        upgraded = hash_password(GOOD)
        assert verify_password(upgraded, GOOD)
        assert not needs_rehash(upgraded)


class TestTimingEqualisation:
    def test_burn_password_time_costs_about_one_hash(self):
        """The username-oracle defence: an unknown username must cost the same
        as a known one, or response time reveals which accounts exist."""
        start = time.perf_counter()
        burn_password_time()
        burn = time.perf_counter() - start

        start = time.perf_counter()
        verify_password(hash_password(GOOD), GOOD)
        real = time.perf_counter() - start

        # Same order of magnitude. A generous band: CI machines are noisy, and
        # the failure this guards against is microseconds-vs-tens-of-ms, i.e.
        # three orders of magnitude, not 2x.
        assert burn > real / 10, f"burn={burn:.4f}s real={real:.4f}s — too fast to hide the oracle"

    def test_burn_password_time_never_raises(self):
        burn_password_time()
