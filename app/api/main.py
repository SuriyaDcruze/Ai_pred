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
from app.decision.rules import RuleStore, check_rules, trades_today
from app.features.sentiment import fetch_news
from app.service import AnalysisService
from app.stream.binance import BinanceClient
from app.utils.logging import get_logger

logger = get_logger(__name__)

# In-memory ring buffer of the most recent signals (swap for Redis/PG later).
_RECENT_SIGNALS: deque = deque(maxlen=100)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.stream.yahoo import YahooClient

    app.state.service = AnalysisService()
    app.state.binance = BinanceClient()   # crypto
    app.state.yahoo = YahooClient()       # stocks / ETFs / indices
    if settings.chat_llm:
        from app.chat.llm import LLMAssistant

        app.state.assistant = LLMAssistant(risk_manager=app.state.service.risk)
    else:
        app.state.assistant = TradingAssistant(risk_manager=app.state.service.risk)
    from app.tracking.tracker import CallStore

    app.state.calls = CallStore()
    app.state.rules = RuleStore()          # your personal trading checklist
    # Hands-free AI logging — config lives on the server, so it keeps running
    # even when every browser tab is closed.
    app.state.autolog = {"enabled": False, "symbol": "BTCUSDT", "timeframe": "1m"}
    autolog_task = asyncio.create_task(_autolog_loop())
    logger.info("Aegis API ready (v%s, device=%s)", __version__, settings.resolve_device())
    try:
        yield
    finally:
        autolog_task.cancel()


