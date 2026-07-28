"""Tests for the Embedding Generator (Sprint 3 · Vol 14 · Milestone 2).

Cover deterministic embeddings, L2 normalisation, storage into ``memory_embeddings``, rebuild,
idempotent backfill, concurrency, invalid/mismatched vectors, duplicate protection, rollback,
and that nothing but ``memory_embeddings`` is written. Temporary databases only.
"""

from __future__ import annotations

import math
import threading

import pytest

from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.builder import MemoryBuilder
from app.memory.retrieval import RetrievalEngine
from app.memory.store import MemoryStore
from app.similarity.embedding import (
    EMBEDDING_KIND,
    EMBEDDING_VERSION,
    EmbeddingGenerator,
    l2_normalize,
)
from app.similarity.feature_vector import FEATURE_VERSION, VECTOR_DIM, FeatureVectorBuilder
from app.similarity.models import (
    DimensionMismatchError,
    FeatureVector,
    InvalidFeatureVectorError,
    UnsupportedVersionError,
)

_TS = 1_700_000_000


# --------------------------------------------------------------------------- fixtures
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
def generator(stores):
    ps, ms = stores
    return EmbeddingGenerator(RetrievalEngine(ps, ms), ms)


def _seed(ps, ms, *, i=0, symbol="REL.NS", sector="Energy", prob=0.62, r=2.0, build=True):
    rec = PredictionRecord(
        symbol=symbol, exchange="NSE", timeframe="1d", current_price=100.0, direction="BUY",
        recommendation="BUY", created_candle_ts=_TS + i, entry=100.0, stop=95.0, target1=110.0,
        outcome_prob=prob, sector=sector, market_regime="BULL", prediction_model_version="pred-1",
        status=PredictionStatus.ACTIVE,
    )
    ps.create(rec)
    ps.update_resolution(rec.prediction_id, status=PredictionStatus.TARGET_HIT,
                         resolved_price=110.0, resolution_reason="t", realised_r=r, holding_bars=5)
    if build:
        MemoryBuilder(ps, ms).build(rec.prediction_id)
    return rec.prediction_id


def _fv(values, *, feature_version=FEATURE_VERSION, dimension=VECTOR_DIM):
    return FeatureVector(values=tuple(values), feature_version=feature_version, schema_version=1, dimension=dimension)


# --------------------------------------------------------------- pure transform
def test_l2_normalize_unit_length():
    out = l2_normalize([3.0, 4.0])
    assert math.isclose(math.sqrt(sum(v * v for v in out)), 1.0, abs_tol=1e-9)
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]   # zero stays zero


def test_generate_from_feature_vector_is_deterministic(generator):
    fv = _fv([0.1] * VECTOR_DIM)
    a = generator.generate_from_feature_vector(fv)
    b = generator.generate_from_feature_vector(fv)
    assert a.vector == b.vector
    assert math.isclose(math.sqrt(sum(v * v for v in a.vector)), 1.0, abs_tol=1e-9)
    assert a.embedding_version == EMBEDDING_VERSION and a.feature_version == FEATURE_VERSION
    assert a.dimension == VECTOR_DIM and a.embedding_kind == EMBEDDING_KIND and a.created_at


def test_identical_vectors_identical_embeddings(generator):
    fv1 = _fv([0.2] * VECTOR_DIM)
    fv2 = _fv([0.2] * VECTOR_DIM)
    assert generator.generate_from_feature_vector(fv1).vector == generator.generate_from_feature_vector(fv2).vector


def test_different_vectors_different_embeddings(generator):
    a = generator.generate_from_feature_vector(_fv([0.1] * VECTOR_DIM))
    b = generator.generate_from_feature_vector(_fv([0.1] * (VECTOR_DIM - 1) + [0.9]))
    assert a.vector != b.vector


def test_zero_vector_embeds_to_zero(generator):
    emb = generator.generate_from_feature_vector(_fv([0.0] * VECTOR_DIM))
    assert all(v == 0.0 for v in emb.vector)


# --------------------------------------------------------------- validation
def test_unsupported_feature_version_rejected(generator):
    with pytest.raises(UnsupportedVersionError):
        generator.generate_from_feature_vector(_fv([0.1] * VECTOR_DIM, feature_version="sim-fv-999"))


def test_dimension_mismatch_rejected(generator):
    with pytest.raises(DimensionMismatchError):
        generator.generate_from_feature_vector(_fv([0.1] * 50, dimension=50))


def test_invalid_vector_rejected(generator):
    with pytest.raises(InvalidFeatureVectorError):
        generator.generate_from_feature_vector(_fv([float("nan")] + [0.1] * (VECTOR_DIM - 1)))


def test_store_without_prediction_id_rejected(generator):
    emb = generator.generate_from_feature_vector(_fv([0.1] * VECTOR_DIM))   # no prediction_id
    with pytest.raises(InvalidFeatureVectorError):
        generator.store_embedding(emb)


# --------------------------------------------------------------- storage / rebuild
def test_build_and_store_persists_vector(generator, stores):
    ps, ms = stores
    pid = _seed(ps, ms)
    record = generator.retrieval.get_record(pid)
    generator.build_and_store(record)

    stored = ms.get_embedding(pid, EMBEDDING_KIND)
    assert stored is not None and stored.vector is not None
    assert stored.dim == VECTOR_DIM
    assert stored.model_name == f"{EMBEDDING_VERSION}/{FEATURE_VERSION}"   # versions packed
    assert math.isclose(math.sqrt(sum(v * v for v in stored.vector)), 1.0, abs_tol=1e-6)


