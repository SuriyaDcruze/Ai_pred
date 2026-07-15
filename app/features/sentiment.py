"""News + sentiment analysis — a genuinely *new* information source.

Everything else the model sees (RSI, MACD, candlestick patterns) is derived from
price itself, which is why it can't see beyond price. News is different: it's the
one input that can move the market *before* the chart reacts.

This module pulls free financial headlines (Yahoo Finance RSS — no API key, no
account) and scores them with a finance-tuned lexicon. Dependency-free: no NLTK,
no transformer, no extra install.

Honest note: sentiment is a *candidate* edge, not a guaranteed one. It's worth
testing precisely because it's information the price-only features cannot contain.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.utils.logging import get_logger

logger = get_logger(__name__)

_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline"

# Finance-tuned lexicon. Weights are deliberately simple and auditable — you can
# read exactly why a headline scored the way it did.
_POSITIVE: dict[str, float] = {
    "surge": 2.0, "surges": 2.0, "soar": 2.0, "soars": 2.0, "rally": 2.0, "rallies": 2.0,
    "jump": 1.5, "jumps": 1.5, "gain": 1.2, "gains": 1.2, "rise": 1.2, "rises": 1.2,
    "climb": 1.2, "climbs": 1.2, "up": 0.6, "higher": 1.0, "beat": 1.5, "beats": 1.5,
    "record": 1.3, "high": 0.8, "bullish": 2.0, "upgrade": 1.8, "upgraded": 1.8,
    "outperform": 1.5, "buy": 1.0, "strong": 1.0, "growth": 1.0, "profit": 1.2,
    "boost": 1.3, "boosts": 1.3, "approve": 1.2, "approved": 1.2, "adoption": 1.2,
    "breakout": 1.5, "recover": 1.2, "recovery": 1.2, "optimism": 1.5, "inflow": 1.3,
    "inflows": 1.3, "accumulate": 1.2, "milestone": 1.0, "partnership": 1.0,
}
_NEGATIVE: dict[str, float] = {
    "plunge": 2.0, "plunges": 2.0, "crash": 2.5, "crashes": 2.5, "slump": 1.8,
    "slumps": 1.8, "tumble": 1.8, "tumbles": 1.8, "drop": 1.3, "drops": 1.3,
    "fall": 1.3, "falls": 1.3, "sink": 1.5, "sinks": 1.5, "down": 0.6, "lower": 1.0,
    "loss": 1.3, "losses": 1.3, "miss": 1.5, "misses": 1.5, "bearish": 2.0,
    "downgrade": 1.8, "downgraded": 1.8, "underperform": 1.5, "sell": 1.0,
    "sell-off": 2.0, "selloff": 2.0, "weak": 1.2, "warn": 1.5, "warns": 1.5,
    "warning": 1.5, "fear": 1.5, "fears": 1.5, "risk": 0.8, "concern": 1.0,
    "concerns": 1.0, "lawsuit": 1.5, "ban": 1.8, "bans": 1.8, "hack": 2.2,
    "hacked": 2.2, "fraud": 2.2, "probe": 1.3, "crackdown": 1.8, "outflow": 1.3,
    "outflows": 1.3, "liquidation": 1.8, "correction": 1.2, "slide": 1.4,
    "slides": 1.4, "halt": 1.3, "halted": 1.3, "delay": 1.0, "reject": 1.5,
    "rejected": 1.5, "collapse": 2.4,
}
_NEGATORS = {"not", "no", "never", "without", "despite", "unlikely"}


@dataclass
class Headline:
    title: str
    link: str
    published: str
    score: float          # -1 (very bearish) .. +1 (very bullish)


@dataclass
class NewsSentiment:
    symbol: str
    score: float          # aggregate, -1 .. +1
    label: str            # Bullish / Slightly Bullish / Neutral / ...
    headlines: list[Headline]

    @property
    def mood_emoji(self) -> str:
        if self.score >= 0.35:
            return "🟢"
        if self.score <= -0.35:
            return "🔴"
        return "⚪"


def score_text(text: str) -> float:
    """Score one headline in roughly [-1, 1]. Handles simple negation."""
    words = re.findall(r"[a-z\-]+", text.lower())
    total = 0.0
    for i, w in enumerate(words):
        weight = _POSITIVE.get(w, 0.0) - _NEGATIVE.get(w, 0.0)
        if weight == 0.0:
            continue
        # "not strong" -> flip the sign
        if i > 0 and words[i - 1] in _NEGATORS:
            weight = -weight
        total += weight
    if total == 0.0:
        return 0.0
    # squash into [-1, 1] — 3 points of evidence is already a strong headline
    return max(-1.0, min(1.0, total / 3.0))


def _label(score: float) -> str:
    if score >= 0.35:
        return "Bullish"
    if score >= 0.12:
        return "Slightly Bullish"
    if score <= -0.35:
        return "Bearish"
    if score <= -0.12:
        return "Slightly Bearish"
    return "Neutral"


def _news_symbol(symbol: str) -> str:
    """Map an app symbol to the ticker Yahoo's news feed understands."""
    s = symbol.upper()
    if s.endswith("USDT"):          # BTCUSDT -> BTC-USD
        return f"{s[:-4]}-USD"
    if s.endswith("USD"):
        return f"{s[:-3]}-USD"
    return s                         # stocks pass through (AAPL, ITC.NS)


async def fetch_news(symbol: str, limit: int = 8, timeout: float = 8.0) -> NewsSentiment:
    """Fetch recent headlines for ``symbol`` and score their sentiment.

    Never raises — on any network/parse failure it returns a neutral, empty result
    so the dashboard and decision engine keep working.
    """
    ticker = _news_symbol(symbol)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                _RSS,
                params={"s": ticker, "region": "US", "lang": "en-US"},
                headers={"User-Agent": "Mozilla/5.0 (compatible; AegisBot/1.0)"},
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
    except Exception as exc:  # noqa: BLE001 - news is best-effort, never fatal
        logger.warning("news fetch failed for %s: %s", ticker, exc)
        return NewsSentiment(symbol=symbol.upper(), score=0.0, label="Neutral", headlines=[])

    heads: list[Headline] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        heads.append(
            Headline(
                title=title,
                link=(item.findtext("link") or "").strip(),
                published=(item.findtext("pubDate") or "").strip(),
                score=round(score_text(title), 3),
            )
        )
        if len(heads) >= limit:
            break

    # Recent headlines matter more — weight by position (newest first in the feed).
    if heads:
        weights = [1.0 / (1 + 0.25 * i) for i in range(len(heads))]
        agg = sum(h.score * w for h, w in zip(heads, weights)) / sum(weights)
    else:
        agg = 0.0
    agg = round(max(-1.0, min(1.0, agg)), 3)

    return NewsSentiment(symbol=symbol.upper(), score=agg, label=_label(agg), headlines=heads)
