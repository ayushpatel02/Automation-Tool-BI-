"""Application configuration, loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Core
    database_url: str = "sqlite+aiosqlite:///./app.db"

    # Security
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 14
    encryption_key: str = "change-me-generate-a-real-fernet-key"

    # Default LLM provider keys (optional; users normally supply their own)
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # Generation tuning
    max_retries: int = 3
    schema_token_budget: int = 7000

    # TMDL validation
    te2_cli_path: str | None = None

    # Output
    generated_dir: Path = Path("./generated")

    # CORS / frontend. Also used as the base URL for password-reset links.
    frontend_origin: str = "http://localhost:8501"

    # Email (SMTP). If smtp_host is unset, emails are logged instead of sent so the
    # password-reset flow stays usable in local development without a mail server.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_from: str = "no-reply@pbigen.local"

    # How long a password-reset link / code stays valid.
    password_reset_expire_minutes: int = 30

    @property
    def default_provider_keys(self) -> dict[str, str | None]:
        """Map of provider name -> default API key (fallback when user has none stored)."""
        return {
            "google": self.gemini_api_key,
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
