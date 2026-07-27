"""Memory Builder — turn completed predictions into Historical Memory (Sprint 2 · M3).

The Memory Builder is the orchestrator of enrichment. For a **completed** (terminal)
prediction it assembles the reasoning record, ensures an embedding placeholder exists, and
keeps the derived aggregates current. It is the only Sprint 2 component that reads
predictions and writes satellites in the same workflow.

Strict boundaries:

* **Reads** predictions **only** through :class:`~app.forward_testing.store.PredictionStore`
  (never direct SQL); **writes** satellites **only** through
  :class:`~app.memory.store.MemoryStore` (never direct SQL, never ``predictions``).
* **Creates no facts.** It reshapes what the models already produced into a reasoning row —
  it never runs a model, never computes an embedding vector (placeholders only), and never
  retrains anything. It imports neither the Prediction nor the Outcome engine.

Primary trigger is **backfill** (scan for resolved predictions with no memory and enrich
them), so Historical Memory is correct with no change to frozen Sprint 1 code and even for
predictions that resolved before this engine existed. An optional
:meth:`on_resolved` hook allows near-real-time enrichment when explicitly enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.forward_testing.models import PredictionRecord
from app.forward_testing.store import PredictionStore
from app.memory.aggregates import compute_aggregates
from app.memory.errors import MemoryStoreError
from app.memory.models import DEFAULT_EMBEDDING_KIND, MemoryEmbedding, MemoryReasoning
from app.memory.store import MemoryStore
from app.utils.logging import get_logger

logger = get_logger(__name__)

#: Stamped into each reasoning record's build metadata; bump when the builder's output
#: shape changes so old records can be identified and rebuilt.
BUILDER_VERSION: str = "1"
_PROVENANCE: str = "memory_builder"


class BuildStatus(str, Enum):
    """Outcome of building memory for one prediction."""

    BUILT = "built"                    # enriched (created or idempotently refreshed)
    SKIPPED_OPEN = "skipped_open"      # prediction is not yet terminal
    SKIPPED_MISSING = "skipped_missing"  # no such prediction


@dataclass(frozen=True)
class BackfillSummary:
    """Result of a backfill pass."""

    scanned: int
    built: int
    skipped: int
    failed: int


class MemoryBuilder:
    """Enrich completed predictions into Historical Memory records + aggregates."""

    def __init__(
        self,
        prediction_store: PredictionStore,
        memory_store: MemoryStore,
        *,
        hook_enabled: bool = False,
    ):
        """Wire the builder to its read and write stores.

        Args:
            prediction_store: read-only source of predictions (Sprint 1).
            memory_store: the only write target (satellite tables).
            hook_enabled: whether :meth:`on_resolved` actually builds (default off — backfill
                is the primary mechanism).
        """
        self.predictions = prediction_store
        self.memory = memory_store
        self.hook_enabled = hook_enabled

    # ------------------------------------------------------------------ build one
    def build(self, prediction_id: str, *, refresh_aggregates: bool = True) -> BuildStatus:
        """Build (or idempotently refresh) memory for one prediction.

        Reads the prediction, verifies it is terminal, enriches its satellites, and — unless
        ``refresh_aggregates`` is ``False`` (used by batch backfill) — refreshes the derived
        aggregates.

        Returns:
            :class:`BuildStatus` describing what happened (never raises for a missing or open
            prediction — those are normal skips).
        """
        logger.info("memory build started for %s", prediction_id)
        record = self.predictions.get(prediction_id)
        if record is None:
            logger.info("memory build skipped: prediction %s not found", prediction_id)
            return BuildStatus.SKIPPED_MISSING
        if not record.is_terminal():
            logger.info("memory build skipped: prediction %s still open (%s)", prediction_id, record.status.value)
            return BuildStatus.SKIPPED_OPEN

        self._enrich(record)
        if refresh_aggregates:
            self.refresh_aggregates()
        logger.info("memory build completed for %s", prediction_id)
        return BuildStatus.BUILT

    # ------------------------------------------------------------------- enrich
    def _enrich(self, record: PredictionRecord) -> None:
        """Write the satellite rows for a terminal prediction (idempotent).

        Reasoning is upserted (safe to repeat). The embedding **placeholder** is created only
        when absent, so a vector later populated by the Similarity Engine is never overwritten
        with ``NULL``.
        """
        self.memory.upsert_reasoning(self._reasoning_for(record))
        if not self.memory.embedding_exists(record.prediction_id, DEFAULT_EMBEDDING_KIND):
            self.memory.create_embedding(
                MemoryEmbedding(record.prediction_id, embedding_kind=DEFAULT_EMBEDDING_KIND)
            )

    def _reasoning_for(self, record: PredictionRecord) -> MemoryReasoning:
        """Assemble a deterministic reasoning record from already-stored prediction fields.

        Deterministic (no timestamps in the payload) so repeated builds converge to identical
        content. Build metadata (builder version + provenance) rides in a reserved
        ``_builder`` key; the build timestamp is the row's ``created_at`` (preserved across
        upserts) and the schema version is the row's ``schema_version``.
        """
        confidence = record.outcome_prob if record.outcome_prob is not None else record.decision_score
        factors: dict[str, Any] = {
            "recommendation": record.recommendation,
            "direction": record.direction,
            "outcome_prob": record.outcome_prob,
            "decision_score": record.decision_score,
            "market_regime": record.market_regime,
            "market_phase": record.market_phase,
            "sector": record.sector,
            "session": record.session,
            "volatility_bucket": record.volatility_bucket,
            "_builder": {"version": BUILDER_VERSION, "provenance": _PROVENANCE},
        }
        rule_check = record.context.get("rule_check", {}) if isinstance(record.context, dict) else {}
        return MemoryReasoning(
            prediction_id=record.prediction_id,
            confidence=confidence,
            rationale=self._rationale(record, confidence),
            factors=factors,
            rule_check=rule_check if isinstance(rule_check, dict) else {},
        )

    @staticmethod
    def _rationale(record: PredictionRecord, confidence: float | None) -> str:
        """A short, deterministic human summary of the decision and how it resolved."""
        parts = [f"{record.recommendation} {record.symbol} ({record.timeframe})"]
        if record.market_regime:
            parts.append(f"regime={record.market_regime}")
        if record.sector:
            parts.append(f"sector={record.sector}")
        if confidence is not None:
            parts.append(f"confidence={float(confidence):.2f}")
        parts.append(f"outcome={record.status.value}")
        if record.realised_r is not None:
            parts.append(f"realised={float(record.realised_r):+.2f}R")
        return " · ".join(parts)

    # -------------------------------------------------------------- aggregates
    def refresh_aggregates(self) -> int:
        """Recompute all rollups from the resolved predictions and upsert them (idempotent).

        Aggregates are **derived** — recomputing from source is always correct and cannot be
        left inconsistent, which is why this is preferred over fragile running counters
        (predictions are immutable and never deleted, so buckets only ever grow — no stale
        rows to prune). Returns the number of aggregate rows written.
        """
        resolved = self.predictions.list_completed()
        rows = compute_aggregates(resolved)
        for aggregate in rows:
            self.memory.upsert_aggregate(aggregate)
        logger.info("memory aggregates refreshed: %d rows", len(rows))
        return len(rows)

    # ---------------------------------------------------------------- backfill
    def backfill(self, limit: int | None = None) -> BackfillSummary:
        """Enrich every completed prediction that has no memory yet, then refresh aggregates.

        Fully idempotent: predictions already enriched are skipped, so running backfill twice
        produces the same final state. One prediction's enrichment failure is logged and
        counted, never aborting the batch or corrupting prior work.

        Returns:
            A :class:`BackfillSummary` of scanned / built / skipped / failed counts.
        """
        completed = self.predictions.list_completed(limit=limit)
        scanned = built = skipped = failed = 0
        for record in completed:
            scanned += 1
            if self.memory.reasoning_exists(record.prediction_id):
                skipped += 1
                logger.info("memory backfill skipped %s (already built)", record.prediction_id)
                continue
            try:
                self._enrich(record)
                built += 1
            except MemoryStoreError as exc:
                failed += 1
                logger.warning("memory backfill enrich failed for %s: %s", record.prediction_id, exc)

        self.refresh_aggregates()
        summary = BackfillSummary(scanned=scanned, built=built, skipped=skipped, failed=failed)
        logger.info(
            "memory backfill summary: scanned=%d built=%d skipped=%d failed=%d",
            scanned, built, skipped, failed,
        )
        return summary

    # ----------------------------------------------------------- optional hook
    def on_resolved(self, prediction_id: str) -> BuildStatus | None:
        """Optional near-real-time hook: build memory for a just-resolved prediction.

        A no-op returning ``None`` unless ``hook_enabled`` was set — backfill remains the
        primary mechanism, so this cannot become a hidden dependency of Sprint 1.
        """
        if not self.hook_enabled:
            return None
        return self.build(prediction_id)
