"""Central config via pydantic-settings. .env supported; prod secrets MUST come from env.

Prod fail-fast (P0-2): APP_ENV=prod with dev JWT secret or dev DB password raises at
startup — a misconfigured platform must never come up half-secured.
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"  # dev | test | prod
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8081"]
    log_level: str = "INFO"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "realtaxi"
    postgres_password: str = "change-me-dev"
    postgres_db: str = "realtaxihk"

    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = "dev-only-secret-change-in-prod-0123456789abcdef-0123456789abcdef"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    refresh_token_expire_days: int = 14

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

    # --- OTP / rate limiting (P1-2) ---
    otp_ip_rate_limit: int = 10  # requests per IP per window
    otp_ip_window_s: int = 600  # 10 minutes
    otp_global_hourly_limit: int = 500  # cost cap across the platform

    # --- WebSocket (P1-9) ---
    ws_heartbeat_s: int = 30  # server ping cadence for idle-keepalive

    # --- External services (optional at startup; providers raise if unconfigured) ---
    google_maps_api_key: str = ""  # P2-3: route/distance integration, not wired yet
    whatsapp_business_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v21.0"
    whatsapp_otp_template: str = ""  # empty -> plain text message (sandbox)
    fcm_credentials_json: str = ""  # service-account JSON (inline or file path)
    sentry_dsn: str = ""  # optional error tracking (P2-4)
    prometheus_enabled: bool = False  # mounts /metrics when true

    driver_deposit_default_hkd: int = 500
    no_show_penalty_hkd: int = 50

    @model_validator(mode="after")
    def _prod_safety(self) -> "Settings":
        """Fail fast on prod misconfiguration (P0-2) — never boot half-secured."""
        if self.app_env == "prod":
            if self.jwt_secret_key.startswith("dev-only"):
                raise ValueError("JWT_SECRET_KEY still has the dev default — override it in prod")
            if not self.postgres_password or self.postgres_password == "change-me-dev":
                raise ValueError(
                    "POSTGRES_PASSWORD still has the dev default — override it in prod"
                )
        return self

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
