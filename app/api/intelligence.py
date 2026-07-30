"""Decision Intelligence REST API (`/intelligence/*`, Sprint 5 · Milestone 5).

A **thin, deterministic, read-only transport** over the completed Decision Intelligence Engine
(M2–M4). It validates a request, invokes the existing pipeline — **compose** (M2) → **explain**
(M3) → **assess confidence** (M4) — and serialises the result. It contains **no business logic**:
it composes nothing, explains nothing, computes no confidence, prioritises nothing, and never runs
a model, a search, the learning pipeline's maths, or touches the database directly. All logic lives
in `app/decision_intelligence/`; this layer only transports its results.

Route ownership (ADR 0016 discipline): this router **owns the `/intelligence/*` sub-namespace** and
declares its static routes (`/health`, `/version`, `/symbol/{symbol}`) **before** the
`/{prediction_id}` catch-all. It does **not** touch the legacy exact-path `GET /intelligence` (the
V3 live-analysis view) — a separate, distinct route.

**Response rule:** only existing composed information + versions + checksums — never a fabricated
field, never a wall-clock timestamp (so identical objects yield byte-identical responses). Imports
**neither** the Prediction nor the Outcome engine.
"""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.decision_intelligence.compose import MissingPredictionError, build_engine
from app.decision_intelligence.confidence import ConfidenceEngine
from app.decision_intelligence.evidence import EvidenceEngine
from app.decision_intelligence.models import (
    DECISION_INTELLIGENCE_VERSION,
    DecisionIntelligenceError,
)
from app.decision_intelligence.providers import LearningPipelineProvider
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/intelligence", tags=["decision-intelligence"])

#: The API response-schema version (the shape of these payloads); distinct from the DI method
#: version (`di-1`). Bumped only on a breaking response change.
API_VERSION: str = "1"
SCHEMA_VERSION: str = DECISION_INTELLIGENCE_VERSION

_PID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


# --------------------------------------------------------------------------- schemas
class VersionsModel(BaseModel):
    api_version: str
    decision_intelligence_version: str
    schema_version: str


class IntelligenceResponse(BaseModel):
    """The composed Decision Intelligence object + its evidence, explanation, confidence, and
    prioritisation — serialised faithfully (deterministic; no wall-clock)."""

    versions: VersionsModel
    decision: dict[str, Any]
    evidence: dict[str, Any]
    explanation: dict[str, Any]
    confidence: dict[str, Any]
    prioritisation: dict[str, Any]
    checksums: dict[str, str]


class HealthResponse(BaseModel):
    status: str
    ready: bool
    api_version: str
    decision_intelligence_version: str
    schema_version: str
    dependencies: dict[str, bool]


class VersionResponse(BaseModel):
    api_version: str
    decision_intelligence_version: str
    schema_version: str


