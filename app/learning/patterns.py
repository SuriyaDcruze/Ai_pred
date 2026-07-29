"""Pattern Extraction Engine — recurring historical conditions (Sprint 4 · Vol 15 · M2).

Discovers **candidate behavioural patterns** by grouping the deterministic Learning Dataset
(M1) along setup dimensions. This is **descriptive analytics only**: it computes **no
statistics, significance, confidence intervals, or recommendations** and exposes no HTTP. It is
a **pure, read-only transform** over the (frozen) Learning Dataset — it modifies nothing, writes
nothing, and imports neither the Prediction nor the Outcome engine.

Determinism: pattern ids are a function of their identity (not random), records group
deterministically, evidence ids are sorted, and patterns are ordered by
``(grouping_key, grouping_value)`` — so the **same dataset always yields identical patterns**
(verified by a SHA-256 checksum over their stable content). Every returned pattern is a
``HYPOTHESIS``; an empty/thin dataset yields ``INSUFFICIENT_DATA``. Nothing becomes
``VALIDATED`` here — that is a later milestone.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any, Callable

from app.learning.models import (
    DATASET_VERSION,
    LEARNING_VERSION,
    CandidatePattern,
    DuplicatePatternError,
    InvalidDatasetError,
    LearningDataset,
    LearningRecord,
    LearningStatus,
    PatternExtractionResult,
    UnknownDimensionError,
    UnsupportedVersionError,
    _utc_now_iso,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

#: A recurring pattern must group at least this many completed decisions to be a candidate
#: hypothesis (a structural minimum, not a statistical test). Configurable.
DEFAULT_MIN_EVIDENCE: int = 3
_CONF_BUCKETS = 10


def confidence_bucket(confidence: float | None) -> str | None:
    """Bucket a [0,1] confidence into a 0.1-wide band (e.g. ``0.60-0.70``), or ``None``."""
    if confidence is None:
        return None
    c = max(0.0, min(1.0, float(confidence)))
    lo = math.floor(c * _CONF_BUCKETS) / _CONF_BUCKETS
    if lo >= 1.0:
        lo = 0.9
    return f"{lo:.2f}-{lo + 0.1:.2f}"


def holding_bucket(holding: int | None) -> str | None:
    """Bucket a holding period (bars) into a coarse band, or ``None``."""
    if holding is None:
        return None
    h = int(holding)
    if h <= 5:
        return "0-5"
    if h <= 10:
        return "6-10"
    if h <= 20:
        return "11-20"
    if h <= 50:
        return "21-50"
    return "50+"


#: The pattern dimensions: name → (key extractor over a LearningRecord, pattern_type). Extend
#: by adding an entry — existing interfaces are unchanged.
PATTERN_DIMENSIONS: dict[str, tuple[Callable[[LearningRecord], str | None], str]] = {
    "symbol": (lambda r: r.symbol, "INSTRUMENT"),
    "sector": (lambda r: r.sector, "SETUP"),
    "timeframe": (lambda r: r.timeframe, "SETUP"),
    "market_regime": (lambda r: r.market_regime, "MARKET"),
    "market_phase": (lambda r: r.market_phase, "MARKET"),
    "confidence_bucket": (lambda r: confidence_bucket(r.confidence), "CONFIDENCE"),
    "prediction_model_version": (lambda r: r.prediction_model_version, "MODEL"),
    "feature_version": (lambda r: r.feature_version, "MODEL"),
    "holding_period_bucket": (lambda r: holding_bucket(r.holding_period), "HOLDING"),
    "outcome_category": (lambda r: r.outcome, "OUTCOME"),
}


def available_dimensions() -> list[str]:
    """The dimensions the extractor understands (in a stable order)."""
    return list(PATTERN_DIMENSIONS)


def _checksum(patterns: "list[CandidatePattern]") -> str:
    payload = json.dumps([p.stable_dict() for p in patterns], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PatternExtractor:
    """Groups a Learning Dataset into deterministic candidate patterns (read-only)."""

    def __init__(self, *, min_evidence: int = DEFAULT_MIN_EVIDENCE, dimensions: "list[str] | None" = None):
        """Configure the extractor.

        Args:
            min_evidence: minimum group size for a candidate pattern (structural, not
                statistical). Groups below it are dropped and counted (never silent).
            dimensions: which dimensions to extract (defaults to all). Unknown → error.
        """
        self.min_evidence = min_evidence
        self.dimensions = self._resolve(dimensions)

    @staticmethod
    def _resolve(dimensions: "list[str] | None") -> list[str]:
        if dimensions is None:
            return list(PATTERN_DIMENSIONS)
        resolved: list[str] = []
        for dim in dimensions:                      # dedupe, preserve order, validate
            if dim not in PATTERN_DIMENSIONS:
                raise UnknownDimensionError(f"unknown pattern dimension {dim!r}")
            if dim not in resolved:
                resolved.append(dim)
        return resolved

    def extract(
        self, dataset: LearningDataset, *, dimensions: "list[str] | None" = None
    ) -> PatternExtractionResult:
        """Extract candidate patterns from a Learning Dataset.

        Raises:
            InvalidDatasetError: the dataset is malformed.
            UnsupportedVersionError: the dataset's learning/dataset version is unsupported.
            UnknownDimensionError: an unknown dimension was requested.
            DuplicatePatternError / InconsistentEvidenceError: internal keying/evidence bug.
        """
        started = time.perf_counter()
        self._validate_dataset(dataset)
        dims = self._resolve(dimensions) if dimensions is not None else self.dimensions

        # A thin/empty corpus yields no patterns — the expected young-system behaviour.
        if dataset.status is LearningStatus.INSUFFICIENT_DATA:
            return self._result((), dataset, dims, insufficient=0, started=started,
                                 status=LearningStatus.INSUFFICIENT_DATA)

        patterns: list[CandidatePattern] = []
        insufficient_groups = 0
        for dim in dims:
            key_of, pattern_type = PATTERN_DIMENSIONS[dim]
            groups: dict[str, list[LearningRecord]] = {}
            for record in dataset.records:
                value = key_of(record)
                if value is None or value == "":
                    continue
                groups.setdefault(str(value), []).append(record)
            for value, recs in groups.items():
                if len(recs) < self.min_evidence:
                    insufficient_groups += 1
                    continue
                patterns.append(CandidatePattern.create(
                    learning_version=dataset.learning_version, dataset_version=dataset.dataset_version,
                    pattern_type=pattern_type, grouping_key=dim, grouping_value=value,
                    prediction_ids=[r.prediction_id for r in recs],
                    corpus_size=dataset.corpus_size, status=LearningStatus.HYPOTHESIS,
                ))

        patterns.sort(key=lambda p: (p.grouping_key, p.grouping_value))
        ids = [p.pattern_id for p in patterns]
        if len(ids) != len(set(ids)):
            raise DuplicatePatternError("duplicate pattern ids in extraction result")

        status = LearningStatus.HYPOTHESIS if patterns else LearningStatus.INSUFFICIENT_DATA
        return self._result(tuple(patterns), dataset, dims, insufficient=insufficient_groups,
                            started=started, status=status)

    def _result(self, patterns, dataset, dims, *, insufficient, started, status) -> PatternExtractionResult:
        result = PatternExtractionResult(
            patterns=patterns, status=status, corpus_size=dataset.corpus_size,
            dimensions=tuple(dims), insufficient_groups=insufficient,
            learning_version=dataset.learning_version, dataset_version=dataset.dataset_version,
            checksum=_checksum(list(patterns)), min_evidence=self.min_evidence,
        )
        logger.info(
            "pattern extraction: corpus=%d patterns=%d insufficient=%d dims=%d dataset_version=%s "
            "status=%s in %.1fms",
            dataset.corpus_size, len(patterns), insufficient, len(dims), dataset.dataset_version,
            status.value, (time.perf_counter() - started) * 1000,
        )
        return result

    @staticmethod
    def _validate_dataset(dataset: Any) -> None:
        if not isinstance(dataset, LearningDataset):
            raise InvalidDatasetError("expected a LearningDataset")
        if dataset.learning_version != LEARNING_VERSION or dataset.dataset_version != DATASET_VERSION:
            raise UnsupportedVersionError(
                f"unsupported dataset versions {dataset.learning_version}/{dataset.dataset_version}"
            )
        for record in dataset.records:
            if not isinstance(record, LearningRecord):
                raise InvalidDatasetError("dataset contains a non-LearningRecord row")
