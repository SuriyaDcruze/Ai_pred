"""Tests for the Pattern Extraction Engine (Sprint 4 · Vol 15 · Milestone 2).

Cover deterministic extraction + checksum, grouping correctness, duplicate elimination,
filtered/incremental extraction, ordering, empty/thin dataset, dataset validation (malformed /
version / corrupted / evidence), the append-only pattern migration, concurrency, evidence
traceability, and that only HYPOTHESIS / INSUFFICIENT_DATA states are used (never VALIDATED).
Temporary databases only.
"""

from __future__ import annotations

import threading

import pytest

from app.database.connection import get_connection
from app.database.migrations import applied_versions, run_migrations
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.retrieval import RetrievalEngine
from app.memory.store import MemoryStore
from app.learning.dataset import LearningDatasetBuilder
from app.learning.models import (
    DATASET_VERSION,
    LEARNING_VERSION,
    CandidatePattern,
    InconsistentEvidenceError,
    InvalidDatasetError,
    LearningDataset,
    LearningStatus,
    UnknownDimensionError,
    UnsupportedVersionError,
)
from app.learning.patterns import (
    PatternExtractor,
    available_dimensions,
    confidence_bucket,
    holding_bucket,
)

_TS = 1_700_000_000


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "prediction_history.db")


@pytest.fixture()
def stores(db_path):
    ps = PredictionStore(path=db_path)
    ms = MemoryStore(path=db_path)
    try:
        yield ps, ms
    finally:
        ps.close()
        ms.close()


@pytest.fixture()
def retrieval(stores):
    ps, ms = stores
    return RetrievalEngine(ps, ms)


def _seed(ps, *, i, symbol="REL.NS", sector="Energy", regime="BULL", tf="1d", prob=0.62,
          resolve_r=2.0, holding=5):
    rec = PredictionRecord(
        symbol=symbol, exchange="NSE", timeframe=tf, current_price=100.0, direction="BUY",
        recommendation="BUY", created_candle_ts=_TS + i, entry=100.0, stop=95.0, target1=110.0,
        outcome_prob=prob, sector=sector, market_regime=regime, prediction_model_version="pred-1",
        feature_version="feat-1", status=PredictionStatus.ACTIVE,
    )
    rec.created_at = f"2026-01-01T00:00:{i:02d}+00:00"
    ps.create(rec)
    ps.update_resolution(
        rec.prediction_id,
        status=PredictionStatus.TARGET_HIT if resolve_r > 0 else PredictionStatus.STOP_HIT,
        resolved_price=110.0 if resolve_r > 0 else 95.0, resolution_reason="t",
        realised_r=resolve_r, holding_bars=holding,
    )
    return rec.prediction_id


def _dataset(retrieval, **kw):
    return LearningDatasetBuilder(retrieval, min_corpus=1).build(**kw)


# --------------------------------------------------------------- helpers
def test_confidence_and_holding_buckets():
    assert confidence_bucket(0.62) == "0.60-0.70"
    assert confidence_bucket(1.0) == "0.90-1.00" and confidence_bucket(None) is None
    assert holding_bucket(3) == "0-5" and holding_bucket(15) == "11-20" and holding_bucket(99) == "50+"
    assert holding_bucket(None) is None


# --------------------------------------------------------------- determinism / grouping
def test_deterministic_extraction(retrieval, stores):
    ps, _ = stores
    for i in range(6):
        _seed(ps, i=i, symbol=f"S{i}.NS", sector="Energy")
    ds = _dataset(retrieval)
    ex = PatternExtractor(min_evidence=3, dimensions=["sector"])
    a, b = ex.extract(ds), ex.extract(ds)
    assert a.checksum == b.checksum
    assert [p.pattern_id for p in a.patterns] == [p.pattern_id for p in b.patterns]


def test_grouping_correctness(retrieval, stores):
    ps, _ = stores
    for i in range(4):
        _seed(ps, i=i, sector="Energy", symbol="REL.NS")
    for i in range(4, 7):
        _seed(ps, i=i, sector="IT", symbol="TCS.NS")
    ds = _dataset(retrieval)
    result = PatternExtractor(min_evidence=3, dimensions=["sector"]).extract(ds)
    by_value = {p.grouping_value: p for p in result.patterns}
    assert by_value["Energy"].evidence_count == 4 and by_value["IT"].evidence_count == 3
    assert all(p.status is LearningStatus.HYPOTHESIS for p in result.patterns)
    assert result.status is LearningStatus.HYPOTHESIS


