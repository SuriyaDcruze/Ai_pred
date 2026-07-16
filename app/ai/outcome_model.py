"""Target-before-stop Outcome Model — meta-labeling for trade *selection*.

Phase 3, and the highest-value remaining idea. We proved directional accuracy is
stuck (~61%) and, crucially, that **accuracy is not profit**: the model calls
direction right ~60% of the time yet trades break even, because the stop gets hit
first, the move is too small, or fees eat it.

So this model does **not** predict direction. Given a trade the direction model
*already wants to take*, it predicts:

    TARGET_HIT_FIRST · STOP_HIT_FIRST · NO_CLEAR_MOVE

and lets us **veto** the trades likely to lose. It's a second, independent
intelligence layer — the production direction model is never touched.

The two leakage traps this code is built around:
  1. **Path-dependent labels.** The outcome label depends on the chronological order
     of future highs/lows (which barrier is touched first), never the final close.
     Same-candle TP+SL is resolved conservatively (assume the STOP first).
  2. **Out-of-fold direction probabilities.** When the direction model's own
     probabilities are used as inputs, they must come from a model that did NOT see
     the row — otherwise the outcome model trains on leaked confidence. We generate
     them with expanding-window out-of-fold prediction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# outcome classes
TARGET_FIRST, STOP_FIRST, NO_MOVE = 0, 1, 2


def outcome_labels(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, side: np.ndarray, atr: np.ndarray,
    *, horizon: int = 12, tp_mult: float = 1.5, sl_mult: float = 1.0,
    same_candle_policy: str = "adverse",
) -> tuple[np.ndarray, np.ndarray]:
    """For each bar with a directional `side` (+1 long, -1 short, 0 no-trade), scan the
    next `horizon` candles and label which barrier is hit first.

    Returns (labels, r_outcome): the class, and the realised R-multiple of the trade
    (path-dependent, fees excluded here — the evaluator adds cost).
    """
    n = len(close)
    labels = np.full(n, -1, dtype=np.int64)
    r_out = np.full(n, np.nan)

    for t in range(n - horizon):
        s = side[t]
        if s == 0 or np.isnan(atr[t]) or atr[t] <= 0:
            continue
        entry = close[t]
        if s > 0:                                   # long
            tp = entry + tp_mult * atr[t]
            sl = entry - sl_mult * atr[t]
        else:                                       # short
            tp = entry - tp_mult * atr[t]
            sl = entry + sl_mult * atr[t]

        label = NO_MOVE
        r = 0.0
        for j in range(t + 1, t + horizon + 1):
            hi, lo = high[j], low[j]
            hit_tp = (hi >= tp) if s > 0 else (lo <= tp)
            hit_sl = (lo <= sl) if s > 0 else (hi >= sl)
            if hit_tp and hit_sl:                   # same candle: be pessimistic
                if same_candle_policy == "adverse":
                    label, r = STOP_FIRST, -1.0
                else:
                    label, r = TARGET_FIRST, tp_mult
                break
            if hit_tp:
                label, r = TARGET_FIRST, tp_mult
                break
            if hit_sl:
                label, r = STOP_FIRST, -1.0
                break
        else:
            # timed out — mark to market in R units
            exit_px = close[min(t + horizon, n - 1)]
            move = (exit_px - entry) if s > 0 else (entry - exit_px)
            r = move / (sl_mult * atr[t])
            label = NO_MOVE
        labels[t] = label
        r_out[t] = r

    return labels, r_out


def oof_direction_probs(X: np.ndarray, y_dir: np.ndarray, *, horizon: int, n_folds: int = 5,
                        seed: int = 7) -> np.ndarray:
    """Out-of-fold calibrated direction probabilities for every row.

    Expanding window: fold k's rows are predicted by a model trained only on the rows
    *before* fold k (with a purge gap). Rows in fold 0 get a neutral prior. This is the
    'never use in-sample predictions' rule the spec demands.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    n = len(X)
    probs = np.full((n, 3), 1 / 3)
    fold = n // (n_folds + 1)
    for k in range(1, n_folds + 1):
        tr_end = fold * k
        val_start = tr_end + horizon                # purge
        val_end = min(val_start + fold, n) if k < n_folds else n
        if val_start >= val_end:
            continue
        Xtr, ytr = X[:tr_end], y_dir[:tr_end]
        ok = ytr >= 0
        if ok.sum() < 100 or len(set(ytr[ok])) < 2:
            continue
        sc = StandardScaler().fit(Xtr[ok])
        m = LogisticRegression(max_iter=1500, random_state=seed).fit(sc.transform(Xtr[ok]), ytr[ok])
        cls = list(m.classes_)
        p = m.predict_proba(sc.transform(X[val_start:val_end]))
        full = np.full((val_end - val_start, 3), 0.0)
        for i, c in enumerate(cls):
            full[:, c] = p[:, i]
        probs[val_start:val_end] = full
    return probs


def direction_side(dir_probs: np.ndarray) -> np.ndarray:
    """Turn direction probabilities into a trade side: +1 long, -1 short, 0 none."""
    pred = dir_probs.argmax(axis=1)
    side = np.where(pred == 0, 1, np.where(pred == 1, -1, 0)).astype(np.int64)
    return side


def build_outcome_features(base_X: np.ndarray, dir_probs: np.ndarray) -> np.ndarray:
    """Outcome-model inputs = base features + out-of-fold direction signal.

    The direction signal (its probabilities, entropy, and margin) is the whole point:
    the outcome model gets to ask "how confident was the direction call, and in what
    regime?" — then judge whether *that kind* of setup tends to reach target first.
    """
    p_up, p_dn, p_side = dir_probs[:, 0], dir_probs[:, 1], dir_probs[:, 2]
    eps = 1e-9
    entropy = -(dir_probs * np.log(dir_probs + eps)).sum(axis=1)
    margin = dir_probs.max(axis=1) - np.sort(dir_probs, axis=1)[:, -2]
    extra = np.column_stack([p_up, p_dn, p_side, entropy, margin])
    return np.hstack([base_X, extra]).astype(np.float32)
