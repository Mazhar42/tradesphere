from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables (or a local .env file)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str
    test_database_url: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 10

    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    refresh_reuse_grace_seconds: int = 30

    cors_origins: list[str] = []

    business_timezone: str = "Asia/Dhaka"
    fiscal_year_start_month: int = 7  # Bangladesh fiscal year: 1 July - 30 June

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
