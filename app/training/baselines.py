"""Baseline race — does the deep net actually earn its 580,000 parameters?

The MASTER MODEL CONTEXT is blunt about this and we ignored it:

    "Advanced models must be compared against simpler baselines.
     A complex model should not be accepted only because it is more advanced."

We went straight to a TCN + Transformer and never once checked whether a naive
rule, a logistic regression, or an XGBoost would do the same job. That is exactly
the mistake the document warns about, and it is embarrassingly common — a big model
that ties a small model is not a better model, it is a more expensive one.

This module runs the race properly:

  * **Always-Up / Always-Down / Always-Neutral** — the null hypotheses. If a model
    can't beat "always guess the majority class", it has learned nothing at all.
  * **Previous-Direction** — "tomorrow looks like today". Momentum's dumbest form.
  * **Logistic Regression** — linear, on the same 45 features.
  * **Random Forest**, **XGBoost** — the strong tabular baselines.
  * **Our deep net** — scored on the identical holdout.

Everything is judged on the SAME chronological split, with scalers fitted on train
only. Metrics include Brier score, because a model that is accurate but wildly
overconfident is dangerous, and accuracy alone will not tell you that.

    python -m app.training.baselines --symbol BTCUSDT --interval 1h --bars 20000
"""

from __future__ import annotations

import argparse
import asyncio

import numpy as np
import pandas as pd

from app.utils.logging import get_logger

logger = get_logger(__name__)


def _fetch(symbol: str, interval: str, bars: int) -> pd.DataFrame:
    from app.data.schemas import candles_to_frame
    from app.stream.binance import BinanceClient
    from app.stream.yahoo import YahooClient

    client = YahooClient() if YahooClient.is_stock(symbol) else BinanceClient()
    return candles_to_frame(asyncio.run(client.fetch_history(symbol, interval, total=bars)))


def build_xy(
    df: pd.DataFrame, horizon: int, cost_pct: float
) -> tuple[np.ndarray, np.ndarray, list[str], np.ndarray]:
    """Features + cost-aware UP/DOWN/NEUTRAL labels, aligned with no lookahead.

    Also returns ``pos`` — where each surviving row sits in the ORIGINAL frame, so
    the deep net can be scored on exactly the same bars as the baselines instead of
    a silently shifted set.
    """
    from app.ai.dataset import make_labels
    from app.features.engineering import FeatureBuilder

    fb = FeatureBuilder()
    feats = fb.build_frame(df)
    cols = list(fb.feature_columns)

    labels = make_labels(
        feats["close"].to_numpy(), feats["high"].to_numpy(), feats["low"].to_numpy(),
        horizon=horizon, cost_pct=cost_pct,
    )
    y = labels["direction"]
    X = feats[cols].to_numpy(dtype=np.float32)

    # make_labels can only label bars that have `horizon` future bars after them
    n = min(len(X), len(y))
    X, y = X[:n], y[:n]
    ok = ~np.isnan(X).any(axis=1) & (y >= 0)

    # feats may be shorter than df (warm-up rows dropped) — map back to df rows
    offset = len(df) - len(feats)
    pos = np.nonzero(ok)[0] + offset
    return X[ok], y[ok].astype(int), cols, pos


def _metrics(name: str, y_true: np.ndarray, y_pred: np.ndarray, proba: np.ndarray | None) -> dict:
    """Directional accuracy is measured on UP/DOWN bars only — guessing NEUTRAL
    everywhere is not a trading strategy, and it should not be rewarded as one."""
    acc = float((y_pred == y_true).mean())
    directional = (y_true != 2)
    dir_acc = float((y_pred[directional] == y_true[directional]).mean()) if directional.any() else 0.0
    # of the calls it actually made (non-neutral), how many were right?
    called = (y_pred != 2)
    precision = float((y_pred[called] == y_true[called]).mean()) if called.any() else 0.0

    brier = float("nan")
    if proba is not None:
        onehot = np.zeros_like(proba)
        onehot[np.arange(len(y_true)), y_true] = 1.0
        brier = float(((proba - onehot) ** 2).sum(axis=1).mean())

    return {
        "model": name, "accuracy": acc, "dir_acc": dir_acc,
        "precision_on_calls": precision, "calls_pct": float(called.mean()), "brier": brier,
    }


