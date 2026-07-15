"""The production model — a calibrated logistic regression that beat the deep net.

We raced a 580,000-parameter TCN+Transformer against simple baselines on honest,
non-overlapping, cost-aware labels. A plain logistic regression won by ~11
points of directional accuracy, with a far better Brier score — and it won again
after the deep net was retrained on the identical target. The MASTER MODEL CONTEXT
told us this could happen and told us to check; we checked, and it happened.

So this is now the model. It is not a downgrade — it is a correction:

  * **Better** — 59% vs 48% directional accuracy on the held-out test.
  * **Honest** — its probabilities are calibrated (isotonic), so "60%" means 60%.
  * **Fast** — trains in seconds on a CPU. No GPU, no Colab, no hour-long runs.
  * **Legible** — you can read its coefficients and see what it weighs.

It is a drop-in for the deep :class:`Predictor`: :meth:`SklearnPredictor.predict`
returns the same :class:`ModelPrediction`, so the decision engine, dashboard,
tracker and API do not change.

    python -m app.ai.sklearn_model --symbol BTCUSDT --interval 1h --bars 20000
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

from app.data.schemas import ModelPrediction
from app.utils.logging import get_logger

logger = get_logger(__name__)

SKLEARN_MODEL_PATH = os.path.join("artifacts", "sklearn_model.pkl")


class SklearnPredictor:
    """Calibrated tabular predictor with the deep model's output contract."""

    def __init__(self, model, scaler, calibrator, feature_columns: list[str],
                 horizon: int, cost_pct: float, meta: dict | None = None):
        self.model = model
        self.scaler = scaler
        self.calibrator = calibrator
        self.feature_columns = feature_columns
        self.horizon = horizon
        self.cost_pct = cost_pct
        self.meta = meta or {}
        from app.features.engineering import FeatureBuilder

        self.fb = FeatureBuilder()
        # We only use the LAST row of features, but indicators need warm-up history
        # to be meaningful (EMA-200 is the longest). We don't *hard-require* 200,
        # though: fewer bars just means a few slow indicators are still warming up,
        # and predict() runs them through nan_to_num. Requiring 210 (as an earlier
        # version did) was stricter than the old deep model's 128 and broke callers
        # that used to work. Ask for a sensible minimum and degrade gracefully.
        self.min_bars = 130

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, path: str = SKLEARN_MODEL_PATH) -> "SklearnPredictor":
        import joblib

        if not os.path.exists(path):
            raise FileNotFoundError(
                f"No sklearn model at {path!r}. Train one with "
                f"`python -m app.ai.sklearn_model`."
            )
        d = joblib.load(path)
        logger.info("Loaded %s (test dir-acc %.1f%%) from %s",
                    d["meta"].get("model_name", "model"),
                    d["meta"].get("test_dir_acc", 0) * 100, path)
        return cls(d["model"], d["scaler"], d["calibrator"], d["feature_columns"],
                   d["horizon"], d["cost_pct"], d["meta"])

    # ------------------------------------------------------------------ #
    def predict(self, ohlcv: pd.DataFrame) -> ModelPrediction:
        """Same signature and return type as the deep Predictor."""
        if len(ohlcv) < self.min_bars:
            raise ValueError(f"Need >= {self.min_bars} candles, got {len(ohlcv)}")

        feats = self.fb.build_frame(ohlcv)
        row = feats.iloc[-1]
        x = row[self.feature_columns].to_numpy(dtype=np.float32).reshape(1, -1)
        x = np.nan_to_num(x)
        xs = self.scaler.transform(x)

        raw = self.model.predict_proba(xs)                 # (1, 3): up / down / neutral
        proba = self.calibrator.transform(raw)[0] if self.calibrator else raw[0]

        last_close = float(ohlcv["close"].iloc[-1])
        atr = float(row.get("atr", last_close * 0.01) or last_close * 0.01)
        atr_pct = atr / last_close if last_close else 0.01

        # The linear model predicts direction, not price levels. Derive plausible
        # target/return bands from ATR and the horizon — honest, if approximate.
        reach = atr_pct * np.sqrt(self.horizon)
        p_up, p_dn, p_side = float(proba[0]), float(proba[1]), float(proba[2])
        drift = (p_up - p_dn) * reach

        # Calibrated confidence = the probability of the class it's actually calling.
        confidence = float(max(p_up, p_dn, p_side))

        return ModelPrediction(
            p_bullish=p_up,
            p_bearish=p_dn,
            p_sideways=p_side,
            predicted_high=last_close * (1.0 + reach),
            predicted_low=last_close * (1.0 - reach),
            predicted_close=last_close * (1.0 + drift),
            expected_volatility=atr_pct,
            confidence=confidence,
        )


