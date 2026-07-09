"""FastAPI application exposing the trading AI over REST + WebSocket.

Endpoints:
    GET  /health           liveness + model status
    GET  /market           latest ticker snapshot for a symbol
    GET  /history          historical candles
    POST /analyze          full trade signal (BUY/SELL/WAIT + risk plan)
    POST /predict          raw multi-task model output
    GET  /signals          most recent signals cache
    WS   /ws/signals       stream live signals as candles close
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.schemas import (
    AnalyzeRequest,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    PredictRequest,
    RecordCallRequest,
)
from app.chat.assistant import TradingAssistant
from app.config import settings
from app.data.schemas import Candle, candles_to_frame
from app.service import AnalysisService
from app.stream.binance import BinanceClient
from app.utils.logging import get_logger

logger = get_logger(__name__)

# In-memory ring buffer of the most recent signals (swap for Redis/PG later).
_RECENT_SIGNALS: deque = deque(maxlen=100)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = AnalysisService()
    app.state.binance = BinanceClient()
    if settings.chat_llm:
        from app.chat.llm import LLMAssistant

        app.state.assistant = LLMAssistant(risk_manager=app.state.service.risk)
    else:
        app.state.assistant = TradingAssistant(risk_manager=app.state.service.risk)
    from app.tracking.tracker import CallStore

    app.state.calls = CallStore()
    app.state.autolog = False  # hands-free logging of the AI's own picks
    logger.info("Aegis API ready (v%s, device=%s)", __version__, settings.resolve_device())
    yield


app = FastAPI(title="Aegis Trading AI", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _get_candles(req: AnalyzeRequest) -> list[Candle]:
    if req.candles:
        return req.candles
    client: BinanceClient = app.state.binance
    return await client.fetch_history(req.symbol, req.timeframe, total=req.limit)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    service: AnalysisService = app.state.service
    # Touch predictor to report whether a real model is loaded.
    _ = service.predictor
    return HealthResponse(
        status="ok",
        version=__version__,
        device=settings.resolve_device(),
        model_loaded=not service._using_heuristic,
    )


@app.get("/market")
async def market(symbol: str = Query(...), timeframe: str = "1m"):
    client: BinanceClient = app.state.binance
    candles = await client.fetch_klines(symbol, timeframe, limit=1)
    if not candles:
        raise HTTPException(404, f"No market data for {symbol}")
    c = candles[-1]
    return {"symbol": symbol.upper(), "price": c.close, "time": c.open_time, "volume": c.volume}


@app.get("/history")
async def history(symbol: str = Query(...), timeframe: str = "1h", limit: int = Query(300, ge=1, le=1000)):
    client: BinanceClient = app.state.binance
    candles = await client.fetch_history(symbol, timeframe, total=limit)
    return {"symbol": symbol.upper(), "timeframe": timeframe, "candles": candles}


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    service: AnalysisService = app.state.service
    candles = await _get_candles(req)
    df = candles_to_frame(candles)
    try:
        signal = service.analyze(df, req.symbol, req.exchange, req.timeframe)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _RECENT_SIGNALS.appendleft(signal)
    return {"signal": signal}


@app.post("/predict")
async def predict(req: PredictRequest):
    service: AnalysisService = app.state.service
    candles = await _get_candles(req)
    df = candles_to_frame(candles)
    if len(df) < 60:
        raise HTTPException(422, "Need at least 60 candles.")
    prediction = service.predictor.predict(df)
    return {"symbol": req.symbol.upper(), "prediction": prediction}


@app.get("/signals")
async def signals(limit: int = Query(20, ge=1, le=100)):
    return {"signals": list(_RECENT_SIGNALS)[:limit]}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Ask the trading assistant a question, optionally about a tapped price."""
    service: AnalysisService = app.state.service
    assistant: TradingAssistant = app.state.assistant
    candles = req.candles or await app.state.binance.fetch_history(req.symbol, req.timeframe, total=req.limit)
    df = candles_to_frame(candles)
    if len(df) < 60:
        raise HTTPException(422, "Need at least 60 candles for context.")
    features = service.feature_builder.build_frame(df)
    signal = service.analyze(df, req.symbol, service_exchange(), req.timeframe)
    reply = assistant.respond(signal, df, features, req.message, hypo_price=req.price)

    # If this is a tap (a price), also compute the AI's OWN pick for that same
    # spot — so one tap is a head-to-head: You vs AI at the identical point.
    ai = None
    if req.price is not None:
        ai_side = service.ai_direction(df)        # debiased pick
        atr = float(features["atr"].iloc[-1]) if "atr" in features else req.price * 0.01
        plan = service.risk.build_plan(ai_side, req.price, atr)
        if plan is not None:
            ai = {"side": ai_side.value, "stop": plan.stop_loss,
                  "tp1": plan.take_profit_1, "tp2": plan.take_profit_2}

    return ChatResponse(
        reply=reply.reply,
        markers=[m.__dict__ for m in reply.markers],
        decision=reply.decision,
        confidence=reply.confidence,
        side=reply.side,
        entry=reply.entry,
        stop=reply.stop,
        tp1=reply.tp1,
        tp2=reply.tp2,
        ai_side=ai["side"] if ai else None,
        ai_stop=ai["stop"] if ai else None,
        ai_tp1=ai["tp1"] if ai else None,
        ai_tp2=ai["tp2"] if ai else None,
    )


