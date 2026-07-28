"""Similarity REST API (`/memory/similar*`, Sprint 3 · Vol 14 · Milestone 5).

A **thin transport layer** over the completed Similarity Engine — it validates input, maps
domain errors to HTTP, and serialises results. It contains **no search algorithm**: all
similarity logic lives in `SimilaritySearchEngine` (M3) / `RetrievalEngine` (M4). It never
touches the database directly and imports neither the Prediction nor Outcome engine.

The domain objects are created once in the app lifespan and shared via ``request.app.state``
(``similarity_engine`` + ``retrieval``). This one router owns every ``/memory/similar*`` route
so ``/health`` and ``/search`` are matched before the ``/{prediction_id}`` catch-all.

**Response rule:** never expose raw embeddings, feature vectors, or internal hashing — only
prediction ids, similarity scores, honest outcomes/stats, and engine versions.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.similarity.embedding import EMBEDDING_VERSION
from app.similarity.feature_vector import FEATURE_VERSION, VECTOR_DIM
from app.similarity.models import (
    DimensionMismatchError,
    MissingEmbeddingError,
    SearchRequestError,
    UnsupportedVersionError,
)
from app.similarity.search import SIMILARITY_VERSION, SimilarityFilter
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/memory/similar", tags=["similarity"])

_MAX_TOP_K = 200


# --------------------------------------------------------------------------- schemas
class SimilaritySearchRequest(BaseModel):
    """Body for ``POST /memory/similar/search`` — the search target + candidate filters."""

    prediction_id: str
    top_k: int = 10          # bounds enforced in the handler (400), for a consistent taxonomy
    threshold: float = -1.0
    symbol: str | None = None
    sector: str | None = None
    timeframe: str | None = None
    market_regime: str | None = None
    market_phase: str | None = None
    outcome: str | None = None
    prediction_model_version: str | None = None
    feature_version: str | None = None


class NeighbourModel(BaseModel):
    prediction_id: str
    similarity_score: float
    confidence: float | None = None
    outcome: str | None = None
    realised_r: float | None = None
    holding_period: int | None = None
    symbol: str | None = None
    sector: str | None = None
    market_regime: str | None = None
    market_phase: str | None = None
    timeframe: str | None = None
    embedding_version: str
    feature_version: str


class SimilarityResponse(BaseModel):
    available: bool
    reason: str = ""
    prediction_id: str | None = None
    neighbours: list[NeighbourModel] = []
    sample_size: int = 0
    summary: dict[str, Any] | None = None
    versions: dict[str, Any] = {}
    metadata: dict[str, Any] | None = None


class SimilarityHealthResponse(BaseModel):
    enabled: bool
    embedding_version: str
    feature_version: str
    vector_dimension: int
    search_version: str


# --------------------------------------------------------------------------- helpers
def _engine(request: Request) -> Any:
    engine = getattr(request.app.state, "similarity_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Similarity Engine unavailable")
    return engine


def _retrieval(request: Request) -> Any:
    retrieval = getattr(request.app.state, "retrieval", None)
    if retrieval is None:  # pragma: no cover - misconfiguration
        raise HTTPException(status_code=503, detail="Historical Memory retrieval is not available")
    return retrieval


@contextmanager
def _observe(endpoint: str, prediction_id: str | None = None) -> Iterator[None]:
    """Structured timing/status log — identifiers only, never embeddings or vectors."""
    start = time.perf_counter()
    try:
        yield
        logger.info("similarity api %s ok in %.1fms", endpoint, (time.perf_counter() - start) * 1000)
    except HTTPException as exc:
        logger.info("similarity api %s -> %d in %.1fms", endpoint, exc.status_code, (time.perf_counter() - start) * 1000)
        raise


def _validate(top_k: int, threshold: float) -> None:
    if not (1 <= top_k <= _MAX_TOP_K):
        raise HTTPException(status_code=400, detail=f"top_k must be within 1..{_MAX_TOP_K}")
    if not (-1.0 <= threshold <= 1.0):
        raise HTTPException(status_code=400, detail="threshold must be within [-1, 1]")


def _neighbour_dict(n: Any) -> dict[str, Any]:
    """Serialise one neighbour — outcome facts + versions, **never** the raw vector."""
    return {
        "prediction_id": n.prediction_id,
        "similarity_score": n.similarity_score,
        "confidence": n.confidence,
        "outcome": n.outcome,
        "realised_r": n.realised_r,
        "holding_period": n.holding_bars,
        "symbol": n.symbol,
        "sector": n.sector,
        "market_regime": n.market_regime,
        "market_phase": n.market_phase,
        "timeframe": n.timeframe,
        "embedding_version": n.embedding_version,
        "feature_version": n.feature_version,
    }


def _serialize(result: Any, prediction_id: str | None) -> SimilarityResponse:
    """Map a domain ``SimilaritySearchResult`` to the API contract."""
    s = result.summary
    return SimilarityResponse(
        available=True,
        reason="",
        prediction_id=prediction_id,
        neighbours=[_neighbour_dict(n) for n in result.neighbours],
        sample_size=s.sample_size,
        summary={
            "sample_size": s.sample_size, "resolved": s.resolved, "win_rate": s.win_rate,
            "avg_realised_r": s.avg_realised_r, "outcome_distribution": s.outcome_distribution,
        },
        versions={
            "embedding_version": EMBEDDING_VERSION, "feature_version": result.feature_version,
            "similarity_version": result.similarity_version, "vector_dimension": VECTOR_DIM,
        },
        metadata={
            "metric": result.metric, "candidate_count": result.candidate_count,
            "returned": result.returned, "cap_applied": result.cap_applied,
        },
    )


def _run(request: Request, prediction_id: str, top_k: int, threshold: float, filter: SimilarityFilter) -> SimilarityResponse:
    """Validate → search (domain) → serialise, mapping domain errors to HTTP codes."""
    _validate(top_k, threshold)
    engine = _engine(request)
    if _retrieval(request).predictions.get(prediction_id) is None:
        raise HTTPException(status_code=404, detail=f"prediction {prediction_id!r} not found")
    try:
        result = engine.search_by_prediction(prediction_id, k=top_k, filter=filter, min_similarity=threshold)
    except MissingEmbeddingError:
        raise HTTPException(status_code=404, detail=f"no embedding for prediction {prediction_id!r}")
    except (UnsupportedVersionError, DimensionMismatchError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except SearchRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _serialize(result, prediction_id)


def _filter(**kwargs: Any) -> SimilarityFilter:
    return SimilarityFilter(**kwargs)


# --------------------------------------------------------------------------- endpoints
@router.get("/health", response_model=SimilarityHealthResponse)
async def health(request: Request) -> SimilarityHealthResponse:
    """Similarity Engine status + versions (no database access)."""
    with _observe("GET /memory/similar/health"):
        return SimilarityHealthResponse(
            enabled=getattr(request.app.state, "similarity_engine", None) is not None,
            embedding_version=EMBEDDING_VERSION,
            feature_version=FEATURE_VERSION,
            vector_dimension=VECTOR_DIM,
            search_version=SIMILARITY_VERSION,
        )


@router.post("/search", response_model=SimilarityResponse)
async def search(req: SimilaritySearchRequest, request: Request) -> SimilarityResponse:
    """Ranked neighbours for a search target described in the request body."""
    with _observe("POST /memory/similar/search", req.prediction_id):
        return _run(
            request, req.prediction_id, req.top_k, req.threshold,
            _filter(symbol=req.symbol, sector=req.sector, timeframe=req.timeframe,
                    market_regime=req.market_regime, market_phase=req.market_phase, outcome=req.outcome,
                    prediction_model_version=req.prediction_model_version, feature_version=req.feature_version),
        )


@router.get("", response_model=SimilarityResponse)
async def similar_query(
    request: Request,
    prediction_id: str | None = Query(None, description="The query prediction (similarity target)."),
    top_k: int = Query(10),
    threshold: float = Query(-1.0),
    symbol: str | None = Query(None),
    sector: str | None = Query(None),
    timeframe: str | None = Query(None),
    market_regime: str | None = Query(None),
    market_phase: str | None = Query(None),
    outcome: str | None = Query(None),
    prediction_model_version: str | None = Query(None),
    feature_version: str | None = Query(None),
) -> SimilarityResponse:
    """Ranked neighbours for a query prediction given as a query parameter (+ candidate filters)."""
    with _observe("GET /memory/similar", prediction_id):
        if not prediction_id:
            raise HTTPException(status_code=400, detail="prediction_id query parameter is required")
        return _run(
            request, prediction_id, top_k, threshold,
            _filter(symbol=symbol, sector=sector, timeframe=timeframe, market_regime=market_regime,
                    market_phase=market_phase, outcome=outcome,
                    prediction_model_version=prediction_model_version, feature_version=feature_version),
        )


@router.get("/{prediction_id}", response_model=SimilarityResponse)
async def similar_by_id(
    prediction_id: str,
    request: Request,
    top_k: int = Query(10),
    threshold: float = Query(-1.0),
    symbol: str | None = Query(None),
    sector: str | None = Query(None),
    timeframe: str | None = Query(None),
    market_regime: str | None = Query(None),
    market_phase: str | None = Query(None),
    outcome: str | None = Query(None),
    prediction_model_version: str | None = Query(None),
    feature_version: str | None = Query(None),
) -> SimilarityResponse:
    """Ranked neighbours of a prediction (path target) with its honest summary + versions."""
    with _observe("GET /memory/similar/{id}", prediction_id):
        return _run(
            request, prediction_id, top_k, threshold,
            _filter(symbol=symbol, sector=sector, timeframe=timeframe, market_regime=market_regime,
                    market_phase=market_phase, outcome=outcome,
                    prediction_model_version=prediction_model_version, feature_version=feature_version),
        )
