"""Tests for news sentiment scoring and the Yahoo (stock) adapter.

The scoring tests are fully offline/deterministic — no network needed.
"""

import pytest

from app.features.sentiment import _label, _news_symbol, score_text
from app.stream.yahoo import YahooClient


# --------------------------- sentiment scoring --------------------------- #


def test_bullish_headline_scores_positive():
    s = score_text("Bitcoin surges to record high on strong ETF inflows")
    assert s > 0.5


def test_bearish_headline_scores_negative():
    s = score_text("Apple stock plunges after weak earnings and profit warning")
    assert s < -0.5


def test_neutral_headline_scores_zero():
    assert score_text("Company announces annual shareholder meeting date") == 0.0


def test_negation_flips_the_sign():
    # "not strong" should not read as bullish
    assert score_text("Demand is not strong") < 0
    assert score_text("Demand is strong") > 0


def test_score_is_bounded():
    extreme = "crash plunge collapse fraud hack slump tumble selloff"
    assert -1.0 <= score_text(extreme) <= 1.0
    assert score_text(extreme) == -1.0


def test_labels_match_score_bands():
    assert _label(0.5) == "Bullish"
    assert _label(0.2) == "Slightly Bullish"
    assert _label(0.0) == "Neutral"
    assert _label(-0.2) == "Slightly Bearish"
    assert _label(-0.5) == "Bearish"


@pytest.mark.parametrize(
    "app_symbol,news_ticker",
    [("BTCUSDT", "BTC-USD"), ("ETHUSDT", "ETH-USD"), ("AAPL", "AAPL"), ("ITC.NS", "ITC.NS")],
)
def test_symbol_maps_to_news_ticker(app_symbol, news_ticker):
    assert _news_symbol(app_symbol) == news_ticker


# ------------------------- stock/crypto routing -------------------------- #


@pytest.mark.parametrize("symbol", ["AAPL", "ITC.NS", "TSLA", "RELIANCE.NS"])
def test_stocks_are_detected(symbol):
    assert YahooClient.is_stock(symbol) is True


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
def test_crypto_is_not_a_stock(symbol):
    assert YahooClient.is_stock(symbol) is False
