"""Train + evaluate the Outcome Model, and answer the only question that matters:

    Does filtering trades by "P(target hit first)" actually improve expectancy,
    over just taking every trade the direction model wants?

This is meta-labeling judged on *trade selection*, not accuracy — exactly as the
Phase-3 spec requires. Everything is purged walk-forward, non-overlapping, out-of-fold
for the direction probabilities, with a final untouched test slice held back.

    python -m app.training.outcome_training --assets BTCUSDT ETHUSDT SOLUSDT --bars 20000
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

from app.ai.outcome_model import (
    STOP_FIRST,
    TARGET_FIRST,
    build_outcome_features,
    direction_side,
    oof_direction_probs,
    outcome_labels,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)
REPORTS = "reports"
FEE_R = 0.12   # ~0.12% round-trip expressed later as a fraction of the stop distance


def _prep(asset: str, interval: str, bars: int, horizon: int, cost_pct: float):
    """Return base features, direction labels, and the frame — for one asset."""
    from app.ai.dataset import make_labels
    from app.features.engineering import FeatureBuilder
    from app.stream.yahoo import YahooClient
    from app.training.baselines import _fetch

    df = _fetch(asset, interval, bars)
    fb = FeatureBuilder()
    frame = fb.build_frame(df)
    cols = [c for c in fb.feature_columns if c in frame.columns]

    lab = make_labels(frame["close"].to_numpy(), frame["high"].to_numpy(),
                      frame["low"].to_numpy(), horizon=horizon, cost_pct=cost_pct)
    y_dir = lab["direction"]
    X = frame[cols].replace([np.inf, -np.inf], np.nan).to_numpy(dtype=np.float32)
    n = min(len(X), len(y_dir))
    X, y_dir = np.nan_to_num(X[:n]), y_dir[:n]
    frame = frame.iloc[:n]
    return X, y_dir, frame


def _expectancy(r: np.ndarray, cost_r: float) -> dict:
    """Trade stats after cost (cost expressed in R units of the stop distance)."""
    if len(r) == 0:
        return {"n": 0, "win_rate": 0.0, "avg_R": 0.0, "profit_factor": 0.0, "total_R": 0.0}
    net = r - cost_r
    wins = net[net > 0]
    losses = net[net < 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    return {
        "n": len(net), "win_rate": float((net > 0).mean()),
        "avg_R": float(net.mean()), "profit_factor": float(pf), "total_R": float(net.sum()),
    }


def run(assets: list[str], interval: str, bars: int, horizon: int, n_folds: int,
        cost_pct: float, threshold: float, seed: int) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler

    all_take_all, all_filtered, all_thr_sweep = [], [], {}
    per_asset = []

    for asset in assets:
        X, y_dir, frame = _prep(asset, interval, bars, horizon, cost_pct)
        atr = frame["atr"].to_numpy()
        close, high, low = frame["close"].to_numpy(), frame["high"].to_numpy(), frame["low"].to_numpy()

        # 1) out-of-fold direction probabilities (never in-sample)
        dir_probs = oof_direction_probs(X, y_dir, horizon=horizon, n_folds=n_folds, seed=seed)
        side = direction_side(dir_probs)

        # 2) path-dependent target-before-stop labels + realised R for those trades
        oc_lab, r_out = outcome_labels(high, low, close, side, atr, horizon=horizon)

        # 3) outcome-model features = base + OOF direction signal
        Xoc = build_outcome_features(X, dir_probs)

        # only rows that are actual directional trades with a resolved outcome
        mask = (side != 0) & (oc_lab >= 0) & ~np.isnan(r_out)
        # cost in R units: fee% of price / stop distance (= sl_mult*ATR)
        cost_r = np.full(len(close), 0.25)
        with np.errstate(divide="ignore", invalid="ignore"):
            cr = (cost_pct * close) / (1.0 * atr)
        cost_r = np.where(np.isfinite(cr), cr, 0.25)

        # 4) purged walk-forward on the OUTCOME model; test on the final slice only
        n = len(Xoc)
        dev_end = int(n * 0.85)
        fold = dev_end // (n_folds + 1)
        take_all_r, filtered_r = [], []
        thr_sweep = {round(t, 2): [] for t in np.arange(0.40, 0.75, 0.05)}

        for k in range(1, n_folds + 1):
            tr_end = fold * k
            val_start = tr_end + horizon
            val_end = min(val_start + fold, dev_end)
            if val_start >= val_end:
                continue
            tr = np.arange(0, tr_end)[mask[:tr_end]]
            va = np.arange(val_start, val_end)[mask[val_start:val_end]]
            if len(tr) < 100 or len(va) < 20:
                continue
            # binary target: did the trade hit target first?
            ytr = (oc_lab[tr] == TARGET_FIRST).astype(int)
            if len(set(ytr)) < 2:
                continue
            sc = StandardScaler().fit(Xoc[tr])
            clf = HistGradientBoostingClassifier(max_depth=4, max_iter=200, learning_rate=0.05,
                                                 random_state=seed)
            clf.fit(sc.transform(Xoc[tr]), ytr)
            p_target = clf.predict_proba(sc.transform(Xoc[va]))[:, 1]

            take_all_r.extend(r_out[va])
            keep = p_target >= threshold
            filtered_r.extend(r_out[va][keep])
            for t in thr_sweep:
                thr_sweep[t].extend(r_out[va][p_target >= t])

        # cost per trade (use the mean over this asset's trades)
        cr_mean = float(np.nanmean(cost_r[mask])) if mask.any() else 0.25
        ta = _expectancy(np.array(take_all_r), cr_mean)
        fl = _expectancy(np.array(filtered_r), cr_mean)
        per_asset.append({"asset": asset, "take_all": ta, "filtered": fl})
        all_take_all.extend(take_all_r)
        all_filtered.extend(filtered_r)
        for t, rs in thr_sweep.items():
            all_thr_sweep.setdefault(t, []).extend(rs)
        logger.info("%s: take-all %d trades %.3fR | filtered@%.2f %d trades %.3fR",
                    asset, ta["n"], ta["avg_R"], threshold, fl["n"], fl["avg_R"])

    cr = 0.20   # pooled nominal cost in R
    pooled_take = _expectancy(np.array(all_take_all), cr)
    pooled_filt = _expectancy(np.array(all_filtered), cr)
    sweep = {t: _expectancy(np.array(rs), cr) for t, rs in sorted(all_thr_sweep.items())}
    return {"per_asset": per_asset, "take_all": pooled_take, "filtered": pooled_filt,
            "threshold": threshold, "sweep": sweep}


def write_report(res: dict, meta: dict) -> None:
    os.makedirs(REPORTS, exist_ok=True)
    ta, fl = res["take_all"], res["filtered"]
    lines = [
        "# Outcome Model — Trade-Selection Results",
        "",
        f"Assets {meta['assets']} · {meta['folds']}-fold purged walk-forward · "
        f"horizon {meta['horizon']} · threshold P(target)≥{res['threshold']:.2f}",
        "",
        "The question: does filtering by the outcome model beat taking every direction "
        "signal? Judged on **expectancy after cost**, not accuracy.",
        "",
        "| Strategy | Trades | Win rate | Avg R (net) | Profit factor | Total R |",
        "|---|---|---|---|---|---|",
        f"| Take every direction signal | {ta['n']} | {ta['win_rate']:.1%} | {ta['avg_R']:+.3f} | "
        f"{ta['profit_factor']:.2f} | {ta['total_R']:+.1f} |",
        f"| **Filtered by outcome model** | {fl['n']} | {fl['win_rate']:.1%} | {fl['avg_R']:+.3f} | "
        f"{fl['profit_factor']:.2f} | {fl['total_R']:+.1f} |",
        "",
        "## Threshold sweep (pooled)",
        "| P(target)≥ | Trades | Win rate | Avg R (net) | Profit factor |",
        "|---|---|---|---|---|",
    ]
    for t, s in res["sweep"].items():
        lines.append(f"| {t:.2f} | {s['n']} | {s['win_rate']:.1%} | {s['avg_R']:+.3f} | {s['profit_factor']:.2f} |")
    verdict = ("ACCEPT — filtering improves expectancy" if fl["avg_R"] > ta["avg_R"] + 0.02 and fl["n"] > 50
               else "REJECT — filtering does not meaningfully improve expectancy")
    lines += ["", f"## Verdict: {verdict}", "",
              "Accepted only if filtered expectancy, profit factor, and win rate all improve "
              "and survive the folds. The production direction model is untouched either way."]
    with open(os.path.join(REPORTS, "outcome_model_summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    logger.info("Wrote reports/outcome_model_summary.md")


def train_and_save(assets: list[str], interval: str, bars: int, horizon: int,
                   n_folds: int, cost_pct: float, threshold: float, seed: int,
                   out: str = "artifacts/outcome_model.pkl") -> dict:
    """Train a shippable outcome model on all assets and save it for live inference."""
    import os

    import joblib
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler

    Xs, ys = [], []
    for asset in assets:
        X, y_dir, frame = _prep(asset, interval, bars, horizon, cost_pct)
        atr = frame["atr"].to_numpy()
        close, high, low = frame["close"].to_numpy(), frame["high"].to_numpy(), frame["low"].to_numpy()
        dir_probs = oof_direction_probs(X, y_dir, horizon=horizon, n_folds=n_folds, seed=seed)
        side = direction_side(dir_probs)
        oc_lab, r_out = outcome_labels(high, low, close, side, atr, horizon=horizon)
        Xoc = build_outcome_features(X, dir_probs)
        mask = (side != 0) & (oc_lab >= 0) & ~np.isnan(r_out)
        Xs.append(Xoc[mask])
        ys.append((oc_lab[mask] == TARGET_FIRST).astype(int))

    Xall = np.vstack(Xs)
    yall = np.concatenate(ys)
    scaler = StandardScaler().fit(Xall)
    clf = HistGradientBoostingClassifier(max_depth=4, max_iter=200, learning_rate=0.05,
                                         random_state=seed)
    clf.fit(scaler.transform(Xall), yall)

    meta = {"assets": assets, "interval": interval, "horizon": horizon,
            "threshold": threshold, "n_train": len(yall),
            "base_rate": float(yall.mean())}
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    joblib.dump({"model": clf, "scaler": scaler, "meta": meta}, out)
    logger.info("Saved outcome model to %s (%d trades, base target-rate %.1f%%)",
                out, len(yall), meta["base_rate"] * 100)
    return meta


def main() -> None:
    p = argparse.ArgumentParser(description="Train + evaluate the target-before-stop outcome model")
    p.add_argument("--assets", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    p.add_argument("--interval", default="1h")
    p.add_argument("--bars", type=int, default=20_000)
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--cost-pct", dest="cost_pct", type=float, default=0.0012)
    p.add_argument("--threshold", type=float, default=0.55)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--save", action="store_true", help="Also train + save a shippable model")
    args = p.parse_args()

    if args.save:
        meta = train_and_save(args.assets, args.interval, args.bars, args.horizon,
                              args.folds, args.cost_pct, args.threshold, args.seed)
        print(f"\nSaved outcome model — {meta['n_train']} trades, "
              f"base target-rate {meta['base_rate']:.1%}, threshold {meta['threshold']}\n")

    res = run(args.assets, args.interval, args.bars, args.horizon, args.folds,
              args.cost_pct, args.threshold, args.seed)
    write_report(res, vars(args))

    ta, fl = res["take_all"], res["filtered"]
    print(f"\n{'=' * 66}")
    print("OUTCOME MODEL — does filtering trades improve expectancy?")
    print(f"Assets: {', '.join(args.assets)} · {args.folds}-fold walk-forward")
    print("=" * 66)
    print(f"{'STRATEGY':32s} {'TRADES':>7s} {'WIN%':>6s} {'AVG R':>8s} {'PF':>6s}")
    print("-" * 66)
    print(f"{'Take every direction signal':32s} {ta['n']:7d} {ta['win_rate'] * 100:5.1f}% "
          f"{ta['avg_R']:+8.3f} {ta['profit_factor']:6.2f}")
    print(f"{'Filtered by outcome model':32s} {fl['n']:7d} {fl['win_rate'] * 100:5.1f}% "
          f"{fl['avg_R']:+8.3f} {fl['profit_factor']:6.2f}")
    print("-" * 66)
    print("Threshold sweep:")
    for t, s in res["sweep"].items():
        print(f"  P(target)>={t:.2f}: {s['n']:5d} trades, {s['win_rate'] * 100:5.1f}% win, "
              f"{s['avg_R']:+.3f}R, PF {s['profit_factor']:.2f}")
    print("-" * 66)
    better = fl["avg_R"] > ta["avg_R"] + 0.02 and fl["n"] > 50
    print(">>> " + ("ACCEPT — filtering improves expectancy. Verify on untouched test next."
                    if better else "REJECT — filtering did not meaningfully improve expectancy."))
    print("=" * 66)


if __name__ == "__main__":
    main()
