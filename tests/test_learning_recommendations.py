"""Tests for the Recommendation Engine (Sprint 4 · Vol 15 · Milestone 4).

Cover deterministic + reproducible recommendation generation, communication-confidence
classification (independent of significance), evidence traceability, non-empty contextual
limitations, deterministic identity, duplicate elimination, malformed / version-mismatch /
missing-evidence inputs, empty validated dataset, concurrency, the append-only
`learning_recommendations` migration + row round-trip, the **no-trading-advice** guarantee,
no-writes, and no-engine-imports. Temporary databases only.
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
    ConfidenceInterval,
    InvalidValidationError,
    LearningStatus,
    MissingEvidenceError,
    Recommendation,
    RecommendationConfidence,
    RecommendationType,
    Significance,
    UnsupportedVersionError,
    ValidatedPattern,
    ValidationResult,
)
from app.learning.patterns import PatternExtractor
from app.learning.recommendations import (
    RecommendationEngine,
    recommendation_category,
    recommendation_confidence,
    recommendation_type_of,
)
from app.learning.statistics import StatisticalValidator

_TS = 1_700_000_000

_ADVICE_PHRASES = [
    "you should", "will win", "guarantee", "take this trade", "should buy", "should sell",
    "is a profitable strategy", "buy now", "sell now",
]


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


def _seed(ps, *, i, sector="Energy", regime="BULL", tf="1d", resolve_r=2.0, holding=5):
    rec = PredictionRecord(
        symbol=f"S{i}.NS", exchange="NSE", timeframe=tf, current_price=100.0, direction="BUY",
        recommendation="BUY", created_candle_ts=_TS + i, entry=100.0, stop=95.0, target1=110.0,
        outcome_prob=0.62, sector=sector, market_regime=regime, prediction_model_version="pred-1",
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


def _pipeline(retrieval, ps, *, n=12, sector="Energy", min_sample=10):
    for i in range(n):
        _seed(ps, i=i, sector=sector)
    ds = LearningDatasetBuilder(retrieval, min_corpus=1).build()
    patterns = PatternExtractor(min_evidence=3, dimensions=["sector"]).extract(ds).patterns
    validation = StatisticalValidator(min_sample=min_sample).validate(ds, patterns)
    return validation, patterns


# ---- manual builders (isolate rubric / classifier tests from seeding) ----------------------
def _ci(low, high, quality):
    return ConfidenceInterval(low=low, high=high, width=round(high - low, 6), quality=quality)


def _vp(*, key="pk", grouping_key="sector", grouping_value="Energy", n=12, wins=12, win_rate=1.0,
        ci=None, p=0.001, consistency=1.0, status=LearningStatus.VALIDATED, baseline=0.5):
    ci = ci or _ci(0.70, 1.0, "MODERATE")
    return ValidatedPattern(
        pattern_key=key, learning_version=LEARNING_VERSION, dataset_version=DATASET_VERSION,
        pattern_type="SETUP", grouping_key=grouping_key, grouping_value=grouping_value,
        sample_size=n, wins=wins, losses=n - wins, win_rate=win_rate, loss_rate=(n - wins) / n,
        average_r=1.0, expectancy=1.0, profit_factor=2.0, max_drawdown_r=1.0, avg_holding_bars=5.0,
        confidence_interval=ci,
        significance=Significance(p_value=p, z_score=3.0, baseline=baseline, significant=p <= 0.05),
        correction_method="benjamini_hochberg", correction_significant=True,
        consistency_score=consistency, status=status, evidence_count=n,
    )


def _validation(patterns, *, correction="benjamini_hochberg"):
    return ValidationResult(
        validated_patterns=tuple(patterns), status=LearningStatus.VALIDATED,
        corpus_size=sum(p.sample_size for p in patterns),
        validated_count=sum(1 for p in patterns if p.status is LearningStatus.VALIDATED),
        hypothesis_count=0, insufficient_count=0, hypotheses_tested=len(patterns),
        correction_method=correction, min_sample=10, alpha=0.05, baseline=0.5,
        learning_version=LEARNING_VERSION, dataset_version=DATASET_VERSION, checksum="c",
        validation_duration_ms=0.0,
    )


def _candidate(key, ids, *, grouping_key="sector", grouping_value="Energy"):
    return CandidatePattern(
        pattern_id=key, learning_version=LEARNING_VERSION, dataset_version=DATASET_VERSION,
        pattern_type="SETUP", grouping_key=grouping_key, grouping_value=grouping_value,
        evidence_count=len(ids), prediction_ids=tuple(ids), corpus_size=len(ids),
        status=LearningStatus.HYPOTHESIS,
    )


# --------------------------------------------------------------- classifier / rubric (unit)
def test_recommendation_type_classification():
    strong = _vp(ci=_ci(0.70, 0.95, "MODERATE"), consistency=1.0)
    weak = _vp(win_rate=0.2, wins=2, ci=_ci(0.05, 0.40, "MODERATE"), consistency=1.0)
    unstable = _vp(ci=_ci(0.70, 0.95, "MODERATE"), consistency=0.30)
    assert recommendation_type_of(strong) is RecommendationType.HISTORICAL_STRENGTH
    assert recommendation_type_of(weak) is RecommendationType.HISTORICAL_WEAKNESS
    assert recommendation_type_of(unstable) is RecommendationType.UNSTABLE_BEHAVIOUR


def test_recommendation_category_by_dimension():
    assert recommendation_category("sector") == "Sector Observation"
    assert recommendation_category("market_regime") == "Regime Observation"
    assert recommendation_category("timeframe") == "Timeframe Observation"
    assert recommendation_category("symbol") == "Symbol Observation"
    assert recommendation_category("mystery") == "Historical Observation"


def test_confidence_independent_of_significance():
    # All three are statistically significant (VALIDATED) — communication confidence still varies.
    high = _vp(n=120, ci=_ci(0.90, 0.99, "HIGH"), consistency=0.95)
    medium = _vp(n=60, ci=_ci(0.60, 0.90, "MODERATE"), consistency=0.70)
    low = _vp(n=12, ci=_ci(0.55, 0.99, "LOW"), consistency=None)
    assert recommendation_confidence(high) is RecommendationConfidence.HIGH
    assert recommendation_confidence(medium) is RecommendationConfidence.MEDIUM
    assert recommendation_confidence(low) is RecommendationConfidence.LOW
    assert low.significance.significant                      # significant, yet only LOW to communicate


# --------------------------------------------------------------- determinism
def test_deterministic_generation():
    vp = _vp(key="k1")
    validation = _validation([vp])
    cands = [_candidate("k1", ["a", "b", "c"])]
    eng = RecommendationEngine()
    a, b = eng.generate(validation, cands), eng.generate(validation, cands)
    assert a.checksum == b.checksum
    assert [r.recommendation_id for r in a.recommendations] == [r.recommendation_id for r in b.recommendations]


def test_identical_inputs_identical_recommendations():
    vp = _vp(key="k1")
    r1 = RecommendationEngine().generate(_validation([vp]), [_candidate("k1", ["a", "b"])]).recommendations[0]
    r2 = RecommendationEngine().generate(_validation([vp]), [_candidate("k1", ["a", "b"])]).recommendations[0]
    assert r1.recommendation_id == r2.recommendation_id and r1.summary == r2.summary


# --------------------------------------------------------------- identity
def test_recommendation_identity_is_deterministic():
    vp = _vp(key="k1")
    rec = RecommendationEngine().generate(_validation([vp]), [_candidate("k1", ["a"])]).recommendations[0]
    rtype = recommendation_type_of(vp).value
    assert rec.recommendation_key == f"{LEARNING_VERSION}|k1|{rtype}"
    assert rec.recommendation_id == rec.recommendation_hash[:16]
    assert len(rec.recommendation_hash) == 64                # sha256 hex


# --------------------------------------------------------------- evidence / limitations
def test_evidence_traceability_end_to_end(retrieval, stores):
    ps, _ = stores
    validation, patterns = _pipeline(retrieval, ps, n=12)
    result = RecommendationEngine().generate(validation, patterns)
    assert result.status is LearningStatus.VALIDATED and result.recommendations
    energy_candidate = next(p for p in patterns if p.grouping_value == "Energy")
    rec = next(r for r in result.recommendations if "Energy" in r.title)
    assert set(rec.supporting_prediction_ids) == set(energy_candidate.prediction_ids)
    assert rec.evidence_count == rec.sample_size == 12


def test_limitations_always_present_and_contextual():
    regime = _vp(key="r", grouping_key="market_regime", grouping_value="BULL")
    rec = RecommendationEngine().generate(_validation([regime]), [_candidate("r", ["a"], grouping_key="market_regime")]).recommendations[0]
    assert rec.limitations                                    # never empty
    assert any("regime" in lim.lower() for lim in rec.limitations)
    assert any("not trading advice" in lim.lower() for lim in rec.limitations)

    unstable = _vp(key="u", consistency=0.30)
    rec2 = RecommendationEngine().generate(_validation([unstable]), [_candidate("u", ["a"])]).recommendations[0]
    assert any("curve-fit" in lim.lower() for lim in rec2.limitations)


def test_no_trading_advice_language(retrieval, stores):
    ps, _ = stores
    validation, patterns = _pipeline(retrieval, ps, n=12)
    result = RecommendationEngine().generate(validation, patterns)
    for rec in result.recommendations:
        blob = " ".join([rec.title, rec.summary, rec.detailed_explanation, rec.statistical_basis,
                         *rec.limitations]).lower()
        for phrase in _ADVICE_PHRASES:
            assert phrase not in blob, f"advice phrase {phrase!r} leaked into a recommendation"
        assert "historically" in rec.summary.lower()         # descriptive framing


# --------------------------------------------------------------- dedup / empty / errors
def test_duplicate_patterns_collapsed():
    vp = _vp(key="dup")
    validation = _validation([vp, vp])                       # same validated pattern twice
    result = RecommendationEngine().generate(validation, [_candidate("dup", ["a", "b"])])
    assert result.recommendations_created == 1


def test_no_validated_patterns_is_insufficient():
    hyp = _vp(key="h", status=LearningStatus.HYPOTHESIS)
    result = RecommendationEngine().generate(_validation([hyp]), [_candidate("h", ["a"])])
    assert result.status is LearningStatus.INSUFFICIENT_DATA and result.recommendations == ()


def test_missing_evidence_rejected():
    vp = _vp(key="k1")
    with pytest.raises(MissingEvidenceError):
        RecommendationEngine().generate(_validation([vp]), [])   # no candidate ⇒ no evidence


def test_malformed_validation_rejected():
    with pytest.raises(InvalidValidationError):
        RecommendationEngine().generate({"not": "a validation"}, [])


def test_version_mismatch_rejected():
    vp = _vp(key="k1")
    bad = ValidationResult(
        validated_patterns=(vp,), status=LearningStatus.VALIDATED, corpus_size=12,
        validated_count=1, hypothesis_count=0, insufficient_count=0, hypotheses_tested=1,
        correction_method="none", min_sample=10, alpha=0.05, baseline=0.5,
        learning_version="lrn-999", dataset_version=DATASET_VERSION, checksum="c",
        validation_duration_ms=0.0,
    )
    with pytest.raises(UnsupportedVersionError):
        RecommendationEngine().generate(bad, [_candidate("k1", ["a"])])


def test_confidence_distribution_reported():
    vps = [_vp(key="hi", n=120, ci=_ci(0.90, 0.99, "HIGH"), consistency=0.95),
           _vp(key="lo", n=12, ci=_ci(0.55, 0.99, "LOW"), consistency=None)]
    cands = [_candidate("hi", ["a"]), _candidate("lo", ["b"])]
    result = RecommendationEngine().generate(_validation(vps), cands)
    assert result.confidence_distribution["HIGH"] == 1 and result.confidence_distribution["LOW"] == 1


# --------------------------------------------------------------- migration (append-only)
def test_learning_recommendations_migration(tmp_path):
    conn = get_connection(str(tmp_path / "ph.db"))
    run_migrations(conn)
    assert 9 in applied_versions(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(learning_recommendations)")}
    assert {"recommendation_id", "recommendation_key", "pattern_key", "recommendation_type",
            "recommendation_category", "summary", "supporting_prediction_ids_json",
            "limitations_json", "recommendation_confidence"} <= cols


def test_recommendation_row_round_trip(tmp_path):
    vp = _vp(key="k1")
    rec = RecommendationEngine().generate(_validation([vp]), [_candidate("k1", ["a", "b", "c"])]).recommendations[0]
    conn = get_connection(str(tmp_path / "rt.db"))
    run_migrations(conn)
    row = rec.to_row()
    with conn:
        conn.execute(
            f"INSERT INTO learning_recommendations ({', '.join(row)}) "
            f"VALUES ({', '.join(':' + k for k in row)})", row
        )
    got = Recommendation.from_row(
        conn.execute("SELECT * FROM learning_recommendations WHERE recommendation_id=?",
                     (rec.recommendation_id,)).fetchone()
    )
    assert got.recommendation_key == rec.recommendation_key
    assert got.supporting_prediction_ids == ("a", "b", "c")
    assert got.recommendation_confidence is rec.recommendation_confidence
    assert got.limitations == rec.limitations


def test_recommendations_migration_leaves_predictions_unchanged(tmp_path):
    path = str(tmp_path / "ph.db")
    ps = PredictionStore(path=path)
    _seed(ps, i=0)
    schema = ps._conn.execute("SELECT sql FROM sqlite_master WHERE name='predictions'").fetchone()["sql"]
    run_migrations(ps._conn)
    assert ps._conn.execute("SELECT sql FROM sqlite_master WHERE name='predictions'").fetchone()["sql"] == schema
    ps.close()


# --------------------------------------------------------------- concurrency / isolation
def test_concurrent_generation():
    vp = _vp(key="k1")
    validation = _validation([vp])
    cands = [_candidate("k1", ["a", "b", "c"])]
    eng = RecommendationEngine()
    checksums: list[str] = []
    errors: list[Exception] = []

    def work() -> None:
        try:
            checksums.append(eng.generate(validation, cands).checksum)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(set(checksums)) == 1


def test_generation_performs_no_writes(retrieval, stores):
    ps, ms = stores
    validation, patterns = _pipeline(retrieval, ps, n=12)
    before = ms._conn.execute("SELECT COUNT(*) AS n FROM learning_recommendations").fetchone()["n"]
    RecommendationEngine().generate(validation, patterns)
    after = ms._conn.execute("SELECT COUNT(*) AS n FROM learning_recommendations").fetchone()["n"]
    assert before == after == 0                              # read-only: writes nothing


def test_recommendations_module_does_not_import_engines():
    import ast

    import app.learning.recommendations as rec
    with open(rec.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
