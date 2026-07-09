"""Central, typed configuration loaded from environment / .env file.

All tunables live here so the rest of the codebase never reads ``os.environ``
directly. Import the singleton ``settings``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. Values come from env vars prefixed ``AEGIS_``."""

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # --- Model / inference ---
    device: Literal["auto", "cpu", "cuda"] = "auto"
    model_path: str = "artifacts/model_best.pt"
    seq_len: int = Field(128, ge=16, le=1024, description="Input window length in candles")

    # --- Decision thresholds ---
    min_confidence: float = Field(0.80, ge=0.0, le=1.0)
    min_rr: float = Field(2.0, gt=0.0)

    # --- Conversational assistant ---
    chat_llm: bool = Field(True, description="Use the Claude LLM assistant when available")
    chat_model: str = Field("claude-opus-4-8", description="Anthropic model id for the chat assistant")

    # --- Risk ---
    max_account_risk: float = Field(0.01, gt=0.0, le=0.1, description="Fraction of equity per trade")
    account_equity: float = Field(10_000.0, gt=0.0)

    # --- Exchange keys (public streams do not need them) ---
    binance_api_key: str = ""
    binance_api_secret: str = ""

    # --- Infra ---
    postgres_dsn: str = "postgresql+asyncpg://aegis:aegis@localhost:5432/aegis"
    redis_url: str = "redis://localhost:6379/0"

    def resolve_device(self) -> str:
        """Return the concrete torch device string, honouring ``auto``."""
        if self.device != "auto":
            return self.device
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:  # torch not importable in some contexts
            return "cpu"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
