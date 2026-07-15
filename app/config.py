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
    #
    # READ THIS BEFORE CHANGING min_confidence.
    #
    # The model's confidence peaks around 60% and averages ~36%. With the original
    # 0.80 gate, measured over 500 live BTC candles, it emitted WAIT 500/500 times:
    # the gate was mathematically unreachable, so the platform never traded.
    #
    # 0.55 is the deliberate answer to that, and it is a REAL trade-off, not a fix:
    # signals now fire, but they fire from a model measured at ~51.6% directional
    # accuracy. That is a coin flip, and a coin flip minus 0.15% fees is a slow,
    # reliable loser. Anything below `SAFE_CONFIDENCE` is a LEARNING setting.
    # The dashboard shows a permanent warning while it is, and that is on purpose.
    #
    # Do not raise this back to 0.80 expecting signals; raise it only when a
    # retrained model actually earns the confidence.
    # NB: confidence is over 3 classes (up/down/sideways), so pure chance is 33%,
    # not 50%. 0.45 is meaningfully above random. Measured, 0.45 + 3-of-5
    # confirmations yields a ~5% signal rate (about 1 trade per 20 candles).
    min_confidence: float = Field(0.45, ge=0.0, le=1.0)
    min_rr: float = Field(2.0, gt=0.0)   # keep this. R:R is genuinely protective.

    # How many of the 5 confirmations (trend, volume, momentum, structure,
    # candlestick) must agree. Was effectively 5 (unanimity) — which, measured, fired
    # zero signals in 500 candles because each check fails 51-72% of the time alone.
    # 3-of-5 is a confluence gate, not a rubber stamp; 5 is a locked door.
    min_confirmations: int = Field(3, ge=1, le=5)

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


# The confidence gate below which signals are "learning mode", not tradeable
# conviction. Kept as a constant so the API and dashboard can warn about it in one
# place rather than each re-deciding what "safe" means.
SAFE_CONFIDENCE = 0.70

# Measured out-of-sample directional accuracy of the shipped model. Displayed to
# the user verbatim. If you retrain, re-measure this and update it — do not guess,
# and do not round it upward.
MEASURED_ACCURACY = 0.516


def learning_mode() -> bool:
    """True when the confidence gate is below tradeable conviction."""
    return settings.min_confidence < SAFE_CONFIDENCE
