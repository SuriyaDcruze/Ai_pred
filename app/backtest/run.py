"""End-to-end honest evaluation: fetch → split → train → backtest out-of-sample.

    python -m app.backtest.run --symbol BTCUSDT --interval 1h --bars 20000 --epochs 25

Flow:
  1. Download ``bars`` real candles.
  2. Split into train (first ``1-holdout``) and test (last ``holdout``).
  3. Train the model on the TRAIN slice only (scaler + weights never see test).
  4. Backtest the trained model on the TEST slice — data it has never seen.
  5. Print the honest performance report.

Use ``--no-train`` to reuse an existing checkpoint, or ``--synthetic`` offline.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.backtest.engine import Backtester
from app.config import settings
from app.data.schemas import candles_to_frame
from app.data.synthetic import generate_ohlcv
from app.service import AnalysisService
from app.utils.logging import get_logger

logger = get_logger(__name__)


def _load(args) -> "pd.DataFrame":  # noqa: F821
    if args.synthetic:
        return generate_ohlcv(n=args.bars)
    from app.stream.binance import BinanceClient

    client = BinanceClient(futures=args.futures)
    candles = asyncio.run(client.fetch_history(args.symbol, args.interval, total=args.bars))
    return candles_to_frame(candles)


def main(args) -> dict:
    df = _load(args)
    split = int(len(df) * (1.0 - args.holdout))
    train_df, test_df = df.iloc[:split], df.iloc[split:]
    logger.info("Total %d bars → train %d / test %d (holdout %.0f%%)",
                len(df), len(train_df), len(test_df), args.holdout * 100)

    if not args.no_train:
        from app.training.train import build_parser as train_parser
        from app.training.train import train as train_model

        # Train ONLY on the train slice by handing the trainer synthetic-mode
        # data via a temporary in-memory path is awkward; instead we persist the
        # train slice and let the trainer read it through the synthetic hook.
        import app.training.train as train_mod

        targs = train_parser().parse_args([])
        targs.synthetic = True  # bypass network; we inject the frame below
        targs.bars = len(train_df)
        targs.seq_len = args.seq_len
        targs.horizon = args.horizon
        targs.epochs = args.epochs
        targs.folds = args.folds
        targs.out = args.out
        targs.run_name = f"bt-{args.symbol}"

        # Monkey-inject the real train slice in place of synthetic generation.
        original = train_mod.load_dataframe
        train_mod.load_dataframe = lambda _a: train_df
        try:
            train_model(targs)
        finally:
            train_mod.load_dataframe = original
        logger.info("Training complete → %s", args.out)

    # Load the checkpoint we just trained (not whatever the default path holds),
    # and optionally relax the confidence gate for a diagnostic edge measurement.
    from app.ai.predictor import Predictor
    from app.decision.engine import DecisionEngine

    predictor = Predictor.load(path=args.out)
    engine = DecisionEngine(min_confidence=args.min_confidence)
    service = AnalysisService(predictor=predictor, decision_engine=engine)
    bt = Backtester(
        service=service,
        fee_pct=args.fee, slippage_pct=args.slippage,
        max_hold_bars=args.max_hold,
    )
    result = bt.run(
        test_df, symbol=args.symbol, exchange="binance", timeframe=args.interval,
        start_equity=args.equity, risk_per_trade=settings.max_account_risk,
    )
    summary = result.summary()
    print("\n" + "=" * 48)
    print(f" OUT-OF-SAMPLE BACKTEST — {args.symbol} {args.interval}")
    print("=" * 48)
    print(json.dumps(summary, indent=2))
    print("=" * 48)
    _verdict(summary)
    return summary


def _verdict(s: dict) -> None:
    pf = s.get("profit_factor")
    exp = s.get("expectancy_r", 0)
    n = s.get("trades", 0)
    print("\nHonest read:")
    if n < 30:
        print(f"• Only {n} trades — too few to conclude anything. Need 100+ for signal.")
    if s["total_return_pct"] > 0 and (pf or 0) > 1.1 and exp > 0:
        print("• Positive out-of-sample edge on this slice. Promising — but validate on")
        print("  more symbols/periods and paper-trade before risking real money.")
    else:
        print("• No reliable edge on this slice. The model is NOT ready to trade real")
        print("  money. This is the normal, expected result — most models fail here.")
    print("• A single profitable backtest is necessary, not sufficient. Never skip paper trading.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Train + out-of-sample backtest")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--futures", action="store_true")
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--bars", type=int, default=20000)
    p.add_argument("--holdout", type=float, default=0.25)
    p.add_argument("--seq-len", dest="seq_len", type=int, default=96)
    p.add_argument("--horizon", type=int, default=6)
    p.add_argument("--epochs", type=int, default=20)
    p.add_argument("--folds", type=int, default=4)
    p.add_argument("--no-train", action="store_true", help="Reuse existing checkpoint")
    p.add_argument("--min-confidence", dest="min_confidence", type=float, default=settings.min_confidence,
                   help="Lower this (e.g. 0.5) for a DIAGNOSTIC run to surface trades and measure raw edge")
    p.add_argument("--out", default=settings.model_path)
    p.add_argument("--equity", type=float, default=10_000.0)
    p.add_argument("--fee", type=float, default=0.0004)
    p.add_argument("--slippage", type=float, default=0.0002)
    p.add_argument("--max-hold", dest="max_hold", type=int, default=48)
    return p


if __name__ == "__main__":
    main(build_parser().parse_args())
