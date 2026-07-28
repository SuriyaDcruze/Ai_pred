"""Tests for the Similarity Search Engine (Sprint 3 · Vol 14 · Milestone 3).

Cover cosine correctness, ranking + deterministic ordering, top-k, filtering, candidate cap,
threshold, empty corpus, duplicate prevention, version/dimension/request validation, honest
summary stats, concurrency, and that search performs no writes. Embeddings are crafted
directly (axis-aligned unit vectors) so cosine values are exact. Temporary databases only.
"""

from __future__ import annotations

import math
import threading

import pytest

from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.models import MemoryEmbedding
from app.memory.retrieval import RetrievalEngine
from app.memory.store import MemoryStore
from app.similarity.embedding import EMBEDDING_KIND, EMBEDDING_VERSION
from app.similarity.feature_vector import FEATURE_VERSION, VECTOR_DIM
from app.similarity.models import (
    DimensionMismatchError,
    Embedding,
    InvalidFeatureVectorError,
    MissingEmbeddingError,
    SearchRequestError,
    UnsupportedVersionError,
)
from app.similarity.search import (
    SimilarityFilter,
    SimilaritySearchEngine,
    cosine_similarity,
)

_TS = 1_700_000_000


# --------------------------------------------------------------------------- helpers
def _axis(idx: int, sign: float = 1.0) -> list[float]:
    v = [0.0] * VECTOR_DIM
    v[idx] = sign
    return v


def _mk_embedding(vector: list[float], version: str = EMBEDDING_VERSION) -> Embedding:
    return Embedding(
        vector=tuple(vector), embedding_version=version, feature_version=FEATURE_VERSION,
        schema_version=1, dimension=len(vector), embedding_kind=EMBEDDING_KIND,
        created_at="2026-01-01T00:00:00+00:00",
    )


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
def engine(stores):
    ps, ms = stores
    return SimilaritySearchEngine(RetrievalEngine(ps, ms), ms)


def _seed(ps, ms, *, i, vector, symbol=None, sector="Energy", regime="BULL", phase=None,
          tf="1d", resolve_r=None, model_name=None):
    rec = PredictionRecord(
        symbol=symbol or f"S{i:02d}.NS", exchange="NSE", timeframe=tf, current_price=100.0,
        direction="BUY", recommendation="BUY", created_candle_ts=_TS + i, entry=100.0,
        stop=95.0, target1=110.0, outcome_prob=0.6, sector=sector, market_regime=regime,
        market_phase=phase, prediction_model_version="pred-1", status=PredictionStatus.ACTIVE,
    )
    ps.create(rec)
    if resolve_r is not None:
        ps.update_resolution(
            rec.prediction_id,
            status=PredictionStatus.TARGET_HIT if resolve_r > 0 else PredictionStatus.STOP_HIT,
            resolved_price=110.0 if resolve_r > 0 else 95.0,
            resolution_reason="t", realised_r=resolve_r, holding_bars=5,
        )
    ms.upsert_embedding(MemoryEmbedding(
        prediction_id=rec.prediction_id, embedding_kind=EMBEDDING_KIND,
        model_name=model_name or f"{EMBEDDING_VERSION}/{FEATURE_VERSION}",
        dim=len(vector), vector=list(vector), schema_version=1,
    ))
    return rec.prediction_id


# --------------------------------------------------------------- cosine metric
def test_cosine_similarity_values():
    assert cosine_similarity(_axis(0), _axis(0)) == pytest.approx(1.0)
    assert cosine_similarity(_axis(0), _axis(1)) == pytest.approx(0.0)
    assert cosine_similarity(_axis(0), _axis(0, -1.0)) == pytest.approx(-1.0)
    assert cosine_similarity([0.0] * VECTOR_DIM, _axis(0)) == 0.0   # zero vector → 0, no error


def test_cosine_intermediate():
    # [0.6, 0.8, 0, ...] vs e0 → 0.6
    v = [0.6, 0.8] + [0.0] * (VECTOR_DIM - 2)
    assert cosine_similarity(v, _axis(0)) == pytest.approx(0.6)


