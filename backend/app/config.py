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

    # CORS
    frontend_origin: str = "http://localhost:8501"

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
