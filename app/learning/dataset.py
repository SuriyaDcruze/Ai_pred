"""Learning Dataset Builder — deterministic view of completed decisions (Sprint 4 · M1).

A **pure, read-only** transformation: it reads completed Historical Memory Records (via
`RetrievalEngine`), projects each into a :class:`LearningRecord`, and assembles a deterministic,
versioned :class:`LearningDataset` — the canonical input every later Learning milestone depends
on. It computes **no statistics, patterns, or recommendations**, exposes no HTTP, performs
**no writes**, and imports neither the Prediction nor Outcome engine.

Determinism: records are ordered by `(prediction_timestamp, prediction_id)` and fingerprinted
with a SHA-256 checksum over their stable content, so the same corpus always yields the same
dataset (the volatile `generated_at` / `build_duration_ms` are excluded from the checksum).

Honesty: when the corpus has fewer than `min_corpus` completed trades — including the common
early case of **zero** — the dataset's status is `INSUFFICIENT_DATA`. That is the expected
behaviour for a young system, not an error.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any

from app.memory.retrieval import MemoryFilter, RetrievalEngine
from app.learning.models import (
    DATASET_VERSION,
    DEFAULT_MIN_CORPUS,
    LEARNING_VERSION,
    SUPPORTED_RECORD_SCHEMA,
    LearningDataset,
    LearningRecord,
    LearningStatus,
    _utc_now_iso,
    checksum_of,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

_PAGE = 500


class LearningDatasetBuilder:
    """Builds the deterministic Learning Dataset from Historical Memory (read-only)."""

    learning_version: str = LEARNING_VERSION
    dataset_version: str = DATASET_VERSION

    def __init__(self, retrieval: RetrievalEngine, *, min_corpus: int = DEFAULT_MIN_CORPUS):
        """Wire the builder to Historical Memory.

        Args:
            retrieval: the read-only Memory Record source (Sprint 2).
            min_corpus: minimum completed trades for the dataset to be considered sufficient;
                below it the dataset reports ``INSUFFICIENT_DATA``.
        """
        self.retrieval = retrieval
        self.min_corpus = min_corpus

    def build(self, *, filter: MemoryFilter | None = None) -> LearningDataset:
        """Assemble the Learning Dataset (optionally filtered).

        Reads completed Memory Records (those with a realised outcome), validates and projects
        each into a :class:`LearningRecord`, orders them deterministically, and stamps the
        dataset with versions, source provenance, and a reproducible checksum. Rebuilding is
        idempotent — the same corpus yields an identical checksum and records.

        Raises:
            LearningError subclasses: a malformed / inconsistent / unsupported record.
        """
        started = time.perf_counter()
        records: list[LearningRecord] = []
        cursor: str | None = None
        while True:
            page = self.retrieval.search(filter or MemoryFilter(), limit=_PAGE, cursor=cursor)
            for memory_record in page.records:
                data = memory_record.to_dict()
                if data.get("realised_r") is None:
                    continue  # open / cancelled → not a completed-with-outcome trade
                records.append(LearningRecord.from_memory_record(data))
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        records.sort(key=lambda r: (r.prediction_timestamp or "", r.prediction_id))
        corpus_size = len(records)
        status = LearningStatus.INSUFFICIENT_DATA if corpus_size < self.min_corpus else None
        dataset = LearningDataset(
            records=tuple(records),
            corpus_size=corpus_size,
            dataset_version=DATASET_VERSION,
            learning_version=LEARNING_VERSION,
            generated_at=_utc_now_iso(),
            source_versions=self._source_versions(records),
            build_duration_ms=(time.perf_counter() - started) * 1000,
            checksum=checksum_of(records),
            status=status,
            min_corpus=self.min_corpus,
            filter=self._filter_dict(filter),
        )
        logger.info(
            "learning dataset built: corpus=%d checksum=%s version=%s status=%s in %.1fms",
            corpus_size, dataset.checksum[:12], LEARNING_VERSION,
            status.value if status else "SUFFICIENT", dataset.build_duration_ms,
        )
        return dataset

    @staticmethod
    def _source_versions(records: list[LearningRecord]) -> dict[str, Any]:
        """Deterministic provenance: the model/feature versions present in the corpus."""
        return {
            "prediction_model_versions": sorted(
                {r.prediction_model_version for r in records if r.prediction_model_version}
            ),
            "feature_versions": sorted({r.feature_version for r in records if r.feature_version}),
            "memory_record_schema": SUPPORTED_RECORD_SCHEMA,
        }

    @staticmethod
    def _filter_dict(filter: MemoryFilter | None) -> dict[str, Any] | None:
        if filter is None:
            return None
        return {k: v for k, v in dataclasses.asdict(filter).items() if v is not None}
