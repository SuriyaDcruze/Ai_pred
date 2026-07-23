"""Read-only aggregation helpers for the Forward Testing dashboard (Sprint 1 · M5).

Pure functions over already-fetched :class:`PredictionRecord` objects. **No database
access, no model logic, no engine imports.** They exist so the dashboard's grouped and
live-vs-backtest views are computed **server-side** — keeping the dashboard a pure
presentation layer (its rule: no business logic in the browser, never query the DB
directly) — while reusing the Prediction Store's *read* methods for the data itself.

The R-based aggregation mirrors ``PredictionStore.statistics`` semantics so the numbers a
user sees in a breakdown are consistent with the overall stats.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from app.forward_testing.models import PredictionRecord

#: Dimension name → the record attribute it groups on.
_DIMENSIONS: dict[str, str] = {
    "market": "exchange",
    "sector": "sector",
    "timeframe": "timeframe",
    "regime": "market_regime",
    "confidence": "__confidence_bucket__",  # derived, see confidence_bucket()
}

#: Below this many resolved trades, a win rate is not a claim (mirrors the API summary).
MIN_MEANINGFUL_SAMPLE = 50
#: Below this, we do not even attempt a significance read.
MIN_SIGNIFICANCE_SAMPLE = 30


def available_dimensions() -> list[str]:
    """The dimensions the breakdown endpoint understands."""
    return list(_DIMENSIONS)


def confidence_bucket(record: PredictionRecord) -> str | None:
    """Bucket a record by its decision-time confidence (outcome prob, else score).

    Returns a label like ``"0.60–0.70"`` or ``None`` when no probability was stored.
    """
    p = record.outcome_prob if record.outcome_prob is not None else record.decision_score
    if p is None:
        return None
    p = max(0.0, min(1.0, float(p)))
    lo = math.floor(p * 10) / 10
    if lo >= 1.0:  # p == 1.0 lands in the top bucket
        lo = 0.9
    return f"{lo:.2f}–{lo + 0.1:.2f}"


def _bucket_value(record: PredictionRecord, attr: str) -> str | None:
    """The grouping key for one record on one dimension."""
    if attr == "__confidence_bucket__":
        return confidence_bucket(record)
    value = getattr(record, attr, None)
    if value is None or value == "":
        return None
    return str(value)


def _max_drawdown(r_values: list[float]) -> float:
    """Largest peak-to-trough decline of the cumulative R curve (positive)."""
    equity = peak = max_dd = 0.0
    for r in r_values:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def aggregate(records: Iterable[PredictionRecord]) -> dict[str, Any]:
    """Aggregate a set of records by realised R (resolved records only).

    Returns counts, win rate, average/expectancy R, average win/loss R, total R, profit
    factor, max drawdown (R) and average holding bars. Position-size agnostic. Unresolved
    records are ignored. All figures are ``None``/0 when nothing has resolved.
    """
    resolved = [r for r in records if r.realised_r is not None]
    stats: dict[str, Any] = {
        "resolved": len(resolved),
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "avg_r": None,
        "expectancy": None,      # per-trade expected R (== avg_r by definition)
        "avg_win_r": None,
        "avg_loss_r": None,
        "total_r": 0.0,
        "profit_factor": None,
        "max_drawdown_r": 0.0,
        "avg_holding_bars": None,
    }
    if not resolved:
        return stats

    r_values = [float(r.realised_r) for r in resolved]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    stats["wins"] = len(wins)
    stats["losses"] = len(losses)
    stats["win_rate"] = len(wins) / len(r_values)
    stats["avg_r"] = sum(r_values) / len(r_values)
    stats["expectancy"] = stats["avg_r"]  # mean realised R per trade *is* the expectancy
    stats["avg_win_r"] = (gross_profit / len(wins)) if wins else None
    stats["avg_loss_r"] = (-gross_loss / len(losses)) if losses else None
    stats["total_r"] = sum(r_values)
    stats["profit_factor"] = (
        gross_profit / gross_loss if gross_loss > 0 else (None if not wins else float("inf"))
    )
    stats["max_drawdown_r"] = _max_drawdown(r_values)

    holdings = [r.holding_bars for r in resolved if r.holding_bars is not None]
    if holdings:
        stats["avg_holding_bars"] = sum(holdings) / len(holdings)
    return stats


def group_and_aggregate(
    records: Iterable[PredictionRecord], dimension: str
) -> list[dict[str, Any]]:
    """Group records by a dimension and aggregate each group.

    Args:
        records: resolved (and/or open) predictions.
        dimension: one of :func:`available_dimensions`.

    Returns:
        One row per bucket ``{"bucket": <label>, "stats": {...}}`` sorted by resolved
        count (desc). Records with no value for the dimension are grouped under
        ``"unknown"`` so nothing is silently dropped.

    Raises:
        ValueError: if the dimension is not recognised.
    """
    if dimension not in _DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}; use one of {available_dimensions()}")

    attr = _DIMENSIONS[dimension]
    groups: dict[str, list[PredictionRecord]] = {}
    for record in records:
        key = _bucket_value(record, attr) or "unknown"
        groups.setdefault(key, []).append(record)

    rows = [{"bucket": bucket, "stats": aggregate(recs)} for bucket, recs in groups.items()]
    rows.sort(key=lambda row: row["stats"]["resolved"], reverse=True)
    return rows


def live_vs_backtest(
    live_win_rate: float | None,
    resolved: int,
    *,
    backtest_win_rate: float,
) -> dict[str, Any]:
    """Compare the live win rate against the backtest baseline — honestly.

    Reports the difference, sample size, a 95% confidence interval on the live win rate,
    and a status that refuses to over-claim:

    * ``no_data`` — nothing resolved live.
    * ``building_sample`` — too few trades to say anything (``< MIN_SIGNIFICANCE_SAMPLE``).
    * ``statistically_significant`` — enough trades and the 95% CI excludes a coin flip.
    * ``inconclusive`` — enough trades but the CI still spans a coin flip.

    ``backtest_win_rate`` below zero means "no baseline configured": the comparison fields
    are returned as ``None`` and the status still reflects the live sample size.
    """
    baseline_configured = backtest_win_rate is not None and backtest_win_rate >= 0
    result: dict[str, Any] = {
        "live_win_rate": live_win_rate,
        "backtest_win_rate": backtest_win_rate if baseline_configured else None,
        "difference": None,
        "sample_size": resolved,
        "ci_low": None,
        "ci_high": None,
        "status": "no_data",
        "baseline_configured": baseline_configured,
    }

    if resolved == 0 or live_win_rate is None:
        return result

    # 95% normal-approximation confidence interval on the live win rate.
    se = math.sqrt(max(live_win_rate * (1 - live_win_rate), 0.0) / resolved)
    ci_low = max(0.0, live_win_rate - 1.96 * se)
    ci_high = min(1.0, live_win_rate + 1.96 * se)
    result["ci_low"] = ci_low
    result["ci_high"] = ci_high

    if baseline_configured:
        result["difference"] = live_win_rate - backtest_win_rate

    if resolved < MIN_SIGNIFICANCE_SAMPLE:
        result["status"] = "building_sample"
    elif ci_low > 0.5 or ci_high < 0.5:
        result["status"] = "statistically_significant"
    else:
        result["status"] = "inconclusive"
    return result
