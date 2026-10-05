"""TDD — Module A: OTP auth, KYC state machine, masking, JWT deps.

`TestOtpService` covers the OTP service as it now is: a way to prove a phone
number, and a **secondary** login for a number an account has already proven. It
is no longer a registration path — see `test_auth_api.py` for the email+password
account model that replaced it.
"""

import json
import re

import pytest


class TestMasking:
    def test_mask_phone(self):
        from app.core.masking import mask_phone

        assert mask_phone("+85291234567") == "+852****4567"

    def test_mask_phone_keeps_prefix(self):
        from app.core.masking import mask_phone

        assert mask_phone("+85291234567").startswith("+852")


PHONE = "+85291234567"


async def _mk_verified_user(session, phone: str = PHONE):
    """An account that has *proven* `phone`.

    `verify_otp` only signs in a number some account has already verified, so
    every test about the secondary login needs this row to exist. Built with the
    ORM rather than through the API so `TestOtpService` stays about the service.
    """
    from datetime import UTC, datetime

    from app.models import User, UserRole

    user = User(phone_e164=phone, role=UserRole.PASSENGER, phone_verified_at=datetime.now(UTC))
    session.add(user)
    await session.flush()
    return user


class TestOtpService:
    async def test_a_code_round_trips_for_an_already_verified_number(self, db_session, otp_inbox):
        from app.services.auth.otp_service import OtpService

        await _mk_verified_user(db_session)
        svc = OtpService(db_session)
        result = await svc.request_otp(PHONE)
        assert result["sent"] is True
        # SEC-02: the code is never echoed in the response — not even in dev.
        # The suite reads it at the notify seam, where the app actually sends it.
        assert "dev_code" not in result
        code = otp_inbox[PHONE]
        assert re.fullmatch(r"\d{6}", code)

        auth = await svc.verify_otp(PHONE, code)
        assert auth.user.phone_e164 == PHONE
        # An OTP can no longer create an account, so this is now always False
        # rather than "did this verify register somebody".
        assert auth.created is False
        assert auth.user.role.value == "PASSENGER"

    async def test_a_code_cannot_sign_in_an_account_that_only_claims_the_number(
        self, db_session, otp_inbox
    ):
        """The property that makes keeping an OTP login defensible at all.

        Registration records the number as a *claim*. If `verify_otp` matched on
        the claim, then whoever could receive a code for a number would become
        whichever account had typed that number at sign-up — so a stolen code
        would reach an account the SIM never owned.
        """
        from app.core.exceptions import BusinessRuleError
        from app.models import User, UserRole
        from app.services.auth.otp_service import OtpService

        db_session.add(User(phone_e164=PHONE, role=UserRole.PASSENGER))  # claimed, unproven
        await db_session.flush()

        svc = OtpService(db_session)
        await svc.request_otp(PHONE)
        with pytest.raises(BusinessRuleError, match="no account has verified"):
            await svc.verify_otp(PHONE, otp_inbox[PHONE])

    async def test_wrong_code_rejected_and_attempts_counted(self, db_session, otp_inbox):
        from app.core.exceptions import BusinessRuleError
        from app.services.auth.otp_service import OtpService

        svc = OtpService(db_session)
        await svc.request_otp(PHONE)
        for _ in range(5):
            with pytest.raises(BusinessRuleError):
                await svc.verify_otp(PHONE, "000000")
        # after max attempts the code is dead even if correct
        with pytest.raises(BusinessRuleError):
            await svc.verify_otp(PHONE, otp_inbox[PHONE])

    async def test_expired_code_rejected(self, db_session, otp_inbox):
        from app.core.exceptions import BusinessRuleError
        from app.services.auth.otp_service import OtpService

        svc = OtpService(db_session)
        await svc.request_otp(PHONE, ttl_seconds=-1)
        with pytest.raises(BusinessRuleError):
            await svc.verify_otp(PHONE, otp_inbox[PHONE])

    async def test_invalid_phone_format_rejected(self, db_session):
        from app.services.auth.otp_service import OtpService

        svc = OtpService(db_session)
        with pytest.raises(ValueError):
            await svc.request_otp("91234567")  # missing +852
        with pytest.raises(ValueError):
            await svc.request_otp("+852123456789")  # 9 digits

    async def test_resend_cooldown(self, db_session):
        from app.core.exceptions import BusinessRuleError
        from app.services.auth.otp_service import OtpService

        svc = OtpService(db_session)
        await svc.request_otp(PHONE)
        with pytest.raises(BusinessRuleError):
            await svc.request_otp(PHONE)


