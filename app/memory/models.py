"""Domain models for the Historical Memory satellite tables (Sprint 2 · Milestone 1).

Historical Memory does **not** re-store what `predictions` already holds. These dataclasses
represent **only the new satellite rows** — the reasoning narrative, the embedding slot, and
the derived aggregates — each keyed back to a prediction by ``prediction_id`` (or, for
aggregates, standing on their own as a rollup). The canonical, composed *Memory Record*
(prediction + satellites) is a **read model** that belongs to the Retrieval Engine in a
later milestone; it is deliberately not defined here, so nothing in this module duplicates a
field that lives on the `predictions` table.

This module is pure domain + persistence mapping (``to_row`` / ``from_row``). It imports
**nothing** from the Prediction or Outcome engines, and contains **no** memory-building,
retrieval, or aggregation logic — those arrive in Milestones 2–4.
"""

from __future__ import annotations

import json
import struct
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

#: Current schema version of the satellite records. Bumped only when a record's shape
#: changes in a later migration; lets future readers stay backward compatible.
MEMORY_SCHEMA_VERSION: int = 1

#: Default embedding kind used when the Similarity Engine (Vol 14) later populates vectors.
#: Stored as a free string so new kinds can be added without a schema change.
DEFAULT_EMBEDDING_KIND: str = "context_v1"


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (the storage format for timestamps).

    Defined locally so the ``app.memory`` package stays independent of ``app.forward_testing``.
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """Read a column by name from a row, tolerating missing keys (forward compatible)."""
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _load_json(raw: Any) -> dict[str, Any]:
    """Parse a JSON column into a dict, never raising on bad/empty data."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def pack_vector(values: list[float] | None) -> bytes | None:
    """Serialise a float vector to a compact little-endian float32 blob.

    Returns ``None`` for a missing vector so the embedding slot can stay empty until the
    Similarity Engine fills it. This is a storage encoding only — no similarity math.
    """
    if values is None:
        return None
    return struct.pack(f"<{len(values)}f", *values)


def unpack_vector(blob: bytes | None) -> list[float] | None:
    """Inverse of :func:`pack_vector`; returns ``None`` when there is no stored vector."""
    if not blob:
        return None
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


class AggregateDimension(str, Enum):
    """The dimensions a performance rollup can be grouped by.

    Mirrors the retrieval/analytics dimensions in the Sprint 2 plan; stored as the string
    value in ``memory_aggregates.dimension``.
    """

    OVERALL = "overall"
    SYMBOL = "symbol"
    SECTOR = "sector"
    TIMEFRAME = "timeframe"
    REGIME = "regime"
    CONFIDENCE_BUCKET = "confidence_bucket"
    OUTCOME = "outcome"


@dataclass
class MemoryReasoning:
    """The structured "why" behind one decision — 1:1 with a prediction.

    Holds the explanation that ``predictions`` does not: a free-text rationale, the
    structured factors that drove the call, and a snapshot of the rule checklist. The numeric
    ``confidence`` mirrors the decision-time confidence so it can be indexed and queried
    without unpacking JSON.
    """

    prediction_id: str
    confidence: float | None = None
    rationale: str | None = None
    factors: dict[str, Any] = field(default_factory=dict)
    rule_check: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)
    schema_version: int = MEMORY_SCHEMA_VERSION

    def to_row(self) -> dict[str, Any]:
        """Flatten to a ``memory_reasoning`` row (column name → value)."""
        return {
            "prediction_id": self.prediction_id,
            "created_at": self.created_at,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "factors_json": json.dumps(self.factors) if self.factors else None,
            "rule_check_json": json.dumps(self.rule_check) if self.rule_check else None,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "MemoryReasoning":
        """Rebuild from a database row, reading by column name and tolerating gaps."""
        return cls(
            prediction_id=_get(row, "prediction_id"),
            confidence=_get(row, "confidence"),
            rationale=_get(row, "rationale"),
            factors=_load_json(_get(row, "factors_json")),
            rule_check=_load_json(_get(row, "rule_check_json")),
            created_at=_get(row, "created_at"),
            schema_version=int(_get(row, "schema_version", MEMORY_SCHEMA_VERSION)),
        )


@dataclass
class MemoryEmbedding:
    """A vector representation of a historical decision — the Similarity placeholder.

    Multiple ``embedding_kind`` rows may exist per prediction so a new embedding model can be
    added alongside an old one. ``vector`` is a packed float32 blob (see :func:`pack_vector`)
    and stays ``None`` until the Similarity Engine populates it.
    """

    prediction_id: str
    embedding_kind: str = DEFAULT_EMBEDDING_KIND
    model_name: str | None = None
    dim: int | None = None
    vector: list[float] | None = None
    embedding_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=_utc_now_iso)
    schema_version: int = MEMORY_SCHEMA_VERSION

    def to_row(self) -> dict[str, Any]:
        """Flatten to a ``memory_embeddings`` row; the vector is packed to a blob."""
        return {
            "embedding_id": self.embedding_id,
            "prediction_id": self.prediction_id,
            "embedding_kind": self.embedding_kind,
            "model_name": self.model_name,
            "dim": self.dim if self.dim is not None else (len(self.vector) if self.vector else None),
            "vector": pack_vector(self.vector),
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "MemoryEmbedding":
        """Rebuild from a database row, unpacking the stored blob back to floats."""
        return cls(
            embedding_id=_get(row, "embedding_id"),
            prediction_id=_get(row, "prediction_id"),
            embedding_kind=_get(row, "embedding_kind", DEFAULT_EMBEDDING_KIND),
            model_name=_get(row, "model_name"),
            dim=_get(row, "dim"),
            vector=unpack_vector(_get(row, "vector")),
            created_at=_get(row, "created_at"),
            schema_version=int(_get(row, "schema_version", MEMORY_SCHEMA_VERSION)),
        )


@dataclass
class MemoryAggregate:
    """A derived performance rollup for one (dimension, bucket, model_version).

    Fully derivable from ``predictions`` — never a source of truth. All figures are in
    R-multiples so they are position-size agnostic. This model is a plain container; the
    computation that fills it belongs to the Memory Builder (Milestone 3).
    """

    dimension: AggregateDimension
    bucket: str
    model_version: str = ""
    n_resolved: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float | None = None
    avg_r: float | None = None
    expectancy: float | None = None
    total_r: float | None = None
    profit_factor: float | None = None
    max_drawdown_r: float | None = None
    avg_holding_bars: float | None = None
    updated_at: str = field(default_factory=_utc_now_iso)

    def to_row(self) -> dict[str, Any]:
        """Flatten to a ``memory_aggregates`` row."""
        return {
            "dimension": self.dimension.value,
            "bucket": self.bucket,
            "model_version": self.model_version,
            "n_resolved": self.n_resolved,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "avg_r": self.avg_r,
            "expectancy": self.expectancy,
            "total_r": self.total_r,
            "profit_factor": self.profit_factor,
            "max_drawdown_r": self.max_drawdown_r,
            "avg_holding_bars": self.avg_holding_bars,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "MemoryAggregate":
        """Rebuild from a database row."""
        return cls(
            dimension=AggregateDimension(_get(row, "dimension", AggregateDimension.OVERALL.value)),
            bucket=_get(row, "bucket", ""),
            model_version=_get(row, "model_version", ""),
            n_resolved=int(_get(row, "n_resolved", 0)),
            wins=int(_get(row, "wins", 0)),
            losses=int(_get(row, "losses", 0)),
            win_rate=_get(row, "win_rate"),
            avg_r=_get(row, "avg_r"),
            expectancy=_get(row, "expectancy"),
            total_r=_get(row, "total_r"),
            profit_factor=_get(row, "profit_factor"),
            max_drawdown_r=_get(row, "max_drawdown_r"),
            avg_holding_bars=_get(row, "avg_holding_bars"),
            updated_at=_get(row, "updated_at"),
        )
