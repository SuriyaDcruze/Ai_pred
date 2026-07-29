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


class InvalidDatasetError(LearningError):
    """The input to pattern extraction is not a well-formed Learning Dataset."""


class UnknownDimensionError(LearningError):
    """A pattern dimension was requested that the extractor does not know."""


class DuplicatePatternError(LearningError):
    """Two candidate patterns share an identifier (a determinism/keying bug)."""


class InconsistentEvidenceError(LearningError):
    """A pattern's evidence_count does not match its list of supporting prediction ids."""


class MalformedPatternError(LearningError):
    """The input to statistical validation is not a well-formed candidate pattern."""


class StatisticsError(LearningError):
    """A statistic could not be computed from the evidence (e.g. non-finite realised R)."""


class UnknownCorrectionError(LearningError):
    """An unknown multiple-comparison correction strategy was requested."""


class InvalidValidationError(LearningError):
    """The input to the Recommendation Engine is not a well-formed validation result/pattern."""


class MissingEvidenceError(LearningError):
    """A validated pattern has no supporting evidence available to build a recommendation."""


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


# --------------------------------------------------------------------------- candidate pattern
def _pattern_id(learning_version: str, dataset_version: str, grouping_key: str, grouping_value: str) -> str:
    """A deterministic id for a pattern — a function of its identity, so the same dataset always
    yields the same ids (never a random UUID)."""
    raw = f"{learning_version}|{dataset_version}|{grouping_key}={grouping_value}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CandidatePattern:
    """A recurring historical condition (a group of completed decisions sharing a key/value).

    **Metadata + evidence only** — no computed statistics, confidence, or recommendation. Its
    id is deterministic (a function of version + grouping), and it keeps the supporting
    ``prediction_ids`` so any later statistic traces back to its originating records. In
    Milestone 2 every returned pattern is a ``HYPOTHESIS`` (never ``VALIDATED``).
    """

    pattern_id: str
    learning_version: str
    dataset_version: str
    pattern_type: str
    grouping_key: str
    grouping_value: str
    evidence_count: int
    prediction_ids: tuple[str, ...]
    corpus_size: int
    status: LearningStatus
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if self.evidence_count != len(self.prediction_ids):
            raise InconsistentEvidenceError(
                f"{self.pattern_id}: evidence_count {self.evidence_count} != {len(self.prediction_ids)} ids"
            )

    @classmethod
    def create(
        cls, *, learning_version: str, dataset_version: str, pattern_type: str,
        grouping_key: str, grouping_value: str, prediction_ids: "list[str] | tuple[str, ...]",
        corpus_size: int, status: LearningStatus,
    ) -> "CandidatePattern":
        """Build a pattern with a deterministic id and sorted evidence."""
        ids = tuple(sorted(prediction_ids))
        return cls(
            pattern_id=_pattern_id(learning_version, dataset_version, grouping_key, grouping_value),
            learning_version=learning_version, dataset_version=dataset_version,
            pattern_type=pattern_type, grouping_key=grouping_key, grouping_value=grouping_value,
            evidence_count=len(ids), prediction_ids=ids, corpus_size=corpus_size, status=status,
        )

    def stable_dict(self) -> dict[str, Any]:
        """Deterministic content (excludes ``created_at``) — for the extraction checksum."""
        return {
            "pattern_id": self.pattern_id, "pattern_type": self.pattern_type,
            "grouping_key": self.grouping_key, "grouping_value": self.grouping_value,
            "evidence_count": self.evidence_count, "prediction_ids": list(self.prediction_ids),
            "status": self.status.value,
        }

    def to_row(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id, "run_id": None,
            "learning_version": self.learning_version, "dataset_version": self.dataset_version,
            "pattern_type": self.pattern_type, "grouping_key": self.grouping_key,
            "grouping_value": self.grouping_value, "evidence_count": self.evidence_count,
            "prediction_ids_json": json.dumps(list(self.prediction_ids)),
            "corpus_size": self.corpus_size, "status": self.status.value,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "CandidatePattern":
        ids = json.loads(_get(row, "prediction_ids_json") or "[]")
        return cls(
            pattern_id=_get(row, "pattern_id"), learning_version=_get(row, "learning_version"),
            dataset_version=_get(row, "dataset_version"), pattern_type=_get(row, "pattern_type"),
            grouping_key=_get(row, "grouping_key"), grouping_value=_get(row, "grouping_value"),
            evidence_count=int(_get(row, "evidence_count", 0)), prediction_ids=tuple(ids),
            corpus_size=int(_get(row, "corpus_size", 0)),
            status=LearningStatus(_get(row, "status", LearningStatus.HYPOTHESIS.value)),
            created_at=_get(row, "created_at"),
        )


@dataclass(frozen=True)
class PatternExtractionResult:
    """The result of one pattern-extraction pass over a Learning Dataset."""

    patterns: tuple[CandidatePattern, ...]
    status: LearningStatus              # INSUFFICIENT_DATA (no patterns) or HYPOTHESIS
    corpus_size: int
    dimensions: tuple[str, ...]
    insufficient_groups: int            # groups below min_evidence (dropped, not silent)
    learning_version: str
    dataset_version: str
    checksum: str
    min_evidence: int
    created_at: str = field(default_factory=_utc_now_iso)

    @property
    def pattern_count(self) -> int:
        return len(self.patterns)


# ---------------------------------------------------------------- statistical validation (M3)
def _round(value: float | None, ndigits: int = 10) -> float | None:
    """Round a float for a stable, cross-run checksum; passes through None / non-finite."""
    if value is None:
        return None
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):  # NaN / ±inf carried verbatim
        return v
    return round(v, ndigits)


