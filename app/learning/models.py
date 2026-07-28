"""Domain models for the Behavioural Learning Engine (Sprint 4 · Volume 15 · Milestone 1).

The Behavioural Learning Engine is **descriptive analytics** over completed Historical Memory —
no model training, no inference, read-only. This module defines the **Learning Dataset** (a
deterministic, versioned view of completed decisions), the canonical **learning states**, the
storage model for a learning run, and typed errors. It imports nothing from the Prediction or
Outcome engines.

**Not to be confused with** the legacy meta-model retrainer in `app/training/` (Volume 15's
other sense), which *does* train models via the validated promotion pipeline. This engine
never trains anything.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

#: The learning method version. Bump on any change to how the dataset/analysis is derived.
LEARNING_VERSION: str = "lrn-1"
#: The dataset schema version (the shape of a learning record). Bump on shape changes.
DATASET_VERSION: str = "lds-1"
#: Highest Memory Record schema version this build understands.
SUPPORTED_RECORD_SCHEMA: int = 1
#: Default minimum completed trades for a dataset to be worth analysing; below it the dataset
#: reports INSUFFICIENT_DATA. (Per-pattern statistical thresholds are a later milestone.)
DEFAULT_MIN_CORPUS: int = 30


class LearningStatus(str, Enum):
    """The canonical states every Learning artifact uses (established here; Milestone 1 only
    classifies datasets as INSUFFICIENT_DATA — VALIDATED/HYPOTHESIS are for later milestones)."""

    VALIDATED = "VALIDATED"
    HYPOTHESIS = "HYPOTHESIS"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# --------------------------------------------------------------------------- errors
class LearningError(Exception):
    """Base class for every Behavioural Learning error."""


class InvalidMemoryRecordError(LearningError):
    """The input is not a well-formed Memory Record."""


class IncompleteOutcomeError(LearningError):
    """A record lacks a completed outcome (no realised R / missing status) and cannot be a
    learning row."""


class InconsistentTimestampError(LearningError):
    """A record's outcome timestamp precedes its prediction timestamp."""


class UnsupportedVersionError(LearningError):
    """A record carries a schema version this build does not support."""


