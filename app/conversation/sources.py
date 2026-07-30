"""Decision Intelligence source adapters for the Retrieval Orchestrator (Sprint 6 · Milestone 3).

Concrete implementations of :class:`DecisionIntelligenceSource` that fetch the composed Decision
Intelligence payload **through the Decision Intelligence Engine** (Sprint 5) — the same compose →
explain → assess pipeline the `/intelligence/*` API serves. This keeps the orchestrator core
transport-independent: swap this adapter for a REST/RPC one without changing orchestration.

`InProcessSource` reaches Decision Intelligence via its public composition API only; it does not read
the Prediction/Memory/Similarity/Learning engines independently, and it imports **neither** the
Prediction nor the Outcome engine.
"""

from __future__ import annotations

from typing import Any

from app.conversation.retrieval import DecisionIntelligenceSource
from app.decision_intelligence.compose import MissingPredictionError, build_engine
from app.decision_intelligence.confidence import ConfidenceEngine
from app.decision_intelligence.evidence import EvidenceEngine
from app.decision_intelligence.models import DECISION_INTELLIGENCE_VERSION
from app.decision_intelligence.providers import LearningPipelineProvider

_API_VERSION = "1"


class InProcessSource(DecisionIntelligenceSource):
    """Fetches Decision Intelligence in-process (compose → explain → assess), serialised to the same
    deterministic payload the `/intelligence/*` API returns (stable content + checksums)."""

    def __init__(self, prediction_store: Any, retrieval: Any, *, learning_provider: Any = None) -> None:
        self._store = prediction_store
        self._retrieval = retrieval
        self._learning_provider = learning_provider or LearningPipelineProvider(retrieval)

    def _engine(self) -> Any:
        return build_engine(prediction_store=self._store, retrieval=self._retrieval,
                            learning_provider=self._learning_provider)

    def _payload(self, prediction_id: str) -> dict[str, Any]:
        decision = self._engine().compose(prediction_id)
        explained = EvidenceEngine().explain(decision)
        confidence = ConfidenceEngine().assess(decision, explained)
        return {
            "versions": {"api_version": _API_VERSION,
                         "decision_intelligence_version": DECISION_INTELLIGENCE_VERSION,
                         "schema_version": DECISION_INTELLIGENCE_VERSION},
            "decision": {**decision.stable_dict(), "checksum": decision.checksum},
            "evidence": {"graph": explained.evidence_graph.stable_dict(),
                         "provenance_map": explained.provenance_map, "checksum": explained.checksum},
            "explanation": explained.explanation.stable_dict(),
            "confidence": confidence.to_dict(),
            "prioritisation": {"score": confidence.prioritisation_score, "level": confidence.level.value},
        }

    def fetch_decision(self, prediction_id: str) -> dict[str, Any] | None:
        try:
            return self._payload(prediction_id)
        except MissingPredictionError:
            return None

    def fetch_decision_by_symbol(self, symbol: str) -> dict[str, Any] | None:
        candidates = [p for p in self._store.list_all() if p.symbol == symbol]
        if not candidates:
            return None
        latest = max(candidates, key=lambda p: (p.created_at or "", p.prediction_id))
        return self.fetch_decision(latest.prediction_id)

    def fetch_health(self) -> dict[str, Any]:
        ready = self._store is not None and self._retrieval is not None
        return {"status": "ready" if ready else "unavailable", "ready": ready,
                "decision_intelligence_version": DECISION_INTELLIGENCE_VERSION,
                "schema_version": DECISION_INTELLIGENCE_VERSION, "api_version": _API_VERSION}

    def fetch_version(self) -> dict[str, Any]:
        return {"api_version": _API_VERSION,
                "decision_intelligence_version": DECISION_INTELLIGENCE_VERSION,
                "schema_version": DECISION_INTELLIGENCE_VERSION}
