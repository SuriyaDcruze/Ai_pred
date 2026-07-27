"""Retrieval Engine — the read layer of Historical Memory (Sprint 2 · Milestone 4).

The Retrieval Engine composes a **Memory Record** on read from the immutable ``predictions``
fact (via :class:`~app.forward_testing.store.PredictionStore`) and the Historical Memory
satellites (via :class:`~app.memory.store.MemoryStore`). It offers filtered search, keyset
pagination, read-only aggregate queries, the Similarity **contract** (an explicit
"unavailable" until the Similarity Engine lands — never a fake score), and a bounded GPT
context bundle.

Strict boundaries:

* **Read-only.** It calls only read methods of the two stores; it performs **no writes** and
  issues no direct SQL. ``predictions`` is never touched, and it imports neither the
  Prediction nor Outcome engine.
* **Composes, never duplicates.** The Memory Record is assembled dynamically from the
  embedded prediction plus its satellites — no prediction field is copied into a second
  source of truth. A missing satellite yields ``null``/defaults, never an error.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field, replace
from typing import Any

from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.errors import MemoryNotFoundError, MemoryQueryError
from app.memory.models import (
    MEMORY_SCHEMA_VERSION,
    AggregateDimension,
    MemoryAggregate,
    MemoryEmbedding,
    MemoryReasoning,
)
from app.memory.store import MemoryStore
from app.utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500
_MIN_MEANINGFUL_SAMPLE = 50

#: Maps a terminal status to a plain trade result; open states → "OPEN".
_TRADE_RESULT: dict[str, str] = {
    PredictionStatus.TARGET_HIT.value: "WIN",
    PredictionStatus.STOP_HIT.value: "LOSS",
    PredictionStatus.EXPIRED.value: "EXPIRED",
    PredictionStatus.CANCELLED.value: "CANCELLED",
}
#: Friendly outcome aliases accepted by the ``outcome`` filter.
_OUTCOME_ALIAS: dict[str, str] = {"WIN": "TARGET_HIT", "LOSS": "STOP_HIT"}
_VALID_STATUS = {s.value for s in PredictionStatus}


def _confidence(record: PredictionRecord) -> float | None:
    """Decision-time confidence: outcome probability, else decision score."""
    return record.outcome_prob if record.outcome_prob is not None else record.decision_score


# --------------------------------------------------------------------------- Memory Record
@dataclass
class MemoryRecord:
    """One historical decision, composed on read (prediction + satellites).

    The prediction is embedded as the single source of truth; the satellites are optional.
    :meth:`to_dict` surfaces the canonical flat view consumers code against.
    """

    prediction: PredictionRecord
    reasoning: MemoryReasoning | None = None
    embedding: MemoryEmbedding | None = None
    aggregate: MemoryAggregate | None = None

    @property
    def prediction_id(self) -> str:
        return self.prediction.prediction_id

    @property
    def symbol(self) -> str:
        return self.prediction.symbol

    @property
    def trade_result(self) -> str:
        return _TRADE_RESULT.get(self.prediction.status.value, "OPEN")

    def _metadata(self) -> dict[str, Any]:
        r = self.reasoning
        builder = (r.factors.get("_builder", {}) if r and isinstance(r.factors, dict) else {}) or {}
        return {
            "built": r is not None,
            "built_at": r.created_at if r else None,
            "reasoning_schema_version": r.schema_version if r else None,
            "builder_version": builder.get("version"),
            "provenance": builder.get("provenance"),
            "source": self.prediction.source,
            "record_schema_version": MEMORY_SCHEMA_VERSION,
        }

    def to_dict(self) -> dict[str, Any]:
        """Assemble the canonical Memory Record view. Missing satellites → defaults/null."""
        p = self.prediction
        r = self.reasoning
        emb = self.embedding
        agg = self.aggregate
        return {
            "prediction_id": p.prediction_id,
            "created_at": p.created_at,
            "symbol": p.symbol,
            "exchange": p.exchange,
            "timeframe": p.timeframe,
            "source": p.source,
            # prediction
            "direction": p.direction,
            "recommendation": p.recommendation,
            "direction_prob": p.direction_prob,
            "outcome_prob": p.outcome_prob,
            "decision_score": p.decision_score,
            "confidence": _confidence(p),
            # plan / risk
            "entry": p.entry, "stop": p.stop, "target1": p.target1, "target2": p.target2,
            # outcome
            "status": p.status.value,
            "trade_result": self.trade_result,
            "resolution_reason": p.resolution_reason,
            "resolved_at": p.resolved_at,
            "resolved_price": p.resolved_price,
            "realised_r": p.realised_r,
            "holding_bars": p.holding_bars,
            # context
            "market_regime": p.market_regime,
            "market_phase": p.market_phase,
            "sector": p.sector,
            "session": p.session,
            "volatility_bucket": p.volatility_bucket,
            "context": p.context,
            "versions": {
                "prediction_model_version": p.prediction_model_version,
                "outcome_model_version": p.outcome_model_version,
                "feature_version": p.feature_version,
            },
            # satellites (optional)
            "reasoning": {
                "rationale": r.rationale, "factors": r.factors,
                "rule_check": r.rule_check, "confidence": r.confidence,
            } if r else None,
            "embedding": {
                "kind": emb.embedding_kind, "present": emb.vector is not None,
                "dim": emb.dim, "model_name": emb.model_name,
            } if emb else None,
            "aggregate": {
                "dimension": agg.dimension.value, "bucket": agg.bucket,
                "model_version": agg.model_version, "n_resolved": agg.n_resolved,
                "win_rate": agg.win_rate, "avg_r": agg.avg_r, "expectancy": agg.expectancy,
                "profit_factor": agg.profit_factor,
            } if agg else None,
            "metadata": self._metadata(),
        }


# --------------------------------------------------------------------------- filter + page
@dataclass
class MemoryFilter:
    """AND-composed retrieval filter; every field is optional."""

    symbol: str | None = None
    timeframe: str | None = None
    market_regime: str | None = None
    sector: str | None = None
    prediction_model_version: str | None = None
    outcome_model_version: str | None = None
    feature_version: str | None = None
    confidence_min: float | None = None
    confidence_max: float | None = None
    outcome: str | None = None          # status value or WIN/LOSS alias
    date_from: str | None = None        # ISO-8601, inclusive (compares created_at)
    date_to: str | None = None          # ISO-8601, inclusive

    def normalized_outcome(self) -> str | None:
        if self.outcome is None:
            return None
        return _OUTCOME_ALIAS.get(self.outcome.upper(), self.outcome.upper())

    def validate(self) -> None:
        """Reject a malformed filter with a meaningful :class:`MemoryQueryError`."""
        for name in ("confidence_min", "confidence_max"):
            v = getattr(self, name)
            if v is not None and not (0.0 <= float(v) <= 1.0):
                raise MemoryQueryError(f"{name} must be within [0, 1], got {v!r}")
        if (
            self.confidence_min is not None
            and self.confidence_max is not None
            and self.confidence_min > self.confidence_max
        ):
            raise MemoryQueryError("confidence_min cannot exceed confidence_max")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise MemoryQueryError("date_from cannot be after date_to")
        outcome = self.normalized_outcome()
        if outcome is not None and outcome not in _VALID_STATUS:
            raise MemoryQueryError(f"unknown outcome {self.outcome!r}")


@dataclass
class SearchPage:
    """One page of retrieval results with a keyset cursor for the next page."""

    records: list[MemoryRecord]
    next_cursor: str | None
    limit: int

    @property
    def count(self) -> int:
        return len(self.records)


@dataclass
class SimilarityResult:
    """The Similarity contract's response. Until the Similarity Engine lands, this is always
    an explicit *unavailable* — never a fabricated score."""

    available: bool
    reason: str
    results: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- engine
class RetrievalEngine:
    """Read-only composition, search, aggregate reads, and the similarity contract."""

    def __init__(self, prediction_store: PredictionStore, memory_store: MemoryStore):
        """Wire the engine to its two read-only stores."""
        self.predictions = prediction_store
        self.memory = memory_store

    # --------------------------------------------------------------- compose
    def _relevant_aggregate(self, record: PredictionRecord) -> MemoryAggregate | None:
        """The most relevant rollup for a record's context (sector, else overall)."""
        if record.sector:
            agg = self.memory.get_aggregate(AggregateDimension.SECTOR, record.sector, "")
            if agg is not None:
                return agg
        return self.memory.get_aggregate(AggregateDimension.OVERALL, "all", "")

    def _compose(self, record: PredictionRecord) -> MemoryRecord:
        """Assemble a Memory Record from a prediction + its satellites (all optional)."""
        return MemoryRecord(
            prediction=record,
            reasoning=self.memory.get_reasoning(record.prediction_id),
            embedding=self.memory.get_embedding(record.prediction_id),
            aggregate=self._relevant_aggregate(record),
        )

    def get_record(self, prediction_id: str) -> MemoryRecord:
        """Compose the full Memory Record for one prediction.

        Raises:
            MemoryNotFoundError: no such prediction.
        """
        record = self.predictions.get(prediction_id)
        if record is None:
            raise MemoryNotFoundError(f"unknown prediction {prediction_id!r}")
        logger.info("memory retrieval: record %s", prediction_id)
        return self._compose(record)

    # ---------------------------------------------------------------- search
    def search(
        self, filter: MemoryFilter | None = None, *, limit: int = _DEFAULT_LIMIT, cursor: str | None = None
    ) -> SearchPage:
        """Filtered, deterministically-ordered, keyset-paginated search.

        Ordering is ``(created_at, prediction_id)`` descending — stable and reproducible.
        ``cursor`` continues after the previous page. Reads predictions via
        ``PredictionStore`` and filters in memory (no direct SQL).
        """
        filter = filter or MemoryFilter()
        filter.validate()
        limit = self._validate_limit(limit)
        cursor_key = self._decode_cursor(cursor)

        matched = [p for p in self.predictions.list_all() if self._matches(p, filter)]
        matched.sort(key=self._order_key, reverse=True)
        if cursor_key is not None:
            matched = [p for p in matched if self._order_key(p) < cursor_key]

        page = matched[:limit]
        has_more = len(matched) > limit
        next_cursor = self._encode_cursor(page[-1]) if page and has_more else None
        logger.info(
            "memory search: matched=%d page=%d limit=%d paginated=%s",
            len(matched), len(page), limit, cursor is not None,
        )
        return SearchPage(records=[self._compose(p) for p in page], next_cursor=next_cursor, limit=limit)

    @staticmethod
    def _order_key(record: PredictionRecord) -> tuple[str, str]:
        return (record.created_at or "", record.prediction_id)

    def _matches(self, p: PredictionRecord, f: MemoryFilter) -> bool:
        if f.symbol is not None and p.symbol != f.symbol:
            return False
        if f.timeframe is not None and p.timeframe != f.timeframe:
            return False
        if f.market_regime is not None and p.market_regime != f.market_regime:
            return False
        if f.sector is not None and p.sector != f.sector:
            return False
        if f.prediction_model_version is not None and p.prediction_model_version != f.prediction_model_version:
            return False
        if f.outcome_model_version is not None and p.outcome_model_version != f.outcome_model_version:
            return False
        if f.feature_version is not None and p.feature_version != f.feature_version:
            return False
        outcome = f.normalized_outcome()
        if outcome is not None and p.status.value != outcome:
            return False
        if f.confidence_min is not None or f.confidence_max is not None:
            conf = _confidence(p)
            if conf is None:
                return False
            if f.confidence_min is not None and conf < f.confidence_min:
                return False
            if f.confidence_max is not None and conf > f.confidence_max:
                return False
        if f.date_from is not None and (p.created_at or "") < f.date_from:
            return False
        if f.date_to is not None and (p.created_at or "") > f.date_to:
            return False
        return True

    # ------------------------------------------------------------ pagination
    @staticmethod
    def _validate_limit(limit: int) -> int:
        if not isinstance(limit, int) or limit <= 0 or limit > _MAX_LIMIT:
            raise MemoryQueryError(f"limit must be an integer in 1..{_MAX_LIMIT}, got {limit!r}")
        return limit

    def _encode_cursor(self, record: PredictionRecord) -> str:
        created, pid = self._order_key(record)
        return base64.urlsafe_b64encode(f"{created}|{pid}".encode()).decode()

    @staticmethod
    def _decode_cursor(cursor: str | None) -> tuple[str, str] | None:
        if cursor is None:
            return None
        try:
            raw = base64.urlsafe_b64decode(cursor.encode()).decode()
            created, pid = raw.split("|", 1)
        except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
            raise MemoryQueryError("malformed pagination cursor") from exc
        return (created, pid)

    # ------------------------------------------------------------ aggregates
    def aggregates(
        self, dimension: AggregateDimension | str | None = None,
        bucket: str | None = None, model_version: str = "",
    ) -> list[MemoryAggregate]:
        """Read-only aggregate query. Never computes — reads via ``MemoryStore`` only.

        With ``bucket`` given, returns the single matching rollup (empty list if absent);
        otherwise lists the dimension's rollups (or all when ``dimension`` is ``None``).
        """
        if bucket is not None:
            if dimension is None:
                raise MemoryQueryError("bucket requires a dimension")
            agg = self.memory.get_aggregate(dimension, bucket, model_version)
            return [agg] if agg is not None else []
        return self.memory.list_aggregates(dimension)

    # ------------------------------------------------------------ similarity
    def similar(self, prediction_id: str, *, k: int = 5) -> SimilarityResult:
        """The Similarity contract — always **unavailable** until the Similarity Engine (Vol 14).

        Validates the prediction exists, then returns an explicit unavailable result with
        **no** fabricated scores.

        Raises:
            MemoryNotFoundError: no such prediction.
        """
        if self.predictions.get(prediction_id) is None:
            raise MemoryNotFoundError(f"unknown prediction {prediction_id!r}")
        logger.info("memory similarity requested for %s — unavailable (no Similarity Engine)", prediction_id)
        return SimilarityResult(available=False, reason="Similarity Engine unavailable", results=[])

    # ---------------------------------------------------------- gpt context
    def gpt_context(
        self, *, symbol: str | None = None, filter: MemoryFilter | None = None, k: int = 5
    ) -> dict[str, Any]:
        """A bounded, deterministic grounding bundle for the GPT assistant.

        Returns the top ``k`` matching Memory Records (most recent first), the relevant
        aggregate summary, the resolved **sample size**, and metadata — so the assistant is
        grounded and cannot over-claim from a thin sample. Fully deterministic (no
        timestamps, no randomness).
        """
        k = self._validate_limit(k)
        base = filter or MemoryFilter()
        if symbol is not None:
            base = replace(base, symbol=symbol)   # never mutate the caller's filter
        page = self.search(base, limit=k)

        if symbol is not None:
            aggregate = self.memory.get_aggregate(AggregateDimension.SYMBOL, symbol, "")
        else:
            aggregate = self.memory.get_aggregate(AggregateDimension.OVERALL, "all", "")
        sample_size = aggregate.n_resolved if aggregate else 0

        return {
            "records": [r.to_dict() for r in page.records],
            "aggregate": {
                "dimension": aggregate.dimension.value, "bucket": aggregate.bucket,
                "n_resolved": aggregate.n_resolved, "win_rate": aggregate.win_rate,
                "avg_r": aggregate.avg_r, "profit_factor": aggregate.profit_factor,
            } if aggregate else None,
            "sample_size": sample_size,
            "metadata": {
                "k": k,
                "symbol": symbol,
                "returned": page.count,
                "min_meaningful_sample": _MIN_MEANINGFUL_SAMPLE,
                "note": (
                    "Sample too small to be conclusive." if sample_size < _MIN_MEANINGFUL_SAMPLE
                    else "Sample size is meaningful; still compare against backtest."
                ),
            },
        }
