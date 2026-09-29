"""JWT (HS256) helpers. Secret MUST be supplied via env — there is no default."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import get_settings


def create_access_token(
    claims: dict[str, Any],
    expires_minutes: int | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + timedelta(
        minutes=settings.access_token_expire_minutes if expires_minutes is None else expires_minutes
    )
    # `iat` drives the revocation-epoch check (SEC-18); `jti` gives every token a
    # stable identity for audit logs and future deny-lists.
    payload = {"iat": now, "exp": expire, "jti": uuid.uuid4().hex, **claims}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    # The algorithm list is pinned to the configured algorithm, which rules out
    # the `alg=none` / HS-vs-RS confusion family.
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
