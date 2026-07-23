"""Resolver — score an open prediction against real future price.

Given a prediction and the candles that occurred *after* it was made, the resolver
decides whether the trade hit its **target**, its **stop**, or **expired** at the maximum
holding period — and returns the realised R-multiple and holding period. If none of those
have happened yet, it returns ``None`` (still open) and the monitor tries again later.

The resolution rule mirrors the existing paper-trade resolver
(:func:`app.tracking.tracker.resolve_call`): walk the future candles in order, and if a
single bar spans both the stop and the target, **pessimistically assume the stop hit
first** (intrabar order is unknowable). This keeps forward-test results honest — never
flattering.

Pure and engine-independent: it takes a record and candles, imports nothing from the
Prediction or Outcome engines, and touches no database.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.data.schemas import Candle
from app.forward_testing.models import PredictionRecord, PredictionStatus

_TRADE_DIRECTIONS = frozenset({"BUY", "SELL"})


@dataclass(frozen=True)
class ResolutionOutcome:
    """The settled result of a prediction — the terminal state and its numbers."""

    status: PredictionStatus      # TARGET_HIT | STOP_HIT | EXPIRED
    resolved_price: float
    resolution_reason: str
    realised_r: float
    holding_bars: int


def resolve_prediction(
    record: PredictionRecord,
    candles: list[Candle],
    *,
    max_hold_bars: int,
) -> ResolutionOutcome | None:
    """Resolve one prediction against future candles.

    Args:
        record: The (open) prediction to resolve.
        candles: Candles for the same instrument; only those *after*
            ``record.created_candle_ts`` are considered.
        max_hold_bars: Bars after which an unresolved trade is marked EXPIRED
            (marked to market in R).

    Returns:
        A :class:`ResolutionOutcome` when the trade has hit its target, stop, or expiry;
        otherwise ``None`` (still open). Also ``None`` when the record is not a tradeable
        setup (direction is WAIT) or lacks the levels needed to resolve.
    """
    if record.direction not in _TRADE_DIRECTIONS:
        return None  # WAIT is not a trade — nothing to resolve
    if record.stop is None or record.target1 is None:
        return None  # cannot resolve without a stop and a target

    long = record.direction == "BUY"
    entry = record.entry if record.entry is not None else record.current_price
    stop = record.stop
    target = record.target1
    risk = abs(entry - stop) or 1e-9

    held = 0
    for candle in candles:
        bar_ts = int(candle.open_time.timestamp())
        if bar_ts <= record.created_candle_ts:
            continue  # bars up to and including the origin bar don't count
        held += 1

        hit_stop = candle.low <= stop if long else candle.high >= stop
        hit_target = candle.high >= target if long else candle.low <= target

        if hit_stop:  # pessimistic: stop wins a same-bar tie
            return ResolutionOutcome(
                status=PredictionStatus.STOP_HIT,
                resolved_price=stop,
                resolution_reason="stop hit",
                realised_r=-1.0,
                holding_bars=held,
            )
        if hit_target:
            realised = round(abs(target - entry) / risk, 4)
            return ResolutionOutcome(
                status=PredictionStatus.TARGET_HIT,
                resolved_price=target,
                resolution_reason="target hit",
                realised_r=realised,
                holding_bars=held,
            )
        if held >= max_hold_bars:  # time barrier — mark to market
            move = (candle.close - entry) if long else (entry - candle.close)
            return ResolutionOutcome(
                status=PredictionStatus.EXPIRED,
                resolved_price=candle.close,
                resolution_reason="expired (max holding reached)",
                realised_r=round(move / risk, 4),
                holding_bars=held,
            )

    return None  # neither barrier reached yet — still open
