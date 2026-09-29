"""PDPO masking helpers — raw identifiers never leave the API layer."""

from __future__ import annotations


def mask_phone(phone_e164: str) -> str:
    """+85291234567 -> +852****4567 (country prefix + last 4 visible)."""
    if len(phone_e164) < 8:
        return "*" * len(phone_e164)
    return f"{phone_e164[:-8]}****{phone_e164[-4:]}"


def mask_driver_id_last4(last4: str) -> str:
    return f"****{last4}"
