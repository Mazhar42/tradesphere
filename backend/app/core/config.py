from functools import lru_cache
from ipaddress import ip_network
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The value committed in docker-compose.yml for local development. Refused in production.
DEV_JWT_SECRET = "dev-only-secret-change-me-in-production-0123456789"  # noqa: S105


class Settings(BaseSettings):
    """Application settings, read from environment variables (or a local .env file)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str
    # Migrations and `seed` connect as the schema owner. Unset: same as database_url.
    migration_database_url: str | None = None
    # Platform administration and cross-dealership listings. Unset: same as database_url.
    platform_database_url: str | None = None
    test_database_url: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 10

    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    refresh_reuse_grace_seconds: int = 30

    # Failed logins are counted in the audit log (shared by all workers). Past the limit
    # for one username, or a larger one per client IP, sign-in is refused for the window.
    login_max_failures_per_user: int = 5
    login_max_failures_per_ip: int = 30
    login_window_minutes: int = 15

    cors_origins: list[str] = []

    # A second dealership shares the database only once isolation has been reviewed
    # (docs/operations.md, "Dealerships").
    allow_multiple_tenants: bool = False

    # Addresses or networks of the reverse proxies in front of the API. Only from these is
    # X-Forwarded-For believed; from anyone else the connecting address is the client.
    trusted_proxies: list[str] = ["127.0.0.1"]

    log_format: Literal["text", "json"] = "text"
    # Where unhandled errors are reported (Sentry, or a compatible service such as GlitchTip).
    # Unset: errors are only logged.
    sentry_dsn: SecretStr | None = None

    business_timezone: str = "Asia/Dhaka"
    fiscal_year_start_month: int = 7  # Bangladesh fiscal year: 1 July - 30 June

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def owner_database_url(self) -> str:
        return self.migration_database_url or self.database_url

    @property
    def platform_url(self) -> str:
        return self.platform_database_url or self.database_url

    @field_validator("database_url", "migration_database_url", "platform_database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str | None) -> str | None:
        """Hosts such as Render give plain `postgresql://` URLs; the app uses psycopg 3."""
        if value is None:
            return None
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        return value

    @field_validator("trusted_proxies")
    @classmethod
    def _proxies_are_specific(cls, value: list[str]) -> list[str]:
        for entry in value:
            # Raises for "*" and typos, which uvicorn would otherwise accept silently.
            if ip_network(entry).prefixlen == 0:
                raise ValueError("TRUSTED_PROXIES must name your proxies, not every address.")
        return value

    @model_validator(mode="after")
    def _production_must_be_safe(self) -> "Settings":
        if not self.is_production:
            return self
        secret = self.jwt_secret.get_secret_value()
        if secret == DEV_JWT_SECRET or len(secret) < 32:
            raise ValueError("JWT_SECRET must be a unique secret of at least 32 characters.")
        if "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS must list the app origin, not '*'.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
