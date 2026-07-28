"""Feature Vector Builder — Memory Record → deterministic numerical vector (Sprint 3 · M1).

The **canonical, versioned** feature representation of a historical prediction. It is a
**pure transformation**: given the same Memory Record it always produces the exact same
vector. It **never** trains a model, generates an embedding, compares/ranks vectors, or
touches Historical Memory — it only reads the Memory Record *contract* (the mapping produced
by ``RetrievalEngine.MemoryRecord.to_dict()``) and imports neither engine.

Determinism guarantees:

* **Fixed, immutable feature order** (`_SPECS`) — the vector layout never changes within a
  ``feature_version``. Changing order or encoding requires a **new** ``feature_version``.
* **Stable hashing** for open-vocabulary categoricals (sector, model versions) via
  ``hashlib`` — never Python's salted ``hash()`` — so buckets are identical across processes.
* **Documented encodings** — fixed one-hot vocabularies for enums, clamped min-max scaling
  for numerics, explicit *present* flags so a missing value is distinct from a real zero.

Feature version ``sim-fv-1`` → **dimension 100** (see the group breakdown in Volume 14).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from app.similarity.models import (
    FeatureVector,
    InvalidMemoryRecordError,
    MissingFieldError,
    SimilarityError,
    UnsupportedVersionError,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

#: The encoding contract. Bump on ANY change to feature order, vocab, or normalization.
FEATURE_VERSION: str = "sim-fv-1"
#: Revision of the feature schema shape (paired with FEATURE_VERSION).
SCHEMA_VERSION: int = 1
#: Highest Memory Record schema version this builder understands (memory schema v1).
SUPPORTED_RECORD_SCHEMA: int = 1

# --------------------------------------------------------------------------- vocabularies
# Fixed one-hot vocabularies for enums. An unknown value encodes as all-zeros (no crash).
REGIME_VOCAB: tuple[str, ...] = ("BULL", "BEAR", "NEUTRAL", "RANGE", "SIDEWAYS", "VOLATILE")
PHASE_VOCAB: tuple[str, ...] = ("ACCUMULATION", "MARKUP", "DISTRIBUTION", "MARKDOWN")
VOL_VOCAB: tuple[str, ...] = ("LOW", "MEDIUM", "HIGH", "EXTREME")
SESSION_VOCAB: tuple[str, ...] = ("PRE", "REGULAR", "POST", "OVERNIGHT")
TIMEFRAME_VOCAB: tuple[str, ...] = ("1M", "5M", "15M", "30M", "1H", "4H", "1D", "1W")
DIRECTION_VOCAB: tuple[str, ...] = ("BUY", "SELL", "WAIT")
RESULT_VOCAB: tuple[str, ...] = ("WIN", "LOSS", "EXPIRED", "CANCELLED", "OPEN")

_SECTOR_BUCKETS = 16
_MODEL_BUCKETS = 8
_CONF_BUCKETS = 10


# --------------------------------------------------------------------------- primitives
def clamp_scale(value: float | None, lo: float, hi: float, *, missing: float = 0.0) -> float:
    """Min-max scale ``value`` from ``[lo, hi]`` to ``[0, 1]`` (clamped). ``None`` → ``missing``."""
    if value is None:
        return missing
    v = float(value)
    if v <= lo:
        return 0.0
    if v >= hi:
        return 1.0
    return (v - lo) / (hi - lo)


def onehot_enum(value: Any, vocab: tuple[str, ...]) -> list[float]:
    """One-hot encode ``value`` over ``vocab`` (case-insensitive). Unknown/None → all-zeros."""
    out = [0.0] * len(vocab)
    if value is None:
        return out
    token = str(value).strip().upper()
    for i, term in enumerate(vocab):
        if token == term:
            out[i] = 1.0
            break
    return out


def onehot_hash(value: Any, buckets: int) -> list[float]:
    """One-hot encode an open-vocabulary string into ``buckets`` via **stable** hashing.

    Uses SHA-1 (not Python's salted ``hash()``) so the bucket is identical across processes
    and runs. Empty/None → all-zeros.
    """
    out = [0.0] * buckets
    if value is None or str(value).strip() == "":
        return out
    digest = hashlib.sha1(str(value).encode("utf-8")).hexdigest()
    out[int(digest, 16) % buckets] = 1.0
    return out


def confidence_bucket_index(confidence: float | None) -> int | None:
    """Bucket a [0,1] confidence into one of ``_CONF_BUCKETS`` (0.0–0.1 … 0.9–1.0), or None."""
    if confidence is None:
        return None
    c = max(0.0, min(1.0, float(confidence)))
    return min(_CONF_BUCKETS - 1, int(c * _CONF_BUCKETS))


def _present(value: Any) -> float:
    return 1.0 if value is not None else 0.0


def _num(record: Mapping[str, Any], key: str) -> float | None:
    v = record.get(key)
    return None if v is None else float(v)


# --------------------------------------------------------------------------- field encoders
def _enc_sector(r: Mapping[str, Any]) -> list[float]:
    return onehot_hash(r.get("sector"), _SECTOR_BUCKETS)


def _enc_regime(r: Mapping[str, Any]) -> list[float]:
    return onehot_enum(r.get("market_regime"), REGIME_VOCAB)


def _enc_phase(r: Mapping[str, Any]) -> list[float]:
    return onehot_enum(r.get("market_phase"), PHASE_VOCAB)


def _enc_vol(r: Mapping[str, Any]) -> list[float]:
    return onehot_enum(r.get("volatility_bucket"), VOL_VOCAB)


def _enc_session(r: Mapping[str, Any]) -> list[float]:
    return onehot_enum(r.get("session"), SESSION_VOCAB)


def _enc_timeframe(r: Mapping[str, Any]) -> list[float]:
    return onehot_enum(r.get("timeframe"), TIMEFRAME_VOCAB)


def _enc_direction(r: Mapping[str, Any]) -> list[float]:
    return onehot_enum(r.get("direction"), DIRECTION_VOCAB)


def _enc_confidence(r: Mapping[str, Any]) -> list[float]:
    c = r.get("confidence")
    return [clamp_scale(c, 0.0, 1.0), _present(c)]


def _enc_decision_score(r: Mapping[str, Any]) -> list[float]:
    # decision_score clamped to a documented [-1, 1] band, scaled to [0, 1].
    return [clamp_scale(r.get("decision_score"), -1.0, 1.0)]


def _geometry(r: Mapping[str, Any]) -> tuple[float | None, float | None, float | None]:
    """(stop_distance, target_distance, risk_reward) as fractions of entry, or None each."""
    entry, stop, target = _num(r, "entry"), _num(r, "stop"), _num(r, "target1")
    if not entry:
        return (None, None, None)
    stop_d = abs(entry - stop) / abs(entry) if stop is not None else None
    target_d = abs(target - entry) / abs(entry) if target is not None else None
    rr = (target_d / stop_d) if (stop_d and target_d is not None) else None
    return (stop_d, target_d, rr)


def _enc_stop_distance(r: Mapping[str, Any]) -> list[float]:
    return [clamp_scale(_geometry(r)[0], 0.0, 1.0)]


def _enc_target_distance(r: Mapping[str, Any]) -> list[float]:
    return [clamp_scale(_geometry(r)[1], 0.0, 1.0)]


def _enc_risk_reward(r: Mapping[str, Any]) -> list[float]:
    return [clamp_scale(_geometry(r)[2], 0.0, 10.0)]


def _enc_geometry_present(r: Mapping[str, Any]) -> list[float]:
    entry, stop, target = _num(r, "entry"), _num(r, "stop"), _num(r, "target1")
    return [1.0 if (entry and stop is not None and target is not None) else 0.0]


def _enc_realised_r(r: Mapping[str, Any]) -> list[float]:
    v = r.get("realised_r")
    # realised R clamped to [-3, +5] then scaled to [0, 1]; present flag preserves "missing".
    return [clamp_scale(v, -3.0, 5.0), _present(v)]


def _enc_holding(r: Mapping[str, Any]) -> list[float]:
    v = r.get("holding_bars")
    return [clamp_scale(v, 0.0, 200.0), _present(v)]


def _enc_result(r: Mapping[str, Any]) -> list[float]:
    return onehot_enum(r.get("trade_result"), RESULT_VOCAB)


def _versions(r: Mapping[str, Any]) -> Mapping[str, Any]:
    v = r.get("versions")
    return v if isinstance(v, Mapping) else {}


def _enc_pred_model(r: Mapping[str, Any]) -> list[float]:
    return onehot_hash(_versions(r).get("prediction_model_version"), _MODEL_BUCKETS)


def _enc_outcome_model(r: Mapping[str, Any]) -> list[float]:
    return onehot_hash(_versions(r).get("outcome_model_version"), _MODEL_BUCKETS)


def _enc_feature_version(r: Mapping[str, Any]) -> list[float]:
    return onehot_hash(_versions(r).get("feature_version"), _MODEL_BUCKETS)


def _enc_conf_bucket(r: Mapping[str, Any]) -> list[float]:
    out = [0.0] * _CONF_BUCKETS
    idx = confidence_bucket_index(r.get("confidence"))
    if idx is not None:
        out[idx] = 1.0
    return out


def _reasoning(r: Mapping[str, Any]) -> Mapping[str, Any] | None:
    v = r.get("reasoning")
    return v if isinstance(v, Mapping) else None


def _enc_factor_count(r: Mapping[str, Any]) -> list[float]:
    reasoning = _reasoning(r)
    factors = reasoning.get("factors") if reasoning else None
    n = len([k for k in factors if k != "_builder"]) if isinstance(factors, Mapping) else 0
    return [clamp_scale(n, 0.0, 20.0)]


def _enc_rule_counts(r: Mapping[str, Any]) -> list[float]:
    reasoning = _reasoning(r)
    rule_check = reasoning.get("rule_check") if reasoning else None
    if not isinstance(rule_check, Mapping):
        return [0.0, 0.0]
    n = len(rule_check)
    passed = sum(1 for v in rule_check.values() if v is True)
    return [clamp_scale(n, 0.0, 20.0), clamp_scale(passed, 0.0, 20.0)]


def _enc_reasoning_present(r: Mapping[str, Any]) -> list[float]:
    return [1.0 if _reasoning(r) is not None else 0.0]


def _enc_embedding_present(r: Mapping[str, Any]) -> list[float]:
    emb = r.get("embedding")
    return [1.0 if (isinstance(emb, Mapping) and emb.get("present")) else 0.0]


# --------------------------------------------------------------------------- schema (fixed order)
@dataclass(frozen=True)
class _FeatureSpec:
    name: str
    width: int
    encode: Callable[[Mapping[str, Any]], list[float]]


#: The immutable, ordered feature layout for ``FEATURE_VERSION``. **Never reorder or edit in
#: place** — a change is a new feature version.
_SPECS: tuple[_FeatureSpec, ...] = (
    # --- market (42) ---
    _FeatureSpec("sector", _SECTOR_BUCKETS, _enc_sector),
    _FeatureSpec("market_regime", len(REGIME_VOCAB), _enc_regime),
    _FeatureSpec("market_phase", len(PHASE_VOCAB), _enc_phase),
    _FeatureSpec("volatility_bucket", len(VOL_VOCAB), _enc_vol),
    _FeatureSpec("session", len(SESSION_VOCAB), _enc_session),
    _FeatureSpec("timeframe", len(TIMEFRAME_VOCAB), _enc_timeframe),
    # --- trade (10) ---
    _FeatureSpec("direction", len(DIRECTION_VOCAB), _enc_direction),
    _FeatureSpec("confidence", 2, _enc_confidence),
    _FeatureSpec("decision_score", 1, _enc_decision_score),
    _FeatureSpec("stop_distance", 1, _enc_stop_distance),
    _FeatureSpec("target_distance", 1, _enc_target_distance),
    _FeatureSpec("risk_reward", 1, _enc_risk_reward),
    _FeatureSpec("geometry_present", 1, _enc_geometry_present),
    # --- outcome (9) ---
    _FeatureSpec("realised_r", 2, _enc_realised_r),
    _FeatureSpec("holding_bars", 2, _enc_holding),
    _FeatureSpec("trade_result", len(RESULT_VOCAB), _enc_result),
    # --- model (24) ---
    _FeatureSpec("prediction_model_version", _MODEL_BUCKETS, _enc_pred_model),
    _FeatureSpec("outcome_model_version", _MODEL_BUCKETS, _enc_outcome_model),
    _FeatureSpec("feature_version", _MODEL_BUCKETS, _enc_feature_version),
    # --- context (15) ---
    _FeatureSpec("confidence_bucket", _CONF_BUCKETS, _enc_conf_bucket),
    _FeatureSpec("factor_count", 1, _enc_factor_count),
    _FeatureSpec("rule_counts", 2, _enc_rule_counts),
    _FeatureSpec("reasoning_present", 1, _enc_reasoning_present),
    _FeatureSpec("embedding_present", 1, _enc_embedding_present),
)

#: Total vector length for ``FEATURE_VERSION`` — derived from the fixed specs (stable).
VECTOR_DIM: int = sum(spec.width for spec in _SPECS)


def feature_layout() -> list[tuple[str, int]]:
    """The (feature name, width) layout in order — for documentation and debugging."""
    return [(spec.name, spec.width) for spec in _SPECS]


# --------------------------------------------------------------------------- builder
class FeatureVectorBuilder:
    """Converts one Memory Record into a deterministic :class:`FeatureVector`.

    Stateless and pure. Accepts either a Memory Record **mapping** (the ``to_dict()``
    contract) or any object exposing ``to_dict()`` (e.g. a ``MemoryRecord``).
    """

    feature_version: str = FEATURE_VERSION
    schema_version: int = SCHEMA_VERSION
    dimension: int = VECTOR_DIM

    def build(self, record: Any, *, feature_version: str = FEATURE_VERSION) -> FeatureVector:
        """Build the feature vector for one Memory Record.

        Args:
            record: a Memory Record mapping (``to_dict()`` output) or an object with
                ``to_dict()``.
            feature_version: must match :data:`FEATURE_VERSION`; any other value is rejected
                so a caller cannot silently request an unimplemented encoding.

        Returns:
            The immutable :class:`FeatureVector` (dimension :data:`VECTOR_DIM`).

        Raises:
            UnsupportedVersionError: unknown ``feature_version`` or record schema version.
            InvalidMemoryRecordError / MissingFieldError: malformed or incomplete record.
        """
        if feature_version != FEATURE_VERSION:
            raise UnsupportedVersionError(
                f"unsupported feature_version {feature_version!r} (only {FEATURE_VERSION!r})"
            )

        rec = record.to_dict() if hasattr(record, "to_dict") else record
        if not isinstance(rec, Mapping):
            raise InvalidMemoryRecordError("Memory Record must be a mapping or expose to_dict()")
        self._validate(rec)

        values: list[float] = []
        for spec in _SPECS:
            encoded = spec.encode(rec)
            if len(encoded) != spec.width:  # pragma: no cover - guards an immutable spec bug
                raise SimilarityError(f"encoder {spec.name!r} produced {len(encoded)} != {spec.width}")
            values.extend(float(x) for x in encoded)

        vector = FeatureVector(
            values=tuple(values),
            feature_version=FEATURE_VERSION,
            schema_version=SCHEMA_VERSION,
            dimension=VECTOR_DIM,
        )
        logger.info(
            "similarity feature vector built: schema=%d feature=%s dim=%d",
            SCHEMA_VERSION, FEATURE_VERSION, vector.dimension,
        )
        return vector

    @staticmethod
    def _validate(rec: Mapping[str, Any]) -> None:
        """Reject records missing identity/lifecycle or carrying an unsupported schema."""
        for field in ("prediction_id", "status"):
            if not rec.get(field):
                raise MissingFieldError(f"Memory Record missing required field {field!r}")
        metadata = rec.get("metadata")
        if isinstance(metadata, Mapping):
            sv = metadata.get("record_schema_version")
            if sv is not None and int(sv) > SUPPORTED_RECORD_SCHEMA:
                raise UnsupportedVersionError(
                    f"record schema version {sv} > supported {SUPPORTED_RECORD_SCHEMA}"
                )
