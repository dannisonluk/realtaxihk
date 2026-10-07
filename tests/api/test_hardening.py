"""Regression suite for the MVP -> production hardening wave.

docs/PRODUCTION_READINESS.md: B1-B4 + P0/P1/P2 items each get an executable
check here. Live DB/Redis (realtaxi-db/realtaxi-redis containers) required.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings


def _mk_user_token(client: TestClient, phone: str) -> str:
    """A fully verified, ACTIVE account's token.

    Was a bare OTP login, which no longer reaches any business route: P-2 gates
    on `AccountStatus.ACTIVE` and P-4 layers a phone deadline on top. This module
    is not about those gates, so it clears them and moves on — they are covered
    by `test_identity_api` and `test_phone_reverify`.
    """
    return client.activate(phone)


def _admin_headers(client) -> dict:
    return client.admin_headers()


_ORDER = {
    "pickup_lat": 22.284,
    "pickup_lng": 114.158,
    "dropoff_lat": 22.315,
    "dropoff_lng": 114.219,
    "pickup_address": "Statue Square, Central",
    "dropoff_address": "Harbour North, North Point",
    "distance_km": "4.2",
    "taxi_type": "URBAN",
}


def _register_driver(client: TestClient, token: str, phone: str) -> str:
    r = client.post(
        "/api/v1/drivers/register",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "hk_id_last4": phone[-4:],
            "taxi_driver_plate_no": f"TD{phone[-5:]}",
            "vehicle_reg_mark": f"V{phone[-4:]}",
            "taxi_type": "URBAN",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestB1AuthMeDeletedUser:
    def test_me_404_not_500(self, client):
        """B1: /auth/me with a valid JWT for a missing user -> guarded, no NameError.

        A 404, not a 403: the token is well-formed and its signature is good, so
        the honest answer is "that account does not exist" rather than "you may
        not do this". `require_live_principal` raises it, so the check still
        happens before the handler body — the property under test is that
        nothing reaches an unguarded `session.get(...).phone_e164`.
        """
        from app.core.security import create_access_token

        ghost = create_access_token({"sub": str(uuid.uuid4()), "role": "PASSENGER"})
        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {ghost}"})
        assert r.status_code == 404
        assert r.json()["code"] != "INTERNAL_ERROR"


class TestB2LedgerConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_grants_no_lost_update(self, client):
        """B2/P0-1: 10 parallel grants -> every cent lands, chain consistent."""
        phone = f"+85251{uuid.uuid4().int % 1000000:06d}"
        token = _mk_user_token(client, phone)
        did = _register_driver(client, token, phone)
        h = _admin_headers(client)
        client.post(f"/api/v1/admin/drivers/{did}/review", headers=h, json={"decision": "approve"})

        factory = client.db_factory

        # seed the deposit row once (serially) so the race is on the append path
        from app.models import DriverProfile
        from app.services.ledger.ledger_service import LedgerService

        async with factory() as s0:
            from sqlalchemy import select as _select

            dp = (
                (await s0.execute(_select(DriverProfile).where(DriverProfile.id == did)))
                .scalars()
                .first()
            )
            await LedgerService.ensure_deposit_row(s0, dp)
            await s0.commit()

        async def grant(i):
            async with factory() as s:
                from decimal import Decimal

                from app.models import LedgerEntryType
                from app.services.ledger.ledger_service import LedgerService

                await LedgerService(s).append(
                    did,
                    LedgerEntryType.DEPOSIT_TOPUP,
                    Decimal("10"),
                    note=f"parallel-{i}",
                )
                await s.commit()

        await asyncio.gather(*[grant(i) for i in range(10)])

        r = client.get("/api/v1/drivers/me/ledger", headers={"Authorization": f"Bearer {token}"})
        items = r.json()["items"]
        balances = [float(i["balance_after_hkd"]) for i in items]
        assert len(items) == 10
        assert balances[-1] == 100.0
        for prev, cur in zip([0.0, *balances[:-1]], balances, strict=True):
            assert abs((cur - prev) - 10.0) < 1e-9


class TestB3TipCap:
    def test_fare_tip_overflow_rejected(self, client):
        """B3: tip > 500 -> 422, not a DB overflow 500."""
        r = client.post(
            "/api/v1/fare/estimate",
            json={
                "taxi_type": "URBAN",
                "distance_km": "5",
                "tip": "1000000",
            },
        )
        assert r.status_code == 422


class TestP0ConfigFailFast:
    def test_prod_with_dev_secret_raises(self):
        """P0-2: prod + dev JWT secret must fail at startup."""
        with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
            Settings(
                app_env="prod",
                jwt_secret_key="dev-only-secret-change-in-prod-0123456789abcdef",
            )

    def test_prod_with_dev_password_raises(self):
        with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
            Settings(
                app_env="prod",
                jwt_secret_key="x" * 64,
                postgres_password="change-me-dev",
            )


class TestP0Deactivation:
    @pytest.mark.asyncio
    async def test_disabled_user_loses_access(self, client):
        """P0-3: is_active=False -> guarded endpoints 403 immediately."""
        phone = f"+85252{uuid.uuid4().int % 1000000:06d}"
        token = _mk_user_token(client, phone)
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        user_id = me.json()["id"]

        from sqlalchemy import update

        from app.models import User

        async with client.db_factory() as s:
            await s.execute(update(User).where(User.id == user_id).values(is_active=False))
            await s.commit()

        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403
        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    def test_admin_requires_real_row(self, client):
        """P0-3: ADMIN claim without a real active admin row -> 403.

        Two flavours now, because there are two ways the claim can be hollow: an
        id that exists nowhere at all, and an id that names a `users` row whose
        role happens to be ADMIN. The second is the legacy console identity, and
        it must not open `/api/v1/admin/*` — only an `admin_accounts` row with a
        proven second factor does.
        """
        from app.core.security import create_access_token

        token = create_access_token({"sub": str(uuid.uuid4()), "role": "ADMIN"})
        r = client.get(
            "/api/v1/admin/drivers",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

        legacy_id = uuid.uuid4()
        client.exec_sql(
            "INSERT INTO users "
            "(id, phone_e164, role, is_active, account_status, created_at, updated_at) "
            "VALUES (:id, '+85281000999', 'ADMIN', true, 'ACTIVE', now(), now())",
            {"id": legacy_id},
        )
        legacy = create_access_token({"sub": str(legacy_id), "role": "ADMIN"})
        r = client.get(
            "/api/v1/admin/drivers",
            headers={"Authorization": f"Bearer {legacy}"},
        )
        assert r.status_code == 403, r.text


class TestP0Health:
    def test_health_reports_checks(self, client):
        """P0-4: /health pings DB and Redis for real."""
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["checks"] == {"db": True, "redis": True}

    def test_health_returns_503_when_db_down(self, client, monkeypatch):
        """P0-4: a failing dependency must answer 503, not 200.

        The docs promise "503 on failure" and a load balancer / readiness probe
        keys off the STATUS CODE, not the body — a 200 here keeps routing
        traffic to a node whose database is gone.
        """
        import app.core.db as db

        def _boom():
            raise RuntimeError("db unreachable")

        monkeypatch.setattr(db, "get_session_factory", _boom)
        r = client.get("/health")
        assert r.status_code == 503
        assert r.json()["checks"]["db"] is False
        assert r.json()["status"] == "degraded"


class TestLifespanBackgroundJobs:
    def test_jobs_start_on_boot_and_stop_on_shutdown(self):
        """The lifespan must actually start every background loop.

        Nothing asserted this before, so a refactor that dropped the startup
        hook would silently disable ghost-order sweeping and data purges while
        every other test stayed green.

        Asserting the job *names* rather than a count: the original `== 2` broke
        the moment weekly settlement was added, which is a false alarm, whereas
        a missing job is a real one.
        """
        from fastapi.testclient import TestClient

        from app.main import create_app

        app = create_app()
        with TestClient(app):
            jobs = app.state.jobs
            assert sorted(j.get_name() for j in jobs) == [
                "geo_sweep",
                "pdpo_purge",
                "prebook_broadcaster",
                "recurring_mint",
                "weekly_settlement",
            ]
            assert all(not j.done() for j in jobs)
        # Shutdown must cancel them — leaked tasks keep the loop alive.
        assert all(j.done() for j in jobs)


class TestP1AuthRotation:
    def test_refresh_rotates_and_old_token_dies(self, client):
        """P1-5: refresh rotation - old token single-use.

        `client.otp_login` rather than a bare request/verify: an OTP login is a
        *secondary* login now, so it refuses a number no account has already
        proven. The helper arranges that and returns the whole session body.
        """
        phone = f"+85253{uuid.uuid4().int % 1000000:06d}"
        body = client.otp_login(phone)
        assert body["refresh_token"]
        old_refresh = body["refresh_token"]

        r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
        assert r1.status_code == 200
        r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
        assert r2.status_code == 401  # replayed -> dead

    def test_logout_revokes_all(self, client):
        phone = f"+85254{uuid.uuid4().int % 1000000:06d}"
        token = _mk_user_token(client, phone)
        h = {"Authorization": f"Bearer {token}"}
        r = client.post("/api/v1/auth/logout", headers=h)
        assert r.json()["ok"] is True


class TestP1OtpLimits:
    def test_otp_request_ip_rate_limited(self, client):
        """P1-2: > otp_ip_rate_limit requests from one IP -> 429."""
        ip_limit = 10
        last = None
        for i in range(ip_limit + 2):
            last = client.post(
                "/api/v1/auth/otp/request",
                json={"phone_e164": f"+852600000{i:02d}"},
                headers={"X-Forwarded-For": "203.0.113.77"},
            )
        assert last is not None, "the loop body always assigns a response"
        assert last.status_code == 429


class TestP1OrderTollsInSnapshot:
    def test_order_snapshot_includes_tunnel_toll(self, client):
        """P1-10: tunnels no longer silently dropped from the fare snapshot."""
        phone = f"+85257{uuid.uuid4().int % 1000000:06d}"
        token = _mk_user_token(client, phone)
        payload = dict(_ORDER, crosses_harbour=True, tunnels=["cross_harbour"])
        r = client.post(
            "/api/v1/orders", headers={"Authorization": f"Bearer {token}"}, json=payload
        )
        assert r.status_code == 201, r.text
        codes = [s["code"] for s in r.json()["fare"]["surcharges"]]
        assert "tunnel_cross_harbour" in codes  # toll captured, not dropped
        assert r.json()["fare"]["tunnels"] == ["cross_harbour"]


class TestP2OrderHistoryAndDetail:
    def test_history_keyset_pagination(self, client):
        phone = f"+85258{uuid.uuid4().int % 1000000:06d}"
        token = _mk_user_token(client, phone)
        h = {"Authorization": f"Bearer {token}"}
        first = client.post("/api/v1/orders", headers=h, json=_ORDER).json()
        second = client.post("/api/v1/orders", headers=h, json=_ORDER).json()
        r = client.get("/api/v1/orders?limit=1", headers=h)
        items = r.json()["items"]
        assert len(items) == 1
        r2 = client.get(f"/api/v1/orders?limit=10&before_id={items[0]['id']}", headers=h)
        ids = [o["id"] for o in r2.json()["items"]]
        assert first["id"] in ids or second["id"] in ids

    def test_history_keyset_survives_a_shared_timestamp(self, client):
        """The cursor is a *composite* key, and the tie-breaker must reach SQL.

        The list is ordered `created_at DESC, id DESC`, so `before_id` has to
        compare on both columns. It did not. The predicate was written as a
        Python tuple comparison —

            (Order.created_at, Order.id) < (anchor[0], anchor[1])

        — and Python's tuple `<` compares element-wise, testing equality first.
        `bool(Order.created_at == ts)` is `False` for a SQLAlchemy column (it does
        not raise), so the comparison falls straight through to
        `created_at < ts` and the `id` tie-breaker never reaches the query. The
        anchor's `Order.id` was selected and then dropped.

        That is invisible until two orders share a `created_at`, which is not
        hypothetical: `created_at` is `server_default=func.now()` and Postgres's
        `now()` is the *transaction* timestamp, so every row written in one
        transaction carries the identical value. This test forces the collision
        instead of hoping for one. Before the fix, page two comes back empty and
        the older order is unreachable — pagination silently loses a row.
        """
        phone = f"+85257{uuid.uuid4().int % 1000000:06d}"
        token = _mk_user_token(client, phone)
        h = {"Authorization": f"Bearer {token}"}
        first = client.post("/api/v1/orders", headers=h, json=_ORDER).json()
        second = client.post("/api/v1/orders", headers=h, json=_ORDER).json()

        # Force both onto one timestamp: exactly what `func.now()` produces when
        # two rows are written inside a single transaction.
        client.exec_sql(
            "UPDATE orders SET created_at = timestamptz '2026-01-01 00:00:00+08' "
            "WHERE id IN (:a, :b)",
            {"a": first["id"], "b": second["id"]},
        )

        page1 = client.get("/api/v1/orders?limit=1", headers=h).json()["items"]
        assert len(page1) == 1, "expected the newest order on the first page"
        page2 = client.get(f"/api/v1/orders?limit=10&before_id={page1[0]['id']}", headers=h).json()[
            "items"
        ]

        seen = {page1[0]["id"], *(o["id"] for o in page2)}
        assert seen == {first["id"], second["id"]}, (
            "keyset pagination dropped an order that shares the cursor's timestamp: "
            f"page 1 gave {page1[0]['id']}, page 2 gave {[o['id'] for o in page2]}"
        )

    def test_detail_forbidden_for_stranger(self, client):
        owner = _mk_user_token(client, f"+85259{uuid.uuid4().int % 1000000:06d}")
        oid = client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {owner}"},
            json=_ORDER,
        ).json()["id"]
        stranger = _mk_user_token(client, f"+85259{(uuid.uuid4().int + 1) % 1000000:06d}")
        r = client.get(
            f"/api/v1/orders/{oid}",
            headers={"Authorization": f"Bearer {stranger}"},
        )
        assert r.status_code == 403
