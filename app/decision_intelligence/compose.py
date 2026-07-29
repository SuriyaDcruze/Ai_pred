"""Composition Engine for the Decision Intelligence Engine (Sprint 5 · Milestone 2).

The Composition Engine **assembles** a complete :class:`DecisionIntelligence` object by reading the
**already-produced** outputs of the four prior engines — the stored Prediction/Outcome/Risk verdict,
Historical Memory context, Similarity neighbours, and the Learning Engine's validated observations —
and placing each into its own section. It is a **deterministic orchestration layer only**: it
**re-runs no model, recomputes no statistic, regenerates no embedding, and rebuilds no memory** — it
consumes existing outputs verbatim (the anti-duplication guarantee). It writes nothing and imports
**neither** the Prediction nor the Outcome engine (every source is injected + duck-typed).

Milestone 2 is composition only — **no explanation, narrative, composite confidence, persistence,
caching, or REST API** (those are later milestones).

Determinism + graceful degradation:
- The pipeline runs the four sources in a **fixed order** (Prediction → Memory → Similarity →
  Learning); the object's sections are then stored in a stable order and fingerprinted by the M1
  SHA-256 checksum (volatile fields excluded), so identical inputs always yield an identical object.
- A source that **lacks data** contributes an `INSUFFICIENT_DATA` section — the whole object is never
  failed because one subsystem is thin (the honest young-system behaviour). A source that **errors**
  contributes an `ERROR` section. Only a **missing prediction** (the required anchor) raises.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Sequence

from app.decision_intelligence.models import (
    DecisionComponent,
    DecisionIntelligence,
    DecisionIntelligenceError,
    DecisionStatus,
    EvidenceRef,
    Provenance,
    Subsystem,
    UpstreamVersions,
    section_for,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

_VERSION_KEYS = (
    "prediction_model_version", "outcome_model_version", "feature_version",
    "embedding_version", "learning_version", "dataset_version",
)


# --------------------------------------------------------------------------- errors
class CompositionError(DecisionIntelligenceError):
    """Base class for composition failures."""


class MissingPredictionError(CompositionError):
    """The required prediction (the composition anchor) does not exist."""


# --------------------------------------------------------------------------- learning view
@dataclass(frozen=True)
class LearningView:
    """A subsystem-agnostic projection of the Learning Engine's observations for one decision —
    produced by a learning **provider** (see ``providers.py``) and consumed verbatim by
    :class:`LearningSource`. The Composition Engine never computes any of these numbers."""

    status: DecisionStatus
    recommendation_count: int = 0
    pattern_count: int = 0
    evidence_ids: tuple[str, ...] = ()
    learning_version: str | None = None
    dataset_version: str | None = None


# --------------------------------------------------------------------------- source adapters
class SourceAdapter(ABC):
    """One subsystem's read adapter — it reads **only its own** subsystem and returns **only its
    own** section. It may assemble; it may never transform another subsystem's data."""

    subsystem: Subsystem

    @abstractmethod
    def compose(self, prediction_id: str, record: Any) -> DecisionComponent:
        """Read this subsystem's existing output for ``prediction_id`` and return its section."""

    def versions(self, record: Any, component: DecisionComponent) -> dict[str, str | None]:
        """This source's contribution to the object's :class:`UpstreamVersions` (default: none)."""
        return {}


def _error_component(subsystem: Subsystem, exc: Exception) -> DecisionComponent:
    return DecisionComponent(
        subsystem=subsystem, section=section_for(subsystem), status=DecisionStatus.ERROR,
        provenance=Provenance(subsystem=subsystem), payload={"error": type(exc).__name__},
    )


class PredictionSource(SourceAdapter):
    """Reads the **stored** Prediction + Outcome + Risk verdict from the prediction store (verbatim).
    This is the composition anchor: the prediction must exist. Never invokes any model."""

    subsystem = Subsystem.PREDICTION

    def __init__(self, prediction_store: Any) -> None:
        self._store = prediction_store

    def fetch(self, prediction_id: str) -> Any:
        return self._store.get(prediction_id)

    def compose(self, prediction_id: str, record: Any) -> DecisionComponent:
        status = getattr(record.status, "value", record.status)
        payload = {                              # verbatim stored verdict — no timestamps (checksum)
            "direction": record.direction, "recommendation": record.recommendation,
            "direction_prob": record.direction_prob, "outcome_prob": record.outcome_prob,
            "decision_score": record.decision_score, "current_price": record.current_price,
            "entry": record.entry, "stop": record.stop,
            "target1": getattr(record, "target1", None), "target2": getattr(record, "target2", None),
            "market_regime": record.market_regime, "market_phase": record.market_phase,
            "sector": record.sector, "timeframe": record.timeframe, "status": status,
            "realised_r": record.realised_r, "holding_bars": record.holding_bars,
        }
        provenance = Provenance(
            subsystem=self.subsystem, source=prediction_id,
            subsystem_version=record.prediction_model_version, confidence=record.direction_prob,
        )
        return DecisionComponent(
            subsystem=self.subsystem, section=section_for(self.subsystem),
            status=DecisionStatus.COMPLETE, provenance=provenance, payload=payload,
            evidence=(EvidenceRef(kind="prediction", ref_id=prediction_id, subsystem=self.subsystem),),
        )

    def versions(self, record: Any, component: DecisionComponent) -> dict[str, str | None]:
        return {
            "prediction_model_version": record.prediction_model_version,
            "outcome_model_version": record.outcome_model_version,
            "feature_version": record.feature_version,
        }


