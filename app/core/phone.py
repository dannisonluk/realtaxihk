r"""Hong Kong phone numbers, in the one form this system stores them.

`+852` followed by exactly eight digits — E.164, no spaces, no dashes. That
shape was previously written out as a literal regex in three places
(`app/api/auth.py` twice, `app/api/identity.py` once) *and* in
`app/services/auth/otp_service.py`, which is four chances for one of them to
drift. A number that the API accepted but the OTP service rejected would have
produced a 400 from a service call rather than a 422 from request validation —
a worse error message for the same mistake.

The pattern is deliberately **not** narrowed to mobile prefixes (5/6/9). Nothing
in this system depends on the prefix, the reviewer account deliberately lives in
a reserved range outside them, and a future landline or pager path would have to
come back here and relax it. `\d{8}` is the honest description of what is
enforced.
"""

from __future__ import annotations

import re

# `Field(pattern=...)` needs the string; the compiled form is for the services,
# which validate values that did not come through a Pydantic model.
HK_PHONE_PATTERN = r"^\+852\d{8}$"
HK_PHONE_RE = re.compile(HK_PHONE_PATTERN)

#: The eight digits without the country code — what a person would recognise as
#: "their number", used in messages and in the reserved-range check.
HK_PHONE_DIGITS = 8


def is_hk_phone(value: str | None) -> bool:
    """True for a value this system will accept as a Hong Kong number."""
    return bool(value) and HK_PHONE_RE.fullmatch(value) is not None
