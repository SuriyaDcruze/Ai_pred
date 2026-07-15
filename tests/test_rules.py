"""Tests for **My Rules** — the personal trading checklist.

These are the tests that matter most for this feature: a rule that silently
passes when it should fail is worse than no rule at all, because it launders a
bad trade as an approved one.
"""

import numpy as np
import pandas as pd
import pytest

from app.data.schemas import RiskPlan, Side, Signal
from app.decision.rules import RULES, FAIL, PASS, SKIP, RuleStore, check_rules, trades_today


@pytest.fixture
def store(tmp_path):
    return RuleStore(path=str(tmp_path / "rules.db"))


def _features(rsi=50.0, ema_50=110.0, ema_200=100.0, atr=1.0, close=100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {"rsi": [rsi], "ema_50": [ema_50], "ema_200": [ema_200], "atr": [atr], "close": [close]}
    )


def _signal(side=Side.BUY, confidence=0.75, rr=2.0, patterns=None) -> Signal:
    from datetime import datetime, timezone

    return Signal(
        symbol="BTCUSDT", exchange="binance", timeframe="1h",
        generated_at=datetime.now(timezone.utc),
        market_status="Bullish", decision=side, confidence=confidence,
        probability="High", trend_strength="Strong", expected_holding="4h",
        risk=RiskPlan(
            entry_low=100, entry_high=100, stop_loss=98, take_profit_1=104,
            take_profit_2=108, risk_reward=rr, position_size=0.1, account_risk_pct=1.0,
        ),
        patterns=patterns or [],
    )


def _status(verdict, rule_id):
    return next(r.status for r in verdict.results if r.id == rule_id)


# ------------------------------- the gates ------------------------------- #


def test_good_setup_passes_every_rule(store):
    """Confident, with-trend, healthy R:R, RSI not stretched -> checklist happy."""
    v = check_rules(_signal(), _features(), store, news_score=0.1, trades_today_count=0)
    assert v.failed == 0
    assert v.obeys_rules
    assert "OBEYS" in v.verdict


def test_low_confidence_is_rejected(store):
    v = check_rules(_signal(confidence=0.40), _features(), store)
    assert _status(v, "min_confidence") == FAIL
    assert not v.obeys_rules


def test_buying_in_a_downtrend_is_rejected(store):
    # ema_50 below ema_200 = downtrend; buying it fights the trend
    v = check_rules(_signal(side=Side.BUY), _features(ema_50=90, ema_200=100), store)
    assert _status(v, "with_trend") == FAIL


def test_selling_in_a_downtrend_is_allowed(store):
    v = check_rules(_signal(side=Side.SELL), _features(ema_50=90, ema_200=100), store)
    assert _status(v, "with_trend") == PASS


def test_poor_risk_reward_is_rejected(store):
    v = check_rules(_signal(rr=0.8), _features(), store)
    assert _status(v, "min_rr") == FAIL


def test_buying_an_overbought_market_is_rejected(store):
    v = check_rules(_signal(side=Side.BUY), _features(rsi=82), store)
    assert _status(v, "not_stretched") == FAIL


def test_selling_an_oversold_market_is_rejected(store):
    v = check_rules(_signal(side=Side.SELL, confidence=0.8),
                    _features(rsi=18, ema_50=90, ema_200=100), store)
    assert _status(v, "not_stretched") == FAIL


def test_buying_into_clearly_bearish_news_is_rejected(store):
    v = check_rules(_signal(side=Side.BUY), _features(), store, news_score=-0.8)
    assert _status(v, "news_agrees") == FAIL


def test_mild_news_does_not_block_a_trade(store):
    v = check_rules(_signal(side=Side.BUY), _features(), store, news_score=-0.1)
    assert _status(v, "news_agrees") == PASS


def test_daily_trade_cap_is_enforced(store):
    v = check_rules(_signal(), _features(), store, trades_today_count=3)
    assert _status(v, "max_trades") == FAIL
    v2 = check_rules(_signal(), _features(), store, trades_today_count=2)
    assert _status(v2, "max_trades") == PASS


# ------------------------- the WAIT / off states ------------------------- #


def test_wait_signal_is_not_reported_as_obeying(store):
    """Regression: a WAIT used to read 'This trade OBEYS your rules'. It isn't a trade."""
    v = check_rules(_signal(side=Side.WAIT), _features(), store)
    assert v.has_trade is False
    assert "No trade" in v.verdict
    assert "OBEYS" not in v.verdict


def test_disabled_rule_cannot_fail_you(store):
    store.set("min_confidence", enabled=False)
    v = check_rules(_signal(confidence=0.10), _features(), store)
    assert _status(v, "min_confidence") == SKIP
    assert v.failed == 0


def test_a_broken_rule_never_kills_the_signal(store):
    """Missing indicator columns must degrade to n/a, not raise."""
    v = check_rules(_signal(), pd.DataFrame({"close": [100.0]}), store)
    assert _status(v, "with_trend") == SKIP        # no ema columns
    assert v is not None


def test_nan_indicators_degrade_gracefully(store):
    v = check_rules(_signal(), _features(rsi=np.nan, ema_50=np.nan), store)
    assert _status(v, "with_trend") == SKIP
    assert _status(v, "not_stretched") == SKIP


# ------------------------------- persistence ----------------------------- #


def test_settings_survive_a_restart(tmp_path):
    path = str(tmp_path / "r.db")
    RuleStore(path).set("min_confidence", enabled=False, value=85.0)
    reopened = RuleStore(path)                    # simulate a server restart
    on, val = reopened.settings()["min_confidence"]
    assert on is False and val == 85.0


def test_reset_restores_defaults(store):
    store.set("with_trend", enabled=False)
    store.reset()
    assert store.settings()["with_trend"][0] is True


def test_unknown_rule_is_rejected(store):
    with pytest.raises(KeyError):
        store.set("make_me_rich", enabled=True)


def test_catalogue_exposes_every_rule(store):
    assert {r["id"] for r in store.catalogue()} == {r.id for r in RULES}


# ---------------------------- the daily counter -------------------------- #


def test_only_your_own_trades_count_against_the_daily_cap():
    """The AI's auto-logged picks must not burn through *your* daily limit."""
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    now = datetime.now(timezone.utc)
    calls = [
        SimpleNamespace(source="manual", created_at=now),
        SimpleNamespace(source="manual", created_at=now),
        SimpleNamespace(source="ai", created_at=now),                      # not yours
        SimpleNamespace(source="manual", created_at=now - timedelta(days=2)),  # not today
    ]
    assert trades_today(calls) == 2
