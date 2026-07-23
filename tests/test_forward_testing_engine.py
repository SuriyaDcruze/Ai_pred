"""Tests for the Forward Testing Engine (Sprint 1 · Milestone 3).

Covers the resolver (target / stop / expiry / pessimistic tie / still-open), the engine
(record + monitor pass), the prediction lifecycle, restart recovery, and the async
background monitor. All persistence uses temporary databases; the Prediction and Outcome
engines are never imported or invoked.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.data.schemas import Candle
from app.forward_testing.engine import ForwardTestingEngine
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.monitor import ForwardTestingMonitor
from app.forward_testing.resolver import resolve_prediction
from app.forward_testing.store import PredictionStore

_ORIGIN = 1_700_000_000  # created_candle_ts used across the tests


@pytest.fixture
def store(tmp_path) -> PredictionStore:
    s = PredictionStore(path=str(tmp_path / "prediction_history.db"))
    yield s
    s.close()


@pytest.fixture
def engine(store) -> ForwardTestingEngine:
    return ForwardTestingEngine(store, max_hold_bars=10)


def _candle(offset_bars: int, high: float, low: float, close: float | None = None) -> Candle:
    """A candle ``offset_bars`` hours after the origin (open_time strictly after origin)."""
    ts = datetime.fromtimestamp(_ORIGIN, tz=timezone.utc) + timedelta(hours=offset_bars)
    px = close if close is not None else (high + low) / 2
    return Candle(open_time=ts, open=px, high=high, low=low, close=px, volume=1.0, closed=True)


def _record(**overrides) -> PredictionRecord:
    base = dict(
        symbol="RELIANCE.NS", exchange="yahoo", timeframe="1h",
        current_price=100.0, direction="BUY", recommendation="BUY",
        created_candle_ts=_ORIGIN,
        entry=100.0, stop=98.0, target1=103.0,     # risk 2.0 → target = +1.5R
        status=PredictionStatus.ACTIVE,
    )
    base.update(overrides)
    return PredictionRecord(**base)


# --------------------------------------------------------------------------- #
# Resolver
# --------------------------------------------------------------------------- #

def test_resolver_detects_target_first():
    record = _record()
    candles = [_candle(1, high=101, low=99), _candle(2, high=104, low=100)]  # bar 2 hits 103
    outcome = resolve_prediction(record, candles, max_hold_bars=10)
    assert outcome.status is PredictionStatus.TARGET_HIT
    assert outcome.realised_r == pytest.approx(1.5)   # (103-100)/2
    assert outcome.holding_bars == 2
    assert outcome.resolved_price == 103.0


def test_resolver_detects_stop_first():
    record = _record()
    candles = [_candle(1, high=101, low=97)]          # low 97 <= stop 98
    outcome = resolve_prediction(record, candles, max_hold_bars=10)
    assert outcome.status is PredictionStatus.STOP_HIT
    assert outcome.realised_r == -1.0
    assert outcome.holding_bars == 1


def test_resolver_is_pessimistic_on_a_same_bar_tie():
    """A bar that spans both stop and target counts as a stop (honest)."""
    record = _record()
    candles = [_candle(1, high=104, low=97)]          # spans 98 and 103
    outcome = resolve_prediction(record, candles, max_hold_bars=10)
    assert outcome.status is PredictionStatus.STOP_HIT


def test_resolver_expires_at_max_holding():
    record = _record()
    # never touches 103 or 98; expires after 3 bars, marked to market at close 101
    candles = [_candle(i, high=101.5, low=99.0, close=101.0) for i in range(1, 4)]
    outcome = resolve_prediction(record, candles, max_hold_bars=3)
    assert outcome.status is PredictionStatus.EXPIRED
    assert outcome.holding_bars == 3
    assert outcome.realised_r == pytest.approx(0.5)   # (101-100)/2


def test_resolver_returns_none_when_still_open():
    record = _record()
    candles = [_candle(1, high=101, low=99.5)]         # nothing hit, under max hold
    assert resolve_prediction(record, candles, max_hold_bars=10) is None


def test_resolver_ignores_bars_up_to_the_origin():
    record = _record()
    # a pre-origin bar that would hit the stop must be ignored
    before = Candle(
        open_time=datetime.fromtimestamp(_ORIGIN - 3600, tz=timezone.utc),
        open=100, high=101, low=90, close=100, volume=1.0, closed=True,
    )
    after = _candle(1, high=104, low=100)
    outcome = resolve_prediction(record, [before, after], max_hold_bars=10)
    assert outcome.status is PredictionStatus.TARGET_HIT


def test_resolver_handles_short_trades():
    record = _record(direction="SELL", entry=100.0, stop=102.0, target1=97.0)  # risk 2
    candles = [_candle(1, high=100, low=96)]          # low 96 <= target 97
    outcome = resolve_prediction(record, candles, max_hold_bars=10)
    assert outcome.status is PredictionStatus.TARGET_HIT
    assert outcome.realised_r == pytest.approx(1.5)


def test_resolver_short_stop():
    record = _record(direction="SELL", entry=100.0, stop=102.0, target1=97.0)
    candles = [_candle(1, high=103, low=99)]          # high 103 >= stop 102
    outcome = resolve_prediction(record, candles, max_hold_bars=10)
    assert outcome.status is PredictionStatus.STOP_HIT


def test_resolver_skips_wait_predictions():
    record = _record(direction="WAIT", recommendation="WAIT")
    assert resolve_prediction(record, [_candle(1, 104, 96)], max_hold_bars=10) is None


def test_resolver_needs_stop_and_target():
    assert resolve_prediction(_record(stop=None), [_candle(1, 104, 96)], max_hold_bars=10) is None
    assert resolve_prediction(_record(target1=None), [_candle(1, 104, 96)], max_hold_bars=10) is None


# --------------------------------------------------------------------------- #
# Engine — record
# --------------------------------------------------------------------------- #

def test_engine_records_an_actionable_recommendation(engine, store):
    rec = engine.record(
        symbol="RELIANCE.NS", exchange="yahoo", timeframe="1h",
        current_price=100.0, direction="BUY", recommendation="BUY",
        created_candle_ts=_ORIGIN, stop=98.0, target1=103.0,
        outcome_prob=0.72, sector="Energy",
    )
    assert rec is not None
    assert rec.status is PredictionStatus.ACTIVE
    assert rec.entry == 100.0                          # entry defaults to current price
    assert store.count() == 1
    assert store.get(rec.prediction_id).sector == "Energy"


def test_engine_skips_wait_recommendations(engine, store):
    assert engine.record(
        symbol="TCS.NS", exchange="yahoo", timeframe="1h",
        current_price=100.0, direction="WAIT", recommendation="WAIT",
        created_candle_ts=_ORIGIN,
    ) is None
    assert store.count() == 0


def test_engine_record_is_duplicate_protected(engine, store):
    kwargs = dict(
        symbol="RELIANCE.NS", exchange="yahoo", timeframe="1h",
        current_price=100.0, direction="BUY", recommendation="BUY",
        created_candle_ts=_ORIGIN, stop=98.0, target1=103.0,
    )
    assert engine.record(**kwargs) is not None
    assert engine.record(**kwargs) is None             # same bar → deduped
    assert store.count() == 1


def test_engine_records_version_stamps(engine, store):
    rec = engine.record(
        symbol="RELIANCE.NS", exchange="yahoo", timeframe="1h",
        current_price=100.0, direction="BUY", recommendation="BUY",
        created_candle_ts=_ORIGIN, stop=98.0, target1=103.0,
        prediction_model_version="pred-v1", outcome_model_version="out-v1",
        feature_version="feat-v1",
    )
    stored = store.get(rec.prediction_id)
    assert stored.prediction_model_version == "pred-v1"
    assert stored.outcome_model_version == "out-v1"
    assert stored.feature_version == "feat-v1"


# --------------------------------------------------------------------------- #
# Engine — monitor pass (integration with the store + resolver)
# --------------------------------------------------------------------------- #

def _fetcher(candles_by_symbol: dict[str, list[Candle]]):
    """Build a fake candle fetcher from a symbol → candles map."""
    def fetch(symbol: str, timeframe: str) -> list[Candle]:
        return candles_by_symbol.get(symbol, [])
    return fetch


def test_monitor_resolves_a_winning_prediction(engine, store):
    rec = engine.record(
        symbol="RELIANCE.NS", exchange="yahoo", timeframe="1h",
        current_price=100.0, direction="BUY", recommendation="BUY",
        created_candle_ts=_ORIGIN, stop=98.0, target1=103.0,
    )
    fetch = _fetcher({"RELIANCE.NS": [_candle(1, high=104, low=100)]})

    summary = engine.monitor_once(fetch)
    assert summary == {"checked": 1, "resolved": 1, "still_open": 0}

    resolved = store.get(rec.prediction_id)
    assert resolved.status is PredictionStatus.TARGET_HIT
    assert resolved.realised_r == pytest.approx(1.5)
    assert resolved.is_terminal()


def test_monitor_leaves_unresolved_predictions_open(engine, store):
    rec = engine.record(
        symbol="RELIANCE.NS", exchange="yahoo", timeframe="1h",
        current_price=100.0, direction="BUY", recommendation="BUY",
        created_candle_ts=_ORIGIN, stop=98.0, target1=103.0,
    )
    fetch = _fetcher({"RELIANCE.NS": [_candle(1, high=101, low=99.5)]})  # nothing hit
    summary = engine.monitor_once(fetch)
    assert summary == {"checked": 1, "resolved": 0, "still_open": 1}
    assert store.get(rec.prediction_id).status is PredictionStatus.ACTIVE


def test_monitor_is_idempotent(engine, store):
    """Running the pass again after resolution changes nothing."""
    rec = engine.record(
        symbol="RELIANCE.NS", exchange="yahoo", timeframe="1h",
        current_price=100.0, direction="BUY", recommendation="BUY",
        created_candle_ts=_ORIGIN, stop=98.0, target1=103.0,
    )
    fetch = _fetcher({"RELIANCE.NS": [_candle(1, high=104, low=100)]})

    assert engine.monitor_once(fetch)["resolved"] == 1
    second = engine.monitor_once(fetch)                # already resolved → nothing open
    assert second == {"checked": 0, "resolved": 0, "still_open": 0}
    assert store.get(rec.prediction_id).realised_r == pytest.approx(1.5)


def test_monitor_survives_a_failing_fetch(engine, store):
    engine.record(
        symbol="RELIANCE.NS", exchange="yahoo", timeframe="1h",
        current_price=100.0, direction="BUY", recommendation="BUY",
        created_candle_ts=_ORIGIN, stop=98.0, target1=103.0,
    )

    def boom(symbol: str, timeframe: str):
        raise RuntimeError("provider down")

    summary = engine.monitor_once(boom)                # must not raise
    assert summary["resolved"] == 0                    # left open, retried next pass


def test_monitor_handles_multiple_predictions(engine, store):
    engine.record(symbol="A", exchange="x", timeframe="1h", current_price=100.0,
                  direction="BUY", recommendation="BUY", created_candle_ts=_ORIGIN,
                  stop=98.0, target1=103.0)
    engine.record(symbol="B", exchange="x", timeframe="1h", current_price=100.0,
                  direction="BUY", recommendation="BUY", created_candle_ts=_ORIGIN,
                  stop=98.0, target1=103.0)
    fetch = _fetcher({
        "A": [_candle(1, high=104, low=100)],          # A wins
        "B": [_candle(1, high=101, low=97)],           # B stops out
    })
    summary = engine.monitor_once(fetch)
    assert summary == {"checked": 2, "resolved": 2, "still_open": 0}


# --------------------------------------------------------------------------- #
# Prediction lifecycle
# --------------------------------------------------------------------------- #

def test_full_lifecycle_active_to_target_to_completed_stats(engine, store):
    rec = engine.record(
        symbol="RELIANCE.NS", exchange="yahoo", timeframe="1h",
        current_price=100.0, direction="BUY", recommendation="BUY",
        created_candle_ts=_ORIGIN, stop=98.0, target1=103.0,
    )
    assert store.get(rec.prediction_id).status is PredictionStatus.ACTIVE   # opened active
    engine.monitor_once(_fetcher({"RELIANCE.NS": [_candle(1, high=104, low=100)]}))

    final = store.get(rec.prediction_id)
    assert final.status is PredictionStatus.TARGET_HIT
    stats = store.statistics()
    assert stats["resolved"] == 1
    assert stats["wins"] == 1
    assert stats["total_r"] == pytest.approx(1.5)


# --------------------------------------------------------------------------- #
# Restart recovery
# --------------------------------------------------------------------------- #

def test_engine_recovers_open_predictions_after_restart(tmp_path):
    path = str(tmp_path / "prediction_history.db")

    first_store = PredictionStore(path=path)
    ForwardTestingEngine(first_store).record(
        symbol="RELIANCE.NS", exchange="yahoo", timeframe="1h",
        current_price=100.0, direction="BUY", recommendation="BUY",
        created_candle_ts=_ORIGIN, stop=98.0, target1=103.0,
    )
    first_store.close()                                # simulate shutdown

    # a fresh engine over a fresh store resolves the prediction recorded before the restart
    second_store = PredictionStore(path=path)
    second_engine = ForwardTestingEngine(second_store)
    summary = second_engine.monitor_once(
        _fetcher({"RELIANCE.NS": [_candle(1, high=104, low=100)]})
    )
    assert summary["resolved"] == 1
    assert second_store.statistics()["wins"] == 1
    second_store.close()


# --------------------------------------------------------------------------- #
# Background monitor (async)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_monitor_run_pass_resolves(engine, store):
    engine.record(
        symbol="RELIANCE.NS", exchange="yahoo", timeframe="1h",
        current_price=100.0, direction="BUY", recommendation="BUY",
        created_candle_ts=_ORIGIN, stop=98.0, target1=103.0,
    )
    monitor = ForwardTestingMonitor(
        engine, _fetcher({"RELIANCE.NS": [_candle(1, high=104, low=100)]}), interval_secs=0.01
    )
    summary = await monitor.run_pass()
    assert summary["resolved"] == 1


@pytest.mark.asyncio
async def test_monitor_start_and_stop(engine, store):
    engine.record(
        symbol="RELIANCE.NS", exchange="yahoo", timeframe="1h",
        current_price=100.0, direction="BUY", recommendation="BUY",
        created_candle_ts=_ORIGIN, stop=98.0, target1=103.0,
    )
    monitor = ForwardTestingMonitor(
        engine, _fetcher({"RELIANCE.NS": [_candle(1, high=104, low=100)]}), interval_secs=0.01
    )
    monitor.start()
    assert monitor.running is True
    for _ in range(50):                                 # let a pass run
        if store.statistics()["resolved"] == 1:
            break
        await _sleep()
    await monitor.stop()
    assert monitor.running is False
    assert store.statistics()["resolved"] == 1


async def _sleep():
    import asyncio
    await asyncio.sleep(0.01)
