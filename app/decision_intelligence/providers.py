"""Learning provider for the Composition Engine (Sprint 5 · Milestone 2).

Bridges the Composition Engine to the **Learning Engine** without the composition core depending on
it. :class:`LearningPipelineProvider` runs the Learning Engine's own read-only pipeline (dataset →
patterns → validation → recommendations) and returns the observations **relevant to one decision**
as a :class:`LearningView`. It **reuses** the Learning Engine verbatim — it computes **no** statistic
of its own — so the composition never duplicates or recalculates learning work.

It imports `app.learning` (a read surface) but **neither** the Prediction nor the Outcome engine.
On a thin corpus the Learning Engine honestly returns `INSUFFICIENT_DATA`, which flows straight
through to the composed learning section.
"""

from __future__ import annotations

from typing import Any

from app.decision_intelligence.compose import LearningView
from app.decision_intelligence.models import DecisionStatus
from app.learning.dataset import LearningDatasetBuilder
from app.learning.models import (
    DATASET_VERSION,
    DEFAULT_MIN_CORPUS,
    LEARNING_VERSION,
    LearningStatus,
)
from app.learning.patterns import DEFAULT_MIN_EVIDENCE, PatternExtractor
from app.learning.recommendations import RecommendationEngine
from app.learning.statistics import DEFAULT_MIN_SAMPLE, StatisticalValidator

#: Which validated-pattern grouping keys map to which prediction-record attribute (relevance).
_RELEVANCE: dict[str, str] = {
    "symbol": "symbol", "sector": "sector", "timeframe": "timeframe", "market_regime": "market_regime",
}


def _relevant(validated_pattern: Any, record: Any) -> bool:
    """Whether a validated pattern describes this decision's own setup (same dimension value)."""
    attr = _RELEVANCE.get(validated_pattern.grouping_key)
    return attr is not None and validated_pattern.grouping_value == getattr(record, attr, None)


class LearningPipelineProvider:
    """Runs the Learning Engine's read-only pipeline and projects the observations relevant to a
    decision. Deterministic (the Learning Engine is deterministic); reads only, writes nothing."""

    def __init__(
        self, retrieval: Any, *, min_corpus: int = DEFAULT_MIN_CORPUS,
        min_sample: int = DEFAULT_MIN_SAMPLE, min_evidence: int = DEFAULT_MIN_EVIDENCE,
    ) -> None:
        self._retrieval = retrieval
        self._min_corpus = min_corpus
        self._min_sample = min_sample
        self._min_evidence = min_evidence

    def observations_for(self, record: Any) -> LearningView:
        """The Learning Engine's validated observations relevant to ``record`` (verbatim)."""
        dataset = LearningDatasetBuilder(self._retrieval, min_corpus=self._min_corpus).build()
        if dataset.status is LearningStatus.INSUFFICIENT_DATA:
            return LearningView(status=DecisionStatus.INSUFFICIENT_DATA,
                                learning_version=LEARNING_VERSION, dataset_version=DATASET_VERSION)

        patterns = PatternExtractor(min_evidence=self._min_evidence).extract(dataset).patterns
        validation = StatisticalValidator(min_sample=self._min_sample).validate(dataset, patterns)
        recommendations = RecommendationEngine().generate(validation, patterns).recommendations

        by_key = {v.pattern_key: v for v in validation.validated_patterns}
        relevant = [r for r in recommendations
                    if (vp := by_key.get(r.pattern_key)) is not None and _relevant(vp, record)]
        status = DecisionStatus.COMPLETE if relevant else DecisionStatus.INSUFFICIENT_DATA
        return LearningView(
            status=status, recommendation_count=len(relevant), pattern_count=validation.validated_count,
            evidence_ids=tuple(r.recommendation_id for r in relevant),
            learning_version=LEARNING_VERSION, dataset_version=DATASET_VERSION,
        )
