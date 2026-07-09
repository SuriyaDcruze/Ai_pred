"""Request/response models for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field

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