app = FastAPI(title="Aegis Trading AI", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _provider(symbol: str):
    """Pick the data source for a symbol.

    Crypto pairs (…USDT) come from Binance; everything else (AAPL, ITC.NS, TSLA…)
    is a stock and comes from Yahoo Finance. Both expose the same interface, so
    nothing downstream cares which one it got.
    """
    from app.stream.yahoo import YahooClient

    return app.state.yahoo if YahooClient.is_stock(symbol) else app.state.binance


async def _get_candles(req: AnalyzeRequest) -> list[Candle]:
    if req.candles:
        return req.candles
    return await _provider(req.symbol).fetch_history(req.symbol, req.timeframe, total=req.limit)


@app.get("/")
async def root():
    """Open straight to the dashboard (nice landing for Render / HF Spaces)."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/dashboard/")


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
    candles = await _provider(symbol).fetch_klines(symbol, timeframe, limit=1)
    if not candles:
        raise HTTPException(404, f"No market data for {symbol}")
    c = candles[-1]
    return {"symbol": symbol.upper(), "price": c.close, "time": c.open_time, "volume": c.volume}


@app.get("/history")
async def history(symbol: str = Query(...), timeframe: str = "1h", limit: int = Query(300, ge=1, le=1000)):
    candles = await _provider(symbol).fetch_history(symbol, timeframe, total=limit)
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
    checklist = await _checklist_for(signal, df)
    outcome = _outcome_for(service, df)
    return {"signal": signal, "rules": checklist, "outcome": outcome}


def _outcome_for(service: AnalysisService, df: pd.DataFrame) -> dict | None:
    """Outcome-model verdict for the latest bar (the trade-selection veto layer)."""
    try:
        prediction = service.predictor.predict(df)
        return service.assess_outcome(df, prediction)
    except Exception:  # noqa: BLE001
        return None


@app.get("/outcome")
async def outcome(symbol: str = Query(...), timeframe: str = "1h"):
    """The outcome model's verdict on the live setup: will target hit before stop?

    This is the trade-selection layer that turned break-even into positive expectancy
    in backtests (see reports/outcome_model_summary.md). It VETOes trades the direction
    model wants but that historically don't reach their target first.
    """
    service: AnalysisService = app.state.service
    candles = await _provider(symbol).fetch_history(symbol.upper(), timeframe, total=400)
    df = candles_to_frame(candles)
    prediction = service.predictor.predict(df)
    verdict = service.assess_outcome(df, prediction)
    if verdict is None:
        return {"available": False,
                "note": "Outcome model not trained yet. Run: python -m app.training.outcome_training --save"}
    return {"available": True, "direction": prediction.direction.value,
            "direction_confidence": round(prediction.confidence, 3), **verdict}


async def _checklist_for(signal, df: pd.DataFrame) -> dict:
    """Score a signal against the user's own rules. Never fatal — a broken rule
    must not cost you the signal."""
    try:
        features = service_features(df)
        score = None
        if any(r["id"] == "news_agrees" and r["enabled"] for r in app.state.rules.catalogue()):
            score = (await fetch_news(signal.symbol, limit=6)).score
        verdict = check_rules(
            signal,
            features,
            app.state.rules,
            news_score=score,
            trades_today_count=trades_today(app.state.calls.all()),
        )
        return {
            "verdict": verdict.verdict,
            "advice": verdict.advice,
            "obeys": verdict.obeys_rules,
            "has_trade": verdict.has_trade,
            "passed": verdict.passed,
            "failed": verdict.failed,
            "results": [vars(r) for r in verdict.results],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("rule check failed: %s", exc)
        return {}


def service_features(df: pd.DataFrame) -> pd.DataFrame:
    return app.state.service.feature_builder.build_frame(df)


@app.get("/risk-notice")
async def risk_notice():
    """The honest state of the model, for the dashboard banner.

    This endpoint exists so the warning cannot drift out of sync with the config.
    If someone lowers the gate, the banner appears automatically.
    """
    from app.config import MEASURED_ACCURACY, SAFE_CONFIDENCE, learning_mode

    return {
        "learning_mode": learning_mode(),
        "min_confidence": settings.min_confidence,
        "safe_confidence": SAFE_CONFIDENCE,
        "measured_accuracy": MEASURED_ACCURACY,
        "headline": "LEARNING MODE — do not trade real money on these signals",
        "detail": (
            f"The confidence gate is set to {settings.min_confidence:.0%}, below the "
            f"{SAFE_CONFIDENCE:.0%} bar for tradeable conviction. Signals will fire, but they "
            f"come from a model measured at {MEASURED_ACCURACY:.1%} directional accuracy — a coin "
            f"flip. After fees, a coin flip is a losing strategy. Use these to learn and to "
            f"forward-test, not to trade."
        ),
    }


@app.get("/sectors")
async def sectors(timeframe: str = "1d"):
    """NSE sector strength ranking (relative to Nifty 50) — sector rotation context.
    Honest context for decisions, not a standalone signal."""
    import anyio

    from app.sector import sector_rankings

    return await anyio.to_thread.run_sync(lambda: sector_rankings(timeframe))


@app.get("/intelligence")
async def intelligence(symbol: str = Query(...), timeframe: str = "1d"):
    """V3 explainable stock intelligence: market state, relative strength, direction,
    the outcome-model decision, historical similarity, a trade plan, and a plain-English
    'why' — for one stock. Decision comes from the validated outcome model; the rest is
    honest context."""
    import anyio

    from app.intelligence import analyze_stock

    service: AnalysisService = app.state.service
    horizon = 5 if timeframe == "1d" else 12
    return await anyio.to_thread.run_sync(lambda: analyze_stock(service, symbol.upper(), timeframe, horizon))


@app.get("/screener/nse")
async def screener_nse():
    """Scan liquid NSE stocks and return today's TAKE setups with buy/sell levels.

    Uses the NSE-trained outcome model (validated to hold on Indian stocks). Only
    surfaces setups both models agree on. Backtest-verified, NOT proven live.
    """
    import anyio

    from app.screener import scan_nse

    service: AnalysisService = app.state.service
    # the scan does blocking work (fetch + fit per stock) — run it off the event loop
    return await anyio.to_thread.run_sync(lambda: scan_nse(service))


@app.get("/rules")
async def get_rules():
    """Your checklist and its current settings."""
    return {"rules": app.state.rules.catalogue()}


@app.post("/rules")
async def set_rule(
    rule_id: str = Query(..., description="Which rule to change"),
    enabled: bool | None = Query(None),
    value: float | None = Query(None),
):
    try:
        app.state.rules.set(rule_id, enabled=enabled, value=value)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"rules": app.state.rules.catalogue()}


@app.post("/rules/reset")
async def reset_rules():
    app.state.rules.reset()
    return {"rules": app.state.rules.catalogue()}


@app.get("/rules/check")
async def check_current(symbol: str = Query(...), timeframe: str = "1m"):
    """Score the live setup against your checklist — the dashboard polls this."""
    service: AnalysisService = app.state.service
    candles = await _provider(symbol).fetch_history(symbol.upper(), timeframe, total=300)
    df = candles_to_frame(candles)
    try:
        signal = service.analyze(df, symbol.upper(), service_exchange(symbol), timeframe)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "decision": signal.decision,
        "confidence": signal.confidence,
        **(await _checklist_for(signal, df)),
    }


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
    candles = req.candles or await _provider(req.symbol).fetch_history(req.symbol, req.timeframe, total=req.limit)
    df = candles_to_frame(candles)
    if len(df) < 60:
        raise HTTPException(422, "Need at least 60 candles for context.")
    features = service.feature_builder.build_frame(df)
    signal = service.analyze(df, req.symbol, service_exchange(req.symbol), req.timeframe)
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


def service_exchange(symbol: str = "BTCUSDT") -> str:
    return _provider(symbol).name


@app.get("/news")
async def news(symbol: str = Query(...), limit: int = Query(8, ge=1, le=20)):
    """Recent headlines + sentiment for a symbol.

    News is the one input that is NOT derived from price — it can move the market
    before the chart reacts. Free source (Yahoo RSS), no API key.
    """
    s = await fetch_news(symbol, limit=limit)
    return {
        "symbol": s.symbol,
        "score": s.score,
        "label": s.label,
        "emoji": s.mood_emoji,
        "headlines": [
            {"title": h.title, "link": h.link, "published": h.published, "score": h.score}
            for h in s.headlines
        ],
    }


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
    """Log the AI's own pick for the latest candle.

    Duplicates are rejected by the database itself (one AI pick per candle per
    market), so this is safe to call as often as we like.
    """
    import uuid
    from datetime import datetime, timezone

    from app.tracking.tracker import TrackedCall

    service: AnalysisService = app.state.service
    df = candles_to_frame(candles)
    pick = service.ai_paper_trade(df)
    if pick is None:
        return None
    call = TrackedCall(
        id=uuid.uuid4().hex[:8],
        created_at=datetime.now(tz=timezone.utc).isoformat(),
        symbol=symbol.upper(), timeframe=timeframe,
        side=pick["side"], entry=pick["entry"], stop=pick["stop"],
        tp1=pick["tp1"], tp2=pick["tp2"],
        clicked_time=int(candles[-1].open_time.timestamp()),
        clicked_price=pick["entry"], source="ai",
    )
    stored = app.state.calls.add(call)      # None if the DB deduped it
    return stored.id if stored else None


@app.post("/calls/ai")
async def log_ai_pick(symbol: str = Query(...), timeframe: str = "1h"):
    """Immediately log the AI's current pick so you can watch it play out."""
    candles = await _provider(symbol).fetch_history(symbol, timeframe, total=300)
    cid = _record_ai_call(symbol, timeframe, candles)
    return {"ok": cid is not None, "id": cid}


def _tf_seconds(tf: str) -> int:
    n = int("".join(c for c in tf if c.isdigit()) or 1)
    if tf.endswith("h"):
        return n * 3600
    if tf.endswith("d"):
        return n * 86400
    return n * 60


async def _autolog_loop() -> None:
    """Server-side auto-log: keeps predicting even with NO browser open.

    Runs for the whole life of the server. When auto-log is on it logs the AI's
    pick for the configured market; the DB's one-pick-per-candle rule means we
    can retry freely without creating duplicates.
    """
    while True:
        sleep_s = 10
        try:
            cfg = app.state.autolog
            if cfg.get("enabled"):
                symbol, tf = cfg["symbol"], cfg["timeframe"]
                candles = await _provider(symbol).fetch_history(symbol, tf, total=300)
                cid = _record_ai_call(symbol, tf, candles)
                if cid:
                    logger.info("Auto-logged AI pick %s (%s %s)", cid, symbol, tf)
                # check about twice per candle; duplicates are dropped by the DB
                sleep_s = max(20, _tf_seconds(tf) // 2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            logger.warning("auto-log loop error: %s", exc)
            sleep_s = 20
        await asyncio.sleep(sleep_s)


@app.post("/autolog")
async def set_autolog(
    enabled: bool = Query(...),
    symbol: str = Query("BTCUSDT"),
    timeframe: str = Query("1m"),
):
    """Turn hands-free AI auto-logging on/off.

    This runs on the SERVER, so it keeps logging the AI's picks even after you
    close the website.
    """
    app.state.autolog = {"enabled": enabled, "symbol": symbol.upper(), "timeframe": timeframe}
    logger.info("Auto-log %s for %s %s", "ON" if enabled else "OFF", symbol.upper(), timeframe)
    return app.state.autolog


@app.get("/autolog")
async def get_autolog():
    return getattr(app.state, "autolog", {"enabled": False, "symbol": "BTCUSDT", "timeframe": "1m"})


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
            candles = await _provider(symbol).fetch_history(symbol, tf, total=1000)
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
    candles = await _provider(symbol).fetch_history(symbol, timeframe, total=300)
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
async def patterns(
    symbol: str = Query(...),
    timeframe: str = "1h",
    limit: int = Query(300, ge=60, le=1000),
    full: bool = Query(True, description="Include the full library, not just the 14 core patterns"),
):
    """Per-candle candlestick patterns for drawing markers on the chart.

    ``full=True`` adds the extended library (Marubozu, Three White Soldiers,
    Abandoned Baby, Tasuki Gaps…). Those are display-only — the model is trained on
    the core set and does not see them.
    """
    from app.features.candlesticks import add_candlestick_patterns, detected_patterns
    from app.features.patterns_extra import add_extended_patterns, extended_detected

    candles = await _provider(symbol).fetch_history(symbol, timeframe, total=limit)
    df = candles_to_frame(candles)
    enriched = add_candlestick_patterns(df)
    if full:
        enriched = add_extended_patterns(enriched)

    out = []
    for ts, row in enriched.iterrows():
        found = detected_patterns(row)                       # core (the model sees these)
        if full:
            found += extended_detected(row)                  # extended (display only)
        if found:
            p = found[0]
            out.append({
                "time": int(ts.timestamp()),
                "name": p["name"].split(" / ")[0],           # short label
                "dir": p["dir"],
                "all": [f["name"] for f in found],           # everything on this bar
            })
    return {"symbol": symbol.upper(), "timeframe": timeframe, "patterns": out}


@app.get("/patterns/library")
async def pattern_library():
    """The whole catalogue — every pattern the platform can name, and whether the
    model actually learns from it."""
    from app.features.candlesticks import CANDLE_FEATURE_COLUMNS, PATTERN_INFO
    from app.features.patterns_extra import EXTENDED_PATTERN_INFO

    items = [
        {"name": n, "desc": d, "dir": dr, "rarity": "common",
         "model_sees": col in CANDLE_FEATURE_COLUMNS}
        for col, (n, d, dr) in PATTERN_INFO.items()
    ] + [
        {"name": n, "desc": d, "dir": dr, "rarity": r, "model_sees": False}
        for _, (n, d, dr, r) in EXTENDED_PATTERN_INFO.items()
    ]
    return {
        "total": len(items),
        "model_features": sum(1 for i in items if i["model_sees"]),
        "patterns": items,
    }


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
    client = _provider(symbol)
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
                # NB: auto-logging is handled by the server-side _autolog_loop, so it
                # keeps running even with no browser connected. Nothing to do here.
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
