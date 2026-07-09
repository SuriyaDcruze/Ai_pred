"""Core domain entities shared across every layer of the platform.

These pydantic models are the single source of truth for data shapes. Keep them
free of business logic and framework dependencies.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

import pandas as pd
from pydantic import BaseModel, Field, field_validator


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"


class MarketStatus(str, Enum):
    STRONG_BULLISH = "Strong Bullish"
    BULLISH = "Bullish"
    NEUTRAL = "Neutral"
    BEARISH = "Bearish"
    STRONG_BEARISH = "Strong Bearish"


class Candle(BaseModel):
    """A single OHLCV candle. ``open_time`` is the candle's start timestamp."""

    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int = 0
    closed: bool = True

    @field_validator("high")
    @classmethod
    def _high_is_max(cls, v: float, info) -> float:  # noqa: ANN001
        return v


class OrderBookLevel(BaseModel):
    price: float
    qty: float


class OrderBook(BaseModel):
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def imbalance(self) -> float:
        """Bid/ask volume imbalance in [-1, 1]; >0 means more bid pressure."""
        bid_vol = sum(l.qty for l in self.bids)
        ask_vol = sum(l.qty for l in self.asks)
        total = bid_vol + ask_vol
        return 0.0 if total == 0 else (bid_vol - ask_vol) / total


class ModelPrediction(BaseModel):
    """Raw multi-task output of the neural model for one window."""

    p_bullish: float
    p_bearish: float
    p_sideways: float
    predicted_high: float
    predicted_low: float
    predicted_close: float
    expected_volatility: float
    confidence: float

    @property
    def direction(self) -> Side:
        m = max(self.p_bullish, self.p_bearish, self.p_sideways)
        if m == self.p_bullish:
            return Side.BUY
        if m == self.p_bearish:
            return Side.SELL
        return Side.WAIT


class RiskPlan(BaseModel):
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward: float
    position_size: float
    account_risk_pct: float


class Signal(BaseModel):
    """The final, human-facing recommendation."""

    symbol: str
    exchange: str
    timeframe: str
    generated_at: datetime
    market_status: MarketStatus
    decision: Side
    confidence: float
    probability: str  # Low | Medium | High
    trend_strength: str
    expected_holding: str
    risk: RiskPlan | None = None
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)  # candlestick patterns spotted (beginner-friendly)
    final_recommendation: str = ""


def candles_to_frame(candles: list[Candle]) -> pd.DataFrame:
    """Convert a list of :class:`Candle` into an indexed OHLCV DataFrame."""
    if not candles:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume", "trades"]
        )
    df = pd.DataFrame([c.model_dump() for c in candles])
    df = df.set_index("open_time").sort_index()
    return df[["open", "high", "low", "close", "volume", "trades"]].astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float}
    )
