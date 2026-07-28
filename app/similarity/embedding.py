"""Embedding Generator — deterministic embeddings of feature vectors (Sprint 3 · Vol 14 · M2).

Turns a :class:`~app.similarity.models.FeatureVector` (from M1) into a **deterministic**
embedding and stores it in the Historical Memory ``memory_embeddings`` satellite (via
``MemoryStore`` only — never direct SQL). It does **not** perform similarity search, rank
neighbours, expose an API, modify Memory Records / predictions / Historical Memory facts, or
train any model.

**Embedding strategy — ``sim-emb-1``:** the L2-normalised feature vector. This is fully
deterministic (identical feature vector → identical embedding, bit-for-bit), needs no model
artifact, and puts every embedding on the unit sphere so a later cosine/dot similarity (M3) is
well-behaved. A zero vector (no informative features) stays zero.

**Versioning & storage mapping:** the ``memory_embeddings`` table (Sprint 2, frozen) has no
dedicated version columns, so ``embedding_version`` + ``feature_version`` are stored in the
row's ``model_name`` as ``"<embedding_version>/<feature_version>"``; ``dimension`` → ``dim``,
``schema_version`` → the row's ``schema_version``, and ``embedding_kind`` fills the placeholder
the Memory Builder created. Idempotent by ``(prediction_id, embedding_kind)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.memory.models import DEFAULT_EMBEDDING_KIND, MemoryEmbedding
from app.memory.retrieval import RetrievalEngine
from app.memory.store import MemoryStore
from app.similarity.feature_vector import FEATURE_VERSION, VECTOR_DIM, FeatureVectorBuilder
from app.similarity.models import (
    DimensionMismatchError,
    Embedding,
    FeatureVector,
    InvalidFeatureVectorError,
    UnsupportedVersionError,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

#: The embedding scheme identifier. Bump on ANY change to the transform. Pinned to the
#: feature version it is built on.
EMBEDDING_VERSION: str = "sim-emb-1"
#: Revision of the embedding metadata shape.
EMBEDDING_SCHEMA_VERSION: int = 1
#: The satellite slot embeddings occupy — the same kind the Memory Builder placeholders use,
#: so generating an embedding fills that NULL placeholder in place.
EMBEDDING_KIND: str = DEFAULT_EMBEDDING_KIND

#: How embedding_version + feature_version are packed into the row's ``model_name``.
_MODEL_NAME: str = f"{EMBEDDING_VERSION}/{FEATURE_VERSION}"
_PAGE = 500


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def l2_normalize(values: tuple[float, ...] | list[float]) -> list[float]:
    """Return the L2-normalised vector (unit length). A zero vector is returned unchanged."""
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0.0:
        return list(values)
    return [v / norm for v in values]


@dataclass(frozen=True)
class EmbeddingBackfillSummary:
    """Result of an embedding backfill pass."""

    scanned: int
    embedded: int
    skipped: int
    failed: int


class EmbeddingGenerator:
    """Generate, store, rebuild and backfill deterministic embeddings."""

    embedding_version: str = EMBEDDING_VERSION
    feature_version: str = FEATURE_VERSION
    schema_version: int = EMBEDDING_SCHEMA_VERSION
    dimension: int = VECTOR_DIM
    embedding_kind: str = EMBEDDING_KIND

    def __init__(
        self,
        retrieval: RetrievalEngine,
        memory_store: MemoryStore,
        *,
        feature_builder: FeatureVectorBuilder | None = None,
    ):
        """Wire the generator to Historical Memory.

        Args:
            retrieval: read-only source of Memory Records (Sprint 2).
            memory_store: the only write target — ``memory_embeddings`` rows.
            feature_builder: the M1 builder (a fresh one is created if omitted).
        """
        self.retrieval = retrieval
        self.memory = memory_store
        self.features = feature_builder or FeatureVectorBuilder()

    # ------------------------------------------------------------------ generate
    def generate_from_feature_vector(
        self, vector: FeatureVector, *, prediction_id: str | None = None
    ) -> Embedding:
        """Deterministically embed a feature vector (pure — no storage).

        Raises:
            UnsupportedVersionError: the vector's feature version is not supported.
            DimensionMismatchError: the vector's dimension is not the expected dimension.
            InvalidFeatureVectorError: the vector contains NaN/inf.
        """
        if vector.feature_version != FEATURE_VERSION:
            raise UnsupportedVersionError(
                f"unsupported feature_version {vector.feature_version!r} (only {FEATURE_VERSION!r})"
            )
        if vector.dimension != VECTOR_DIM or len(vector.values) != VECTOR_DIM:
            raise DimensionMismatchError(
                f"feature vector dimension {vector.dimension}/{len(vector.values)} != {VECTOR_DIM}"
            )
        if any(not math.isfinite(v) for v in vector.values):
            raise InvalidFeatureVectorError("feature vector contains non-finite values")

        return Embedding(
            vector=tuple(l2_normalize(vector.values)),
            embedding_version=EMBEDDING_VERSION,
            feature_version=FEATURE_VERSION,
            schema_version=EMBEDDING_SCHEMA_VERSION,
            dimension=VECTOR_DIM,
            embedding_kind=EMBEDDING_KIND,
            created_at=_utc_now_iso(),
            prediction_id=prediction_id,
        )

    def generate_embedding(self, memory_record: Any) -> Embedding:
        """Embed one Memory Record (mapping or object with ``to_dict()``).

        Builds the feature vector (M1) then embeds it. Raises the same typed errors as the
        feature builder (missing/invalid record, unsupported version) plus the embedding
        errors above.
        """
        rec = memory_record.to_dict() if hasattr(memory_record, "to_dict") else memory_record
        vector = self.features.build(rec)
        prediction_id = rec.get("prediction_id") if isinstance(rec, dict) else None
        return self.generate_from_feature_vector(vector, prediction_id=prediction_id)

    # --------------------------------------------------------------------- store
    def store_embedding(self, embedding: Embedding) -> Embedding:
        """Persist an embedding to ``memory_embeddings`` (idempotent upsert; MemoryStore only)."""
        if not embedding.prediction_id:
            raise InvalidFeatureVectorError("cannot store an embedding without a prediction_id")
        self.memory.upsert_embedding(
            MemoryEmbedding(
                prediction_id=embedding.prediction_id,
                embedding_kind=embedding.embedding_kind,
                model_name=_MODEL_NAME,
                dim=embedding.dimension,
                vector=list(embedding.vector),
                schema_version=embedding.schema_version,
            )
        )
        logger.info(
            "similarity embedding stored for %s (embedding=%s dim=%d)",
            embedding.prediction_id, EMBEDDING_VERSION, embedding.dimension,
        )
        return embedding

    def _has_embedding(self, prediction_id: str) -> bool:
        existing = self.memory.get_embedding(prediction_id, EMBEDDING_KIND)
        return existing is not None and existing.vector is not None

    def build_and_store(self, memory_record: Any, *, overwrite: bool = False) -> Embedding | None:
        """Generate and store an embedding for one Memory Record.

        Returns the stored :class:`Embedding`, or ``None`` when a populated embedding already
        exists and ``overwrite`` is ``False`` (idempotent skip).
        """
        rec = memory_record.to_dict() if hasattr(memory_record, "to_dict") else memory_record
        prediction_id = rec.get("prediction_id")
        if not overwrite and prediction_id and self._has_embedding(prediction_id):
            return None
        return self.store_embedding(self.generate_embedding(rec))

    def rebuild_embedding(self, prediction_id: str) -> Embedding:
        """Regenerate and overwrite the embedding for one prediction (fetches its record)."""
        record = self.retrieval.get_record(prediction_id)
        embedding = self.build_and_store(record, overwrite=True)
        assert embedding is not None  # overwrite path always stores
        logger.info("similarity embedding rebuilt for %s", prediction_id)
        return embedding

    # ------------------------------------------------------------------ backfill
    def backfill_embeddings(self, limit: int | None = None) -> EmbeddingBackfillSummary:
        """Embed every enriched Memory Record that has no populated embedding yet.

        Discovers candidates via the Retrieval Engine (built records only — those the Memory
        Builder has enriched), skips ones already embedded, and is fully idempotent: a second
        pass embeds nothing new. One record's failure is logged and counted, never aborting the
        batch or corrupting prior embeddings.
        """
        scanned = embedded = skipped = failed = 0
        cursor: str | None = None
        done = False
        while not done:
            page = self.retrieval.search(limit=_PAGE, cursor=cursor)
            for record in page.records:
                data = record.to_dict()
                metadata = data.get("metadata") or {}
                if not metadata.get("built"):
                    continue  # not enriched → not an embedding candidate
                scanned += 1
                prediction_id = data.get("prediction_id")
                if prediction_id and self._has_embedding(prediction_id):
                    skipped += 1
                else:
                    try:
                        self.build_and_store(record, overwrite=True)
                        embedded += 1
                    except Exception as exc:  # noqa: BLE001 - one bad record must not stop the batch
                        failed += 1
                        logger.warning("embedding backfill failed for %s: %s", prediction_id, exc)
                if limit is not None and scanned >= limit:
                    done = True
                    break
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        summary = EmbeddingBackfillSummary(scanned=scanned, embedded=embedded, skipped=skipped, failed=failed)
        logger.info(
            "similarity embedding backfill: scanned=%d embedded=%d skipped=%d failed=%d (v=%s dim=%d)",
            scanned, embedded, skipped, failed, EMBEDDING_VERSION, VECTOR_DIM,
        )
        return summary