class MemorySource(SourceAdapter):
    """Reads the composed Memory Record via ``RetrievalEngine`` (read-only). A thin/unresolved
    prediction carries little history → `INSUFFICIENT_DATA`; a resolved one → `COMPLETE`."""

    subsystem = Subsystem.HISTORICAL_MEMORY

    def __init__(self, retrieval: Any) -> None:
        self._retrieval = retrieval

    def compose(self, prediction_id: str, record: Any) -> DecisionComponent:
        try:
            present = self._retrieval.get_record(prediction_id) is not None
        except Exception:                        # noqa: BLE001 — unavailable degrades, never fails
            present = False
        resolved = record.realised_r is not None
        status = DecisionStatus.COMPLETE if (present and resolved) else DecisionStatus.INSUFFICIENT_DATA
        return DecisionComponent(
            subsystem=self.subsystem, section=section_for(self.subsystem), status=status,
            provenance=Provenance(subsystem=self.subsystem, source=prediction_id),
            payload={"record_present": present, "resolved": resolved},
            evidence=(EvidenceRef(kind="memory", ref_id=prediction_id, subsystem=self.subsystem),),
        )


class SimilaritySource(SourceAdapter):
    """Reads neighbours via ``RetrievalEngine.similar`` (read-only). Unavailable/empty →
    `INSUFFICIENT_DATA` (never a fabricated score); neighbours present → `COMPLETE` with honest stats."""

    subsystem = Subsystem.SIMILARITY

    def __init__(self, retrieval: Any, *, k: int = 5) -> None:
        self._retrieval = retrieval
        self._k = k

    def compose(self, prediction_id: str, record: Any) -> DecisionComponent:
        try:
            result = self._retrieval.similar(prediction_id, k=self._k)
        except Exception:                        # noqa: BLE001 — degrade gracefully
            result = None
        results = list(getattr(result, "results", None) or [])
        if result is None or not getattr(result, "available", False) or not results:
            return DecisionComponent(
                subsystem=self.subsystem, section=section_for(self.subsystem),
                status=DecisionStatus.INSUFFICIENT_DATA,
                provenance=Provenance(subsystem=self.subsystem, source=prediction_id),
                payload={"available": bool(getattr(result, "available", False)),
                         "reason": getattr(result, "reason", "")},
            )
        summary = getattr(result, "summary", None) or {}
        evidence = tuple(
            EvidenceRef(kind="neighbour", ref_id=str(n["prediction_id"]), subsystem=self.subsystem)
            for n in results if n.get("prediction_id")
        )
        provenance = Provenance(
            subsystem=self.subsystem, source=prediction_id,
            subsystem_version=results[0].get("embedding_version"), confidence=summary.get("win_rate"),
        )
        return DecisionComponent(
            subsystem=self.subsystem, section=section_for(self.subsystem),
            status=DecisionStatus.COMPLETE, provenance=provenance,
            payload={"neighbour_count": len(results), "sample_size": getattr(result, "sample_size", None),
                     "win_rate": summary.get("win_rate"), "avg_realised_r": summary.get("avg_realised_r")},
            evidence=evidence,
        )

    def versions(self, record: Any, component: DecisionComponent) -> dict[str, str | None]:
        return {"embedding_version": component.provenance.subsystem_version}


