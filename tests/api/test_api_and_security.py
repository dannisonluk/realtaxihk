"""TDD Cycle 3 — RED: FastAPI app shell, error format, fare endpoint, JWT."""

from fastapi.testclient import TestClient


class TestHealth:
    def test_health_ok(self, client: TestClient):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestFareEndpoint:
    def test_estimate_with_discount(self, client: TestClient):
        r = client.post(
            "/api/v1/fare/estimate",
            json={
                "taxi_type": "URBAN",
                "distance_km": "10",
                "waiting_min": "5",
                "tunnels": ["cross_harbour"],
                "crosses_harbour": True,
                "discount_percent": "15",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["meter_fare"] == "116.5"
        assert body["meter_after_discount"] == "99.0"
        assert body["surcharges_total"] == "50.0"
        assert body["total_fare"] == "149.0"
        assert body["is_estimate"] is True
        assert "僅供參考" in body["disclaimer_zh"]
        assert "2024-07-14" in body["tariff_version"]

    def test_business_rule_error_format(self, client: TestClient):
        # crosses_harbour without cross-harbour tunnel -> service ValueError
        r = client.post(
            "/api/v1/fare/estimate",
            json={"taxi_type": "URBAN", "distance_km": "5", "crosses_harbour": True},
        )
        assert r.status_code == 400
        body = r.json()
        assert body["code"] == "BUSINESS_RULE_VIOLATION"
        assert "message" in body and "details" in body

    def test_validation_error_format(self, client: TestClient):
        r = client.post(
            "/api/v1/fare/estimate",
            json={"taxi_type": "URBAN", "distance_km": "-1"},
        )
        assert r.status_code == 422
        body = r.json()
        assert body["code"] == "VALIDATION_ERROR"
        assert isinstance(body["details"], dict)

    def test_unknown_route_standard_error(self, client: TestClient):
        r = client.get("/api/v1/definitely-not-a-route")
        assert r.status_code == 404
        body = r.json()
        assert body["code"] == "NOT_FOUND"


class TestSecurity:
    def test_token_roundtrip(self):
        from app.core.security import create_access_token, decode_access_token

        token = create_access_token({"sub": "user-1", "role": "passenger"})
        payload = decode_access_token(token)
        assert payload["sub"] == "user-1"
        assert payload["role"] == "passenger"

    def test_expired_token_rejected(self):
        import pytest
        from jwt import ExpiredSignatureError

        from app.core.security import create_access_token, decode_access_token

        token = create_access_token({"sub": "user-1"}, expires_minutes=-1)
        with pytest.raises(ExpiredSignatureError):
            decode_access_token(token)

    def test_garbage_token_rejected(self):
        import pytest
        from jwt import InvalidTokenError

        from app.core.security import decode_access_token

        with pytest.raises(InvalidTokenError):
            decode_access_token("not.a.token")
