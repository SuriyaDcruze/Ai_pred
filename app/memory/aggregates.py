"""Aggregate rollup computation for Historical Memory (Sprint 2 · Milestone 3).

Pure functions that turn a set of resolved predictions into :class:`MemoryAggregate`
rollups. **No I/O, no engine imports, no inference** — just arithmetic over already-stored
facts. The Memory Builder persists whatever these return; the Memory Store never computes.

All figures are in R-multiples, so they are position-size agnostic and consistent with
``PredictionStore.statistics`` and the Forward Testing dashboard. Two rollups are produced
per (dimension, bucket): one **across all models** (``model_version = ''``) and one **per
prediction-model version**, so a model swap never silently blends two models' performance
into a single number (the reason ``model_version`` is part of the aggregate key).
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable

from app.forward_testing.models import PredictionRecord
from app.memory.models import AggregateDimension, MemoryAggregate


def confidence_bucket(record: PredictionRecord) -> str | None:
    """Bucket a record by decision-time confidence (outcome prob, else score), width 0.1."""
    p = record.outcome_prob if record.outcome_prob is not None else record.decision_score
    if p is None:
        return None
    p = max(0.0, min(1.0, float(p)))
    lo = math.floor(p * 10) / 10
    if lo >= 1.0:
        lo = 0.9
    return f"{lo:.2f}-{lo + 0.1:.2f}"


#: Dimension → the bucket key it groups a record on. ``None``/empty keys are skipped.
DIMENSION_KEYS: dict[AggregateDimension, Callable[[PredictionRecord], str | None]] = {
    AggregateDimension.OVERALL: lambda r: "all",
    AggregateDimension.SYMBOL: lambda r: r.symbol,
    AggregateDimension.SECTOR: lambda r: r.sector,
    AggregateDimension.REGIME: lambda r: r.market_regime,
    AggregateDimension.TIMEFRAME: lambda r: r.timeframe,
    AggregateDimension.CONFIDENCE_BUCKET: confidence_bucket,
}


def _max_drawdown(records: list[PredictionRecord]) -> float:
    """Largest peak-to-trough decline of the cumulative-R curve (records ordered in time)."""
    ordered = sorted(records, key=lambda r: (r.created_at or "", r.prediction_id))
    equity = peak = max_dd = 0.0
    for r in ordered:
        equity += float(r.realised_r)  # caller guarantees realised_r is not None
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def _metrics(records: list[PredictionRecord]) -> dict[str, Any]:
    """Compute the rollup metrics for a non-empty set of resolved records."""
    r_values = [float(r.realised_r) for r in records]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    avg_r = sum(r_values) / len(r_values)
    holdings = [r.holding_bars for r in records if r.holding_bars is not None]
    return {
        "n_resolved": len(r_values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(r_values),
        "avg_r": avg_r,
        "expectancy": avg_r,  # mean realised R per trade *is* the expectancy
        "total_r": sum(r_values),
        "profit_factor": (
            gross_profit / gross_loss if gross_loss > 0 else (None if not wins else float("inf"))
        ),
        "max_drawdown_r": _max_drawdown(records),
        "avg_holding_bars": (sum(holdings) / len(holdings)) if holdings else None,
    }


def _make(
    dimension: AggregateDimension, bucket: str, model_version: str, records: list[PredictionRecord]
) -> MemoryAggregate:
    return MemoryAggregate(dimension=dimension, bucket=bucket, model_version=model_version, **_metrics(records))


def compute_aggregates(records: Iterable[PredictionRecord]) -> list[MemoryAggregate]:
    """Compute every rollup for a set of predictions.

    Only resolved records (``realised_r is not None``) contribute. For each supported
    dimension, produces one combined rollup per bucket (``model_version = ''``) plus one per
    ``(bucket, prediction_model_version)`` for records that carry a model version.

    Returns:
        A list of :class:`MemoryAggregate`; empty when nothing has resolved.
    """
    resolved = [r for r in records if r.realised_r is not None]
    rows: list[MemoryAggregate] = []
    if not resolved:
        return rows

    for dimension, key_of in DIMENSION_KEYS.items():
        # Combined across all models.
        combined: dict[str, list[PredictionRecord]] = {}
        for record in resolved:
            key = key_of(record)
            if key:
                combined.setdefault(str(key), []).append(record)
        for bucket, recs in combined.items():
            rows.append(_make(dimension, bucket, "", recs))

        # Split per prediction-model version (skip records with no version).
        per_model: dict[tuple[str, str], list[PredictionRecord]] = {}
        for record in resolved:
            key = key_of(record)
            version = record.prediction_model_version
            if key and version:
                per_model.setdefault((str(key), version), []).append(record)
        for (bucket, version), recs in per_model.items():
            rows.append(_make(dimension, bucket, version, recs))

    return rows