# --------------------------------------------------------------------------- helpers
@contextmanager
def _observe(endpoint: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
        logger.info("intelligence api %s ok in %.1fms", endpoint, (time.perf_counter() - start) * 1000)
    except HTTPException as exc:
        logger.info("intelligence api %s -> %d in %.1fms", endpoint, exc.status_code,
                    (time.perf_counter() - start) * 1000)
        raise


def _deps(request: Request) -> tuple[Any, Any]:
    forward_store = getattr(request.app.state, "forward_store", None)
    retrieval = getattr(request.app.state, "retrieval", None)
    if forward_store is None or retrieval is None:
        raise HTTPException(status_code=503, detail="Decision Intelligence dependencies unavailable")
    return forward_store, retrieval


def _check_schema_version(schema_version: str | None) -> None:
    if schema_version is not None and schema_version != SCHEMA_VERSION:
        raise HTTPException(status_code=409,
                            detail=f"schema_version {schema_version!r} != served {SCHEMA_VERSION!r}")


def _versions() -> VersionsModel:
    return VersionsModel(api_version=API_VERSION,
                         decision_intelligence_version=DECISION_INTELLIGENCE_VERSION,
                         schema_version=SCHEMA_VERSION)


def _intelligence(request: Request, prediction_id: str) -> IntelligenceResponse:
    """Invoke the existing pipeline (compose → explain → assess) and serialise — no logic here."""
    forward_store, retrieval = _deps(request)
    engine = build_engine(prediction_store=forward_store, retrieval=retrieval,
                          learning_provider=LearningPipelineProvider(retrieval))
    try:
        decision = engine.compose(prediction_id)
        explained = EvidenceEngine().explain(decision)
        confidence = ConfidenceEngine().assess(decision, explained)
    except MissingPredictionError:
        raise HTTPException(status_code=404, detail=f"prediction {prediction_id!r} not found")
    except DecisionIntelligenceError as exc:               # malformed evidence/confidence — 422
        raise HTTPException(status_code=422, detail=str(exc))
    return _serialize(decision, explained, confidence)


def _serialize(decision: Any, explained: Any, confidence: Any) -> IntelligenceResponse:
    """Deterministic serialisation — stable content + checksums only (no wall-clock timestamp)."""
    return IntelligenceResponse(
        versions=_versions(),
        decision={**decision.stable_dict(), "checksum": decision.checksum},
        evidence={"graph": explained.evidence_graph.stable_dict(),
                  "provenance_map": explained.provenance_map, "checksum": explained.checksum},
        explanation=explained.explanation.stable_dict(),
        confidence=confidence.to_dict(),
        prioritisation={"score": confidence.prioritisation_score, "level": confidence.level.value},
        checksums={"decision": decision.checksum, "evidence": explained.checksum,
                   "confidence": confidence.checksum},
    )


# --------------------------------------------------------------------------- endpoints
@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Infrastructure readiness only — no business logic, no engine invocation."""
    with _observe("GET /intelligence/health"):
        forward_store = getattr(request.app.state, "forward_store", None)
        retrieval = getattr(request.app.state, "retrieval", None)
        ready = forward_store is not None and retrieval is not None
        return HealthResponse(
            status="ready" if ready else "unavailable", ready=ready, api_version=API_VERSION,
            decision_intelligence_version=DECISION_INTELLIGENCE_VERSION, schema_version=SCHEMA_VERSION,
            dependencies={"prediction_store": forward_store is not None,
                          "retrieval": retrieval is not None, "learning": retrieval is not None},
        )


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    """The API / Decision Intelligence / schema versions (compatibility is explicit)."""
    with _observe("GET /intelligence/version"):
        return VersionResponse(api_version=API_VERSION,
                               decision_intelligence_version=DECISION_INTELLIGENCE_VERSION,
                               schema_version=SCHEMA_VERSION)


@router.get("/symbol/{symbol}", response_model=IntelligenceResponse)
async def by_symbol(
    symbol: str, request: Request,
    schema_version: str | None = Query(None, description="If set, must equal the served schema version."),
) -> IntelligenceResponse:
    """Decision Intelligence for a symbol's **latest** stored prediction (deterministic ordering)."""
    with _observe("GET /intelligence/symbol/{symbol}"):
        if not _SYMBOL_RE.match(symbol):
            raise HTTPException(status_code=400, detail="invalid symbol format")
        _check_schema_version(schema_version)
        forward_store, _ = _deps(request)
        candidates = [p for p in forward_store.list_all() if p.symbol == symbol]
        if not candidates:
            raise HTTPException(status_code=404, detail=f"no prediction for symbol {symbol!r}")
        latest = max(candidates, key=lambda p: (p.created_at or "", p.prediction_id))
        return _intelligence(request, latest.prediction_id)


@router.get("/{prediction_id}", response_model=IntelligenceResponse)
async def by_prediction(
    prediction_id: str, request: Request,
    schema_version: str | None = Query(None, description="If set, must equal the served schema version."),
) -> IntelligenceResponse:
    """The complete Decision Intelligence object for one prediction (compose → explain → confidence)."""
    with _observe("GET /intelligence/{prediction_id}"):
        if not _PID_RE.match(prediction_id):
            raise HTTPException(status_code=400, detail="invalid prediction_id format")
        _check_schema_version(schema_version)
        return _intelligence(request, prediction_id)