@dataclass(frozen=True)
class ConfidenceInterval:
    """A confidence interval on a rate — the interval is reported, never the point estimate
    alone. ``quality`` is a coarse label of the interval's width (a wide CI is not actionable)."""

    low: float
    high: float
    width: float
    quality: str                     # HIGH | MODERATE | LOW (narrower ⇒ higher quality)
    method: str = "wilson"
    level: float = 0.95

    def stable_dict(self) -> dict[str, Any]:
        return {
            "low": _round(self.low), "high": _round(self.high), "width": _round(self.width),
            "quality": self.quality, "method": self.method, "level": self.level,
        }


@dataclass(frozen=True)
class Significance:
    """The outcome of a two-sided significance test of a pattern's win rate vs a baseline
    (default: a coin flip). ``significant`` is the **raw** verdict, *before* the run's
    multiple-comparison correction (which is applied across the family of patterns)."""

    p_value: float
    z_score: float
    baseline: float
    significant: bool
    test: str = "two_proportion_z"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "p_value": _round(self.p_value), "z_score": _round(self.z_score),
            "baseline": _round(self.baseline), "significant": self.significant, "test": self.test,
        }


@dataclass(frozen=True)
class ValidatedPattern:
    """A candidate pattern after statistical validation — descriptive statistics + a confidence
    interval + a (corrected) significance verdict + a lifecycle ``status``.

    A pattern becomes ``VALIDATED`` only when it clears the minimum sample, is significant **after**
    multiple-comparison correction, and its confidence interval excludes the baseline; below the
    sample floor it is ``INSUFFICIENT_DATA``; otherwise it stays a ``HYPOTHESIS``. **No
    recommendation** is produced here (that is a later milestone). Its ``pattern_key`` is the
    deterministic pattern identity (a function of version + grouping), so the same logical pattern
    always carries the same key; every field traces back to the supporting ``evidence_count``."""

    pattern_key: str
    learning_version: str
    dataset_version: str
    pattern_type: str
    grouping_key: str
    grouping_value: str
    sample_size: int
    wins: int
    losses: int
    win_rate: float
    loss_rate: float
    average_r: float
    expectancy: float
    profit_factor: float | None
    max_drawdown_r: float | None
    avg_holding_bars: float | None
    confidence_interval: ConfidenceInterval
    significance: Significance
    correction_method: str
    correction_significant: bool
    consistency_score: float | None
    status: LearningStatus
    evidence_count: int
    run_id: str | None = None
    created_at: str = field(default_factory=_utc_now_iso)

    def stable_dict(self) -> dict[str, Any]:
        """Deterministic content (excludes ``created_at`` / ``run_id``) — for the run checksum."""
        return {
            "pattern_key": self.pattern_key, "pattern_type": self.pattern_type,
            "grouping_key": self.grouping_key, "grouping_value": self.grouping_value,
            "sample_size": self.sample_size, "wins": self.wins, "losses": self.losses,
            "win_rate": _round(self.win_rate), "loss_rate": _round(self.loss_rate),
            "average_r": _round(self.average_r), "expectancy": _round(self.expectancy),
            "profit_factor": _round(self.profit_factor), "max_drawdown_r": _round(self.max_drawdown_r),
            "avg_holding_bars": _round(self.avg_holding_bars),
            "confidence_interval": self.confidence_interval.stable_dict(),
            "significance": self.significance.stable_dict(),
            "correction_method": self.correction_method,
            "correction_significant": self.correction_significant,
            "consistency_score": _round(self.consistency_score), "status": self.status.value,
            "evidence_count": self.evidence_count,
        }

    def to_row(self) -> dict[str, Any]:
        ci, sig = self.confidence_interval, self.significance
        return {
            "pattern_key": self.pattern_key, "run_id": self.run_id,
            "learning_version": self.learning_version, "dataset_version": self.dataset_version,
            "pattern_type": self.pattern_type, "grouping_key": self.grouping_key,
            "grouping_value": self.grouping_value, "sample_size": self.sample_size,
            "wins": self.wins, "losses": self.losses, "win_rate": self.win_rate,
            "loss_rate": self.loss_rate, "average_r": self.average_r, "expectancy": self.expectancy,
            "profit_factor": self.profit_factor, "max_drawdown_r": self.max_drawdown_r,
            "avg_holding_bars": self.avg_holding_bars, "ci_low": ci.low, "ci_high": ci.high,
            "ci_width": ci.width, "ci_quality": ci.quality, "p_value": sig.p_value,
            "z_score": sig.z_score, "baseline": sig.baseline, "significant": int(sig.significant),
            "correction_method": self.correction_method,
            "correction_significant": int(self.correction_significant),
            "consistency_score": self.consistency_score, "status": self.status.value,
            "evidence_count": self.evidence_count, "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "ValidatedPattern":
        ci = ConfidenceInterval(
            low=_get(row, "ci_low"), high=_get(row, "ci_high"), width=_get(row, "ci_width"),
            quality=_get(row, "ci_quality"),
        )
        sig = Significance(
            p_value=_get(row, "p_value"), z_score=_get(row, "z_score"),
            baseline=_get(row, "baseline"), significant=bool(_get(row, "significant", 0)),
        )
        return cls(
            pattern_key=_get(row, "pattern_key"), learning_version=_get(row, "learning_version"),
            dataset_version=_get(row, "dataset_version"), pattern_type=_get(row, "pattern_type"),
            grouping_key=_get(row, "grouping_key"), grouping_value=_get(row, "grouping_value"),
            sample_size=int(_get(row, "sample_size", 0)), wins=int(_get(row, "wins", 0)),
            losses=int(_get(row, "losses", 0)), win_rate=_get(row, "win_rate"),
            loss_rate=_get(row, "loss_rate"), average_r=_get(row, "average_r"),
            expectancy=_get(row, "expectancy"), profit_factor=_get(row, "profit_factor"),
            max_drawdown_r=_get(row, "max_drawdown_r"), avg_holding_bars=_get(row, "avg_holding_bars"),
            confidence_interval=ci, significance=sig,
            correction_method=_get(row, "correction_method"),
            correction_significant=bool(_get(row, "correction_significant", 0)),
            consistency_score=_get(row, "consistency_score"),
            status=LearningStatus(_get(row, "status", LearningStatus.HYPOTHESIS.value)),
            evidence_count=int(_get(row, "evidence_count", 0)), run_id=_get(row, "run_id"),
            created_at=_get(row, "created_at"),
        )


@dataclass(frozen=True)
class ValidationResult:
    """The result of one statistical-validation pass over a set of candidate patterns.

    Deterministic: identical dataset + patterns + config always yield identical validated
    patterns and the same ``checksum``. ``hypotheses_tested`` records how many patterns entered
    the family (the count the multiple-comparison correction accounts for)."""

    validated_patterns: tuple[ValidatedPattern, ...]
    status: LearningStatus              # run-level: VALIDATED / HYPOTHESIS / INSUFFICIENT_DATA
    corpus_size: int
    validated_count: int
    hypothesis_count: int
    insufficient_count: int
    hypotheses_tested: int
    correction_method: str
    min_sample: int
    alpha: float
    baseline: float
    learning_version: str
    dataset_version: str
    checksum: str
    validation_duration_ms: float
    created_at: str = field(default_factory=_utc_now_iso)

    @property
    def pattern_count(self) -> int:
        return len(self.validated_patterns)


# ---------------------------------------------------------------- recommendations (M4)
class RecommendationType(str, Enum):
    """The performance/stability character of a validated pattern (part of the recommendation's
    deterministic identity). Extend by adding a member — existing keys are unchanged."""

    HISTORICAL_STRENGTH = "HISTORICAL_STRENGTH"     # win rate significantly ABOVE the baseline
    HISTORICAL_WEAKNESS = "HISTORICAL_WEAKNESS"     # win rate significantly BELOW the baseline
    UNSTABLE_BEHAVIOUR = "UNSTABLE_BEHAVIOUR"       # win rate not stable across sub-periods
    STABLE_BEHAVIOUR = "STABLE_BEHAVIOUR"           # (reserved for extension)


class RecommendationConfidence(str, Enum):
    """Confidence in **communicating** the observation — NOT statistical significance. Derived
    deterministically from sample size, CI width, consistency, and evidence quality."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


def _recommendation_key(learning_version: str, pattern_key: str, recommendation_type: str) -> str:
    """The deterministic identity key of a recommendation (a function of the validated pattern +
    its type + the learning version) — the same logical recommendation always keys the same."""
    return f"{learning_version}|{pattern_key}|{recommendation_type}"


@dataclass(frozen=True)
class Recommendation:
    """An evidence-bound **descriptive** historical observation about a validated pattern.

    **Never advice, never a prediction.** It restates M3's already-computed statistics in
    plain language, carries the supporting ``prediction_id``s (full auditability), always lists
    ``limitations``, and tags a **communication** confidence (independent of significance). Its
    ``recommendation_id`` is deterministic (a function of the pattern key + type + version), so
    identical inputs always yield identical recommendations."""

    recommendation_id: str
    recommendation_key: str
    recommendation_hash: str
    learning_version: str
    dataset_version: str
    pattern_key: str
    pattern_hash: str
    recommendation_type: RecommendationType
    recommendation_category: str
    title: str
    summary: str
    detailed_explanation: str
    statistical_basis: str
    evidence_count: int
    sample_size: int
    confidence_interval: ConfidenceInterval
    significance: Significance
    consistency_score: float | None
    recommendation_confidence: RecommendationConfidence
    supporting_prediction_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    run_id: str | None = None
    generated_at: str = field(default_factory=_utc_now_iso)

    def stable_dict(self) -> dict[str, Any]:
        """Deterministic content (excludes ``generated_at`` / ``run_id``) — for the run checksum."""
        return {
            "recommendation_id": self.recommendation_id, "recommendation_key": self.recommendation_key,
            "recommendation_hash": self.recommendation_hash, "pattern_key": self.pattern_key,
            "pattern_hash": self.pattern_hash, "recommendation_type": self.recommendation_type.value,
            "recommendation_category": self.recommendation_category, "title": self.title,
            "summary": self.summary, "detailed_explanation": self.detailed_explanation,
            "statistical_basis": self.statistical_basis, "evidence_count": self.evidence_count,
            "sample_size": self.sample_size,
            "confidence_interval": self.confidence_interval.stable_dict(),
            "significance": self.significance.stable_dict(),
            "consistency_score": _round(self.consistency_score),
            "recommendation_confidence": self.recommendation_confidence.value,
            "supporting_prediction_ids": list(self.supporting_prediction_ids),
            "limitations": list(self.limitations),
        }

    def to_row(self) -> dict[str, Any]:
        ci, sig = self.confidence_interval, self.significance
        return {
            "recommendation_id": self.recommendation_id, "recommendation_key": self.recommendation_key,
            "recommendation_hash": self.recommendation_hash, "run_id": self.run_id,
            "learning_version": self.learning_version, "dataset_version": self.dataset_version,
            "pattern_key": self.pattern_key, "pattern_hash": self.pattern_hash,
            "recommendation_type": self.recommendation_type.value,
            "recommendation_category": self.recommendation_category, "title": self.title,
            "summary": self.summary, "detailed_explanation": self.detailed_explanation,
            "statistical_basis": self.statistical_basis, "evidence_count": self.evidence_count,
            "sample_size": self.sample_size, "ci_low": ci.low, "ci_high": ci.high,
            "ci_width": ci.width, "ci_quality": ci.quality, "p_value": sig.p_value,
            "z_score": sig.z_score, "baseline": sig.baseline, "significant": int(sig.significant),
            "consistency_score": self.consistency_score,
            "recommendation_confidence": self.recommendation_confidence.value,
            "supporting_prediction_ids_json": json.dumps(list(self.supporting_prediction_ids)),
            "limitations_json": json.dumps(list(self.limitations)), "generated_at": self.generated_at,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Recommendation":
        ci = ConfidenceInterval(
            low=_get(row, "ci_low"), high=_get(row, "ci_high"), width=_get(row, "ci_width"),
            quality=_get(row, "ci_quality"),
        )
        sig = Significance(
            p_value=_get(row, "p_value"), z_score=_get(row, "z_score"),
            baseline=_get(row, "baseline"), significant=bool(_get(row, "significant", 0)),
        )
        return cls(
            recommendation_id=_get(row, "recommendation_id"),
            recommendation_key=_get(row, "recommendation_key"),
            recommendation_hash=_get(row, "recommendation_hash"), run_id=_get(row, "run_id"),
            learning_version=_get(row, "learning_version"), dataset_version=_get(row, "dataset_version"),
            pattern_key=_get(row, "pattern_key"), pattern_hash=_get(row, "pattern_hash"),
            recommendation_type=RecommendationType(_get(row, "recommendation_type")),
            recommendation_category=_get(row, "recommendation_category"), title=_get(row, "title"),
            summary=_get(row, "summary"), detailed_explanation=_get(row, "detailed_explanation"),
            statistical_basis=_get(row, "statistical_basis"),
            evidence_count=int(_get(row, "evidence_count", 0)),
            sample_size=int(_get(row, "sample_size", 0)), confidence_interval=ci, significance=sig,
            consistency_score=_get(row, "consistency_score"),
            recommendation_confidence=RecommendationConfidence(_get(row, "recommendation_confidence")),
            supporting_prediction_ids=tuple(json.loads(_get(row, "supporting_prediction_ids_json") or "[]")),
            limitations=tuple(json.loads(_get(row, "limitations_json") or "[]")),
            generated_at=_get(row, "generated_at"),
        )


@dataclass(frozen=True)
class RecommendationResult:
    """The result of one recommendation-generation pass over a validation result.

    Deterministic: identical validation + candidates + config always yield identical
    recommendations and the same ``checksum``. A run with no VALIDATED patterns → status
    ``INSUFFICIENT_DATA`` and no recommendations (fabricates nothing)."""

    recommendations: tuple[Recommendation, ...]
    status: LearningStatus
    validated_patterns_processed: int
    recommendations_created: int
    rejected: int
    confidence_distribution: dict[str, int]
    learning_version: str
    dataset_version: str
    checksum: str
    generation_duration_ms: float
    generated_at: str = field(default_factory=_utc_now_iso)

    @property
    def recommendation_count(self) -> int:
        return len(self.recommendations)