def test_min_evidence_drops_small_groups(retrieval, stores):
    ps, _ = stores
    for i in range(4):
        _seed(ps, i=i, sector="Energy")
    _seed(ps, i=9, sector="IT", symbol="TCS.NS")   # only 1 → below min_evidence
    result = PatternExtractor(min_evidence=3, dimensions=["sector"]).extract(_dataset(retrieval))
    values = {p.grouping_value for p in result.patterns}
    assert values == {"Energy"} and result.insufficient_groups == 1     # IT dropped, counted


def test_deterministic_ordering(retrieval, stores):
    ps, _ = stores
    for i in range(3):
        _seed(ps, i=i, sector="Zeta")
    for i in range(3, 6):
        _seed(ps, i=i, sector="Alpha", symbol="A.NS")
    result = PatternExtractor(min_evidence=3, dimensions=["sector"]).extract(_dataset(retrieval))
    keys = [(p.grouping_key, p.grouping_value) for p in result.patterns]
    assert keys == sorted(keys)


def test_pattern_id_is_deterministic_not_random(retrieval, stores):
    ps, _ = stores
    for i in range(3):
        _seed(ps, i=i, sector="Energy")
    p1 = PatternExtractor(min_evidence=3, dimensions=["sector"]).extract(_dataset(retrieval)).patterns[0]
    p2 = PatternExtractor(min_evidence=3, dimensions=["sector"]).extract(_dataset(retrieval)).patterns[0]
    assert p1.pattern_id == p2.pattern_id


# --------------------------------------------------------------- dimensions / filtering
def test_multiple_dimensions(retrieval, stores):
    ps, _ = stores
    for i in range(5):
        _seed(ps, i=i, sector="Energy", regime="BULL")
    result = PatternExtractor(min_evidence=3, dimensions=["sector", "market_regime"]).extract(_dataset(retrieval))
    keys = {p.grouping_key for p in result.patterns}
    assert keys == {"sector", "market_regime"}


def test_duplicate_dimensions_deduped(retrieval, stores):
    ps, _ = stores
    for i in range(3):
        _seed(ps, i=i, sector="Energy")
    result = PatternExtractor(min_evidence=3, dimensions=["sector", "sector"]).extract(_dataset(retrieval))
    assert len([p for p in result.patterns if p.grouping_key == "sector"]) == 1   # no dup


def test_incremental_extract_with_dimension_subset(retrieval, stores):
    ps, _ = stores
    for i in range(4):
        _seed(ps, i=i, sector="Energy", tf="1d")
    ex = PatternExtractor(min_evidence=3)                         # all dims by default
    only_tf = ex.extract(_dataset(retrieval), dimensions=["timeframe"])
    assert {p.grouping_key for p in only_tf.patterns} == {"timeframe"}


def test_unknown_dimension_raises(retrieval, stores):
    ps, _ = stores
    _seed(ps, i=0)
    with pytest.raises(UnknownDimensionError):
        PatternExtractor(dimensions=["not_a_dimension"])


# --------------------------------------------------------------- empty / thin
def test_empty_dataset_is_insufficient(retrieval):
    result = PatternExtractor().extract(_dataset(retrieval))
    assert result.status is LearningStatus.INSUFFICIENT_DATA and result.patterns == ()


def test_thin_dataset_yields_insufficient(retrieval, stores):
    ps, _ = stores
    for i in range(3):
        _seed(ps, i=i, symbol=f"S{i}.NS")
    ds = LearningDatasetBuilder(retrieval, min_corpus=10).build()   # dataset itself INSUFFICIENT
    result = PatternExtractor(min_evidence=1).extract(ds)
    assert result.status is LearningStatus.INSUFFICIENT_DATA and result.patterns == ()


def test_no_hypothesis_groups_yields_insufficient(retrieval, stores):
    ps, _ = stores
    # 5 distinct symbols, each once → no group reaches min_evidence=3.
    for i in range(5):
        _seed(ps, i=i, symbol=f"S{i}.NS", sector=f"Sec{i}")
    result = PatternExtractor(min_evidence=3, dimensions=["symbol"]).extract(_dataset(retrieval))
    assert result.status is LearningStatus.INSUFFICIENT_DATA and result.patterns == ()


# --------------------------------------------------------------- evidence / traceability
def test_evidence_traceability(retrieval, stores):
    ps, _ = stores
    ids = [_seed(ps, i=i, sector="Energy") for i in range(3)]
    pattern = PatternExtractor(min_evidence=3, dimensions=["sector"]).extract(_dataset(retrieval)).patterns[0]
    assert set(pattern.prediction_ids) == set(ids)                # traces back to every trade
    assert pattern.evidence_count == 3