class TestAuthApi:
    def test_request_verify_me_flow(self, client):
        """The secondary login end to end, plus PDPO masking on the way out."""
        body = client.otp_login(PHONE)
        token = body["access_token"]
        assert body["user"]["phone_masked"] == "+852****4567"
        # raw phone never leaks
        assert "91234567" not in json.dumps(body).replace("+852****4567", "")
        # An OTP never registers, so the flag is a definite False, not absent.
        assert body["created"] is False

        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["phone_masked"] == "+852****4567"

    def test_otp_request_never_echoes_the_code(self, client):
        """SEC-02, asserted on the raw response rather than through a helper."""
        r = client.post("/api/v1/auth/otp/request", json={"phone_e164": PHONE})
        assert r.status_code == 200
        assert "dev_code" not in r.json()

    def test_verify_with_bad_code_400(self, client):
        client.post("/api/v1/auth/otp/request", json={"phone_e164": PHONE})
        r = client.post(
            "/api/v1/auth/otp/verify",
            json={"phone_e164": PHONE, "code": "000000"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "BUSINESS_RULE_VIOLATION"

    def test_me_requires_token(self, client):
        r = client.get("/api/v1/auth/me")
        assert r.status_code == 401
        assert r.json()["code"] == "UNAUTHORIZED"


class TestDriverKycApi:
    def _new_user_token(self, client, phone: str) -> str:
        """A fully verified, ACTIVE account's token.

        Was a bare OTP login, which no longer reaches driver registration:
        `require_phone_verified` is the gate that unlocks business routes, and
        P-4 layers a phone deadline on top (`require_phone_current`). These tests
        are about the KYC flow that follows registration, so they clear those
        gates first — the gates themselves are covered by `test_identity_api`
        and `test_phone_reverify`.
        """
        return client.activate(phone)

    def test_register_driver_pending_kyc(self, client):
        token = self._new_user_token(client, "+85291230001")
        r = client.post(
            "/api/v1/drivers/register",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "hk_id_last4": "1234",
                "taxi_driver_plate_no": "TD12345",
                "vehicle_reg_mark": "AB1234",
                "taxi_type": "URBAN",
            },
        )
        assert r.status_code == 201
        assert r.json()["status"] == "PENDING_KYC"
        assert r.json()["taxi_driver_plate_no"] == "TD12345"

    def test_admin_review_approve_flow(self, client):

        admin = client.admin_headers()
        admin_token = admin["Authorization"].split(" ", 1)[1]
        token = self._new_user_token(client, "+85291230002")
        r = client.post(
            "/api/v1/drivers/register",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "hk_id_last4": "5678",
                "taxi_driver_plate_no": "TD22222",
                "vehicle_reg_mark": "CD5678",
                "taxi_type": "URBAN",
            },
        )
        driver_id = r.json()["id"]

        r = client.get(
            "/api/v1/admin/drivers?status=PENDING_KYC",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 200
        assert any(d["id"] == driver_id for d in r.json()["items"])

        r = client.post(
            f"/api/v1/admin/drivers/{driver_id}/review",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"decision": "approve", "note": "docs ok"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "DEPOSIT_REQUIRED"

        # driver cannot become ACTIVE without deposit
        r = client.get("/api/v1/drivers/me", headers={"Authorization": f"Bearer {token}"})
        assert r.json()["status"] == "DEPOSIT_REQUIRED"
        assert r.json()["deposit"]["required_hkd"] == "500.00"

    def test_admin_restore_flow(self, client):
        """SUSPENDED -> ACTIVE is a different door from `approve`.

        `approve` maps to DEPOSIT_REQUIRED; sending it to a suspended driver is
        an illegal transition (the old console did exactly that). `restore` is
        the decision that takes a suspended operator back online without
        re-running KYC or demanding a fresh deposit.
        """
        admin = client.admin_headers()
        admin_token = admin["Authorization"].split(" ", 1)[1]
        token = self._new_user_token(client, "+85291230009")
        r = client.post(
            "/api/v1/drivers/register",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "hk_id_last4": "9999",
                "taxi_driver_plate_no": "TD99999",
                "vehicle_reg_mark": "ZZ9999",
                "taxi_type": "URBAN",
            },
        )
        driver_id = r.json()["id"]

        # KYC approve -> DEPOSIT_REQUIRED, then deposit grant -> ACTIVE,
        # then suspend -> SUSPENDED.
        r = client.post(
            f"/api/v1/admin/drivers/{driver_id}/review",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"decision": "approve", "note": "docs ok"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "DEPOSIT_REQUIRED"

        grant = client.post(
            f"/api/v1/admin/drivers/{driver_id}/deposit/grant",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"amount_hkd": "500.00", "note": "deposit"},
        )
        assert grant.status_code == 200, grant.text

        r = client.post(
            f"/api/v1/admin/drivers/{driver_id}/review",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"decision": "suspend", "note": "flow"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SUSPENDED"

        # The old broken call is refused, not silently remapped.
        r = client.post(
            f"/api/v1/admin/drivers/{driver_id}/review",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"decision": "approve", "note": "wrong"},
        )
        assert r.status_code in (400, 422), r.text

        # Restore returns the driver to ACTIVE.
        r = client.post(
            f"/api/v1/admin/drivers/{driver_id}/review",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"decision": "restore", "note": "resolved"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACTIVE"

    def test_admin_required(self, client):
        token = self._new_user_token(client, "+85291230003")
        r = client.get("/api/v1/admin/drivers", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403
