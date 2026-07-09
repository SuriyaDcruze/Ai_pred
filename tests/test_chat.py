"""Tests for the conversational trading assistant and the /chat endpoint."""

from fastapi.testclient import TestClient

from app.chat.assistant import TradingAssistant
from app.data.schemas import Side
from app.features.engineering import FeatureBuilder
from app.service import AnalysisService


def _context(ohlcv):
    service = AnalysisService()
    features = service.feature_builder.build_frame(ohlcv)
    signal = service.analyze(ohlcv, "TESTUSDT", "synthetic", "1h")
    return signal, features


def test_what_if_returns_green_red_levels(ohlcv):
    signal, features = _context(ohlcv)
    price = float(ohlcv["close"].iloc[-1])
    a = TradingAssistant()
    reply = a.respond(signal, ohlcv, features, "if I trade here", hypo_price=price)
    colors = {m.color for m in reply.markers}
    assert "green" in colors and "red" in colors  # entry/target vs stop
    assert "to-1 trade" in reply.reply  # friendly risk/reward phrasing


def test_explicit_buy_builds_long_plan(ohlcv):
    signal, features = _context(ohlcv)
    price = float(ohlcv["close"].iloc[-1])
    a = TradingAssistant()
    reply = a.respond(signal, ohlcv, features, "buy here", hypo_price=price)
    # For a long, the stop (red) must sit BELOW the entry marker.
    entry = next(m for m in reply.markers if m.style == "marker")
    stop = next(m for m in reply.markers if "Stop" in m.label)
    assert stop.price < entry.price


def test_explicit_sell_builds_short_plan(ohlcv):
    signal, features = _context(ohlcv)
    price = float(ohlcv["close"].iloc[-1])
    a = TradingAssistant()
    reply = a.respond(signal, ohlcv, features, "sell here", hypo_price=price)
    entry = next(m for m in reply.markers if m.style == "marker")
    stop = next(m for m in reply.markers if "Stop" in m.label)
    assert stop.price > entry.price


def test_why_wait_lists_blockers(ohlcv):
    signal, features = _context(ohlcv)
    a = TradingAssistant()
    reply = a.respond(signal, ohlcv, features, "why wait?")
    assert "WAIT" in reply.reply
    assert reply.decision == Side.WAIT.value


def test_where_shows_actionable_levels(ohlcv):
    signal, features = _context(ohlcv)
    a = TradingAssistant()
    reply = a.respond(signal, ohlcv, features, "where do I trade?")
    labels = " ".join(m.label for m in reply.markers)
    # Whether active or waiting, we now surface an exact entry + stop + targets.
    assert "Stop" in labels
    assert any(k in labels for k in ("Entry", "BUY", "SELL", "Watch"))


def test_chat_endpoint_with_supplied_candles():
    from app.api.main import app
    from app.data.synthetic import generate_ohlcv

    df = generate_ohlcv(n=200)
    candles = [
        {"open_time": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low,
         "close": r.close, "volume": r.volume, "trades": int(r.trades)}
        for ts, r in df.iterrows()
    ]
    with TestClient(app) as client:
        resp = client.post("/chat", json={
            "symbol": "BTCUSDT", "timeframe": "1h",
            "message": "If I trade here?", "price": float(df["close"].iloc[-1]),
            "candles": candles,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "reply" in body and isinstance(body["markers"], list)