def run(symbol: str, interval: str, bars: int, horizon: int, cost_pct: float,
        stride: bool = True, checkpoint: str | None = None) -> pd.DataFrame:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    df = _fetch(symbol, interval, bars)
    X, y, cols, pos = build_xy(df, horizon, cost_pct)
    logger.info("%d usable samples, %d features", len(X), X.shape[1])

    # Chronological split — never shuffle a time series.
    split = int(len(X) * 0.8)

    # PURGE (López de Prado): bar `split-1`'s label depends on the next `horizon`
    # bars, which live in the test set. Without dropping them, the training labels
    # literally contain test-period information. Embargo the boundary.
    train_end = split - horizon
    Xtr, ytr = X[:train_end], y[:train_end]

    # STRIDE: with horizon H, bars t and t+1 share H-1 of their H future candles,
    # so their labels are ~(H-1)/H identical. Scoring on every consecutive bar
    # rewards a model for simply repeating its last answer — which is exactly why
    # "Previous-Direction" looked like a genius. Sample the test set every H bars
    # so each evaluated label has a DISJOINT future window. This is the difference
    # between an honest number and a flattering one.
    test_idx = np.arange(split, len(X), horizon) if stride else np.arange(split, len(X))
    Xte, yte = X[test_idx], y[test_idx]

    logger.info("train %d (purged %d) / test %d %s",
                len(Xtr), horizon, len(Xte),
                f"sampled every {horizon} bars — NON-OVERLAPPING labels" if stride
                else "consecutive — WARNING: labels overlap")

    dist = np.bincount(yte, minlength=3) / len(yte)
    logger.info("test class mix — up %.1f%% / down %.1f%% / neutral %.1f%%", *(dist * 100))

    # Scaler fitted on TRAIN ONLY. Fitting on everything is textbook leakage.
    sc = StandardScaler().fit(Xtr)
    Xtr_s, Xte_s = sc.transform(Xtr), sc.transform(Xte)

    rows: list[dict] = []

    # ---- the null hypotheses ----
    for name, const in [("Always-UP", 0), ("Always-DOWN", 1), ("Always-NEUTRAL", 2)]:
        rows.append(_metrics(name, yte, np.full_like(yte, const), None))

    majority = int(np.bincount(ytr).argmax())
    rows.append(_metrics("Majority-Class", yte, np.full_like(yte, majority), None))

    # "tomorrow looks like today" — the label of the bar one HORIZON earlier, i.e.
    # the most recent label whose outcome was actually known at decision time.
    prev = y[np.maximum(test_idx - horizon, 0)]
    rows.append(_metrics("Previous-Direction", yte, prev, None))

    # ---- real models ----
    models = [
        ("Logistic Regression", LogisticRegression(max_iter=1500)),  # multinomial by default
        ("Random Forest", RandomForestClassifier(n_estimators=250, max_depth=8,
                                                 min_samples_leaf=20, n_jobs=-1, random_state=7)),
    ]
    try:
        from xgboost import XGBClassifier

        models.append(("XGBoost", XGBClassifier(
            n_estimators=400, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, objective="multi:softprob",
            num_class=3, eval_metric="mlogloss", random_state=7, n_jobs=-1,
        )))
    except ImportError:
        logger.warning("xgboost not installed — skipping (pip install xgboost)")

    for name, m in models:
        m.fit(Xtr_s, ytr)
        rows.append(_metrics(name, yte, m.predict(Xte_s), m.predict_proba(Xte_s)))
        logger.info("  %s done", name)

    # ---- our deep net, on the identical holdout ----
    try:
        logger.info("scoring the deep net on %d bars (slow — one forward pass each)…", len(yte))
        rows.append(_deep_net(df, len(X), test_idx, yte, pos, checkpoint))
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not score the deep net: %s", exc)

    out = pd.DataFrame(rows)
    return out.sort_values("dir_acc", ascending=False).reset_index(drop=True)


def _deep_net(df: pd.DataFrame, feats_len: int, test_idx: np.ndarray,
              yte: np.ndarray, df_pos: np.ndarray, checkpoint: str | None = None) -> dict:
    """Score the trained checkpoint on EXACTLY the bars the baselines were scored on."""
    from app.ai.predictor import Predictor

    predictor = Predictor.load(checkpoint)

    preds, probas = [], []
    for i in test_idx:
        end = int(df_pos[i])                     # this row's position in the original frame
        p = predictor.predict(df.iloc[: end + 1])
        pr = [p.p_bullish, p.p_bearish, p.p_sideways]
        probas.append(pr)
        preds.append(int(np.argmax(pr)))

    return _metrics("★ Our Deep Net (TCN+Transformer)", yte,
                    np.array(preds), np.array(probas))


def main() -> None:
    p = argparse.ArgumentParser(description="Race the deep net against simple baselines")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--bars", type=int, default=20_000)
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--cost-pct", dest="cost_pct", type=float, default=0.0012,
                   help="Round-trip cost (fees+slippage). Moves smaller than this are NEUTRAL.")
    p.add_argument("--deep-model", dest="deep_model", default=None,
                   help="Checkpoint for the deep net (default: settings.model_path)")
    p.add_argument("--overlapping", action="store_true",
                   help="Score every consecutive bar. Inflates results — for demonstration only.")
    args = p.parse_args()

    table = run(args.symbol, args.interval, args.bars, args.horizon, args.cost_pct,
                stride=not args.overlapping, checkpoint=args.deep_model)

    print(f"\n{'=' * 78}")
    print(f"BASELINE RACE — {args.symbol} {args.interval}, horizon {args.horizon} bars")
    print(f"Labels are COST-AWARE: a move smaller than {args.cost_pct:.2%} round-trip = NEUTRAL")
    print("=" * 78)
    print(f"{'MODEL':34s} {'DIR ACC':>8s} {'PREC':>7s} {'CALLS':>7s} {'BRIER':>7s}")
    print("-" * 78)
    for _, r in table.iterrows():
        brier = "  n/a" if np.isnan(r.brier) else f"{r.brier:.3f}"
        print(f"{r.model:34s} {r.dir_acc * 100:7.1f}% {r.precision_on_calls * 100:6.1f}% "
              f"{r.calls_pct * 100:6.1f}% {brier:>7s}")
    print("-" * 78)
    print("DIR ACC = accuracy on bars that actually moved (50% = coin flip)")
    print("PREC    = when it made a call, how often it was right")
    print("CALLS   = how often it committed to a direction at all")
    print("BRIER   = probability quality; LOWER is better. 0.67 = uninformative.")
    print("=" * 78)

    best = table.iloc[0]
    deep = table[table.model.str.contains("Deep Net")]
    if not deep.empty:
        d = deep.iloc[0]
        if best.model != d.model:
            print(f"\n>>> '{best.model}' BEATS our deep net "
                  f"({best.dir_acc:.1%} vs {d.dir_acc:.1%}).")
            print(">>> The 580K-parameter network is not earning its complexity.")
        else:
            print(f"\n>>> The deep net wins at {d.dir_acc:.1%}. It earns its place.")
    print()


if __name__ == "__main__":
    main()
