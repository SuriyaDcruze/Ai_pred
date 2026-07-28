"""Similarity Search Engine — cosine k-NN over stored embeddings (Sprint 3 · Vol 14 · M3).

Given a query (an existing prediction's embedding, or an arbitrary embedding), find the most
similar historical Memory Records by **cosine similarity** over the embeddings stored in
``memory_embeddings`` (M2), and report their **honest** outcome statistics.

Strict boundaries:

* **Read-only.** It reads Memory Records (via ``RetrievalEngine``) and embeddings (via
  ``MemoryStore``) — it performs **no writes**, generates no embeddings, retrains nothing,
  and modifies neither Historical Memory nor `predictions`. It exposes no HTTP. It imports
  neither the Prediction nor Outcome engine.
* **Facts only.** It returns similar records + aggregate statistics (sample size, win rate,
  average realised R, outcome distribution). It never produces a recommendation.

Algorithm (``sim-search-1``): **filter first, then brute-force cosine.** Candidates are
narrowed by cheap Memory-Record predicates, each candidate embedding is compared by cosine
(embeddings are unit-length from M2, so cosine = dot product), results are thresholded and
sorted **deterministically** (``-similarity``, then ``prediction_id``), and the top *k* are
returned. SQLite has no ANN index, so a **candidate cap** bounds the brute-force set; whenever
the cap bites it is **logged**, never silently applied.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from app.memory.retrieval import MemoryFilter, RetrievalEngine
from app.memory.store import MemoryStore
from app.similarity.embedding import EMBEDDING_VERSION
from app.similarity.feature_vector import FEATURE_VERSION, VECTOR_DIM
from app.similarity.models import (
    DimensionMismatchError,
    Embedding,
    InvalidFeatureVectorError,
    MissingEmbeddingError,
    SearchRequestError,
    UnsupportedVersionError,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

#: Identifier of this search implementation (metric + ordering contract).
SIMILARITY_VERSION: str = "sim-search-1"
METRIC: str = "cosine"

_DEFAULT_CANDIDATE_CAP = 1000
_PAGE = 500


# --------------------------------------------------------------------------- metric
def cosine_similarity(a: tuple[float, ...] | list[float], b: tuple[float, ...] | list[float]) -> float:
    """Cosine similarity of two equal-length vectors.

    Identical direction → ``1.0``, orthogonal → ``0.0``, opposite → ``-1.0``. A zero vector
    (no informative features) yields ``0.0`` rather than a division error.
    """
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# --------------------------------------------------------------------------- request/result types
@dataclass
class SimilarityFilter:
    """Candidate pre-filter — every field optional, AND-composed. Applied *before* cosine."""

    symbol: str | None = None
    sector: str | None = None
    timeframe: str | None = None
    market_regime: str | None = None
    market_phase: str | None = None
    outcome: str | None = None                      # status value or WIN/LOSS alias
    prediction_model_version: str | None = None
    feature_version: str | None = None


@dataclass(frozen=True)
class SimilarityNeighbour:
    """One similar historical decision (no raw embedding vector is exposed)."""

    prediction_id: str
    similarity_score: float
    confidence: float | None
    outcome: str                                    # WIN / LOSS / EXPIRED / CANCELLED / OPEN
    status: str
    realised_r: float | None
    holding_bars: int | None
    symbol: str | None
    sector: str | None
    market_regime: str | None
    market_phase: str | None
    timeframe: str | None
    embedding_version: str
    feature_version: str


@dataclass(frozen=True)
class SimilaritySummary:
    """Honest aggregate over the returned neighbours — facts only, never a recommendation."""

    sample_size: int
    resolved: int
    win_rate: float | None
    avg_realised_r: float | None
    outcome_distribution: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SimilaritySearchResult:
    """The full result of a similarity search."""

    query_prediction_id: str | None
    neighbours: list[SimilarityNeighbour]
    summary: SimilaritySummary
    candidate_count: int
    returned: int
    cap_applied: bool
    similarity_version: str
    metric: str
    feature_version: str


# --------------------------------------------------------------------------- engine
class SimilaritySearchEngine:
    """Read-only cosine k-NN over the embeddings stored in Historical Memory."""

    def __init__(
        self,
        retrieval: RetrievalEngine,
        memory_store: MemoryStore,
        *,
        default_candidate_cap: int = _DEFAULT_CANDIDATE_CAP,
    ):
        """Wire the engine to its two read-only sources.

        Args:
            retrieval: composed Memory Records + candidate filtering (Sprint 2).
            memory_store: stored embeddings (`memory_embeddings`).
            default_candidate_cap: default bound on the brute-force candidate set.
        """
        self.retrieval = retrieval
        self.memory = memory_store
        self.default_candidate_cap = default_candidate_cap

    # --------------------------------------------------------------- public API
    def search_by_prediction(
        self,
        prediction_id: str,
        *,
        k: int = 10,
        filter: SimilarityFilter | None = None,
        min_similarity: float = -1.0,
        candidate_cap: int | None = None,
    ) -> SimilaritySearchResult:
        """Find the k historical decisions most similar to ``prediction_id`` (excluding itself).

        Raises:
            MissingEmbeddingError: the query prediction has no stored embedding.
            plus the request/version errors of :meth:`search`.
        """
        stored = self.memory.get_embedding(prediction_id)
        if stored is None or stored.vector is None:
            raise MissingEmbeddingError(f"prediction {prediction_id!r} has no stored embedding")
        query = self._embedding_from_stored(stored, prediction_id)
        return self.search(
            query, exclude_prediction_id=prediction_id, k=k, filter=filter,
            min_similarity=min_similarity, candidate_cap=candidate_cap,
        )

    def search(
        self,
        query: Embedding,
        *,
        exclude_prediction_id: str | None = None,
        k: int = 10,
        filter: SimilarityFilter | None = None,
        min_similarity: float = -1.0,
        candidate_cap: int | None = None,
    ) -> SimilaritySearchResult:
        """Find the k stored embeddings most similar to ``query`` (an :class:`Embedding`).

        Raises:
            SearchRequestError: malformed ``k`` / ``min_similarity`` / ``candidate_cap``.
            UnsupportedVersionError / DimensionMismatchError / InvalidFeatureVectorError:
                the query embedding is not a supported, well-formed ``sim-emb-1`` vector.
        """
        started = time.perf_counter()
        k = self._validate_k(k)
        cap = self._validate_cap(candidate_cap if candidate_cap is not None else self.default_candidate_cap)
        min_similarity = self._validate_threshold(min_similarity)
        query_vec = self._validate_query(query)

        candidates, cap_applied, skipped = self._candidates(filter, cap)
        scored: list[tuple[float, str, dict[str, Any]]] = []
        seen: set[str] = set()
        for record, vector in candidates:
            pid = record.get("prediction_id")
            if not pid or pid == exclude_prediction_id or pid in seen:
                continue
            seen.add(pid)
            score = cosine_similarity(query_vec, vector)
            if score >= min_similarity:
                scored.append((score, pid, record))

        # Deterministic ordering: highest similarity first, ties broken by prediction_id.
        scored.sort(key=lambda item: (-item[0], item[1]))
        top = scored[:k]
        neighbours = [self._neighbour(record, score) for score, _pid, record in top]

        result = SimilaritySearchResult(
            query_prediction_id=exclude_prediction_id,
            neighbours=neighbours,
            summary=self._summary(neighbours),
            candidate_count=len(candidates),
            returned=len(neighbours),
            cap_applied=cap_applied,
            similarity_version=SIMILARITY_VERSION,
            metric=METRIC,
            feature_version=FEATURE_VERSION,
        )
        logger.info(
            "similarity search %s: candidates=%d compared=%d returned=%d cap_applied=%s "
            "version_skipped=%d in %.1fms",
            METRIC, len(candidates), len(scored), len(neighbours), cap_applied, skipped,
            (time.perf_counter() - started) * 1000,
        )
        return result

    # --------------------------------------------------------------- candidates
    def _candidates(
        self, filter: SimilarityFilter | None, cap: int
    ) -> tuple[list[tuple[dict[str, Any], tuple[float, ...]]], bool, int]:
        """Filter Memory Records, attach their embeddings, and cap the brute-force set.

        Returns ``(candidates, cap_applied, version_skipped)`` where each candidate is
        ``(record_dict, embedding_vector)``. Records without a compatible embedding are
        skipped (counted, never silent); the cap bounds the returned list.
        """
        filter = filter or SimilarityFilter()
        memory_filter = MemoryFilter(
            symbol=filter.symbol, timeframe=filter.timeframe, sector=filter.sector,
            market_regime=filter.market_regime, outcome=filter.outcome,
            prediction_model_version=filter.prediction_model_version,
            feature_version=filter.feature_version,
        )
        candidates: list[tuple[dict[str, Any], tuple[float, ...]]] = []
        version_skipped = 0
        cap_applied = False
        cursor: str | None = None
        done = False
        while not done:
            page = self.retrieval.search(memory_filter, limit=_PAGE, cursor=cursor)
            for record in page.records:
                data = record.to_dict()
                if filter.market_phase is not None and data.get("market_phase") != filter.market_phase:
                    continue
                pid = data.get("prediction_id")
                stored = self.memory.get_embedding(pid) if pid else None
                if stored is None or stored.vector is None:
                    continue                                        # no embedding → not a candidate
                emb_version, _feat = _parse_versions(stored.model_name)
                if emb_version != EMBEDDING_VERSION or len(stored.vector) != VECTOR_DIM:
                    version_skipped += 1                            # incompatible embedding → skip (logged)
                    continue
                candidates.append((data, tuple(stored.vector)))
                if len(candidates) >= cap:
                    cap_applied = True
                    done = True
                    break
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        if version_skipped:
            logger.info("similarity search skipped %d embedding(s) of an incompatible version", version_skipped)
        return candidates, cap_applied, version_skipped

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _embedding_from_stored(stored: Any, prediction_id: str) -> Embedding:
        emb_version, feat_version = _parse_versions(stored.model_name)
        return Embedding(
            vector=tuple(stored.vector),
            embedding_version=emb_version or "",
            feature_version=feat_version or "",
            schema_version=int(stored.schema_version or 1),
            dimension=len(stored.vector),
            embedding_kind=stored.embedding_kind,
            created_at=stored.created_at or "",
            prediction_id=prediction_id,
        )

    @staticmethod
    def _validate_query(query: Embedding) -> tuple[float, ...]:
        if query.embedding_version != EMBEDDING_VERSION:
            raise UnsupportedVersionError(
                f"unsupported embedding_version {query.embedding_version!r} (only {EMBEDDING_VERSION!r})"
            )
        if query.dimension != VECTOR_DIM or len(query.vector) != VECTOR_DIM:
            raise DimensionMismatchError(f"query dimension {query.dimension} != {VECTOR_DIM}")
        if any(not math.isfinite(v) for v in query.vector):
            raise InvalidFeatureVectorError("query embedding contains non-finite values")
        return query.vector

    @staticmethod
    def _validate_k(k: int) -> int:
        if not isinstance(k, int) or k < 1:
            raise SearchRequestError(f"k must be a positive integer, got {k!r}")
        return k

    @staticmethod
    def _validate_cap(cap: int) -> int:
        if not isinstance(cap, int) or cap < 1:
            raise SearchRequestError(f"candidate_cap must be a positive integer, got {cap!r}")
        return cap

    @staticmethod
    def _validate_threshold(value: float) -> float:
        if not (-1.0 <= float(value) <= 1.0):
            raise SearchRequestError(f"min_similarity must be within [-1, 1], got {value!r}")
        return float(value)

    @staticmethod
    def _neighbour(record: dict[str, Any], score: float) -> SimilarityNeighbour:
        return SimilarityNeighbour(
            prediction_id=record.get("prediction_id"),
            similarity_score=score,
            confidence=record.get("confidence"),
            outcome=record.get("trade_result"),
            status=record.get("status"),
            realised_r=record.get("realised_r"),
            holding_bars=record.get("holding_bars"),
            symbol=record.get("symbol"),
            sector=record.get("sector"),
            market_regime=record.get("market_regime"),
            market_phase=record.get("market_phase"),
            timeframe=record.get("timeframe"),
            embedding_version=EMBEDDING_VERSION,
            feature_version=FEATURE_VERSION,
        )

    @staticmethod
    def _summary(neighbours: list[SimilarityNeighbour]) -> SimilaritySummary:
        distribution: dict[str, int] = {}
        for n in neighbours:
            key = n.outcome or "UNKNOWN"
            distribution[key] = distribution.get(key, 0) + 1

        resolved = [n for n in neighbours if n.realised_r is not None]
        if not resolved:
            return SimilaritySummary(
                sample_size=len(neighbours), resolved=0, win_rate=None,
                avg_realised_r=None, outcome_distribution=distribution,
            )
        wins = sum(1 for n in resolved if float(n.realised_r) > 0)
        return SimilaritySummary(
            sample_size=len(neighbours),
            resolved=len(resolved),
            win_rate=wins / len(resolved),
            avg_realised_r=sum(float(n.realised_r) for n in resolved) / len(resolved),
            outcome_distribution=distribution,
        )


def _parse_versions(model_name: str | None) -> tuple[str | None, str | None]:
    """Recover ``(embedding_version, feature_version)`` from a packed ``model_name``."""
    if not model_name:
        return (None, None)
    parts = model_name.split("/", 1)
    return (parts[0], parts[1] if len(parts) > 1 else None)
