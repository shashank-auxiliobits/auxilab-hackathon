"""Application configuration.

All settings are sourced from environment variables (12-factor). In production,
inject these via your orchestrator's secret store — never commit a populated
``.env``. See ``.env.example`` for the full list.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings loaded from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="AP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Runtime ---------------------------------------------------------
    environment: Literal["development", "staging", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = Field(
        default=True,
        description="Emit structured JSON logs (recommended for production).",
    )

    # --- Database --------------------------------------------------------
    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://ap:ap_password@localhost:5432/ap_invoice",  # type: ignore[assignment]
        description="Async SQLAlchemy DSN (must use the asyncpg driver).",
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # --- API server ------------------------------------------------------
    api_host: str = "0.0.0.0"  # noqa: S104 - bind-all is intentional inside containers
    api_port: int = 8000
    api_root_path: str = ""
    cors_allow_origins: list[str] = Field(default_factory=list)
    rate_limit: str = Field(
        default="120/minute",
        description="Default per-client rate limit (slowapi syntax).",
    )

    # --- Security --------------------------------------------------------
    api_key_pepper: str = Field(
        default="change-me-in-production",
        min_length=8,
        description="Server-side pepper mixed into API-key hashes. Rotate carefully.",
    )
    admin_token: str | None = Field(
        default=None,
        description=(
            "Bearer token for provisioning endpoints (create orgs & API keys). "
            "If unset, those endpoints are disabled."
        ),
    )

    # --- MCP server ------------------------------------------------------
    mcp_host: str = "0.0.0.0"  # noqa: S104
    mcp_port: int = 8080
    mcp_transport: Literal["stdio", "streamable-http"] = "streamable-http"
    mcp_api_key: str | None = Field(
        default=None,
        description=(
            "API key used to scope tool calls when running the MCP server over stdio "
            "(no HTTP headers). Over streamable-HTTP, clients send their own key."
        ),
    )

    # --- LLM extractor ---------------------------------------------------
    extractor_engine: Literal["hybrid", "llm", "deterministic"] = "hybrid"
    anthropic_api_key: str | None = None
    extractor_model: str = "claude-opus-4-8"
    extractor_fast_model: str = "claude-haiku-4-5-20251001"
    extractor_max_tokens: int = 4096
    extractor_timeout_seconds: float = 60.0

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Allow a comma-separated string for CORS origins from a single env var."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment in ("production", "staging")

    @property
    def llm_available(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    return Settings()
