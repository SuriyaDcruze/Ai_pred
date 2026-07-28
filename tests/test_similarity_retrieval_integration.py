"""Retrieval-integration tests for the Similarity Engine (Sprint 3 · Vol 14 · Milestone 4).

Verify the additive dependency-injection wiring of the Similarity Search Engine into
``RetrievalEngine``: enabled vs disabled behaviour, graceful fallback, the response contract,
typed validation, determinism, concurrency, read-only, and — critically — that existing
Historical Memory retrieval behaviour is **unchanged** (backward compatible). Temporary DBs.
"""

from __future__ import annotations

import threading

import pytest

from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.errors import MemoryNotFoundError
from app.memory.models import MemoryEmbedding
from app.memory.retrieval import MemoryFilter, RetrievalEngine, SimilarityResult
from app.memory.store import MemoryStore
from app.similarity.embedding import EMBEDDING_KIND, EMBEDDING_VERSION
from app.similarity.feature_vector import FEATURE_VERSION, VECTOR_DIM
from app.similarity.models import Embedding, MissingEmbeddingError
from app.similarity.search import SimilaritySearchEngine

_TS = 1_700_000_000


def _axis(idx: int, sign: float = 1.0) -> list[float]:
    v = [0.0] * VECTOR_DIM
    v[idx] = sign
    return v


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


def _seed(ps, ms, *, i, vector, sector="Energy", resolve_r=2.0, embed=True):
    rec = PredictionRecord(
        symbol=f"S{i:02d}.NS", exchange="NSE", timeframe="1d", current_price=100.0,
        direction="BUY", recommendation="BUY", created_candle_ts=_TS + i, entry=100.0,
        stop=95.0, target1=110.0, outcome_prob=0.6, sector=sector, market_regime="BULL",
        prediction_model_version="pred-1", status=PredictionStatus.ACTIVE,
    )
    ps.create(rec)
    if resolve_r is not None:
        ps.update_resolution(
            rec.prediction_id,
            status=PredictionStatus.TARGET_HIT if resolve_r > 0 else PredictionStatus.STOP_HIT,
            resolved_price=110.0 if resolve_r > 0 else 95.0, resolution_reason="t",
            realised_r=resolve_r, holding_bars=5,
        )
    if embed:
        ms.upsert_embedding(MemoryEmbedding(
            prediction_id=rec.prediction_id, embedding_kind=EMBEDDING_KIND,
            model_name=f"{EMBEDDING_VERSION}/{FEATURE_VERSION}", dim=VECTOR_DIM,
            vector=list(vector), schema_version=1,
        ))
    return rec.prediction_id


def _enabled(ps, ms) -> RetrievalEngine:
    retrieval = RetrievalEngine(ps, ms)
    retrieval.set_similarity_engine(SimilaritySearchEngine(retrieval, ms))
    return retrieval


# --------------------------------------------------------------- disabled (backward compat)
def test_disabled_returns_unavailable(stores):
    ps, ms = stores
    pid = _seed(ps, ms, i=1, vector=_axis(0))
    result = RetrievalEngine(ps, ms).similar(pid)          # no engine injected (default)
    assert result.available is False
    assert result.reason == "Similarity Engine unavailable" and result.results == []


def test_disabled_unknown_prediction_still_404s(stores):
    ps, ms = stores
    with pytest.raises(MemoryNotFoundError):
        RetrievalEngine(ps, ms).similar("no-such-id")


def test_constructor_injection_also_works(stores):
    ps, ms = stores
    pid = _seed(ps, ms, i=1, vector=_axis(0))
    _seed(ps, ms, i=2, vector=_axis(0))
    retrieval = RetrievalEngine(ps, ms)
    retrieval.set_similarity_engine(SimilaritySearchEngine(retrieval, ms))
    # also via constructor kwarg
    r2 = RetrievalEngine(ps, ms)
    r2.set_similarity_engine(SimilaritySearchEngine(r2, ms))
    assert r2.similar(pid).available is True


# --------------------------------------------------------------- enabled
def test_enabled_returns_neighbours(stores):
    ps, ms = stores
    q = _seed(ps, ms, i=1, vector=_axis(0), resolve_r=2.0)
    _seed(ps, ms, i=2, vector=_axis(0), resolve_r=2.0)
    _seed(ps, ms, i=3, vector=_axis(1), resolve_r=-1.0)
    result = _enabled(ps, ms).similar(q, k=10)

    assert result.available is True and result.reason == ""
    ids = [n["prediction_id"] for n in result.results]
    assert q not in ids                                    # excludes self
    assert result.results[0]["similarity_score"] >= result.results[-1]["similarity_score"]
    # response contract present
    assert result.sample_size == len(result.results)
    assert result.summary["win_rate"] is not None
    assert result.metadata["similarity_version"] == "sim-search-1"
    assert result.metadata["feature_version"] == FEATURE_VERSION


def test_enabled_hides_raw_vectors(stores):
    ps, ms = stores
    q = _seed(ps, ms, i=1, vector=_axis(0))
    _seed(ps, ms, i=2, vector=_axis(0))
    n = _enabled(ps, ms).similar(q).results[0]
    assert "vector" not in n and "embedding" not in n
    assert "embedding_version" in n and "feature_version" in n   # versions, not vectors


def test_enabled_empty_corpus(stores):
    ps, ms = stores
    q = _seed(ps, ms, i=1, vector=_axis(0))       # only the query itself is embedded
    result = _enabled(ps, ms).similar(q)
    assert result.available is True and result.results == []
    assert result.sample_size == 0