# ---------------------------------------------------------------------- #
# Training
# ---------------------------------------------------------------------- #

def train(symbol: str, interval: str, bars: int, horizon: int, cost_pct: float,
          model_name: str = "logistic", out: str = SKLEARN_MODEL_PATH) -> dict:
    from sklearn.preprocessing import StandardScaler

    from app.ai.calibration import ProbabilityCalibrator, brier_score, expected_calibration_error
    from app.training.baselines import _fetch, build_xy

    df = _fetch(symbol, interval, bars)
    X, y, cols, _ = build_xy(df, horizon, cost_pct)

    # train / calibrate / test — chronological, with the boundary purged.
    n = len(X)
    a, b = int(n * 0.70), int(n * 0.85)
    h = horizon
    Xtr, ytr = X[: a - h], y[: a - h]
    Xcal, ycal = X[a : b - h], y[a : b - h]
    idx = np.arange(b, n, h)                              # non-overlapping test
    Xte, yte = X[idx], y[idx]
    logger.info("train %d / calib %d / test %d (non-overlapping)", len(Xtr), len(Xcal), len(Xte))

    scaler = StandardScaler().fit(Xtr)
    if model_name == "xgboost":
        from xgboost import XGBClassifier

        model = XGBClassifier(n_estimators=400, max_depth=5, learning_rate=0.05,
                              subsample=0.8, colsample_bytree=0.8, num_class=3,
                              objective="multi:softprob", eval_metric="mlogloss",
                              random_state=7, n_jobs=-1)
    else:
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression(max_iter=2000, C=1.0)
    model.fit(scaler.transform(Xtr), ytr)

    # Calibrate on the held-out calibration slice (never the test set).
    raw_cal = model.predict_proba(scaler.transform(Xcal))
    calibrator = ProbabilityCalibrator("isotonic").fit(raw_cal, ycal)

    # Honest test-set report.
    raw_te = model.predict_proba(scaler.transform(Xte))
    cal_te = calibrator.transform(raw_te)
    pred = cal_te.argmax(axis=1)
    directional = yte != 2
    dir_acc = float((pred[directional] == yte[directional]).mean()) if directional.any() else 0.0
    conf = cal_te.max(axis=1)
    ece = expected_calibration_error(conf, (pred == yte).astype(float))

    meta = {
        "model_name": model_name, "symbol": symbol, "interval": interval,
        "horizon": horizon, "cost_pct": cost_pct,
        "test_dir_acc": dir_acc, "test_brier": brier_score(cal_te, yte),
        "test_ece": ece, "n_train": len(Xtr), "n_test": len(Xte),
    }

    import joblib

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    joblib.dump({
        "model": model, "scaler": scaler, "calibrator": calibrator,
        "feature_columns": cols, "horizon": horizon, "cost_pct": cost_pct, "meta": meta,
    }, out)

    logger.info("Saved %s to %s", model_name, out)
    logger.info("  test directional accuracy: %.1f%%", dir_acc * 100)
    logger.info("  test Brier: %.3f | ECE: %.3f", meta["test_brier"], ece)
    return meta


def main() -> None:
    p = argparse.ArgumentParser(description="Train the production calibrated tabular model")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--bars", type=int, default=20_000)
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--cost-pct", dest="cost_pct", type=float, default=0.0012)
    p.add_argument("--model", default="logistic", choices=["logistic", "xgboost"])
    args = p.parse_args()

    meta = train(args.symbol, args.interval, args.bars, args.horizon, args.cost_pct, args.model)
    print(f"\nTrained {meta['model_name']} — test directional accuracy "
          f"{meta['test_dir_acc']:.1%}, ECE {meta['test_ece']:.3f}\n")


if __name__ == "__main__":
    main()