def test_build_and_store_is_idempotent(generator, stores):
    ps, ms = stores
    pid = _seed(ps, ms)
    record = generator.retrieval.get_record(pid)
    first = generator.build_and_store(record)
    second = generator.build_and_store(record)             # already populated → skip
    assert first is not None and second is None
    assert ms._fetchone("SELECT COUNT(*) AS n FROM memory_embeddings WHERE prediction_id=?", (pid,))["n"] == 1


def test_rebuild_overwrites(generator, stores):
    ps, ms = stores
    pid = _seed(ps, ms)
    generator.build_and_store(generator.retrieval.get_record(pid))
    emb = generator.rebuild_embedding(pid)
    assert emb.prediction_id == pid
    assert ms._fetchone("SELECT COUNT(*) AS n FROM memory_embeddings WHERE prediction_id=?", (pid,))["n"] == 1


def test_generate_from_real_memory_record(generator, stores):
    ps, ms = stores
    pid = _seed(ps, ms)
    emb = generator.generate_embedding(generator.retrieval.get_record(pid))
    assert emb.prediction_id == pid and emb.dimension == VECTOR_DIM


# --------------------------------------------------------------- backfill
def test_backfill_embeds_all_built(generator, stores):
    ps, ms = stores
    pids = [_seed(ps, ms, i=i, symbol=f"S{i}.NS") for i in range(5)]
    summary = generator.backfill_embeddings()
    assert summary.scanned == 5 and summary.embedded == 5 and summary.skipped == 0 and summary.failed == 0
    for pid in pids:
        assert ms.get_embedding(pid, EMBEDDING_KIND).vector is not None


def test_backfill_is_idempotent(generator, stores):
    ps, ms = stores
    for i in range(4):
        _seed(ps, ms, i=i, symbol=f"S{i}.NS")
    first = generator.backfill_embeddings()
    second = generator.backfill_embeddings()
    assert first.embedded == 4
    assert second.embedded == 0 and second.skipped == 4   # nothing re-embedded


def test_backfill_skips_unbuilt(generator, stores):
    ps, ms = stores
    _seed(ps, ms, i=0, symbol="A.NS", build=True)
    _seed(ps, ms, i=1, symbol="B.NS", build=False)   # resolved but no memory built
    summary = generator.backfill_embeddings()
    assert summary.scanned == 1 and summary.embedded == 1   # only the built one is a candidate


def test_empty_backfill(generator):
    summary = generator.backfill_embeddings()
    assert summary == type(summary)(scanned=0, embedded=0, skipped=0, failed=0)


# --------------------------------------------------------------- concurrency / rollback
def test_concurrent_build_and_store(stores):
    ps, ms = stores
    pids = [_seed(ps, ms, i=i, symbol=f"S{i}.NS") for i in range(6)]
    gen = EmbeddingGenerator(RetrievalEngine(ps, ms), ms)
    records = {pid: gen.retrieval.get_record(pid) for pid in pids}
    errors: list[Exception] = []

    def work(pid: str) -> None:
        try:
            gen.build_and_store(records[pid], overwrite=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(pid,)) for pid in pids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    for pid in pids:
        assert ms.get_embedding(pid, EMBEDDING_KIND).vector is not None
    # one embedding row per prediction — no duplicates under concurrency.
    assert ms._fetchone("SELECT COUNT(*) AS n FROM memory_embeddings", ())["n"] == len(pids)


def test_store_failure_rolls_back(generator, stores, monkeypatch):
    from app.memory.errors import MemoryStoreError
    ps, ms = stores
    pid = _seed(ps, ms)
    record = generator.retrieval.get_record(pid)

    def boom(_embedding):
        raise MemoryStoreError("boom")

    monkeypatch.setattr(ms, "upsert_embedding", boom)
    with pytest.raises(MemoryStoreError):
        generator.build_and_store(record, overwrite=True)
    # the placeholder (NULL vector) remains; no populated embedding was written.
    assert ms.get_embedding(pid, EMBEDDING_KIND).vector is None


# --------------------------------------------------------------- isolation
def test_embedding_writes_only_memory_embeddings(generator, stores):
    ps, ms = stores
    pid = _seed(ps, ms)
    pred_before = dict(ms._conn.execute("SELECT * FROM predictions WHERE prediction_id=?", (pid,)).fetchone())
    reasoning_before = ms._conn.execute("SELECT COUNT(*) AS n FROM memory_reasoning").fetchone()["n"]
    agg_before = ms._conn.execute("SELECT COUNT(*) AS n FROM memory_aggregates").fetchone()["n"]

    generator.build_and_store(generator.retrieval.get_record(pid), overwrite=True)

    assert dict(ms._conn.execute("SELECT * FROM predictions WHERE prediction_id=?", (pid,)).fetchone()) == pred_before
    assert ms._conn.execute("SELECT COUNT(*) AS n FROM memory_reasoning").fetchone()["n"] == reasoning_before
    assert ms._conn.execute("SELECT COUNT(*) AS n FROM memory_aggregates").fetchone()["n"] == agg_before


def test_embedding_module_does_not_import_engines():
    import ast

    import app.similarity.embedding as emb
    with open(emb.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
