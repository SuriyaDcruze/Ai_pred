"""Request/response models for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.data.schemas import Candle, Signal


class AnalyzeRequest(BaseModel):
    symbol: str = Field(..., examples=["BTCUSDT"])
    exchange: str = Field("binance-spot", examples=["binance-spot"])
    timeframe: str = Field("1h", examples=["1h"])
    candles: list[Candle] | None = Field(
        None, description="Optional explicit candles; if omitted, data is fetched live."
    )
    limit: int = Field(300, ge=60, le=1000)


class PredictRequest(AnalyzeRequest):
    """Same inputs as analyze; returns raw model output instead of a signal."""


class HealthResponse(BaseModel):
    status: str
    version: str
    device: str
    model_loaded: bool


class SignalResponse(BaseModel):
    signal: Signal


class ChatRequest(BaseModel):
    symbol: str = Field("BTCUSDT")
    timeframe: str = Field("1h")
    message: str = Field(..., examples=["Where do I trade?"])
    price: float | None = Field(
        None, description="Optional price the user tapped on the chart for a 'what if' question."
    )
    candles: list[Candle] | None = Field(None, description="Optional explicit candles; else fetched live.")
    limit: int = Field(300, ge=60, le=1000)


class ChatMarkerModel(BaseModel):
    price: float
    color: str
    label: str
    style: str = "line"


class ChatResponse(BaseModel):
    reply: str
    markers: list[ChatMarkerModel] = Field(default_factory=list)
    decision: str | None = None
    confidence: float | None = None
    side: str | None = None      # BUY / SELL suggested for a tapped point
    entry: float | None = None   # the exact price the answer is about
    stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    # The AI's OWN independent pick at the same spot (for the head-to-head)
    ai_side: str | None = None
    ai_stop: float | None = None
    ai_tp1: float | None = None
    ai_tp2: float | None = None


class RecordCallRequest(BaseModel):
    symbol: str
    timeframe: str = "1h"
    side: str                    # BUY / SELL
    entry: float
    stop: float
    tp1: float
    tp2: float
    clicked_time: int            # epoch seconds of the tapped candle
    clicked_price: float
    source: str = "manual"       # "manual" | "ai"


# --------------------------------------------------------------------------- #
# Forward Testing (Sprint 1 · M4) — request model for POST /forward/prediction
# --------------------------------------------------------------------------- #
class ForwardPredictionRequest(BaseModel):
    """A recommendation to record for forward testing.

    This endpoint does **not** run any model: the caller supplies a recommendation the
    Prediction/Outcome engines already produced, and Forward Testing stores it and later
    scores it against real price. Only actionable BUY/SELL calls can be forward-tested —
    a WAIT is not a trade — so ``recommendation`` is validated to BUY or SELL here.
    """

    symbol: str = Field(..., examples=["RELIANCE.NS"], min_length=1)
    exchange: str = Field("NSE", examples=["NSE", "binance-spot"])
    timeframe: str = Field("1d", examples=["1d", "1h"])
    current_price: float = Field(..., gt=0, description="Price at the moment of the call.")
    direction: str = Field(..., examples=["BUY"], description="Model's directional read.")
    recommendation: str = Field(..., examples=["BUY"], description="Final decision (BUY/SELL).")
    created_candle_ts: int = Field(..., gt=0, description="Epoch seconds of the origin bar.")

    # Risk-defined plan (optional, but a record with no stop/target cannot be resolved
    # to a win/loss — the resolver will simply leave it open until it expires).
    entry: float | None = Field(None, gt=0)
    stop: float | None = Field(None, gt=0)
    target1: float | None = Field(None, gt=0)
    target2: float | None = Field(None, gt=0)

    # Model outputs (stored verbatim; no validation beyond probability bounds).
    direction_prob: float | None = Field(None, ge=0, le=1)
    outcome_prob: float | None = Field(None, ge=0, le=1)
    decision_score: float | None = None

    # Explainability context (all optional).
    market_regime: str | None = None
    market_phase: str | None = None
    sector: str | None = None
    session: str | None = None
    volatility_bucket: str | None = None
    similarity_score: float | None = None
    context: dict[str, Any] | None = None

    # Independent version stamps.
    prediction_model_version: str | None = None
    outcome_model_version: str | None = None
    feature_version: str | None = None

    source: str = Field("manual", examples=["manual", "forward", "screener"])

    @field_validator("direction", "recommendation")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("recommendation")
    @classmethod
    def _actionable(cls, v: str) -> str:
        if v not in {"BUY", "SELL"}:
            raise ValueError("recommendation must be BUY or SELL to forward-test it")
        return v

    @field_validator("direction")
    @classmethod
    def _known_direction(cls, v: str) -> str:
        if v not in {"BUY", "SELL", "WAIT"}:
            raise ValueError("direction must be BUY, SELL, or WAIT")
        return v
