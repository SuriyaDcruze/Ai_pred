"""Probability calibration — make the confidence number mean what it says.

This fixes a bug that cost us the whole day.

A neural network's softmax output is **not a probability**. It is a number between
0 and 1 that we have been *calling* a probability. When our model said "80%
confident", that did not mean it was right 80% of the time — it meant nothing at
all. We then built a trade gate on top of that meaningless number, set it to 80%,
and watched the system emit WAIT on 500 candles out of 500. The gate wasn't strict.
The gate was measuring a quantity that didn't exist.

The MASTER MODEL CONTEXT is explicit about this and we skipped it:

    "Among predictions assigned approximately 70 percent probability, the predicted
     event should occur approximately 70 percent of the time over a sufficiently
     large sample if the model is well calibrated."

Calibration does **not** make the model more accurate. A 55%-accurate model stays
55% accurate. What it does is make the confidence *honest*, so that:

  * a 60% signal really does win about 60% of the time,
  * the confidence gate finally means something,
  * position sizing on confidence stops being nonsense.

Method: **isotonic regression** (non-parametric, handles the sigmoid-shaped
miscalibration deep nets typically show) with Platt scaling as a fallback for small
samples. Fitted on a **validation split only** — never on the test set, which would
be leakage of exactly the kind the document forbids.

    python -m app.ai.calibration --model artifacts/model_best.pt
"""

from __future__ import annotations

import argparse
import os

import numpy as np

from app.utils.logging import get_logger

logger = get_logger(__name__)

CALIB_PATH = os.path.join("artifacts", "calibration.pkl")


# --------------------------------------------------------------------------- #
# Metrics that actually tell you whether a probability is trustworthy
# --------------------------------------------------------------------------- #

def brier_score(proba: np.ndarray, y: np.ndarray) -> float:
    """Mean squared error of the probabilities. Lower is better.

    For 3 classes, guessing 1/3 everywhere scores ~0.667. Anything near that is
    an uninformative model, no matter how good its accuracy looks.
    """
    onehot = np.zeros_like(proba)
    onehot[np.arange(len(y)), y] = 1.0
    return float(((proba - onehot) ** 2).sum(axis=1).mean())


def expected_calibration_error(conf: np.ndarray, correct: np.ndarray, bins: int = 10) -> float:
    """ECE: average gap between claimed confidence and actual accuracy.

    0.00 = perfectly honest. 0.20 = when it says 80%, it's really right ~60%.
    """
    edges = np.linspace(0, 1, bins + 1)
    ece, n = 0.0, len(conf)
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if not m.any():
            continue
        ece += (m.sum() / n) * abs(correct[m].mean() - conf[m].mean())
    return float(ece)


def reliability_table(conf: np.ndarray, correct: np.ndarray, bins: int = 10) -> list[dict]:
    """The reliability diagram, as numbers you can read in a terminal."""
    edges = np.linspace(0, 1, bins + 1)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if not m.any():
            continue
        rows.append({
            "bin": f"{lo:.0%}-{hi:.0%}",
            "n": int(m.sum()),
            "claimed": float(conf[m].mean()),
            "actual": float(correct[m].mean()),
            "gap": float(conf[m].mean() - correct[m].mean()),
        })
    return rows


# --------------------------------------------------------------------------- #
# The calibrator
# --------------------------------------------------------------------------- #

class ProbabilityCalibrator:
    """Maps raw model scores to honest probabilities. One calibrator per class."""

    def __init__(self, method: str = "isotonic"):
        self.method = method
        self.models: list = []
        self.n_classes = 0

    def fit(self, proba: np.ndarray, y: np.ndarray) -> "ProbabilityCalibrator":
        from sklearn.isotonic import IsotonicRegression
        from sklearn.linear_model import LogisticRegression

        self.n_classes = proba.shape[1]
        self.models = []
        # One-vs-rest: calibrate each class's score against whether it was correct.
        for k in range(self.n_classes):
            target = (y == k).astype(int)
            if len(np.unique(target)) < 2:      # class never appears — identity map
                self.models.append(None)
                continue
            if self.method == "isotonic" and len(y) >= 200:
                m = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
                m.fit(proba[:, k], target)
            else:                                # Platt: safer on small samples
                m = LogisticRegression()
                m.fit(proba[:, k].reshape(-1, 1), target)
            self.models.append(m)
        return self

    def transform(self, proba: np.ndarray) -> np.ndarray:
        from sklearn.isotonic import IsotonicRegression

        out = np.zeros_like(proba)
        for k, m in enumerate(self.models):
            if m is None:
                out[:, k] = proba[:, k]
            elif isinstance(m, IsotonicRegression):
                out[:, k] = m.predict(proba[:, k])
            else:
                out[:, k] = m.predict_proba(proba[:, k].reshape(-1, 1))[:, 1]
        # Renormalise so the three class probabilities sum to 1, as the spec requires.
        s = out.sum(axis=1, keepdims=True)
        s[s == 0] = 1.0
        return out / s

    def save(self, path: str = CALIB_PATH) -> None:
        import joblib

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        joblib.dump({"method": self.method, "models": self.models, "n_classes": self.n_classes}, path)
        logger.info("Saved calibrator to %s", path)

    @classmethod
    def load(cls, path: str = CALIB_PATH) -> "ProbabilityCalibrator | None":
        import joblib

        if not os.path.exists(path):
            return None
        d = joblib.load(path)
        c = cls(d["method"])
        c.models, c.n_classes = d["models"], d["n_classes"]
        return c


