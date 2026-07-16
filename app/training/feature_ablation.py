"""Feature-group ablation — do the new features ACTUALLY improve accuracy?

Phases 7-8 of the Accuracy Improvement spec, and the honest gatekeeper for the new
market-regime / price-action / session features. It races the model on the same
non-overlapping, cost-aware, out-of-sample split with different feature sets and
reports the accuracy delta. A feature group earns its place only if it improves
out-of-sample accuracy — not because it "seems useful."

    python -m app.training.feature_ablation --symbol BTCUSDT --bars 20000
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from app.features.engineering import RAW_FEATURE_COLUMNS
from app.features.market_regime import REGIME_FEATURES, add_market_regime
from app.features.price_action import PRICE_ACTION_FEATURES, add_price_action
from app.features.session import SESSION_FEATURES, add_session_features
from app.utils.logging import get_logger

logger = get_logger(__name__)


def _build_full_frame(df: pd.DataFrame, is_stock: bool) -> pd.DataFrame:
    """Base engineered frame + all candidate new feature groups."""
    from app.features.engineering import FeatureBuilder

    frame = FeatureBuilder().build_frame(df)
    frame = add_market_regime(frame)
    frame = add_price_action(frame)
    frame = add_session_features(frame, is_stock=is_stock)
    return frame


def _xy(frame: pd.DataFrame, df: pd.DataFrame, cols: list[str], horizon: int, cost_pct: float):
    from app.ai.dataset import make_labels

    labels = make_labels(frame["close"].to_numpy(), frame["high"].to_numpy(),
                         frame["low"].to_numpy(), horizon=horizon, cost_pct=cost_pct)
    y = labels["direction"]
    X = frame[cols].replace([np.inf, -np.inf], np.nan).to_numpy(dtype=np.float32)
    n = min(len(X), len(y))
    X, y = X[:n], y[:n]
    ok = ~np.isnan(X).any(axis=1) & (y >= 0)
    return X[ok], y[ok].astype(int)


def _score(X, y, horizon: int) -> tuple[float, int]:
    """Out-of-sample directional accuracy on a non-overlapping test slice."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    split = int(len(X) * 0.8)
    Xtr, ytr = X[: split - horizon], y[: split - horizon]      # purge boundary
    idx = np.arange(split, len(X), horizon)                    # non-overlapping test
    Xte, yte = X[idx], y[idx]
    if len(Xte) < 30 or len(set(ytr)) < 2:
        return float("nan"), len(Xte)

    sc = StandardScaler().fit(Xtr)
    m = LogisticRegression(max_iter=2000).fit(sc.transform(Xtr), ytr)
    pred = m.predict(sc.transform(Xte))
    directional = yte != 2
    if not directional.any():
        return float("nan"), len(Xte)
    return float((pred[directional] == yte[directional]).mean()), int(directional.sum())


def run(symbol: str, interval: str, bars: int, horizon: int, cost_pct: float, is_stock: bool):
    from app.training.baselines import _fetch

    df = _fetch(symbol, interval, bars)
    frame = _build_full_frame(df, is_stock)

    base = [c for c in RAW_FEATURE_COLUMNS if c in frame.columns]
    groups = {
        "A. base only": base,
        "B. + regime": base + list(REGIME_FEATURES),
        "C. + price-action": base + list(PRICE_ACTION_FEATURES),
        "D. + session": base + list(SESSION_FEATURES),
        "E. + regime + price-action": base + list(REGIME_FEATURES) + list(PRICE_ACTION_FEATURES),
        "F. all new groups": base + list(REGIME_FEATURES) + list(PRICE_ACTION_FEATURES) + list(SESSION_FEATURES),
    }
    rows = []
    for name, cols in groups.items():
        cols = [c for c in cols if c in frame.columns]
        X, y = _xy(frame, df, cols, horizon, cost_pct)
        acc, n = _score(X, y, horizon)
        rows.append({"set": name, "n_features": len(cols), "dir_acc": acc, "test_n": n})
        logger.info("  %s: %d feats -> %.1f%% (n=%d)", name, len(cols),
                    (acc * 100 if acc == acc else float("nan")), n)
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Feature-group ablation")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--bars", type=int, default=20_000)
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--cost-pct", dest="cost_pct", type=float, default=0.0012)
    p.add_argument("--stock", action="store_true")
    args = p.parse_args()

    table = run(args.symbol, args.interval, args.bars, args.horizon, args.cost_pct, args.stock)
    base_acc = table.iloc[0]["dir_acc"]

    print(f"\n{'=' * 62}")
    print(f"FEATURE ABLATION — {args.symbol} {args.interval}, horizon {args.horizon}")
    print("Do the new feature groups improve out-of-sample accuracy?")
    print("=" * 62)
    print(f"{'FEATURE SET':32s} {'FEATS':>5s} {'DIR ACC':>8s} {'vs BASE':>8s}")
    print("-" * 62)
    for _, r in table.iterrows():
        acc = "  n/a" if r.dir_acc != r.dir_acc else f"{r.dir_acc * 100:6.1f}%"
        delta = "" if r.dir_acc != r.dir_acc or base_acc != base_acc else f"{(r.dir_acc - base_acc) * 100:+6.1f}pp"
        print(f"{r.set:32s} {int(r.n_features):5d} {acc:>8s} {delta:>8s}")
    print("-" * 62)
    print("pp = percentage points vs the base feature set. Positive = the new")
    print("features helped. One symbol is not proof — confirm across assets.")
    print("=" * 62)


if __name__ == "__main__":
    main()