# --------------------------------------------------------------- ranking / top-k
def test_ranking_order_descending(engine, stores):
    ps, ms = stores
    _seed(ps, ms, i=1, vector=_axis(0))            # sim 1.0
    _seed(ps, ms, i=2, vector=[0.6, 0.8] + [0.0] * (VECTOR_DIM - 2))   # sim 0.6
    _seed(ps, ms, i=3, vector=_axis(1))            # sim 0.0
    _seed(ps, ms, i=4, vector=_axis(0, -1.0))      # sim -1.0
    result = engine.search(_mk_embedding(_axis(0)))
    scores = [round(n.similarity_score, 4) for n in result.neighbours]
    assert scores == sorted(scores, reverse=True)          # descending
    assert scores[0] == pytest.approx(1.0) and scores[-1] == pytest.approx(-1.0)


def test_top_k(engine, stores):
    ps, ms = stores
    for i in range(5):
        _seed(ps, ms, i=i, vector=_axis(i))
    result = engine.search(_mk_embedding(_axis(0)), k=2)
    assert result.returned == 2 and len(result.neighbours) == 2


def test_deterministic_ordering_and_tiebreak(engine, stores):
    ps, ms = stores
    # two identical-vector candidates → tie in similarity → ordered by prediction_id.
    a = _seed(ps, ms, i=1, vector=_axis(1))
    b = _seed(ps, ms, i=2, vector=_axis(1))
    r1 = engine.search(_mk_embedding(_axis(0)))
    r2 = engine.search(_mk_embedding(_axis(0)))
    ids1 = [n.prediction_id for n in r1.neighbours]
    ids2 = [n.prediction_id for n in r2.neighbours]
    assert ids1 == ids2                                    # reproducible
    tied = [pid for pid in ids1 if pid in (a, b)]
    assert tied == sorted(tied)                            # deterministic tie-break by id


# --------------------------------------------------------------- filtering
def test_filter_by_sector(engine, stores):
    ps, ms = stores
    _seed(ps, ms, i=1, vector=_axis(0), sector="Energy")
    _seed(ps, ms, i=2, vector=_axis(0), sector="IT")
    result = engine.search(_mk_embedding(_axis(0)), filter=SimilarityFilter(sector="IT"))
    assert result.returned == 1 and result.neighbours[0].sector == "IT"


def test_filter_by_market_phase(engine, stores):
    ps, ms = stores
    _seed(ps, ms, i=1, vector=_axis(0), phase="MARKUP")
    _seed(ps, ms, i=2, vector=_axis(0), phase="MARKDOWN")
    result = engine.search(_mk_embedding(_axis(0)), filter=SimilarityFilter(market_phase="MARKUP"))
    assert result.returned == 1 and result.neighbours[0].market_phase == "MARKUP"


# --------------------------------------------------------------- cap / threshold / empty
def test_candidate_cap(engine, stores):
    ps, ms = stores
    for i in range(5):
        _seed(ps, ms, i=i, vector=_axis(0))
    result = engine.search(_mk_embedding(_axis(0)), candidate_cap=2)
    assert result.candidate_count <= 2 and result.cap_applied is True


def test_threshold_filters(engine, stores):
    ps, ms = stores
    _seed(ps, ms, i=1, vector=_axis(0))            # 1.0
    _seed(ps, ms, i=2, vector=_axis(1))            # 0.0
    _seed(ps, ms, i=3, vector=_axis(0, -1.0))      # -1.0
    result = engine.search(_mk_embedding(_axis(0)), min_similarity=0.5)
    assert result.returned == 1 and result.neighbours[0].similarity_score == pytest.approx(1.0)


def test_empty_corpus(engine):
    result = engine.search(_mk_embedding(_axis(0)))
    assert result.returned == 0 and result.candidate_count == 0
    assert result.summary.sample_size == 0 and result.summary.win_rate is None


def test_no_duplicate_predictions(engine, stores):
    ps, ms = stores
    for i in range(4):
        _seed(ps, ms, i=i, vector=_axis(0))
    result = engine.search(_mk_embedding(_axis(0)))
    ids = [n.prediction_id for n in result.neighbours]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------- search_by_prediction
def test_search_by_prediction_excludes_self(engine, stores):
    ps, ms = stores
    q = _seed(ps, ms, i=1, vector=_axis(0))
    _seed(ps, ms, i=2, vector=_axis(0))
    _seed(ps, ms, i=3, vector=_axis(1))
    result = engine.search_by_prediction(q, k=10)
    ids = {n.prediction_id for n in result.neighbours}
    assert q not in ids                                    # never returns the query itself
    assert result.query_prediction_id == q


