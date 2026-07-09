"""Signal sweep — hunt for a predictable timeframe/horizon combination.

For each (timeframe, horizon) it trains a fresh model on the in-sample slice and
measures **out-of-sample directional accuracy** on a large sample. Directional
accuracy (not the trade backtest) is the right metric here: it isolates whether
the *model* has predictive skill, independent of the strict trading gate.

The output is a ranked table. Read it honestly:
  * ~50%      → coin flip, no signal in that config
  * 52-54%    → faint; likely won't survive costs on its own
  * >=55%     → worth a proper, cost-aware backtest
  * >=58%     → genuinely notable (rare) — validate hard before believing it

Run:
    python -m app.backtest.sweep --symbol BTCUSDT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile

import numpy as np

from app.data.schemas import candles_to_frame
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Sensible defaults. Bars are capped to what each timeframe can realistically
# provide (BTC history on Binance starts ~2017).
DEFAULT_TIMEFRAMES = ("15m", "1h", "4h", "1d")
DEFAULT_HORIZONS = (1, 3, 6)
DEFAULT_BARS = {"15m": 30000, "1h": 30000, "4h": 15000, "1d": 3000}


def _fetch(symbol: str, interval: str, bars: int):
    from app.stream.binance import BinanceClient

    candles = asyncio.run(BinanceClient().fetch_history(symbol, interval, total=bars))
    return candles_to_frame(candles)


def _train_config(train_df, seq_len: int, horizon: int, epochs: int, folds: int, out: str) -> str:
    """Train one model on ``train_df`` and save to ``out``. Returns ``out``."""
    import app.training.train as tm

    args = tm.build_parser().parse_args([])
    args.synthetic = True          # bypass network; inject the frame below
    args.bars = len(train_df)
    args.seq_len = seq_len
    args.horizon = horizon
    args.epochs = epochs
    args.folds = folds
    args.out = out
    args.run_name = "sweep"

    original = tm.load_dataframe
    tm.load_dataframe = lambda _a: train_df
    try:
        tm.train(args)
    finally:
        tm.load_dataframe = original
    return out


def directional_accuracy(predictor, test_df, horizon: int, stride: int = 3) -> tuple[float, int]:
    """Fast, batched OOS up/down accuracy (skips sideways-labelled bars).

    Engineers + scales features once, windows them, and runs the model in
    batches — O(n) instead of the O(n^2) of calling ``predict`` per bar.
    """
    import torch

    from app.ai.dataset import make_labels

    labels = make_labels(
        test_df["close"].to_numpy(), test_df["high"].to_numpy(),
        test_df["low"].to_numpy(), horizon,
    )
    feats = predictor.fb.transform(test_df)          # (T, F) scaled
    seq = predictor.model.cfg.seq_len
    idxs = [
        i for i in range(seq - 1, len(test_df) - horizon, stride)
        if labels["direction"][i] in (0, 1)
    ]
    if not idxs:
        return 0.0, 0

    X = np.stack([feats[i - seq + 1 : i + 1] for i in idxs]).astype(np.float32)
    y = np.array([labels["direction"][i] for i in idxs])

    model, dev = predictor.model, predictor.device
    guesses = []
    with torch.no_grad():
        for b in range(0, len(X), 256):
            xb = torch.from_numpy(X[b : b + 256]).to(dev)
            proba = model.predict_proba(xb)["direction_proba"].cpu().numpy()
            guesses.append(np.where(proba[:, 0] >= proba[:, 1], 0, 1))
    guesses = np.concatenate(guesses)
    return float((guesses == y).mean()), len(y)


def run_sweep(
    symbol: str = "BTCUSDT",
    timeframes=DEFAULT_TIMEFRAMES,
    horizons=DEFAULT_HORIZONS,
    bars=None,
    holdout: float = 0.25,
    seq_len: int = 64,
    epochs: int = 15,
    folds: int = 3,
    stride: int = 3,
) -> list[dict]:
    from app.ai.predictor import Predictor

    bars = bars or DEFAULT_BARS
    results: list[dict] = []
    tmpdir = tempfile.mkdtemp(prefix="sweep_")

    for tf in timeframes:
        n = bars.get(tf, 20000)
        logger.info("Fetching %d %s %s candles…", n, symbol, tf)
        df = _fetch(symbol, tf, n)
        if len(df) < seq_len + max(horizons) + 300:
            logger.warning("Not enough %s data (%d bars) — skipping.", tf, len(df))
            continue
        split = int(len(df) * (1.0 - holdout))
        train_df, test_df = df.iloc[:split], df.iloc[split:]

        for horizon in horizons:
            out = os.path.join(tmpdir, f"m_{tf}_{horizon}.pt")
            try:
                _train_config(train_df, seq_len, horizon, epochs, folds, out)
                predictor = Predictor.load(path=out)
                acc, n_samples = directional_accuracy(predictor, test_df, horizon, stride)
            except Exception as exc:  # noqa: BLE001 - keep the sweep going
                logger.warning("Config %s h=%d failed: %s", tf, horizon, exc)
                continue
            row = {"timeframe": tf, "horizon": horizon, "accuracy_pct": round(acc * 100, 1),
                   "samples": n_samples, "train_bars": len(train_df)}
            results.append(row)
            logger.info("  %s h=%d -> %.1f%% on %d samples", tf, horizon, acc * 100, n_samples)

    results.sort(key=lambda r: r["accuracy_pct"], reverse=True)
    _print_table(results)
    return results


def _print_table(results: list[dict]) -> None:
    print("\n" + "=" * 60)
    print(" SIGNAL SWEEP — out-of-sample directional accuracy")
    print("=" * 60)
    print(f"{'timeframe':<10}{'horizon':<9}{'accuracy':<11}{'samples':<9}")
    print("-" * 60)
    for r in results:
        flag = " <== worth a look" if r["accuracy_pct"] >= 55 else ""
        print(f"{r['timeframe']:<10}{r['horizon']:<9}{r['accuracy_pct']:<11}{r['samples']:<9}{flag}")
    print("=" * 60)
    best = results[0] if results else None
    if best and best["accuracy_pct"] >= 55:
        print(f"\nBest: {best['timeframe']} h={best['horizon']} at {best['accuracy_pct']}% — "
              f"run a proper cost-aware backtest on this before believing it.")
    else:
        print("\nNo config cleared 55%. Honest read: this feature set does not predict "
              "direction well on any tested timeframe. The next move is a different "
              "*approach* (new features/targets), not more compute.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Timeframe/horizon signal sweep")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    p.add_argument("--horizons", nargs="+", type=int, default=list(DEFAULT_HORIZONS))
    p.add_argument("--holdout", type=float, default=0.25)
    p.add_argument("--seq-len", dest="seq_len", type=int, default=64)
    p.add_argument("--epochs", type=int, default=15)
    p.add_argument("--folds", type=int, default=3)
    p.add_argument("--stride", type=int, default=3)
    return p


if __name__ == "__main__":
    a = build_parser().parse_args()
    res = run_sweep(
        symbol=a.symbol, timeframes=tuple(a.timeframes), horizons=tuple(a.horizons),
        holdout=a.holdout, seq_len=a.seq_len, epochs=a.epochs, folds=a.folds, stride=a.stride,
    )
    print("\n" + json.dumps(res, indent=2))
