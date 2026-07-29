"""Tests for the Statistical Validation Engine (Sprint 4 · Vol 15 · Milestone 3).

Cover the statistical primitives (Wilson interval, proportion z-test, consistency, corrections),
deterministic + reproducible validation, confidence-interval presence, significance, the
multiple-comparison correction strategies, the sample-size floor, hypothesis rejection vs
VALIDATED promotion, insufficient / empty data, corrupted statistics, malformed patterns, version
mismatch, concurrency, **regression against the Sprint 2 aggregate values** (proving reuse), the
append-only `learning_pattern_stats` migration + row round-trip, no-writes, and no-engine-imports.
Temporary databases only.
"""

from __future__ import annotations

import math
import threading

import pytest

from app.database.connection import get_connection
from app.database.migrations import applied_versions, run_migrations
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.aggregates import compute_aggregates
from app.memory.models import AggregateDimension
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
    LearningRecord,
    LearningStatus,
    MalformedPatternError,
    StatisticsError,
    UnsupportedVersionError,
    ValidatedPattern,
)
from app.learning.patterns import PatternExtractor
from app.learning.statistics import (
    CORRECTION_STRATEGIES,
    StatisticalValidator,
    UnknownCorrectionError,
    available_corrections,
    ci_quality,
    consistency_score,
    proportion_ztest,
    wilson_interval,
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


def _seed(ps, *, i, symbol=None, sector="Energy", regime="BULL", tf="1d", prob=0.62,
          resolve_r=2.0, holding=5):
    rec = PredictionRecord(
        symbol=symbol or f"S{i}.NS", exchange="NSE", timeframe=tf, current_price=100.0,
        direction="BUY", recommendation="BUY", created_candle_ts=_TS + i, entry=100.0, stop=95.0,
        target1=110.0, outcome_prob=prob, sector=sector, market_regime=regime,
        prediction_model_version="pred-1", feature_version="feat-1", status=PredictionStatus.ACTIVE,
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


def _patterns(ds, *, dims=("sector",), min_evidence=3):
    return PatternExtractor(min_evidence=min_evidence, dimensions=list(dims)).extract(ds).patterns


# ---- manual dataset builders (for edge cases the builder cannot produce) -------------------
def _record(pid, r, *, ts="2026-01-01T00:00:00+00:00", sector="Energy", holding=5):
    return LearningRecord(
        prediction_id=pid, outcome="WIN" if r > 0 else "LOSS", realised_r=r, win=r > 0,
        confidence=0.6, holding_period=holding, prediction_timestamp=ts, outcome_timestamp=ts,
        symbol="X.NS", sector=sector, timeframe="1d", market_regime="BULL", market_phase=None,
        prediction_model_version="pred-1", feature_version="feat-1", similarity_metadata=None,
        memory_reference={"prediction_id": pid},
    )


def _manual_dataset(records, *, status=None):
    return LearningDataset(
        records=tuple(records), corpus_size=len(records), dataset_version=DATASET_VERSION,
        learning_version=LEARNING_VERSION, generated_at="x", source_versions={},
        build_duration_ms=0.0, checksum="c", status=status, min_corpus=1,
    )


def _manual_pattern(records, *, key="sector", value="Energy"):
    return CandidatePattern.create(
        learning_version=LEARNING_VERSION, dataset_version=DATASET_VERSION, pattern_type="SETUP",
        grouping_key=key, grouping_value=value, prediction_ids=[r.prediction_id for r in records],
        corpus_size=len(records), status=LearningStatus.HYPOTHESIS,
    )


# --------------------------------------------------------------- statistical primitives
def test_wilson_interval_bounds_and_all_wins():
    low, high = wilson_interval(10, 10)
    assert high == pytest.approx(1.0) and 0.70 < low < 1.0   # excludes a coin flip
    lo2, hi2 = wilson_interval(5, 10)
    assert lo2 < 0.5 < hi2                                    # straddles 0.5 at 50%
    assert wilson_interval(0, 0) == (0.0, 1.0)               # no data ⇒ widest


def test_ci_quality_bands():
    assert ci_quality(0.10) == "HIGH"
    assert ci_quality(0.30) == "MODERATE"
    assert ci_quality(0.60) == "LOW"


def test_proportion_ztest_matches_expectations():
    z0, p0 = proportion_ztest(5, 10, 0.5)
    assert z0 == 0.0 and p0 == 1.0                           # exactly at baseline
    z1, p1 = proportion_ztest(10, 10, 0.5)
    assert z1 > 3.0 and p1 < 0.01                            # far from baseline ⇒ significant
    # degenerate baseline (zero null variance)
    assert proportion_ztest(4, 5, 1.0)[1] == 0.0             # any deviation ⇒ fully significant


def test_consistency_score_stable_vs_unstable():
    wins_then_losses = [_record(f"a{i}", 2.0, ts=f"t{i:02d}") for i in range(5)] + \
                       [_record(f"b{i}", -1.0, ts=f"t{i + 5:02d}") for i in range(5)]
    assert consistency_score(wins_then_losses, 2) == pytest.approx(0.0)   # 100% then 0%
    all_wins = [_record(f"w{i}", 2.0, ts=f"t{i:02d}") for i in range(6)]
    assert consistency_score(all_wins, 2) == pytest.approx(1.0)           # perfectly stable
    assert consistency_score([_record("x", 2.0)], 2) is None              # too little to split


def test_correction_strategies_differ():
    pvals = [0.01, 0.02, 0.03]
    assert CORRECTION_STRATEGIES["bonferroni"](pvals, 0.05) == [True, False, False]
    assert CORRECTION_STRATEGIES["benjamini_hochberg"](pvals, 0.05) == [True, True, True]
    assert CORRECTION_STRATEGIES["none"](pvals, 0.05) == [True, True, True]
    assert "benjamini_hochberg" in available_corrections()


# --------------------------------------------------------------- determinism / reproducibility
def test_deterministic_validation(retrieval, stores):
    ps, _ = stores
    for i in range(10):
        _seed(ps, i=i, sector="Energy")
    ds = _dataset(retrieval)
    patterns = _patterns(ds)
    v = StatisticalValidator(min_sample=10)
    a, b = v.validate(ds, patterns), v.validate(ds, patterns)
    assert a.checksum == b.checksum
    assert [p.pattern_key for p in a.validated_patterns] == [p.pattern_key for p in b.validated_patterns]
    assert [p.status for p in a.validated_patterns] == [p.status for p in b.validated_patterns]


def test_identical_datasets_identical_results(retrieval, stores):
    ps, _ = stores
    for i in range(8):
        _seed(ps, i=i, sector="Energy")
    ds1, ds2 = _dataset(retrieval), _dataset(retrieval)
    v = StatisticalValidator(min_sample=5)
    assert v.validate(ds1, _patterns(ds1)).checksum == v.validate(ds2, _patterns(ds2)).checksum


# --------------------------------------------------------------- validated promotion / rejection
def test_validated_promotion(retrieval, stores):
    ps, _ = stores
    for i in range(12):
        _seed(ps, i=i, sector="Energy", resolve_r=2.0)       # all wins
    ds = _dataset(retrieval)
    result = StatisticalValidator(min_sample=10).validate(ds, _patterns(ds))
    energy = next(p for p in result.validated_patterns if p.grouping_value == "Energy")
    assert energy.status is LearningStatus.VALIDATED
    assert energy.confidence_interval.low > 0.5              # interval excludes the coin flip
    assert energy.significance.significant and energy.correction_significant
    assert result.status is LearningStatus.VALIDATED and result.validated_count == 1


def test_coin_flip_is_hypothesis_not_validated(retrieval, stores):
    ps, _ = stores
    for i in range(6):
        _seed(ps, i=i, sector="Energy", resolve_r=2.0)
    for i in range(6, 12):
        _seed(ps, i=i, sector="Energy", resolve_r=-1.0)      # 6W / 6L ⇒ ~50%
    ds = _dataset(retrieval)
    result = StatisticalValidator(min_sample=10).validate(ds, _patterns(ds))
    energy = next(p for p in result.validated_patterns if p.grouping_value == "Energy")
    assert energy.win_rate == pytest.approx(0.5)
    assert energy.status is LearningStatus.HYPOTHESIS       # never promoted on a coin flip
    assert not energy.correction_significant


def test_below_sample_floor_is_insufficient(retrieval, stores):
    ps, _ = stores
    for i in range(5):
        _seed(ps, i=i, sector="Energy", resolve_r=2.0)       # 5 < min_sample
    ds = _dataset(retrieval)
    result = StatisticalValidator(min_sample=30).validate(ds, _patterns(ds))
    energy = next(p for p in result.validated_patterns if p.grouping_value == "Energy")
    assert energy.status is LearningStatus.INSUFFICIENT_DATA
    assert result.status is LearningStatus.INSUFFICIENT_DATA


def test_weak_evidence_never_validated_across_configs(retrieval, stores):
    ps, _ = stores
    for i in range(11):                                       # 6W / 5L, marginal
        _seed(ps, i=i, sector="Energy", resolve_r=2.0 if i < 6 else -1.0)
    ds = _dataset(retrieval)
    result = StatisticalValidator(min_sample=10, correction="bonferroni").validate(ds, _patterns(ds))
    assert all(p.status is not LearningStatus.VALIDATED for p in result.validated_patterns)


# --------------------------------------------------------------- confidence / significance shape
def test_every_validated_pattern_carries_ci_and_significance(retrieval, stores):
    ps, _ = stores
    for i in range(10):
        _seed(ps, i=i, sector="Energy")
    ds = _dataset(retrieval)
    result = StatisticalValidator(min_sample=5).validate(ds, _patterns(ds))
    for p in result.validated_patterns:
        ci = p.confidence_interval
        assert 0.0 <= ci.low <= ci.high <= 1.0 and ci.method == "wilson" and ci.level == 0.95
        assert p.significance.baseline == 0.5 and p.correction_method == "benjamini_hochberg"


def test_correction_recorded_and_hypotheses_counted(retrieval, stores):
    ps, _ = stores
    for i in range(6):
        _seed(ps, i=i, sector="Energy")
    for i in range(6, 10):
        _seed(ps, i=i, sector="IT", symbol="TCS.NS")
    ds = _dataset(retrieval)
    patterns = _patterns(ds)
    result = StatisticalValidator(min_sample=3, correction="bonferroni").validate(ds, patterns)
    assert result.correction_method == "bonferroni"
    assert result.hypotheses_tested == len(patterns)
    assert all(p.correction_method == "bonferroni" for p in result.validated_patterns)


# --------------------------------------------------------------- empty / insufficient
def test_empty_dataset_is_insufficient(retrieval):
    result = StatisticalValidator().validate(_dataset(retrieval), [])
    assert result.status is LearningStatus.INSUFFICIENT_DATA and result.validated_patterns == ()


def test_no_patterns_is_insufficient(retrieval, stores):
    ps, _ = stores
    for i in range(5):
        _seed(ps, i=i, sector="Energy")
    result = StatisticalValidator().validate(_dataset(retrieval), [])
    assert result.status is LearningStatus.INSUFFICIENT_DATA and result.validated_patterns == ()


def test_thin_dataset_short_circuits(retrieval, stores):
    ps, _ = stores
    for i in range(3):
        _seed(ps, i=i, sector="Energy")
    thin = LearningDatasetBuilder(retrieval, min_corpus=10).build()   # dataset INSUFFICIENT_DATA
    patterns = _manual_pattern([_record("a", 2.0), _record("b", 2.0), _record("c", 2.0)])
    result = StatisticalValidator().validate(thin, [patterns])
    assert result.status is LearningStatus.INSUFFICIENT_DATA and result.validated_patterns == ()


# --------------------------------------------------------------- corrupted / malformed / version
def test_corrupted_statistics_rejected():
    records = [_record("a", float("nan")), _record("b", 2.0), _record("c", 2.0)]
    ds = _manual_dataset(records)
    with pytest.raises(StatisticsError):
        StatisticalValidator(min_sample=1).validate(ds, [_manual_pattern(records)])


def test_malformed_pattern_rejected():
    ds = _manual_dataset([_record("a", 2.0)])
    with pytest.raises(MalformedPatternError):
        StatisticalValidator().validate(ds, [{"not": "a pattern"}])


def test_malformed_dataset_rejected():
    with pytest.raises(InvalidDatasetError):
        StatisticalValidator().validate({"not": "a dataset"}, [])


def test_version_mismatch_rejected():
    ds = _manual_dataset([_record("a", 2.0), _record("b", 2.0), _record("c", 2.0)])
    bad = CandidatePattern(
        pattern_id="x", learning_version=LEARNING_VERSION, dataset_version="lds-999",
        pattern_type="SETUP", grouping_key="sector", grouping_value="Energy",
        evidence_count=0, prediction_ids=(), corpus_size=0, status=LearningStatus.HYPOTHESIS,
    )
    with pytest.raises(UnsupportedVersionError):
        StatisticalValidator().validate(ds, [bad])


def test_evidence_absent_from_dataset_rejected():
    ds = _manual_dataset([_record("a", 2.0), _record("b", 2.0)])
    ghost = CandidatePattern.create(
        learning_version=LEARNING_VERSION, dataset_version=DATASET_VERSION, pattern_type="SETUP",
        grouping_key="sector", grouping_value="Energy", prediction_ids=["a", "b", "missing"],
        corpus_size=2, status=LearningStatus.HYPOTHESIS,
    )
    with pytest.raises(InconsistentEvidenceError):
        StatisticalValidator(min_sample=1).validate(ds, [ghost])


def test_unknown_correction_rejected():
    with pytest.raises(UnknownCorrectionError):
        StatisticalValidator(correction="made_up_method")


# --------------------------------------------------------------- reuse: regression vs Sprint 2
def test_regression_against_memory_aggregates(retrieval, stores):
    ps, _ = stores
    # A realistic mixed record set so win rate / avg R / drawdown are all non-trivial.
    for i in range(7):
        _seed(ps, i=i, sector="Energy", resolve_r=2.0, holding=4)
    for i in range(7, 12):
        _seed(ps, i=i, sector="Energy", resolve_r=-1.0, holding=8)
    ds = _dataset(retrieval)
    result = StatisticalValidator(min_sample=5).validate(ds, _patterns(ds))
    vp = next(p for p in result.validated_patterns if p.grouping_value == "Energy")

    aggs = {(a.dimension, a.bucket, a.model_version): a for a in compute_aggregates(ps.list_all())}
    energy = aggs[(AggregateDimension.SECTOR, "Energy", "")]
    # The validator reuses the Sprint 2 aggregate math ⇒ figures must match exactly.
    assert vp.sample_size == energy.n_resolved
    assert vp.win_rate == energy.win_rate
    assert vp.average_r == energy.avg_r
    assert vp.expectancy == energy.expectancy
    assert vp.profit_factor == energy.profit_factor
    assert vp.max_drawdown_r == energy.max_drawdown_r
    assert vp.avg_holding_bars == energy.avg_holding_bars


# --------------------------------------------------------------- migration (append-only)
def test_learning_pattern_stats_migration(tmp_path):
    conn = get_connection(str(tmp_path / "ph.db"))
    run_migrations(conn)
    assert 8 in applied_versions(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(learning_pattern_stats)")}
    assert {"pattern_key", "win_rate", "ci_low", "ci_high", "ci_quality", "p_value", "z_score",
            "correction_method", "correction_significant", "consistency_score", "status",
            "evidence_count"} <= cols


def test_validated_pattern_row_round_trip(tmp_path, retrieval, stores):
    ps, _ = stores
    for i in range(10):
        _seed(ps, i=i, sector="Energy")
    ds = _dataset(retrieval)
    vp = StatisticalValidator(min_sample=5).validate(ds, _patterns(ds)).validated_patterns[0]

    conn = get_connection(str(tmp_path / "roundtrip.db"))
    run_migrations(conn)
    row = vp.to_row()
    with conn:
        conn.execute(
            f"INSERT INTO learning_pattern_stats ({', '.join(row)}) "
            f"VALUES ({', '.join(':' + k for k in row)})", row
        )
    got = ValidatedPattern.from_row(
        conn.execute("SELECT * FROM learning_pattern_stats WHERE pattern_key=?",
                     (vp.pattern_key,)).fetchone()
    )
    assert got.pattern_key == vp.pattern_key and got.status is vp.status
    assert got.win_rate == vp.win_rate and got.confidence_interval.quality == vp.confidence_interval.quality
    assert got.significance.significant == vp.significance.significant


def test_stats_migration_leaves_predictions_unchanged(tmp_path):
    path = str(tmp_path / "ph.db")
    ps = PredictionStore(path=path)
    _seed(ps, i=0)
    schema = ps._conn.execute("SELECT sql FROM sqlite_master WHERE name='predictions'").fetchone()["sql"]
    run_migrations(ps._conn)
    assert ps._conn.execute("SELECT sql FROM sqlite_master WHERE name='predictions'").fetchone()["sql"] == schema
    ps.close()


# --------------------------------------------------------------- concurrency / isolation
def test_concurrent_validation(retrieval, stores):
    ps, _ = stores
    for i in range(10):
        _seed(ps, i=i, sector="Energy")
    ds = _dataset(retrieval)
    patterns = _patterns(ds)
    v = StatisticalValidator(min_sample=5)
    checksums: list[str] = []
    errors: list[Exception] = []

    def work() -> None:
        try:
            checksums.append(v.validate(ds, patterns).checksum)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(set(checksums)) == 1


def test_validation_performs_no_writes(retrieval, stores):
    ps, ms = stores
    for i in range(10):
        _seed(ps, i=i, sector="Energy")
    before = ms._conn.execute("SELECT COUNT(*) AS n FROM learning_pattern_stats").fetchone()["n"]
    ds = _dataset(retrieval)
    StatisticalValidator(min_sample=5).validate(ds, _patterns(ds))
    after = ms._conn.execute("SELECT COUNT(*) AS n FROM learning_pattern_stats").fetchone()["n"]
    assert before == after == 0                              # read-only: writes nothing


def test_statistics_module_does_not_import_engines():
    import ast

    import app.learning.statistics as stat
    with open(stat.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