def service_exchange() -> str:
    return app.state.binance.name


# --------------------------------------------------------------------------- #
# Forward-testing: record tapped calls and score them against real price.
# --------------------------------------------------------------------------- #


@app.post("/calls")
async def record_call(req: RecordCallRequest):
    """Save a call the user marked on the chart (for later WIN/LOSS scoring)."""
    import uuid
    from datetime import datetime, timezone

    from app.tracking.tracker import TrackedCall

    call = TrackedCall(
        id=uuid.uuid4().hex[:8],
        created_at=datetime.now(tz=timezone.utc).isoformat(),
        symbol=req.symbol.upper(), timeframe=req.timeframe,
        side=req.side.upper(), entry=req.entry, stop=req.stop,
        tp1=req.tp1, tp2=req.tp2,
        clicked_time=req.clicked_time, clicked_price=req.clicked_price,
        source=req.source,
    )
    app.state.calls.add(call)
    return {"ok": True, "id": call.id}


def _record_ai_call(symbol: str, timeframe: str, candles: list[Candle]) -> str | None:
    """Log the AI's own pick for the latest candle (deduped by candle time)."""
    import uuid
    from datetime import datetime, timezone

    from app.tracking.tracker import TrackedCall

    service: AnalysisService = app.state.service
    df = candles_to_frame(candles)
    pick = service.ai_paper_trade(df)
    if pick is None:
        return None
    clicked_time = int(candles[-1].open_time.timestamp())
    # Dedupe: one AI call per candle per market.
    for c in app.state.calls.all():
        if c.source == "ai" and c.symbol == symbol.upper() and c.timeframe == timeframe and c.clicked_time == clicked_time:
            return None
    call = TrackedCall(
        id=uuid.uuid4().hex[:8],
        created_at=datetime.now(tz=timezone.utc).isoformat(),
        symbol=symbol.upper(), timeframe=timeframe,
        side=pick["side"], entry=pick["entry"], stop=pick["stop"],
        tp1=pick["tp1"], tp2=pick["tp2"],
        clicked_time=clicked_time, clicked_price=pick["entry"], source="ai",
    )
    app.state.calls.add(call)
    return call.id


@app.post("/calls/ai")
async def log_ai_pick(symbol: str = Query(...), timeframe: str = "1h"):
    """Immediately log the AI's current pick so you can watch it play out."""
    candles = await app.state.binance.fetch_history(symbol, timeframe, total=300)
    cid = _record_ai_call(symbol, timeframe, candles)
    return {"ok": cid is not None, "id": cid}


