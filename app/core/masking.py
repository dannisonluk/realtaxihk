"""PDPO masking helpers — raw identifiers never leave the API layer."""

from __future__ import annotations


def mask_phone(phone_e164: str) -> str:
    """+85291234567 -> +852****4567 (country prefix + last 4 visible)."""
    if len(phone_e164) < 8:
        return "*" * len(phone_e164)
    return f"{phone_e164[:-8]}****{phone_e164[-4:]}"


def mask_driver_id_last4(last4: str) -> str:
    return f"****{last4}"


def mask_email(email: str) -> str:
    """`dannison@example.com` -> `d******n@example.com`.

    The local part is masked and the domain is kept: the domain is what tells an
    operator which account they are looking at (and it is not the identifier),
    while the local part is the part that is guessable and would be the whole
    address on a short one. A one-character local part is masked entirely rather
    than left visible, since showing it would give away the entire local part.
    """
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    if len(local) <= 2:
        return f"{'*' * len(local)}@{domain}"
    return f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}@{domain}"
