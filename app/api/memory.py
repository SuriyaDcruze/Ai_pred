"""Historical Memory REST API (`/memory/*`, Sprint 2 · Milestone 5).

A **thin transport layer** over the Retrieval Engine and Memory Builder — the thin-controller
pattern (ADR 0006). Handlers validate input, call the domain, and shape JSON; they hold **no
business logic**, never touch the database directly, and import neither the Prediction nor the
Outcome engine. All domain logic stays in ``PredictionStore`` / ``MemoryStore`` /
``MemoryBuilder`` / ``RetrievalEngine``, which are created once in the app lifespan and shared
via ``request.app.state``.

Errors are mapped to consistent HTTP status codes (404 unknown, 422 invalid input, 500
internal) and never expose a stack trace.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.memory.builder import BuildStatus, MemoryBuilder
from app.memory.errors import MemoryNotFoundError, MemoryQueryError, MemoryStoreError
from app.memory.models import AggregateDimension, MemoryAggregate
from app.memory.retrieval import MemoryFilter, RetrievalEngine
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/memory", tags=["historical-memory"])

_MAX_LIMIT = 500


# --------------------------------------------------------------------------- #
# Response models (OpenAPI documentation). The composed Memory Record is a deep,
# forward-compatible object, so record/search/timeline/context return structured
# dicts; the operational endpoints get explicit models.
# --------------------------------------------------------------------------- #
class SearchResponse(BaseModel):
    count: int
    next_cursor: str | None = None
    records: list[dict[str, Any]]


class StatisticsResponse(BaseModel):
    dimension: str | None = None
    count: int
    total_resolved: int = Field(..., description="Sample size across the returned rollups.")
    aggregates: list[dict[str, Any]]


class SimilarityResponse(BaseModel):
    available: bool
    reason: str
    results: list[dict[str, Any]] = []


class BuildResponse(BaseModel):
    prediction_id: str
    status: str


class BackfillResponse(BaseModel):
    processed: int
    built: int
    skipped: int
    failed: int


class RebuildResponse(BaseModel):
    rebuilt_rows: int


# --------------------------------------------------------------------------- helpers
def _retrieval(request: Request) -> RetrievalEngine:
    engine = getattr(request.app.state, "retrieval", None)
    if engine is None:  # pragma: no cover - misconfiguration, not a user error
        raise HTTPException(status_code=503, detail="Historical Memory retrieval is not available")
    return engine


def _builder(request: Request) -> MemoryBuilder:
    builder = getattr(request.app.state, "memory_builder", None)
    if builder is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="Historical Memory builder is not available")
    return builder


def _http_from(exc: MemoryStoreError) -> HTTPException:
    """Map a domain error to a consistent HTTP response (never leaking a stack trace)."""
    if isinstance(exc, MemoryNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MemoryQueryError):
        return HTTPException(status_code=422, detail=str(exc))
    logger.warning("memory api: internal error: %s", exc)
    return HTTPException(status_code=500, detail="historical memory operation failed")


@contextmanager
def _observe(endpoint: str, prediction_id: str | None = None) -> Iterator[None]:
    """Structured timing/log for one request — endpoint, duration, and (later) status.

    Logs identifiers only, never prediction content.
    """
    start = time.perf_counter()
    try:
        yield
        logger.info(
            "memory api %s ok in %.1fms%s",
            endpoint, (time.perf_counter() - start) * 1000,
            f" [{prediction_id}]" if prediction_id else "",
        )
    except HTTPException as exc:
        logger.info(
            "memory api %s -> %d in %.1fms%s",
            endpoint, exc.status_code, (time.perf_counter() - start) * 1000,
            f" [{prediction_id}]" if prediction_id else "",
        )
        raise


def _aggregate_dict(a: MemoryAggregate) -> dict[str, Any]:
    return {
        "dimension": a.dimension.value, "bucket": a.bucket, "model_version": a.model_version,
        "n_resolved": a.n_resolved, "wins": a.wins, "losses": a.losses,
        "win_rate": a.win_rate, "avg_r": a.avg_r, "expectancy": a.expectancy,
        "total_r": a.total_r, "profit_factor": a.profit_factor,
        "max_drawdown_r": a.max_drawdown_r, "avg_holding_bars": a.avg_holding_bars,
    }


# --------------------------------------------------------------------------- read endpoints
@router.get("/record/{prediction_id}")
async def get_record(prediction_id: str, request: Request) -> dict[str, Any]:
    """One fully composed Memory Record (404 if the prediction does not exist)."""
    with _observe("GET /memory/record", prediction_id):
        try:
            return {"record": _retrieval(request).get_record(prediction_id).to_dict()}
        except MemoryStoreError as exc:
            raise _http_from(exc)


@router.get("/search", response_model=SearchResponse)
async def search(
    request: Request,
    symbol: str | None = Query(None),
    timeframe: str | None = Query(None),
    sector: str | None = Query(None),
    market_regime: str | None = Query(None),
    outcome: str | None = Query(None, description="Status value or WIN/LOSS alias."),
    prediction_model_version: str | None = Query(None),
    outcome_model_version: str | None = Query(None),
    feature_version: str | None = Query(None),
    confidence_min: float | None = Query(None, ge=0.0, le=1.0),
    confidence_max: float | None = Query(None, ge=0.0, le=1.0),
    date_from: str | None = Query(None, description="ISO-8601, inclusive."),
    date_to: str | None = Query(None, description="ISO-8601, inclusive."),
    limit: int = Query(50, ge=1, le=_MAX_LIMIT),
    cursor: str | None = Query(None),
) -> SearchResponse:
    """Filtered, keyset-paginated search over Historical Memory."""
    with _observe("GET /memory/search"):
        filter = MemoryFilter(
            symbol=symbol, timeframe=timeframe, sector=sector, market_regime=market_regime,
            outcome=outcome, prediction_model_version=prediction_model_version,
            outcome_model_version=outcome_model_version, feature_version=feature_version,
            confidence_min=confidence_min, confidence_max=confidence_max,
            date_from=date_from, date_to=date_to,
        )
        try:
            page = _retrieval(request).search(filter, limit=limit, cursor=cursor)
        except MemoryStoreError as exc:
            raise _http_from(exc)
        return SearchResponse(
            count=page.count, next_cursor=page.next_cursor,
            records=[r.to_dict() for r in page.records],
        )


@router.get("/statistics", response_model=StatisticsResponse)
async def statistics(
    request: Request,
    dimension: str | None = Query(None, description="overall|symbol|sector|timeframe|regime|confidence_bucket|outcome"),
    bucket: str | None = Query(None),
    model_version: str = Query(""),
) -> StatisticsResponse:
    """Aggregate statistics (read-only; never computed in the API). Always reports sample size."""
    with _observe("GET /memory/statistics"):
        dim: AggregateDimension | None = None
        if dimension is not None:
            try:
                dim = AggregateDimension(dimension)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"unknown dimension {dimension!r}")
        try:
            aggregates = _retrieval(request).aggregates(dim, bucket, model_version)
        except MemoryStoreError as exc:
            raise _http_from(exc)
        # Sample size from the combined (all-model) rollups only, so a prediction counted in
        # both its combined and per-model rows is not double-counted; fall back to the
        # returned rows when the query was already scoped to a specific model version.
        combined = [a for a in aggregates if a.model_version == ""]
        total_resolved = sum(a.n_resolved for a in (combined or aggregates))
        return StatisticsResponse(
            dimension=dimension,
            count=len(aggregates),
            total_resolved=total_resolved,
            aggregates=[_aggregate_dict(a) for a in aggregates],
        )


@router.get("/timeline", response_model=SearchResponse)
async def timeline(
    request: Request,
    symbol: str | None = Query(None),
    date_from: str | None = Query(None, alias="from", description="ISO-8601, inclusive."),
    date_to: str | None = Query(None, alias="to", description="ISO-8601, inclusive."),
    limit: int = Query(50, ge=1, le=_MAX_LIMIT),
) -> SearchResponse:
    """Chronological Memory Records (newest first) for a symbol / date window."""
    with _observe("GET /memory/timeline"):
        filter = MemoryFilter(symbol=symbol, date_from=date_from, date_to=date_to)
        try:
            page = _retrieval(request).search(filter, limit=limit)
        except MemoryStoreError as exc:
            raise _http_from(exc)
        return SearchResponse(
            count=page.count, next_cursor=page.next_cursor,
            records=[r.to_dict() for r in page.records],
        )


@router.get("/similar/{prediction_id}", response_model=SimilarityResponse)
async def similar(prediction_id: str, request: Request, k: int = Query(5, ge=1, le=100)) -> SimilarityResponse:
    """Similar historical decisions — the documented **unavailable** contract (no fake scores)."""
    with _observe("GET /memory/similar", prediction_id):
        try:
            result = _retrieval(request).similar(prediction_id, k=k)
        except MemoryStoreError as exc:
            raise _http_from(exc)
        return SimilarityResponse(available=result.available, reason=result.reason, results=result.results)


@router.get("/context")
async def context(
    request: Request,
    symbol: str | None = Query(None),
    k: int = Query(5, ge=1, le=100),
) -> dict[str, Any]:
    """A bounded, deterministic GPT grounding bundle (records + aggregate + sample size + metadata)."""
    with _observe("GET /memory/context"):
        try:
            return _retrieval(request).gpt_context(symbol=symbol, k=k)
        except MemoryStoreError as exc:
            raise _http_from(exc)


# --------------------------------------------------------------------------- write (build) endpoints
@router.post("/build/{prediction_id}", response_model=BuildResponse)
async def build(prediction_id: str, request: Request) -> BuildResponse:
    """Enrich one completed prediction (idempotent). 404 if the prediction does not exist."""
    with _observe("POST /memory/build", prediction_id):
        try:
            status = _builder(request).build(prediction_id)
        except MemoryStoreError as exc:
            raise _http_from(exc)
        if status is BuildStatus.SKIPPED_MISSING:
            raise HTTPException(status_code=404, detail=f"unknown prediction {prediction_id!r}")
        return BuildResponse(prediction_id=prediction_id, status=status.value)


@router.post("/backfill", response_model=BackfillResponse)
async def backfill(request: Request, limit: int | None = Query(None, ge=1, le=10_000)) -> BackfillResponse:
    """Enrich all completed predictions with no memory yet (idempotent)."""
    with _observe("POST /memory/backfill"):
        try:
            summary = _builder(request).backfill(limit=limit)
        except MemoryStoreError as exc:
            raise _http_from(exc)
        return BackfillResponse(
            processed=summary.scanned, built=summary.built,
            skipped=summary.skipped, failed=summary.failed,
        )


@router.post("/rebuild-aggregates", response_model=RebuildResponse)
async def rebuild_aggregates(request: Request) -> RebuildResponse:
    """Recompute all derived aggregates from source (idempotent repair path)."""
    with _observe("POST /memory/rebuild-aggregates"):
        try:
            written = _builder(request).refresh_aggregates()
        except MemoryStoreError as exc:
            raise _http_from(exc)
        return RebuildResponse(rebuilt_rows=written)
