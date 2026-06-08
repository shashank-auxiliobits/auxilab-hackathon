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

    # --- LLM provider (mandatory) ----------------------------------------
    # A single multimodal provider handles BOTH stages: invoice extraction
    # (vision over images/PDFs) and the RAG + approval decision. Choose Claude
    # or GPT; the same model is used for both.
    llm_provider: Literal["claude", "openai"] = "claude"

    # Claude (Anthropic SDK) — vision + text capable.
    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-4-8"

    # OpenAI / GPT (vision + text capable). base_url=None → api.openai.com;
    # set it to point at any OpenAI-compatible endpoint.
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o"

    # Shared LLM call limits (extraction + decision).
    extractor_max_tokens: int = 4096
    extractor_timeout_seconds: float = 60.0

    # --- RAG / embeddings (for vendor policy documents) ------------------
    embedding_provider: Literal["local"] = Field(
        default="local",
        description="Embedding backend. 'local' is a deterministic, offline embedder "
        "(no external calls); swap for a hosted provider in production.",
    )
    embedding_dim: int = 256
    rag_chunk_size: int = Field(default=1200, description="Target chunk size in characters.")
    rag_top_k: int = Field(default=6, description="Chunks retrieved per query.")
    policy_compiler_max_tokens: int = 4096

    # --- Autonomy (touchless processing) --------------------------------
    min_extraction_confidence: float = Field(
        default=0.6,
        ge=0,
        le=1,
        description="LLM-extracted invoices below this per-field confidence are held for review.",
    )

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
        """The configured provider has the credentials it needs (extraction + decision)."""
        if self.llm_provider == "claude":
            return bool(self.anthropic_api_key)
        return bool(self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    return Settings()
