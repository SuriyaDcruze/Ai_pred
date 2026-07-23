"""Unit tests for the Prediction Store (Sprint 1 · Milestone 2).

Covers create / read / update / queries / statistics, plus the four guarantees the
background monitor will rely on: duplicate protection, audit-friendly (immutable)
writes, idempotent updates, and restart safety.

All tests use temporary databases; production data is never touched.
"""

from __future__ import annotations

import pytest

from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore


@pytest.fixture
def store(tmp_path) -> PredictionStore:
    """A store backed by a throwaway database."""
    s = PredictionStore(path=str(tmp_path / "prediction_history.db"))
    yield s
    s.close()


def _record(**overrides) -> PredictionRecord:
    base = dict(
        symbol="RELIANCE.NS",
        exchange="yahoo",
        timeframe="1d",
        current_price=1296.60,
        direction="BUY",
        recommendation="BUY",
        created_candle_ts=1_700_000_000,
        direction_prob=0.62,
        outcome_prob=0.71,
        entry=1296.60,
        stop=1270.00,
        target1=1336.50,
        sector="Energy",
        context={"note": "sector strong"},
        feature_version="features-45@0f1e2d3c",
    )
    base.update(overrides)
    return PredictionRecord(**base)


def _resolve(store: PredictionStore, record: PredictionRecord, r: float, bars: int = 5,
             status: PredictionStatus | None = None) -> bool:
    """Helper: close a prediction with a realised R."""
    if status is None:
        status = PredictionStatus.TARGET_HIT if r > 0 else PredictionStatus.STOP_HIT
    return store.update_resolution(
        record.prediction_id,
        status=status,
        resolved_price=record.current_price * (1 + r / 100),
        resolution_reason="target hit" if r > 0 else "stop hit",
        realised_r=r,
        holding_bars=bars,
    )


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #

def test_create_returns_the_stored_record(store):
    record = _record()
    assert store.create(record) is record
    assert store.count() == 1


def test_created_record_is_readable_with_all_fields(store):
    original = _record()
    store.create(original)

    fetched = store.get(original.prediction_id)
    assert fetched is not None
    assert fetched.symbol == original.symbol
    assert fetched.direction == original.direction
    assert fetched.entry == original.entry
    assert fetched.sector == "Energy"
    assert fetched.context == {"note": "sector strong"}
    assert fetched.feature_version == original.feature_version
    assert fetched.status is PredictionStatus.PENDING


def test_create_is_duplicate_protected(store):
    """Same symbol/timeframe/candle/source records once — the monitor may retry safely."""
    assert store.create(_record()) is not None
    assert store.create(_record()) is None          # duplicate → None, not an exception
    assert store.count() == 1


def test_duplicate_protection_allows_a_different_candle(store):
    store.create(_record())
    assert store.create(_record(created_candle_ts=1_700_086_400)) is not None
    assert store.count() == 2


def test_duplicate_protection_allows_a_different_source(store):
    store.create(_record())
    assert store.create(_record(source="manual")) is not None
    assert store.count() == 2


def test_duplicate_protection_allows_a_different_symbol(store):
    store.create(_record())
    assert store.create(_record(symbol="TCS.NS")) is not None
    assert store.count() == 2


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #

def test_get_unknown_id_returns_none(store):
    assert store.get("does-not-exist") is None


def test_list_active_returns_open_predictions(store):
    store.create(_record())
    store.create(_record(created_candle_ts=2, status=PredictionStatus.ACTIVE))
    assert len(store.list_active()) == 2


def test_list_active_excludes_resolved(store):
    open_one = _record()
    closed = _record(created_candle_ts=2)
    store.create(open_one)
    store.create(closed)
    _resolve(store, closed, r=1.5)

    active = store.list_active()
    assert [r.prediction_id for r in active] == [open_one.prediction_id]


def test_list_completed_returns_only_terminal(store):
    store.create(_record())                       # stays open
    closed = _record(created_candle_ts=2)
    store.create(closed)
    _resolve(store, closed, r=-1.0)

    completed = store.list_completed()
    assert [r.prediction_id for r in completed] == [closed.prediction_id]


def test_list_completed_respects_limit(store):
    for i in range(5):
        rec = _record(created_candle_ts=i)
        store.create(rec)
        _resolve(store, rec, r=1.0)
    assert len(store.list_completed(limit=3)) == 3