class CorruptedMetadataError(LearningError):
    """A record's metadata is present but malformed."""


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _get(record: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        value = record[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


# --------------------------------------------------------------------------- learning record
@dataclass(frozen=True)
class LearningRecord:
    """One completed decision, projected for later statistical analysis.

    Computed **on read** from a Memory Record (never persisted as a duplicate). Every row keeps
    its `prediction_id` + `memory_reference` so any later statistic can be traced to its
    originating historical records.
    """

    prediction_id: str
    outcome: str                     # trade result: WIN / LOSS / EXPIRED / CANCELLED / OPEN
    realised_r: float
    win: bool
    confidence: float | None
    holding_period: int | None
    prediction_timestamp: str | None
    outcome_timestamp: str | None
    symbol: str | None
    sector: str | None
    timeframe: str | None
    market_regime: str | None
    market_phase: str | None
    prediction_model_version: str | None
    feature_version: str | None
    similarity_metadata: dict[str, Any] | None
    memory_reference: dict[str, Any]

    @classmethod
    def from_memory_record(cls, record: Mapping[str, Any]) -> "LearningRecord":
        """Validate a Memory Record (the ``to_dict()`` contract) and project a learning row.

        Raises:
            InvalidMemoryRecordError: not a mapping / missing identity.
            CorruptedMetadataError: metadata present but malformed.
            UnsupportedVersionError: record schema version too new.
            IncompleteOutcomeError: no realised outcome / missing status.
            InconsistentTimestampError: outcome timestamp before prediction timestamp.
        """
        if not isinstance(record, Mapping):
            raise InvalidMemoryRecordError("Memory Record must be a mapping")
        prediction_id = record.get("prediction_id")
        if not prediction_id:
            raise InvalidMemoryRecordError("Memory Record missing prediction_id")

        metadata = record.get("metadata")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise CorruptedMetadataError(f"{prediction_id}: metadata is malformed")
        schema_version = (metadata or {}).get("record_schema_version")
        if schema_version is not None and int(schema_version) > SUPPORTED_RECORD_SCHEMA:
            raise UnsupportedVersionError(
                f"{prediction_id}: record schema {schema_version} > supported {SUPPORTED_RECORD_SCHEMA}"
            )

        realised = record.get("realised_r")
        if realised is None:
            raise IncompleteOutcomeError(f"{prediction_id}: no realised outcome")
        if not record.get("status"):
            raise IncompleteOutcomeError(f"{prediction_id}: missing status")

        created = record.get("created_at")
        resolved = record.get("resolved_at")
        if created and resolved and resolved < created:
            raise InconsistentTimestampError(
                f"{prediction_id}: outcome {resolved!r} precedes prediction {created!r}"
            )

        versions = record.get("versions") if isinstance(record.get("versions"), Mapping) else {}
        embedding = record.get("embedding") if isinstance(record.get("embedding"), Mapping) else None
        similarity_metadata = (
            {"embedding_kind": embedding.get("kind"), "present": embedding.get("present"),
             "dim": embedding.get("dim")}
            if embedding else None
        )
        return cls(
            prediction_id=prediction_id,
            outcome=record.get("trade_result") or record.get("status"),
            realised_r=float(realised),
            win=float(realised) > 0,
            confidence=record.get("confidence"),
            holding_period=record.get("holding_bars"),
            prediction_timestamp=created,
            outcome_timestamp=resolved,
            symbol=record.get("symbol"),
            sector=record.get("sector"),
            timeframe=record.get("timeframe"),
            market_regime=record.get("market_regime"),
            market_phase=record.get("market_phase"),
            prediction_model_version=versions.get("prediction_model_version"),
            feature_version=versions.get("feature_version"),
            similarity_metadata=similarity_metadata,
            memory_reference={
                "prediction_id": prediction_id,
                "record_schema_version": schema_version,
            },
        )

    def stable_dict(self) -> dict[str, Any]:
        """A deterministic dict of the row's content (used for the dataset checksum)."""
        return {
            "prediction_id": self.prediction_id, "outcome": self.outcome,
            "realised_r": self.realised_r, "win": self.win, "confidence": self.confidence,
            "holding_period": self.holding_period,
            "prediction_timestamp": self.prediction_timestamp,
            "outcome_timestamp": self.outcome_timestamp, "symbol": self.symbol,
            "sector": self.sector, "timeframe": self.timeframe,
            "market_regime": self.market_regime, "market_phase": self.market_phase,
            "prediction_model_version": self.prediction_model_version,
            "feature_version": self.feature_version,
            "similarity_metadata": self.similarity_metadata,
        }


def checksum_of(records: "list[LearningRecord] | tuple[LearningRecord, ...]") -> str:
    """A deterministic SHA-256 fingerprint over the ordered records' stable content.

    Excludes volatile fields (generation time, build duration), so the same corpus always
    yields the same checksum.
    """
    payload = json.dumps([r.stable_dict() for r in records], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- dataset
@dataclass(frozen=True)
class LearningDataset:
    """The deterministic, versioned Learning Dataset — the canonical source for every later
    Learning milestone. Computed on read; never a stored duplicate of predictions/memory."""

    records: tuple[LearningRecord, ...]
    corpus_size: int
    dataset_version: str
    learning_version: str
    generated_at: str
    source_versions: dict[str, Any]
    build_duration_ms: float
    checksum: str
    status: LearningStatus | None          # INSUFFICIENT_DATA when thin; None when sufficient
    min_corpus: int
    filter: dict[str, Any] | None = None

    @property
    def is_sufficient(self) -> bool:
        """Whether the corpus clears ``min_corpus`` (i.e. not INSUFFICIENT_DATA)."""
        return self.status is not LearningStatus.INSUFFICIENT_DATA

    def to_run(self, *, run_id: str | None = None, kind: str = "dataset") -> "LearningRun":
        """Project the dataset's metadata to a persistable :class:`LearningRun` (audit/repro)."""
        return LearningRun(
            run_id=run_id or uuid.uuid4().hex,
            kind=kind,
            learning_version=self.learning_version,
            dataset_version=self.dataset_version,
            corpus_size=self.corpus_size,
            checksum=self.checksum,
            status=self.status.value if self.status else None,
            created_at=self.generated_at,
            params_json=json.dumps({"min_corpus": self.min_corpus, "filter": self.filter}),
            source_versions_json=json.dumps(self.source_versions),
            build_duration_ms=self.build_duration_ms,
        )


# --------------------------------------------------------------------------- storage model
@dataclass
class LearningRun:
    """Persistable metadata of one learning run (row of ``learning_runs``)."""

    run_id: str
    kind: str
    learning_version: str
    dataset_version: str
    corpus_size: int
    checksum: str | None = None
    status: str | None = None
    created_at: str = field(default_factory=_utc_now_iso)
    params_json: str | None = None
    source_versions_json: str | None = None
    build_duration_ms: float | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "kind": self.kind, "learning_version": self.learning_version,
            "dataset_version": self.dataset_version, "created_at": self.created_at,
            "corpus_size": self.corpus_size, "checksum": self.checksum, "status": self.status,
            "params_json": self.params_json, "source_versions_json": self.source_versions_json,
            "build_duration_ms": self.build_duration_ms,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "LearningRun":
        return cls(
            run_id=_get(row, "run_id"), kind=_get(row, "kind"),
            learning_version=_get(row, "learning_version"), dataset_version=_get(row, "dataset_version"),
            corpus_size=int(_get(row, "corpus_size", 0)), checksum=_get(row, "checksum"),
            status=_get(row, "status"), created_at=_get(row, "created_at"),
            params_json=_get(row, "params_json"), source_versions_json=_get(row, "source_versions_json"),
            build_duration_ms=_get(row, "build_duration_ms"),
        )
