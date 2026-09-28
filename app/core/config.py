"""Central config via pydantic-settings. .env supported; prod requires real secret."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8081"]

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "realtaxi"
    postgres_password: str = "change-me-dev"
    postgres_db: str = "realtaxihk"

    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = "dev-only-secret-change-in-prod-0123456789abcdef-0123456789abcdef"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120

    driver_deposit_default_hkd: int = 500
    no_show_penalty_hkd: int = 50

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