def test_similar_by_embedding(stores):
    ps, ms = stores
    _seed(ps, ms, i=1, vector=_axis(0))
    _seed(ps, ms, i=2, vector=_axis(1))
    query = Embedding(vector=tuple(_axis(0)), embedding_version=EMBEDDING_VERSION,
                      feature_version=FEATURE_VERSION, schema_version=1, dimension=VECTOR_DIM,
                      embedding_kind=EMBEDDING_KIND, created_at="x")
    result = _enabled(ps, ms).similar_by_embedding(query, k=5)
    assert result.available is True and len(result.results) == 2


def test_similar_by_embedding_disabled(stores):
    ps, ms = stores
    query = Embedding(vector=tuple(_axis(0)), embedding_version=EMBEDDING_VERSION,
                      feature_version=FEATURE_VERSION, schema_version=1, dimension=VECTOR_DIM,
                      embedding_kind=EMBEDDING_KIND, created_at="x")
    assert RetrievalEngine(ps, ms).similar_by_embedding(query).available is False


# --------------------------------------------------------------- validation / fallback
def test_missing_embedding_raises_typed(stores):
    ps, ms = stores
    rec = PredictionRecord(
        symbol="X.NS", exchange="NSE", timeframe="1d", current_price=1.0, direction="BUY",
        recommendation="BUY", created_candle_ts=_TS + 99, status=PredictionStatus.ACTIVE,
    )
    ps.create(rec)                                          # exists, but no embedding
    with pytest.raises(MissingEmbeddingError):
        _enabled(ps, ms).similar(rec.prediction_id)


def test_enabled_unknown_prediction_404s(stores):
    ps, ms = stores
    with pytest.raises(MemoryNotFoundError):
        _enabled(ps, ms).similar("no-such-id")


def test_unexpected_engine_failure_falls_back(stores):
    ps, ms = stores
    pid = _seed(ps, ms, i=1, vector=_axis(0))

    class BrokenEngine:
        def search_by_prediction(self, prediction_id, *, k=5):
            raise RuntimeError("kaboom")   # unexpected (not a SimilarityError)

    retrieval = RetrievalEngine(ps, ms)
    retrieval.set_similarity_engine(BrokenEngine())
    result = retrieval.similar(pid)
    assert result.available is False and result.reason == "Similarity Engine unavailable"


def test_deterministic(stores):
    ps, ms = stores
    q = _seed(ps, ms, i=1, vector=_axis(0))
    _seed(ps, ms, i=2, vector=_axis(0))
    _seed(ps, ms, i=3, vector=_axis(1))
    retrieval = _enabled(ps, ms)
    a = [n["prediction_id"] for n in retrieval.similar(q).results]
    b = [n["prediction_id"] for n in retrieval.similar(q).results]
    assert a == b


# --------------------------------------------------------------- backward compatibility
def test_existing_retrieval_behaviour_unchanged(stores):
    ps, ms = stores
    from app.memory.builder import MemoryBuilder
    pid = _seed(ps, ms, i=1, vector=_axis(0))
    MemoryBuilder(ps, ms).build(pid)
    retrieval = _enabled(ps, ms)   # engine injected — must not affect other methods

    assert retrieval.get_record(pid).to_dict()["prediction_id"] == pid
    assert retrieval.search(MemoryFilter(sector="Energy")).count >= 1
    assert retrieval.aggregates() != []
    assert "records" in retrieval.gpt_context(symbol="S01.NS")


def test_disabled_similarresult_shape_is_backward_compatible():
    # constructing the old 3-arg way still works (new fields default).
    r = SimilarityResult(available=False, reason="x", results=[])
    assert r.sample_size is None and r.summary is None and r.metadata is None


# --------------------------------------------------------------- concurrency / read-only
def test_concurrent_similarity(stores):
    ps, ms = stores
    ids = [_seed(ps, ms, i=i, vector=_axis(i % 5)) for i in range(6)]
    retrieval = _enabled(ps, ms)
    counts: list[int] = []
    errors: list[Exception] = []

    def work(pid: str) -> None:
        try:
            counts.append(len(retrieval.similar(pid).results))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(pid,)) for pid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_similarity_performs_no_writes(stores):
    ps, ms = stores
    q = _seed(ps, ms, i=1, vector=_axis(0))
    _seed(ps, ms, i=2, vector=_axis(0))
    retrieval = _enabled(ps, ms)
    preds = ms._conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]
    embs = ms._conn.execute("SELECT COUNT(*) AS n FROM memory_embeddings").fetchone()["n"]
    retrieval.similar(q)
    assert ms._conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"] == preds
    assert ms._conn.execute("SELECT COUNT(*) AS n FROM memory_embeddings").fetchone()["n"] == embs


def test_retrieval_has_no_module_level_similarity_import():
    """The similarity import must be lazy (call-time) — a module-level import of app.similarity
    would create a RetrievalEngine <-> SimilaritySearchEngine cycle. Only top-level imports are
    inspected; the deliberate lazy import inside a method is allowed (and necessary)."""
    import ast

    import app.memory.retrieval as ret
    with open(ret.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    top_level: list[str] = []
    for node in tree.body:                       # module top level only, not nested/lazy imports
        if isinstance(node, ast.Import):
            top_level += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(top_level)
    assert not any(m.startswith("app.similarity") for m in top_level)   # lazy only, no cycle