def test_queries_can_filter_by_symbol(store):
    store.create(_record(symbol="RELIANCE.NS"))
    store.create(_record(symbol="TCS.NS", created_candle_ts=2))
    assert len(store.list_active(symbol="TCS.NS")) == 1


def test_list_all_returns_everything(store):
    store.create(_record())
    closed = _record(created_candle_ts=2)
    store.create(closed)
    _resolve(store, closed, r=1.5)
    assert len(store.list_all()) == 2


# --------------------------------------------------------------------------- #
# Update — status
# --------------------------------------------------------------------------- #

def test_update_status_moves_an_open_prediction(store):
    record = _record()
    store.create(record)
    assert store.update_status(record.prediction_id, PredictionStatus.ACTIVE) is True
    assert store.get(record.prediction_id).status is PredictionStatus.ACTIVE


def test_update_status_is_idempotent_for_the_same_state(store):
    record = _record()
    store.create(record)
    store.update_status(record.prediction_id, PredictionStatus.ACTIVE)
    assert store.update_status(record.prediction_id, PredictionStatus.ACTIVE) is False


def test_update_status_never_reopens_a_terminal_prediction(store):
    """The core idempotency guarantee: resolved predictions are final."""
    record = _record()
    store.create(record)
    _resolve(store, record, r=1.5)

    assert store.update_status(record.prediction_id, PredictionStatus.ACTIVE) is False
    assert store.get(record.prediction_id).status is PredictionStatus.TARGET_HIT


def test_update_status_on_unknown_id_returns_false(store):
    assert store.update_status("nope", PredictionStatus.ACTIVE) is False


def test_update_bumps_the_updated_at_timestamp(store):
    record = _record()
    store.create(record)
    before = store.get(record.prediction_id).updated_at
    store.update_status(record.prediction_id, PredictionStatus.ACTIVE)
    assert store.get(record.prediction_id).updated_at >= before


# --------------------------------------------------------------------------- #
# Update — resolution
# --------------------------------------------------------------------------- #

def test_update_resolution_closes_the_prediction(store):
    record = _record()
    store.create(record)
    assert _resolve(store, record, r=1.5, bars=7) is True

    resolved = store.get(record.prediction_id)
    assert resolved.status is PredictionStatus.TARGET_HIT
    assert resolved.realised_r == 1.5
    assert resolved.holding_bars == 7
    assert resolved.resolution_reason == "target hit"
    assert resolved.resolved_at is not None
    assert resolved.is_terminal() is True


def test_update_resolution_is_idempotent(store):
    record = _record()
    store.create(record)
    assert _resolve(store, record, r=1.5) is True
    assert _resolve(store, record, r=-1.0) is False          # second call ignored
    assert store.get(record.prediction_id).realised_r == 1.5  # original outcome kept


def test_update_resolution_rejects_a_non_terminal_status(store):
    record = _record()
    store.create(record)
    with pytest.raises(ValueError, match="not a terminal state"):
        store.update_resolution(
            record.prediction_id,
            status=PredictionStatus.ACTIVE,
            resolved_price=1.0,
            resolution_reason="nope",
            realised_r=0.0,
            holding_bars=1,
        )


def test_update_resolution_on_unknown_id_returns_false(store):
    assert store.update_resolution(
        "nope",
        status=PredictionStatus.EXPIRED,
        resolved_price=1.0,
        resolution_reason="expired",
        realised_r=0.0,
        holding_bars=1,
    ) is False


def test_resolution_preserves_the_original_prediction(store):
    """Audit guarantee: what the models said is never rewritten."""
    record = _record()
    store.create(record)
    _resolve(store, record, r=-1.0)

    after = store.get(record.prediction_id)
    assert after.direction == "BUY"
    assert after.entry == 1296.60
    assert after.stop == 1270.00
    assert after.direction_prob == 0.62
    assert after.outcome_prob == 0.71
    assert after.created_candle_ts == 1_700_000_000


def test_immutable_columns_cannot_be_updated(store):
    """Attempting to rewrite history is a programming error, not a silent write."""
    record = _record()
    store.create(record)
    with pytest.raises(ValueError, match="immutable columns"):
        store._write_lifecycle(record.prediction_id, {"entry": 999.0})


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #

def test_statistics_on_an_empty_store(store):
    stats = store.statistics()
    assert stats["total"] == 0
    assert stats["resolved"] == 0
    assert stats["win_rate"] is None
    assert stats["avg_r"] is None
    assert stats["max_drawdown_r"] == 0.0


