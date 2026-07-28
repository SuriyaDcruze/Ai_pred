"""Tests for the Feature Vector Builder (Sprint 3 · Vol 14 · Milestone 1).

Cover determinism, enum + hash encoding, numeric normalization, missing/optional fields,
invalid records, unsupported versions, and dimension stability. Also an end-to-end check that
a real Memory Record (from the Retrieval Engine) encodes without modifying Historical Memory.
"""

from __future__ import annotations

import pytest

from app.similarity.feature_vector import (
    DIRECTION_VOCAB,
    FEATURE_VERSION,
    REGIME_VOCAB,
    SCHEMA_VERSION,
    VECTOR_DIM,
    FeatureVectorBuilder,
    clamp_scale,
    confidence_bucket_index,
    feature_layout,
    onehot_enum,
    onehot_hash,
)
from app.similarity.models import (
    FeatureVector,
    InvalidMemoryRecordError,
    MissingFieldError,
    UnsupportedVersionError,
)


def _record(**overrides):
    """A minimal, valid Memory Record dict (the RetrievalEngine.to_dict() contract)."""
    rec = {
        "prediction_id": "abc123",
        "symbol": "RELIANCE.NS",
        "timeframe": "1d",
        "direction": "BUY",
        "recommendation": "BUY",
        "confidence": 0.62,
        "decision_score": 0.4,
        "entry": 100.0, "stop": 95.0, "target1": 110.0, "target2": None,
        "status": "TARGET_HIT",
        "trade_result": "WIN",
        "realised_r": 2.0,
        "holding_bars": 5,
        "market_regime": "BULL",
        "market_phase": "MARKUP",
        "sector": "Energy",
        "session": "REGULAR",
        "volatility_bucket": "MEDIUM",
        "context": {},
        "versions": {
            "prediction_model_version": "pred-1",
            "outcome_model_version": "out-1",
            "feature_version": "feat-1",
        },
        "reasoning": {"rationale": "x", "factors": {"trend": "up", "_builder": {"version": "1"}},
                      "rule_check": {"rr_ok": True, "trend_ok": False}, "confidence": 0.62},
        "embedding": {"kind": "context_v1", "present": False, "dim": None, "model_name": None},
        "metadata": {"built": True, "record_schema_version": 1},
    }
    rec.update(overrides)
    return rec


@pytest.fixture()
def builder():
    return FeatureVectorBuilder()


# --------------------------------------------------------------- primitives
def test_clamp_scale():
    assert clamp_scale(None, 0, 1) == 0.0
    assert clamp_scale(-5, 0, 1) == 0.0        # below → 0
    assert clamp_scale(5, 0, 1) == 1.0         # above → 1
    assert clamp_scale(0.5, 0, 1) == pytest.approx(0.5)
    assert clamp_scale(0.0, -3, 5) == pytest.approx(3 / 8)   # realised-R style band


def test_onehot_enum_and_unknown():
    assert onehot_enum("buy", DIRECTION_VOCAB) == [1.0, 0.0, 0.0]
    assert onehot_enum("SELL", DIRECTION_VOCAB) == [0.0, 1.0, 0.0]
    assert onehot_enum("hold", DIRECTION_VOCAB) == [0.0, 0.0, 0.0]   # unknown → all-zeros
    assert onehot_enum(None, REGIME_VOCAB) == [0.0] * len(REGIME_VOCAB)


def test_onehot_hash_is_stable_and_bucketed():
    a = onehot_hash("Energy", 16)
    b = onehot_hash("Energy", 16)
    assert a == b and sum(a) == 1.0 and len(a) == 16   # deterministic, exactly one hot
    assert onehot_hash("", 16) == [0.0] * 16            # empty → all-zeros


def test_confidence_bucket_index():
    assert confidence_bucket_index(0.62) == 6
    assert confidence_bucket_index(0.0) == 0
    assert confidence_bucket_index(1.0) == 9    # top edge stays in last bucket
    assert confidence_bucket_index(None) is None


# --------------------------------------------------------------- determinism
def test_identical_input_identical_vector(builder):
    v1 = builder.build(_record())
    v2 = builder.build(_record())
    assert v1 == v2 and v1.values == v2.values


def test_repeated_build_same_object_is_stable(builder):
    rec = _record()
    assert builder.build(rec).values == builder.build(rec).values


def test_different_records_differ(builder):
    base = builder.build(_record())
    assert builder.build(_record(direction="SELL")).values != base.values
    assert builder.build(_record(market_regime="BEAR")).values != base.values
    assert builder.build(_record(sector="IT")).values != base.values
    assert builder.build(_record(confidence=0.9)).values != base.values