def test_search_by_prediction_missing_embedding_raises(engine, stores):
    ps, ms = stores
    rec = PredictionRecord(
        symbol="X.NS", exchange="NSE", timeframe="1d", current_price=1.0, direction="BUY",
        recommendation="BUY", created_candle_ts=_TS + 99, status=PredictionStatus.ACTIVE,
    )
    ps.create(rec)   # prediction exists, but no embedding stored
    with pytest.raises(MissingEmbeddingError):
        engine.search_by_prediction(rec.prediction_id)


# --------------------------------------------------------------- version / request validation
def test_incompatible_version_candidates_skipped(engine, stores):
    ps, ms = stores
    _seed(ps, ms, i=1, vector=_axis(0))                                  # current version
    _seed(ps, ms, i=2, vector=_axis(0), model_name="sim-emb-0/sim-fv-1")  # old version → skipped
    result = engine.search(_mk_embedding(_axis(0)))
    assert result.returned == 1


def test_query_version_mismatch_raises(engine):
    with pytest.raises(UnsupportedVersionError):
        engine.search(_mk_embedding(_axis(0), version="sim-emb-999"))


def test_query_dimension_mismatch_raises(engine):
    bad = Embedding(vector=(0.1, 0.2), embedding_version=EMBEDDING_VERSION,
                    feature_version=FEATURE_VERSION, schema_version=1, dimension=2,
                    embedding_kind=EMBEDDING_KIND, created_at="x")
    with pytest.raises(DimensionMismatchError):
        engine.search(bad)


def test_query_non_finite_raises(engine):
    with pytest.raises(InvalidFeatureVectorError):
        engine.search(_mk_embedding([float("nan")] + [0.0] * (VECTOR_DIM - 1)))


def test_malformed_request_raises(engine):
    with pytest.raises(SearchRequestError):
        engine.search(_mk_embedding(_axis(0)), k=0)
    with pytest.raises(SearchRequestError):
        engine.search(_mk_embedding(_axis(0)), min_similarity=2.0)
    with pytest.raises(SearchRequestError):
        engine.search(_mk_embedding(_axis(0)), candidate_cap=0)


# --------------------------------------------------------------- summary stats
def test_summary_statistics(engine, stores):
    ps, ms = stores
    _seed(ps, ms, i=1, vector=_axis(0), resolve_r=2.0)     # WIN
    _seed(ps, ms, i=2, vector=_axis(0), resolve_r=2.0)     # WIN
    _seed(ps, ms, i=3, vector=_axis(0), resolve_r=-1.0)    # LOSS
    result = engine.search(_mk_embedding(_axis(0)))
    s = result.summary
    assert s.sample_size == 3 and s.resolved == 3
    assert s.win_rate == pytest.approx(2 / 3)
    assert s.avg_realised_r == pytest.approx(1.0)          # (2+2-1)/3
    assert s.outcome_distribution.get("WIN") == 2 and s.outcome_distribution.get("LOSS") == 1


# --------------------------------------------------------------- concurrency / read-only
def test_concurrent_searches(stores):
    ps, ms = stores
    for i in range(6):
        _seed(ps, ms, i=i, vector=_axis(i % 5))
    engine = SimilaritySearchEngine(RetrievalEngine(ps, ms), ms)
    results: list[int] = []
    errors: list[Exception] = []

    def work() -> None:
        try:
            results.append(engine.search(_mk_embedding(_axis(0))).returned)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(set(results)) == 1          # all searches agree


def test_search_performs_no_writes(engine, stores):
    ps, ms = stores
    for i in range(3):
        _seed(ps, ms, i=i, vector=_axis(0))
    preds = ms._conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]
    embs = ms._conn.execute("SELECT COUNT(*) AS n FROM memory_embeddings").fetchone()["n"]
    engine.search(_mk_embedding(_axis(0)))
    engine.search(_mk_embedding(_axis(1)), filter=SimilarityFilter(sector="Energy"))
    assert ms._conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"] == preds
    assert ms._conn.execute("SELECT COUNT(*) AS n FROM memory_embeddings").fetchone()["n"] == embs


def test_search_module_does_not_import_engines():
    import ast

    import app.similarity.search as srch
    with open(srch.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
