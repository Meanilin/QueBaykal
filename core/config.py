"""Application configuration.

Single source of truth for all runtime settings.
Loaded once at startup, frozen, then injected via `get_settings()`.

Order of precedence (highest first):
1. Real environment variables (e.g. injected by Docker / Kubernetes)
2. `.env.local` (developer-only, gitignored)
3. `.env` (default values, checked in with placeholders)

Usage:
    from core.config import get_settings
    settings = get_settings()
    print(settings.bot_token)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TelegramSettings(BaseSettings):
    """Telegram bot credentials and mode."""

    bot_token: SecretStr = Field(..., description="Bot token from @BotFather")
    admin_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list,
        description="Telegram user IDs with global admin rights (root)",
    )
    parse_mode: Literal["HTML", "MarkdownV2", "Markdown"] = "HTML"
    bot_mode: Literal["polling", "webhook"] = "polling"
    webhook_domain: str | None = None
    webhook_path: str = "/webhook"
    webhook_secret: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_prefix="TG_",
        env_file=(_PROJECT_ROOT / ".env.local", _PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v: object) -> list[int]:
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip().isdigit()]
        if isinstance(v, int):
            return [v]
        if isinstance(v, list):
            return [int(x) for x in v]
        return []


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection settings (asyncpg)."""

    host: str = "postgres"
    port: int = 5432
    user: str = "cinema"
    password: SecretStr = SecretStr("cinema_secret")
    database: str = "cinema_queue"
    echo: bool = False
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout_seconds: int = 30

    model_config = SettingsConfigDict(
        env_prefix="POSTGRES_",
        env_file=(_PROJECT_ROOT / ".env.local", _PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def to_dsn(self, driver: Literal["asyncpg", "psycopg2"] = "asyncpg") -> str:
        pwd = self.password.get_secret_value()
        if driver == "asyncpg":
            return f"postgresql+asyncpg://{self.user}:{pwd}@{self.host}:{self.port}/{self.database}"
        return f"postgresql+psycopg2://{self.user}:{pwd}@{self.host}:{self.port}/{self.database}"


class RedisSettings(BaseSettings):
    """Redis for FSM storage and cache."""

    host: str = "redis"
    port: int = 6379
    db: int = 0
    password: SecretStr | None = None
    fsm_ttl_seconds: int = 86_400  # 24h
    cache_ttl_seconds: int = 300  # 5m

    model_config = SettingsConfigDict(
        env_prefix="REDIS_",
        env_file=(_PROJECT_ROOT / ".env.local", _PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def to_dsn(self) -> str:
        auth = f":{self.password.get_secret_value()}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class APISettings(BaseSettings):
    """HTTP API server (FastAPI)."""

    host: str = "0.0.0.0"
    port: int = 8080
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])
    agents_api_key: SecretStr | None = None  # bearer token for /api/agent/*

    model_config = SettingsConfigDict(
        env_prefix="API_",
        env_file=(_PROJECT_ROOT / ".env.local", _PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return v
        return ["*"]


class LoggingSettings(BaseSettings):
    """Structured logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    json_output: bool = False  # human-readable in dev, JSON in prod
    include_timestamp: bool = True

    model_config = SettingsConfigDict(
        env_prefix="LOG_",
        env_file=(_PROJECT_ROOT / ".env.local", _PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


class AppSettings(BaseSettings):
    """Top-level settings, aggregates all sub-settings."""

    environment: Literal["dev", "staging", "prod"] = "dev"
    service_name: str = "cinema-queue-bot"
    debug: bool = False

    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    api: APISettings = Field(default_factory=APISettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    model_config = SettingsConfigDict(
        env_file=(_PROJECT_ROOT / ".env.local", _PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Cached settings instance. Called once at startup."""
    return AppSettings()


__all__ = [
    "AppSettings",
    "TelegramSettings",
    "DatabaseSettings",
    "RedisSettings",
    "APISettings",
    "LoggingSettings",
    "get_settings",
]