class LearningSource(SourceAdapter):
    """Reads the Learning Engine's observations via an injected **provider** (which returns a
    :class:`LearningView`). No provider (or no relevant observations) → `INSUFFICIENT_DATA`. The
    Composition Engine computes no learning statistic — the Learning Engine owns those."""

    subsystem = Subsystem.LEARNING

    def __init__(self, provider: Any = None) -> None:
        self._provider = provider

    def compose(self, prediction_id: str, record: Any) -> DecisionComponent:
        view: LearningView | None = None
        if self._provider is not None:
            try:
                view = self._provider.observations_for(record)
            except Exception:                    # noqa: BLE001 — degrade gracefully
                view = None
        if view is None:
            return DecisionComponent(
                subsystem=self.subsystem, section=section_for(self.subsystem),
                status=DecisionStatus.INSUFFICIENT_DATA,
                provenance=Provenance(subsystem=self.subsystem, source=prediction_id),
                payload={"available": False},
            )
        evidence = tuple(
            EvidenceRef(kind="recommendation", ref_id=str(i), subsystem=self.subsystem)
            for i in view.evidence_ids
        )
        return DecisionComponent(
            subsystem=self.subsystem, section=section_for(self.subsystem), status=view.status,
            provenance=Provenance(subsystem=self.subsystem, source=prediction_id,
                                  subsystem_version=view.learning_version),
            payload={"recommendation_count": view.recommendation_count,
                     "pattern_count": view.pattern_count, "dataset_version": view.dataset_version},
            evidence=evidence,
        )

    def versions(self, record: Any, component: DecisionComponent) -> dict[str, str | None]:
        return {"learning_version": component.provenance.subsystem_version,
                "dataset_version": (component.payload or {}).get("dataset_version")}


# --------------------------------------------------------------------------- the engine
class CompositionEngine:
    """Assembles the Decision Intelligence object from the four sources (deterministic, read-only).

    Stateless and re-entrant: it holds only its (immutable) sources and shares no mutable state, so
    concurrent composition of the same prediction yields identical objects.
    """

    def __init__(
        self, *, prediction: PredictionSource, memory: SourceAdapter, similarity: SourceAdapter,
        learning: SourceAdapter,
    ) -> None:
        self._prediction = prediction
        #: Fixed, deterministic pipeline order — Prediction → Memory → Similarity → Learning.
        self._pipeline: tuple[SourceAdapter, ...] = (prediction, memory, similarity, learning)

    def compose(self, prediction_id: str) -> DecisionIntelligence:
        """Compose the Decision Intelligence object for a prediction.

        Raises:
            MissingPredictionError: ``prediction_id`` is invalid or has no stored prediction.
        """
        if not isinstance(prediction_id, str) or not prediction_id:
            raise MissingPredictionError("prediction_id must be a non-empty string")
        record = self._prediction.fetch(prediction_id)
        if record is None:
            raise MissingPredictionError(f"prediction {prediction_id!r} not found")

        components: list[DecisionComponent] = []
        version_parts: dict[str, str | None] = {}
        for source in self._pipeline:
            try:
                component = source.compose(prediction_id, record)
            except Exception as exc:             # noqa: BLE001 — a source error is an ERROR section
                logger.info("composition: %s source errored (%s)", source.subsystem.value,
                            type(exc).__name__)
                component = _error_component(source.subsystem, exc)
            components.append(component)
            for key, value in source.versions(record, component).items():
                if value is not None:
                    version_parts[key] = value

        status = self._overall_status(components)
        upstream = UpstreamVersions(**{k: version_parts.get(k) for k in _VERSION_KEYS})
        result = DecisionIntelligence.create(
            prediction_id=prediction_id, status=status, upstream_versions=upstream,
            components=tuple(components),
        )
        logger.info("composition: %s status=%s sections=%s", prediction_id, status.value,
                    {c.subsystem.value: c.status.value for c in components})
        return result

    @staticmethod
    def _overall_status(components: Sequence[DecisionComponent]) -> DecisionStatus:
        """Prediction is the required anchor (present ⇒ at least PARTIAL); all COMPLETE ⇒ COMPLETE;
        any ERROR ⇒ ERROR; otherwise PARTIAL (graceful, prediction-only degradation)."""
        statuses = [c.status for c in components]
        if any(s is DecisionStatus.ERROR for s in statuses):
            return DecisionStatus.ERROR
        if all(s is DecisionStatus.COMPLETE for s in statuses):
            return DecisionStatus.COMPLETE
        return DecisionStatus.PARTIAL


def build_engine(
    *, prediction_store: Any, retrieval: Any, learning_provider: Any = None, similarity_k: int = 5,
) -> CompositionEngine:
    """Wire a :class:`CompositionEngine` over the real read surfaces (all injected, duck-typed).

    ``learning_provider`` is optional — without it the learning section is `INSUFFICIENT_DATA`
    (see ``app.decision_intelligence.providers.LearningPipelineProvider`` for the real one)."""
    return CompositionEngine(
        prediction=PredictionSource(prediction_store),
        memory=MemorySource(retrieval),
        similarity=SimilaritySource(retrieval, k=similarity_k),
        learning=LearningSource(learning_provider),
    )
