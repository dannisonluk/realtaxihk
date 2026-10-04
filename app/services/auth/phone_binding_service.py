"""Binding and proving a phone number on an account that already exists.

This is the call車 unlock. Signing in no longer requires a proven number;
*starting a booking* does. The two halves are

    POST /identity/phone/request   send a code to a number, refusing one that
                                   somebody else has already verified
    POST /identity/phone/confirm   prove the code and attach the number

and the split is not cosmetic. The send is the half with a cost attached — a
billed WhatsApp message — and the half that needs its refusal *before* the
message goes out, because sending a code to a number the caller can never own is
both a nuisance to that number's owner and a free way to make the platform pay
for messages.

Why `confirm` re-derives the number instead of trusting pending state
--------------------------------------------------------------------
`confirm` takes the number from the same request body rather than from a
"pending binding" record in Redis. That is what makes the code prove the exact
number being bound — a code issued for A cannot attach B — with no second piece
of state to keep in sync, expire, or lose to a Redis flush. The OTP row is
already keyed by number, so the binding is implicit in the code and there is
nothing extra that can drift.

Why the uniqueness check is not the authority
---------------------------------------------
`_assert_unclaimed` gives a sentence instead of a 500, but the guarantee is
`uq_users_phone_e164_verified` — a partial unique index on verified rows only.
Two accounts proving the same number at the same instant both pass the check and
one of them loses the `flush()`, which is why that `IntegrityError` is caught
and translated rather than allowed to surface. Same division of labour as
`uq_refund_pending_per_driver` in `app/models/user.py`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError
from app.core.phone import is_hk_phone
from app.models import User
from app.services.auth.identity_service import promote_if_ready
from app.services.auth.otp_service import OtpService
from app.services.auth.phone_reverify_service import next_deadline

logger = logging.getLogger("realtaxihk.phone_binding")


def _now() -> datetime:
    return datetime.now(UTC)


class PhoneBindingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def request(self, user: User, phone_e164: str) -> dict:
        """Send a code to `phone_e164` for `user` to prove.

        Refuses a number that is already verified on a *different* account
        before sending anything. That order matters: the check has to come
        first, or the platform pays for a message whose only possible outcome is
        a refusal at the next step.
        """
        self._assert_number(phone_e164)
        await self._assert_unclaimed(phone_e164, user)
        return await OtpService(self.session).request_otp(phone_e164)

    async def confirm(self, user: User, phone_e164: str, code: str) -> User:
        """Prove `code` and attach `phone_e164` to `user`.

        Doubles as the P-4 re-verification when the number is the one already on
        the account: the deadline is pushed out either way, because proving the
        number is proving the number. `/identity/phone/reverify` remains as a
        narrower endpoint for the case where the account is overdue and the
        client only wants to clear the block.
        """
        self._assert_number(phone_e164)
        await self._assert_unclaimed(phone_e164, user)

        # Proves the code was issued for *this* number and consumed it, so the
        # assignment below cannot be riding on a code sent somewhere else.
        await OtpService(self.session).consume_code(phone_e164, code)

        user.phone_e164 = phone_e164
        user.phone_verified_at = _now()
        # P-4: proving the number starts the monthly clock. Without this the
        # account would verify and immediately be reported as overdue, because
        # a NULL deadline is treated as due (fail-closed).
        user.phone_reverify_due_at = next_deadline()
        promote_if_ready(user)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise BusinessRuleError(
                "that number was verified by another account a moment ago"
            ) from exc

        logger.info("phone bound user_id=%s", user.id)
        return user

    @staticmethod
    def _assert_number(phone_e164: str) -> None:
        if not is_hk_phone(phone_e164):
            raise BusinessRuleError("phone must be a Hong Kong number in E.164 form (+852XXXXXXXX)")

    async def _assert_unclaimed(self, phone_e164: str, user: User) -> None:
        owner = (
            await self.session.execute(
                select(User.id).where(
                    User.phone_e164 == phone_e164,
                    User.phone_verified_at.is_not(None),
                    User.id != user.id,
                )
            )
        ).scalar_one_or_none()
        if owner is not None:
            raise BusinessRuleError("that number is already verified on another account")
