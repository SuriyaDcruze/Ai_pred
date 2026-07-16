"""Purged walk-forward evaluation with the full metric suite.

This is the fair-comparison harness required by the Accuracy Improvement spec. It:

  * splits time-series into **expanding-window folds** (never shuffled),
  * inserts a **purge gap** (>= prediction horizon) between train and validation so
    overlapping labels can't leak future info across the boundary,
  * evaluates **only on non-overlapping** validation samples (stride = horizon),
  * fits scaling + calibration **inside each fold, on training data only**,
  * reports the complete metric table (balanced accuracy, macro-F1, per-class
    precision/recall, log loss, Brier, ECE, coverage) — never training accuracy.

Every feature-group challenger runs through this exact function with the same folds,
seed, labels, and horizon, so differences are attributable to features alone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np


@dataclass
class FoldMetrics:
    fold: int
    dir_acc: float
    balanced_acc: float
    macro_f1: float
    log_loss: float
    brier: float
    ece: float
    coverage: float
    per_class: dict = field(default_factory=dict)   # {class: {"precision":..,"recall":..}}
    n_val: int = 0


def _ece(conf: np.ndarray, correct: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    n = len(conf)
    e = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            e += (m.sum() / n) * abs(correct[m].mean() - conf[m].mean())
    return float(e)


def _fold_metrics(fold: int, proba: np.ndarray, y: np.ndarray) -> FoldMetrics:
    from sklearn.metrics import balanced_accuracy_score, f1_score, log_loss, precision_recall_fscore_support

    pred = proba.argmax(axis=1)
    conf = proba.max(axis=1)
    correct = (pred == y).astype(float)

    directional = y != 2
    dir_acc = float((pred[directional] == y[directional]).mean()) if directional.any() else float("nan")

    labels = [0, 1, 2]
    prec, rec, _, _ = precision_recall_fscore_support(y, pred, labels=labels, average=None, zero_division=0)
    per_class = {c: {"precision": float(prec[i]), "recall": float(rec[i])} for i, c in enumerate(labels)}

    onehot = np.eye(3)[y]
    brier = float(((proba - onehot) ** 2).sum(axis=1).mean())
    try:
        ll = float(log_loss(y, proba, labels=labels))
    except ValueError:
        ll = float("nan")

    # coverage = fraction of non-NEUTRAL predictions (the model "committing")
    coverage = float((pred != 2).mean())

    return FoldMetrics(
        fold=fold, dir_acc=dir_acc,
        balanced_acc=float(balanced_accuracy_score(y, pred)),
        macro_f1=float(f1_score(y, pred, labels=labels, average="macro", zero_division=0)),
        log_loss=ll, brier=brier, ece=_ece(conf, correct),
        coverage=coverage, per_class=per_class, n_val=len(y),
    )


def purged_walk_forward(
    X: np.ndarray, y: np.ndarray, horizon: int, *,
    n_folds: int = 5, calibrate: bool = True, seed: int = 7, model_name: str = "logistic",
) -> dict:
    """Run purged expanding-window CV. Returns aggregate + per-fold metrics + timing."""
    from sklearn.preprocessing import StandardScaler

    from app.ai.calibration import ProbabilityCalibrator

    n = len(X)
    # expanding windows: each fold trains on everything up to a growing cut, validates
    # on the next non-overlapping block. Reserve the final block as untouched test.
    dev_end = int(n * 0.85)                       # last 15% stays untouched here
    fold_size = dev_end // (n_folds + 1)
    t0 = time.perf_counter()

    folds: list[FoldMetrics] = []
    for k in range(1, n_folds + 1):
        train_end = fold_size * k
        val_start = train_end + horizon          # PURGE gap = one horizon
        val_end = min(val_start + fold_size, dev_end)
        if val_start >= val_end:
            continue

        Xtr, ytr = X[:train_end], y[:train_end]
        # non-overlapping validation samples
        vidx = np.arange(val_start, val_end, horizon)
        Xv, yv = X[vidx], y[vidx]
        if len(Xv) < 20 or len(set(ytr)) < 2:
            continue

        sc = StandardScaler().fit(Xtr)                        # fit on TRAIN only
        Xtr_s, Xv_s = sc.transform(Xtr), sc.transform(Xv)

        if model_name == "xgboost":
            from xgboost import XGBClassifier

            model = XGBClassifier(n_estimators=300, max_depth=5, learning_rate=0.05,
                                  subsample=0.8, colsample_bytree=0.8, num_class=3,
                                  objective="multi:softprob", eval_metric="mlogloss",
                                  random_state=seed, n_jobs=-1)
        else:
            from sklearn.linear_model import LogisticRegression

            model = LogisticRegression(max_iter=2000, random_state=seed)
        model.fit(Xtr_s, ytr)
        proba = model.predict_proba(Xv_s)

        if calibrate:
            # calibrate on a held-out tail of TRAIN, never on validation
            cut = int(len(Xtr) * 0.85)
            if cut - horizon > 50 and len(set(ytr[cut:])) > 1:
                cal = ProbabilityCalibrator("isotonic").fit(
                    model.predict_proba(sc.transform(Xtr[cut:])), ytr[cut:]
                )
                proba = cal.transform(proba)

        folds.append(_fold_metrics(k, proba, yv))

    elapsed = time.perf_counter() - t0
    if not folds:
        return {"folds": [], "ok": False}

    accs = np.array([f.dir_acc for f in folds])
    return {
        "ok": True,
        "n_folds": len(folds),
        "mean_dir_acc": float(np.nanmean(accs)),
        "std_dir_acc": float(np.nanstd(accs)),
        "worst_dir_acc": float(np.nanmin(accs)),
        "best_dir_acc": float(np.nanmax(accs)),
        "mean_balanced_acc": float(np.mean([f.balanced_acc for f in folds])),
        "mean_macro_f1": float(np.mean([f.macro_f1 for f in folds])),
        "mean_log_loss": float(np.nanmean([f.log_loss for f in folds])),
        "mean_brier": float(np.mean([f.brier for f in folds])),
        "mean_ece": float(np.mean([f.ece for f in folds])),
        "mean_coverage": float(np.mean([f.coverage for f in folds])),
        "up_precision": float(np.mean([f.per_class[0]["precision"] for f in folds])),
        "up_recall": float(np.mean([f.per_class[0]["recall"] for f in folds])),
        "down_precision": float(np.mean([f.per_class[1]["precision"] for f in folds])),
        "down_recall": float(np.mean([f.per_class[1]["recall"] for f in folds])),
        "neutral_precision": float(np.mean([f.per_class[2]["precision"] for f in folds])),
        "neutral_recall": float(np.mean([f.per_class[2]["recall"] for f in folds])),
        "train_time_s": round(elapsed, 2),
        "folds": folds,
    }
