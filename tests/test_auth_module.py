"""TDD — Module A: OTP auth, KYC state machine, masking, JWT deps."""

import re

import pytest
from conftest import ADMIN_ID

from app.core.security import create_access_token


class TestMasking:
    def test_mask_phone(self):
        from app.core.masking import mask_phone

        assert mask_phone("+85291234567") == "+852****4567"

    def test_mask_phone_keeps_prefix(self):
        from app.core.masking import mask_phone

        assert mask_phone("+85291234567").startswith("+852")


class TestOtpService:
    async def test_request_and_verify_roundtrip(self, db_session, otp_inbox):
        from app.services.otp_service import OtpService

        svc = OtpService(db_session)
        result = await svc.request_otp("+85291234567")
        assert result["sent"] is True
        # SEC-02: the code is never echoed in the response — not even in dev.
        # The suite reads it at the notify seam, where the app actually sends it.
        assert "dev_code" not in result
        code = otp_inbox["+85291234567"]
        assert re.fullmatch(r"\d{6}", code)

        auth = await svc.verify_otp("+85291234567", code)
        assert auth.user.phone_e164 == "+85291234567"
        assert auth.created is True
        assert auth.user.role.value == "PASSENGER"

    async def test_wrong_code_rejected_and_attempts_counted(self, db_session, otp_inbox):
        from app.core.exceptions import BusinessRuleError
        from app.services.otp_service import OtpService

        svc = OtpService(db_session)
        await svc.request_otp("+85291234567")
        for _ in range(5):
            with pytest.raises(BusinessRuleError):
                await svc.verify_otp("+85291234567", "000000")
        # after max attempts the code is dead even if correct
        with pytest.raises(BusinessRuleError):
            await svc.verify_otp("+85291234567", otp_inbox["+85291234567"])

    async def test_expired_code_rejected(self, db_session, otp_inbox):
        from app.core.exceptions import BusinessRuleError
        from app.services.otp_service import OtpService

        svc = OtpService(db_session)
        await svc.request_otp("+85291234567", ttl_seconds=-1)
        with pytest.raises(BusinessRuleError):
            await svc.verify_otp("+85291234567", otp_inbox["+85291234567"])

    async def test_invalid_phone_format_rejected(self, db_session):
        from app.services.otp_service import OtpService

        svc = OtpService(db_session)
        with pytest.raises(ValueError):
            await svc.request_otp("91234567")  # missing +852
        with pytest.raises(ValueError):
            await svc.request_otp("+852123456789")  # 9 digits

    async def test_resend_cooldown(self, db_session):
        from app.core.exceptions import BusinessRuleError
        from app.services.otp_service import OtpService

        svc = OtpService(db_session)
        await svc.request_otp("+85291234567")
        with pytest.raises(BusinessRuleError):
            await svc.request_otp("+85291234567")


class TestAuthApi:
    def test_request_verify_me_flow(self, client):
        r = client.post("/api/v1/auth/otp/request", json={"phone_e164": "+85291234567"})
        assert r.status_code == 200
        # SEC-02: no code in the response, in any environment.
        assert "dev_code" not in r.json()

        r = client.post(
            "/api/v1/auth/otp/verify",
            json={"phone_e164": "+85291234567", "code": client.otp_inbox["+85291234567"]},
        )
        assert r.status_code == 200
        token = r.json()["access_token"]
        assert r.json()["user"]["phone_masked"] == "+852****4567"
        # raw phone never leaks
        assert "91234567" not in r.text.replace("+852****4567", "")

        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["phone_masked"] == "+852****4567"

    def test_verify_with_bad_code_400(self, client):
        client.post("/api/v1/auth/otp/request", json={"phone_e164": "+85291234567"})
        r = client.post(
            "/api/v1/auth/otp/verify",
            json={"phone_e164": "+85291234567", "code": "000000"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "BUSINESS_RULE_VIOLATION"

    def test_me_requires_token(self, client):
        r = client.get("/api/v1/auth/me")
        assert r.status_code == 401
        assert r.json()["code"] == "UNAUTHORIZED"


class TestDriverKycApi:
    def _new_user_token(self, client, phone: str) -> str:
        client.post("/api/v1/auth/otp/request", json={"phone_e164": phone})
        r = client.post(
            "/api/v1/auth/otp/verify",
            json={"phone_e164": phone, "code": client.otp_inbox[phone]},
        )
        return r.json()["access_token"]

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

        admin_token = create_access_token({"sub": ADMIN_ID, "role": "ADMIN"})
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
        assert r.json()["deposit"]["required_hkd"] == "500.0"

    def test_admin_required(self, client):
        token = self._new_user_token(client, "+85291230003")
        r = client.get("/api/v1/admin/drivers", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403
