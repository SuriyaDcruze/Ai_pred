"""Analysis service — orchestrates the full pipeline into a :class:`Signal`.

    OHLCV ─▶ FeatureBuilder ─▶ Predictor ─▶ DecisionEngine ─▶ RiskManager ─▶ Signal

This is the one place the layers are wired together, and the only object the API
needs to hold. It lazily loads a trained model and transparently falls back to
the heuristic predictor when none exists.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.ai.heuristic import HeuristicPredictor
from app.data.schemas import ModelPrediction, RiskPlan, Side, Signal
from app.decision.engine import DecisionEngine
from app.features.engineering import FeatureBuilder
from app.risk.manager import RiskManager
from app.utils.logging import get_logger

logger = get_logger(__name__)


class AnalysisService:
    def __init__(
        self,
        predictor=None,
        decision_engine: DecisionEngine | None = None,
        risk_manager: RiskManager | None = None,
    ):
        self.feature_builder = FeatureBuilder()
        self.decision = decision_engine or DecisionEngine()
        self.risk = risk_manager or RiskManager()
        self._predictor = predictor
        self._using_heuristic = predictor is None

    @property
    def predictor(self):
        """Lazy-load the best available model.

        Order of preference:
          1. The **calibrated logistic model** — it beat the 580K-param deep net on
             honest non-overlapping labels (59% vs 48% directional accuracy) and its
             confidence is calibrated. This is the production model.
          2. The deep net, if the sklearn model isn't present (legacy).
          3. A pure-heuristic fallback so the platform never hard-fails.
        """
        if self._predictor is None:
            try:
                from app.ai.sklearn_model import SklearnPredictor

                self._predictor = SklearnPredictor.load()
                self._using_heuristic = False
                logger.info("AnalysisService using the calibrated logistic model.")
                return self._predictor
            except (FileNotFoundError, Exception) as exc:  # noqa: BLE001
                logger.info("No sklearn model (%s); trying the deep net.", exc)
            try:
                from app.ai.predictor import Predictor

                self._predictor = Predictor.load()
                self._using_heuristic = False
                logger.info("AnalysisService using the deep neural model.")
            except (FileNotFoundError, Exception) as exc:  # noqa: BLE001
                logger.warning("No trained model (%s); using heuristic predictor.", exc)
                self._predictor = HeuristicPredictor()
                self._using_heuristic = True
        return self._predictor

    @property
    def outcome_model(self):
        """The target-before-stop veto layer. Loaded once; None if not trained yet."""
        if not hasattr(self, "_outcome_model"):
            from app.ai.outcome_model import OutcomePredictor

            self._outcome_model = OutcomePredictor.load()
            if self._outcome_model is not None:
                logger.info("AnalysisService loaded the outcome (trade-selection) model.")
        return self._outcome_model

    def assess_outcome(self, ohlcv: pd.DataFrame, prediction: ModelPrediction) -> dict | None:
        """Score the live setup with the outcome model: will target hit before stop?

        Returns None if the model isn't available or the input is too short. Never
        raises — a veto layer must never break the primary signal.
        """
        model = self.outcome_model
        if model is None:
            return None
        try:
            import numpy as np

            feats = self.feature_builder.build_frame(ohlcv)
            cols = [c for c in self.feature_builder.feature_columns if c in feats.columns]
            row = np.nan_to_num(feats.iloc[-1][cols].to_numpy(dtype="float32"))
            probs = [prediction.p_bullish, prediction.p_bearish, prediction.p_sideways]
            return model.assess(row, probs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("outcome assessment failed: %s", exc)
            return None

    def analyze(
        self, ohlcv: pd.DataFrame, symbol: str, exchange: str, timeframe: str
    ) -> Signal:
        if len(ohlcv) < 60:
            raise ValueError("Need at least 60 candles for a reliable analysis.")

        features = self.feature_builder.build_frame(ohlcv)
        prediction: ModelPrediction = self.predictor.predict(ohlcv)
        decision = self.decision.evaluate(features, prediction)

        entry = float(ohlcv["close"].iloc[-1])
        atr = float(features["atr"].iloc[-1]) if "atr" in features else entry * 0.01

        risk_plan: RiskPlan | None = None
        final_side = decision.side
        if decision.side in (Side.BUY, Side.SELL):
            risk_plan = self.risk.build_plan(decision.side, entry, atr)
            if not self.risk.meets_min_rr(risk_plan):
                decision.risks.append(
                    f"R:R {risk_plan.risk_reward if risk_plan else 0} below "
                    f"minimum {self.risk.min_rr}; downgraded to WAIT."
                )
                final_side = Side.WAIT
                risk_plan = None

        probability = self._probability_label(prediction.confidence, final_side)
        recommendation = self._recommendation_text(final_side)
        if self._using_heuristic:
            decision.risks.append("Heuristic mode: neural model not trained — treat as indicative only.")

        # Candlestick patterns spotted on the latest candle (from the book).
        from app.features.candlesticks import detected_patterns

        icon = {"bull": "🟢", "bear": "🔴", "neutral": "⚪"}
        patterns = [
            f"{icon[p['dir']]} {p['name']} — {p['desc']}"
            for p in detected_patterns(features.iloc[-1])
        ]

        return Signal(
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            generated_at=datetime.now(tz=timezone.utc),
            market_status=decision.market_status,
            decision=final_side,
            confidence=round(prediction.confidence, 4),
            probability=probability,
            trend_strength=decision.trend_strength,
            expected_holding=self._holding_estimate(timeframe),
            risk=risk_plan,
            reasons=decision.reasons,
            risks=decision.risks,
            patterns=patterns,
            final_recommendation=recommendation,
        )

    def ai_direction(self, ohlcv: pd.DataFrame) -> Side:
        """The AI's directional pick, DEBIASED against the model's own baseline.

        The raw model can get stuck on one side (e.g. always bearish). Instead of
        absolute P(up) vs P(down), we compare the latest bar's tilt to the model's
        *average* tilt over the recent window: more bullish than usual → BUY, more
        bearish than usual → SELL. This makes the pick respond to real changes
        rather than a fixed bias.
        """
        import numpy as np

        pred = self.predictor

        # The debiasing below was built for the deep net, whose raw output got stuck
        # on one side. The calibrated logistic model does not have that bias (it
        # calls BUY and SELL roughly equally), and its `.model` is a scikit-learn
        # estimator with no `.cfg.seq_len` / torch interface — so for it, and for the
        # heuristic fallback, just trust the calibrated probabilities directly.
        cfg = getattr(getattr(pred, "model", None), "cfg", None)
        if cfg is None or not hasattr(pred, "fb"):
            p = pred.predict(ohlcv)
            return Side.BUY if p.p_bullish >= p.p_bearish else Side.SELL

        import torch

        feats = pred.fb.transform(ohlcv)
        seq = cfg.seq_len
        n = len(feats)
        if n < seq + 8:
            p = pred.predict(ohlcv)
            return Side.BUY if p.p_bullish >= p.p_bearish else Side.SELL

        lo = max(seq - 1, n - 30)
        idxs = list(range(lo, n))
        X = np.stack([feats[i - seq + 1 : i + 1] for i in idxs]).astype("float32")
        with torch.no_grad():
            proba = pred.model.predict_proba(
                torch.from_numpy(X).to(pred.device)
            )["direction_proba"].cpu().numpy()
        tilt = proba[:, 0] - proba[:, 1]                 # bull - bear per bar
        # Split at the median of the recent window so the AI takes both sides
        # (~50/50) based on whether it's *relatively* bullish now, not the raw
        # (biased) absolute reading.
        baseline = float(np.median(tilt[:-1]))
        return Side.BUY if tilt[-1] >= baseline else Side.SELL

    def ai_paper_trade(self, ohlcv: pd.DataFrame) -> dict | None:
        """The AI's OWN pick for this candle, as a paper trade to track.

        Uses the model's directional lean (bull vs bear) + ATR risk plan, so we
        can score the AI's judgement over time even when the strict live gate
        would say WAIT. This is what builds the AI's real, forward-tested record.
        """
        if len(ohlcv) < 60:
            return None
        features = self.feature_builder.build_frame(ohlcv)
        pred = self.predictor.predict(ohlcv)
        side = self.ai_direction(ohlcv)          # debiased pick
        entry = float(ohlcv["close"].iloc[-1])
        atr = float(features["atr"].iloc[-1]) if "atr" in features else entry * 0.01
        plan = self.risk.build_plan(side, entry, atr)
        if plan is None:
            return None
        return {
            "side": side.value, "entry": entry,
            "stop": plan.stop_loss, "tp1": plan.take_profit_1, "tp2": plan.take_profit_2,
            "confidence": round(pred.confidence, 4),
        }

    @staticmethod
    def _probability_label(conf: float, side: Side) -> str:
        if side is Side.WAIT:
            return "Low"
        if conf >= 0.85:
            return "High"
        if conf >= 0.7:
            return "Medium"
        return "Low"

    @staticmethod
    def _recommendation_text(side: Side) -> str:
        if side is Side.BUY:
            return "BUY only after price enters the entry zone. Do not chase if missed."
        if side is Side.SELL:
            return "SELL only after price enters the entry zone. Do not chase if missed."
        return "No high-probability trade exists right now. Wait for confirmation."

    @staticmethod
    def _holding_estimate(timeframe: str) -> str:
        table = {
            "1m": "5-20 minutes", "5m": "15-60 minutes", "15m": "30-120 minutes",
            "1h": "2-8 hours", "4h": "8-24 hours", "1d": "2-7 days",
        }
        return table.get(timeframe, "Varies with timeframe")
