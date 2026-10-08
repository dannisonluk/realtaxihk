# syntax=docker/dockerfile:1
# SEC-21: build from the lockfile, not from `pip install .`. `pyproject.toml` only
# carries `>=` floors, so the previous build could resolve a different — and
# unreviewed — version of any dependency on every rebuild, straight into prod.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Layer cache: dependencies first (they change rarely), project code second.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini alembic ./alembic
COPY app ./app
RUN uv sync --frozen --no-dev

ENV PATH="/srv/.venv/bin:$PATH"

EXPOSE 8000
# SEC-31: `--no-proxy-headers` is NOT cosmetic. uvicorn's ProxyHeadersMiddleware
# is ON by default with `forwarded_allow_ips=127.0.0.1`, and it overwrites
# `scope["client"]` from the client-supplied X-Forwarded-For. Any caller whose
# TCP peer is 127.0.0.1 could therefore pick its own apparent IP and walk
# straight through every IP rate limit — defeating SEC-07 below the app layer.
# With it off, `request.client.host` is always the real peer, and the app's own
# right-anchored `_client_ip()` + TRUSTED_PROXY_COUNT is the ONE place trust is
# decided. Verified: with this flag the XFF bucket disappears from Redis.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log"]
