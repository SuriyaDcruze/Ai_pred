"""Forward Testing Engine — record recommendations and resolve them against reality.

The engine is the orchestrator of continuous forward validation:

* :meth:`record` turns an **approved recommendation** (already produced by the Prediction
  and Outcome engines) into a stored :class:`PredictionRecord`. It records only
  actionable BUY/SELL calls, and relies on the store's duplicate protection so recording
  the same bar twice is harmless.
* :meth:`monitor_once` performs one monitoring pass: it reads the *open* predictions from
  the store, fetches recent candles for each, resolves them, and writes back any that hit
  their target, stop, or expiry.

Two invariants:

* **Independent of the Prediction/Outcome engines.** The engine imports nothing from them.
  It *consumes* recommendation data passed to :meth:`record`; the caller (a later
  milestone) obtains that data from the service layer.
* **Restart-safe.** No open predictions are held in memory. Every :meth:`monitor_once`
  re-reads them from the database, so a process restart resumes exactly where it left off.
"""

from __future__ import annotations

from typing import Any, Callable

from app.data.schemas import Candle
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.resolver import resolve_prediction
from app.forward_testing.store import PredictionStore
from app.utils.logging import get_logger

logger = get_logger(__name__)

#: Fetches candles for one instrument. Injected so the engine stays independent of any
#: particular data provider (and trivially testable with a fake).
CandleFetcher = Callable[[str, str], list[Candle]]

_TRADE_RECOMMENDATIONS = frozenset({"BUY", "SELL"})
_DEFAULT_MAX_HOLD_BARS = 48


class ForwardTestingEngine:
    """Records predictions and resolves them against future market data."""

    def __init__(self, store: PredictionStore, max_hold_bars: int = _DEFAULT_MAX_HOLD_BARS):
        """Create the engine over a :class:`PredictionStore`.

        Args:
            store: The persistence layer (Milestone 2).
            max_hold_bars: Bars after which an unresolved prediction is EXPIRED.
        """
        self.store = store
        self.max_hold_bars = max_hold_bars

    # ------------------------------------------------------------------ record
    def record(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
        current_price: float,
        direction: str,
        recommendation: str,
        created_candle_ts: int,
        entry: float | None = None,
        stop: float | None = None,
        target1: float | None = None,
        target2: float | None = None,
        direction_prob: float | None = None,
        outcome_prob: float | None = None,
        decision_score: float | None = None,
        market_regime: str | None = None,
        market_phase: str | None = None,
        sector: str | None = None,
        session: str | None = None,
        volatility_bucket: str | None = None,
        similarity_score: float | None = None,
        context: dict[str, Any] | None = None,
        prediction_model_version: str | None = None,
        outcome_model_version: str | None = None,
        feature_version: str | None = None,
        source: str = "forward",
    ) -> PredictionRecord | None:
        """Record an approved recommendation for forward testing.

        Only **actionable** recommendations (BUY / SELL) become predictions — a WAIT is
        not a trade and is skipped. A market-entry prediction is created **ACTIVE** (the
        position is live from its origin bar). Duplicate protection is delegated to the
        store.

        Args:
            symbol, exchange, timeframe, current_price: instrument + price at the call.
            direction: the model's directional read (BUY / SELL / WAIT).
            recommendation: the final decision after the gates (BUY / SELL / WAIT).
            created_candle_ts: epoch seconds of the bar the call was based on.
            entry, stop, target1, target2: the risk-defined plan.
            direction_prob, outcome_prob, decision_score: model outputs (stored verbatim).
            market_*, sector, session, volatility_bucket, similarity_score, context:
                explainability context.
            *_version: the three independent version stamps.
            source: 'forward' | 'manual' | 'screener'.

        Returns:
            The stored record, or ``None`` if the recommendation is not actionable or an
            identical prediction already exists (duplicate protection).
        """
        if recommendation not in _TRADE_RECOMMENDATIONS:
            logger.debug("skip non-actionable recommendation %s for %s", recommendation, symbol)
            return None

        record = PredictionRecord(
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            current_price=current_price,
            direction=direction,
            recommendation=recommendation,
            created_candle_ts=created_candle_ts,
            source=source,
            direction_prob=direction_prob,
            outcome_prob=outcome_prob,
            decision_score=decision_score,
            entry=entry if entry is not None else current_price,
            stop=stop,
            target1=target1,
            target2=target2,
            market_regime=market_regime,
            market_phase=market_phase,
            sector=sector,
            session=session,
            volatility_bucket=volatility_bucket,
            similarity_score=similarity_score,
            context=context or {},
            prediction_model_version=prediction_model_version,
            outcome_model_version=outcome_model_version,
            feature_version=feature_version,
            status=PredictionStatus.ACTIVE,  # market entry → live from the origin bar
        )
        stored = self.store.create(record)
        if stored is None:
            return None
        logger.info(
            "recorded forward prediction %s: %s %s (%s)",
            stored.prediction_id, recommendation, symbol, timeframe,
        )
        return stored

    # ----------------------------------------------------------------- monitor
    def monitor_once(self, fetch_candles: CandleFetcher) -> dict[str, int]:
        """Run one monitoring pass over all open predictions.

        Restart-safe: the open set is read fresh from the store, so this is correct even
        immediately after a process restart. Idempotent: resolving an already-terminal
        record is a no-op (guarded by the store).

        Args:
            fetch_candles: ``(symbol, timeframe) -> list[Candle]`` — recent candles for an
                instrument. Injected so the engine stays provider-agnostic.

        Returns:
            ``{"checked": n, "resolved": r, "still_open": n - r}``.
        """
        open_predictions = self.store.list_active()
        checked = 0
        resolved = 0

        for record in open_predictions:
            if record.direction not in _TRADE_RECOMMENDATIONS:
                continue
            checked += 1
            try:
                candles = fetch_candles(record.symbol, record.timeframe)
            except Exception as exc:  # noqa: BLE001 - one bad fetch must not stop the pass
                logger.warning("candle fetch failed for %s: %s", record.symbol, exc)
                continue

            outcome = resolve_prediction(record, candles, max_hold_bars=self.max_hold_bars)
            if outcome is None:
                continue

            updated = self.store.update_resolution(
                record.prediction_id,
                status=outcome.status,
                resolved_price=outcome.resolved_price,
                resolution_reason=outcome.resolution_reason,
                realised_r=outcome.realised_r,
                holding_bars=outcome.holding_bars,
            )
            if updated:
                resolved += 1
                logger.info(
                    "resolved %s: %s (%+.2fR, %d bars)",
                    record.prediction_id, outcome.status.value,
                    outcome.realised_r, outcome.holding_bars,
                )

        return {"checked": checked, "resolved": resolved, "still_open": checked - resolved}
