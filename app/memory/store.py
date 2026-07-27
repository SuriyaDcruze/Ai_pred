"""Memory Store — persistence for the Historical Memory satellite tables (Sprint 2 · M2).

The Memory Store is the **only** component that reads and writes the Historical Memory
satellite tables (``memory_reasoning``, ``memory_embeddings``, ``memory_aggregates``). It is
a pure persistence layer:

* It **does not** build memory, enrich, compute embeddings, compute aggregates, retrieve,
  search, or expose HTTP — those are later milestones.
* It **writes only to the satellite tables**. It never writes ``predictions`` (Sprint 1 owns
  that table), and it imports nothing from the Prediction or Outcome engines.

It mirrors :class:`~app.forward_testing.store.PredictionStore`'s operational guarantees:

* **Thread-safe.** A reentrant lock serialises access to the shared connection (opened with
  ``check_same_thread=False``), so a background worker and a request thread never corrupt
  each other; WAL keeps readers off the writer's back.
* **Atomic writes.** Every write runs inside a transaction that commits on success and rolls
  back on any error.
* **Idempotent upserts.** ``upsert_*`` methods key on the natural identity of each satellite
  (``prediction_id``; ``(prediction_id, embedding_kind)``; ``(dimension, bucket,
  model_version)``), so repeated writes never create duplicate rows and always converge to
  the same final state.
* **Meaningful errors.** Constraint violations become typed exceptions
  (:mod:`app.memory.errors`) — never silent failures.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, NoReturn

from app.database.connection import DEFAULT_DB_PATH, get_connection
from app.database.migrations import run_migrations
from app.memory.errors import (
    MemoryConflictError,
    MemoryForeignKeyError,
    MemoryNotFoundError,
    MemorySchemaError,
    MemoryStoreError,
)
from app.memory.models import (
    DEFAULT_EMBEDDING_KIND,
    MEMORY_SCHEMA_VERSION,
    AggregateDimension,
    MemoryAggregate,
    MemoryEmbedding,
    MemoryReasoning,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


class MemoryStore:
    """CRUD over the Historical Memory satellite tables — persistence only."""

    def __init__(self, path: str = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None):
        """Open the store, ensuring the schema is current.

        Args:
            path: Database path; defaults to the permanent prediction-history database.
            conn: An existing connection to adopt (mainly for tests / sharing); when given,
                ``path`` is ignored.
        """
        self._conn = conn or get_connection(path)
        # Reentrant so a public method may call another while holding the lock.
        self._lock = threading.RLock()
        run_migrations(self._conn)

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _validate_schema_version(version: int) -> None:
        """Reject a record whose ``schema_version`` this build cannot represent."""
        if not isinstance(version, int) or not (1 <= version <= MEMORY_SCHEMA_VERSION):
            raise MemorySchemaError(
                f"unsupported schema_version {version!r} (supported 1..{MEMORY_SCHEMA_VERSION})"
            )

    @staticmethod
    def _raise_integrity(ctx: str, exc: sqlite3.IntegrityError) -> NoReturn:
        """Translate a raw SQLite integrity error into a precise, typed exception."""
        message = str(exc).upper()
        if "FOREIGN KEY" in message:
            raise MemoryForeignKeyError(
                f"{ctx}: referenced prediction does not exist"
            ) from exc
        if "UNIQUE" in message or "PRIMARY KEY" in message:
            raise MemoryConflictError(f"{ctx}: record already exists") from exc
        raise MemoryStoreError(f"{ctx}: integrity error: {exc}") from exc

    def _write(self, ctx: str, sql: str, params: Any) -> sqlite3.Cursor:
        """Run one write inside a transaction; commit on success, roll back + raise on error.

        Structured logging records constraint violations and rollbacks. Never logs record
        *content* (rationale, vectors) — only identities and the operation name.
        """
        try:
            with self._lock, self._conn:  # lock + transaction (auto commit/rollback)
                return self._conn.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            logger.warning("memory %s: constraint violation (rolled back): %s", ctx, exc)
            self._raise_integrity(ctx, exc)
        except sqlite3.Error as exc:
            logger.warning("memory %s: transaction failed (rolled back): %s", ctx, exc)
            raise MemoryStoreError(f"{ctx} failed: {exc}") from exc

    def _fetchone(self, sql: str, params: Any) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: Any = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    @staticmethod
    def _dim_value(dimension: AggregateDimension | str) -> str:
        return dimension.value if isinstance(dimension, AggregateDimension) else str(dimension)

    # ============================================================= memory_reasoning
    def create_reasoning(self, reasoning: MemoryReasoning) -> MemoryReasoning:
        """Insert a reasoning row (strict).

        Raises:
            MemoryConflictError: reasoning already exists for this prediction.
            MemoryForeignKeyError: the prediction does not exist.
            MemorySchemaError: unsupported ``schema_version``.
        """
        self._validate_schema_version(reasoning.schema_version)
        row = reasoning.to_row()
        cols = ", ".join(row)
        placeholders = ", ".join(f":{k}" for k in row)
        self._write("create_reasoning", f"INSERT INTO memory_reasoning ({cols}) VALUES ({placeholders})", row)
        logger.info("memory reasoning created for %s", reasoning.prediction_id)
        return reasoning

    def upsert_reasoning(self, reasoning: MemoryReasoning) -> MemoryReasoning:
        """Insert or update the reasoning for a prediction (idempotent).

        ``created_at`` is preserved on update (it is not in the update set). Repeated calls
        converge to the same final state and never duplicate rows.
        """
        self._validate_schema_version(reasoning.schema_version)
        row = reasoning.to_row()
        cols = ", ".join(row)
        placeholders = ", ".join(f":{k}" for k in row)
        sql = (
            f"INSERT INTO memory_reasoning ({cols}) VALUES ({placeholders}) "
            "ON CONFLICT(prediction_id) DO UPDATE SET "
            "confidence=excluded.confidence, rationale=excluded.rationale, "
            "factors_json=excluded.factors_json, rule_check_json=excluded.rule_check_json, "
            "schema_version=excluded.schema_version"
        )
        self._write("upsert_reasoning", sql, row)
        logger.info("memory reasoning upserted for %s", reasoning.prediction_id)
        return reasoning

    def update_reasoning(self, reasoning: MemoryReasoning) -> MemoryReasoning:
        """Update an existing reasoning row.

        Raises:
            MemoryNotFoundError: no reasoning exists for the prediction.
            MemorySchemaError: unsupported ``schema_version``.
        """
        self._validate_schema_version(reasoning.schema_version)
        row = reasoning.to_row()
        sql = (
            "UPDATE memory_reasoning SET confidence=:confidence, rationale=:rationale, "
            "factors_json=:factors_json, rule_check_json=:rule_check_json, "
            "schema_version=:schema_version WHERE prediction_id=:prediction_id"
        )
        cursor = self._write("update_reasoning", sql, row)
        if cursor.rowcount == 0:
            raise MemoryNotFoundError(f"no reasoning for prediction {reasoning.prediction_id}")
        logger.info("memory reasoning updated for %s", reasoning.prediction_id)
        return reasoning

    def get_reasoning(self, prediction_id: str) -> MemoryReasoning | None:
        """Fetch the reasoning for a prediction, or ``None``."""
        row = self._fetchone("SELECT * FROM memory_reasoning WHERE prediction_id = ?", (prediction_id,))
        return MemoryReasoning.from_row(row) if row else None

    def reasoning_exists(self, prediction_id: str) -> bool:
        """Whether reasoning exists for a prediction."""
        return self._fetchone("SELECT 1 FROM memory_reasoning WHERE prediction_id = ?", (prediction_id,)) is not None

    def delete_reasoning(self, prediction_id: str) -> bool:
        """Delete a reasoning row (administrative). Returns ``True`` if a row was removed."""
        cursor = self._write("delete_reasoning", "DELETE FROM memory_reasoning WHERE prediction_id = ?", (prediction_id,))
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("memory reasoning deleted for %s", prediction_id)
        return deleted

    # ============================================================ memory_embeddings
    def create_embedding(self, embedding: MemoryEmbedding) -> MemoryEmbedding:
        """Insert an embedding row (strict). Does **not** compute the vector — store only.

        Raises:
            MemoryConflictError: an embedding of this ``embedding_kind`` already exists for
                the prediction.
            MemoryForeignKeyError: the prediction does not exist.
            MemorySchemaError: unsupported ``schema_version``.
        """
        self._validate_schema_version(embedding.schema_version)
        row = embedding.to_row()
        cols = ", ".join(row)
        placeholders = ", ".join(f":{k}" for k in row)
        self._write("create_embedding", f"INSERT INTO memory_embeddings ({cols}) VALUES ({placeholders})", row)
        logger.info("memory embedding created for %s (%s)", embedding.prediction_id, embedding.embedding_kind)
        return embedding

    def upsert_embedding(self, embedding: MemoryEmbedding) -> MemoryEmbedding:
        """Insert or update an embedding, keyed by ``(prediction_id, embedding_kind)`` (idempotent).

        ``embedding_id`` and ``created_at`` are preserved on update.
        """
        self._validate_schema_version(embedding.schema_version)
        row = embedding.to_row()
        cols = ", ".join(row)
        placeholders = ", ".join(f":{k}" for k in row)
        sql = (
            f"INSERT INTO memory_embeddings ({cols}) VALUES ({placeholders}) "
            "ON CONFLICT(prediction_id, embedding_kind) DO UPDATE SET "
            "model_name=excluded.model_name, dim=excluded.dim, vector=excluded.vector, "
            "schema_version=excluded.schema_version"
        )
        self._write("upsert_embedding", sql, row)
        logger.info("memory embedding upserted for %s (%s)", embedding.prediction_id, embedding.embedding_kind)
        return embedding

    def update_embedding(self, embedding: MemoryEmbedding) -> MemoryEmbedding:
        """Update an existing embedding, addressed by ``(prediction_id, embedding_kind)``.

        Raises:
            MemoryNotFoundError: no embedding of that kind exists for the prediction.
        """
        self._validate_schema_version(embedding.schema_version)
        row = embedding.to_row()
        sql = (
            "UPDATE memory_embeddings SET model_name=:model_name, dim=:dim, vector=:vector, "
            "schema_version=:schema_version WHERE prediction_id=:prediction_id AND embedding_kind=:embedding_kind"
        )
        cursor = self._write("update_embedding", sql, row)
        if cursor.rowcount == 0:
            raise MemoryNotFoundError(
                f"no embedding '{embedding.embedding_kind}' for prediction {embedding.prediction_id}"
            )
        logger.info("memory embedding updated for %s (%s)", embedding.prediction_id, embedding.embedding_kind)
        return embedding

    def get_embedding(
        self, prediction_id: str, embedding_kind: str = DEFAULT_EMBEDDING_KIND
    ) -> MemoryEmbedding | None:
        """Fetch one embedding by prediction + kind, or ``None``."""
        row = self._fetchone(
            "SELECT * FROM memory_embeddings WHERE prediction_id = ? AND embedding_kind = ?",
            (prediction_id, embedding_kind),
        )
        return MemoryEmbedding.from_row(row) if row else None

    def list_embeddings(self, prediction_id: str) -> list[MemoryEmbedding]:
        """All embeddings for a prediction (every kind), ordered by kind."""
        rows = self._fetchall(
            "SELECT * FROM memory_embeddings WHERE prediction_id = ? ORDER BY embedding_kind",
            (prediction_id,),
        )
        return [MemoryEmbedding.from_row(r) for r in rows]

    def embedding_exists(self, prediction_id: str, embedding_kind: str = DEFAULT_EMBEDDING_KIND) -> bool:
        """Whether an embedding of the given kind exists for a prediction."""
        return self._fetchone(
            "SELECT 1 FROM memory_embeddings WHERE prediction_id = ? AND embedding_kind = ?",
            (prediction_id, embedding_kind),
        ) is not None

    # ============================================================ memory_aggregates
    def upsert_aggregate(self, aggregate: MemoryAggregate) -> MemoryAggregate:
        """Insert or update a rollup keyed by ``(dimension, bucket, model_version)`` (idempotent).

        This is persistence only — the **values** are computed by the Memory Builder (M3);
        the store just writes whatever it is given.
        """
        row = aggregate.to_row()
        cols = ", ".join(row)
        placeholders = ", ".join(f":{k}" for k in row)
        sql = (
            f"INSERT INTO memory_aggregates ({cols}) VALUES ({placeholders}) "
            "ON CONFLICT(dimension, bucket, model_version) DO UPDATE SET "
            "n_resolved=excluded.n_resolved, wins=excluded.wins, losses=excluded.losses, "
            "win_rate=excluded.win_rate, avg_r=excluded.avg_r, expectancy=excluded.expectancy, "
            "total_r=excluded.total_r, profit_factor=excluded.profit_factor, "
            "max_drawdown_r=excluded.max_drawdown_r, avg_holding_bars=excluded.avg_holding_bars, "
            "updated_at=excluded.updated_at"
        )
        self._write("upsert_aggregate", sql, row)
        logger.info(
            "memory aggregate upserted: %s/%s (model=%s)",
            aggregate.dimension.value, aggregate.bucket, aggregate.model_version or "*",
        )
        return aggregate

    def get_aggregate(
        self, dimension: AggregateDimension | str, bucket: str, model_version: str = ""
    ) -> MemoryAggregate | None:
        """Fetch one rollup by its composite key, or ``None``."""
        row = self._fetchone(
            "SELECT * FROM memory_aggregates WHERE dimension = ? AND bucket = ? AND model_version = ?",
            (self._dim_value(dimension), bucket, model_version),
        )
        return MemoryAggregate.from_row(row) if row else None

    def list_aggregates(self, dimension: AggregateDimension | str | None = None) -> list[MemoryAggregate]:
        """All rollups, optionally filtered to one dimension; ordered by dimension, bucket."""
        if dimension is None:
            rows = self._fetchall("SELECT * FROM memory_aggregates ORDER BY dimension, bucket")
        else:
            rows = self._fetchall(
                "SELECT * FROM memory_aggregates WHERE dimension = ? ORDER BY bucket",
                (self._dim_value(dimension),),
            )
        return [MemoryAggregate.from_row(r) for r in rows]

    def aggregate_exists(
        self, dimension: AggregateDimension | str, bucket: str, model_version: str = ""
    ) -> bool:
        """Whether a rollup exists for the composite key."""
        return self._fetchone(
            "SELECT 1 FROM memory_aggregates WHERE dimension = ? AND bucket = ? AND model_version = ?",
            (self._dim_value(dimension), bucket, model_version),
        ) is not None

    def delete_aggregate(
        self, dimension: AggregateDimension | str, bucket: str, model_version: str = ""
    ) -> bool:
        """Delete one rollup. Returns ``True`` if a row was removed."""
        cursor = self._write(
            "delete_aggregate",
            "DELETE FROM memory_aggregates WHERE dimension = ? AND bucket = ? AND model_version = ?",
            (self._dim_value(dimension), bucket, model_version),
        )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