@app.post("/autolog")
async def set_autolog(enabled: bool = Query(...)):
    """Turn hands-free AI auto-logging on/off (logs the AI's pick each candle close)."""
    app.state.autolog = enabled
    return {"autolog": enabled}


@app.get("/autolog")
async def get_autolog():
    return {"autolog": getattr(app.state, "autolog", False)}


@app.get("/calls")
async def list_calls():
    """Return all tracked calls, freshly scored against live price, + a win-rate."""
    from app.tracking.tracker import resolve_call, summarize

    store = app.state.calls
    calls = store.all()
    # Re-score OPEN calls (and keep decided ones) using fresh candles per market.
    by_market: dict[tuple[str, str], list] = {}
    for c in calls:
        by_market.setdefault((c.symbol, c.timeframe), []).append(c)

    for (symbol, tf), group in by_market.items():
        try:
            candles = await app.state.binance.fetch_history(symbol, tf, total=1000)
        except Exception:  # noqa: BLE001 - network; leave calls as-is
            continue
        for call in group:
            if call.status == "OPEN":
                resolve_call(call, candles)

    store.save_all(calls)
    calls_sorted = sorted(calls, key=lambda c: c.created_at, reverse=True)
    from dataclasses import asdict

    return {"calls": [asdict(c) for c in calls_sorted], "stats": summarize(calls)}


@app.post("/round")
async def head_to_head(
    symbol: str = Query(...), timeframe: str = "1h",
    clicked_time: int = Query(...), entry: float = Query(...), human_side: str = Query(...),
):
    """A fair You-vs-AI round: both bet at the same entry against ONE shared band.

    Upper and lower lines sit equidistant from entry. Whichever price touches
    first decides BOTH bets at the same instant — up → BUY wins/SELL loses, down
    → SELL wins/BUY loses. No more one-resolves-before-the-other.
    """
    import uuid
    from datetime import datetime, timezone

    from app.tracking.tracker import TrackedCall

    service: AnalysisService = app.state.service
    candles = await app.state.binance.fetch_history(symbol, timeframe, total=300)
    df = candles_to_frame(candles)
    features = service.feature_builder.build_frame(df)
    atr = float(features["atr"].iloc[-1]) if "atr" in features else entry * 0.01
    band = 1.5 * atr
    upper = round(entry + band, 8)
    lower = round(entry - band, 8)

    ai_side = service.ai_direction(df).value      # debiased pick
    human_side = human_side.upper()

    def make(side: str, source: str) -> TrackedCall:
        buy = side == "BUY"
        return TrackedCall(
            id=uuid.uuid4().hex[:8],
            created_at=datetime.now(tz=timezone.utc).isoformat(),
            symbol=symbol.upper(), timeframe=timeframe, side=side, entry=entry,
            stop=lower if buy else upper,          # shared band: your stop = their target
            tp1=upper if buy else lower,
            tp2=upper if buy else lower,
            clicked_time=clicked_time, clicked_price=entry, source=source,
        )

    app.state.calls.add(make(human_side, "manual"))
    app.state.calls.add(make(ai_side, "ai"))
    return {"human_side": human_side, "ai_side": ai_side, "upper": upper, "lower": lower,
            "agree": human_side == ai_side}


@app.delete("/calls")
async def clear_calls():
    app.state.calls.clear()
    return {"ok": True}


@app.delete("/calls/recent")
async def remove_recent_calls(count: int = Query(2, ge=1, le=100)):
    """Remove the most recent ``count`` calls (undo)."""
    removed = app.state.calls.remove_last(count)
    return {"removed": removed}


@app.get("/calls/export")
async def export_calls():
    """Export resolved calls as CSV — a forward-tested dataset for future training.

    Each closed call is a real labelled example: the setup (symbol/timeframe/side/
    entry) and the verified outcome (WIN/LOSS + R). Accumulate enough of these and
    they train a 'win/loss filter' on top of the model. See docs/ROADMAP.md.
    """
    import csv
    import io

    from fastapi.responses import PlainTextResponse

    calls = [c for c in app.state.calls.all() if c.status in ("WIN", "LOSS")]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "source", "symbol", "timeframe", "side", "entry", "stop", "tp1",
                "clicked_time", "status", "r_multiple", "resolved_time"])
    for c in calls:
        w.writerow([c.id, c.source, c.symbol, c.timeframe, c.side, c.entry, c.stop, c.tp1,
                    c.clicked_time, c.status, c.r_multiple, c.resolved_time])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=aegis_calls.csv"})


