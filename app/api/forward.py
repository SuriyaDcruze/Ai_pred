"""Forward Testing REST API (Sprint 1 · Milestone 4).

Exposes the Forward Testing Engine over ``/forward/*``. Every endpoint is a thin adapter
over the existing :class:`~app.forward_testing.store.PredictionStore` and
:class:`~app.forward_testing.engine.ForwardTestingEngine` — **no business logic and no
model calls live here**. In particular this router imports nothing from, and never
invokes, the Prediction Engine (``app/ai/sklearn_model.py``) or the Outcome Engine
(``app/ai/outcome_model.py``): it records recommendations those engines already produced
and reports how they turned out.

The store and engine are created once in the application lifespan and shared via
``request.app.state`` (``forward_store`` / ``forward_engine``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.schemas import ForwardPredictionRequest
from app.forward_testing.engine import ForwardTestingEngine
from app.forward_testing.models import PredictionRecord
from app.forward_testing.store import PredictionStore
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/forward", tags=["forward-testing"])

#: Below this many resolved trades the live sample proves nothing — we say so out loud.
_MIN_MEANINGFUL_SAMPLE = 50


# --------------------------------------------------------------------------- #
# Wiring helpers — read the shared store / engine off app.state.
# --------------------------------------------------------------------------- #
def _store(request: Request) -> PredictionStore:
    store = getattr(request.app.state, "forward_store", None)
    if store is None:  # pragma: no cover - misconfiguration, not a user error
        raise HTTPException(status_code=503, detail="Forward Testing store is not available")
    return store


def _engine(request: Request) -> ForwardTestingEngine:
    engine = getattr(request.app.state, "forward_engine", None)
    if engine is None:  # pragma: no cover - misconfiguration, not a user error
        raise HTTPException(status_code=503, detail="Forward Testing engine is not available")
    return engine


def _serialize(record: PredictionRecord) -> dict[str, Any]:
    """Flatten a record for JSON, keeping ``context`` as a nested object (not a string)."""
    data = record.to_row()
    data.pop("context_json", None)
    data["context"] = record.context or {}
    data["is_open"] = record.is_open()
    data["is_terminal"] = record.is_terminal()
    return data


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.post("/prediction", status_code=201)
async def create_prediction(req: ForwardPredictionRequest, request: Request) -> dict[str, Any]:
    """Record a recommendation for forward testing.

    Returns ``201`` with the stored record. A duplicate (same symbol, timeframe, origin
    candle and source) is rejected with ``409`` — that is deduplication, not a failure.
    """
    engine = _engine(request)
    stored = engine.record(
        symbol=req.symbol,
        exchange=req.exchange,
        timeframe=req.timeframe,
        current_price=req.current_price,
        direction=req.direction,
        recommendation=req.recommendation,
        created_candle_ts=req.created_candle_ts,
        entry=req.entry,
        stop=req.stop,
        target1=req.target1,
        target2=req.target2,
        direction_prob=req.direction_prob,
        outcome_prob=req.outcome_prob,
        decision_score=req.decision_score,
        market_regime=req.market_regime,
        market_phase=req.market_phase,
        sector=req.sector,
        session=req.session,
        volatility_bucket=req.volatility_bucket,
        similarity_score=req.similarity_score,
        context=req.context,
        prediction_model_version=req.prediction_model_version,
        outcome_model_version=req.outcome_model_version,
        feature_version=req.feature_version,
        source=req.source,
    )
    if stored is None:
        # recommendation is validated BUY/SELL upstream, so None here means a duplicate.
        raise HTTPException(
            status_code=409,
            detail="A prediction for this symbol, timeframe, candle and source already exists.",
        )
    return {"prediction": _serialize(stored)}


@router.get("/active")
async def list_active(
    request: Request,
    symbol: str | None = Query(None, description="Optional symbol filter."),
) -> dict[str, Any]:
    """Open predictions (PENDING / ENTRY_TRIGGERED / ACTIVE), oldest first."""
    records = _store(request).list_active(symbol=symbol)
    return {"count": len(records), "predictions": [_serialize(r) for r in records]}


@router.get("/completed")
async def list_completed(
    request: Request,
    limit: int | None = Query(None, ge=1, le=1000, description="Max records to return."),
    symbol: str | None = Query(None, description="Optional symbol filter."),
) -> dict[str, Any]:
    """Resolved predictions (terminal states), newest first."""
    records = _store(request).list_completed(limit=limit, symbol=symbol)
    return {"count": len(records), "predictions": [_serialize(r) for r in records]}


@router.get("/stats")
async def stats(
    request: Request,
    symbol: str | None = Query(None, description="Optional symbol filter."),
) -> dict[str, Any]:
    """Aggregate performance of resolved predictions (all figures in R-multiples)."""
    return _store(request).statistics(symbol=symbol)


@router.get("/summary")
async def summary(
    request: Request,
    symbol: str | None = Query(None, description="Optional symbol filter."),
) -> dict[str, Any]:
    """Stats plus an honest read on what the live sample does — and does not — prove."""
    data = _store(request).statistics(symbol=symbol)
    resolved = int(data.get("resolved", 0))

    if resolved == 0:
        confidence = "no_data"
        note = (
            "No forward-tested trades have resolved yet. The edge is backtest-only until a "
            "live sample accumulates."
        )
    elif resolved < _MIN_MEANINGFUL_SAMPLE:
        confidence = "insufficient_sample"
        note = (
            f"Only {resolved} live trade(s) have resolved — well below the "
            f"{_MIN_MEANINGFUL_SAMPLE}+ needed to trust a win rate. Treat these figures as "
            "provisional, not proof."
        )
    else:
        confidence = "building"
        note = (
            f"{resolved} live trades resolved. This is a real forward-tested sample, but "
            "keep comparing it against the backtest edge before drawing conclusions."
        )

    return {
        "stats": data,
        "confidence": confidence,
        "note": note,
        "min_meaningful_sample": _MIN_MEANINGFUL_SAMPLE,
        "disclaimer": (
            "Forward testing is not live trading and not a backtest. It measures whether the "
            "existing models keep their edge on unseen data. A backtest edge is not proven "
            "live until the resolved sample is large enough."
        ),
    }


# NOTE: the parameterised route is declared last so it never shadows the static routes
# above (/active, /completed, /stats, /summary).
@router.get("/prediction/{prediction_id}")
async def get_prediction(prediction_id: str, request: Request) -> dict[str, Any]:
    """One prediction by id, or ``404`` if it does not exist."""
    record = _store(request).get(prediction_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Prediction {prediction_id!r} not found")
    return {"prediction": _serialize(record)}
