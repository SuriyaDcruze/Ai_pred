"""Training pipeline.

Features required by the spec, all implemented here:
  * Time-series split + walk-forward validation (expanding window)
  * Early stopping on validation loss
  * Mixed-precision training (AMP) on CUDA
  * AdamW optimizer
  * Cosine LR scheduler with warmup
  * Gradient clipping
  * Checkpoint saving (best + last), bundling the FeatureBuilder scaler stats
  * TensorBoard logging

Run:
    python -m app.training.train --symbol BTCUSDT --interval 1h --epochs 20
    python -m app.training.train --synthetic --epochs 5     # offline / CI
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
from dataclasses import asdict

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader

try:
    from torch.utils.tensorboard import SummaryWriter
except Exception:  # tensorboard optional — fall back to a no-op writer
    class SummaryWriter:  # type: ignore[no-redef]
        def __init__(self, *a, **k):
            pass

        def add_scalar(self, *a, **k):
            pass

        def close(self):
            pass

from app.ai.dataset import SequenceDataset, make_labels
from app.ai.model import HybridTradingModel, ModelConfig
from app.config import settings
from app.data.schemas import candles_to_frame
from app.data.synthetic import generate_ohlcv
from app.features.engineering import FeatureBuilder
from app.training.losses import MultiTaskLoss
from app.utils.logging import get_logger

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Data acquisition
# --------------------------------------------------------------------------- #


def load_dataframe(args: argparse.Namespace) -> pd.DataFrame:
    if args.synthetic:
        logger.info("Using synthetic dataset (%d bars)", args.bars)
        return generate_ohlcv(n=args.bars)
    from app.stream.binance import BinanceClient

    client = BinanceClient(futures=args.futures)
    candles = asyncio.run(client.fetch_history(args.symbol, args.interval, total=args.bars))
    return candles_to_frame(candles)


# --------------------------------------------------------------------------- #
# Walk-forward folds
# --------------------------------------------------------------------------- #


def walk_forward_folds(n: int, n_folds: int, min_train: float = 0.4):
    """Yield (train_end, val_end) index cutoffs for expanding-window CV."""
    start = int(n * min_train)
    fold_size = (n - start) // n_folds
    for k in range(n_folds):
        train_end = start + k * fold_size
        val_end = train_end + fold_size
        if val_end > n:
            break
        yield train_end, min(val_end, n)


# --------------------------------------------------------------------------- #
# Optimizer / schedule
# --------------------------------------------------------------------------- #


def cosine_warmup(step: int, total: int, warmup: int) -> float:
    if step < warmup:
        return step / max(1, warmup)
    progress = (step - warmup) / max(1, total - warmup)
    return 0.5 * (1.0 + math.cos(math.pi * progress))


# --------------------------------------------------------------------------- #
# Train / eval loops
# --------------------------------------------------------------------------- #


def run_epoch(model, loader, loss_fn, device, optimizer=None, scaler=None, scheduler=None, total_steps=0, step_offset=0, warmup=0):
    train = optimizer is not None
    model.train(train)
    agg: dict[str, float] = {}
    count = 0
    use_amp = scaler is not None and device == "cuda"

    for i, (x, targets) in enumerate(loader):
        x = x.to(device)
        targets = {k: v.to(device) for k, v in targets.items()}

        with torch.set_grad_enabled(train):
            with torch.autocast(device_type="cuda", enabled=use_amp):
                out = model(x)
                loss, comps = loss_fn(out, targets)

            if train:
                optimizer.zero_grad(set_to_none=True)
                if use_amp:
                    scaler.scale(loss).backward()
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    scaler.step(optimizer)
                    scaler.update()
                else:
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    optimizer.step()
                if scheduler is not None:
                    lr_scale = cosine_warmup(step_offset + i, total_steps, warmup)
                    for g in optimizer.param_groups:
                        g["lr"] = g["base_lr"] * lr_scale

        for k, val in comps.items():
            agg[k] = agg.get(k, 0.0) + float(val.detach())
        count += 1

    return {k: v / max(1, count) for k, v in agg.items()}


def train(args: argparse.Namespace) -> str:
    device = settings.resolve_device()
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    logger.info("Training on device=%s", device)

    df = load_dataframe(args)
    if len(df) < args.seq_len + args.horizon + 100:
        raise SystemExit("Not enough data to train; increase --bars.")

    fb = FeatureBuilder()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    writer = SummaryWriter(log_dir=os.path.join("runs", args.run_name))

    best_val = float("inf")
    best_path = args.out
    patience_left = args.patience
    global_step = 0

    folds = list(walk_forward_folds(len(df), args.folds))
    logger.info("Walk-forward: %d folds over %d bars", len(folds), len(df))

    model: HybridTradingModel | None = None
    for fold_idx, (train_end, val_end) in enumerate(folds):
        # Fit scaler ONLY on the training slice of this fold (no leakage).
        train_df = df.iloc[:train_end]
        val_df = df.iloc[:val_end]
        fb.fit(train_df)

        feats_train = fb.transform(train_df)
        feats_val = fb.transform(val_df)
        labels_train = make_labels(
            train_df["close"].to_numpy(), train_df["high"].to_numpy(), train_df["low"].to_numpy(), args.horizon
        )
        labels_val = make_labels(
            val_df["close"].to_numpy(), val_df["high"].to_numpy(), val_df["low"].to_numpy(), args.horizon
        )
        ds_train = SequenceDataset(feats_train, labels_train, args.seq_len, args.horizon)
        # Validation windows: only those ending in the held-out segment.
        ds_val_full = SequenceDataset(feats_val, labels_val, args.seq_len, args.horizon)
        ds_val_full.indices = [e for e in ds_val_full.indices if e >= train_end]

        if len(ds_train) == 0 or len(ds_val_full) == 0:
            continue

        dl_train = DataLoader(ds_train, batch_size=args.batch_size, shuffle=True, drop_last=True)
        dl_val = DataLoader(ds_val_full, batch_size=args.batch_size)

        if model is None:  # build once, carry weights forward across folds
            cfg = ModelConfig(n_features=fb.n_features, seq_len=args.seq_len)
            model = HybridTradingModel(cfg).to(device)
            loss_fn = MultiTaskLoss().to(device)
            params = list(model.parameters()) + list(loss_fn.parameters())
            optimizer = torch.optim.AdamW(params, lr=args.lr, weight_decay=1e-4)
            for g in optimizer.param_groups:
                g["base_lr"] = g["lr"]
            scaler = torch.amp.GradScaler("cuda", enabled=device == "cuda")
            total_steps = args.epochs * len(folds) * max(1, len(dl_train))
            warmup = max(10, total_steps // 20)
            logger.info("Model: %s trainable params", f"{model.num_parameters():,}")

        for epoch in range(args.epochs):
            tr = run_epoch(
                model, dl_train, loss_fn, device, optimizer, scaler, scheduler=True,
                total_steps=total_steps, step_offset=global_step, warmup=warmup,
            )
            va = run_epoch(model, dl_val, loss_fn, device)
            global_step += len(dl_train)

            writer.add_scalar("loss/train", tr["loss"], global_step)
            writer.add_scalar("loss/val", va["loss"], global_step)
            writer.add_scalar("loss/val_direction", va["loss_direction"], global_step)
            logger.info(
                "fold %d ep %d | train %.4f | val %.4f (dir %.4f)",
                fold_idx, epoch, tr["loss"], va["loss"], va["loss_direction"],
            )

            if va["loss"] < best_val - 1e-4:
                best_val = va["loss"]
                patience_left = args.patience
                _save_checkpoint(best_path, model, fb, cfg)
            else:
                patience_left -= 1
                if patience_left <= 0:
                    logger.info("Early stopping (no val improvement).")
                    writer.close()
                    return best_path

    writer.close()
    if not os.path.exists(best_path):  # ensure we always leave a checkpoint
        _save_checkpoint(best_path, model, fb, cfg)
    logger.info("Best val loss %.4f -> %s", best_val, best_path)
    return best_path


def _save_checkpoint(path: str, model: HybridTradingModel, fb: FeatureBuilder, cfg: ModelConfig) -> None:
    torch.save(
        {
            "model_state": model.state_dict(),
            "model_config": asdict(cfg),
            "feature_state": fb.state_dict(),
        },
        path,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Train the Aegis hybrid trading model")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--futures", action="store_true")
    p.add_argument("--synthetic", action="store_true", help="Use offline synthetic data")
    p.add_argument("--bars", type=int, default=5000)
    p.add_argument("--seq-len", dest="seq_len", type=int, default=settings.seq_len)
    p.add_argument("--horizon", type=int, default=5)
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--folds", type=int, default=4)
    p.add_argument("--batch-size", dest="batch_size", type=int, default=64)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--patience", type=int, default=6)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default=settings.model_path)
    p.add_argument("--run-name", dest="run_name", default="aegis")
    return p


if __name__ == "__main__":
    train(build_parser().parse_args())