def test_never_validated_state(retrieval, stores):
    ps, _ = stores
    for i in range(4):
        _seed(ps, i=i, sector="Energy")
    result = PatternExtractor(min_evidence=3).extract(_dataset(retrieval))
    assert all(p.status is not LearningStatus.VALIDATED for p in result.patterns)


def test_inconsistent_evidence_rejected():
    with pytest.raises(InconsistentEvidenceError):
        CandidatePattern(
            pattern_id="x", learning_version=LEARNING_VERSION, dataset_version=DATASET_VERSION,
            pattern_type="SETUP", grouping_key="sector", grouping_value="Energy",
            evidence_count=5, prediction_ids=("a", "b"), corpus_size=5, status=LearningStatus.HYPOTHESIS,
        )


# --------------------------------------------------------------- dataset validation
def test_malformed_dataset_rejected():
    with pytest.raises(InvalidDatasetError):
        PatternExtractor().extract({"not": "a dataset"})


def test_unsupported_dataset_version_rejected():
    bad = LearningDataset(
        records=(), corpus_size=0, dataset_version="lds-999", learning_version=LEARNING_VERSION,
        generated_at="x", source_versions={}, build_duration_ms=0.0, checksum="c",
        status=None, min_corpus=1,
    )
    with pytest.raises(UnsupportedVersionError):
        PatternExtractor().extract(bad)


# --------------------------------------------------------------- migration (append-only)
def test_learning_patterns_migration(tmp_path):
    conn = get_connection(str(tmp_path / "ph.db"))
    run_migrations(conn)
    assert 7 in applied_versions(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(learning_patterns)")}
    assert {"pattern_id", "grouping_key", "grouping_value", "evidence_count",
            "prediction_ids_json", "status", "pattern_type"} <= cols


def test_pattern_row_round_trip(tmp_path):
    conn = get_connection(str(tmp_path / "ph.db"))
    run_migrations(conn)
    pattern = CandidatePattern.create(
        learning_version=LEARNING_VERSION, dataset_version=DATASET_VERSION, pattern_type="SETUP",
        grouping_key="sector", grouping_value="Energy", prediction_ids=["b", "a", "c"],
        corpus_size=10, status=LearningStatus.HYPOTHESIS,
    )
    row = pattern.to_row()
    with conn:
        conn.execute(
            f"INSERT INTO learning_patterns ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})", row
        )
    got = CandidatePattern.from_row(
        conn.execute("SELECT * FROM learning_patterns WHERE pattern_id=?", (pattern.pattern_id,)).fetchone()
    )
    assert got.grouping_value == "Energy" and got.evidence_count == 3
    assert got.prediction_ids == ("a", "b", "c")     # sorted evidence preserved


def test_pattern_migration_leaves_predictions_unchanged(tmp_path):
    path = str(tmp_path / "ph.db")
    ps = PredictionStore(path=path)
    _seed(ps, i=0)
    schema = ps._conn.execute("SELECT sql FROM sqlite_master WHERE name='predictions'").fetchone()["sql"]
    run_migrations(ps._conn)
    assert ps._conn.execute("SELECT sql FROM sqlite_master WHERE name='predictions'").fetchone()["sql"] == schema
    ps.close()


# --------------------------------------------------------------- concurrency / isolation
def test_concurrent_extraction(retrieval, stores):
    ps, _ = stores
    for i in range(6):
        _seed(ps, i=i, sector="Energy")
    ds = _dataset(retrieval)
    ex = PatternExtractor(min_evidence=3, dimensions=["sector"])
    checksums: list[str] = []
    errors: list[Exception] = []

    def work() -> None:
        try:
            checksums.append(ex.extract(ds).checksum)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(set(checksums)) == 1


def test_extraction_performs_no_writes(retrieval, stores):
    ps, ms = stores
    for i in range(4):
        _seed(ps, i=i, sector="Energy")
    patterns_before = ms._conn.execute("SELECT COUNT(*) AS n FROM learning_patterns").fetchone()["n"]
    PatternExtractor(min_evidence=3).extract(_dataset(retrieval))
    assert ms._conn.execute("SELECT COUNT(*) AS n FROM learning_patterns").fetchone()["n"] == patterns_before  # 0


def test_patterns_module_does_not_import_engines():
    import ast

    import app.learning.patterns as pat
    with open(pat.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)


def test_available_dimensions_stable():
    dims = available_dimensions()
    assert "sector" in dims and "confidence_bucket" in dims and "outcome_category" in dims
