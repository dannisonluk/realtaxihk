"""Central config via pydantic-settings. .env supported; prod secrets MUST come from env.

Fail-closed by design (SEC-01~05):
- `app_env` has NO usable default. An unset/blank APP_ENV is a hard startup error,
  because the previous `app_env: str = "dev"` default silently turned a container
  with no `.env` (i.e. every Docker image — the Dockerfile never COPYs `.env`) into
  a dev-mode server that returned a fixed OTP `123456` in the response body.
- `app_env` is whitelisted, not compared by equality. The old check was
  `if self.app_env == "prod"`, so `production`, `PROD`, `staging` or `prod ` (trailing
  space) skipped every prod safety check and let the platform boot with the
  repo-committed JWT secret — forgeable ADMIN tokens, offline.
- `jwt_secret_key` has no default either; it must be supplied and must carry real
  entropy. A secret that is long but low-entropy (`"x" * 64`) is rejected.
- The dev OTP shortcut needs an explicit second switch (`ALLOW_DEV_OTP`) and is
  forced off in prod, so "which env am I in?" is never the only thing standing
  between the internet and a fixed verification code.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

VALID_APP_ENVS = ("dev", "test", "prod")

# Secrets we know are public (they live in git history / .env.example).
_KNOWN_DEV_SECRETS = frozenset(
    {
        "dev-only-secret-change-in-prod-0123456789abcdef-0123456789abcdef",
        "dev-only-secret-change-in-prod-0123456789abcdef",
    }
)
_MIN_SECRET_CHARS = 32
_MIN_SECRET_DISTINCT = 8


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # SEC-01: deliberately blank sentinel, not "dev". Blank -> startup error.
    app_env: str = ""
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    # Local dev origins for the admin console (`admin-web/`). Both spellings of
    # loopback are listed because a CORS origin is matched as a literal string:
    # `http://127.0.0.1:8081` and `http://localhost:8081` are different origins,
    # and listing only one makes the console fail every request with an opaque
    # browser error and nothing in the server log.
    #
    # 127.0.0.1 is the spelling used everywhere else in this file, deliberately —
    # see the Postgres note below — so the console's own instructions and its
    # serve script both use it.
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8081",
        "http://127.0.0.1:8081",
    ]
    log_level: str = "INFO"

    # 127.0.0.1, never "localhost". On Windows, `localhost` resolves to BOTH
    # ::1 and 127.0.0.1, and the Docker-published ports are IPv4-only: the
    # client tries ::1 first, the SYN is silently dropped (not refused) and the
    # connect only fails after a ~2s timeout before falling back to IPv4.
    # Measured on this machine: `localhost` -> 2034ms per connection,
    # `127.0.0.1` -> 1ms. Since a fresh connection is opened per request in
    # several code paths, that turned into ~4-6s per HTTP call.
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "realtaxi"
    postgres_password: str = "change-me-dev"
    postgres_db: str = "realtaxihk"

    redis_url: str = "redis://127.0.0.1:6379/0"

    # SEC-04: no default — must be provided and must have entropy.
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    # SEC-18: a 2-hour access token outlives any logout. 15 minutes bounds it.
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 14
    # SEC-17: replaying a rotated refresh token revokes the whole family.
    refresh_reuse_detection: bool = True
    # SEC-02: the fixed dev OTP needs BOTH a non-prod env and this explicit switch.
    allow_dev_otp: bool = False

    # --- Background jobs (P0-5 / P1-6) ---
    jobs_enabled: bool = True
    geo_sweep_interval_s: int = 300  # ghost-order sweep cadence
    max_broadcast_minutes: int = 30  # unmatched BROADCASTING auto-cancel age
    purge_interval_s: int = 86400  # PDPO purge cadence
    retention_days_otp: int = 30  # PDPO: keep OTP rows 30 days
    retention_days_refresh: int = 30  # PDPO: keep dead refresh tokens 30 days

    # --- Weekly settlement (P2-2: the platform's revenue model) ---
    weekly_settlement_enabled: bool = True
    weekly_fee_hkd: int = 200  # deducted from every ACTIVE driver, per week
    weekly_settlement_interval_s: int = 604800  # 7 days between settlement runs
    refund_min_hkd: int = 1  # balance below this is not worth a refund request

    # --- OTP / rate limiting (P1-2, SEC-07/08) ---
    otp_ip_rate_limit: int = 10  # requests per IP per window
    otp_ip_window_s: int = 600  # 10 minutes
    otp_phone_rate_limit: int = 5  # per phone number, per window
    otp_phone_rate_limit_strict: int = 1  # per phone, once the global soft cap trips
    otp_phone_window_s: int = 3600
    # SEC-08: this is a SOFT threshold. Crossing it raises an alert and tightens the
    # per-phone limit — it must never 429 every user of the platform at once.
    otp_global_hourly_limit: int = 500
    # Hard ceiling: only far past the soft cap do we shed load (503 + Retry-After).
    otp_global_hourly_hard_limit: int = 5000
    # SEC-07: number of TRUSTED proxy hops in front of the app (0 = direct/exposed).
    # X-Forwarded-For is only read when this is > 0, and the client address is taken
    # from the right, so a client-supplied prefix cannot forge its own source IP.
    trusted_proxy_count: int = 0

    # --- Request/DoS limits (SEC-09~11) ---
    max_request_body_bytes: int = 1_048_576  # 1 MiB
    fare_estimate_ip_rate_limit: int = 60  # per IP, per window
    fare_estimate_ip_window_s: int = 60
    driver_location_rate_limit: int = 120  # per driver, per window
    driver_location_window_s: int = 60

    # --- WebSocket (P1-9, SEC-14/16/30) ---
    ws_heartbeat_s: int = 30  # server ping cadence for idle-keepalive
    ws_max_connections_per_user: int = 5
    ws_max_connections_total: int = 2000
    ws_idle_timeout_s: int = 300  # no traffic in either direction -> reap
    # SEC-16: sustained inbound tick rate per connection, plus a burst allowance so
    # a reconnect that replays a few queued ticks is not throttled.
    ws_ticks_per_second: int = 2
    ws_tick_burst: int = 5

    # --- Security headers / ops (SEC-22, 24) ---
    security_headers_enabled: bool = True
    hsts_max_age_s: int = 31_536_000
    # Empty -> /metrics is NOT mounted at all (fail-closed).
    metrics_token: str = ""

    # --- External services (optional at startup; providers raise if unconfigured) ---
    google_maps_api_key: str = ""  # P2-3: route/distance integration, not wired yet
    whatsapp_business_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v21.0"
    whatsapp_otp_template: str = ""  # empty -> plain text message (sandbox)
    fcm_credentials_json: str = ""  # service-account JSON (inline or file path)
    sentry_dsn: str = ""  # optional error tracking (P2-4)
    prometheus_enabled: bool = False  # mounts /metrics when true AND metrics_token set

    # --- Email delivery (P-2: registration verification) ---
    # SMTP rather than a vendor API: the verification link is low-volume and
    # transactional, and SMTP works with any provider (SES, Postmark, a mailbox)
    # without adding an SDK and a second credential format to manage.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""  # e.g. "RealTaxi HK <no-reply@realtaxihk.com>"
    smtp_starttls: bool = True
    # How long a verification link stays usable. Long enough to survive an email
    # sitting in a queue, short enough that a leaked link is not permanent.
    email_verify_ttl_hours: int = 24
    # The base URL the verification link points at (the app's public origin).
    # A missing value in prod is caught by `_fail_closed` below, because a link
    # built from a default would send users to the wrong host.
    public_base_url: str = "http://127.0.0.1:8000"

    # --- Object storage (P-2: avatars, P-3: licence photos) ---
    # Cloudflare R2 via its S3-compatible API. `endpoint_url` is the account
    # endpoint (`https://<account_id>.r2.cloudflarestorage.com`).
    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    # Public read host for the bucket, if one is configured. Empty means the
    # API serves images through a signed redirect instead.
    r2_public_base_url: str = ""
    # Uploads are presigned by the API and sent straight to R2 by the client, so
    # the API never proxies a 5 MB photo through its own worker.
    upload_url_ttl_s: int = 900
    max_avatar_bytes: int = 5 * 1024 * 1024

    # --- Account lifecycle (P-2 / P-4) ---
    # P-4: how often a user must re-prove the phone number.
    phone_reverify_interval_days: int = 30
    # Once due, how long the account keeps working before new business is
    # blocked. A grace window rather than an immediate block: the re-verify is
    # triggered by the app, and locking a driver out mid-shift because a
    # notification was missed is worse than a day of grace.
    phone_reverify_grace_days: int = 7

    driver_deposit_default_hkd: int = 500
    no_show_penalty_hkd: int = 50

    @model_validator(mode="after")
    def _fail_closed(self) -> Settings:
        """Reject unsafe configurations at import/startup, never mid-request."""
        # 1) SEC-01: APP_ENV must be explicit and whitelisted. Blank means "nobody
        #    told us", which is the dangerous case — refuse to guess.
        if not self.app_env:
            raise ValueError(
                "APP_ENV is not set. Set it explicitly to one of "
                f"{VALID_APP_ENVS} — there is no default, because defaulting to "
                "'dev' would expose the dev OTP code on a production host."
            )
        if self.app_env not in VALID_APP_ENVS:
            raise ValueError(
                f"APP_ENV must be one of {VALID_APP_ENVS}, got {self.app_env!r}. "
                "Values like 'production'/'PROD'/'staging' would silently skip the "
                "prod safety checks."
            )

        # 2) SEC-04/05: prod-specific checks. These run BEFORE the generic entropy
        #    check so the message names the actual offending setting.
        if self.app_env == "prod":
            if self.jwt_secret_key.startswith("dev-only"):
                raise ValueError("JWT_SECRET_KEY still has the dev default — override it in prod")
            if self.jwt_secret_key in _KNOWN_DEV_SECRETS:
                raise ValueError(
                    "JWT_SECRET_KEY is a public/committed value — rotate it before prod"
                )
            if not self.postgres_password or self.postgres_password == "change-me-dev":
                raise ValueError(
                    "POSTGRES_PASSWORD still has the dev default — override it in prod"
                )
            # SEC-02: the dev OTP shortcut is not a prod option, ever.
            if self.allow_dev_otp:
                raise ValueError("ALLOW_DEV_OTP must not be enabled when APP_ENV=prod")

            # P-2: a verification email is only usable if it can be sent and the
            # link points at the real host. Both fail *silently* at runtime — the
            # user simply never gets an email, or gets one pointing at localhost —
            # so refuse to start instead.
            if not self.smtp_host or not self.smtp_from:
                raise ValueError(
                    "SMTP_HOST and SMTP_FROM must be set when APP_ENV=prod — "
                    "registration cannot verify an email address without them."
                )
            if not self.public_base_url.startswith("https://"):
                raise ValueError(
                    f"PUBLIC_BASE_URL must be an https:// URL in prod, got "
                    f"{self.public_base_url!r}. A verification link built on http "
                    "travels in cleartext and points at the wrong host if this is "
                    "still the default."
                )

        # 3) SEC-04: a secret must exist and carry real entropy. Length alone is not
        #    enough — "x" * 64 is 64 chars and zero entropy.
        if not self.jwt_secret_key:
            raise ValueError("JWT_SECRET_KEY is not set. Generate one with `openssl rand -hex 32`.")
        if len(self.jwt_secret_key) < _MIN_SECRET_CHARS:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least {_MIN_SECRET_CHARS} characters "
                f"(got {len(self.jwt_secret_key)})."
            )
        if len(set(self.jwt_secret_key)) < _MIN_SECRET_DISTINCT:
            raise ValueError(
                "JWT_SECRET_KEY has insufficient entropy — it uses fewer than "
                f"{_MIN_SECRET_DISTINCT} distinct characters."
            )
        return self

    @property
    def dev_otp_enabled(self) -> bool:
        """SEC-02: the fixed dev OTP needs BOTH a non-prod env and an explicit switch."""
        return self.allow_dev_otp and self.app_env in ("dev", "test")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