@app.get("/patterns")
async def patterns(symbol: str = Query(...), timeframe: str = "1h", limit: int = Query(300, ge=60, le=1000)):
    """Per-candle candlestick patterns for drawing markers on the chart."""
    from app.features.candlesticks import add_candlestick_patterns, detected_patterns

    candles = await app.state.binance.fetch_history(symbol, timeframe, total=limit)
    df = candles_to_frame(candles)
    enriched = add_candlestick_patterns(df)
    out = []
    for ts, row in enriched.iterrows():
        found = detected_patterns(row)
        if found:
            p = found[0]
            out.append({
                "time": int(ts.timestamp()),
                "name": p["name"].split(" / ")[0],  # short label
                "dir": p["dir"],
            })
    return {"symbol": symbol.upper(), "timeframe": timeframe, "patterns": out}


@app.websocket("/ws/signals")
async def ws_signals(ws: WebSocket, symbol: str = "BTCUSDT", timeframe: str = "1h"):
    """Live feed for the dashboard.

    Emits three message types so the client can render without polling:
      * ``history`` — the initial candle buffer (draw the chart immediately)
      * ``candle``  — every kline tick from Binance (the forming bar updates live)
      * ``signal``  — a fresh analysis, computed once per closed candle
    """
    await ws.accept()
    service: AnalysisService = app.state.service
    client: BinanceClient = app.state.binance
    symbol = symbol.upper()

    buffer: list[Candle] = await client.fetch_history(symbol, timeframe, total=settings.seq_len + 200)
    await ws.send_json(
        {"type": "history", "symbol": symbol, "timeframe": timeframe,
         "candles": [c.model_dump(mode="json") for c in buffer]}
    )
    # Immediate first signal so the panel is populated on connect.
    try:
        first = service.analyze(candles_to_frame(buffer), symbol, client.name, timeframe)
        _RECENT_SIGNALS.appendleft(first)
        await ws.send_json({"type": "signal", "signal": first.model_dump(mode="json")})
    except ValueError:
        pass

    try:
        async for candle in client.stream_candles(symbol, timeframe):
            # Forward every tick so the chart's last bar animates live.
            await ws.send_json({"type": "candle", "candle": candle.model_dump(mode="json")})
            if not candle.closed:
                continue
            # Candle closed: commit it and recompute the signal.
            buffer.append(candle)
            buffer = buffer[-(settings.seq_len + 400):]
            df = candles_to_frame(buffer)
            try:
                signal = service.analyze(df, symbol, client.name, timeframe)
                _RECENT_SIGNALS.appendleft(signal)
                # Hands-free: log the AI's own pick BEFORE sending the update, so the
                # dashboard's refresh (triggered by this message) already sees it.
                if getattr(app.state, "autolog", False):
                    _record_ai_call(symbol, timeframe, buffer[-300:])
                await ws.send_json({"type": "signal", "signal": signal.model_dump(mode="json")})
            except ValueError:
                continue
    except WebSocketDisconnect:
        logger.info("WS client disconnected (%s %s)", symbol, timeframe)
    except Exception as exc:  # pragma: no cover - network
        logger.exception("WS error: %s", exc)
        await ws.close()


# Serve a minimal dashboard if the static file exists.
try:
    from fastapi.staticfiles import StaticFiles
    import os

    _static = os.path.join(os.path.dirname(__file__), "..", "dashboard", "static")
    if os.path.isdir(_static):
        app.mount("/dashboard", StaticFiles(directory=_static, html=True), name="dashboard")
except Exception:  # pragma: no cover
    pass