def test_statistics_counts_open_and_resolved(store):
    store.create(_record())                       # open
    closed = _record(created_candle_ts=2)
    store.create(closed)
    _resolve(store, closed, r=1.5)

    stats = store.statistics()
    assert stats["open"] == 1
    assert stats["resolved"] == 1
    assert stats["open_risk_r"] == 1.0            # one open trade risking 1R


def test_statistics_computes_win_rate_and_average_r(store):
    for i, r in enumerate([1.5, 1.5, -1.0, -1.0]):
        rec = _record(created_candle_ts=i)
        store.create(rec)
        _resolve(store, rec, r=r)

    stats = store.statistics()
    assert stats["wins"] == 2
    assert stats["losses"] == 2
    assert stats["win_rate"] == 0.5
    assert stats["avg_r"] == pytest.approx(0.25)
    assert stats["total_r"] == pytest.approx(1.0)


def test_statistics_computes_profit_factor(store):
    for i, r in enumerate([2.0, 1.0, -1.0]):      # gross profit 3, gross loss 1
        rec = _record(created_candle_ts=i)
        store.create(rec)
        _resolve(store, rec, r=r)
    assert store.statistics()["profit_factor"] == pytest.approx(3.0)


def test_statistics_computes_max_drawdown(store):
    # equity curve: 2.0 → 1.0 → 0.0 → 1.0; peak 2.0, trough 0.0 → max drawdown 2.0R
    for i, r in enumerate([2.0, -1.0, -1.0, 1.0]):
        rec = _record(created_candle_ts=i)
        store.create(rec)
        _resolve(store, rec, r=r)
    assert store.statistics()["max_drawdown_r"] == pytest.approx(2.0)


def test_statistics_computes_average_holding(store):
    for i, bars in enumerate([4, 6]):
        rec = _record(created_candle_ts=i)
        store.create(rec)
        _resolve(store, rec, r=1.0, bars=bars)
    assert store.statistics()["avg_holding_bars"] == pytest.approx(5.0)


def test_statistics_can_filter_by_symbol(store):
    a = _record(symbol="RELIANCE.NS", created_candle_ts=1)
    b = _record(symbol="TCS.NS", created_candle_ts=2)
    store.create(a); store.create(b)
    _resolve(store, a, r=1.5)
    _resolve(store, b, r=-1.0)

    assert store.statistics(symbol="RELIANCE.NS")["win_rate"] == 1.0
    assert store.statistics(symbol="TCS.NS")["win_rate"] == 0.0


def test_count_filters_by_status(store):
    store.create(_record())
    closed = _record(created_candle_ts=2)
    store.create(closed)
    _resolve(store, closed, r=1.0)

    assert store.count() == 2
    assert store.count(PredictionStatus.PENDING) == 1
    assert store.count(PredictionStatus.TARGET_HIT) == 1


# --------------------------------------------------------------------------- #
# Restart safety & transactions
# --------------------------------------------------------------------------- #

def test_data_survives_a_restart(tmp_path):
    """Restart safety: nothing is held in memory — a new store sees the same records."""
    path = str(tmp_path / "prediction_history.db")

    first = PredictionStore(path=path)
    record = _record()
    first.create(record)
    first.update_status(record.prediction_id, PredictionStatus.ACTIVE)
    first.close()

    second = PredictionStore(path=path)            # simulate a process restart
    resumed = second.list_active()
    assert len(resumed) == 1
    assert resumed[0].prediction_id == record.prediction_id
    assert resumed[0].status is PredictionStatus.ACTIVE
    second.close()


def test_resolved_state_survives_a_restart(tmp_path):
    path = str(tmp_path / "prediction_history.db")

    first = PredictionStore(path=path)
    record = _record()
    first.create(record)
    _resolve(first, record, r=1.5, bars=3)
    first.close()

    second = PredictionStore(path=path)
    stats = second.statistics()
    assert stats["resolved"] == 1
    assert stats["total_r"] == pytest.approx(1.5)
    assert second.list_active() == []
    second.close()


def test_failed_create_leaves_no_partial_row(store):
    """Transaction safety: a rejected duplicate must not change the row count."""
    store.create(_record())
    before = store.count()
    store.create(_record())                        # duplicate → rolled back
    assert store.count() == before


def test_store_works_as_a_context_manager(tmp_path):
    path = str(tmp_path / "prediction_history.db")
    with PredictionStore(path=path) as s:
        s.create(_record())
        assert s.count() == 1
