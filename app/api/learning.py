"""Learning REST API (`/learning/*`, Sprint 4 · Vol 15 · Milestone 5).

A **thin transport layer** over the completed Behavioural Learning Engine (M1–M4). It validates
requests, **composes** the engine pipeline (Dataset → Patterns → Statistics → Recommendations),
and serialises deterministic responses. It contains **no analytics of its own**: every number is
produced by the domain engines. It never touches the database directly, never modifies Historical
Memory, never retrains or predicts, and imports neither the Prediction nor the Outcome engine.

**Statelessness = determinism.** Each request re-composes the (pure, read-only) pipeline over the
current corpus. Because every stage is deterministic, identical inputs always yield identical
content — so concurrent requests are consistent without shared mutable state. Responses carry the
domain checksums so callers (and tests) can assert determinism independently of the volatile
`generated_at` timestamp.

**Response rule:** only ids, honest statistics, evidence, and versions — never embeddings,
feature vectors, or internal reasoning.
"""

from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.learning.dataset import LearningDatasetBuilder
from app.learning.models import (
    DATASET_VERSION,
    DEFAULT_MIN_CORPUS,
    LEARNING_VERSION,
    ConfidenceInterval,
    LearningStatus,
    Recommendation,
    Significance,
    ValidatedPattern,
)
from app.learning.patterns import DEFAULT_MIN_EVIDENCE, PatternExtractor
from app.learning.recommendations import RecommendationEngine, recommendation_category
from app.learning.statistics import (
    CORRECTION_STRATEGIES,
    DEFAULT_ALPHA,
    DEFAULT_BASELINE,
    DEFAULT_CORRECTION,
    DEFAULT_MIN_SAMPLE,
    StatisticalValidator,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/learning", tags=["learning"])

#: The API response-schema version (the shape of these payloads). Bumped only on a breaking
#: response change; distinct from `learning_version` (the analysis method) and the dataset schema.
API_SCHEMA_VERSION: str = "1"
_MAX_PAGE = 500


# --------------------------------------------------------------------------- schemas
class LearningMeta(BaseModel):
    """Envelope metadata on every response — supports forward-compatible evolution."""

    schema_version: str
    api_schema_version: str
    learning_version: str
    dataset_version: str
    generated_at: str


class ConfidenceIntervalModel(BaseModel):
    low: float
    high: float
    width: float
    quality: str
    method: str = "wilson"
    level: float = 0.95


class SignificanceModel(BaseModel):
    p_value: float
    z_score: float
    baseline: float
    significant: bool
    test: str = "two_proportion_z"


class PatternModel(BaseModel):
    """A classified pattern with its full statistical validation result."""

    pattern_key: str
    pattern_type: str
    grouping_key: str
    grouping_value: str
    status: str
    sample_size: int
    wins: int
    losses: int
    win_rate: float
    loss_rate: float
    average_r: float
    expectancy: float
    profit_factor: float | None
    max_drawdown_r: float | None
    avg_holding_bars: float | None
    confidence_interval: ConfidenceIntervalModel
    significance: SignificanceModel
    correction_method: str
    correction_significant: bool
    consistency_score: float | None
    evidence_count: int
    recommendation_category: str


class RecommendationModel(BaseModel):
    """An evidence-bound descriptive recommendation object."""

    recommendation_id: str
    recommendation_key: str
    pattern_key: str
    pattern_hash: str
    recommendation_type: str
    recommendation_category: str
    title: str
    summary: str
    detailed_explanation: str
    statistical_basis: str
    evidence_count: int
    sample_size: int
    confidence_interval: ConfidenceIntervalModel
    significance: SignificanceModel
    consistency_score: float | None
    recommendation_confidence: str
    supporting_prediction_ids: list[str]
    limitations: list[str]


class EvidenceModel(BaseModel):
    recommendation_id: str
    recommendation_key: str
    pattern_key: str
    pattern_hash: str
    recommendation_type: str
    recommendation_category: str
    recommendation_confidence: str
    title: str
    sample_size: int
    evidence_count: int
    supporting_prediction_ids: list[str]
    statistical_basis: str
    confidence_interval: ConfidenceIntervalModel
    significance: SignificanceModel


class HealthResponse(BaseModel):
    enabled: bool
    engine_status: str
    learning_version: str
    schema_version: str
    api_schema_version: str
    corpus_size: int
    min_sample: int
    last_run_at: str | None = None


class SummaryResponse(BaseModel):
    meta: LearningMeta
    status: str
    corpus_size: int
    recommendation_count: int
    validated_pattern_count: int
    hypothesis_count: int
    insufficient_count: int
    checksums: dict[str, str]


class PatternsResponse(BaseModel):
    meta: LearningMeta
    status: str
    corpus_size: int
    total: int
    limit: int
    offset: int
    items: list[PatternModel]
    checksum: str


class RecommendationsResponse(BaseModel):
    meta: LearningMeta
    status: str
    corpus_size: int
    total: int
    limit: int
    offset: int
    confidence_distribution: dict[str, int]
    items: list[RecommendationModel]
    checksum: str


class EvidenceResponse(BaseModel):
    meta: LearningMeta
    evidence: EvidenceModel


class RunResponse(BaseModel):
    meta: LearningMeta
    run_id: str
    status: str
    corpus_size: int
    params: dict[str, Any]
    validated_pattern_count: int
    hypothesis_count: int
    insufficient_count: int
    recommendation_count: int
    confidence_distribution: dict[str, int]
    checksums: dict[str, str]


# --------------------------------------------------------------------------- params / helpers
def learning_params(
    min_sample: int = Query(DEFAULT_MIN_SAMPLE, ge=1, description="Minimum sample for a VALIDATED pattern."),
    min_corpus: int = Query(DEFAULT_MIN_CORPUS, ge=1, description="Minimum corpus for a usable dataset."),
    min_evidence: int = Query(DEFAULT_MIN_EVIDENCE, ge=1, description="Minimum group size for a candidate pattern."),
    correction: str = Query(DEFAULT_CORRECTION, description="Multiple-comparison correction strategy."),
    alpha: float = Query(DEFAULT_ALPHA, gt=0.0, lt=1.0, description="Significance threshold."),
    baseline: float = Query(DEFAULT_BASELINE, ge=0.0, le=1.0, description="Null win rate."),
    learning_version: str | None = Query(None, description="If set, must equal the engine's learning_version."),
    schema_version: str | None = Query(None, description="If set, must equal the dataset schema version."),
) -> dict[str, Any]:
    """Validate + collect the analysis parameters (a FastAPI dependency).

    Type/bound violations are rejected by FastAPI as 422; semantic problems are 400; a version
    the engine does not serve is 409.
    """
    if correction not in CORRECTION_STRATEGIES:
        raise HTTPException(status_code=400,
                            detail=f"unknown correction {correction!r}; known: {', '.join(CORRECTION_STRATEGIES)}")
    if learning_version is not None and learning_version != LEARNING_VERSION:
        raise HTTPException(status_code=409,
                            detail=f"learning_version {learning_version!r} != served {LEARNING_VERSION!r}")
    if schema_version is not None and schema_version != DATASET_VERSION:
        raise HTTPException(status_code=409,
                            detail=f"schema_version {schema_version!r} != served {DATASET_VERSION!r}")
    return {
        "min_sample": min_sample, "min_corpus": min_corpus, "min_evidence": min_evidence,
        "correction": correction, "alpha": alpha, "baseline": baseline,
    }


def pagination(
    limit: int = Query(50, ge=1, le=_MAX_PAGE, description="Page size."),
    offset: int = Query(0, ge=0, description="Rows to skip."),
) -> tuple[int, int]:
    return (limit, offset)


class _Pipeline:
    """One deterministic pass of the Learning pipeline over the current corpus (read-only)."""

    __slots__ = ("dataset", "patterns", "validation", "recommendations", "params", "run_id")

    def __init__(self, dataset, patterns, validation, recommendations, params) -> None:
        self.dataset = dataset
        self.patterns = patterns
        self.validation = validation
        self.recommendations = recommendations
        self.params = params
        key = "|".join([
            dataset.checksum, validation.checksum, recommendations.checksum,
            str(params["min_sample"]), str(params["min_corpus"]), str(params["min_evidence"]),
            params["correction"], str(params["alpha"]), str(params["baseline"]),
        ])
        self.run_id = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _retrieval(request: Request) -> Any:
    retrieval = getattr(request.app.state, "retrieval", None)
    if retrieval is None:
        raise HTTPException(status_code=503, detail="Historical Memory retrieval is unavailable")
    return retrieval


def _run_pipeline(request: Request, params: dict[str, Any]) -> _Pipeline:
    """Compose the domain engines (no analytics here) into one deterministic run."""
    retrieval = _retrieval(request)
    dataset = LearningDatasetBuilder(retrieval, min_corpus=params["min_corpus"]).build()
    patterns = PatternExtractor(min_evidence=params["min_evidence"]).extract(dataset).patterns
    validation = StatisticalValidator(
        min_sample=params["min_sample"], alpha=params["alpha"], baseline=params["baseline"],
        correction=params["correction"],
    ).validate(dataset, patterns)
    recommendations = RecommendationEngine().generate(validation, patterns)
    return _Pipeline(dataset, patterns, validation, recommendations, params)


def _meta(pipeline: _Pipeline) -> LearningMeta:
    return LearningMeta(
        schema_version=DATASET_VERSION, api_schema_version=API_SCHEMA_VERSION,
        learning_version=LEARNING_VERSION, dataset_version=DATASET_VERSION,
        generated_at=pipeline.recommendations.generated_at,
    )


@contextmanager
def _observe(endpoint: str) -> Iterator[None]:
    """Structured timing/status log — endpoint + duration + status only (never data)."""
    start = time.perf_counter()
    try:
        yield
        logger.info("learning api %s ok in %.1fms", endpoint, (time.perf_counter() - start) * 1000)
    except HTTPException as exc:
        logger.info("learning api %s -> %d in %.1fms", endpoint, exc.status_code,
                    (time.perf_counter() - start) * 1000)
        raise


def _ci(ci: ConfidenceInterval) -> ConfidenceIntervalModel:
    return ConfidenceIntervalModel(low=ci.low, high=ci.high, width=ci.width, quality=ci.quality,
                                   method=ci.method, level=ci.level)


def _sig(sig: Significance) -> SignificanceModel:
    return SignificanceModel(p_value=sig.p_value, z_score=sig.z_score, baseline=sig.baseline,
                             significant=sig.significant, test=sig.test)


def _pattern_model(vp: ValidatedPattern) -> PatternModel:
    return PatternModel(
        pattern_key=vp.pattern_key, pattern_type=vp.pattern_type, grouping_key=vp.grouping_key,
        grouping_value=vp.grouping_value, status=vp.status.value, sample_size=vp.sample_size,
        wins=vp.wins, losses=vp.losses, win_rate=vp.win_rate, loss_rate=vp.loss_rate,
        average_r=vp.average_r, expectancy=vp.expectancy, profit_factor=vp.profit_factor,
        max_drawdown_r=vp.max_drawdown_r, avg_holding_bars=vp.avg_holding_bars,
        confidence_interval=_ci(vp.confidence_interval), significance=_sig(vp.significance),
        correction_method=vp.correction_method, correction_significant=vp.correction_significant,
        consistency_score=vp.consistency_score, evidence_count=vp.evidence_count,
        recommendation_category=recommendation_category(vp.grouping_key),
    )


def _recommendation_model(rec: Recommendation) -> RecommendationModel:
    return RecommendationModel(
        recommendation_id=rec.recommendation_id, recommendation_key=rec.recommendation_key,
        pattern_key=rec.pattern_key, pattern_hash=rec.pattern_hash,
        recommendation_type=rec.recommendation_type.value,
        recommendation_category=rec.recommendation_category, title=rec.title, summary=rec.summary,
        detailed_explanation=rec.detailed_explanation, statistical_basis=rec.statistical_basis,
        evidence_count=rec.evidence_count, sample_size=rec.sample_size,
        confidence_interval=_ci(rec.confidence_interval), significance=_sig(rec.significance),
        consistency_score=rec.consistency_score,
        recommendation_confidence=rec.recommendation_confidence.value,
        supporting_prediction_ids=list(rec.supporting_prediction_ids),
        limitations=list(rec.limitations),
    )


def _filter_patterns(
    patterns: tuple[ValidatedPattern, ...], *, symbol: str | None, sector: str | None,
    timeframe: str | None, regime: str | None, status: str | None, category: str | None,
) -> list[ValidatedPattern]:
    """Deterministic dimension/status/category filtering; already ordered by pattern_key."""
    def on(vp: ValidatedPattern, key: str, value: str | None) -> bool:
        return value is None or (vp.grouping_key == key and vp.grouping_value == value)

    out = []
    for vp in patterns:
        if status is not None and vp.status.value != status:
            continue
        if not on(vp, "symbol", symbol) or not on(vp, "sector", sector):
            continue
        if not on(vp, "timeframe", timeframe) or not on(vp, "market_regime", regime):
            continue
        if category is not None and recommendation_category(vp.grouping_key) != category:
            continue
        out.append(vp)
    return out


def _touch_last_run(request: Request, when: str) -> None:
    state = getattr(request.app.state, "learning", None)
    if state is None:
        state = request.app.state.learning = {"last_run_at": None}
    state["last_run_at"] = when


# --------------------------------------------------------------------------- endpoints
@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Engine status + versions + corpus size + configured minimum sample + last run timestamp."""
    with _observe("GET /learning/health"):
        retrieval = getattr(request.app.state, "retrieval", None)
        corpus_size = 0
        if retrieval is not None:
            corpus_size = LearningDatasetBuilder(retrieval, min_corpus=1).build().corpus_size
        state = getattr(request.app.state, "learning", None)
        return HealthResponse(
            enabled=retrieval is not None,
            engine_status="ready" if retrieval is not None else "unavailable",
            learning_version=LEARNING_VERSION, schema_version=DATASET_VERSION,
            api_schema_version=API_SCHEMA_VERSION, corpus_size=corpus_size,
            min_sample=DEFAULT_MIN_SAMPLE, last_run_at=(state or {}).get("last_run_at"),
        )


@router.get("/summary", response_model=SummaryResponse)
async def summary(request: Request, params: dict = Depends(learning_params)) -> SummaryResponse:
    """Corpus size + versions + validated/hypothesis counts + recommendation count + status."""
    with _observe("GET /learning/summary"):
        pipe = _run_pipeline(request, params)
        v = pipe.validation
        return SummaryResponse(
            meta=_meta(pipe), status=v.status.value, corpus_size=pipe.dataset.corpus_size,
            recommendation_count=pipe.recommendations.recommendation_count,
            validated_pattern_count=v.validated_count, hypothesis_count=v.hypothesis_count,
            insufficient_count=v.insufficient_count,
            checksums={"dataset": pipe.dataset.checksum, "validation": v.checksum,
                       "recommendations": pipe.recommendations.checksum},
        )


@router.get("/patterns", response_model=PatternsResponse)
async def patterns(
    request: Request,
    params: dict = Depends(learning_params),
    page: tuple[int, int] = Depends(pagination),
    symbol: str | None = Query(None),
    sector: str | None = Query(None),
    timeframe: str | None = Query(None),
    regime: str | None = Query(None),
    status: str | None = Query(None, description="VALIDATED | HYPOTHESIS | INSUFFICIENT_DATA"),
    category: str | None = Query(None, description="recommendation category (e.g. 'Sector Observation')"),
) -> PatternsResponse:
    """Classified patterns (each with its statistics), filterable + deterministically paginated."""
    with _observe("GET /learning/patterns"):
        if status is not None and status not in {s.value for s in LearningStatus}:
            raise HTTPException(status_code=400, detail=f"invalid status filter {status!r}")
        limit, offset = page
        pipe = _run_pipeline(request, params)
        filtered = _filter_patterns(pipe.validation.validated_patterns, symbol=symbol, sector=sector,
                                    timeframe=timeframe, regime=regime, status=status, category=category)
        window = filtered[offset:offset + limit]
        checksum = hashlib.sha256(
            "|".join(vp.pattern_key for vp in filtered).encode("utf-8")).hexdigest()
        return PatternsResponse(
            meta=_meta(pipe), status=pipe.validation.status.value, corpus_size=pipe.dataset.corpus_size,
            total=len(filtered), limit=limit, offset=offset,
            items=[_pattern_model(vp) for vp in window], checksum=checksum,
        )


@router.get("/statistics", response_model=PatternsResponse)
async def statistics(
    request: Request,
    params: dict = Depends(learning_params),
    page: tuple[int, int] = Depends(pagination),
    symbol: str | None = Query(None),
    sector: str | None = Query(None),
    timeframe: str | None = Query(None),
    regime: str | None = Query(None),
    status: str | None = Query(None),
    category: str | None = Query(None),
) -> PatternsResponse:
    """Statistical validation results by pattern (sample size + CI + significance + correction +
    consistency). Same source as `/patterns`; a stats-first view."""
    with _observe("GET /learning/statistics"):
        if status is not None and status not in {s.value for s in LearningStatus}:
            raise HTTPException(status_code=400, detail=f"invalid status filter {status!r}")
        limit, offset = page
        pipe = _run_pipeline(request, params)
        filtered = _filter_patterns(pipe.validation.validated_patterns, symbol=symbol, sector=sector,
                                    timeframe=timeframe, regime=regime, status=status, category=category)
        window = filtered[offset:offset + limit]
        checksum = hashlib.sha256(
            "|".join(vp.pattern_key for vp in filtered).encode("utf-8")).hexdigest()
        return PatternsResponse(
            meta=_meta(pipe), status=pipe.validation.status.value, corpus_size=pipe.dataset.corpus_size,
            total=len(filtered), limit=limit, offset=offset,
            items=[_pattern_model(vp) for vp in window], checksum=checksum,
        )


@router.get("/recommendations", response_model=RecommendationsResponse)
async def recommendations(
    request: Request,
    params: dict = Depends(learning_params),
    page: tuple[int, int] = Depends(pagination),
    category: str | None = Query(None, description="filter by recommendation category"),
    confidence: str | None = Query(None, description="HIGH | MEDIUM | LOW"),
) -> RecommendationsResponse:
    """Evidence-bound descriptive recommendations, filterable + deterministically paginated."""
    with _observe("GET /learning/recommendations"):
        limit, offset = page
        pipe = _run_pipeline(request, params)
        recs = list(pipe.recommendations.recommendations)
        if category is not None:
            recs = [r for r in recs if r.recommendation_category == category]
        if confidence is not None:
            recs = [r for r in recs if r.recommendation_confidence.value == confidence]
        window = recs[offset:offset + limit]
        checksum = hashlib.sha256(
            "|".join(r.recommendation_key for r in recs).encode("utf-8")).hexdigest()
        return RecommendationsResponse(
            meta=_meta(pipe), status=pipe.recommendations.status.value,
            corpus_size=pipe.dataset.corpus_size, total=len(recs), limit=limit, offset=offset,
            confidence_distribution=pipe.recommendations.confidence_distribution,
            items=[_recommendation_model(r) for r in window], checksum=checksum,
        )


@router.get("/evidence/{recommendation_id}", response_model=EvidenceResponse)
async def evidence(
    recommendation_id: str, request: Request, params: dict = Depends(learning_params)
) -> EvidenceResponse:
    """The supporting trades + statistics + pattern identity behind one recommendation."""
    with _observe("GET /learning/evidence/{id}"):
        pipe = _run_pipeline(request, params)
        rec = next((r for r in pipe.recommendations.recommendations
                    if r.recommendation_id == recommendation_id), None)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"recommendation {recommendation_id!r} not found")
        return EvidenceResponse(
            meta=_meta(pipe),
            evidence=EvidenceModel(
                recommendation_id=rec.recommendation_id, recommendation_key=rec.recommendation_key,
                pattern_key=rec.pattern_key, pattern_hash=rec.pattern_hash,
                recommendation_type=rec.recommendation_type.value,
                recommendation_category=rec.recommendation_category,
                recommendation_confidence=rec.recommendation_confidence.value, title=rec.title,
                sample_size=rec.sample_size, evidence_count=rec.evidence_count,
                supporting_prediction_ids=list(rec.supporting_prediction_ids),
                statistical_basis=rec.statistical_basis,
                confidence_interval=_ci(rec.confidence_interval), significance=_sig(rec.significance),
            ),
        )


@router.post("/run", response_model=RunResponse)
async def run(request: Request, params: dict = Depends(learning_params)) -> RunResponse:
    """Run a Learning analysis pass — idempotent + deterministic (a stable ``run_id`` per corpus +
    params). Reads only; never modifies Historical Memory, retrains, or predicts."""
    with _observe("POST /learning/run"):
        pipe = _run_pipeline(request, params)
        _touch_last_run(request, pipe.recommendations.generated_at)
        v = pipe.validation
        return RunResponse(
            meta=_meta(pipe), run_id=pipe.run_id, status=v.status.value,
            corpus_size=pipe.dataset.corpus_size, params=params,
            validated_pattern_count=v.validated_count, hypothesis_count=v.hypothesis_count,
            insufficient_count=v.insufficient_count,
            recommendation_count=pipe.recommendations.recommendation_count,
            confidence_distribution=pipe.recommendations.confidence_distribution,
            checksums={"dataset": pipe.dataset.checksum, "validation": v.checksum,
                       "recommendations": pipe.recommendations.checksum},
        )