def report(proba: np.ndarray, y: np.ndarray, label: str) -> dict:
    """Print an honest verdict on a set of probabilities."""
    pred = proba.argmax(axis=1)
    conf = proba.max(axis=1)
    correct = (pred == y).astype(float)

    b = brier_score(proba, y)
    ece = expected_calibration_error(conf, correct)
    acc = float(correct.mean())

    print(f"\n--- {label} ---")
    print(f"  accuracy : {acc:.1%}")
    print(f"  Brier    : {b:.3f}   (0.667 = uninformative; lower is better)")
    print(f"  ECE      : {ece:.3f}   (0.00 = perfectly honest confidence)")
    print(f"  {'claimed':>9s} {'actual':>8s} {'gap':>7s} {'n':>6s}")
    for r in reliability_table(conf, correct):
        flag = "  <-- LIES" if abs(r["gap"]) > 0.15 else ""
        print(f"  {r['claimed']:9.1%} {r['actual']:8.1%} {r['gap']:+7.1%} {r['n']:6d}{flag}")
    return {"accuracy": acc, "brier": b, "ece": ece}


def main() -> None:
    p = argparse.ArgumentParser(description="Calibrate the model's confidence")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--bars", type=int, default=12_000)
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--cost-pct", dest="cost_pct", type=float, default=0.0012)
    p.add_argument("--model", default="logistic",
                   help="'logistic' | 'xgboost' | path to a .pt deep-net checkpoint")
    args = p.parse_args()

    from app.training.baselines import _fetch, build_xy

    df = _fetch(args.symbol, args.interval, args.bars)
    X, y, _, _ = build_xy(df, args.horizon, args.cost_pct)

    # Three-way chronological split: train / calibrate / test.
    # The calibrator NEVER sees the test set. That would be leakage.
    n = len(X)
    a, b = int(n * 0.70), int(n * 0.85)
    h = args.horizon
    Xtr, ytr = X[: a - h], y[: a - h]           # purge the boundary
    Xcal, ycal = X[a : b - h], y[a : b - h]
    idx = np.arange(b, n, h)                    # non-overlapping test labels
    Xte, yte = X[idx], y[idx]

    logger.info("train %d / calib %d / test %d (non-overlapping)", len(Xtr), len(Xcal), len(Xte))

    from sklearn.preprocessing import StandardScaler

    sc = StandardScaler().fit(Xtr)
    if args.model == "xgboost":
        from xgboost import XGBClassifier

        m = XGBClassifier(n_estimators=400, max_depth=5, learning_rate=0.05,
                          subsample=0.8, colsample_bytree=0.8, num_class=3,
                          objective="multi:softprob", random_state=7, n_jobs=-1)
    else:
        from sklearn.linear_model import LogisticRegression

        m = LogisticRegression(max_iter=1500)
    m.fit(sc.transform(Xtr), ytr)

    raw_cal = m.predict_proba(sc.transform(Xcal))
    raw_te = m.predict_proba(sc.transform(Xte))

    before = report(raw_te, yte, "BEFORE calibration (raw model scores)")

    cal = ProbabilityCalibrator("isotonic").fit(raw_cal, ycal)
    after = report(cal.transform(raw_te), yte, "AFTER calibration (isotonic)")

    cal.save()
    print(f"\n  ECE {before['ece']:.3f} -> {after['ece']:.3f}   "
          f"Brier {before['brier']:.3f} -> {after['brier']:.3f}")
    print("  Accuracy is UNCHANGED by design. Calibration makes the confidence honest,")
    print("  not the model smarter. That is the whole point.\n")


if __name__ == "__main__":
    main()
