"""Nightly walk-forward retrain — with a champion/challenger guard.

The market changes regime. A model frozen in the past goes stale, so we retrain on
fresh data. That much is standard practice.

The part that matters is what we do with the result. **A new model is never trusted
just because it is new.** It is a *challenger*: it must beat the incumbent
(the *champion*) on out-of-sample directional accuracy by a real margin before it
is allowed to replace it. If it doesn't, we keep the champion and log the attempt.

Without that guard, a single bad night — a choppy week, a bad seed — silently
replaces a working model with a worse one, and you would never know. Retraining
without a promotion gate is how automated systems quietly rot.

Usage
-----
    python -m app.scripts.nightly_retrain                    # BTCUSDT 1h
    python -m app.scripts.nightly_retrain --symbol ETHUSDT --interval 4h
    python -m app.scripts.nightly_retrain --dry-run          # never promote

Schedule it (Windows Task Scheduler / cron) once a day, after the daily close.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from datetime import datetime, timezone

import pandas as pd

from app.config import settings
from app.data.schemas import candles_to_frame
from app.utils.logging import get_logger

logger = get_logger(__name__)

HISTORY_PATH = os.path.join("artifacts", "retrain_history.jsonl")

# A challenger must beat the champion by MORE than this to be promoted. Set above
# zero on purpose: run-to-run noise on a ~50% metric is easily a few tenths of a
# percent, and promoting on noise is just a random walk through model space.
PROMOTION_MARGIN = 0.005          # +0.5 percentage points of directional accuracy
MIN_TEST_SAMPLES = 500            # below this, the accuracy estimate is meaningless


async def _fetch(symbol: str, interval: str, bars: int) -> pd.DataFrame:
    from app.stream.binance import BinanceClient
    from app.stream.yahoo import YahooClient

    client = YahooClient() if YahooClient.is_stock(symbol) else BinanceClient()
    candles = await client.fetch_history(symbol, interval, total=bars)
    return candles_to_frame(candles)


def _score(model_path: str, test_df: pd.DataFrame, horizon: int) -> tuple[float, int]:
    """Out-of-sample directional accuracy of the model at ``model_path``."""
    from app.ai.predictor import Predictor
    from app.backtest.sweep import directional_accuracy

    predictor = Predictor(model_path=model_path)
    return directional_accuracy(predictor, test_df, horizon=horizon)


def _log_attempt(record: dict) -> None:
    os.makedirs(os.path.dirname(HISTORY_PATH) or ".", exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def main(args: argparse.Namespace) -> int:
    from app.training.train import build_parser as train_parser
    from app.training.train import train

    champion_path = args.model_path
    challenger_path = os.path.join("artifacts", "model_challenger.pt")

    logger.info("=" * 64)
    logger.info("Nightly retrain — %s %s", args.symbol, args.interval)
    logger.info("=" * 64)

    # ---- 1. Fresh data, split so the test slice is strictly in the future ----
    df = asyncio.run(_fetch(args.symbol, args.interval, args.bars))
    if len(df) < 2_000:
        logger.error("Only %d bars — not enough to retrain safely. Aborting.", len(df))
        return 1

    split = int(len(df) * 0.85)
    train_df, test_df = df.iloc[:split], df.iloc[split:]
    logger.info("%d bars: %d train / %d holdout (holdout is strictly future data)",
                len(df), len(train_df), len(test_df))

    # ---- 2. Score the incumbent on the SAME holdout, for a fair comparison ----
    champion_acc, n = (0.0, 0)
    if os.path.exists(champion_path):
        champion_acc, n = _score(champion_path, test_df, args.horizon)
        logger.info("CHAMPION  : %.2f%% directional accuracy (n=%d)", champion_acc * 100, n)
        if n < MIN_TEST_SAMPLES:
            logger.error("Holdout only yielded %d samples (need %d). "
                         "The comparison would be noise. Aborting.", n, MIN_TEST_SAMPLES)
            return 1
    else:
        logger.warning("No champion at %s — the first model trained will be promoted.", champion_path)

    # ---- 3. Train the challenger ----
    targs = train_parser().parse_args([])
    targs.symbol, targs.interval = args.symbol, args.interval
    targs.bars, targs.horizon = args.bars, args.horizon
    targs.epochs, targs.folds = args.epochs, args.folds
    targs.out = challenger_path
    targs.run_name = f"nightly-{args.symbol}-{args.interval}"
    logger.info("Training challenger (%d epochs)…", args.epochs)
    train(targs)

    if not os.path.exists(challenger_path):
        logger.error("Training produced no checkpoint. Champion untouched.")
        return 1

    # ---- 4. Judge it on the same holdout ----
    challenger_acc, cn = _score(challenger_path, test_df, args.horizon)
    logger.info("CHALLENGER: %.2f%% directional accuracy (n=%d)", challenger_acc * 100, cn)

    delta = challenger_acc - champion_acc
    promote = (not os.path.exists(champion_path)) or (delta > PROMOTION_MARGIN)

    logger.info("-" * 64)
    logger.info("delta: %+.2f pp (need > +%.1f pp to promote)", delta * 100, PROMOTION_MARGIN * 100)

    if args.dry_run:
        logger.info("DRY RUN — not promoting, whatever the result.")
        promote = False

    if promote:
        if os.path.exists(champion_path):
            backup = champion_path.replace(".pt", f".bak-{datetime.now(timezone.utc):%Y%m%d}.pt")
            shutil.copy2(champion_path, backup)
            logger.info("Champion backed up to %s", backup)
        shutil.move(challenger_path, champion_path)
        logger.info("✅ PROMOTED. The challenger is the new champion.")
    else:
        os.remove(challenger_path)
        logger.info("❌ REJECTED. Keeping the champion — the challenger did not earn its place.")
        logger.info("   (This is the system working. Most retrains SHOULD be rejected.)")
    logger.info("-" * 64)

    _log_attempt({
        "at": datetime.now(timezone.utc).isoformat(),
        "symbol": args.symbol, "interval": args.interval,
        "champion_acc": round(champion_acc, 4),
        "challenger_acc": round(challenger_acc, 4),
        "delta": round(delta, 4),
        "promoted": promote,
        "test_samples": cn,
    })
    logger.info("Logged to %s", HISTORY_PATH)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Nightly walk-forward retrain with a promotion gate")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--bars", type=int, default=20_000)
    p.add_argument("--horizon", type=int, default=5)
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--folds", type=int, default=3)
    p.add_argument("--model-path", dest="model_path", default=settings.model_path)
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="Train and score, but never replace the champion")
    return p


if __name__ == "__main__":
    raise SystemExit(main(build_parser().parse_args()))
