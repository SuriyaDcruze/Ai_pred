"""Tests for the LLM assistant's grounding + graceful fallback.

These run fully offline: without ANTHROPIC_API_KEY (or the anthropic package),
``LLMAssistant.available`` is False and it serves the deterministic reply — so
the app never depends on the network to answer.
"""

import json

from app.chat.llm import LLMAssistant
from app.data.schemas import Side
from app.service import AnalysisService


def _ctx(ohlcv):
    service = AnalysisService()
    features = service.feature_builder.build_frame(ohlcv)
    signal = service.analyze(ohlcv, "BTCUSDT", "binance-spot", "1h")
    return signal, features


def test_llm_falls_back_to_deterministic_without_key(ohlcv, monkeypatch):
    # Force the LLM path to look unavailable regardless of local env.
    a = LLMAssistant()
    a._client_ready = False
    signal, features = _ctx(ohlcv)
    reply = a.respond(signal, ohlcv, features, "where do I trade?")
    # Deterministic answer must still carry exact levels + markers.
    assert reply.markers, "fallback lost the chart markers"
    assert reply.decision in (Side.BUY.value, Side.SELL.value, Side.WAIT.value)


def test_context_is_valid_json_with_ground_truth(ohlcv):
    a = LLMAssistant()
    signal, features = _ctx(ohlcv)
    grounded = a._det.respond(signal, ohlcv, features, "buy here", hypo_price=float(ohlcv["close"].iloc[-1]))
    ctx = LLMAssistant._build_context(
        signal, features, ohlcv, grounded, "buy here", float(ohlcv["close"].iloc[-1])
    )
    data = json.loads(ctx)  # must be valid JSON for the model
    assert data["symbol"] == "BTCUSDT"
    assert "computed_answer" in data and "computed_levels" in data
    assert data["indicators"]["rsi"] is not None


def test_exact_plan_while_waiting_has_entry_and_trigger(ohlcv):
    from app.chat.assistant import TradingAssistant

    signal, features = _ctx(ohlcv)
    reply = TradingAssistant().respond(signal, ohlcv, features, "give me the exact location to trade")
    if signal.decision is Side.WAIT:
        # Must name an exact entry marker and a plain-language WHEN condition.
        assert any(m.style == "marker" for m in reply.markers)
        assert "jump in" in reply.reply.lower() or "watching" in reply.reply.lower()
