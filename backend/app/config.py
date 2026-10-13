"""Application configuration.

Loaded once at import time and validated by pydantic-settings, so a bad
environment fails at startup rather than mid-investigation (SPEC §13).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent

ProviderName = Literal["ollama", "google", "openai", "openai_compat"]


class Settings(BaseSettings):
    """Runtime configuration for the LogSherlock backend."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -------------------------------------------------------
    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "LogSherlock"
    log_level: str = "INFO"

    # --- Network -----------------------------------------------------------
    frontend_origin: str = "http://localhost:3000"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000

    # --- Persistence -------------------------------------------------------
    database_url: str = "sqlite:///./logsherlock.db"
    persist_raw_logs: bool = False

    # --- Model provider (SPEC §4.3, §9.4) ---------------------------------
    model_provider: ProviderName = "ollama"
    # None means "let the provider use its own default". Do NOT hard-code one
    # base URL globally or every provider inherits the wrong endpoint.
    model_base_url: str | None = None
    model_api_key: str | None = None
    model_name: str = "qwen3:1.7b"
    model_timeout_seconds: int = 90
    model_temperature: float = 0.1
    model_max_output_tokens: int = 2048

    # --- Input limits (SPEC §7.2) ------------------------------------------
    max_upload_mb: int = 5
    max_log_lines: int = 20_000
    max_line_length: int = 20_000
    max_evidence_chars: int = 16_000
    max_concurrent_investigations: int = 2
    max_groups_for_model: int = 8

    # --- Redaction (SPEC §12.2) -------------------------------------------
    redaction_enabled: bool = True
    redact_ip_addresses: bool = True

    # --- Derived -----------------------------------------------------------
    @property
    def max_upload_bytes(self) -> int:
        """Upload ceiling in bytes."""
        return self.max_upload_mb * 1024 * 1024

    @property
    def cors_origins(self) -> list[str]:
        """Explicit allow-list. Never a wildcard (SPEC §12.5)."""
        return [origin.strip() for origin in self.frontend_origin.split(",") if origin.strip()]

    @property
    def is_test(self) -> bool:
        """True when running under pytest or with APP_ENV=test."""
        return self.app_env == "test"

    @field_validator("model_name")
    @classmethod
    def _model_name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("MODEL_NAME must not be empty")
        return value.strip()

    @field_validator("max_concurrent_investigations")
    @classmethod
    def _at_least_one_slot(cls, value: int) -> int:
        if value < 1:
            raise ValueError("MAX_CONCURRENT_INVESTIGATIONS must be >= 1")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