# --------------------------------------------------------------- dimension + versioning
def test_dimension_is_stable_and_100(builder):
    assert VECTOR_DIM == 100
    assert sum(w for _, w in feature_layout()) == VECTOR_DIM
    for rec in (_record(), _record(confidence=None, realised_r=None), _record(sector=None)):
        v = builder.build(rec)
        assert v.dimension == VECTOR_DIM and len(v.values) == VECTOR_DIM


def test_vector_carries_versions(builder):
    v = builder.build(_record())
    assert v.feature_version == FEATURE_VERSION and v.schema_version == SCHEMA_VERSION
    assert v.dimension == VECTOR_DIM


def test_feature_vector_rejects_wrong_length():
    with pytest.raises(Exception):
        FeatureVector(values=(0.0, 1.0), feature_version=FEATURE_VERSION, schema_version=1, dimension=100)


# --------------------------------------------------------------- normalization / encoding correctness
def test_confidence_slot_reflects_value(builder):
    # confidence occupies a known-width group; the scaled value must track the input.
    low = builder.build(_record(confidence=0.2)).values
    high = builder.build(_record(confidence=0.9)).values
    # they differ, and neither equals the missing case
    missing = builder.build(_record(confidence=None)).values
    assert low != high and low != missing and high != missing


def test_missing_optional_fields_encode_without_error(builder):
    rec = _record(confidence=None, decision_score=None, realised_r=None, holding_bars=None,
                  sector=None, market_regime=None, entry=None, stop=None, target1=None,
                  reasoning=None, embedding=None)
    v = builder.build(rec)
    assert v.dimension == VECTOR_DIM        # still a full-width, valid vector


def test_open_prediction_encodes(builder):
    v = builder.build(_record(status="ACTIVE", trade_result="OPEN", realised_r=None, holding_bars=None))
    assert v.dimension == VECTOR_DIM


# --------------------------------------------------------------- validation / errors
def test_non_mapping_rejected(builder):
    with pytest.raises(InvalidMemoryRecordError):
        builder.build(["not", "a", "record"])


def test_missing_required_field_rejected(builder):
    with pytest.raises(MissingFieldError):
        builder.build(_record(prediction_id=None))
    with pytest.raises(MissingFieldError):
        builder.build(_record(status=None))


def test_unsupported_feature_version_rejected(builder):
    with pytest.raises(UnsupportedVersionError):
        builder.build(_record(), feature_version="sim-fv-999")


def test_unsupported_record_schema_rejected(builder):
    bad = _record()
    bad["metadata"] = {"built": True, "record_schema_version": 99}
    with pytest.raises(UnsupportedVersionError):
        builder.build(bad)


# --------------------------------------------------------------- integration + isolation
def test_builds_from_real_memory_record(tmp_path):
    """End-to-end: a real Memory Record from the Retrieval Engine encodes, and Historical
    Memory is not modified by feature building."""
    from app.forward_testing.models import PredictionRecord, PredictionStatus
    from app.forward_testing.store import PredictionStore
    from app.memory.builder import MemoryBuilder
    from app.memory.retrieval import RetrievalEngine
    from app.memory.store import MemoryStore

    path = str(tmp_path / "prediction_history.db")
    ps = PredictionStore(path=path)
    ms = MemoryStore(path=path)
    rec = PredictionRecord(
        symbol="REL.NS", exchange="NSE", timeframe="1d", current_price=100.0, direction="BUY",
        recommendation="BUY", created_candle_ts=1_700_000_000, entry=100.0, stop=95.0,
        target1=110.0, outcome_prob=0.62, sector="Energy", market_regime="BULL",
        prediction_model_version="pred-1", status=PredictionStatus.ACTIVE,
    )
    ps.create(rec)
    ps.update_resolution(rec.prediction_id, status=PredictionStatus.TARGET_HIT,
                         resolved_price=110.0, resolution_reason="t", realised_r=2.0, holding_bars=5)
    MemoryBuilder(ps, ms).build(rec.prediction_id)
    memory_record = RetrievalEngine(ps, ms).get_record(rec.prediction_id)

    reasoning_before = ms._conn.execute("SELECT COUNT(*) AS n FROM memory_reasoning").fetchone()["n"]
    vector = FeatureVectorBuilder().build(memory_record)   # accepts the MemoryRecord (to_dict())
    assert vector.dimension == VECTOR_DIM
    # feature building touched nothing.
    assert ms._conn.execute("SELECT COUNT(*) AS n FROM memory_reasoning").fetchone()["n"] == reasoning_before
    ps.close(); ms.close()


def test_similarity_modules_do_not_import_engines():
    import ast

    import app.similarity.feature_vector as fv
    import app.similarity.models as md
    for module in (fv, md):
        with open(module.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
