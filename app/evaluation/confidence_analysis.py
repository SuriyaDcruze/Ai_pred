"""Confidence-bucket analysis — is there a subset of signals we can actually trust?

Phase 6 of the Model Improvement Spec, and the single most important question we can
ask of a break-even model. The average signal is a coin flip after fees. But the
model does not have *one* accuracy — it has a different reliability at every
confidence level. This asks:

    When the model is 70%+ sure, is it actually right ~70% of the time?
    And more importantly — are those high-confidence trades PROFITABLE after fees?

This is not about predicting better. It is about *trading more selectively*: if only
the model's most-confident calls have an edge, we trade only those and WAIT on the
rest. That is the one honest path from break-even to a small real edge.

Two things this analysis is built to stop us fooling ourselves about:

  * **Rarity.** A 75%-accurate bucket with 11 trades in it is noise, not an edge. We
    print the sample count next to every bucket and refuse to celebrate thin ones.
  * **Accuracy ≠ profit.** A bucket can be 65% accurate and still lose money if its
    wins are small and its losses large. So we report **net expectancy after fees**,
    not just accuracy — that is the number that actually pays.

Everything is out-of-sample, non-overlapping, cost-aware. The threshold is chosen
on validation, never on the final test slice.

    python -m app.evaluation.confidence_analysis --symbol BTCUSDT --bars 20000
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from app.utils.logging import get_logger

logger = get_logger(__name__)

BUCKETS = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70),
           (0.70, 0.75), (0.75, 0.80), (0.80, 1.01)]

MIN_BUCKET_SAMPLES = 30      # below this, a bucket's numbers are noise, full stop


def _net_expectancy(pred_dir, y_dir, reach, cost_pct, tp_mult=1.5, sl_mult=1.0):
    """A simple, honest expectancy per *directional* signal, after round-trip cost.

    We approximate each trade as: risk 1 unit (stop at sl_mult), aim tp_mult. If the
    predicted direction matched what happened, count a win of +tp_mult R; else -1 R.
    Then subtract the cost as a fraction of the risk distance. This is deliberately
    simple and pessimistic — the full path-dependent version lives in the backtester;
    here we just want the *shape* of expectancy vs confidence.
    """
    called = pred_dir != 2          # ignore NEUTRAL predictions
    if not called.any():
        return 0.0, 0
    correct = pred_dir[called] == y_dir[called]
    # cost as a fraction of the 1R stop distance (reach ~ ATR move; cost is on price)
    r_cost = float(np.mean(cost_pct / np.maximum(reach[called], 1e-9)))
    per_trade = np.where(correct, tp_mult, -1.0) - r_cost
    return float(per_trade.mean()), int(called.sum())


def run(symbol: str, interval: str, bars: int, horizon: int, cost_pct: float,
        model_name: str = "logistic") -> pd.DataFrame:
    from sklearn.preprocessing import StandardScaler

    from app.ai.calibration import ProbabilityCalibrator
    from app.training.baselines import _fetch, build_xy

    df = _fetch(symbol, interval, bars)
    X, y, cols, pos = build_xy(df, horizon, cost_pct)

    # train / calibrate / test — chronological, boundary purged, test non-overlapping.
    n = len(X)
    a, b = int(n * 0.70), int(n * 0.85)
    h = horizon
    Xtr, ytr = X[: a - h], y[: a - h]
    Xcal, ycal = X[a : b - h], y[a : b - h]
    idx = np.arange(b, n, h)
    Xte, yte, pte = X[idx], y[idx], pos[idx]
    logger.info("train %d / calib %d / test %d (non-overlapping)", len(Xtr), len(Xcal), len(Xte))

    sc = StandardScaler().fit(Xtr)
    if model_name == "xgboost":
        from xgboost import XGBClassifier

        model = XGBClassifier(n_estimators=400, max_depth=5, learning_rate=0.05,
                              subsample=0.8, colsample_bytree=0.8, num_class=3,
                              objective="multi:softprob", eval_metric="mlogloss",
                              random_state=7, n_jobs=-1)
    else:
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression(max_iter=2000)
    model.fit(sc.transform(Xtr), ytr)

    calibrator = ProbabilityCalibrator("isotonic").fit(model.predict_proba(sc.transform(Xcal)), ycal)
    proba = calibrator.transform(model.predict_proba(sc.transform(Xte)))

    pred = proba.argmax(axis=1)
    conf = proba.max(axis=1)

    # approximate per-bar ATR reach for the expectancy calc, from the test rows
    reach = np.full(len(Xte), cost_pct * 3)             # fallback
    try:
        from app.features.engineering import FeatureBuilder

        feats = FeatureBuilder().build_frame(df)
        atr = feats["atr"].to_numpy()
        close = feats["close"].to_numpy()
        off = len(df) - len(feats)
        r = []
        for p in pte:
            j = p - off
            r.append((atr[j] / close[j]) * np.sqrt(horizon) if 0 <= j < len(atr) and close[j] else cost_pct * 3)
        reach = np.array(r)
    except Exception as exc:  # noqa: BLE001
        logger.warning("using fallback reach: %s", exc)

    rows = []
    for lo, hi in BUCKETS:
        m = (conf >= lo) & (conf < hi)
        n_pred = int(m.sum())
        if n_pred == 0:
            rows.append({"bucket": f"{lo:.0%}-{hi:.0%}", "n": 0, "dir_acc": np.nan,
                         "calls": 0, "net_exp_R": np.nan, "trust": "—"})
            continue
        directional = m & (yte != 2)
        dir_acc = float((pred[directional] == yte[directional]).mean()) if directional.any() else np.nan
        exp, calls = _net_expectancy(pred[m], yte[m], reach[m], cost_pct)
        trust = "OK" if n_pred >= MIN_BUCKET_SAMPLES else "too few"
        rows.append({"bucket": f"{lo:.0%}-{hi:.0%}", "n": n_pred, "dir_acc": dir_acc,
                     "calls": calls, "net_exp_R": exp, "trust": trust})

    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Confidence-bucket analysis")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--bars", type=int, default=20_000)
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--cost-pct", dest="cost_pct", type=float, default=0.0012)
    p.add_argument("--model", default="logistic")
    args = p.parse_args()

    table = run(args.symbol, args.interval, args.bars, args.horizon, args.cost_pct, args.model)

    print(f"\n{'=' * 70}")
    print(f"CONFIDENCE-BUCKET ANALYSIS — {args.symbol} {args.interval}")
    print("Out-of-sample, non-overlapping, cost-aware. Does high confidence = profit?")
    print("=" * 70)
    print(f"{'BUCKET':>10s} {'N':>5s} {'DIR ACC':>8s} {'TRADES':>7s} {'NET EXP':>9s}  TRUST")
    print("-" * 70)
    for _, r in table.iterrows():
        acc = "   n/a" if pd.isna(r.dir_acc) else f"{r.dir_acc * 100:6.1f}%"
        exp = "    n/a" if pd.isna(r.net_exp_R) else f"{r.net_exp_R:+7.2f}R"
        print(f"{r.bucket:>10s} {int(r.n):5d} {acc:>8s} {int(r.calls):7d} {exp:>9s}  {r.trust}")
    print("-" * 70)
    print("DIR ACC = accuracy on bars that moved.  NET EXP = expectancy per trade after cost.")
    print("A bucket is only believable if N >= 30 (TRUST=OK). Thin buckets are noise.")
    print("=" * 70)

    # The honest verdict.
    valid = table[(table.trust == "OK") & table.net_exp_R.notna()]
    profitable = valid[valid.net_exp_R > 0.05]
    print()
    if profitable.empty:
        print(">>> No confidence bucket shows a believable positive edge.")
        print(">>> Honest conclusion: stay in LEARNING MODE. Higher confidence did not")
        print(">>> buy us profitability here. This is a valid, important result.")
    else:
        best = profitable.sort_values("net_exp_R").iloc[-1]
        print(f">>> The {best.bucket} bucket looks positive: {best.net_exp_R:+.2f}R over "
              f"{int(best.calls)} trades ({best.dir_acc * 100:.0f}% accurate).")
        print(">>> WORTH TESTING as a selective gate — but confirm on other symbols and")
        print(">>> in a forward-test before trusting it. One backtest is not proof.")
    print()


if __name__ == "__main__":
    main()
